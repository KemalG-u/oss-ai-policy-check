"""Frozen contract shared by every module: vocabulary, report shape, exit codes (schema_version "2").

A change here changes the public contract: see CONTRIBUTING-DEV.md and docs/schema.md.
Dependency direction: contract <- classify, discover, fetch, report, cli. Nothing here imports them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, TypedDict
from urllib.parse import quote

from . import __version__

SCHEMA_VERSION = "2"  # "2" added the needs-review verdict, `confidence` and `review_reason` (docs/schema.md)
TOOL_NAME = "oss-ai-policy-check"

# Verdicts in priority order, strongest first. `incomplete` is NOT a verdict: it is the
# `complete: false` flag on the report. `needs-review` is the abstention: the files carry AI-policy text
# the tool cannot decide on credibly. It ranks below disclosure-required (it never outranks a quoted
# restriction) and above permitted (an abstention is not permission).
BANNED = "banned"
RESTRICTED = "restricted"
DISCLOSURE_REQUIRED = "disclosure-required"
NEEDS_REVIEW = "needs-review"
PERMITTED = "permitted"
NO_POLICY_FOUND = "no-policy-found"
VERDICTS = (BANNED, RESTRICTED, DISCLOSURE_REQUIRED, NEEDS_REVIEW, PERMITTED, NO_POLICY_FOUND)
SEVERITY = {verdict: len(VERDICTS) - 1 - index for index, verdict in enumerate(VERDICTS)}
# Per-evidence signals are the verdicts that can be backed by a quoted line. `needs-review` evidence is an
# AI-policy paragraph no rule decided on.
SIGNALS = (BANNED, RESTRICTED, DISCLOSURE_REQUIRED, PERMITTED, NEEDS_REVIEW)
FAIL_ON_CHOICES = (BANNED, RESTRICTED, DISCLOSURE_REQUIRED, NEEDS_REVIEW)
# How sure the tool is. "low": one deciding sentence, only weak or qualified evidence, or an abstention.
HIGH, LOW = "high", "low"
CONFIDENCES = (HIGH, LOW)
REVIEW_NO_RULE, REVIEW_CONFLICT = "no-rule-decision", "conflicting-signals"
# Only the optional Claude classifier writes these two (docs/llm-mode.md, "How the verdicts are combined").
REVIEW_LLM_LOW, REVIEW_LLM_DISAGREE = "llm-low-confidence", "llm-rules-disagree"
REVIEW_REASONS = (REVIEW_NO_RULE, REVIEW_CONFLICT, REVIEW_LLM_LOW, REVIEW_LLM_DISAGREE)
MAX_REVIEW_PARAGRAPHS = 20
FORMATS = ("text", "json", "markdown", "sarif")
# Which classifier produced the verdict. "rules" (default) is the deterministic classifier, needs no key. "claude" is
# the optional language-model classifier (`--classifier claude`); it only chooses a verdict and paragraph ids, and the
# evidence lines and links are still built here from those ids.
CLASSIFIER_RULES, CLASSIFIER_CLAUDE = "rules", "claude"
CLASSIFIERS = (CLASSIFIER_RULES, CLASSIFIER_CLAUDE)

FILE_READ, FILE_MISSING, FILE_ERROR = "read", "missing", "error"
FILE_STATUSES = (FILE_READ, FILE_MISSING, FILE_ERROR)

MAX_QUOTE_WORDS = 15
MAX_QUOTE_CHARS = 300  # a single huge "word" must not get past the word cap
MAX_POLICY_FILES = 200
MAX_FILE_BYTES = 1_000_000

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INCOMPLETE = 3
EXIT_RESTRICTED = 10
EXIT_DISCLOSURE = 11  # only with --fail-on disclosure-required or needs-review
EXIT_NEEDS_REVIEW = 12  # only with --fail-on needs-review (nothing decided, a human must read the cited paragraphs)
EXIT_BANNED = 20

ORG_PREFIX = "org:.github/"  # `files_checked[].path` / evidence prefix for the owner's `.github` repository

SCOPE = ("root policy files, .github text/templates, conventional docs directories, AI-named policy documents; "
         "one-level policy links; the owner's .github repository unless the repository itself "
         "states an AI policy")
DISCLAIMER = ("Signals are heuristic, not legal advice and never permission. "
              "'no-policy-found' does not mean AI use is allowed.")
LIMITATIONS = (
    "The owner's .github repository is read unless the repository itself states an AI policy (an AI-policy-named "
    "file, or a paragraph pairing an AI term with a policy verb); its CONTRIBUTING and AI policy files are read "
    "even when the repository has its own CONTRIBUTING, and the repository text wins at that gate only.",
    "At most 8 AI-named files beyond the standard candidates are read per repository; the rest are listed in "
    "the warnings.",
    "Arbitrary external policy websites and source subprojects are not scanned.",
    "'complete' covers successful reads within the stated scope, not exhaustive policy discovery.",
)


class Evidence(TypedDict):
    file: str
    line: int
    url: str | None
    signal: str
    confidence: str  # high | low
    quote: str


class UnclassifiedLine(TypedDict):
    file: str
    line: int
    quote: str


class FileChecked(TypedDict):
    path: str
    status: str  # read | missing | error


class Tool(TypedDict):
    name: str
    version: str


class ClassifierInfo(TypedDict):
    kind: str  # rules | claude
    model: str | None  # the model id for kind "claude", else None


class Report(TypedDict):
    schema_version: str
    repo: str
    ref: str | None
    commit_sha: str | None
    org_commit_sha: str | None
    verdict: str
    confidence: str  # high | low
    review_reason: str | None  # set only for needs-review
    complete: bool
    evidence: list[Evidence]
    unclassified_ai_lines: list[UnclassifiedLine]
    files_checked: list[FileChecked]
    warnings: list[str]
    tool: Tool
    classifier: ClassifierInfo  # optional in the schema (reports of tool 0.2.0 and older have none); always written


@dataclass
class Snapshot:
    """What the transport layer hands to the classifier: texts plus how reading went."""
    repo: str
    ref: str | None
    commit_sha: str | None
    documents: dict[str, str] = field(default_factory=dict)
    files_checked: list[FileChecked] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    complete: bool = True
    api_calls: int = 0
    org_commit_sha: str | None = None  # SHA of the owner's `.github` repository when it was read


def clip_quote(line: str) -> str:
    """Literal source excerpt bounded at MAX_QUOTE_WORDS whitespace-separated words and MAX_QUOTE_CHARS
    characters (a longer excerpt is cut and ends with an ellipsis; the result never exceeds the cap)."""
    text = " ".join(line.strip().split()[:MAX_QUOTE_WORDS])
    if len(text) > MAX_QUOTE_CHARS:
        text = text[:MAX_QUOTE_CHARS - 1].rstrip() + "…"
    return text


def verdict_from_signals(signals: Iterable[str]) -> str:
    """Strongest signal wins; no signal means no-policy-found (never permission)."""
    present = set(signals)
    for verdict in VERDICTS:
        if verdict in present:
            return verdict
    return NO_POLICY_FOUND


def dedupe_evidence(items: Iterable[Any]) -> list[Any]:
    """Evidence is unique per (file, line, signal): when several rules (or the model) cite the same line for the
    same signal, one item stays, the one with the highest confidence (the first on a tie). Order is kept: an item
    sits where its first occurrence was. Works on report evidence and on classifier evidence alike."""
    kept: dict[tuple[Any, Any, Any], Any] = {}
    for item in items:
        key = (item["file"], item["line"], item["signal"])
        old = kept.get(key)
        if old is None or (item.get("confidence", LOW) == HIGH and old.get("confidence", LOW) != HIGH):
            kept[key] = item
    return list(kept.values())


def blob_url(repo: str, sha: str | None, path: str, line: int, org_sha: str | None = None) -> str | None:
    """Permalink pinned to a commit SHA; None when no SHA is known.

    A path with the `org:.github/` prefix lives in `OWNER/.github` and is pinned to `org_sha`
    (the main repository SHA would give a 404); without `org_sha` there is no link."""
    if path.startswith(ORG_PREFIX):
        if not org_sha:
            return None
        return (f"https://github.com/{quote(repo.split('/')[0])}/.github/blob/{org_sha}/"
                f"{quote(path[len(ORG_PREFIX):], safe='/')}#L{line}")
    if not sha:
        return None
    return f"https://github.com/{repo}/blob/{sha}/{quote(path, safe='/')}#L{line}"


def build_report(snapshot: Snapshot, classification: Mapping[str, Any]) -> Report:
    """Join transport snapshot and classifier output into the public report."""
    evidence: list[Evidence] = [
        {"file": item["file"], "line": item["line"],
         "url": blob_url(snapshot.repo, snapshot.commit_sha, item["file"], item["line"],
                          snapshot.org_commit_sha),
         "signal": item["signal"], "confidence": item.get("confidence", LOW), "quote": item["quote"]}
        for item in classification["evidence"]]
    abstain = classification["verdict"] == NEEDS_REVIEW
    reason = (classification.get("review_reason") or REVIEW_NO_RULE) if abstain else None
    if abstain and (reason == REVIEW_NO_RULE or not evidence):
        # no rule decided: the AI-policy paragraphs themselves are the evidence. With conflicting signals the
        # classified evidence lines are the paragraphs in conflict.
        evidence += [
            {"file": item["file"], "line": item["line"],
             "url": blob_url(snapshot.repo, snapshot.commit_sha, item["file"], item["line"], snapshot.org_commit_sha),
             "signal": NEEDS_REVIEW, "confidence": LOW, "quote": item["quote"]}
            for item in classification.get("review", [])]
    evidence = dedupe_evidence(evidence)  # one item per (file, line, signal), whichever classifier produced them
    confidence = classification.get("confidence", LOW)
    if not snapshot.complete and classification["verdict"] in (NO_POLICY_FOUND, PERMITTED):
        confidence = LOW  # unread files: nothing is decided, so nothing is "high"
    return {
        "schema_version": SCHEMA_VERSION,
        "repo": snapshot.repo,
        "ref": snapshot.ref,
        "commit_sha": snapshot.commit_sha,
        "org_commit_sha": snapshot.org_commit_sha,
        "verdict": classification["verdict"],
        "confidence": confidence,
        "review_reason": reason,
        "complete": bool(snapshot.complete),
        "evidence": evidence,
        "unclassified_ai_lines": [dict(item) for item in classification["unclassified_ai_lines"]],  # type: ignore[misc]
        "files_checked": [dict(item) for item in snapshot.files_checked],  # type: ignore[misc]
        "warnings": list(snapshot.warnings),
        "tool": {"name": TOOL_NAME, "version": __version__},
        "classifier": dict(classification.get("classifier") or {"kind": CLASSIFIER_RULES, "model": None}),  # type: ignore[misc]
    }


def exit_code(verdict: str, complete: bool, fail_on: str | None = None) -> int:
    """Map a verdict to the process exit code (table in docs/schema.md).

    Incomplete reads only block a decision when nothing restrictive was found: a banned or
    restricted verdict stands even if some candidate file could not be read."""
    if not complete and verdict in (NO_POLICY_FOUND, PERMITTED):
        return EXIT_INCOMPLETE
    codes = {BANNED: EXIT_BANNED, RESTRICTED: EXIT_RESTRICTED, DISCLOSURE_REQUIRED: EXIT_DISCLOSURE,
             NEEDS_REVIEW: EXIT_NEEDS_REVIEW}
    if fail_on is None:
        return codes[verdict] if verdict in (BANNED, RESTRICTED) else EXIT_OK
    if verdict in codes and SEVERITY[verdict] >= SEVERITY[fail_on]:
        return codes[verdict]
    return EXIT_OK
