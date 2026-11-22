# How this repository is produced

The code, the classification rules, the tests and the documentation in this repository are written by AI agents
(Claude Code). The maintainer is a human who directs the work, writes the acceptance criteria and reviews the
results; agents work inside those criteria and do not decide what ships.

Every change has to pass the offline test suite before it is accepted. Accuracy is measured on blind sets of
repositories the rules have not seen; the figures are in the [README](README.md#how-accurate-is-it). The
golden-set measurement needs a local response cache that is not part of the repository, so it is run by the
maintainer and is not a CI check.

The classification rules are hand-written regular expressions in `src/oss_ai_policy_check/rules/patterns.json`
(written by the agents, directed and reviewed by the maintainer). They are not learned from data: no model is
trained or called when the tool runs. The AI policy for contributors to this repository is in
[CONTRIBUTING.md](CONTRIBUTING.md).
