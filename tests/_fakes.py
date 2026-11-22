"""Offline stand-in for the GitHub transport (the `client` protocol: .api(endpoint), .calls).

Like the real transport it can also serve raw file bytes (`raw(repo, sha, path)`, not counted in
`calls`); pass `use_raw=False` to model a client without it (files then come from /contents/).
`org_files` is the content of `a/.github`; `None` means that repository does not exist.
`oversize=True` makes the recursive root tree reply of `a/b` exceed the transport read cap (TooLargeError);
`oversize="all"` does so for every recursive tree reply of `a/b`.
"""
import base64

import _support  # noqa: F401
from oss_ai_policy_check.fetch import FetchError, NotFoundError, TooLargeError

SHA = "0123456789abcdef0123456789abcdef01234567"
ORG_SHA = "fedcba9876543210fedcba9876543210fedcba98"
DEFAULT_FILES = {".github/AI_POLICY.md": b"All AI usage must be disclosed."}


def _children(files, directory):
    """Non-recursive tree entries of `directory` ('' for the root)."""
    prefix = directory + "/" if directory else ""
    seen, entries = set(), []
    for path in sorted(files):
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        head, _, tail = rest.partition("/")
        if tail:
            if head not in seen:
                seen.add(head)
                entries.append({"path": head, "type": "tree", "sha": "T:" + prefix + head})
        else:
            entries.append({"path": head, "type": "blob", "sha": "B:" + path})
    return entries


def _descendants(files, directory):
    prefix = directory + "/" if directory else ""
    return [{"path": p[len(prefix):], "type": "blob"} for p in sorted(files) if p.startswith(prefix)]


class FakeGithub:
    def __init__(self, fail=False, truncated=False, files=None, behaviors=None, use_raw=True,
                 org_files=None, org_error=None, oversize=False):
        self.calls = 0       # API requests only
        self.raw_calls = 0   # raw file requests (no rate-limit cost)
        self.fail, self.truncated, self.oversize = fail, truncated, oversize
        self.files = files if files is not None else dict(DEFAULT_FILES)
        self.org_files = org_files
        self.org_error = org_error
        self.behaviors = behaviors or {}  # path -> exception instance to raise on read
        self.endpoints = []
        self.raw_requests = []
        if use_raw:
            self.raw = self._raw

    def _store(self, repo):
        if repo == "a/b":
            return SHA, self.files
        if repo == "a/.github" and self.org_files is not None:
            return ORG_SHA, self.org_files
        return None, None

    def _raw(self, repo, sha, path):
        self.raw_calls += 1
        self.raw_requests.append((repo, sha, path))
        if self.fail:
            raise FetchError("HTTP 403")
        if path in self.behaviors:
            raise self.behaviors[path]
        store_sha, files = self._store(repo)
        if files is None or sha != store_sha or path not in files:
            raise NotFoundError("HTTP 404")
        return files[path]

    def api(self, endpoint):
        self.calls += 1
        self.endpoints.append(endpoint)
        if self.fail:
            raise FetchError("HTTP 403")
        if endpoint == "repos/a/b":
            return {"default_branch": "main"}
        parts = endpoint.split("?", 1)[0].split("/")
        repo = "/".join(parts[1:3])
        sha, files = self._store(repo)
        if "/commits/" in endpoint:
            if repo == "a/.github" and self.org_error is not None:
                raise self.org_error
            if files is None:
                raise NotFoundError("HTTP 404")
            return {"sha": sha}
        if "/git/trees/" in endpoint:
            tree_id = endpoint.split("/git/trees/", 1)[1].split("?", 1)[0]
            recursive = endpoint.endswith("?recursive=1")
            if tree_id != sha and not tree_id.startswith("T:"):
                raise NotFoundError("HTTP 404")
            directory = "" if tree_id == sha else tree_id[2:]
            if recursive:
                if repo == "a/b" and (self.oversize == "all" or (self.oversize and directory == "")):
                    raise TooLargeError("API reply over the read limit")
                if directory == "" and self.truncated:
                    return {"truncated": True, "tree": _descendants(files, "")[:1]}
                return {"truncated": self.truncated and directory != "", "tree": _descendants(files, directory)}
            return {"tree": _children(files, directory)}
        path = endpoint.split("/contents/", 1)[1].split("?", 1)[0]
        if path in self.behaviors:
            raise self.behaviors[path]
        if files is None or path not in files:
            raise NotFoundError("HTTP 404")
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(files[path]).decode()}
