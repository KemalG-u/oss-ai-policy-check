"""Output formats. A Report (contract.Report) goes in, a string comes out.

Public API
    render(report, fmt) -> str
    render_text(report) -> str         # short human summary, first line = verdict + repo
    render_json(report) -> str
    render_markdown(report) -> str     # PR comment / GITHUB_STEP_SUMMARY
    render_sarif(report) -> str        # SARIF 2.1.0, one result per evidence line

All renderers are pure. Quotes and paths come from untrusted repository text, so every format
neutralises what it could be abused for: control and bidirectional-override characters everywhere,
a leading `::` (a GitHub workflow command) in text, markdown/HTML and @mentions in markdown.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import quote as url_quote

from .contract import (BANNED, DISCLAIMER, DISCLOSURE_REQUIRED, FILE_ERROR, FILE_MISSING,
                       FILE_READ, FORMATS, LIMITATIONS, NEEDS_REVIEW, NO_POLICY_FOUND, PERMITTED,
                       REVIEW_CONFLICT, RESTRICTED, SIGNALS, Report, blob_url)

INFORMATION_URI = "https://github.com/KemalG-u/oss-ai-policy-check"  # placeholder until the repo is public
SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
MARKDOWN_MARKER = "<!-- oss-ai-policy-check -->"  # lets a workflow find and update its own PR comment

# C0 and C1 controls, line/paragraph separators and the bidirectional controls that can reorder displayed text
# (LRM, RLM, ALM, embeddings and overrides U+202A-202E, isolates U+2066-2069).
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u061c\\u200e\\u200f\\u2028-\\u202e\\u2066-\\u2069]")

_MEANING = {
    BANNED: "A quoted line says AI contributions are not accepted.",
    RESTRICTED: "AI use is conditional (human in the loop, limited scopes, or similar).",
    DISCLOSURE_REQUIRED: "AI use must be disclosed or labelled.",
    NEEDS_REVIEW: "The files carry AI-policy text the tool cannot decide on credibly. A person must read it. "
                  "This is not permission.",
    PERMITTED: "Explicit, positive evidence that AI use is accepted.",
    NO_POLICY_FOUND: "Nothing classifiable was found in the scanned scope. This is not permission.",
}


def _clean(value: object) -> str:
    """One-line, control-free text (a hostile repo must not inject terminal escapes or newlines)."""
    return " ".join(_CONTROL.sub(" ", str(value)).split())


def _t(value: object) -> str:
    """`_clean` for the text report: also defuses a leading `::`, which GitHub Actions reads as a command."""
    text = _clean(value)
    return ": :" + text[2:] if text.startswith("::") else text


_REVIEW_WHY = {
    REVIEW_CONFLICT: "The cited lines point in different directions (for example a welcome next to a ban, or a "
                     "qualified ban next to a weaker rule) and no order of precedence settles it credibly.",
}
_REVIEW_WHY_DEFAULT = "These paragraphs name AI and state a rule, but no rule of the tool decided on them."


def _review_why(report: Report) -> str:
    return _REVIEW_WHY.get(report.get("review_reason") or "", _REVIEW_WHY_DEFAULT)


def _no_decision(report: Report) -> bool:
    return not report["complete"] and report["verdict"] in (NO_POLICY_FOUND, PERMITTED)


def _location_path(path: str) -> str:
    """Repository-relative path with forward slashes (a local --path run never leaks its directory)."""
    parts = [p for p in _clean(path).replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def render_json(report: Report) -> str:
    """The report as JSON. Quotes are repository text, so they get the same control-character cleaning as the
    other formats (JSON escaping alone leaves bidirectional overrides in place); the result stays valid JSON."""
    data = dict(report)
    data["evidence"] = [{**item, "quote": _clean(item["quote"])} for item in report["evidence"]]
    data["unclassified_ai_lines"] = [{**item, "quote": _clean(item["quote"])}
                                     for item in report["unclassified_ai_lines"]]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------- text

def render_text(report: Report) -> str:
    sha = report["commit_sha"]
    head = f"VERDICT {report['verdict']} - {_t(report['repo'])}"
    if report["ref"] or sha:
        head += f" @ {_t(report['ref']) if report['ref'] else '(no ref)'}" + (f" ({sha[:12]})" if sha else "")
    lines = [head, f"confidence: {report['confidence']}",
             f"read status: {'complete within scope' if report['complete'] else 'INCOMPLETE'}"]
    if report["verdict"] == NEEDS_REVIEW:
        lines.append(f"NEEDS HUMAN REVIEW ({report['review_reason']}): {_review_why(report)}")
    if _no_decision(report):
        lines.append("no decision: some files could not be read, so a ban or restriction may be missing.")
    if report["evidence"]:
        lines.append(f"evidence ({len(report['evidence'])}):")
        for item in report["evidence"]:
            lines.append(f"  - {item['signal']} ({item['confidence']}): {_t(item['file'])}:{item['line']} "
                         f"- {_t(item['quote'])}")
            if item["url"]:
                lines.append(f"    {item['url']}")
    if report["unclassified_ai_lines"]:
        lines.append(f"AI mentions without a classified rule ({len(report['unclassified_ai_lines'])}):")
        for item in report["unclassified_ai_lines"]:
            lines.append(f"  - {_t(item['file'])}:{item['line']} - {_t(item['quote'])}")
    counts = {status: sum(1 for f in report["files_checked"] if f["status"] == status)
              for status in (FILE_READ, FILE_MISSING, FILE_ERROR)}
    lines.append(f"files checked: {counts[FILE_READ]} read, {counts[FILE_MISSING]} missing, {counts[FILE_ERROR]} error")
    lines += ["WARNING: " + _t(w) for w in report["warnings"]]
    lines += ["LIMIT: " + text for text in LIMITATIONS]
    lines.append(DISCLAIMER)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- markdown

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|~#&$!])")


def _md(value: object) -> str:
    """Escape one-line untrusted text for markdown, including inside a table cell.

    Backslash-escapes markdown/HTML specials and `|`; a zero-width space after `@` stops GitHub from
    turning a quoted `@name` into a mention that notifies someone from a PR comment."""
    return _MD_SPECIAL.sub(r"\\\1", _clean(value)).replace("@", "@\u200b")


def _link(label: str, url: str | None) -> str:
    return f"[{_md(label)}]({url})" if url else _md(label)


def _alert(kind: str, *body: str) -> str:
    return "\n".join([f"> [!{kind}]"] + [f"> {line}" for line in body])


_ALERT_KIND = {BANNED: "CAUTION", RESTRICTED: "WARNING", DISCLOSURE_REQUIRED: "IMPORTANT",
               NEEDS_REVIEW: "WARNING", PERMITTED: "NOTE", NO_POLICY_FOUND: "NOTE"}


def render_markdown(report: Report) -> str:
    repo, sha, verdict = report["repo"], report["commit_sha"], report["verdict"]
    badge = verdict.upper() + (" (NO DECISION: INCOMPLETE READ)" if _no_decision(report) else "")
    repo_url = f"https://github.com/{url_quote(repo, safe='/')}"
    where = f"[{_md(repo)}]({repo_url})"
    if report["ref"]:
        where += " @ `" + _clean(report["ref"]).replace("`", "") + "`"
    if sha:
        where += f" ([`{sha[:12]}`]({repo_url}/commit/{sha}))"

    out = [MARKDOWN_MARKER, f"## AI policy check: {badge}", "", f"**Repository:** {where}", "",
           _alert(_ALERT_KIND[verdict], f"**{_md(badge)}.** {_MEANING[verdict]}"),
           "", f"**Confidence:** {report['confidence']}"]
    if not report["complete"]:
        tail = ("so a ban or restriction may be missing: there is no decision." if _no_decision(report)
                else "so the policy may be stricter than shown.")
        out += ["", _alert("WARNING", "**Incomplete read.** Some files could not be read or the scan "
                           "was cut short, " + tail)]
    if report["warnings"]:
        out += ["", "**Warnings**", ""] + [f"- {_md(w)}" for w in report["warnings"]]

    if verdict == NEEDS_REVIEW:
        out += ["", f"### Needs human review ({len(report['evidence'])})", "", _md(_review_why(report)), ""]
        out += [f"- {_link(item['file'] + ':' + str(item['line']), item['url'])}"
                + ("" if item["signal"] == NEEDS_REVIEW else f" (`{item['signal']}`, {item['confidence']} confidence)")
                + f" - {_md(item['quote'])}" for item in report["evidence"]] or ["- no paragraph could be cited"]
    else:
        out += ["", f"### Evidence ({len(report['evidence'])})", ""]
        if report["evidence"]:
            out += ["| Location | Signal | Confidence | Quote |", "| --- | --- | --- | --- |"]
            for item in report["evidence"]:
                out.append(f"| {_link(item['file'] + ':' + str(item['line']), item['url'])} "
                           f"| `{item['signal']}` | {item['confidence']} | {_md(item['quote'])} |")
        else:
            out.append("No quoted evidence line was found.")

    unclassified = report["unclassified_ai_lines"]
    if unclassified:
        out += ["", "<details>",
                f"<summary>AI mentions without a classified rule ({len(unclassified)})</summary>", ""]
        for item in unclassified:
            url = blob_url(repo, sha, item["file"], item["line"], report.get("org_commit_sha"))
            out.append(f"- {_link(item['file'] + ':' + str(item['line']), url)} - {_md(item['quote'])}")
        out += ["", "</details>"]

    counts = {s: sum(1 for f in report["files_checked"] if f["status"] == s)
              for s in (FILE_READ, FILE_MISSING, FILE_ERROR)}
    out += ["", "<details>",
            f"<summary>Files checked ({counts[FILE_READ]} read, {counts[FILE_MISSING]} missing, "
            f"{counts[FILE_ERROR]} error)</summary>", ""]
    listed = [f for f in report["files_checked"] if f["status"] != FILE_MISSING]
    out += [f"- {_md(f['path'])}: {f['status']}" for f in listed] or ["- no policy file could be read"]
    out += ["", "</details>", "",
            "<sub>" + " ".join(_md(text) for text in LIMITATIONS) + " " + _md(DISCLAIMER)
            + f" Tool: {_md(report['tool']['name'])} {_md(report['tool']['version'])}.</sub>"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- sarif

_SARIF_LEVEL = {BANNED: "error", RESTRICTED: "warning", DISCLOSURE_REQUIRED: "note", PERMITTED: "none",
                NEEDS_REVIEW: "warning"}
_RULE_TEXT = {
    BANNED: "The repository policy says AI contributions are not accepted.",
    RESTRICTED: "The repository allows AI use only under conditions.",
    DISCLOSURE_REQUIRED: "The repository requires AI use to be disclosed or labelled.",
    PERMITTED: "The repository explicitly accepts AI use.",
    NEEDS_REVIEW: "The repository has AI-policy text the tool cannot decide on; a person must read it.",
}


def render_sarif(report: Report) -> str:
    repo, sha = report["repo"], report["commit_sha"]
    rule_index = {signal: index for index, signal in enumerate(SIGNALS)}
    rules = [{"id": signal,
              "name": "".join(part.capitalize() for part in signal.split("-")),
              "shortDescription": {"text": _RULE_TEXT[signal]},
              "fullDescription": {"text": _RULE_TEXT[signal] + " Heuristic signal from a quoted line; not legal advice."},
              "defaultConfiguration": {"level": _SARIF_LEVEL[signal]},
              "properties": {"tags": ["ai-policy"]}} for signal in SIGNALS]

    results = []
    for item in report["evidence"]:
        path = _location_path(item["file"])
        quote_text = _clean(item["quote"])
        fingerprint = hashlib.sha256(f"{path}\n{item['signal']}\n{quote_text}".encode("utf-8")).hexdigest()
        result = {
            "ruleId": item["signal"], "ruleIndex": rule_index[item["signal"]],
            "level": _SARIF_LEVEL[item["signal"]],
            "message": {"text": f"AI policy signal '{item['signal']}': {quote_text}"},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": url_quote(path, safe="/"), "uriBaseId": "%SRCROOT%"},
                "region": {"startLine": max(1, int(item["line"])), "snippet": {"text": quote_text}}}}],
            "partialFingerprints": {"policyEvidence/v1": fingerprint},
        }
        result["properties"] = {"confidence": item["confidence"]}
        if item["url"]:
            result["properties"]["permalink"] = item["url"]
        results.append(result)

    run: dict = {
        "tool": {"driver": {"name": report["tool"]["name"], "version": report["tool"]["version"],
                            "informationUri": INFORMATION_URI, "rules": rules}},
        "automationDetails": {"id": f"{report['tool']['name']}/{repo}/"},
        "invocations": [{"executionSuccessful": True,
                         "toolExecutionNotifications": [{"level": "warning", "message": {"text": _clean(w)}}
                                                        for w in report["warnings"]]}],
        "results": results,
        "properties": {"verdict": report["verdict"], "confidence": report["confidence"],
                       "reviewReason": report["review_reason"], "complete": report["complete"],
                       "unclassifiedAiLines": len(report["unclassified_ai_lines"]),
                       "disclaimer": DISCLAIMER},
    }
    if sha:
        provenance = {"repositoryUri": f"https://github.com/{repo}", "revisionId": sha}
        if report["ref"]:
            provenance["branch"] = _clean(report["ref"])
        run["versionControlProvenance"] = [provenance]
    sarif = {"$schema": SARIF_SCHEMA, "version": SARIF_VERSION, "runs": [run]}
    return json.dumps(sarif, ensure_ascii=False, indent=2) + "\n"


_RENDERERS = {"text": render_text, "json": render_json,
              "markdown": render_markdown, "sarif": render_sarif}
assert tuple(_RENDERERS) == FORMATS


def render(report: Report, fmt: str) -> str:
    if fmt not in _RENDERERS:
        raise ValueError(f"unknown format: {fmt}")
    return _RENDERERS[fmt](report)
