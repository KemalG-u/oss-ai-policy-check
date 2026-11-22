"""Decide which files of a repository can hold an AI policy. Pure: paths in, paths out.

Public API
    is_candidate(path) -> bool
    select_candidates(paths) -> list[str]
    split_candidates(paths, limit=EXTRA_READ_LIMIT) -> (selected, skipped)
    ai_named_rank(path) -> tuple | None
    is_core_candidate(path) -> bool
    linked_paths(path, text, all_paths, repo) -> Iterator[str]
    org_policy_sources(owner, org_paths=None, repo_paths=()) -> list[str]
    in_scope(path), family(path), mentions_ai(text), has_ai_policy_text(text, owner=None), ORG_PREFIX

Candidate files (case-insensitive stem, text extensions only):
    AI_POLICY*, AI_COVENANT*, AI-USE / AI_USE (any case), *ai-usage*, *llm-rules*, code-provenance, generated-content
    CONTRIBUTING*, CONTRIBUTE*, AGENTS.md, AGENT_POLICY*, CLAUDE.md, .cursorrules
    CODE_OF_CONDUCT*, PULL_REQUEST_TEMPLATE*, root README
    every text file under .github/ (PR and issue templates included), except automation, agent skill
    and licence-listing noise (workflows/, actions/, agents/, skills/, prompts/, CODEOWNERS, licenses.txt)
    AI-named files (see ai_named_rank): a basename with an AI word as a whole segment (`AI.md`,
    `ai-coding.md`, `ai-generated-code-policy.rst`, `CONTRIBUTING.ai.md`) in the repository root, `.github/`,
    `docs/`, `doc/`, `policies/` or any `contributing/` directory (at most four directories deep), and
    `CONTRIBUTING.<x>.md` siblings. At most EXTRA_READ_LIMIT of these (plus AI-context links) are read,
    best match first; the rest are reported by the transport as skipped.
    the owner's organisation `.github` repository, only when no repository file carries an AI policy
    statement (see has_ai_policy_text, org_policy_sources); shown as `org:.github/<path>`
"""
from __future__ import annotations

import posixpath
import re
from typing import Iterable, Iterator
from urllib.parse import unquote, urlparse

from .contract import ORG_PREFIX  # single definition lives in the contract; re-exported here

TEXT_EXTENSIONS = {"", ".md", ".markdown", ".rst", ".txt", ".adoc"}
# Directories whose whole tree is searched for candidates (compared lower-case).
DOC_DIRS = {".github", "docs", "doc", "documentation"}
# Monorepos that keep their policy docs one level down: {top-level dir: sub dirs to search}.
MONOREPO_DOC_DIRS = {"llvm": ("docs",)}
CANDIDATE_STEMS = {
    "contributing", "contribute", "agents", "agent_policy", "code_of_conduct", "code-of-conduct",
    "pull_request_template", "pull-request-template", "claude", ".cursorrules",
}
NAME_PATTERN = re.compile(r"(?:ai|llm)[-_]?(?:policy|usage|rules|tool|covenant|contribut\w*|guidelines?|assist\w*|disclosure|statement|etiquette|ethics)"
                          r"|code[-_]provenance|generated[-_]content")
# `AI-USE.md` / `AI_USE.md`: anchored so words that merely end in "ai" ("bonsai-use") do not match.
AI_USE_PATTERN = re.compile(r"(?:^|[-_.])ai[-_]use$")


# Under .github/ these hold automation, agent skills and dependency listings, not community policy.
GITHUB_NOISE_DIRS = {"workflows", "actions", "agents", "skills", "prompts", "instructions", "codeql", "scripts", "linters"}
GITHUB_NOISE_STEMS = {"codeowners", "licenses", "license", "notice", "third-party-notices", "funding"}


def _github_noise(path: str, stem: str) -> bool:
    parts = path.lower().split("/")
    return bool(GITHUB_NOISE_DIRS.intersection(parts[1:-1])) or stem in GITHUB_NOISE_STEMS


# AI-named files: whole word segments of the basename (split on anything that is not a letter or digit),
# so "ai-coding", "AI_USE", "CONTRIBUTING.ai" match while "email", "detail", "rails" or "openai-sdk" do not.
AI_TOKENS = {"ai", "llm", "llms", "genai", "generative"}
POLICY_TOKENS = {"policy", "policies", "use", "usage", "guideline", "guidelines", "rules", "rule", "contributing",
                 "contribution", "contributions", "contribute", "coding", "generated", "assisted", "assistance",
                 "disclosure", "code", "tools", "tool", "agents", "agent", "statement", "covenant", "etiquette"}
FAMILY_TOKENS = {"policy", "policies", "guideline", "guidelines", "rules", "rule", "contribution", "contributions",
                 "coding", "use", "usage", "disclosure", "statement", "covenant", "etiquette", "assisted", "assistance",
                 "generated"}
AI_NAMED_EXTENSIONS = {".md", ".markdown", ".rst", ".txt", ".adoc"}
AI_NAMED_TOP_DIRS = {".github": 1, "policies": 1, "policy": 1, "docs": 3, "doc": 3, "documentation": 3}
AI_NAMED_MAX_DIRS = 4
# Trees that hold copies, builds, examples, translations or other people's code, never the project's own policy.
AI_NAMED_SKIP_DIRS = {"node_modules", "vendor", "third_party", "third-party", "_build", "build", "dist", "test", "tests",
                      "testdata", "fixtures", "examples", "example", "locale", "locales", "i18n", "translations"}
LOCALE_DIRS = {"de", "es", "fr", "hi", "ja", "ko", "pt", "ru", "tr", "uk", "it", "nl", "pl", "vi", "id", "ar", "fa", "he", "sv",
               "cs", "el", "th", "ro", "hu", "fi", "da", "no", "nb", "bg", "ca", "zh", "pt-br", "zh-cn", "zh-tw",
               "zh-hans", "zh-hant", "en-gb"}
LOCALE_SUFFIX = re.compile(r"[a-z]{2}(?:[-_][a-z]{2,4})?")
# Top-level directories, besides DOC_DIRS, that the large-repository fallback and the local reader also list.
POLICY_TREE_DIRS = {"policies", "policy", "contributing"}
EXTRA_READ_LIMIT = 8  # AI-named candidates and AI-context links read beyond the core candidates


def _tokens(stem: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", stem.lower()) if t]


def _extra_location(parts: list[str]) -> int | None:
    """Where an AI-named file may sit: 0 root, 1 .github/ or policies/, 2 a contributing/ directory, 3 docs/ ..."""
    dirs = [d.lower() for d in parts[:-1]]
    if len(dirs) > AI_NAMED_MAX_DIRS or AI_NAMED_SKIP_DIRS.intersection(dirs) or LOCALE_DIRS.intersection(dirs):
        return None
    if not dirs:
        return 0
    if dirs[0] == ".github" and GITHUB_NOISE_DIRS.intersection(dirs[1:]):
        return None
    if "contributing" in dirs:
        return 2
    return AI_NAMED_TOP_DIRS.get(dirs[0])


def ai_named_rank(path: str) -> tuple | None:
    """Sort key (lower is more relevant) when `path` is an AI-named policy document, else None.

    (a) the basename has an AI word (ai, llm, genai, generative) as a whole segment; (b) it is a
    `CONTRIBUTING.<x>.md` sibling (a bare language code such as `CONTRIBUTING.de.md` is a translation, not
    one). Text extensions only, and only in the repository root, `.github/`, `docs/`, `doc/`,
    `documentation/`, `policies/` or below a `contributing/` directory, at most four directories deep."""
    parts = path.split("/")
    stem, ext = posixpath.splitext(parts[-1].lower())
    if ext not in AI_NAMED_EXTENSIONS:
        return None
    location = _extra_location(parts)
    if location is None:
        return None
    tokens = _tokens(stem)
    if AI_TOKENS.intersection(tokens):
        tier = 0 if POLICY_TOKENS.intersection(tokens) else 1
    elif stem.startswith(("contributing.", "contribute.")) and not LOCALE_SUFFIX.fullmatch(stem.split(".", 1)[1]):
        tier = 2
    else:
        return None
    return (tier, location, len(parts), path)


def ai_policy_named(path: str) -> bool:
    """True for AI-named files that read as a policy document rather than a topic page: the AI word
    alone (`AI.md`, `CONTRIBUTING.ai.md`) or beside a policy word (`ai-coding.md`, `llm-policy.rst`);
    not `ai-tools.md` or `llm-providers.md`."""
    if ai_named_rank(path) is None:
        return False
    tokens = set(_tokens(posixpath.splitext(posixpath.basename(path).lower())[0]))
    return bool(AI_TOKENS & tokens) and (bool(FAMILY_TOKENS & tokens) or tokens <= AI_TOKENS | {"contributing", "contribute"})


def _core_candidate(path: str) -> bool:
    base = posixpath.basename(path).lower()
    stem, ext = posixpath.splitext(base)
    in_github = path.startswith(".github/")
    if ext not in TEXT_EXTENSIONS and not (in_github and ext in {".yml", ".yaml"} and "template" in path.lower()):
        return False
    return bool(NAME_PATTERN.search(stem)
                or AI_USE_PATTERN.search(stem)
                or stem in CANDIDATE_STEMS
                or (in_github and ext in TEXT_EXTENSIONS and not _github_noise(path, stem))
                or ("/" not in path and stem == "readme"))


def is_core_candidate(path: str) -> bool:
    """The name-based candidates (see the module docstring), without the AI-named additions."""
    return _core_candidate(path)


def is_candidate(path: str) -> bool:
    """True when the repository-relative POSIX path may hold an AI contribution policy."""
    return _core_candidate(path) or ai_named_rank(path) is not None


def select_candidates(paths: Iterable[str]) -> list[str]:
    """Sorted candidate subset of `paths` (no cap; the transport layer applies MAX_POLICY_FILES)."""
    return sorted(p for p in set(paths) if is_candidate(p))


def split_candidates(paths: Iterable[str], limit: int = EXTRA_READ_LIMIT) -> tuple[list[str], list[str]]:
    """(selected, skipped) for the default scan. Selected: the in-scope core candidates (sorted) followed
    by at most `limit` AI-named additions, most relevant first; skipped: the AI-named files over the limit."""
    unique = set(paths)
    core = sorted(p for p in unique if in_scope(p) and _core_candidate(p))
    seen = set(core)
    extra = sorted((p for p in unique if p not in seen and ai_named_rank(p) is not None), key=ai_named_rank)
    return core + extra[:limit], extra[limit:]


LINK_TARGETS = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)|^\s*\[([^\]]+)\]:\s*(\S+)|<([^<>\s]+)>|^\.\. _([^:]+):\s*(\S+)", re.M)
POLICY_WORDS = re.compile(r"\b(?:polic\w*|guidelines?|contribut\w*|rules|usage|assist\w*|generated|disclos\w*|etiquette)\b", re.I)
MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)


def _link_targets(text: str) -> Iterator[tuple[str, str, str]]:
    """(link text, nearest preceding Markdown heading, target) for every link in `text`."""
    heading = ""
    for paragraph in PARAGRAPH_BREAK.split(text):
        found = MARKDOWN_HEADING.findall(paragraph)
        if found:
            heading = found[-1]
        for match in LINK_TARGETS.finditer(paragraph):
            md_label, md_target, ref_label, ref_target, rst_target, _rst_name, rst_name_target = match.groups()
            yield md_label or ref_label or "", heading, md_target or ref_target or rst_target or rst_name_target


def _ai_link(label: str, heading: str, target: str) -> bool:
    """A link that is about an AI policy: its text or its target name mentions AI, or it sits under a
    heading that mentions AI and its text or target name reads like a policy document. Neighbouring
    prose does not count, so a project-layout link in an agent file is not followed."""
    name = posixpath.splitext(posixpath.basename(target))[0]
    wording = f"{label} {name}"
    return (bool(AI_TOKENS.intersection(_tokens(name))) or mentions_ai(label)
            or (mentions_ai(heading) and bool(POLICY_WORDS.search(wording))))


def linked_paths(path: str, text: str, all_paths: Iterable[str], repo: str, ai_links: bool = True) -> Iterator[str]:
    """Same-repository files that `text` (found at `path`) links to; one level only. A target is followed
    when it is a candidate by name, or (with `ai_links`) when it is a text file in the default scan scope
    and the link itself is about AI (see _ai_link)."""
    all_paths = set(all_paths)
    for label, heading, target in _link_targets(text):
        target = unquote(target.split("#", 1)[0])
        if not target or target.startswith(("mailto:", "javascript:")):
            continue
        parsed = urlparse(target)
        if parsed.scheme:
            parts = parsed.path.strip("/").split("/")
            if parsed.hostname == "github.com" and "/".join(parts[:2]).lower() == repo.lower() and len(parts) > 4 and parts[2] == "blob":
                candidate = "/".join(parts[4:])
            else:
                # Match official rendered docs to their in-repo source by basename.
                name = posixpath.splitext(posixpath.basename(parsed.path))[0].lower()
                yield from sorted(p for p in all_paths
                                  if posixpath.splitext(posixpath.basename(p))[0].lower() == name and is_candidate(p))
                continue
        else:
            candidate = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        if candidate not in all_paths:
            continue
        if is_candidate(candidate) or (in_scope(candidate) and posixpath.splitext(candidate)[1].lower() in TEXT_EXTENSIONS - {""}
                                       and ai_links and _ai_link(label, heading, candidate)):
            yield candidate


AI_WORDS = re.compile(r"\b(?:AIs?|LLMs?|AGI|AI-\w+)\b")  # case-sensitive: "said" is not AI
AI_PHRASES = re.compile(r"\b(?:copilot|chatgpt|gpt-?\d|claude|gemini|generative|artificial intelligence|"
                        r"language models?|machine[- ]generated)\b", re.I)


# An AI term and a policy verb in the same paragraph make an AI policy statement; either alone does not.
AGENT_GENERATED = re.compile(r"\bagent[- ]generated\b", re.I)
POLICY_VERBS = re.compile(
    r"\b(?:must|required?|requires?|requirements?|disclos\w*|ban(?:s|ned|ning)?|prohibit\w*|forbid\w*|"
    r"disallow\w*|not allowed|allowed|accept(?:s|ed|ing)?|reject(?:s|ed|ing)?|permit(?:s|ted)?|welcome[sd]?|"
    r"do not|don't|never|may not|should not)\b", re.I)
PARAGRAPH_BREAK = re.compile(r"\n[ \t\r]*\n")


def in_scope(path: str) -> bool:
    """True for paths the scan reads by default: repo root files, the conventional docs
    directories (any depth) and the monorepo docs subdirectories."""
    parts = path.split("/")
    if len(parts) == 1:
        return True
    if parts[0].lower() in DOC_DIRS:
        return True
    return len(parts) > 2 and parts[1] in MONOREPO_DOC_DIRS.get(parts[0], ())


def mentions_ai(text: str) -> bool:
    """Cheap, non-classifying gate: does this text talk about AI at all?"""
    return bool(AI_WORDS.search(text) or AI_PHRASES.search(text))


def has_ai_policy_text(text: str, owner: str | None = None) -> bool:
    """Cheap, non-classifying gate: does one paragraph of `text` contain both an AI term and a policy
    verb (must, require, disclose, ban, prohibit, accept, reject, allowed, ...)? A bare mention such as
    "AI features" in a README does not count. With `owner`, a paragraph that links into
    `github.com/<owner>/.github` is a pointer to the organisation policy, not a policy of its own."""
    pointer = re.compile(r"github\.com/" + re.escape(owner) + r"/\.github(?![\w-])", re.I) if owner else None
    return any((mentions_ai(par) or AGENT_GENERATED.search(par)) and POLICY_VERBS.search(par)
               and not (pointer and pointer.search(par))
               for par in PARAGRAPH_BREAK.split(text))


def family(path: str) -> str:
    """Community-file family of a path, so a repository file overrides the org default of the
    same kind regardless of where it lives (root, docs/ or .github/): `CONTRIBUTING.md`,
    `docs/contributing.rst` and `.github/CONTRIBUTING.md` are one family; files inside a template
    directory (`ISSUE_TEMPLATE/x.md`) share the directory's family."""
    parts = path.lower().split("/")
    stem = posixpath.splitext(parts[-1])[0]
    if len(parts) > 1 and parts[-2] not in DOC_DIRS:
        stem = parts[-2]
    stem = stem.replace("-", "_")
    if stem == "contribute":
        return "contributing"
    if NAME_PATTERN.search(stem) or AI_USE_PATTERN.search(stem) or ai_policy_named(path):
        return "ai_policy"
    return stem


# Families the owner's `.github` repository can add even when the repository has a file of the same
# kind: the caller only asks for org files when the repository's own text holds no AI policy statement.
ORG_ALWAYS_READ = {"contributing", "ai_policy"}


def org_policy_sources(owner: str, org_paths: Iterable[str] | None = None,
                       repo_paths: Iterable[str] = ()) -> list[str]:
    """Paths to read from the owner's organisation `.github` repository.

    Without `org_paths` it returns the conventional probe list (root, .github/ and docs/).
    With the org repository's file list it returns the policy-bearing files GitHub would offer as
    defaults: root, .github/ and docs/ candidates, README and `profile/` excluded. CONTRIBUTING and AI
    policy files are always included (the repository's own contributing file does not hide them: the
    caller has already established that the repository text has no AI policy statement); for every
    other family a file the repository carries itself wins, wherever it lives. Pure: no I/O."""
    if not owner:
        raise ValueError("owner required")
    if org_paths is None:
        names = ("CONTRIBUTING.md", "AI_POLICY.md", "PULL_REQUEST_TEMPLATE.md")
        return [p for n in names for p in (n, f".github/{n}", f"docs/{n}")]
    own = {family(p) for p in repo_paths} - ORG_ALWAYS_READ
    chosen = []
    for path in set(org_paths):
        if path.lower().startswith("profile/") or not in_scope(path) or not is_candidate(path):
            continue
        if posixpath.splitext(posixpath.basename(path))[0].lower() == "readme":
            continue
        if family(path) in own:
            continue
        chosen.append(path)
    return sorted(chosen)
