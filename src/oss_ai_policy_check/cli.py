"""Command line: oss-ai-policy-check OWNER/REPO [--ref REF] [--format F] [--path DIR] [--fail-on V]
[--no-cache] [--no-org].

Public API
    build_parser() -> argparse.ArgumentParser
    analyze(repo, ref=None, path=None, client=None, *, no_cache=False, org=True) -> Report
    main(argv=None) -> int            # the exit code (table in docs/schema.md)
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from . import __version__
from .classify import classify_documents
from .contract import (EXIT_USAGE, FAIL_ON_CHOICES, FORMATS, Report, build_report, exit_code)
from .fetch import FetchError, check_repo, fetch_snapshot, read_local_directory
from .report import render


def _repo_arg(value: str) -> str:
    try:
        return check_repo(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oss-ai-policy-check",
        description="Read a repository's AI contribution policy and cite the exact lines, "
                    "pinned to a commit SHA. Read-only; no repository code runs.")
    parser.add_argument("repo", type=_repo_arg, metavar="OWNER/REPO")
    parser.add_argument("--ref", help="branch, tag or commit SHA (default: the default branch)")
    parser.add_argument("--format", choices=FORMATS, default="text", dest="fmt")
    parser.add_argument("--path", metavar="LOCAL_DIR",
                        help="read a local clone of OWNER/REPO instead of GitHub")
    parser.add_argument("--fail-on", choices=FAIL_ON_CHOICES, dest="fail_on",
                        help="exit non-zero only when the verdict is at least this strict (needs-review also fails on an abstention)")
    parser.add_argument("--no-cache", action="store_true", dest="no_cache",
                        help="do not read or write the on-disk cache of fetched files")
    parser.add_argument("--no-org", action="store_true", dest="no_org",
                        help="do not read the owner's organization .github repository")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def analyze(repo: str, ref: str | None = None, path: str | None = None, client: Any = None, *,
            no_cache: bool = False, org: bool = True) -> Report:
    """Fetch (or read locally), classify and build the public report.

    `no_cache` and `org` only affect a GitHub fetch; a local `--path` read has neither."""
    snapshot = (read_local_directory(path, repo) if path
                else fetch_snapshot(repo, ref, client, no_cache=no_cache, org=org))
    return build_report(snapshot, classify_documents(snapshot.documents, owner=repo.split("/")[0]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.path and args.ref:
        parser.error("--ref cannot be combined with --path")
    try:
        report = analyze(args.repo, args.ref, args.path, no_cache=args.no_cache, org=not args.no_org)
        output = render(report, args.fmt)
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(output)
    return exit_code(report["verdict"], report["complete"], args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
