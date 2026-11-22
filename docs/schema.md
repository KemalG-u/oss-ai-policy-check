# Report contract (schema_version "2")

Machine-readable schema: `schema/report.schema.json` (JSON Schema 2020-12). Source of truth in code:
`src/oss_ai_policy_check/contract.py`. Changing anything here is a public API change (see `CONTRIBUTING-DEV.md`).

## Command line

```
oss-ai-policy-check OWNER/REPO [--ref REF] [--format text|json|markdown|sarif]
                               [--path LOCAL_DIR]
                               [--fail-on banned|restricted|disclosure-required|needs-review]
                               [--no-cache] [--no-org]
                               [--classifier rules|claude] [--model ID]
```

`--classifier claude` is optional and off by default: the deterministic rules need no key and no extra package.
With it, Claude judges only the AI-policy paragraphs and names the deciding ones; evidence lines and links are
still built from those ids (see `docs/llm-mode.md`). `--model` picks the model and needs `--classifier claude`.

`--path` reads a local clone of OWNER/REPO instead of GitHub (it cannot be combined with `--ref`;
the commit SHA is read from `.git/HEAD` without running git). All interface text, field names and
messages are English.

`--no-cache` skips the on-disk cache of fetched files (keyed by repository and commit SHA). `--no-org`
does not read the owner's organization `.github` repository. Both only affect a GitHub read, not `--path`.
No `gh` CLI is needed; `GITHUB_TOKEN` or `GH_TOKEN` is optional and raises the rate limit.

## Verdict

One value, strongest first:

| verdict | meaning |
|---|---|
| `banned` | a quoted line says AI contributions are not accepted |
| `restricted` | AI use is conditional (human in the loop, no attribution trailers, limited scopes) |
| `disclosure-required` | AI use must be disclosed or labelled |
| `needs-review` | the tool abstains: the files carry AI-policy text it cannot decide on credibly. A person must read the cited paragraphs. This is not permission |
| `permitted` | only with explicit, positive evidence; never inferred from silence |
| `no-policy-found` | no AI-policy text in the scanned scope. This is not permission |

`needs-review` ranks below `disclosure-required` (it never outranks a quoted restriction) and above
`permitted` (an abstention is not permission). It is returned in two situations (four reasons in Claude mode), named by
`review_reason`:

- `no-rule-decision`: at least one paragraph contains an AI term and a policy verb (the same test as the
  organization-lookup gate, `discover.has_ai_policy_text`), but no rule produced evidence. The evidence is
  those paragraphs, each with signal `needs-review`.
- `conflicting-signals`: rules fired, but the order of precedence below cannot settle them credibly. The
  evidence is the classified lines that conflict, each with its own signal.
- `llm-low-confidence` and `llm-rules-disagree`: written only by `--classifier claude` when the model and the rules
  do not agree (table in `docs/llm-mode.md`, "How the verdicts are combined"). The evidence is the model's
  deciding lines together with the rules' lines, each with its own signal.

`no-policy-found` is returned only when no such paragraph exists. A paragraph counts as AI-policy text when
it names an AI term and a policy verb together, outside code blocks and "Example" sections, with three
exceptions: a paragraph that only links into the owner's `.github` repository is a pointer, a paragraph
that says outright that there is no AI policy ("we do not have an AI policy") is a statement of absence,
and in a `README` or an agent instruction file (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`,
`copilot-instructions`, `.cursorrules`) the paragraph must also speak about contributing (contribution, pull
request, patch, commit, issue, policy, disclosure), because those files mostly describe a product or give
commands to a tool.

`complete` is the "incomplete" flag: `false` when any candidate file could not be read, a tree was
truncated, the file limit was hit, or repository discovery failed. It is not a verdict. When
`complete` is `false` and the verdict is `no-policy-found` or `permitted`, no decision is possible.

### Decision order

The classifier applies these steps in order and stops at the first that applies:

1. **Demote qualified bans.** A sentence that matches the ban rule but carries a narrowing qualifier
   (`raw`, `unverified`, `unreviewed`, `fully`, `entirely`, `largely`, `wall of`, `to disguise`,
   `submitted without review`, `mass-produced`, `low-effort`, `unless`, `except`, and the other entries of the
   shared list `narrowing_qualifiers` in `rules/patterns.json`) is not a full ban. It becomes `restricted`
   evidence with `confidence: "low"`.
2. **Conflict check** (`needs-review`, `conflicting-signals`):
   a. an undemoted `banned` line and a `permitted` line (a welcome next to a ban);
   b. a restriction that exists only as demoted evidence next to any weaker signal (`permitted` or
      `disclosure-required`): the sentence says something restrictive of unclear scope, and the weaker rule
      may be the real policy.
3. **Strongest signal wins:** `banned`, then `restricted`, then `disclosure-required`, then `permitted`. No
   file outranks another. A firm (undemoted) restriction beats a welcome; the verdict is `restricted`.
4. **Abstain** (`needs-review`, `no-rule-decision`) when there is no evidence but an AI-policy paragraph.
5. **`no-policy-found`.**

### Confidence

`confidence` is `"high"` or `"low"`, on the report and on every evidence item.

- An evidence item is `low` when its rule is marked `"weak": true` in `rules/patterns.json` (a loose heuristic
  such as the "AI may not write your descriptions" rule or the pull-request-template prompt), or when it is a
  demoted ban (step 1). Otherwise `high`.
- The report is `high` when the evidence that decides the verdict (the items whose signal equals the verdict)
  holds at least one `high` item and comes from at least two distinct sentences. It is `low` when the deciding
  signal is a single sentence, or only weak or demoted evidence.
- `no-policy-found` is `high` when `complete` is `true`, else `low`. `permitted` follows the evidence rule and
  is also `low` when `complete` is `false`. `needs-review` is always `low`.

`confidence` is about the classification of what was read, not about reading: use `complete` for that.
A `low` verdict is still the best reading of the text; treat it as a reason to open the quoted line.

## JSON report

```
{
  "schema_version": "2",
  "repo": "owner/name",
  "ref": "main" | null,
  "commit_sha": "<40 hex>" | null,
  "org_commit_sha": "<40 hex>" | null,
  "verdict": "banned|restricted|disclosure-required|needs-review|permitted|no-policy-found",
  "confidence": "high|low",
  "review_reason": "no-rule-decision|conflicting-signals|llm-low-confidence|llm-rules-disagree" | null,
  "complete": true,
  "evidence": [
    {"file": "docs/AI.md", "line": 12, "url": "https://github.com/owner/name/blob/<sha>/docs/AI.md#L12",
     "signal": "restricted", "confidence": "high", "quote": "at most 15 words from the source line"}
  ],
  "unclassified_ai_lines": [{"file": "CONTRIBUTING.md", "line": 41, "quote": "..."}],
  "files_checked": [{"path": "CONTRIBUTING.md", "status": "read|missing|error"},
                    {"path": "org:.github/AI_POLICY.md", "status": "read"}],
  "warnings": ["read failed: docs/x.md (HTTP 403)"],
  "tool": {"name": "oss-ai-policy-check", "version": "0.3.0"},
  "classifier": {"kind": "rules|claude", "model": null}
}
```

- `confidence` and `review_reason` are required. `review_reason` is `null` unless the verdict is
  `needs-review`.
- `evidence[].url` is a permalink pinned to `commit_sha`. It is `null` only when the SHA is unknown
  (a local directory that is not a git clone).
- Evidence whose `file` starts with `org:.github/` comes from the owner's `.github` repository (read unless
  the repository itself states an AI policy: an AI-policy-named file, or a paragraph pairing an AI term with
  a policy verb; its CONTRIBUTING and AI policy files are read even when the repository has its own
  CONTRIBUTING, and the repository text wins at that gate only). Its `url` is
  pinned to `org_commit_sha`: `https://github.com/<owner>/.github/blob/<org_commit_sha>/<path>#L<line>`, and
  is `null` when `org_commit_sha` is unknown.
- `org_commit_sha` is the commit SHA of the owner's `.github` repository when org files were read, else `null`.
- `evidence[].quote` is a literal excerpt of the source line, at most 15 words and at most 300
  characters (a longer excerpt is cut and ends with `…`). The tool never reproduces whole policy texts.
- `evidence[].signal` is one of `banned`, `restricted`, `disclosure-required`, `permitted`, `needs-review`.
  `needs-review` marks an AI-policy paragraph no rule decided on (reason `no-rule-decision`); at most 20 such
  paragraphs are listed, in file and line order. With `conflicting-signals` the evidence keeps the
  conflicting lines under their own signals.
- `unclassified_ai_lines` lists paragraphs that mention AI but matched no rule. They are shown, not hidden.
- `files_checked[].path` may carry the `org:.github/` prefix for files of the owner's `.github` repository.
- `files_checked[].status`: `read` (text obtained), `missing` (the file does not exist: "no policy
  there"), `error` (could not be read: "unknown"). `missing` does not make the report incomplete;
  `error` does.
- `classifier` is optional (reports written by older tool versions have none; a consumer treats a missing field as
  `{"kind": "rules", "model": null}`). The tool always writes it. `kind` is `rules` (the deterministic classifier) or
  `claude` (`--classifier claude`, `model` is the model id). When a Claude call fails the verdict is the
  deterministic one, so `kind` stays `rules`, `complete` is `false` and `warnings` says why. Adding an optional field
  keeps `schema_version` at `"2"`.
- `warnings` carries the human-readable reasons behind `complete: false` (the one field beyond the
  base contract, kept so incompleteness is explainable).

## Exit codes

| code | meaning |
|---|---|
| 0 | `permitted`, `no-policy-found`, `disclosure-required` or `needs-review` (and read complete, or a stricter verdict was not needed) |
| 10 | `restricted` |
| 20 | `banned` |
| 2 | usage error (bad arguments, unknown format, unreadable `--path`, format not implemented yet) |
| 3 | incomplete and undecided: `complete: false` with verdict `no-policy-found` or `permitted` |
| 11 | `disclosure-required`, only with `--fail-on disclosure-required` or `--fail-on needs-review` |
| 12 | `needs-review`, only with `--fail-on needs-review` |

`--fail-on V` (for the GitHub Action): a verdict at least as strict as `V` exits with its own code
(banned 20, restricted 10, disclosure-required 11, needs-review 12); anything milder exits 0. The order
is `banned`, `restricted`, `disclosure-required`, `needs-review`, so `--fail-on needs-review` fails on every
verdict that is not `permitted` or `no-policy-found`, and `--fail-on disclosure-required` does not fail on an
abstention. Without `--fail-on` the first table applies: like `disclosure-required`, `needs-review` exits 0
unless a gate asks for it, so a script that wants to stop on an abstention must pass `--fail-on needs-review`
or read `verdict`. A `banned` or `restricted` verdict stands even when `complete` is `false`; code 3 is reserved
for the case where nothing restrictive was found but reading was incomplete. An abstention is not an
incomplete read: with `complete: false` it still exits 12 under `--fail-on needs-review`.

## Migration from schema_version "1"

Version 2 adds an enum value, so a strict consumer that validated `verdict` against the five version 1
values would reject a report. The changes:

| change | what a consumer does |
|---|---|
| `schema_version` is `"2"` | accept `"2"` (a `"1"` report from an older tool still has no `confidence`) |
| new verdict `needs-review` (between `disclosure-required` and `permitted`) | treat it as "no decision, a person must read the evidence"; never as `permitted` or `no-policy-found` |
| new required `confidence` on the report and on each evidence item | `"high"` or `"low"`; a gate that wants only firm results can require `high` |
| new required `review_reason` (`null` unless `needs-review`) | read it to tell an abstention from a conflict |
| new evidence `signal` `needs-review` | a paragraph to read, not a finding |
| new exit code 12 and `--fail-on needs-review` | optional; nothing changes for existing `--fail-on` values |
| a report that was `no-policy-found` may now be `needs-review` | that is the point: the text exists, the tool could not decide |

SARIF reports keep the `needs-review` rule at level `warning` and add `confidence` to each result's
`properties` and `confidence` and `reviewReason` to the run's `properties`. The GitHub Action output `verdict`
may be `needs-review`, and `exit-code` may be `12`.
