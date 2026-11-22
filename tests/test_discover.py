import unittest

import _support  # noqa: F401
from oss_ai_policy_check.discover import (family, has_ai_policy_text, in_scope, is_candidate, linked_paths,
                                          mentions_ai, org_policy_sources, select_candidates)


class DiscoverTests(unittest.TestCase):
    def test_policy_paths(self):
        for p in ["AGENT_POLICY.md", "llvm/docs/AIToolPolicy.md", "docs/devel/code-provenance.rst", "docs/dev/contribute.rst"]:
            self.assertTrue(is_candidate(p), p)
        self.assertFalse(is_candidate("src/ai_policy.py"))

    def test_link_resolution(self):
        self.assertEqual(list(linked_paths("AGENTS.md", "[policy](AGENT_POLICY.md)", {"AGENT_POLICY.md"}, "a/b")), ["AGENT_POLICY.md"])

    def test_assistant_instruction_files_are_candidates(self):
        for p in ["CLAUDE.md", ".cursorrules", "AGENTS.md", "AI_POLICY.md", "CONTRIBUTING.md",
                  "CODE_OF_CONDUCT.md", ".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.yml",
                  "README.md"]:
            self.assertTrue(is_candidate(p), p)

    def test_ai_use_files_are_candidates_in_any_case_and_place(self):
        for p in ["AI-USE.md", "AI_USE.md", "ai-use.md", "Ai_Use.MD", ".github/AI-USE.md", "docs/AI_USE.md",
                  "docs/ai-use.rst", "AI-USE"]:
            self.assertTrue(is_candidate(p), p)
        self.assertEqual(family("AI-USE.md"), "ai_policy")
        self.assertEqual(family(".github/ai_use.md"), "ai_policy")
        for p in ["src/ai_use.py", "bonsai-use.md", "docs/images/ai-use.png", "src/ai-user.md"]:
            self.assertFalse(is_candidate(p), p)

    def test_non_candidates(self):
        for p in ["src/main.py", "docs/image.png", "docs/README.md", "package.json"]:
            self.assertFalse(is_candidate(p), p)

    def test_select_candidates_sorted_and_unique(self):
        self.assertEqual(select_candidates(["b.py", "CONTRIBUTING.md", "AGENTS.md", "AGENTS.md"]),
                         ["AGENTS.md", "CONTRIBUTING.md"])

    def test_org_probe_list_without_tree(self):
        probes = org_policy_sources("owner")
        self.assertIn("CONTRIBUTING.md", probes)
        self.assertIn(".github/PULL_REQUEST_TEMPLATE.md", probes)
        self.assertIn("docs/AI_POLICY.md", probes)
        with self.assertRaises(ValueError):
            org_policy_sources("")

    def test_org_sources_skip_profile_readme_and_non_policy_files(self):
        org = ["CONTRIBUTING.md", "AI_POLICY.md", ".github/PULL_REQUEST_TEMPLATE.md", "profile/README.md",
               "README.md", "src/x.py", "scripts/CONTRIBUTING.md", "docs/guide.md", "LICENSE"]
        self.assertEqual(org_policy_sources("o", org),
                         [".github/PULL_REQUEST_TEMPLATE.md", "AI_POLICY.md", "CONTRIBUTING.md"])

    def test_repo_file_of_same_kind_wins_over_org_default(self):
        org = ["CONTRIBUTING.md", "AI_POLICY.md", ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/bug.md"]
        # The repository's own PR template wins, wherever it lives; CONTRIBUTING and AI policy files of the
        # organisation stay in (the caller only asks when the repository text has no AI policy statement).
        self.assertEqual(org_policy_sources("o", org, ["docs/contributing.rst", ".github/pull-request-template.md"]),
                         [".github/ISSUE_TEMPLATE/bug.md", "AI_POLICY.md", "CONTRIBUTING.md"])

    def test_family(self):
        self.assertEqual(family("docs/CONTRIBUTE.rst"), "contributing")
        self.assertEqual(family(".github/AI-POLICY.md"), "ai_policy")
        self.assertEqual(family(".github/ISSUE_TEMPLATE/bug.md"), "issue_template")
        self.assertEqual(family("pull-request-template.md"), "pull_request_template")

    def test_has_ai_policy_text_needs_ai_term_and_policy_verb_in_one_paragraph(self):
        for text in ["AI-generated code must be disclosed.", "We do not accept LLM output.",
                     "Intro.\n\nUse of Copilot is not allowed here.", "Agent-generated PRs will be rejected.",
                     "Machine-generated patches are banned.\r\n\r\nThanks"]:
            self.assertTrue(has_ai_policy_text(text), text)
        for text in ["Our AI features are great.", "README: AI-powered search.\n\nPatches must pass CI.",
                     "Contributors must sign the CLA.", "That was a banner ad for the said product.", ""]:
            self.assertFalse(has_ai_policy_text(text), text)

    def test_paragraph_that_links_to_the_owner_org_policy_is_a_pointer_not_a_policy(self):
        text = "We **require all use of AI in contributions to follow our\n[AI Policy](https://github.com/o/.github/blob/main/AI_POLICY.md)**."
        self.assertTrue(has_ai_policy_text(text))                      # without an owner it reads as a statement
        self.assertFalse(has_ai_policy_text(text, "o"))
        self.assertFalse(has_ai_policy_text(text, "O"))
        self.assertTrue(has_ai_policy_text(text, "other"))             # a link into someone else's .github is not ours
        self.assertTrue(has_ai_policy_text(text + "\n\nAI use must be disclosed.", "o"))

    def test_in_scope(self):
        for p in ["README.md", ".github/x.md", "Docs/a/b/c.md", "llvm/docs/AIToolPolicy.md"]:
            self.assertTrue(in_scope(p), p)
        for p in ["src/CONTRIBUTING.md", "llvm/lib/x.md", "llvm/CONTRIBUTING.md"]:
            self.assertFalse(in_scope(p), p)

    def test_mentions_ai_is_case_aware(self):
        for text in ["No AI-generated code.", "We ban LLMs.", "Do not use Copilot", "uses a language model"]:
            self.assertTrue(mentions_ai(text), text)
        for text in ["He said maintain it", "Please wait for review", "email me at a@b.c"]:
            self.assertFalse(mentions_ai(text), text)

    def test_links_reach_candidates_outside_the_default_scope(self):
        paths = {"CONTRIBUTING.md", "tools/AI_POLICY.md", "tools/notes.md"}
        text = "[a](tools/AI_POLICY.md) [b](tools/notes.md)"
        self.assertEqual(list(linked_paths("CONTRIBUTING.md", text, paths, "a/b")), ["tools/AI_POLICY.md"])


if __name__ == "__main__":
    unittest.main()
