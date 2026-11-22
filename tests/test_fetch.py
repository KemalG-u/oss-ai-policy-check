import email.message
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import _support  # noqa: F401
from _fakes import ORG_SHA, SHA, FakeGithub
from oss_ai_policy_check.contract import MAX_FILE_BYTES
from oss_ai_policy_check.fetch import (AutoTransport, FetchError, GhCliTransport, NetworkError, NotFoundError,
                                       RateLimitedError, RestTransport, TooLargeError, _TrustedRedirects,
                                       check_repo, fetch_snapshot, github_token, read_local_directory)


class RemoteSnapshotTests(unittest.TestCase):
    def test_failed_read_visible(self):
        s = fetch_snapshot("a/b", client=FakeGithub(fail=True))
        self.assertFalse(s.complete)
        self.assertTrue(s.warnings)
        self.assertEqual(s.files_checked, [])

    def test_truncated_visible(self):
        self.assertFalse(fetch_snapshot("a/b", client=FakeGithub(truncated=True)).complete)

    def test_pinned_commit_and_success(self):
        s = fetch_snapshot("a/b", client=FakeGithub())
        self.assertEqual(s.commit_sha, SHA)
        self.assertEqual(s.ref, "main")
        self.assertTrue(s.complete)
        self.assertEqual(s.files_checked, [{"path": ".github/AI_POLICY.md", "status": "read"}])
        self.assertEqual(s.documents, {".github/AI_POLICY.md": "All AI usage must be disclosed."})

    def test_content_read_is_pinned_to_sha(self):
        fake = FakeGithub(use_raw=False)  # a client without raw falls back to the contents API
        s = fetch_snapshot("a/b", client=fake)
        contents = [e for e in fake.endpoints if "/contents/" in e]
        self.assertTrue(contents and all(e.endswith(f"?ref={SHA}") for e in contents))
        self.assertEqual(s.documents, {".github/AI_POLICY.md": "All AI usage must be disclosed."})

    def test_raw_read_is_pinned_to_sha(self):
        fake = FakeGithub()
        fetch_snapshot("a/b", client=fake)
        self.assertEqual(fake.raw_requests, [("a/b", SHA, ".github/AI_POLICY.md")])
        self.assertFalse([e for e in fake.endpoints if "/contents/" in e])

    def test_invalid_repo(self):
        with self.assertRaises(ValueError):
            fetch_snapshot("a/b;bad", client=FakeGithub())
        with self.assertRaises(ValueError):
            check_repo("justname")

    def test_dot_segments_are_not_a_repository(self):
        for bad in ("./b", "../b", "a/.", "a/..", "./.", "../.."):
            with self.assertRaises(ValueError, msg=bad):
                check_repo(bad)
            with self.assertRaises(ValueError, msg=bad):
                fetch_snapshot(bad, client=FakeGithub())
        for good in ("a/b", ".github/.github", "a/b.c", "a..b/c", "a/..b"):
            self.assertEqual(check_repo(good), good)

    def test_explicit_ref_skips_default_branch_lookup(self):
        fake = FakeGithub()
        s = fetch_snapshot("a/b", ref="release/1.0", client=fake)
        self.assertEqual(s.ref, "release/1.0")
        self.assertNotIn("repos/a/b", fake.endpoints)
        self.assertIn("repos/a/b/commits/release%2F1.0", fake.endpoints)

    def test_missing_is_not_incomplete_but_error_is(self):
        fake =FakeGithub(files={".github/a.md": b"x", ".github/b.md": b"y"},
                          behaviors={".github/a.md": NotFoundError("HTTP 404"), ".github/b.md": FetchError("HTTP 500")})
        s = fetch_snapshot("a/b", client=fake)
        status = {f["path"]: f["status"] for f in s.files_checked}
        self.assertEqual(status, {".github/a.md": "missing", ".github/b.md": "error"})
        self.assertFalse(s.complete)
        self.assertEqual(s.warnings, ["read failed: .github/b.md (HTTP 500)"])
        only_missing = fetch_snapshot("a/b", client=FakeGithub(files={".github/a.md": b"x"},
                                                               behaviors={".github/a.md": NotFoundError("HTTP 404")}))
        self.assertTrue(only_missing.complete)

    def test_binary_file_is_read_error_not_crash(self):
        s = fetch_snapshot("a/b", client=FakeGithub(files={".github/a.md": b"\xff\xfe\x00bad"}))
        self.assertEqual(s.files_checked, [{"path": ".github/a.md", "status": "error"}])
        self.assertFalse(s.complete)


class FakeResponse:
    def __init__(self, body=b"{}", headers=None):
        self._body, self.headers = body, headers or {}

    def read(self, n=-1):
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _headers(mapping):
    message = email.message.Message()
    for key, value in mapping.items():
        message[key] = value
    return message


class FakeOpener:
    """Maps URL -> FakeResponse, or an int HTTP status / (status, headers) to raise as HTTPError."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def open(self, request, timeout=None):
        self.requests.append(request)
        route = self.routes[request.full_url]
        if isinstance(route, FakeResponse):
            return route
        status, headers = route if isinstance(route, tuple) else (route, {})
        raise urllib.error.HTTPError(request.full_url, status, "x", _headers(headers), None)


SECRET = "ghp_SUPERSECRETTOKENVALUE123"
API = "https://api.github.com/"
RAW = "https://raw.githubusercontent.com/"


class TokenSelectionTests(unittest.TestCase):
    def test_order_github_token_then_gh_token_then_none(self):
        self.assertEqual(github_token({"GITHUB_TOKEN": "a", "GH_TOKEN": "b"}), "a")
        self.assertEqual(github_token({"GITHUB_TOKEN": "", "GH_TOKEN": "b"}), "b")
        self.assertIsNone(github_token({"GITHUB_TOKEN": "  ", "GH_TOKEN": " "}))
        self.assertIsNone(github_token({}))


class RestTransportTests(unittest.TestCase):
    def test_token_sent_to_api_and_raw_only_as_bearer(self):
        opener = FakeOpener({API + "repos/a/b": FakeResponse(b'{"default_branch": "main"}'),
                             RAW + f"a/b/{SHA}/CONTRIBUTING.md": FakeResponse(b"hello")})
        t = RestTransport(SECRET, opener=opener)
        self.assertEqual(t.api("repos/a/b"), {"default_branch": "main"})
        self.assertEqual(t.raw("a/b", SHA, "CONTRIBUTING.md"), b"hello")
        for request in opener.requests:
            self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
            self.assertEqual(request.get_method(), "GET")
            self.assertTrue(request.get_header("User-agent").startswith("oss-ai-policy-check/"))
        self.assertEqual((t.calls, t.raw_calls), (1, 1))

    def test_anonymous_sends_no_authorization(self):
        opener = FakeOpener({API + "repos/a/b": FakeResponse(b"{}")})
        RestTransport(None, opener=opener).api("repos/a/b")
        self.assertIsNone(opener.requests[0].get_header("Authorization"))

    def test_api_response_cached_in_memory(self):
        opener = FakeOpener({API + "repos/a/b": FakeResponse(b"{}")})
        t = RestTransport(opener=opener)
        t.api("repos/a/b")
        t.api("repos/a/b")
        self.assertEqual((t.calls, len(opener.requests)), (1, 1))

    def test_allowlist(self):
        t = RestTransport(opener=FakeOpener({}))
        for endpoint in ("https://api.github.com/repos/a/b", "user", "graphql", "search/issues?q=x"):
            with self.assertRaises(FetchError):
                t.api(endpoint)
        self.assertEqual(t.calls, 0)

    def test_404_is_not_found(self):
        with self.assertRaises(NotFoundError):
            RestTransport(opener=FakeOpener({API + "repos/a/b": 404})).api("repos/a/b")

    def test_rate_limit_message_anonymous_names_the_fix_and_reset(self):
        reset = str(int(time.time()) + 20 * 60)
        opener = FakeOpener({API + "repos/a/b": (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset})})
        with self.assertRaises(RateLimitedError) as ctx:
            RestTransport(opener=opener).api("repos/a/b")
        message = str(ctx.exception)
        self.assertIn("rate limited, set GITHUB_TOKEN", message)
        self.assertRegex(message, r"resets in (19|20|21) min, at \d\d:\d\d UTC")

    def test_rate_limit_with_token_does_not_ask_for_one_and_never_leaks_it(self):
        for status, headers in ((403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1"}), (429, {"Retry-After": "30"})):
            opener = FakeOpener({API + "repos/a/b": (status, headers)})
            with self.assertRaises(RateLimitedError) as ctx:
                RestTransport(SECRET, opener=opener).api("repos/a/b")
            self.assertTrue(str(ctx.exception).startswith("rate limited"))
            self.assertNotIn("GITHUB_TOKEN", str(ctx.exception))
            self.assertNotIn(SECRET, str(ctx.exception))

    def test_plain_403_is_not_a_rate_limit(self):
        with self.assertRaises(FetchError) as ctx:
            RestTransport(SECRET, opener=FakeOpener({API + "repos/a/b": 403})).api("repos/a/b")
        self.assertNotIsInstance(ctx.exception, RateLimitedError)
        self.assertEqual(str(ctx.exception), "HTTP 403")

    def test_network_failure_message_has_no_url_or_token(self):
        class Broken:
            def open(self, request, timeout=None):
                raise urllib.error.URLError(TimeoutError(request.full_url))
        with self.assertRaises(NetworkError) as ctx:
            RestTransport(SECRET, opener=Broken()).api("repos/a/b")
        self.assertEqual(str(ctx.exception), "network error: TimeoutError")

    def test_api_reply_over_the_read_cap_is_too_large_not_invalid_json(self):
        t = RestTransport(opener=FakeOpener({API + "repos/a/b": FakeResponse(b" " * 16_000_001)}))
        with self.assertRaises(TooLargeError):
            t.api("repos/a/b")

    def test_raw_requires_full_sha_and_bounds_size(self):
        t = RestTransport(opener=FakeOpener({RAW + f"a/b/{SHA}/big.md": FakeResponse(b"x" * (MAX_FILE_BYTES + 1))}))
        with self.assertRaises(FetchError):
            t.raw("a/b", "main", "CONTRIBUTING.md")
        with self.assertRaises(FetchError):
            t.raw("a/b", SHA, "big.md")

    def test_raw_url_quotes_path_and_missing_is_not_found(self):
        opener = FakeOpener({RAW + f"a/b/{SHA}/docs/a%20b.md": FakeResponse(b"ok"), RAW + f"a/b/{SHA}/nope.md": 404})
        t = RestTransport(opener=opener)
        self.assertEqual(t.raw("a/b", SHA, "docs/a b.md"), b"ok")
        with self.assertRaises(NotFoundError):
            t.raw("a/b", SHA, "nope.md")

    def test_redirects_stay_on_github_hosts(self):
        handler = _TrustedRedirects()
        request = urllib.request.Request(API + "repos/a/b", headers={"Authorization": "Bearer " + SECRET})
        followed = handler.redirect_request(request, None, 301, "x", {}, API + "repositories/1")
        self.assertEqual(followed.full_url, API + "repositories/1")
        for target in ("https://evil.example/x", "http://api.github.com/x"):
            with self.assertRaises(FetchError):
                handler.redirect_request(request, None, 301, "x", {}, target)


class FakeGh:
    def __init__(self, error=None):
        self.calls, self.error = 0, error

    def api(self, endpoint):
        self.calls += 1
        if self.error:
            raise self.error
        return {"via": "gh"}


class FakeRest:
    has_token = False
    calls = raw_calls = 0

    def __init__(self, error):
        self.error = error

    def api(self, endpoint):
        raise self.error

    def raw(self, repo, sha, path):
        return b"raw"


class AutoTransportTests(unittest.TestCase):
    def test_mode_reflects_token(self):
        self.assertEqual(AutoTransport("t", rest=RestTransport("t", opener=FakeOpener({}))).mode, "rest+token")
        self.assertEqual(AutoTransport(None, rest=RestTransport(opener=FakeOpener({}))).mode, "rest-anonymous")

    def test_rate_limit_falls_back_to_gh_when_on_path(self):
        gh = FakeGh()
        t = AutoTransport(rest=FakeRest(RateLimitedError("rate limited, set GITHUB_TOKEN")), gh=gh, which=lambda n: "gh")
        self.assertEqual(t.api("repos/a/b"), {"via": "gh"})
        self.assertEqual(t.mode, "gh")
        t.api("repos/a/b")
        self.assertEqual(gh.calls, 2)
        self.assertEqual(t.raw("a/b", SHA, "x"), b"raw")  # raw stays on REST

    def test_no_gh_on_path_gives_the_rate_limit_message(self):
        t = AutoTransport(rest=FakeRest(RateLimitedError("rate limited, set GITHUB_TOKEN")), which=lambda n: None)
        with self.assertRaises(RateLimitedError) as ctx:
            t.api("repos/a/b")
        self.assertIn("set GITHUB_TOKEN", str(ctx.exception))

    def test_failed_gh_keeps_the_rest_message(self):
        t = AutoTransport(rest=FakeRest(RateLimitedError("rate limited, set GITHUB_TOKEN")),
                          gh=FakeGh(FetchError("missing API headers")), which=lambda n: "gh")
        with self.assertRaises(RateLimitedError):
            t.api("repos/a/b")

    def test_not_found_never_falls_back(self):
        gh = FakeGh()
        t = AutoTransport(rest=FakeRest(NotFoundError("HTTP 404")), gh=gh, which=lambda n: "gh")
        with self.assertRaises(NotFoundError):
            t.api("repos/a/b")
        self.assertEqual(gh.calls, 0)


class TransportGuardTests(unittest.TestCase):
    def test_endpoint_allowlist_blocks_before_any_process(self):
        t = GhCliTransport(delay=0)
        for endpoint in ("https://api.github.com/repos/a/b", "user", "graphql", "search/issues?q=x"):
            with self.assertRaises(FetchError):
                t.api(endpoint)
        self.assertEqual(t.calls, 0)


class RequestBudgetTests(unittest.TestCase):
    def test_default_flow_is_three_api_calls_and_one_raw_per_file(self):
        fake = FakeGithub()
        fetch_snapshot("a/b", client=fake)
        self.assertEqual(fake.endpoints, ["repos/a/b", "repos/a/b/commits/main", f"repos/a/b/git/trees/{SHA}?recursive=1"])
        self.assertEqual(fake.calls, 3)
        self.assertEqual(fake.raw_calls, 1)

    def test_full_sha_ref_skips_branch_and_commit_lookup(self):
        fake = FakeGithub()
        s = fetch_snapshot("a/b", ref=SHA, client=fake)
        self.assertEqual(fake.endpoints, [f"repos/a/b/git/trees/{SHA}?recursive=1"])
        self.assertEqual((s.ref, s.commit_sha), (SHA, SHA))

    def test_snapshot_reports_api_calls(self):
        self.assertEqual(fetch_snapshot("a/b", client=FakeGithub()).api_calls, 3)

    def test_truncated_tree_falls_back_to_subtrees(self):
        fake = FakeGithub(truncated=True)
        s = fetch_snapshot("a/b", client=fake)
        self.assertIn(f"repos/a/b/git/trees/{SHA}", fake.endpoints)
        self.assertEqual(s.warnings, ["truncated tree: .github"])
        self.assertEqual(list(s.documents), [".github/AI_POLICY.md"])

    def test_oversize_recursive_tree_falls_back_without_error(self):
        fake = FakeGithub(oversize=True, files={"README.md": b"hi", "CONTRIBUTING.md": b"Be kind.",
                                                ".github/AI_POLICY.md": b"All AI usage must be disclosed.",
                                                "src/deep/x.py": b"x"})
        s = fetch_snapshot("a/b", client=fake)
        self.assertTrue(s.complete, s.warnings)
        self.assertEqual(sorted(s.documents), [".github/AI_POLICY.md", "CONTRIBUTING.md", "README.md"])
        self.assertEqual(fake.endpoints, ["repos/a/b", "repos/a/b/commits/main",
                                          f"repos/a/b/git/trees/{SHA}?recursive=1",   # too large: never parsed
                                          f"repos/a/b/git/trees/{SHA}",               # root, non-recursive
                                          "repos/a/b/git/trees/T:.github?recursive=1"])
        self.assertEqual(fake.calls, 5)

    def test_oversize_docs_subtree_is_a_warning_not_a_crash(self):
        fake = FakeGithub(oversize="all", files={"CONTRIBUTING.md": b"Be kind.", "docs/a.md": b"x"})
        s = fetch_snapshot("a/b", client=fake)
        self.assertEqual(s.warnings, ["truncated tree: docs"])
        self.assertEqual(list(s.documents), ["CONTRIBUTING.md"])

    def test_oversize_tree_over_real_transport_never_reaches_json_parser(self):
        huge = b" " * 16_000_001  # what a cut-off nixpkgs/rust tree looks like at the read cap
        root = (b'{"tree": [{"path": "CONTRIBUTING.md", "type": "blob", "sha": "b"},'
                b' {"path": "src", "type": "tree", "sha": "t"}]}')
        opener = FakeOpener({API + "repos/a/b": FakeResponse(b'{"default_branch": "main"}'),
                             API + "repos/a/b/commits/main": FakeResponse(('{"sha": "%s"}' % SHA).encode()),
                             API + f"repos/a/b/git/trees/{SHA}?recursive=1": FakeResponse(huge),
                             API + f"repos/a/b/git/trees/{SHA}": FakeResponse(root),
                             API + "repos/a/.github/commits/HEAD": 404,
                             RAW + f"a/b/{SHA}/CONTRIBUTING.md": FakeResponse(b"Be kind.")})
        t = RestTransport(opener=opener)
        s = fetch_snapshot("a/b", client=t)
        self.assertEqual(s.warnings, [])
        self.assertTrue(s.complete)
        self.assertEqual(list(s.documents), ["CONTRIBUTING.md"])
        self.assertEqual(t.calls, 5)  # repo, commit, oversize recursive, root, org probe (404)

    def test_scope_and_nested_candidates(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind.", "src/CONTRIBUTING.md": b"x", "src/main.py": b"x",
                                 "docs/dev/AGENTS.md": b"AI agents: ask first."})
        s = fetch_snapshot("a/b", client=fake)
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "docs/dev/AGENTS.md"])
        self.assertEqual([r[2] for r in fake.raw_requests].count("src/CONTRIBUTING.md"), 0)


class LinkTests(unittest.TestCase):
    FILES = {"CONTRIBUTING.md": b"Read the [AI policy](tools/AI_POLICY.md) and [code](src/main.py).",
             "tools/AI_POLICY.md": b"Disclose AI use. See [more](tools/deep/AI_POLICY2.md).",
             "tools/deep/AI_POLICY2.md": b"second level",
             "src/CONTRIBUTING.md": b"not linked"}

    def test_same_repo_link_followed_one_level_only(self):
        s = fetch_snapshot("a/b", client=FakeGithub(files=dict(self.FILES)))
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "tools/AI_POLICY.md"])
        self.assertEqual({f["path"]: f["status"] for f in s.files_checked},
                         {"CONTRIBUTING.md": "read", "tools/AI_POLICY.md": "read"})
        self.assertTrue(s.complete)

    def test_local_directory_still_follows_links_inside_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "CONTRIBUTING.md").write_text("See [policy](docs/AI_POLICY.md).", encoding="utf-8")
            (root / "docs" / "AI_POLICY.md").write_text("ok", encoding="utf-8")
            self.assertEqual(sorted(read_local_directory(root, "a/b").documents), ["CONTRIBUTING.md", "docs/AI_POLICY.md"])


class AiNamedDiscoveryTests(unittest.TestCase):
    def test_ai_named_files_and_contributing_siblings_are_read(self):
        files = {"CONTRIBUTING.md": b"Be kind.", "AI.md": b"Disclose AI use.", "CONTRIBUTING.ai.md": b"AI is welcome.",
                 "policies/ai-policy.md": b"Agents may not open pull requests.",
                 "doc/src/contributing/ai-generated-code-policy.rst": b"Disclose.",
                 "docs/contributing/ai-coding.md": b"Same standards.", "CONTRIBUTING.template.md": b"x"}
        s = fetch_snapshot("a/b", client=FakeGithub(files=files, org_files=None))
        self.assertEqual(sorted(s.documents), sorted(files))
        self.assertTrue(s.complete)

    def test_look_alike_names_and_source_directories_are_not_read(self):
        files = {"CONTRIBUTING.md": b"Be kind.", "docs/email.md": b"x", "docs/detail.md": b"x", "docs/rails.md": b"x",
                 "docs/openai-sdk.md": b"x", "src/ai.md": b"x", "src/openai-sdk/ai.md": b"x", "docs/de/ai.md": b"x",
                 "CONTRIBUTING.de.md": b"x"}
        fake = FakeGithub(files=files, org_files=None)
        s = fetch_snapshot("a/b", client=fake)
        self.assertEqual(list(s.documents), ["CONTRIBUTING.md"])
        self.assertEqual(len(fake.raw_requests), 1)

    def test_extra_reads_are_capped_and_the_skipped_ones_are_named(self):
        files = {"CONTRIBUTING.md": b"Be kind."}
        files.update({f"docs/ai-topic-{i:02d}.md": b"x" for i in range(12)})
        files["docs/ai-coding.md"] = b"Same standards."  # most relevant: always read
        fake = FakeGithub(files=files, org_files=None)
        s = fetch_snapshot("a/b", client=fake)
        self.assertEqual(len(s.documents), 1 + 8)
        self.assertIn("docs/ai-coding.md", s.documents)
        self.assertEqual(len(fake.raw_requests), 9)
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("AI-named files not read (limit 8)", s.warnings[0])
        self.assertIn("docs/ai-topic-11.md", s.warnings[0])
        self.assertFalse(s.complete)

    def test_bare_named_file_is_found_through_its_link(self):
        files = {"CONTRIBUTING.md": b"## AI-assisted contributions\n\nSee the dedicated [policy](docs/handbook/rules.md).\n",
                 "docs/handbook/rules.md": b"Disclose AI use.", "docs/handbook/other.md": b"x"}
        s = fetch_snapshot("a/b", client=FakeGithub(files=files, org_files=None))
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "docs/handbook/rules.md"])

    def test_ai_links_are_followed_from_core_files_only(self):
        files = {"CONTRIBUTING.md": b"Be kind.", "docs/ai-coding.md": b"See [AI rules](docs/handbook/rules.md).",
                 "docs/handbook/rules.md": b"Disclose AI use."}
        s = fetch_snapshot("a/b", client=FakeGithub(files=files, org_files=None))
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "docs/ai-coding.md"])

    def test_policy_directory_is_listed_when_the_tree_is_truncated(self):
        files = {"CONTRIBUTING.md": b"Be kind.", "policies/ai-policy.md": b"Disclose AI use.", "src/ai.md": b"x"}
        s = fetch_snapshot("a/b", client=FakeGithub(files=files, truncated=True, org_files=None))
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "policies/ai-policy.md"])

    def test_policy_named_ai_file_stops_the_org_lookup(self):
        files = {"CONTRIBUTING.md": b"Be kind.", "AI.md": b"Contributions are tracked."}
        org = {"CONTRIBUTING.md": b"Org rules: disclose AI use."}
        s = fetch_snapshot("a/b", client=FakeGithub(files=files, org_files=org))
        self.assertEqual(sorted(s.documents), ["AI.md", "CONTRIBUTING.md"])
        self.assertIsNone(s.org_commit_sha)

    def test_local_directory_reads_ai_named_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "policies").mkdir()
            (root / "AI.md").write_text("Disclose AI use.", encoding="utf-8")
            (root / "policies" / "ai-policy.md").write_text("No agents.", encoding="utf-8")
            (root / "policies" / "other.md").write_text("x", encoding="utf-8")
            self.assertEqual(sorted(read_local_directory(root, "a/b").documents), ["AI.md", "policies/ai-policy.md"])


class CacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_second_run_same_sha_makes_zero_raw_requests(self):
        first, second = FakeGithub(), FakeGithub()
        a = fetch_snapshot("a/b", client=first, cache_dir=self.dir)
        b = fetch_snapshot("a/b", client=second, cache_dir=self.dir)
        self.assertEqual(first.raw_calls, 1)
        self.assertEqual(second.raw_calls, 0)
        self.assertEqual(second.endpoints, ["repos/a/b", "repos/a/b/commits/main"])  # tree came from disk
        self.assertEqual((a.documents, a.files_checked, a.commit_sha), (b.documents, b.files_checked, b.commit_sha))
        self.assertTrue(b.complete)

    def test_no_cache_always_reads(self):
        for _ in range(2):
            fake = FakeGithub()
            fetch_snapshot("a/b", client=fake, cache_dir=self.dir, no_cache=True)
            self.assertEqual((fake.raw_calls, fake.calls), (1, 3))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_injected_client_without_cache_dir_never_touches_disk(self):
        for _ in range(2):
            fake = FakeGithub()
            fetch_snapshot("a/b", client=fake)
            self.assertEqual(fake.raw_calls, 1)

    def test_other_sha_is_a_miss(self):
        fetch_snapshot("a/b", client=FakeGithub(), cache_dir=self.dir)
        other = FakeGithub()
        fetch_snapshot("a/b", ref="f" * 40, client=other, cache_dir=self.dir)  # different, unknown sha
        self.assertEqual(other.raw_calls, 0)  # nothing readable there, and no stale reuse
        self.assertEqual(other.calls, 1)

    def test_corrupt_cache_is_a_miss_not_an_error(self):
        fetch_snapshot("a/b", client=FakeGithub(), cache_dir=self.dir)
        for path in self.dir.rglob("*"):
            if path.is_file():
                path.write_bytes(b"\x00garbage{")
        fake = FakeGithub()
        s = fetch_snapshot("a/b", client=fake, cache_dir=self.dir)
        self.assertEqual(s.documents, {".github/AI_POLICY.md": "All AI usage must be disclosed."})
        self.assertEqual(fake.calls, 3)

    def test_errors_are_not_cached(self):
        broken = FakeGithub(behaviors={".github/AI_POLICY.md": FetchError("HTTP 500")})
        self.assertFalse(fetch_snapshot("a/b", client=broken, cache_dir=self.dir).complete)
        healthy = FakeGithub()
        s = fetch_snapshot("a/b", client=healthy, cache_dir=self.dir)
        self.assertTrue(s.complete)
        self.assertEqual(healthy.raw_calls, 1)


class OrgRepositoryTests(unittest.TestCase):
    ORG = {"CONTRIBUTING.md": b"org contributing", "AI_POLICY.md": b"All AI usage must be disclosed.",
           ".github/PULL_REQUEST_TEMPLATE.md": b"pr template", "profile/README.md": b"profile", "README.md": b"readme",
           "src/x.py": b"print()"}

    def test_org_files_fill_gaps_and_are_prefixed(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_files=dict(self.ORG))
        s = fetch_snapshot("a/b", client=fake)
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "org:.github/.github/PULL_REQUEST_TEMPLATE.md",
                                               "org:.github/AI_POLICY.md", "org:.github/CONTRIBUTING.md"])
        self.assertEqual(s.documents["CONTRIBUTING.md"], "Be kind.")  # both are read; the repo text is kept as is
        self.assertEqual({f["path"] for f in s.files_checked}, set(s.documents))
        self.assertTrue(s.complete)
        self.assertEqual(s.commit_sha, SHA)

    def test_org_commit_sha_set_only_when_org_files_were_read(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_files=dict(self.ORG))
        self.assertEqual(fetch_snapshot("a/b", client=fake).org_commit_sha, ORG_SHA)
        for kw, fake in (({"org": False}, FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_files=dict(self.ORG))),
                         ({}, FakeGithub(files={"CONTRIBUTING.md": b"Be kind."})),
                         ({}, FakeGithub(files={"CONTRIBUTING.md": b"We ban AI."}, org_files=dict(self.ORG)))):
            self.assertIsNone(fetch_snapshot("a/b", client=fake, **kw).org_commit_sha)

    def test_org_costs_two_more_api_calls_and_reads_pinned_raw(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_files=dict(self.ORG))
        fetch_snapshot("a/b", client=fake)
        self.assertEqual(fake.calls, 5)
        self.assertIn("repos/a/.github/commits/HEAD", fake.endpoints)
        self.assertIn(("a/.github", ORG_SHA, "AI_POLICY.md"), fake.raw_requests)

    def test_repo_that_mentions_ai_does_not_look_at_org(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"We do not take AI-generated code."}, org_files=dict(self.ORG))
        s = fetch_snapshot("a/b", client=fake)
        self.assertEqual(list(s.documents), ["CONTRIBUTING.md"])
        self.assertEqual(fake.calls, 3)

    def test_org_contributing_is_read_although_the_repo_has_its_own(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind.", "README.md": b"Our AI features are great."},
                          org_files={"CONTRIBUTING.md": b"AI-generated code must be disclosed."})
        s = fetch_snapshot("a/b", client=fake)  # a bare mention of AI in the README is not a policy
        self.assertEqual(sorted(s.documents), ["CONTRIBUTING.md", "README.md", "org:.github/CONTRIBUTING.md"])
        self.assertEqual({f["path"]: f["status"] for f in s.files_checked}["org:.github/CONTRIBUTING.md"], "read")
        self.assertEqual(s.org_commit_sha, ORG_SHA)
        self.assertEqual(fake.calls, 5)  # repo, commit, tree + org commit, org tree: org adds exactly two

    def test_repo_policy_paragraph_beats_org_in_any_file(self):
        org = {"CONTRIBUTING.md": b"AI-generated code is banned."}
        for files in ({"CONTRIBUTING.md": b"Pull requests with LLM output must be disclosed."},
                      {"CONTRIBUTING.md": b"Be kind.", "docs/AGENTS.md": b"Contributors must not submit Copilot output."},
                      {"README.md": b"Agent-generated patches are rejected."}):
            fake = FakeGithub(files=dict(files), org_files=dict(org))
            s = fetch_snapshot("a/b", client=fake)
            self.assertFalse(any(p.startswith("org:") for p in s.documents), files)
            self.assertEqual(fake.calls, 3, files)

    def test_ai_term_and_policy_verb_in_different_paragraphs_do_not_count(self):
        text = b"We support AI workflows.\n\nPull requests must pass CI."
        fake = FakeGithub(files={"CONTRIBUTING.md": text}, org_files={"CONTRIBUTING.md": b"org"})
        self.assertIn("org:.github/CONTRIBUTING.md", fetch_snapshot("a/b", client=fake).documents)

    def test_repo_that_only_points_to_the_org_policy_still_reads_it(self):
        pointer = b"We require all use of AI to follow our [AI Policy](https://github.com/a/.github/blob/main/AI_POLICY.md)."
        fake = FakeGithub(files={"CONTRIBUTING.md": pointer}, org_files={"AI_POLICY.md": b"AI use must be disclosed."})
        s = fetch_snapshot("a/b", client=fake)
        self.assertIn("org:.github/AI_POLICY.md", s.documents)
        self.assertEqual(fake.calls, 5)

    def test_org_false_skips_lookup(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_files=dict(self.ORG))
        fetch_snapshot("a/b", client=fake, org=False)
        self.assertEqual(fake.calls, 3)

    def test_missing_org_repo_is_fine(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."})
        s = fetch_snapshot("a/b", client=fake)
        self.assertTrue(s.complete)
        self.assertEqual(list(s.documents), ["CONTRIBUTING.md"])
        self.assertEqual(fake.calls, 4)  # the 404 probe is the only extra call

    def test_org_lookup_error_makes_report_incomplete(self):
        fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_error=RateLimitedError("rate limited"))
        s = fetch_snapshot("a/b", client=fake)
        self.assertFalse(s.complete)
        self.assertEqual(s.documents, {"CONTRIBUTING.md": "Be kind."})
        self.assertIn("organization .github lookup failed (rate limited)", s.warnings)

    def test_org_files_cached_by_org_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(2):
                fake = FakeGithub(files={"CONTRIBUTING.md": b"Be kind."}, org_files=dict(self.ORG))
                s = fetch_snapshot("a/b", client=fake, cache_dir=tmp)
            self.assertEqual(fake.raw_calls, 0)
            self.assertIn("org:.github/AI_POLICY.md", s.documents)
            self.assertEqual(fake.calls, 3)  # repo, commit, org commit; both trees from disk


class RateLimitedReadTests(unittest.TestCase):
    def test_rate_limit_stops_reading_with_one_clear_warning(self):
        files = {"CONTRIBUTING.md": b"a", "AGENTS.md": b"b", "CODE_OF_CONDUCT.md": b"c"}
        fake = FakeGithub(files=files, behaviors={n: RateLimitedError("rate limited, set GITHUB_TOKEN") for n in files})
        s = fetch_snapshot("a/b", client=fake)
        self.assertFalse(s.complete)
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("rate limited, set GITHUB_TOKEN", s.warnings[0])
        self.assertEqual(fake.raw_calls, 1)


class LocalDirectoryTests(unittest.TestCase):
    def make(self, files, head=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, text in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if head:
            (root / ".git").mkdir()
            for name, text in head.items():
                (root / ".git" / name).parent.mkdir(parents=True, exist_ok=True)
                (root / ".git" / name).write_text(text, encoding="utf-8")
        return root

    def test_reads_candidates_only(self):
        root = self.make({"AGENTS.md": "AI agents must follow this.", "src/x.md": "AI", "docs/CONTRIBUTING.md": "hi",
                          "docs/notes.md": "not a candidate"})
        s = read_local_directory(root, "a/b")
        self.assertEqual(sorted(s.documents), ["AGENTS.md", "docs/CONTRIBUTING.md"])
        self.assertTrue(s.complete)
        self.assertIsNone(s.commit_sha)

    def test_head_sha_and_branch_without_running_git(self):
        root = self.make({"CONTRIBUTING.md": "x"},
                         head={"HEAD": "ref: refs/heads/main\n", "refs/heads/main": SHA + "\n"})
        s = read_local_directory(root, "a/b")
        self.assertEqual((s.ref, s.commit_sha), ("main", SHA))

    def test_packed_refs(self):
        root = self.make({"CONTRIBUTING.md": "x"},
                         head={"HEAD": "ref: refs/heads/dev\n", "packed-refs": f"# pack\n{SHA} refs/heads/dev\n"})
        self.assertEqual(read_local_directory(root, "a/b").commit_sha, SHA)

    def test_not_a_directory(self):
        with self.assertRaises(FetchError):
            read_local_directory("this/does/not/exist", "a/b")

    def test_unreadable_encoding_is_error(self):
        root = self.make({})
        (root / "CONTRIBUTING.md").write_bytes(b"\xff\xfe\x00bad")
        s = read_local_directory(root, "a/b")
        self.assertEqual(s.files_checked, [{"path": "CONTRIBUTING.md", "status": "error"}])
        self.assertFalse(s.complete)


if __name__ == "__main__":
    unittest.main()


class PolicyNamedDiscoveryTests(unittest.TestCase):
    """Offline fixture shaped like LadybirdBrowser/ladybird: the AI clause sits in Documentation/CodePolicy.md,
    CONTRIBUTING.md only links the Documentation/ tree and the README never mentions AI."""

    FILES = {
        "CONTRIBUTING.md": b"Read [the documentation](https://github.com/a/b/tree/master/Documentation).\n\n"
                           b"## Issue policy\n\nFollow the [issue guidelines](ISSUES.md).\n",
        "README.md": b"A browser.\n",
        "Documentation/CodePolicy.md": b"# Code policy\n\n## On usage of AI and LLMs\n\n"
                                       b"Do not use an AI or LLM to generate changes without review.\n",
        "Documentation/CodingStyle.md": b"Use four spaces.\n",
        "Documentation/PrivacyPolicy.md": b"We keep nothing.\n",
        "Documentation/FAQ.md": b"Questions.\n",
        "Libraries/x/CodePolicy.md": b"vendored\n",
    }

    def test_policy_named_doc_is_read_and_request_count_stays_small(self):
        gh = FakeGithub(files=dict(self.FILES))
        s = fetch_snapshot("a/b", client=gh, org=False)
        self.assertIn("Documentation/CodePolicy.md", s.documents)
        self.assertIn("AI or LLM", s.documents["Documentation/CodePolicy.md"])
        self.assertEqual(sorted(p for _, _, p in gh.raw_requests),
                         ["CONTRIBUTING.md", "Documentation/CodePolicy.md", "README.md"])
        self.assertEqual(gh.calls, 3)  # default branch, commit, one recursive tree
