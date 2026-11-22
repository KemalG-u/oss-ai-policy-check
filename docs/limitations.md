# Limitations

These are the known limits of version 0.2.0. A result is a heuristic signal, not legal advice, and never
permission to contribute with AI. The text of the policy itself is what counts.

## Classification

- **The classifier is regex based.** It matches wording, not meaning. It can miss a policy phrased in an
  unusual way and can flag a line that says something else.
- **Accuracy is measured, and it is moderate.** The tool is a finder: it locates the policy text and links
  the lines, and the class on top is a hint with a confidence level. On blind set B (60 unseen repositories,
  about half deliberately hard, commit de7caa1, 2026-11-22) the decided rows are 68.9% correct (31 of 45), 25.0%
  of repositories (15 of 60) come back as `needs-review`, one says "no policy" although one exists, and the
  overall exact verdict is 51.7% (31 of 60). On blind set A (60 unseen repositories, same commit and date,
  viewed before the `needs-review` change) the decided rows are 74.0% correct (37 of 50), 16.7% (10 of 60)
  abstain and none says "no policy" when one exists. Set B is the cleaner figure. Labels are one person's
  reading of the rubric, pinned to SHAs. The 151-repository gold set is a development set: every row was read
  while tuning, so its figures (below) are optimistic. The tables, the confusion matrix and the method are in
  the [accuracy section](#measured-accuracy) below; the design behind the abstentions is in
  [Abstention and confidence](#abstention-and-confidence).
- **Context is one paragraph.** A rule that depends on another section, a table or an image is not seen.
  Exclusions are tested against the sentence of the match only. Two exceptions are deliberate: a list
  under a heading such as "Unacceptable uses" is read as prohibitions, and a file that also allows AI for
  small autocompletion or "assistively only" turns its ban into a restriction.
- **Quoted or descriptive text can match.** A document that talks about AI policies, for example a tool
  README or a list of other projects' rules, can produce evidence from its own descriptive wording.
- **English only.** Rules and cue words are English.
- **`permitted` is rare and weak.** It needs an explicit welcome sentence ("AI-assisted contributions are
  welcome") and loses to any other signal. On the development set it was found for 5 of 8 labelled
  `permitted` repositories (precision 83%); of the 3 misses, 1 is a policy file that is read but holds
  accountability wording only, no welcome sentence, and 2 rest on labels the rubric does not support well. Silence is never read as permission.
- **Unclassified AI lines are only listed**, unless nothing else decides: a paragraph that mentions AI but
  matches no rule appears under `unclassified_ai_lines` and does not change a verdict that rules produced. When
  no rule fires at all and such a paragraph pairs an AI term with a policy verb, the verdict is `needs-review`.
- **One strongest signal decides.** A policy that bans AI for code but permits it for documentation is
  reported as `banned`; the scope is in the quoted lines, not in the verdict.

## Abstention and confidence

- **`needs-review` is an abstention, not a finding.** It means the files hold AI-policy text (an AI term and a
  policy verb in one paragraph) that no rule decided on, or that the evidence conflicts: a welcome next to a
  full ban, or a restriction that exists only as a ban sentence with a narrowing qualifier next to a weaker
  signal. A person has to read the cited paragraphs. The test for "AI-policy text" is cheap: it will also
  flag a paragraph about something else that happens to name an AI tool and a verb such as `should` or
  `please` (for example documentation about using an LLM for translation). Those abstentions are noise, but
  they are cheap to dismiss, and the alternative was silence on a real policy.
- **`low` confidence is a reason to look, not a verdict of its own.** It marks a verdict that rests on a single
  sentence, on a rule marked weak (the "do not use AI to write descriptions" family and the pull request
  template prompt), or on a ban sentence with a narrowing qualifier (reported as `restricted`). On the
  development set the `low` rows were right 87.7% of the time and the `high` rows 96.5% (both optimistic).
- **The conflict rules are narrower than "welcome next to a restriction".** A firm restriction (a trailer ban,
  an agent limit) next to a welcome is still `restricted`, because on the development set that is the usual
  shape of a restrictive policy that also says AI is fine; making it an abstention would have abstained on
  about a fifth of the labelled `restricted` repositories. Only a full ban, or a restriction that is nothing
  but a qualified ban, next to a welcome makes the tool step back.
- **The abstain rate on unseen repositories is higher than on the development set.** It is 6.0% (9 of 151) on
  the development set because the rules were tuned on those rows, and 16.7% (10 of 60) and 25.0% (15 of 60) on
  the two blind sets; that is the intended behaviour for unusual wording.
- **The README and agent-instruction files are filtered.** A README or an `AGENTS.md` paragraph counts as
  AI-policy text only when it also speaks about contributing (contribution, pull request, patch, commit,
  issue, disclosure, "AI policy"), because those files mostly describe a product or give commands to a tool.
  A policy written there without those words is not flagged.

## Coverage

- **The organization `.github` repository is read only when the repository has no AI policy statement.**
  A statement is a file named like an AI policy, or one paragraph with both an AI term and a policy verb
  (see [how it works](how-it-works.md#3-organization-github-repository)). Then the organization's
  `CONTRIBUTING` and AI policy files are read even if the repository has its own `CONTRIBUTING`. When the
  repository does state a policy, the repository text wins and the organization is not read, so a weaker
  repository policy can hide a stricter organization rule. Its files show up as `org:.github/<path>` in
  `files_checked` and evidence; `--no-org` turns the lookup off.
- **The statement check is cheap and can be wrong both ways.** A README paragraph that mentions an AI
  product and a word like "requires" or "accepts" ("this LLM tool requires Python 3.10") counts as a
  statement and keeps the organization unread. A policy written without any listed verb, or with the AI
  term and the verb in different paragraphs, does not count, and the organization is read in addition.
  Only the owner's `.github` repository is consulted, not a parent organization or a policy hosted
  elsewhere.
- **Not scanned:** external policy websites, wikis, issue text, source subprojects other than the built-in
  `llvm/docs` case, and files outside the locations listed in [how it works](how-it-works.md).
- **Names matter.** A file whose name is not a candidate is not read, even when a candidate file links to
  it. `AI_POLICY.md`, `AI-USE.md` and the other names listed in [how it works](how-it-works.md) are
  candidates; a policy in a file with an unrelated name is missed.
- **Links are followed one level**, and only within the same repository.
- **Caps:** 200 files per run and 1 MB per file. Reaching either sets `complete` to `false`.
- **`complete` is about reading, not discovery.** It means every candidate file in scope was read, not that
  every place a policy could live was searched.

## Measured accuracy

Method: for every repository the tool reads the candidate files at the labelled commit SHA, classifies them
and compares the verdict with the human label (`tests/golden/`, `run_gold.py`, rubric in
`tests/golden/RUBRIC.md`). The organization `.github` repository is pinned to its SHA at the scan date.
Labels are pinned to those SHAs: policies change, so a rerun at HEAD is not comparable. The fixed split
(a seeded hash of the repository name, stratified by verdict) gave 101 `dev` repositories and 50 `holdout`
repositories. The holdout was read once, when the rules 3.0 figure below was measured, and has been seen since; from the
generalization work on, all 151 rows are one development set and the split only survives as a way to
report the 50 former holdout rows separately. Hand-written rules cannot be cross-validated (there is
nothing to fit per fold), so `python tests/golden/run_gold.py --cv --offline` prints the whole set, the
former holdout rows, per-class precision and recall, and the misses grouped by cause.

Blind results for the current rules (rules 4.0, commit de7caa1, scored on 2026-11-22; labels by a single
labeler, pinned to SHAs). These are the figures to expect on repositories the rules have not seen:

| blind set | repositories | decided-row accuracy | abstain rate | "no policy" when one exists | overall, abstention counted wrong |
|---|---|---|---|---|---|
| A (viewed before the `needs-review` change) | 60 | 74.0% (37 of 50) | 16.7% (10 of 60) | 0 of 60 | not recorded |
| B (cleaner figure; about half deliberately hard cases) | 60 | 68.9% (31 of 45) | 25.0% (15 of 60) | 1 of 60 | 51.7% (31 of 60) |

History on the 151-repository gold set (older rules, most rows read while tuning, so these numbers are optimistic; the rules 3.0 holdout row was unseen when it was read):

| | repositories | all labels | high-confidence labels | high or medium |
|---|---|---|---|---|
| Before the accuracy work (v0.1.0 rules), dev | 101 | 38.6% | 50.8% (59) | 42.7% (89) |
| Before the accuracy work, holdout | 50 | 32.0% | 40.7% (27) | 32.0% (50) |
| Rules 3.0, dev (tuned on) | 101 | 84.2% | 91.5% (59) | 89.9% (89) |
| Rules 3.0, holdout (unseen when read; older rules, historical) | 50 | 60.0% | 70.4% (27) | 60.0% (50) |
| Rules 3.0, all | 151 | 76.2% | 84.9% (86) | 79.1% (139) |
| Rules 3.0 with the org-policy transport of 7f38c67, all | 151 | 78.1% | 89.5% (86) | 82.0% (139) |
| Rules 3.0 with that transport, former holdout (seen) | 50 | 64.0% | 77.8% (27) | 64.0% (50) |
| Rules 3.1, all rows (development set, optimistic) | 151 | 88.7% | 98.8% (86) | 92.8% (139) |
| Rules 3.1, former holdout rows (seen, not blind) | 50 | 86.0% | 96.3% (27) | 86.0% (50) |
| Rules 3.1 with widened file discovery, all rows (development set, optimistic) | 151 | 90.7% | 98.8% (86) | 94.2% (139) |
| Rules 3.1 with widened file discovery, former holdout rows (seen, not blind) | 50 | 90.0% | 96.3% (27) | 90.0% (50) |

Rules 4.0 (abstention and confidence, schema version 2), development set, all 151 rows (optimistic):

| | rows | overall (needs-review counted wrong) | (a) decided-row accuracy | (b) abstain rate | (c) dangerous errors |
|---|---|---|---|---|---|
| Rules 3.1 with widened file discovery (before) | 151 | 90.7% (137) | 90.7% (137 of 151) | 0.0% | 5 |
| Rules 4.0, all rows | 151 | 87.4% (132) | 93.0% (132 of 142) | 6.0% (9) | 2 |
| Rules 4.0, 101 dev rows | 101 | 87.1% (88) | 93.6% (88 of 94) | 6.9% (7) | 2 |
| Rules 4.0, 50 former holdout rows (seen, not blind) | 50 | 88.0% (44) | 91.7% (44 of 48) | 4.0% (2) | 0 |

A "dangerous error" is a repository whose label has an AI policy that the tool reports as `no-policy-found`:
it was 5 (3 labelled `restricted`, 2 `permitted`) and is 2, both labelled `low` because the real policy
sits outside the scanned files (no AI-policy text exists for the tool to read). The three repositories the tool
used to call `no-policy-found` although their `AGENTS.md`, `CONTRIBUTING.md` or `docs/` held a policy now come
back as `needs-review`. Of the 9 abstentions on the development set, 4 are rows labelled `disclosure-required`
where a weaker rule meets a qualified ban, 2 labelled `restricted` with soft wording, 1 labelled `permitted`
(a policy file with accountability wording only), 1 labelled `restricted` (a pull request template sentence
that rejects "primarily AI" pull requests) and 1 labelled `no-policy-found` (documentation about developing
with LLMs). Decided-row accuracy went up because the rows that were wrong by guessing mostly became
abstentions, not because more rules were added; the overall figure is lower because an abstention counts as
wrong there. Per class over all 151 rows (precision / recall): `banned` 95.5 / 100, `restricted` 96.9 / 88.6,
`disclosure-required` 85.7 / 80.0, `permitted` 83.3 / 62.5, `no-policy-found` 90.9 / 90.9 (recall counts an
abstained row as missed; precision is over the verdicts the tool gave). `python tests/golden/run_gold.py --split all --offline --summary` prints
all of it. The `--offline` reproduction needs a filled local cache (`tests/golden/_cache`, not part of the repository): the
first run, without `--offline`, fetches the responses from GitHub (set `GITHUB_TOKEN` to avoid the anonymous rate limit).

Only the blind sets above and the rules 3.0 holdout row are unseen-data figures. The other rows were produced by rules edited with every row
in view, so they say how well the rules fit this set, not how well they predict new repositories. The
rules 3.1 work made the rules fewer and broader (18 rules became 12): a ban now needs a scope-wide
prohibition, conditional closures, voice guidance and autocompletion-only carve-outs read as
`restricted`, and the disclosure/restricted boundary follows the rubric.

Per class with rules 3.1 and the widened file discovery, development set (precision / recall): `banned`
95.5 / 100 (21 labelled), `restricted` 96.9 / 88.6 (70), `disclosure-required` 84.8 / 93.3 (30), `permitted`
83.3 / 62.5 (8), `no-policy-found` 80.8 / 95.5 (22). Former holdout rows: `banned` 87.5 / 100 (7),
`restricted` 95.2 / 87.0 (23), `disclosure-required` 90.0 / 90.0 (10), `permitted` 66.7 / 66.7 (3),
`no-policy-found` 87.5 / 100 (7). The 134 to 137 step comes from five policy files the tool did not fetch
before (`AI.md` reached through a link, `CONTRIBUTING.ai.md`, `policies/ai-policy.md`,
`docs/contributing/ai-coding.md`, an `.rst` policy under `doc/src/contributing/`); two now match their
label and one more row (a repository whose own `docs/` AI page now stops the organization lookup) changed
for a reason outside the five.
The same table for rules 3.0 on the blind holdout was `banned` 42.9 / 42.9, `restricted` 72.2 / 56.5,
`disclosure-required` 58.3 / 70.0, `permitted` 0 / 0, `no-policy-found` 58.3 / 100.

Rules 4.0 on the 151 development rows leave 19 rows without the labelled verdict: 9 abstentions (`needs-review`)
and 10 wrong verdicts on decided rows (by cause; the attribution is a reading of the evidence, listed in
`MISS_CAUSES` in `run_gold.py`; `python tests/golden/run_gold.py --cv --offline` prints it). Two of the 10 are rule
disagreements at the `restricted` boundary (a `restricted` label read as `disclosure-required`, and one read as
`banned`). Eight rest on labels the rubric does not settle: a policy page outside the folders the rubric
scans, an `AGENTS.md` trailer rule beside a `disclosure-required` label, an agent-disclosure rule the label
counts as "agent instructions only", a credit-trailer checkbox the label calls `permitted`, a maintainers-only
pull request section that is not AI-specific, and three rows whose label confidence is `low`. None is a policy file
the tool failed to fetch. The tool still errs towards finding a policy class: of 22 predicted `no-policy-found`,
20 are right, and the 2 wrong ones are 1 missed `restricted` and 1 missed `permitted` (both labelled `low`
because the real policy sits outside the scanned files).

What the numbers do not say: the set favours repositories that have a written policy (`restricted` is 70
of 151), the labels are one reviewer's reading of a rubric, and the boundary between `restricted` and
`disclosure-required` is a judgement call (several of the remaining misses sit on it). Medium and low
confidence labels are included in "all labels". A rule set tuned until it scores about 93% on decided rows (87.4% overall) on its own
repositories has not shown that it holds that figure elsewhere; the blind figures above (68.9% and 74.0% on decided rows) are the ones to expect.

## Transport

- No `gh` CLI is needed. The tool uses the anonymous GitHub REST API and `raw.githubusercontent.com`.
  `GITHUB_TOKEN` or `GH_TOKEN` is optional and raises the rate limit; `gh` is used only as a fallback when
  the REST API is rate limited or unreachable.
- `--ref` accepts any commit SHA that GitHub resolves for the repository, and GitHub resolves commits across a
  repository's whole fork network. A SHA that exists only in a fork is therefore read, and reported, under the
  parent's name. Check the commit link in the report when the reference did not come from the project itself.
- API rate limits apply (anonymous: 60 API requests per hour). A hit limit shows up as a read error and
  `complete: false`, not as a verdict. File contents come from `raw.githubusercontent.com`, which does not
  count against the API limit.
- Fetched files and trees are cached on disk by repository and commit SHA, in
  `%LOCALAPPDATA%\oss-ai-policy-check\cache` on Windows and `$XDG_CACHE_HOME/oss-ai-policy-check` or
  `~/.cache/oss-ai-policy-check` elsewhere. A SHA never changes, so entries do not expire; `--no-cache`
  turns the cache off.
- A repository whose recursive tree is truncated by GitHub or larger than the 16 MB read limit (such as
  `llvm/llvm-project`, `rust-lang/rust`, `mozilla-firefox/firefox`, `NixOS/nixpkgs`) is read through the
  root tree and the `.github` and docs trees instead (3 to 5 API requests rather than 1). The 16 MB reply is
  still downloaded once before it is discarded. Policy files outside the root, `.github`, docs and
  `llvm/docs` trees of such a repository are not found, and no warning is added for that, because that is
  the stated scope.
- A local `--path` run reads the files as they are on disk, including uncommitted edits. The SHA shown is
  the one in `.git/HEAD`.

## Reporting a wrong classification

Open an issue with the repository (`owner/name`), the commit SHA you checked, the verdict the tool gave,
and the verdict you expected, with the line that supports it. The steps are in
[CONTRIBUTING.md](../CONTRIBUTING.md#reporting-a-wrong-classification). Reports of this kind become
cases for the golden set.
