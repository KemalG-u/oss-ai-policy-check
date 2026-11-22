# GitHub Action

The action reads a repository's AI contribution policy with `oss-ai-policy-check`, writes the report to the job
summary and returns the verdict as an output. It is read-only: it fetches policy files and never runs repository code.

The examples use the release tag `KemalG-u/oss-ai-policy-check@v0.2.0`. A tag can be moved; for the strictest
pinning replace it with the full commit SHA of that tag (see [Security notes](#security-notes)).

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `repo` | `github.repository` | `OWNER/REPO` to read |
| `ref` | empty | branch, tag or commit SHA; empty is the default branch. Cannot be combined with `path` |
| `path` | empty | local checkout of `repo`, read instead of GitHub. Cannot be combined with `ref` |
| `format` | `markdown` | format of the report file: `text`, `json`, `markdown` or `sarif` |
| `fail-on` | empty | `banned`, `restricted`, `disclosure-required` or `needs-review`. Empty means report only |
| `upload-sarif` | `false` | with `format: sarif`, upload the result to code scanning |
| `github-token` | `github.token` | token used to read the repository |

## Outputs

| Output | Meaning |
|---|---|
| `verdict` | `banned`, `restricted`, `disclosure-required`, `needs-review`, `permitted` or `no-policy-found` |
| `complete` | `true` or `false`. When `false`, `no-policy-found` and `permitted` are not a decision |
| `json-file` | path of the JSON report (`schema_version` 2, see `docs/schema.md`), always written |
| `report-file` | path of the report in the requested `format` |
| `sarif-file` | path of the SARIF report; empty unless `format: sarif` |
| `exit-code` | the CLI exit code from the table in `docs/schema.md` (`2` on a usage or read error) |

The markdown report is always appended to the job summary, whatever `format` is.

## When the step fails

- `fail-on` empty: the step reports and passes for every decided verdict, `banned` included. It fails only when
  nothing could be decided: bad input or a read error (exit code `2`), or an incomplete read that found nothing
  restrictive (exit code `3`, for example GitHub unreachable or rate limited, so `no-policy-found` and `permitted`
  would not be a decision). A green check never stands for a policy that was not read.
- `fail-on` set: a verdict at least as strict as the value fails the step (`banned` 20, `restricted` 10,
  `disclosure-required` 11, `needs-review` 12). `needs-review` means the files hold AI-policy text the tool could
  not decide on, so a person has to read the cited paragraphs; `fail-on: needs-review` fails on it and on every
  stricter verdict. An incomplete read that found nothing restrictive fails with `3` whatever `fail-on` is,
  because nothing can be decided. Anything milder passes.

The failure comes after the SARIF upload and after the outputs are set, so later steps with `if: always()` can
still read `verdict` and `json-file`.

## Recommended permissions

```yaml
permissions:
  contents: read
```

This is enough to read public repositories and a local checkout. Add `security-events: write` only to a job that
uploads SARIF. The action needs no `gh` CLI: it reads GitHub through the REST API and raw file
downloads, and the token only raises the rate limit.

## Example 1: a contributor checks the upstream policy from a fork

Put this in your fork. It reads the upstream project, not the fork, and stops the run if AI contributions are banned.

```yaml
name: upstream-ai-policy
on:
  workflow_dispatch:
    inputs:
      upstream:
        description: "OWNER/REPO of the project you want to contribute to"
        required: true

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - id: policy
        uses: KemalG-u/oss-ai-policy-check@v0.2.0
        with:
          repo: ${{ inputs.upstream }}
          fail-on: banned
      - name: Show the result
        env:
          VERDICT: ${{ steps.policy.outputs.verdict }}
          COMPLETE: ${{ steps.policy.outputs.complete }}
        run: echo "verdict=${VERDICT} complete=${COMPLETE}"
```

The `upstream` value reaches the action as an input and is passed to the CLI through an environment variable. It is
never placed in a shell script.

## Example 2: a maintainer tests that their own policy is readable

Run the tool on your own checkout when policy files change. `no-policy-found` here means the wording of your
policy is not recognised, or that no policy file exists; both are worth fixing. The step prints the lines it
cited, so the maintainer can see how the policy is read.

```yaml
name: ai-policy-readable
on:
  pull_request:
    paths: [CONTRIBUTING.md, AI-USE.md, docs/**]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<full-commit-sha> # v7
        with:
          persist-credentials: false
      - id: policy
        uses: KemalG-u/oss-ai-policy-check@v0.2.0
        with:
          path: .
          format: markdown
      - name: Require a recognised policy
        env:
          VERDICT: ${{ steps.policy.outputs.verdict }}
          COMPLETE: ${{ steps.policy.outputs.complete }}
        run: |
          if [ "${COMPLETE}" != "true" ] || [ "${VERDICT}" = "no-policy-found" ]; then
            echo "::error::the AI policy is missing, not recognised, or could not be read completely"
            exit 1
          fi
```

With `path`, `repo` defaults to the current repository and the commit SHA is read from `.git/HEAD`, so the
permalinks in the report point at the checked-out commit.

## Example 3: a callable workflow (`workflow_call`)

A reusable workflow lets other repositories of your organisation run the check with one `uses:` line.

`.github/workflows/ai-policy.yml` in the repository that owns the policy check:

```yaml
name: ai-policy
on:
  workflow_call:
    inputs:
      repo:
        description: "OWNER/REPO to read; defaults to the calling repository"
        type: string
        default: ""
      fail-on:
        description: "banned, restricted, disclosure-required or needs-review; empty is report only"
        type: string
        default: ""
    outputs:
      verdict:
        description: "The verdict of the policy"
        value: ${{ jobs.check.outputs.verdict }}

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    outputs:
      verdict: ${{ steps.policy.outputs.verdict }}
    steps:
      - id: policy
        uses: KemalG-u/oss-ai-policy-check@v0.2.0
        with:
          repo: ${{ inputs.repo || github.repository }}
          fail-on: ${{ inputs.fail-on }}
```

A caller:

```yaml
jobs:
  policy:
    uses: OWNER/policy-workflows/.github/workflows/ai-policy.yml@<full-commit-sha>
    with:
      fail-on: banned
    permissions:
      contents: read
```

## Optional: SARIF and code scanning

`format: sarif` writes a SARIF report to `sarif-file`. Uploading it is optional. Either let the action do it:

```yaml
permissions:
  contents: read
  security-events: write

steps:
  - uses: KemalG-u/oss-ai-policy-check@v0.2.0
    with:
      format: sarif
      upload-sarif: "true"
```

or upload it yourself, for example with a different category:

```yaml
  - id: policy
    uses: KemalG-u/oss-ai-policy-check@v0.2.0
    with:
      format: sarif
  - uses: github/codeql-action/upload-sarif@<full-commit-sha> # v4
    with:
      sarif_file: ${{ steps.policy.outputs.sarif-file }}
      category: ai-policy
```

Code scanning is not available for private repositories without GitHub Code Security; the SARIF file is still
written.

## Security notes

- Pin every action to a full commit SHA. A tag can be moved; a SHA cannot. This action's own tag `v0.2.0` is the
  convenient form; for a stricter pin, resolve it once (`git ls-remote https://github.com/KemalG-u/oss-ai-policy-check
  refs/tags/v0.2.0`) and write the SHA with the tag as a comment. Let Dependabot propose updates
  (`.github/dependabot.yml` does this for this repository).
- The action never interpolates an input into a shell script. `repo`, `ref`, `path`, `format` and `fail-on` reach
  the CLI as environment variables and are validated before use. Keep the same rule in your own workflows: pass
  `${{ inputs.* }}` and `${{ github.event.* }}` through `env:` and read them as `"${NAME}"`.
- The token is only set in the environment of the step that reads the repository. Nothing is installed or
  downloaded besides Python itself. The default `github.token` with `contents: read` is enough; do not pass a personal access token
  unless you must read a private repository.
- Do not run this on `pull_request_target` with a checkout of the pull request head and a write token. Use
  `pull_request` for the maintainer example above.
- The report quotes at most 15 words and at most 300 characters per line and links to the commit-pinned source.
  It prints no local paths.
- The action does not install the package. The tool is standard library only and runs straight from the action's
  own checkout (`github.action_path`) with `python -P` and `PYTHONPATH` pointing at it, so the tag you pin is the
  code that runs, and nothing from the workspace (which a pull request controls) can be imported by the step
  that holds the token. Without `-P` Python would put the current directory first on its import path.
- The text report is printed between `::stop-commands::<random token>` and `::<token>::`, and no line of it
  starts with repository text, so a file or quote named like a workflow command (`::error ...`) cannot run one.
- The SARIF locations of evidence from the owner's `.github` repository (paths shown as `org:.github/...`) do not
  resolve in the scanned repository's code view: that file lives in another repository. The `permalink`
  property of the result links to it at its pinned commit.
- The action installs Python 3.13 through `actions/setup-python`; this changes the Python on `PATH` for the rest of
  the job. The `-P` isolation needs Python 3.11 or newer.
- The output files live in `RUNNER_TEMP/oss-ai-policy-check`. Running the action twice in one job overwrites them;
  copy the files you need between runs.
