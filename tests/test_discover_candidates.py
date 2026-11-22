"""Candidate detection added with the accuracy work: AI policy names and .github noise."""
import unittest

import _support  # noqa: F401
from oss_ai_policy_check.discover import family, is_candidate


class CandidateNameTests(unittest.TestCase):
    def test_more_ai_policy_names(self):
        for path in ["AI_COVENANT.md", "docs/ai-contribution-guidelines.md", "AI_CONTRIBUTIONS.md", "docs/ai-assistance.rst",
                     ".github/AI_DISCLOSURE.md", "docs/llm-statement.md", "AI_USAGE_POLICY.md"]:
            self.assertTrue(is_candidate(path), path)

    def test_ai_in_other_words_is_not_a_policy_name(self):
        for path in ["docs/maintain.md", "docs/said.md", "docs/aim-contributing-notes.md"]:
            self.assertFalse(is_candidate(path), path)

    def test_covenant_family_is_ai_policy(self):
        self.assertEqual(family("AI_COVENANT.md"), "ai_policy")


class GithubNoiseTests(unittest.TestCase):
    def test_automation_and_agent_skills_are_not_candidates(self):
        for path in [".github/workflows/triage.md", ".github/actions/needs-triage/dist/licenses.txt",
                     ".github/agents/agentic-workflows.md", ".github/skills/cli-exercise/SKILL.md",
                     ".github/prompts/review.md", ".github/instructions/go.instructions.md",
                     ".github/CODEOWNERS", ".github/codeql/queries/notes.md", ".github/FUNDING.md"]:
            self.assertFalse(is_candidate(path), path)

    def test_real_github_community_files_stay_candidates(self):
        for path in [".github/PULL_REQUEST_TEMPLATE.md", ".github/PULL_REQUEST_TEMPLATE/bug.md", ".github/pull_request_template.md",
                     ".github/CONTRIBUTING.md", ".github/AGENTS.md", ".github/ISSUE_TEMPLATE/bug_report.md",
                     ".github/copilot-instructions.md", ".github/docs/policy.md", ".github/PULL_REQUEST_TEMPLATE.yml"]:
            self.assertTrue(is_candidate(path), path)

    def test_policy_named_file_in_a_noise_directory_is_still_read(self):
        self.assertTrue(is_candidate(".github/agents/AI_POLICY.md"))


if __name__ == "__main__":
    unittest.main()
