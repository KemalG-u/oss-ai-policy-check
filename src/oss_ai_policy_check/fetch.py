"""Transport: get policy candidate files from GitHub (REST, raw, or the gh CLI) or from a local clone.

Public API
    fetch_snapshot(repo, ref=None, client=None, *, no_cache=False, cache_dir=None, org=True) -> Snapshot
    read_local_directory(root, repo, ref=None) -> Snapshot
    check_repo(repo) -> str                         # ValueError when not OWNER/REPO
    default_client() -> AutoTransport               # token REST, else anonymous REST, else gh fallback
    default_cache_dir() -> Path
    RestTransport(token=None).api(endpoint) / .raw(repo, sha, path)    # stdlib urllib only
    AutoTransport(token=None)                       # REST with automatic gh fallback
    GhCliTransport().api(endpoint) -> object        # the `client` protocol: .api(endpoint), .calls
    FetchError, NotFoundError, RateLimitedError

A `client` is any object with `api(endpoint) -> parsed JSON` and an integer `calls` attribute
(GitHub API requests only). It may also offer `raw(repo, sha, path) -> bytes`, which reads a file
from raw.githubusercontent.com (not counted against the API rate limit); without it file bytes come
from the contents API. Tests pass a fake. Everything is read-only: GET only, no repository code runs.

Request budget per repository: default branch (1, skipped with --ref), commit SHA (1, skipped when
--ref is a full SHA), one recursive tree (1), then one raw request per file. A cached SHA costs no
tree and no raw request. When the recursive tree is truncated by GitHub or larger than the read cap
(llvm, rust, firefox, nixpkgs), it is replaced by the root tree plus the .github and docs subtrees
(one call each, so 3 to 5 instead of 1; a root `policies/`, `policy/` or `contributing/` directory adds one
more call each). AI-named files and AI-context links beyond the name-based candidates cost raw reads only,
at most EXTRA_READ_LIMIT (8) per repository; skipped ones are named in a warning. The owner's `.github`
repository adds at most two API calls
(commit, recursive tree), and only when no repository file carries an AI policy statement.

Credentials: GITHUB_TOKEN, then GH_TOKEN, else anonymous (60 API requests per hour). The token is
only sent to api.github.com and raw.githubusercontent.com and never appears in any message.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlparse

from . import __version__
from .contract import (FILE_ERROR, FILE_MISSING, FILE_READ, MAX_FILE_BYTES, MAX_POLICY_FILES, Snapshot)
from .discover import (DOC_DIRS, EXTRA_READ_LIMIT, MONOREPO_DOC_DIRS, ORG_PREFIX, POLICY_TREE_DIRS, family,
                       has_ai_policy_text, in_scope, is_candidate, is_core_candidate, linked_paths,
                       org_policy_sources, split_candidates)

REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
ALLOWED_PREFIXES = ("repos/", "rate_limit")
API_HOST, RAW_HOST = "api.github.com", "raw.githubusercontent.com"
MAX_API_BYTES = 16_000_000
TRUSTED_HOSTS = (API_HOST, RAW_HOST)


class FetchError(Exception):
    """A read failed. The message is safe to show: never raw gh output, tokens or local paths."""


class NotFoundError(FetchError):
    """The file or object does not exist (HTTP 404, or absent in a local clone)."""


class RateLimitedError(FetchError):
    """HTTP 403/429 caused by a rate limit, not by a missing permission."""


class TooLargeError(FetchError):
    """The reply is larger than the read cap, so it cannot be parsed (a huge recursive tree)."""


class NetworkError(FetchError):
    """The server could not be reached (DNS, timeout, TLS)."""


def check_repo(repo: str) -> str:
    if not REPO_PATTERN.fullmatch(repo or "") or any(part in (".", "..") for part in repo.split("/")):
        raise ValueError("owner/repo required")
    return repo


def github_token(env: Mapping[str, str] | None = None) -> str | None:
    """GITHUB_TOKEN, then GH_TOKEN, else None. Blank values count as unset."""
    env = os.environ if env is None else env
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


def _rate_limit_message(headers: Mapping[str, str], has_token: bool, now: float | None = None) -> str:
    """Human message for a rate-limited reply; headers are lower-case keys."""
    now = time.time() if now is None else now
    reset = None
    try:
        if "retry-after" in headers:
            reset = now + int(headers["retry-after"])
        elif "x-ratelimit-reset" in headers:
            reset = int(headers["x-ratelimit-reset"])
    except ValueError:
        reset = None
    when = ""
    if reset is not None:
        minutes = max(1, round((reset - now) / 60))
        stamp = datetime.fromtimestamp(reset, timezone.utc).strftime("%H:%M UTC")
        when = f" (resets in {minutes} min, at {stamp})"
    return ("rate limited" if has_token else "rate limited, set GITHUB_TOKEN") + when


class _TrustedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only between GitHub's own https hosts, so the token cannot leak."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS:
            raise FetchError("redirect to an untrusted host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RestTransport:
    """GET-only GitHub REST + raw transport on urllib. `token` is optional (anonymous: 60/hour)."""

    def __init__(self, token: str | None = None, opener: Any = None, timeout: float = 30.0, delay: float = 0.0):
        self._token = token or None
        self._opener = opener or urllib.request.build_opener(_TrustedRedirects)
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self.calls = 0       # API requests (these count against the rate limit)
        self.raw_calls = 0   # raw.githubusercontent.com requests (these do not)
        self.cache: dict[str, Any] = {}
        self.limits: dict[str, str] = {}
        self.last_call = 0.0

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def _get(self, url: str, accept: str) -> tuple[bytes, Mapping[str, str]]:
        host = urlparse(url).hostname
        headers = {"Accept": accept, "User-Agent": f"oss-ai-policy-check/{__version__}"}
        if host == API_HOST:
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        if self._token:
            headers["Authorization"] = "Bearer " + self._token  # only ever sent to TRUSTED_HOSTS
        wait = self.delay - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            with self._opener.open(urllib.request.Request(url, headers=headers, method="GET"),
                                   timeout=self.timeout) as response:
                body = response.read(MAX_FILE_BYTES + 1 if host == RAW_HOST else MAX_API_BYTES + 1)
                reply = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            reply = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
            self.last_call = time.monotonic()
            self._raise_for_status(exc.code, reply)
            raise  # unreachable: _raise_for_status always raises
        except urllib.error.URLError as exc:
            raise NetworkError("network error: " + type(exc.reason).__name__) from None
        except (OSError, ValueError) as exc:
            raise NetworkError("network error: " + type(exc).__name__) from None
        self.last_call = time.monotonic()
        if host == API_HOST and len(body) > MAX_API_BYTES:
            raise TooLargeError("API reply over the read limit")
        if "x-ratelimit-remaining" in reply:
            self.limits = {k: reply[k] for k in ("x-ratelimit-remaining", "x-ratelimit-reset") if k in reply}
        return body, reply

    def _raise_for_status(self, status: int, reply: Mapping[str, str]) -> None:
        if status == 404:
            raise NotFoundError("HTTP 404")
        limited = status == 429 or (status == 403 and (reply.get("x-ratelimit-remaining") == "0"
                                                       or "retry-after" in reply))
        if limited:
            raise RateLimitedError(_rate_limit_message(reply, self.has_token))
        raise FetchError(f"HTTP {status}")

    def api(self, endpoint: str) -> Any:
        if endpoint in self.cache:
            return self.cache[endpoint]
        if endpoint.startswith(("http:", "https:")) or not endpoint.startswith(ALLOWED_PREFIXES):
            raise FetchError("endpoint outside read-only allowlist")
        self.calls += 1
        body, _ = self._get(f"https://{API_HOST}/{endpoint}", "application/vnd.github+json")
        try:
            result = json.loads(body)
        except ValueError as exc:
            raise FetchError("invalid API JSON") from exc
        self.cache[endpoint] = result
        return result

    def raw(self, repo: str, sha: str, path: str) -> bytes:
        """File bytes at a pinned commit from raw.githubusercontent.com; not an API call."""
        check_repo(repo)
        if not SHA_PATTERN.fullmatch(sha or ""):
            raise FetchError("raw reads need a full commit sha")
        self.raw_calls += 1
        body, _ = self._get(f"https://{RAW_HOST}/{repo}/{sha}/{quote(path, safe='/')}", "text/plain")
        if len(body) > MAX_FILE_BYTES:
            raise FetchError("oversize policy file")
        return body


class GhCliTransport:
    """Only GET, argument-list invocation, pacing, in-memory cache; credentials never logged."""

    def __init__(self, delay: float = 0.35):
        self.delay = max(0.0, delay)
        self.calls = 0
        self.cache: dict[str, Any] = {}
        self.last_call = 0.0
        self.limits: dict[str, dict[str, str]] = {}

    def api(self, endpoint: str) -> Any:
        if endpoint in self.cache:
            return self.cache[endpoint]
        if endpoint.startswith(("http:", "https:")) or not endpoint.startswith(ALLOWED_PREFIXES):
            raise FetchError("endpoint outside read-only allowlist")
        wait = self.delay - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        self.calls += 1
        try:
            proc = subprocess.run(["gh", "api", "--include", "--method", "GET", endpoint],
                                  capture_output=True, encoding="utf-8", timeout=90, check=False)
        except FileNotFoundError as exc:
            raise FetchError("gh CLI not found on PATH") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FetchError(type(exc).__name__) from exc
        self.last_call = time.monotonic()
        header, sep, body = proc.stdout.replace("\r\n", "\n").partition("\n\n")
        if not sep:
            raise FetchError("missing API headers")
        headers = {}
        for line in header.splitlines()[1:]:
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.lower()] = val.strip()
        resource = headers.get("x-ratelimit-resource", "unknown")
        self.limits[resource] = {k: headers[k] for k in ("x-ratelimit-remaining", "x-ratelimit-reset", "retry-after") if k in headers}
        if proc.returncode:
            status = re.search(r"HTTP/\S+\s+(\d+)", header)
            # Do not print raw stdout/stderr or silently retry a rate-limited request.
            detail = "HTTP " + status.group(1) if status else "API failure"
            if "retry-after" in headers:
                detail += "; retry-after=" + headers["retry-after"]
            if headers.get("x-ratelimit-remaining") == "0":
                detail += "; reset=" + headers.get("x-ratelimit-reset", "unknown")
            raise (NotFoundError if status and status.group(1) == "404" else FetchError)(detail)
        if len(body) > MAX_API_BYTES:
            raise TooLargeError("API reply over the read limit")
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise FetchError("invalid API JSON") from exc
        self.cache[endpoint] = result
        return result


class AutoTransport:
    """Selection order: REST with token, anonymous REST, then the gh CLI as a backup.

    The gh backup is only used for API calls, and only after REST was rate limited or unreachable;
    raw file reads always go through REST (they do not count against the API limit)."""

    def __init__(self, token: str | None = None, rest: RestTransport | None = None,
                 gh: Any = None, which: Callable[[str], str | None] = shutil.which):
        self.rest = rest or RestTransport(token)
        self._gh = gh
        self._which = which
        self.use_gh = False

    @property
    def mode(self) -> str:
        return "gh" if self.use_gh else ("rest+token" if self.rest.has_token else "rest-anonymous")

    @property
    def calls(self) -> int:
        return self.rest.calls + (self._gh.calls if self._gh else 0)

    @property
    def raw_calls(self) -> int:
        return self.rest.raw_calls

    def api(self, endpoint: str) -> Any:
        failure: FetchError | None = None
        if not self.use_gh:
            try:
                return self.rest.api(endpoint)
            except (RateLimitedError, NetworkError) as exc:
                failure = exc
            if self._gh is None and not self._which("gh"):
                raise failure
            self.use_gh = True
        if self._gh is None:
            self._gh = GhCliTransport()
        try:
            return self._gh.api(endpoint)
        except NotFoundError:
            raise
        except FetchError:
            if failure is None:
                raise
            self.use_gh = False  # gh did not help; keep the more useful REST message
            raise failure from None

    def raw(self, repo: str, sha: str, path: str) -> bytes:
        return self.rest.raw(repo, sha, path)


def default_client() -> AutoTransport:
    return AutoTransport(github_token())


# ---------------------------------------------------------------------------------- cache

def default_cache_dir() -> Path:
    """~/.cache/oss-ai-policy-check; on Windows %LOCALAPPDATA%/oss-ai-policy-check/cache."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "oss-ai-policy-check" / "cache"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "oss-ai-policy-check"


class _Cache:
    """SHA-keyed, best-effort disk cache. A commit SHA never changes, so entries never expire.
    Any I/O or format problem is a cache miss, never an error. Disabled when `root` is None."""

    def __init__(self, root: Path | None):
        self.root = root
        self.hits = 0

    def _dir(self, repo: str, sha: str) -> Path | None:
        if self.root is None or not SHA_PATTERN.fullmatch(sha or ""):
            return None
        return self.root / repo.lower().replace("/", "__") / sha

    @staticmethod
    def _write(target: Path, data: bytes) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError:
            pass

    def load_tree(self, repo: str, sha: str) -> tuple[set[str], list[str]] | None:
        folder = self._dir(repo, sha)
        if folder is None:
            return None
        try:
            data = json.loads((folder / "tree.json").read_text(encoding="utf-8"))
            if data["v"] == 1 and all(isinstance(p, str) for p in data["paths"]) \
                    and all(isinstance(w, str) for w in data["warnings"]):
                self.hits += 1
                return set(data["paths"]), list(data["warnings"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def save_tree(self, repo: str, sha: str, paths: set[str], warnings: list[str]) -> None:
        folder = self._dir(repo, sha)
        if folder is not None:
            payload = {"v": 1, "paths": sorted(paths), "warnings": warnings}
            self._write(folder / "tree.json", json.dumps(payload).encode("utf-8"))

    @staticmethod
    def _file_name(path: str) -> str:
        return "f-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:40] + ".bin"

    def load_file(self, repo: str, sha: str, path: str) -> bytes | None:
        folder = self._dir(repo, sha)
        if folder is None:
            return None
        try:
            stored = (folder / self._file_name(path)).read_bytes()
        except OSError:
            return None
        digest, _, data = stored.partition(b"\n")
        if len(data) > MAX_FILE_BYTES or hashlib.sha256(data).hexdigest().encode() != digest:
            return None  # truncated or damaged entry: treat as a miss
        self.hits += 1
        return data

    def save_file(self, repo: str, sha: str, path: str, data: bytes) -> None:
        folder = self._dir(repo, sha)
        if folder is not None and len(data) <= MAX_FILE_BYTES:
            self._write(folder / self._file_name(path), hashlib.sha256(data).hexdigest().encode() + b"\n" + data)


# ---------------------------------------------------------------------------------- remote

def _collect_documents(paths: set[str], repo: str, read: Callable[[str], str],
                       selected: list[str] | None = None, prefix: str = ""):
    """Read candidate files (plus same-repo policy links, one level) through `read(path)`.

    `paths` is every known file of the repository (the universe links resolve against);
    `selected` defaults to the in-scope candidates among them plus at most EXTRA_READ_LIMIT AI-named
    files (best match first; the rest are named in a warning). AI-context links to files that are not
    name-based candidates draw on the same allowance. `prefix` labels the results (documents,
    files_checked, warnings) for files that come from another repository.
    Returns (documents, files_checked, warnings)."""
    documents: dict[str, str] = {}
    checked: list[dict[str, str]] = []
    warnings: list[str] = []
    extra_used = 0
    if selected is None:
        selected, skipped = split_candidates(paths)
        extra_used = sum(1 for p in selected if not (in_scope(p) and is_core_candidate(p)))
        if skipped:
            shown = ", ".join(skipped[:5]) + (f" and {len(skipped) - 5} more" if len(skipped) > 5 else "")
            warnings.append(f"AI-named files not read (limit {EXTRA_READ_LIMIT}): {shown}")
    else:
        selected = list(selected)
    if len(selected) > MAX_POLICY_FILES:
        warnings.append(f"policy file limit {MAX_POLICY_FILES} reached")
        selected = selected[:MAX_POLICY_FILES]
    linked_added: set[str] = set()
    for path in selected:  # grows while iterating: linked files are appended
        label = prefix + path
        try:
            documents[label] = read(path)
        except NotFoundError:
            checked.append({"path": label, "status": FILE_MISSING})
            continue
        except RateLimitedError as exc:  # every further read would fail the same way
            checked.append({"path": label, "status": FILE_ERROR})
            warnings.append(f"read failed: {label} ({exc})")
            break
        except (FetchError, UnicodeError, ValueError) as exc:
            checked.append({"path": label, "status": FILE_ERROR})
            warnings.append(f"read failed: {label} ({exc})")
            continue
        checked.append({"path": label, "status": FILE_READ})
        if path not in linked_added:
            for linked in linked_paths(path, documents[label], paths, repo,
                                       ai_links=is_core_candidate(path)):
                if linked in selected or len(selected) >= MAX_POLICY_FILES:
                    continue
                if not is_core_candidate(linked):
                    if extra_used >= EXTRA_READ_LIMIT:
                        warnings.append(f"AI-context link not read (limit {EXTRA_READ_LIMIT}): {linked}")
                        continue
                    extra_used += 1
                selected.append(linked)
                linked_added.add(linked)
    return documents, checked, warnings


def _repo_paths(gh: Any, repo: str, sha: str, cache: _Cache) -> tuple[set[str], list[str]]:
    """Known candidate-relevant file paths of `repo` at `sha`: in-scope files plus candidate-named
    files anywhere (the latter only so policy links can resolve). One recursive tree call. A tree that
    GitHub marks truncated, or whose reply is over the read cap (llvm, rust, firefox, nixpkgs), is
    never parsed blindly: policy files live in the root, `.github` and docs trees, so the fallback
    reads those (root tree plus one recursive call per docs directory)."""
    cached = cache.load_tree(repo, sha)
    if cached is not None:
        return cached
    warnings: list[str] = []
    try:
        tree = gh.api(f"repos/{repo}/git/trees/{sha}?recursive=1")
    except TooLargeError:
        tree = None
    if tree is not None and not tree.get("truncated"):
        paths = {i["path"] for i in tree["tree"]
                 if i["type"] == "blob" and (in_scope(i["path"]) or is_candidate(i["path"]))}
    else:
        paths, warnings = _paths_from_subtrees(gh, repo, sha)
    cache.save_tree(repo, sha, paths, warnings)
    return paths, warnings


def _paths_from_subtrees(gh: Any, repo: str, sha: str) -> tuple[set[str], list[str]]:
    warnings: list[str] = []
    paths: set[str] = set()
    root = gh.api(f"repos/{repo}/git/trees/{sha}")
    if root.get("truncated"):
        warnings.append("truncated root tree")
    paths.update(i["path"] for i in root["tree"] if i["type"] == "blob")
    dirs = [(i["path"], i["sha"]) for i in root["tree"]
            if i["type"] == "tree" and i["path"].lower() in DOC_DIRS | POLICY_TREE_DIRS]
    for top, subdirs in MONOREPO_DOC_DIRS.items():
        entry = next((i for i in root["tree"] if i["path"] == top and i["type"] == "tree"), None)
        if entry:
            subtree = gh.api(f"repos/{repo}/git/trees/{entry['sha']}")
            dirs += [(top + "/" + i["path"], i["sha"]) for i in subtree["tree"]
                     if i["type"] == "tree" and i["path"] in subdirs]
    for prefix, tree_sha in dirs:
        try:
            listing = gh.api(f"repos/{repo}/git/trees/{tree_sha}?recursive=1")
        except TooLargeError:
            warnings.append(f"truncated tree: {prefix}")
            continue
        if listing.get("truncated"):
            warnings.append(f"truncated tree: {prefix}")
        paths.update(prefix + "/" + i["path"] for i in listing["tree"] if i["type"] == "blob")
    return paths, warnings


def _make_reader(gh: Any, repo: str, sha: str, cache: _Cache) -> Callable[[str], str]:
    raw = getattr(gh, "raw", None)

    def read(path: str) -> str:
        data = cache.load_file(repo, sha, path)
        if data is None:
            if raw is not None:
                data = raw(repo, sha, path)
            else:
                item = gh.api(f"repos/{repo}/contents/{quote(path, safe='/')}?ref={sha}")
                if item.get("type") != "file" or item.get("encoding") != "base64":
                    raise FetchError("unreadable content")
                if item.get("size", 0) > MAX_FILE_BYTES:
                    raise FetchError("oversize policy file")
                data = base64.b64decode(item["content"])
            if len(data) > MAX_FILE_BYTES:
                raise FetchError("oversize policy file")
            cache.save_file(repo, sha, path, data)
        return data.decode("utf-8-sig")

    return read


def _org_snapshot(gh: Any, owner: str, repo_paths: set[str], cache: _Cache):
    """Default community files from `OWNER/.github` that this repository does not override.
    Returns (documents, files_checked, warnings, org_sha); empty with `org_sha=None` when the org
    repository is absent. `org_sha` is set only when org files were actually read."""
    org_repo = f"{owner}/.github"
    try:
        sha = gh.api(f"repos/{org_repo}/commits/HEAD")["sha"]
    except NotFoundError:
        return {}, [], [], None
    except FetchError as exc:
        if str(exc) == "HTTP 409":  # empty repository
            return {}, [], [], None
        return {}, [], [f"organization .github lookup failed ({exc})"], None
    try:
        org_paths, warnings = _repo_paths(gh, org_repo, sha, cache)
        chosen = org_policy_sources(owner, org_paths, repo_paths)
        if not chosen:
            return {}, [], warnings, None
        docs, checked, more = _collect_documents(org_paths, org_repo, _make_reader(gh, org_repo, sha, cache),
                                                 selected=chosen, prefix=ORG_PREFIX)
        return docs, checked, warnings + more, (sha if checked else None)
    except (FetchError, KeyError, TypeError) as exc:
        return {}, [], [f"organization .github lookup failed ({exc})"], None


def fetch_snapshot(repo: str, ref: str | None = None, client: Any = None, *,
                   no_cache: bool = False, cache_dir: Path | str | None = None, org: bool = True) -> Snapshot:
    """Read candidate policy files of `repo` at `ref` (default branch when None), pinned to a SHA.

    `no_cache=True` skips the disk cache (the CLI `--no-cache` flag). The cache is on by default
    for the built-in transport; with an injected `client` it is on only when `cache_dir` is given.
    `org=False` skips the owner's organisation `.github` repository."""
    check_repo(repo)
    gh = client or default_client()
    if no_cache or (client is not None and cache_dir is None):
        cache = _Cache(None)
    else:
        cache = _Cache(Path(cache_dir) if cache_dir else default_cache_dir())
    warnings: list[str] = []
    documents: dict[str, str] = {}
    checked: list[dict[str, str]] = []
    sha: str | None = None
    org_sha: str | None = None
    try:
        if ref is None:
            ref = gh.api(f"repos/{repo}")["default_branch"]
        sha = ref.lower() if SHA_PATTERN.fullmatch(ref.lower()) else gh.api(
            f"repos/{repo}/commits/{quote(ref, safe='')}")["sha"]
        paths, tree_warnings = _repo_paths(gh, repo, sha, cache)
        warnings += tree_warnings
        documents, checked, more = _collect_documents(paths, repo, _make_reader(gh, repo, sha, cache))
        warnings += more
        # Org defaults are read only when the repository itself states no AI policy (a file named like
        # an AI policy, or a paragraph with an AI term and a policy verb; a paragraph that merely links
        # to the owner's .github policy is a pointer, not a statement); the repository text wins.
        owner = repo.split("/")[0]
        has_policy = (any(family(p) == "ai_policy" for p in documents)
                      or any(has_ai_policy_text(t, owner) for t in documents.values()))
        if org and not has_policy and repo.split("/")[1].lower() != ".github":
            org_docs, org_checked, org_warnings, org_sha = _org_snapshot(gh, owner, {p for p in paths if in_scope(p)}, cache)
            documents.update(org_docs)
            checked += org_checked
            warnings += org_warnings
    except (FetchError, KeyError, TypeError) as exc:
        warnings.append(f"repository discovery failed ({exc})")
    return Snapshot(repo=repo, ref=ref, commit_sha=sha, documents=documents, files_checked=checked,  # type: ignore[arg-type]
                    warnings=warnings, complete=not warnings, api_calls=getattr(gh, "calls", 0),
                    org_commit_sha=org_sha)


# ---------------------------------------------------------------------------------- local

def _local_paths(root: Path) -> set[str]:
    """Relative POSIX paths of regular files in root and in the conventional docs directories."""
    paths: set[str] = set()
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        raise FetchError(type(exc).__name__) from exc

    def walk(directory: Path, prefix: str) -> None:
        for current, dirnames, filenames in os.walk(directory, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git" and not Path(current, d).is_symlink()]
            rel = Path(current).relative_to(directory).as_posix()
            base = prefix if rel == "." else f"{prefix}/{rel}"
            for name in filenames:
                if not Path(current, name).is_symlink():
                    paths.add(f"{base}/{name}")

    for entry in entries:
        if entry.is_symlink():
            continue
        if entry.is_file():
            paths.add(entry.name)
        elif entry.is_dir() and entry.name.lower() in DOC_DIRS | POLICY_TREE_DIRS:
            walk(entry, entry.name)
        elif entry.is_dir() and entry.name in MONOREPO_DOC_DIRS:
            for sub in MONOREPO_DOC_DIRS[entry.name]:
                target = entry / sub
                if target.is_dir() and not target.is_symlink():
                    walk(target, f"{entry.name}/{sub}")
    return paths


def _local_head(root: Path) -> tuple[str | None, str | None]:
    """(branch, commit sha) read straight from .git files; git itself is never run, so a hostile
    clone cannot execute hooks or configured commands."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if re.fullmatch(r"[0-9a-f]{40}", head):
        return None, head
    match = re.fullmatch(r"ref: (refs/heads/([^\s]+))", head)
    if not match or ".." in match.group(1).split("/"):
        return None, None
    ref, branch = match.groups()
    try:
        value = (git / ref).read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{40}", value):
            return branch, value
    except OSError:
        pass
    try:
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40}", parts[0]):
                return branch, parts[0]
    except OSError:
        pass
    return branch, None


def read_local_directory(root: str | Path, repo: str, ref: str | None = None) -> Snapshot:
    """Read candidate files from a local clone of `repo`. The commit SHA comes from .git/HEAD
    when present, so permalinks still work; otherwise evidence urls are null."""
    check_repo(repo)
    base = Path(root)
    if not base.is_dir():
        raise FetchError("not a directory")
    branch, sha = _local_head(base)
    paths = _local_paths(base)

    def read(path: str) -> str:
        target = base.joinpath(*path.split("/"))
        try:
            if target.is_symlink() or not target.is_file():
                raise NotFoundError("missing")
            if target.stat().st_size > MAX_FILE_BYTES:
                raise FetchError("oversize policy file")
            return target.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise FetchError(type(exc).__name__) from exc

    documents, checked, warnings = _collect_documents(paths, repo, read)
    return Snapshot(repo=repo, ref=ref or branch, commit_sha=sha, documents=documents,  # type: ignore[arg-type]
                    files_checked=checked, warnings=warnings, complete=not warnings, api_calls=0)
