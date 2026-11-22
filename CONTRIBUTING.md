# Contributing

Thanks for helping. This file covers how to report a wrong result, how to change the rules, and this
project's own rule for AI-assisted contributions.

## AI-assisted contributions

AI-assisted contributions are welcome here. This repository is itself produced with AI agents; see
[AI-USE.md](AI-USE.md).

- AI use must be disclosed in the pull request description. Name the tool and say what it did.
- A human in the loop is required. The contributor must have reviewed every changed line and must be able
  to explain it in review.
- The contributor is responsible for the change, for its tests, and for its license status.

## Reporting a wrong classification

The most useful contribution is a repository where the tool gets the verdict wrong. Open an issue with:

1. the repository (`owner/name`);
2. the commit SHA you checked (the report prints it, and `--format json` includes `commit_sha`);
3. the verdict the tool printed;
4. the verdict you expected, and the file and line that support it.

Do not paste whole policy texts. A permalink and a quote of at most 15 words is enough. If the text is
ambiguous, say so; ambiguous cases are useful too.

## Setting up

Python 3.10 or newer, standard library only. From the repository root, no install needed:

```
python -X utf8 -B -m unittest discover -s tests
```

Tests are offline. A test that needs the network is a bug. File ownership and module rules are in
[CONTRIBUTING-DEV.md](CONTRIBUTING-DEV.md).

## Adding or changing a rule

Rules live in `src/oss_ai_policy_check/rules/patterns.json`: an `id`, a verdict `class`, a `regex`, and
optional `exclude` expressions tested against the sentence of the match.

1. Start from a real or synthetic sentence that is classified wrongly. Add it to
   `tests/test_classify.py` first and watch it fail. Prefer your own synthetic sentences over copying
   policy text.
2. Change the rule as narrowly as you can. Check that the existing tests still pass; a rule that fixes one
   case and breaks another is not done.
3. If the case came from a real repository, add it to the golden set as `owner/name@sha`, file, line,
   expected verdict and a quote of at most 15 words. Do not store full policy texts.
4. Describe the change under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).

Report fields, verdict names and exit codes are a contract ([docs/schema.md](docs/schema.md)). Changes to
them need a discussion in an issue first.

## Pull requests

Keep a pull request to one change. Say what you ran. Be kind in review; this project follows the usual
norms of respectful collaboration.
