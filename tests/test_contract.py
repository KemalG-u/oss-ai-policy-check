import unittest

import _support
from oss_ai_policy_check import contract as c
from oss_ai_policy_check.classify import classify_documents


class ExitCodeTests(unittest.TestCase):
    def test_error_exit_not_success(self):
        self.assertEqual(c.exit_code("no-policy-found", complete=False), 3)
        self.assertEqual(c.exit_code("banned", complete=False), 20)

    def test_table(self):
        table = {("permitted", True): 0, ("no-policy-found", True): 0, ("disclosure-required", True): 0,
                 ("restricted", True): 10, ("banned", True): 20, ("needs-review", True): 0,
                 ("permitted", False): 3, ("restricted", False): 10, ("disclosure-required", False): 0,
                 ("needs-review", False): 0}
        for (verdict, complete), expected in table.items():
            self.assertEqual(c.exit_code(verdict, complete), expected, (verdict, complete))

    def test_fail_on_threshold(self):
        self.assertEqual(c.exit_code("restricted", True, "banned"), 0)
        self.assertEqual(c.exit_code("banned", True, "banned"), 20)
        self.assertEqual(c.exit_code("banned", True, "restricted"), 20)
        self.assertEqual(c.exit_code("restricted", True, "restricted"), 10)
        self.assertEqual(c.exit_code("disclosure-required", True, "restricted"), 0)
        self.assertEqual(c.exit_code("disclosure-required", True, "disclosure-required"), 11)
        self.assertEqual(c.exit_code("permitted", True, "disclosure-required"), 0)
        self.assertEqual(c.exit_code("no-policy-found", False, "banned"), 3)

    def test_needs_review_exit_code_12_only_when_gated(self):
        self.assertEqual(c.EXIT_NEEDS_REVIEW, 12)
        self.assertEqual(c.exit_code("needs-review", True, "needs-review"), 12)
        self.assertEqual(c.exit_code("needs-review", False, "needs-review"), 12)  # an abstention is not "incomplete"
        for stricter in ("banned", "restricted", "disclosure-required"):
            self.assertEqual(c.exit_code("needs-review", True, stricter), 0, stricter)
        # --fail-on needs-review still fails on every stricter verdict, with the verdict's own code
        self.assertEqual(c.exit_code("banned", True, "needs-review"), 20)
        self.assertEqual(c.exit_code("restricted", True, "needs-review"), 10)
        self.assertEqual(c.exit_code("disclosure-required", True, "needs-review"), 11)
        self.assertEqual(c.exit_code("permitted", True, "needs-review"), 0)
        self.assertEqual(c.exit_code("no-policy-found", True, "needs-review"), 0)


class VerdictTests(unittest.TestCase):
    def test_priority_order(self):
        self.assertEqual(c.VERDICTS, ("banned", "restricted", "disclosure-required", "needs-review", "permitted",
                                      "no-policy-found"))
        # the abstention never outranks a quoted restriction and never reads as permission
        self.assertGreater(c.SEVERITY["needs-review"], c.SEVERITY["permitted"])
        self.assertLess(c.SEVERITY["needs-review"], c.SEVERITY["disclosure-required"])
        self.assertEqual(c.verdict_from_signals(["needs-review"]), "needs-review")
        self.assertEqual(c.SCHEMA_VERSION, "2")
        self.assertEqual(c.verdict_from_signals(["permitted", "disclosure-required"]), "disclosure-required")
        self.assertEqual(c.verdict_from_signals(["permitted", "banned", "restricted"]), "banned")
        self.assertEqual(c.verdict_from_signals([]), "no-policy-found")

    def test_blob_url_pinned_and_quoted(self):
        sha = "a" * 40
        self.assertEqual(c.blob_url("o/r", sha, "docs/My File.md", 7),
                         f"https://github.com/o/r/blob/{sha}/docs/My%20File.md#L7")
        self.assertIsNone(c.blob_url("o/r", None, "x.md", 1))

    def test_org_blob_url_uses_org_repo_and_org_sha(self):
        org_sha = "b" * 40
        self.assertEqual(c.blob_url("o/r", "a" * 40, "org:.github/.github/PULL_REQUEST_TEMPLATE.md", 3, org_sha),
                         f"https://github.com/o/.github/blob/{org_sha}/.github/PULL_REQUEST_TEMPLATE.md#L3")
        self.assertEqual(c.blob_url("o/r", "a" * 40, "org:.github/AI POLICY.md", 1, org_sha),
                         f"https://github.com/o/.github/blob/{org_sha}/AI%20POLICY.md#L1")
        # Never the main repository SHA, and no link at all when the org SHA is unknown.
        self.assertIsNone(c.blob_url("o/r", "a" * 40, "org:.github/AI_POLICY.md", 1))
        self.assertIsNone(c.blob_url("o/r", None, "org:.github/AI_POLICY.md", 1))

    def test_org_prefix_matches_discover(self):
        from oss_ai_policy_check import discover
        self.assertEqual(c.ORG_PREFIX, discover.ORG_PREFIX)

    def test_clip_quote(self):
        self.assertEqual(len(c.clip_quote(" ".join(["w"] * 40)).split()), 15)

    def test_clip_quote_caps_characters_too(self):
        short = "AI is banned here"
        self.assertEqual(c.clip_quote(short), short)  # no ellipsis when nothing is cut
        huge = c.clip_quote("AI " + "x" * 200_000)  # one giant "word" passes the word cap
        self.assertEqual(len(huge), c.MAX_QUOTE_CHARS)
        self.assertEqual(c.MAX_QUOTE_CHARS, 300)
        self.assertTrue(huge.endswith("…"))
        self.assertTrue(huge.startswith("AI xxx"))
        words = c.clip_quote(" ".join(["w" * 30] * 40))  # 15 words of 30 chars exceed 300 characters
        self.assertLessEqual(len(words), c.MAX_QUOTE_CHARS)

    def test_classifier_quotes_and_unclassified_lines_are_capped(self):
        result = classify_documents({"AI_POLICY.md": "AI contributions are not allowed " + "x" * 5000 + "\n"})
        self.assertTrue(result["evidence"])
        for item in result["evidence"]:
            self.assertLessEqual(len(item["quote"]), c.MAX_QUOTE_CHARS)
        result = classify_documents({"CONTRIBUTING.md": "We use AI " + "x" * 5000 + ".\n\nAI is mentioned.\n"})
        self.assertTrue(result["unclassified_ai_lines"])
        for item in result["unclassified_ai_lines"]:
            self.assertLessEqual(len(item["quote"]), c.MAX_QUOTE_CHARS)


class ReportShapeTests(unittest.TestCase):
    def snapshot(self, **kw):
        base = dict(repo="o/r", ref="main", commit_sha="a" * 40,
                    documents={"AI_POLICY.md": "AI contributions are not allowed.\n\nAI helps. We love AI."},
                    files_checked=[{"path": "AI_POLICY.md", "status": "read"}])
        base.update(kw)
        return c.Snapshot(**base)

    def test_org_evidence_gets_org_permalink_and_report_carries_sha(self):
        org_sha = "c" * 40
        s = self.snapshot(documents={"org:.github/AI_POLICY.md": "AI contributions are not allowed."},
                          files_checked=[{"path": "org:.github/AI_POLICY.md", "status": "read"}],
                          org_commit_sha=org_sha)
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual(report["org_commit_sha"], org_sha)
        self.assertEqual(report["evidence"][0]["url"], f"https://github.com/o/.github/blob/{org_sha}/AI_POLICY.md#L1")
        self.assertEqual(_support.schema_errors(report), [])

    def test_org_evidence_without_org_sha_has_no_url(self):
        s = self.snapshot(documents={"org:.github/AI_POLICY.md": "AI contributions are not allowed."},
                          files_checked=[{"path": "org:.github/AI_POLICY.md", "status": "read"}])
        report = c.build_report(s, classify_documents(s.documents))
        self.assertIsNone(report["org_commit_sha"])
        self.assertIsNone(report["evidence"][0]["url"])
        self.assertEqual(_support.schema_errors(report), [])

    def test_limitations_do_not_claim_org_is_unscanned(self):
        self.assertFalse(any("not scanned" in t and ".github" in t for t in c.LIMITATIONS))

    def test_report_conforms_to_schema(self):
        s = self.snapshot()
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual(_support.schema_errors(report), [])
        self.assertEqual(report["verdict"], "banned")
        self.assertEqual(set(report["evidence"][0]), {"file", "line", "url", "signal", "confidence", "quote"})
        self.assertEqual(report["confidence"], "low")  # one deciding sentence
        self.assertIsNone(report["review_reason"])
        self.assertTrue(report["evidence"][0]["url"].endswith("/blob/" + "a" * 40 + "/AI_POLICY.md#L1"))

    def test_report_with_unknown_sha_and_incomplete_conforms(self):
        s = self.snapshot(ref=None, commit_sha=None, complete=False, warnings=["read failed: x (HTTP 500)"],
                          files_checked=[{"path": "x", "status": "error"}])
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual(_support.schema_errors(report), [])
        self.assertIsNone(report["evidence"][0]["url"])
        self.assertFalse(report["complete"])

    def test_needs_review_report_cites_the_ai_policy_paragraphs(self):
        s = self.snapshot(documents={"CONTRIBUTING.md": "# Contributing\n\nSome text.\n\nAI-assisted code must pass the same review bar."},
                          files_checked=[{"path": "CONTRIBUTING.md", "status": "read"}])
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual((report["verdict"], report["confidence"], report["review_reason"]),
                         ("needs-review", "low", "no-rule-decision"))
        self.assertEqual([(e["file"], e["line"], e["signal"], e["confidence"]) for e in report["evidence"]],
                         [("CONTRIBUTING.md", 5, "needs-review", "low")])
        self.assertTrue(report["evidence"][0]["url"].endswith("/blob/" + "a" * 40 + "/CONTRIBUTING.md#L5"))
        self.assertLessEqual(len(report["evidence"][0]["quote"].split()), 15)
        self.assertEqual(_support.schema_errors(report), [])

    def test_conflict_report_cites_the_conflicting_lines(self):
        s = self.snapshot(documents={"AI_POLICY.md": "AI tools are welcome here.\n\nAI code is not allowed."})
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual((report["verdict"], report["review_reason"]), ("needs-review", "conflicting-signals"))
        self.assertEqual({e["signal"] for e in report["evidence"]}, {"permitted", "banned"})
        self.assertEqual(_support.schema_errors(report), [])

    def test_unread_files_make_no_policy_found_low_confidence(self):
        s = self.snapshot(documents={"AI_POLICY.md": "Be kind."}, complete=False, warnings=["read failed: x (HTTP 500)"])
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual((report["verdict"], report["confidence"], report["complete"]), ("no-policy-found", "low", False))

    def test_no_policy_found_is_high_confidence(self):
        s = self.snapshot(documents={"AI_POLICY.md": "Be kind."})
        report = c.build_report(s, classify_documents(s.documents))
        self.assertEqual((report["verdict"], report["confidence"], report["review_reason"]),
                         ("no-policy-found", "high", None))

    def test_schema_checker_rejects_bad_reports(self):
        s = self.snapshot()
        report = c.build_report(s, classify_documents(s.documents))
        report["verdict"] = "serbest"
        report["evidence"][0]["extra"] = 1
        del report["tool"]
        errors = _support.schema_errors(report)
        self.assertEqual(len(errors), 3, errors)

    def test_schema_enums_match_code(self):
        props = _support.SCHEMA["properties"]
        self.assertEqual(tuple(props["verdict"]["enum"]), c.VERDICTS)
        self.assertEqual(tuple(_support.SCHEMA["$defs"]["evidence"]["properties"]["signal"]["enum"]), c.SIGNALS)
        self.assertEqual(tuple(props["confidence"]["enum"]), c.CONFIDENCES)
        self.assertEqual(tuple(x for x in props["review_reason"]["enum"] if x), c.REVIEW_REASONS)
        self.assertEqual(tuple(_support.SCHEMA["$defs"]["evidence"]["properties"]["confidence"]["enum"]), c.CONFIDENCES)
        self.assertEqual(tuple(_support.SCHEMA["$defs"]["file_checked"]["properties"]["status"]["enum"]), c.FILE_STATUSES)
        self.assertEqual(props["schema_version"]["const"], c.SCHEMA_VERSION)
        # `classifier` is the one optional property: always written by build_report, absent from older reports
        self.assertEqual(set(_support.SCHEMA["required"]) | {"classifier"}, set(c.Report.__annotations__))
        self.assertEqual(tuple(props["classifier"]["properties"]["kind"]["enum"]), c.CLASSIFIERS)


if __name__ == "__main__":
    unittest.main()
