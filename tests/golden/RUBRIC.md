# Gold set labeling rubric

Scope: ground truth for measuring the accuracy of oss-ai-policy-check. One JSON object per line in `gold.jsonl`.
Scan date: 2026-11-22. Every label is valid only for the pinned `commit_sha` in its row.

## Row schema

```
{"repo":"owner/name","commit_sha":"<40 hex>","verdict":"banned|restricted|disclosure-required|permitted|no-policy-found",
 "source":"repo|org","evidence":[{"file":"path","line":N,"quote":"<=15 words"}],
 "confidence":"high|medium|low","note":"short"}
```

- `commit_sha` is the default-branch HEAD at scan time. For `source: org` it is the SHA of the org `.github` repo, `evidence.file` reads `owner/.github:path`, and the note ends with `(repo sha xxxxxxxxxx)` for the repo itself.
- `quote` is a verbatim fragment of at most 15 words (markup, quote characters and case ignored when matching). `line` is the line where the quote starts (a quote may wrap onto the next line). No policy text is copied beyond the quote.
- `no-policy-found` rows have an empty evidence list; the note says what was scanned instead.
- Every quote was machine-checked against the file at the pinned SHA; 6 sampled rows were re-fetched live from GitHub at their SHA and matched.

## Scanned files (the only evidence source)

AI_POLICY*, CONTRIBUTING* (root, docs/, doc/, .github/, Documentation/, contributing/, governance/, policies/), .github/PULL_REQUEST_TEMPLATE* (checkboxes count), AGENTS.md, CLAUDE.md, GEMINI.md, .cursorrules, CODE_OF_CONDUCT, README (contributing section), plus any file whose name carries policy, ai, llm, generative, provenance, governance or copilot within those folders. For llvm-project an extras list adds the AI tool policy page in docs/.
Anything outside these files (wiki, project website, devguide, GitHub discussion) is not evidence; when the real policy is known to live there the row gets confidence low or medium and says so.

## Verdict decision procedure (most restrictive signal wins)

1. **banned**: AI-generated contributions are not accepted at all (code, or PRs and issues). Tolerating AI for proofreading or translation does not downgrade a ban. Closing "AI slop" is not a ban; refusing AI-made content as such is.
2. **restricted**: AI use is allowed but at least one AI-specific hard limit applies beyond disclosure and generic accountability. Qualifying limits:
   - AI-written PR descriptions, issues, comments or review replies are forbidden
   - AI co-author or attribution trailers forbidden
   - AI PRs only for already accepted issues, or good-first-issue ban
   - AI media/assets forbidden
   - autonomous agents or AI review bots forbidden (a plain "purely agentic PRs refused" sentence alone is generic accountability, not a limit)
   - issues instead of PRs
   - AI only for autocomplete or mechanical edits
3. **disclosure-required**: AI allowed; the only AI-specific ask is to disclose use (required or requested, checkbox, trailer, label), plus generic accountability (review, understand, test).
4. **permitted**: explicit positive statement that AI-assisted work is welcome or okay, with no disclosure ask and no hard limit; only generic accountability.
5. **no-policy-found**: nothing about AI contribution in the scanned files.

Tie-breaks:
- Several signals: choose the most restrictive that is actually in a scanned file.
- Org-level `<owner>/.github` policy applies when the repo itself has none: `source: org`. If the repo has its own rule, that one is used (`source: repo`).
- AGENTS.md / CLAUDE.md that only give build, style or test guidance to coding agents: **no-policy-found**, confidence medium, note `agent-instructions-only`. If the file states a contribution rule (disclosure, ban, limits), it counts like any other scanned file.
- AI as the product (README of an AI library) or "generated" files are not policy.

## Negation traps

Wording like "we do not ban AI", "does not prohibit", "no need to disclose" must not be read as the opposite. These rows start their note with `negation:` (ghostty-org/ghostty, RustPython/RustPython, nodejs/node, jax-ml/jax, fedify-dev/fedify, flutter/flutter, aaif-goose/goose, rust-lang/rust). Labels follow the whole policy, not the negated sentence.

## Confidence

- **high**: policy is in the scanned files, wording is explicit, verdict clear.
- **medium**: wording is soft ("please", "prefer"), rule lives only in AGENTS.md or a PR template, the verdict sits on a boundary (banned vs restricted, disclosure vs restricted, permitted vs restricted), or the fuller policy is on an external page that is probably stricter or equal.
- **low**: real policy is outside scanned files, signals are mixed, or the single signal is one weak sentence. Each low case is explained in NOTES.md. A scorer may report accuracy with and without low rows.

## Known limits

- Snapshot labels: policies change; always evaluate the tool at the pinned SHA, never at HEAD.
- Policies on external sites are invisible by design (the tool has the same limit); such rows are low or medium.
- Verdict classes are not balanced like the real world: restricted is deliberately large (70 of 151) because most modern written policies carry at least one hard limit.
- Selection is seeded from melissawm/open-source-ai-contribution-policies plus mandatory repos, so policy-bearing repos are over-represented relative to GitHub as a whole.
- Counts: banned 21, restricted 70, permitted 8, disclosure-required 30, no-policy-found 22; confidence high 86, medium 53, low 12.
