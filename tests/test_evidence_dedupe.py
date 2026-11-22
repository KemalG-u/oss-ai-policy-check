"""Evidence is unique per (file, line, signal) in every format; shape of django's pull_request_template.md."""
import json
import re
import unittest

import _support  # noqa: F401  (puts src/ on sys.path)
from oss_ai_policy_check.classify import classify_documents
from oss_ai_policy_check.contract import Snapshot, build_report, dedupe_evidence
from oss_ai_policy_check.report import render_json, render_markdown, render_sarif, render_text

PATH = ".github/pull_request_template.md"
SHA = "c" * 40
DJANGO_TEMPLATE = """#### Trac ticket number
<!-- Replace XXXXX with the corresponding Trac ticket number. -->
<!-- Or delete the line and write "N/A - typo" for typo fixes. -->

ticket-XXXXX

#### Branch description
<!-- Provide a concise overview of the issue or rationale behind the proposed changes. Minimum five words. -->

#### AI Assistance Disclosure (REQUIRED)
<!-- Select exactly ONE of the following: -->
- [ ] **No AI tools were used** in preparing this PR.
- [ ] **If AI tools were used**, I have disclosed which ones, and fully reviewed and verified their output.
<!-- If AI tools were used, provide which tools were used here. -->

#### Checklist
- [ ] This PR follows the [contribution guidelines](https://docs.djangoproject.com/en/stable/internals/contributing/writing-code/submitting-patches/).
- [ ] This PR **does not** disclose a security vulnerability (see [vulnerability reporting](https://docs.djangoproject.com/en/stable/internals/security/)).
- [ ] This PR targets the `main` branch. <!-- Backports will be evaluated and done by mergers, when necessary. -->
- [ ] The commit message is written in past tense, mentions the ticket number (if applicable), and ends with a period (see [guidelines](https://docs.djangoproject.com/en/dev/internals/contributing/committing-code/#committing-guidelines)).
- [ ] I have not requested, and will not request, an automated AI review for this PR. <!-- You are welcome to do so in your own fork. -->

<!-- Leave the following items unchecked if not applicable. -->
- [ ] I have checked the "Has patch" ticket flag in the Trac system.
- [ ] I have added or updated relevant tests.
- [ ] I have added or updated relevant docs, including release notes if applicable.
- [ ] I have attached screenshots in both light and dark modes for any UI changes.
"""


def django_report():
    docs = {PATH: DJANGO_TEMPLATE}
    snap = Snapshot(repo="django/django", ref="main", commit_sha=SHA, documents=docs,
                    files_checked=[{"path": PATH, "status": "read"}])
    return build_report(snap, classify_documents(docs))


class DjangoTemplateTests(unittest.TestCase):
    def test_classifier_cites_each_line_and_signal_once(self):
        c = classify_documents({PATH: DJANGO_TEMPLATE})
        keys = [(e["file"], e["line"], e["signal"]) for e in c["evidence"]]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertEqual(c["verdict"], "disclosure-required")
        line10 = [e for e in c["evidence"] if e["line"] == 10]
        self.assertEqual([e["confidence"] for e in line10], ["high"])  # the high one stays, not the weak twin

    def test_both_rules_fire_without_dedupe(self):
        # the cause: the strong `disclosure` rule and the weak `template-ai-prompt` rule cite the same line
        docs = {PATH: "#### AI Assistance Disclosure (REQUIRED)\n- [ ] **If AI tools were used**, I have disclosed which ones.\n"}
        from oss_ai_policy_check import classify as cl
        data = cl.load_patterns()
        self.assertEqual({r["id"] for r in data["rules"]} >= {"disclosure", "template-ai-prompt"}, True)
        self.assertEqual(len(classify_documents(docs)["evidence"]), 2)  # lines 1 and 2, one item each

    def test_review_request_statement_is_not_disclosure_evidence(self):
        c = classify_documents({PATH: DJANGO_TEMPLATE})
        self.assertNotIn(21, [e["line"] for e in c["evidence"]])
        only = classify_documents({PATH: "- [ ] I have not requested, and will not request, an automated AI review for this PR.\n"})
        self.assertEqual(only["evidence"], [])
        self.assertNotEqual(only["verdict"], "disclosure-required")

    def test_report_count_and_formats_agree(self):
        report = django_report()
        keys = [(e["file"], e["line"], e["signal"]) for e in report["evidence"]]
        self.assertEqual(len(keys), len(set(keys)))
        n = len(report["evidence"])
        self.assertEqual(n, 4)  # lines 10, 12, 13, 14; was 7 with duplicates and line 21
        self.assertIn(f"evidence ({n}):", render_text(report))
        self.assertIn(f"### Evidence ({n})", render_markdown(report))
        self.assertEqual(len(json.loads(render_json(report))["evidence"]), n)
        self.assertEqual(len(json.loads(render_sarif(report))["runs"][0]["results"]), n)
        text = render_text(report)
        self.assertEqual(len(re.findall(r"pull_request_template\.md:10 ", text)), 1)


class DedupeTests(unittest.TestCase):
    def item(self, line=10, signal="disclosure-required", confidence="high", quote="q", file="a.md"):
        return {"file": file, "line": line, "signal": signal, "confidence": confidence, "quote": quote}

    def test_keeps_highest_confidence_wherever_it_appears(self):
        low, high = self.item(confidence="low"), self.item(confidence="high")
        for order in ([low, high], [high, low]):
            out = dedupe_evidence(order)
            self.assertEqual([e["confidence"] for e in out], ["high"])

    def test_different_signal_or_line_or_file_stays(self):
        items = [self.item(), self.item(signal="restricted"), self.item(line=11), self.item(file="b.md")]
        self.assertEqual(len(dedupe_evidence(items)), 4)

    def test_build_report_dedupes_any_classifier_output(self):
        snap = Snapshot(repo="o/r", ref="main", commit_sha=SHA, files_checked=[])
        cls = {"verdict": "disclosure-required", "unclassified_ai_lines": [],
               "evidence": [self.item(confidence="low"), self.item(confidence="high"), self.item(line=13)]}
        report = build_report(snap, cls)
        self.assertEqual([(e["line"], e["confidence"]) for e in report["evidence"]], [(10, "high"), (13, "high")])


if __name__ == "__main__":
    unittest.main()
