# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub private vulnerability reporting: open the
Security tab of this repository and choose "Report a vulnerability". Do not open a public issue for a
security problem.

Include what you did, what you expected, what happened, and the version (`oss-ai-policy-check --version`).

This is a small project maintained by one person. Reports are read as soon as the maintainer can, with no
guaranteed response time.

## Scope

In scope: the Python package in this repository and its documented behavior, for example reading
files outside the intended scope, running code from a scanned repository or local clone, leaking a
credential or a local path into output, or writing anything to GitHub.

Not in scope: a wrong policy classification. That is a correctness issue, and it goes to the public issue
tracker as described in [CONTRIBUTING.md](CONTRIBUTING.md).

## Design notes relevant to security

- The tool is read-only: GET requests only, nothing is written to GitHub.
- It never runs `git` or any code from a scanned repository or local clone.
- Output never contains local paths or raw error text from GitHub, and quotes are cut at 15 words and 300 characters. Text printed by the GitHub Action is wrapped so
  that repository text cannot run a workflow command, and the action runs without importing anything from the
  workspace.

## Supported versions

Only the latest release receives fixes.
