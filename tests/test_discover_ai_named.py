"""AI-named policy documents (AI.md, CONTRIBUTING.ai.md, docs/.../ai-coding.md) and AI-context links."""
import unittest

import _support  # noqa: F401
from oss_ai_policy_check.discover import (EXTRA_READ_LIMIT, ai_named_rank, family, is_candidate, is_core_candidate,
                                          linked_paths, split_candidates)


class AiNamedTests(unittest.TestCase):
    def test_ai_word_segment_in_policy_places(self):
        for path in ["AI.md", "ai.md", "docs/contributing/ai-coding.md", "policies/ai-policy.md", "CONTRIBUTING.ai.md",
                     "doc/src/contributing/ai-generated-code-policy.rst", ".github/llm.md", "docs/ai_use.txt",
                     "contributing/ai.md", "website/contributing/genai.adoc", "documentation/LLM-guidelines.md",
                     "docs/generative-ai.md", "policy/llms.md"]:
            self.assertTrue(ai_named_rank(path), path)
            self.assertTrue(is_candidate(path), path)

    def test_whole_segments_only(self):
        for path in ["docs/email.md", "docs/detail.md", "maintain.md", "docs/rails.md", "docs/openai-sdk.md",
                     "docs/chain.md", "docs/said-ai-not.md.bak", "docs/llmnr.md", "docs/aim.md", "docs/trainai.md"]:
            self.assertIsNone(ai_named_rank(path), path)
            self.assertFalse(is_candidate(path), path)

    def test_only_policy_places_and_text_files(self):
        for path in ["src/ai/policy.md", "lib/ai.md", "src/openai-sdk/ai.md", "ai.py", "docs/ai.png", "docs/ai",
                     "docs/examples/ai.md", "docs/de/ai.md", "docs/zh-hant/ai.md", "node_modules/x/ai.md",
                     ".github/workflows/ai.md", ".github/skills/ai.md", "docs/a/b/c/d/ai.md", "tests/ai.md"]:
            self.assertFalse(is_candidate(path), path)

    def test_contributing_siblings(self):
        for path in ["CONTRIBUTING.template.md", ".github/CONTRIBUTING.agents.md", "CONTRIBUTE.extra.rst"]:
            self.assertTrue(ai_named_rank(path), path)
        for path in ["CONTRIBUTING.de.md", "docs/CONTRIBUTING.zh-cn.md", "CONTRIBUTING.md.orig", "src/CONTRIBUTING.x.md"]:
            self.assertFalse(is_candidate(path), path)

    def test_family_separates_policy_documents_from_topic_pages(self):
        for path in ["AI.md", "CONTRIBUTING.ai.md", "docs/ai-coding.md", "docs/llm-policy.rst"]:
            self.assertEqual(family(path), "ai_policy", path)
        for path in ["docs/ai-features.md", "docs/llm-providers.md", "docs/ai-code-review.md", "CONTRIBUTING.template.md"]:
            self.assertNotEqual(family(path), "ai_policy", path)

    def test_core_candidates_are_unchanged(self):
        self.assertTrue(is_core_candidate("CONTRIBUTING.md"))
        self.assertTrue(is_core_candidate("docs/AI_POLICY.md"))
        self.assertFalse(is_core_candidate("AI.md"))


class SplitCandidatesTests(unittest.TestCase):
    def test_core_first_then_extras_most_relevant_first(self):
        paths = ["CONTRIBUTING.md", "docs/llm-providers.md", "docs/guide/ai-coding.md", "AI.md", "src/x.py", "src/ai.md"]
        selected, skipped = split_candidates(paths)
        self.assertEqual(selected, ["CONTRIBUTING.md", "docs/guide/ai-coding.md", "AI.md", "docs/llm-providers.md"])
        self.assertEqual(skipped, [])

    def test_limit_skips_the_least_relevant(self):
        paths = ["CONTRIBUTING.md"] + [f"docs/ai-notes-{i}.md" for i in range(5)] + ["docs/ai-coding.md", "AI.md"]
        selected, skipped = split_candidates(paths, limit=2)
        self.assertEqual(selected[0], "CONTRIBUTING.md")
        self.assertEqual(selected[1:], ["docs/ai-coding.md", "AI.md"])
        self.assertEqual(len(skipped), 5)

    def test_default_limit_and_out_of_scope_core_files_stay_unread(self):
        paths = [f"docs/ai-{i}.md" for i in range(EXTRA_READ_LIMIT + 3)] + ["src/CONTRIBUTING.md"]
        selected, skipped = split_candidates(paths)
        self.assertEqual(len(selected), EXTRA_READ_LIMIT)
        self.assertEqual(len(skipped), 3)
        self.assertNotIn("src/CONTRIBUTING.md", selected)


class AiLinkTests(unittest.TestCase):
    def test_link_whose_text_names_ai_is_followed_although_the_name_is_bare(self):
        text = "## Guidelines relevant to AI-assisted contributions\n\nSee the dedicated [AI.md](AI.md).\n"
        self.assertEqual(list(linked_paths("CONTRIBUTING.md", text, {"AI.md", "CONTRIBUTING.md"}, "a/b")), ["AI.md"])

    def test_policy_link_under_an_ai_heading_is_followed(self):
        text = "## Using AI tools\n\nPlease read our [usage policy](docs/guide.md).\n"
        self.assertEqual(list(linked_paths("README.md", text, {"docs/guide.md"}, "a/b")), ["docs/guide.md"])

    def test_unrelated_links_are_not_followed(self):
        text = ("## Using AI tools\n\nSee [testing](docs/testing.md) and the [layout](docs/project-layout.md).\n\n"
                "AI agents are welcome, see [build notes](docs/build.md).\n")
        self.assertEqual(list(linked_paths("AGENTS.md", text, {"docs/testing.md", "docs/project-layout.md", "docs/build.md"},
                                           "a/b")), [])

    def test_ai_links_can_be_switched_off(self):
        text = "See [AI.md](AI.md)."
        self.assertEqual(list(linked_paths("docs/x.md", text, {"AI.md"}, "a/b", ai_links=False)), [])

    def test_link_target_must_be_a_known_text_file_in_scope(self):
        text = "See [AI policy](src/ai.py), [AI policy](tools/notes.md), [AI policy](img/ai.png)."
        self.assertEqual(list(linked_paths("README.md", text, {"src/ai.py", "tools/notes.md", "img/ai.png"}, "a/b")), [])


if __name__ == "__main__":
    unittest.main()


class PolicyNamedTests(unittest.TestCase):
    """Ladybird keeps its AI clause in `Documentation/CodePolicy.md`; CONTRIBUTING links the docs tree, not that file."""

    def test_policy_named_files_in_doc_places_are_candidates_after_ai_named_ones(self):
        for path in ["Documentation/CodePolicy.md", "docs/code-policy.rst", "docs/Contribution_Policies.md", "POLICY.md",
                     ".github/code_policy.md"]:
            rank = ai_named_rank(path)
            self.assertIsNotNone(rank, path)
            self.assertEqual(rank[0], 3, path)
            self.assertTrue(is_candidate(path), path)
        self.assertLess(ai_named_rank("docs/ai.md"), ai_named_rank("Documentation/CodePolicy.md"))

    def test_off_topic_policies_and_other_places_are_not(self):
        for path in ["docs/privacy-policy.md", "docs/SecurityPolicy.md", "docs/license-policy.md", "docs/release-policy.md",
                     "docs/deprecation-policy.md", "src/CodePolicy.md", "tests/policy.md", "Documentation/policy.png",
                     "docs/de/code-policy.md", "docs/a/b/c/d/policy.md"]:
            self.assertFalse(is_candidate(path), path)

    def test_policy_named_files_are_capped_and_ranked_last(self):
        paths = ["CONTRIBUTING.md", "docs/a-policy.md", "docs/b-policy.md", "docs/c-policy.md", "docs/d-policy.md",
                 "docs/ai-coding.md"]
        selected, skipped = split_candidates(paths)
        self.assertEqual(selected, ["CONTRIBUTING.md", "docs/ai-coding.md", "docs/a-policy.md", "docs/b-policy.md",
                                    "docs/c-policy.md"])
        self.assertEqual(skipped, ["docs/d-policy.md"])

    def test_policy_named_files_do_not_take_the_ai_named_allowance(self):
        ai = [f"docs/ai-{n}.md" for n in "abcdefgh"]
        selected, skipped = split_candidates(["docs/z-policy.md"] + ai)
        self.assertEqual(sorted(selected), sorted(ai))
        self.assertEqual(skipped, ["docs/z-policy.md"])
