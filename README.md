# oss-ai-policy-check

Finds a repository's AI contribution policy and links every line it cites at a fixed commit, so you can check
the text yourself. When it cannot decide what the policy says, it answers `needs-review` instead of guessing.

The strength of the tool is locating the policy text and pointing at the exact lines. The class it puts on top
(`banned`, `restricted`, `disclosure-required`, ...) is a hint with a confidence level, not a ruling: read the
cited lines. Contributors and coding agents get the rules before they open a pull request; maintainers can test
that a machine finds the policy they wrote.

## Quick start

Python 3.10 or newer, no dependencies, no login.

```
uvx oss-ai-policy-check owner/repo
pip install oss-ai-policy-check && oss-ai-policy-check owner/repo
```

`pipx run oss-ai-policy-check owner/repo` works too. As a GitHub Action (pinned to a release tag; for the
strictest pinning use the full commit SHA of the tag, see [docs/action.md](docs/action.md)):

```yaml
permissions:
  contents: read

steps:
  - id: policy
    uses: KemalG-u/oss-ai-policy-check@v0.2.0
    with:
      repo: owner/repo        # default: the repository running the workflow
      fail-on: banned         # empty means report only
```

From a clone of this repository:

```
python -m venv .venv
.venv/bin/python -m pip install -e .        # Windows: .venv/Scripts/python
.venv/bin/oss-ai-policy-check owner/repo
```

Options: `--ref REF` (branch, tag or SHA), `--path DIR` (read a local clone of `owner/repo` instead of GitHub),
`--format text|json|markdown|sarif`, `--fail-on banned|restricted|disclosure-required|needs-review`,
`--no-cache` (skip the disk cache), `--no-org` (do not read the owner's `.github` repository), `--version`.
Fetched files are cached on disk by repository and commit SHA (`%LOCALAPPDATA%\oss-ai-policy-check\cache` on
Windows, `$XDG_CACHE_HOME` or `~/.cache` elsewhere).

## Example output

Abridged output of a real run on 2026-11-22. Repositories change their policies; the SHA shows what was read.

```
VERDICT restricted - ghostty-org/ghostty @ 6d244209978e599277904ff3ced7b8a2ebcc528d (6d244209978e)
confidence: high
read status: complete within scope
evidence (12):
  - restricted (high): AI_POLICY.md:21 - - **No AI-generated media is allowed (art, images, videos, audio, etc.).**
    https://github.com/ghostty-org/ghostty/blob/6d244209978e599277904ff3ced7b8a2ebcc528d/AI_POLICY.md#L21
  - disclosure-required (high): AI_POLICY.md:5 - - **All AI usage in any form must be disclosed.** You must state
    https://github.com/ghostty-org/ghostty/blob/6d244209978e599277904ff3ced7b8a2ebcc528d/AI_POLICY.md#L5
  ... (10 more)
AI mentions without a classified rule (10):
  - ... (listed in full, not hidden)
```

When the files hold AI-policy text the tool cannot decide on, it says so instead of guessing (abridged):

```
VERDICT needs-review - mozilla-firefox/firefox @ a3178f3cbfac6e25d5bc7b6365c4ff520a42205e (a3178f3cbfac)
confidence: low
NEEDS HUMAN REVIEW (no-rule-decision): These paragraphs name AI and state a rule, but no rule of the tool decided on them.
evidence (4):
  - needs-review (low): docs/contributing/ai-coding.md:13 - AI-assisted contributions must meet the same standards of correctness, security, and maintainability as any other
    https://github.com/mozilla-firefox/firefox/blob/a3178f3cbfac6e25d5bc7b6365c4ff520a42205e/docs/contributing/ai-coding.md#L13
  ... (3 more)
```

## Verdicts

One verdict per report, strongest signal first. Treat it as a hint: `confidence` (`high` or `low`) says how
sure the tool is about what it read, and `complete` is a separate flag.

| verdict | meaning |
|---|---|
| `banned` | a quoted line says AI-assisted work is refused |
| `restricted` | AI use is conditional: a human in the loop, own-voice descriptions, limits on scope |
| `disclosure-required` | AI use has to be disclosed or labelled |
| `needs-review` | the tool abstains: the files hold AI-policy text it cannot decide on credibly (no rule matched it, or the signals conflict, for example a welcome next to a ban). A person must read the cited paragraphs; this is not permission |
| `permitted` | explicit, positive evidence only, never inferred from silence (emitted for an explicit welcome statement such as "AI-assisted contributions are welcome") |
| `no-policy-found` | no AI-policy text in the scanned scope; this is not permission |

The tool abstains instead of guessing: `no-policy-found` is returned only when no paragraph pairs an AI term
with a policy verb. A ban sentence that carries a narrowing qualifier ("unreviewed", "fully", "unless") is
reported as `restricted`, not `banned`, and marked `low` confidence; so is a verdict that rests on one
sentence or on a loose heuristic rule. The order of precedence is in [docs/schema.md](docs/schema.md#decision-order).

`complete` is `false` when a candidate file could not be read, a tree was truncated, the file limit was
hit, or repository discovery failed.

**"No policy found" and "could not read" are different answers.** A file that does not exist is recorded
as `missing`: nothing is there. A file that exists but failed to load is recorded as `error`: unknown.
When `complete` is `false` and nothing restrictive was found, the tool exits with code 3 and says no
decision is possible, because a restriction may sit in the file it could not read. Printing "no policy"
in that case would tell a contributor that AI use is fine when the tool simply did not see the text.

## Exit codes

| code | meaning |
|---|---|
| 0 | `permitted`, `no-policy-found`, `disclosure-required` or `needs-review` |
| 10 | `restricted` |
| 20 | `banned` |
| 11 | `disclosure-required`, only with `--fail-on disclosure-required` or `needs-review` |
| 12 | `needs-review`, only with `--fail-on needs-review` |
| 3 | incomplete and undecided: `complete` is `false` and nothing restrictive was found |
| 2 | usage error |

`--fail-on V` exits non-zero only when the verdict is at least as strict as `V`. A `banned` or `restricted`
verdict stands even when `complete` is `false`. The JSON report is `schema_version` "2" (it adds the
`needs-review` verdict, `confidence` and `review_reason`; migration notes are in [docs/schema.md](docs/schema.md#migration-from-schema_version-1)).

## GitHub Action

The action reads a repository's policy, writes the report to the job summary and returns the verdict as an
output (example in the quick start above). Inputs, outputs, permissions and more examples are in
[docs/action.md](docs/action.md).

## How accurate is it?

Measured on repositories the rules had not seen. Labels are one person's reading of a written rubric, pinned
to commit SHAs; the tool was run at commit de7caa1 on 2026-11-22.

| blind set | repos | accuracy on decided rows | abstained (`needs-review`) | says "no policy" when one exists |
|---|---|---|---|---|
| A (60 unseen repositories) | 60 | 74.0% (37 of 50) | 16.7% (10 of 60) | 0 of 60 |
| B (60 unseen repositories, about half deliberately hard) | 60 | 68.9% (31 of 45) | 25.0% (15 of 60) | 1 of 60 |

- Set B is the cleaner figure: set A was viewed before the `needs-review` change was made. Counting an
  abstention as wrong, set B's overall exact-verdict figure is 51.7% (31 of 60).
- "Decided rows" are the rows where the tool gave a class instead of abstaining. On these sets about seven
  decided verdicts in ten matched the label, and between one repository in six and one in four came back as
  `needs-review`.
- The blind-set labels are kept out of this repository on purpose, so that future measurements stay blind.
- The 151-repository development set scores higher (93.0% on decided rows) but every row was read while
  tuning the rules, so that number is optimistic and says little about new repositories.

What this means in practice: use the evidence lines (they are exact and linked) and the `needs-review` answer
as the product, and the class as a first guess. Method, per-class tables and known misses are in
[docs/limitations.md](docs/limitations.md#measured-accuracy).

## How it works and its limits

- [How it works](docs/how-it-works.md): which files are read and how the verdict is chosen.
- [Limitations](docs/limitations.md): known gaps and how to report a wrong classification.

The classifier is regex based and can be wrong in both directions. A verdict is not legal advice and not
permission to contribute with AI. The text of the policy itself is what counts: follow the linked lines, not
the label.

## Privacy

No token is needed. The tool makes read-only GET requests for public files to GitHub's REST API
(`api.github.com`) and to `raw.githubusercontent.com`, and sends nothing else anywhere: the repository name,
branch and file paths are the only data in those requests. It does not clone the repository, run any of its code,
or write to GitHub. An optional `GITHUB_TOKEN` or `GH_TOKEN` only raises the API rate limit and goes to those two
GitHub hosts only; the `gh` CLI is used only as a fallback when the REST API is rate limited or unreachable. No
analytics, no telemetry, and the report never includes local paths. The only thing written locally is the
commit-keyed file cache (`--no-cache` turns it off). Quotes are cut at 15 words and 300 characters.

## Related projects

- [beduldul/ai-policy](https://github.com/beduldul/ai-policy) (Go, MIT): a checker for the same question.
  The discovery and line-evidence design here is adapted from it; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
- [melissawm/open-source-ai-contribution-policies](https://github.com/melissawm/open-source-ai-contribution-policies):
  a list of policies that projects have written. A list, not a checker.
- [ai-policy.dev](https://ai-policy.dev): ready-made policy texts that maintainers can copy. Useful if you
  need to write a policy; this tool can then check that it is found.

## Project

[AI-USE.md](AI-USE.md) says how this repository is produced. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports: [SECURITY.md](SECURITY.md). Changes: [CHANGELOG.md](CHANGELOG.md). License: MIT, see [LICENSE](LICENSE).
