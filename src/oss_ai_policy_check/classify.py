"""Pure classifier: document text in, evidence out. No network, no file access beyond the rules file.

Public API
    load_patterns(path=None) -> dict
    classify_documents(documents, patterns=None, owner=None) -> Classification
    decide(evidence, review) -> (verdict, review_reason, confidence)
    body_rules(documents, patterns=None) -> dict
    body_flags(classification) -> dict

The classifier abstains instead of guessing: a verdict of `needs-review` means AI-policy text exists that no
rule decided on, or the evidence conflicts (`decide` holds the precedence, docs/schema.md "Decision order").
`confidence` is "low" for one deciding sentence, a rule marked weak, or a ban sentence demoted to `restricted`
because it carries a narrowing qualifier (the shared `narrowing_qualifiers` list in rules/patterns.json).

Evidence items returned here carry extra internal keys (`rule`: the rule id that fired, `sentence`: which
sentence, `demoted`: a ban downgraded by a qualifier); `contract.build_report` drops them, so they are not part
of the public report.
"""
from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, TypedDict

from .contract import (BANNED, DISCLOSURE_REQUIRED, HIGH, LOW, MAX_REVIEW_PARAGRAPHS, NEEDS_REVIEW,
                       NO_POLICY_FOUND, PERMITTED, RESTRICTED, SEVERITY, clip_quote, verdict_from_signals)
from .discover import AGENT_GENERATED, AI_PHRASES, AI_WORDS, POLICY_VERBS


class ClassifiedEvidence(TypedDict):
    file: str
    line: int
    signal: str
    quote: str
    rule: str
    confidence: str
    sentence: str
    demoted: bool


class Classification(TypedDict):
    verdict: str
    confidence: str
    signals: list[str]
    evidence: list[ClassifiedEvidence]
    unclassified_ai_lines: list[dict[str, Any]]
    review: list[dict[str, Any]]
    review_reason: str | None
    body_rules: dict[str, list[dict[str, Any]]]


# Body (PR description / trailer) rules depend on document text, never on repository names.
# They only add information; the class rules live in rules/patterns.json.
TRAILER_BAN = re.compile(
    r"AI attribution is not welcome"
    r"|\bno\s+`?(?:Co-Authored-By|Generated-by)`?"
    r"|\b(?:do not|don't|must not|never|should not)\s+(?:add|include|use|put|append|insert|keep|leave)\b[^.!?]{0,60}"
    r"\b(?:Co-Authored-By|Generated-by|AI co-?authors?|AI trailers?)\b"
    r"|\bno AI (?:co-?authors?|attribution|trailers?)\b"
    r"|\bAI (?:co-?author )?trailers? (?:are|is) (?:not allowed|prohibited|forbidden|banned)", re.I)
VOICE = re.compile(
    r"\bown (?:human )?voice\b"
    r"|descriptions? (?:written|generated|produced) by an?\b"
    r"|\b(?:PR|pull request) (?:description|body|text)s?[^.!?]{0,60}\b(?:own words|written by (?:a )?human|human[- ]written)\b"
    r"|write (?:it |this |the description )?in your own words", re.I)

_default_patterns: dict[str, Any] | None = None


def load_patterns(path: str | Path | None = None) -> dict[str, Any]:
    """Load rules; with no path, the packaged rules/patterns.json."""
    global _default_patterns
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _default_patterns is None:
        source = resources.files("oss_ai_policy_check") / "rules" / "patterns.json"
        _default_patterns = json.loads(source.read_text(encoding="utf-8"))
    return _default_patterns


def body_rules(documents: Mapping[str, str], patterns: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Full-text scan for rules a class alone cannot express: AI trailer ban, human voice.

    Returns {rule: [{file, line, quote}]}; an empty list means no signal found."""
    data = patterns or load_patterns()
    ai = compile_ai_term(data)
    found: dict[str, list[dict[str, Any]]] = {"ai_trailer_ban": [], "human_voice": []}
    for path, text in sorted(documents.items()):
        document_ai = bool(ai.search(text))
        for number, line in enumerate(text.splitlines(), 1):
            quote = clip_quote(line)
            if TRAILER_BAN.search(line):
                found["ai_trailer_ban"].append({"file": path, "line": number, "quote": quote})
            if document_ai and VOICE.search(line):
                found["human_voice"].append({"file": path, "line": number, "quote": quote})
    return found


def body_flags(classification: Mapping[str, Any]) -> dict[str, bool]:
    """Boolean flags derived from a classification: banned, restricted, disclosure_required,
    ai_trailer_ban, human_voice. Falls back to evidence quotes when body_rules is absent."""
    signals = set(classification.get("signals") or []) | {classification.get("verdict")}
    stored = classification.get("body_rules") or {}
    trailer, voice = bool(stored.get("ai_trailer_ban")), bool(stored.get("human_voice"))
    for item in classification.get("evidence") or []:
        quote = item.get("quote", "") if isinstance(item, dict) else ""
        trailer = trailer or bool(TRAILER_BAN.search(quote))
        voice = voice or bool(VOICE.search(quote))
    return {"banned": "banned" in signals, "restricted": "restricted" in signals,
            "disclosure_required": "disclosure-required" in signals,
            "ai_trailer_ban": trailer, "human_voice": voice}


FENCE_OPEN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
EXAMPLE_HEADING = re.compile(r"^(?:an?\s+)?(?:example|sample|snippet|demo)s?\b", re.I)
RST_BLOCK = re.compile(r"^(\s*)(?:\.\.\s+(?:code-block|code|sourcecode|parsed-literal)::.*|.*::)\s*$")


def strip_code(text: str) -> str:
    """Blank out text that only illustrates: fenced code blocks, reStructuredText literal blocks and
    sections headed "Example(s)". Line numbers are preserved (blank lines stay), so evidence still
    points at the real source line. An unclosed fence is left alone."""
    lines = text.splitlines()
    out = list(lines)
    i = 0
    while i < len(lines):
        fence = FENCE_OPEN.match(lines[i])
        if fence:
            marker = fence.group(1)
            close = re.compile(r"^\s{0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            j = i + 1
            while j < len(lines) and not close.match(lines[j]):
                j += 1
            if j < len(lines):
                for k in range(i, j + 1):
                    out[k] = ""
                i = j + 1
                continue
        i += 1
    i = 0
    while i < len(lines):  # reStructuredText literal blocks: indented lines after "::"
        block = RST_BLOCK.match(lines[i]) if "::" in lines[i] and not lines[i].lstrip().startswith("#") else None
        if block:
            indent = len(block.group(1))
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                j += 1
            for k in range(i + 1, j):
                out[k] = ""
            i = j
            continue
        i += 1
    level = None  # inside an "Example" section until a heading of the same or a higher level
    for i, line in enumerate(lines):
        heading = HEADING.match(line)
        if heading:
            depth = len(heading.group(1))
            if level is not None and depth <= level:
                level = None
            if level is None and EXAMPLE_HEADING.match(heading.group(2).strip(" *_`")):
                level = depth
        if level is not None:
            out[i] = ""
    return "\n".join(out)


LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s")


def _paragraphs(lines: list[str]) -> list[tuple[int, int, str, list[int]]]:
    """Paragraph context handles wrapped prose while keeping the exact source line.

    Each paragraph also carries the offsets where a list item starts. Items are separate sentences:
    a "." joins an item to the previous one so that no rule matches across two items."""
    paragraphs, start = [], 0
    for i in range(len(lines) + 1):
        if i == len(lines) or not lines[i].strip():
            if i > start:
                offsets, position, text, in_list = [], 0, lines[start], bool(LIST_ITEM.match(lines[start]))
                position = len(lines[start]) + 1
                for k in range(start + 1, i):
                    item = bool(LIST_ITEM.match(lines[k]))
                    if item and in_list:
                        offsets.append(position)
                    text += ("." if item and in_list else " ") + lines[k]
                    in_list = in_list or item
                    position += len(lines[k]) + 1
                paragraphs.append((start, i, text, offsets))
            start = i + 1
    # A paragraph that ends with ":" (possibly closing emphasis) introduces the list that follows it: one context.
    merged: list[tuple[int, int, str, list[int]]] = []
    for item in paragraphs:
        if (merged and re.search(r":[*_]*\s*$", merged[-1][2]) and LIST_ITEM.match(lines[item[0]])
                and item[0] - merged[-1][1] <= 2):
            first = merged.pop()
            shift = len(first[2]) + 1
            merged.append((first[0], item[1], first[2] + " " + item[2],
                           first[3] + [shift] + [x + shift for x in item[3]]))
        else:
            merged.append(item)
    return merged


def _boundaries(paragraph: str, item_starts: list[int] = ()) -> list[int]:
    """Sentence starts. An exception in one sentence must not hide a stricter rule in another.
    Abbreviations such as "e.g. hallucinated APIs" and open parentheses do not end a sentence."""
    out = list(item_starts)
    for boundary in re.finditer(r"(?<=[.!?])[*_`)\]\"']{0,4}\s+(?=[A-Z\U0001F300-\U0001FAFF])", paragraph):
        prefix = paragraph[:boundary.start()]
        if re.search(r"\b(?:e\.g\.|i\.e\.|etc\.)$", prefix, re.I):
            continue
        if prefix.count("(") > prefix.count(")"):
            continue
        out.append(boundary.end())
    return sorted(set(out))


PROHIBITIVE_HEADING = re.compile(
    r"\b(?:unacceptable|not allowed|not permitted|not accepted|prohibited|forbidden|banned|disallowed|"
    r"don'?ts?|do not|never|avoid|what not to)\b", re.I)
HEADING_LIKE = re.compile(r"^\s{0,3}(?:#{1,6}\s+(.*?)|(?::octicon:\S+\s+)?\*\*([^*]{2,60}?)\*\*:?)\s*#*\s*$")


def _prohibitive_lines(lines: list[str]) -> list[bool]:
    """For every line: does the nearest heading above it (or the line itself) announce prohibitions?
    A heading is a Markdown heading or a line that is only bold text."""
    out, current = [], False
    for line in lines:
        found = HEADING_LIKE.match(line)
        if found:
            current = bool(PROHIBITIVE_HEADING.search(found.group(1) or found.group(2) or ""))
        out.append(current)
    return out


def compile_ai_term(data: Mapping[str, Any]) -> re.Pattern[str]:
    """`ai_term_exact` is case sensitive ("AI", "LLM"); `ai_term` is not."""
    exact = data.get("ai_term_exact")
    base = data["ai_term"]
    return re.compile(f"(?:{exact})|(?i:{base})") if exact else re.compile(base, re.I)


class _Rules:
    """Compiled form of rules/patterns.json.

    Rule keys: id, class, regex (case-insensitive, run on the paragraph); optional
      paths             regex on the file path: the rule applies only to such files
      exclude           regexes; the match's sentence is skipped when one matches
      require / forbid  names from `named`: the sentence must match all / none of them
      narrow            skip a sentence whose only target is a narrow thing (descriptions, comments, media):
                        once those phrases are removed no broad thing (contribution, pull request, code) is left
      negate            name of a negation pattern; a negated sentence is skipped
      forbid_paragraph  regexes tested on the whole paragraph
      forbid_document   regexes tested on the whole file: a carve-out elsewhere in the file ("AI only for
                        autocompletion") means a prohibition in it is not scope-wide
      ctx_waives        names from `require` that need not match when the paragraph sits under a
                        prohibitive heading ("Unacceptable uses", "Do not"): the heading is the prohibition
      ungated           the rule also runs on paragraphs that never mention AI (agent instruction files)
      weak              true: the rule is a loose heuristic; its evidence carries confidence "low"
      demote_on         names from `named`: a sentence that matches one still fires, but as `demote_to`
                        (default "restricted") with confidence "low" instead of the rule's own class. A ban
                        that carries a narrowing qualifier ("unreviewed", "fully", "unless") is not a full ban.
    `named` entries carry their own inline flags; the special name `ai_sentence` is the AI term. The special name
    `narrowing_qualifiers` is the shared qualifier list: the top-level `narrowing_qualifiers` array of regexes
    (an entry "@name" includes the named pattern `name`)."""

    def __init__(self, data: Mapping[str, Any]):
        self.ai = compile_ai_term(data)
        self.named = {k: re.compile(v) for k, v in data.get("named", {}).items()}
        self.named["ai_sentence"] = self.ai
        parts = [(data["named"][x[1:]] if x.startswith("@") else x) for x in data.get("narrowing_qualifiers", [])]
        self.named["narrowing_qualifiers"] = re.compile("|".join(f"(?:{x})" for x in parts) or r"(?!)", re.I)
        review = data.get("review_cue", {})
        self.review_paths = re.compile(review["paths"], re.I) if review.get("paths") else None
        self.review_cue = re.compile(review["cue"], re.I) if review.get("cue") else None
        self.review_exclude = [re.compile(x, re.I) for x in review.get("exclude", [])]
        self.review_verbs = re.compile(review["soft_verbs"], re.I) if review.get("soft_verbs") else None
        self.cue = re.compile(data.get("related_cue", r"vouch|unreviewed|reviewed.*contributor|own human voice"), re.I)
        self.rules = []
        for rule in data["rules"]:
            self.rules.append((
                rule, re.compile(rule["regex"], re.I),
                re.compile(rule["paths"], re.I) if rule.get("paths") else None,
                [re.compile(x, re.I) for x in rule.get("exclude", [])],
                [re.compile(x, re.I) for x in rule.get("forbid_paragraph", [])],
                [re.compile(x, re.I) for x in rule.get("forbid_document", [])]))

    def review_worthy(self, path: str, paragraph: str) -> bool:
        """README and agent-instruction files talk about AI as the product or as a tool to command ("Claude must run
        the tests"). Their paragraphs count as AI-policy text only when they also speak about contributing.
        A paragraph that says outright that there is no AI policy is a statement of absence, not a policy."""
        if any(x.search(paragraph) for x in self.review_exclude):
            return False
        if self.review_paths and self.review_paths.search(path):
            return bool(self.review_cue and self.review_cue.search(paragraph))
        return True

    def any_ungated(self, path: str) -> bool:
        return any(r.get("ungated") and (paths is None or paths.search(path)) for r, _, paths, _, _, _ in self.rules)

    def judge(self, rule: Mapping[str, Any], excludes: list[re.Pattern[str]], sentence: str,
              prohibitive: bool = False) -> str | None:
        """None: the sentence is not a signal of this rule. "ok": it is. "demote": it is, but carries a
        narrowing qualifier, so the rule's class is downgraded and the evidence is low confidence."""
        if any(x.search(sentence) for x in excludes):
            return None
        waived = rule.get("ctx_waives", []) if prohibitive else []
        if any(not self.named[n].search(sentence) for n in rule.get("require", []) if n not in waived):
            return None
        if any(self.named[n].search(sentence) for n in rule.get("forbid", [])):
            return None
        if rule.get("negate") and self.named[rule["negate"]].search(sentence):
            return None
        if rule.get("narrow"):  # a sentence about descriptions, comments or media only names no broad thing
            rest = self.named["narrow"].sub(" ", sentence)
            if rest != sentence and not self.named["broad"].search(rest):
                return None
        if any(self.named[n].search(sentence) for n in rule.get("demote_on", [])):
            return "demote"
        return "ok"


def _line_of(lines: list[str], start: int, offset: int) -> int:
    """Source line index of `offset` characters into the paragraph that starts at line `start`."""
    line = start
    while line < len(lines) - 1 and offset >= len(lines[line]) + 1:
        offset -= len(lines[line]) + 1
        line += 1
    return line


def _is_policy_paragraph(paragraph: str, pointer: re.Pattern[str] | None,
                         soft: re.Pattern[str] | None = None) -> re.Match[str] | None:
    """An AI term and a policy verb in one paragraph (the test of discover.has_ai_policy_text, widened by the
    soft verbs of rules/patterns.json: "encourage", "please", "feel free", ... because policies written in a
    welcoming voice are still policies). Returns the match of the AI term, or None. A paragraph that only
    points at the organisation policy is no policy of its own."""
    found = AI_WORDS.search(paragraph) or AI_PHRASES.search(paragraph) or AGENT_GENERATED.search(paragraph)
    if not found or (pointer and pointer.search(paragraph)):
        return None
    if POLICY_VERBS.search(paragraph) or (soft and soft.search(paragraph)):
        return found
    return None


def decide(evidence: list[ClassifiedEvidence], review: list[dict[str, Any]]) -> tuple[str, str | None, str]:
    """Verdict, review reason and confidence from the evidence and the uncovered AI-policy paragraphs.

    Precedence (docs/schema.md "Decision order"):
      1. conflicting evidence that strongest-wins cannot resolve credibly -> needs-review (conflicting-signals):
         a welcome next to a full ban; or a restriction that exists only as a demoted ban (a ban sentence with a
         narrowing qualifier) next to any weaker signal (welcome, disclosure)
      2. any evidence: strongest signal wins (banned > restricted > disclosure-required > permitted)
      3. no evidence but AI-policy paragraphs: needs-review (no-rule-decision)
      4. nothing: no-policy-found
    Confidence is "low" when the deciding signal is one sentence, comes only from weak or demoted evidence,
    and always for needs-review."""
    signals = {x["signal"] for x in evidence}
    strict = {x["signal"] for x in evidence if not x["demoted"]}  # evidence that is not a qualified-ban demotion
    if BANNED in strict and PERMITTED in strict:
        return NEEDS_REVIEW, "conflicting-signals", LOW  # "AI is welcome" next to "AI is banned"
    if signals & {BANNED, RESTRICTED} and not strict & {BANNED, RESTRICTED}:
        # Every restriction is a demoted ban whose scope is unclear. Against any weaker evidence (welcome,
        # disclosure) there is no credible winner; alone it stays a low-confidence restricted.
        if signals & {PERMITTED, DISCLOSURE_REQUIRED}:
            return NEEDS_REVIEW, "conflicting-signals", LOW
    if signals:
        verdict = verdict_from_signals(signals)
        deciding = [x for x in evidence if x["signal"] == verdict]
        sentences = {(x["file"], x["sentence"]) for x in deciding}
        strong = any(x["confidence"] == HIGH for x in deciding)
        return verdict, None, HIGH if strong and len(sentences) >= 2 else LOW
    if review:
        return NEEDS_REVIEW, "no-rule-decision", LOW
    return NO_POLICY_FOUND, None, HIGH


def classify_documents(documents: Mapping[str, str], patterns: dict[str, Any] | None = None,
                       owner: str | None = None) -> Classification:
    """Pure classifier: mapping of repository-relative path -> unmodified UTF-8 text.

    `owner` (optional) lets a paragraph that only links into the owner's `.github` repository count as a pointer,
    not as AI-policy text of its own."""
    data = patterns or load_patterns()
    rules = _Rules(data)
    ai = rules.ai
    pointer = re.compile(r"github\.com/" + re.escape(owner) + r"/\.github(?![\w-])", re.I) if owner else None
    evidence: list[ClassifiedEvidence] = []
    mentions: list[dict[str, Any]] = []
    policy_paragraphs: list[dict[str, Any]] = []
    covered: set[tuple[str, int]] = set()
    for path, raw in sorted(documents.items()):
        text = strip_code(raw)
        lines = text.splitlines()
        document_ai = bool(ai.search(text))
        prohibitive = _prohibitive_lines(lines)
        for start, end, paragraph, item_starts in _paragraphs(lines):
            paragraph = paragraph.replace("*", " ")  # emphasis marks must not break "must **not** use"; offsets stay
            # A heading by itself is not a policy rule.
            if all(re.match(r"^\s*#{1,6}\s", line) for line in lines[start:end]):
                continue
            term = _is_policy_paragraph(paragraph, pointer, rules.review_verbs)
            if term and rules.review_worthy(path, paragraph):
                line = _line_of(lines, start, term.start())
                policy_paragraphs.append({"file": path, "line": line + 1, "quote": clip_quote(lines[line]),
                                          "key": (path, start)})
            gated = bool(ai.search(paragraph) or (document_ai and rules.cue.search(paragraph)))
            if not gated and not rules.any_ungated(path):
                continue
            bounds = _boundaries(paragraph, item_starts)
            found = False
            for rule, regex, rule_paths, excludes, paragraph_forbids, document_forbids in rules.rules:
                if rule_paths and not rule_paths.search(path):
                    continue
                if any(x.search(text) for x in document_forbids):
                    continue
                if not gated and not rule.get("ungated"):
                    continue
                if any(x.search(paragraph) for x in paragraph_forbids):
                    continue
                for match in regex.finditer(paragraph):
                    left = max((x for x in bounds if x <= match.start()), default=0)
                    right = min((x for x in bounds if x >= match.end()), default=len(paragraph))
                    verdict = rules.judge(rule, excludes, paragraph[left:right], prohibitive[start])
                    if verdict is None:
                        continue
                    demoted = verdict == "demote"
                    line = _line_of(lines, start, match.start())
                    evidence.append({
                        "file": path, "line": line + 1,
                        "signal": rule.get("demote_to", RESTRICTED) if demoted else rule["class"],
                        "quote": clip_quote(lines[line]), "rule": rule["id"],
                        "confidence": LOW if demoted or rule.get("weak") else HIGH,
                        "sentence": f"{start}:{left}", "demoted": demoted})
                    found = True
            if found:
                covered.add((path, start))
            elif gated:
                mentions.append({"file": path, "line": start + 1, "quote": clip_quote(lines[start])})
    unique = {tuple(x.items()): x for x in evidence}
    ordered = sorted(unique.values(), key=lambda x: (-SEVERITY[x["signal"]], x["file"], x["line"], x["rule"]))
    signals = sorted({x["signal"] for x in ordered}, key=SEVERITY.get, reverse=True)
    review = [{k: v for k, v in x.items() if k != "key"} for x in policy_paragraphs if x["key"] not in covered]
    verdict, reason, confidence = decide(ordered, review)
    return {"verdict": verdict, "confidence": confidence, "signals": signals,
            "evidence": ordered, "unclassified_ai_lines": mentions,
            "review": review[:MAX_REVIEW_PARAGRAPHS] if verdict == NEEDS_REVIEW else [],
            "review_reason": reason, "body_rules": body_rules(documents, data)}
