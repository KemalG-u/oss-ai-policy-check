"""Command line: oss-ai-policy-check OWNER/REPO [--ref REF] [--format F] [--path DIR] [--fail-on V]
[--no-cache] [--no-org] [--classifier rules|claude] [--model ID].

Public API
    build_parser() -> argparse.ArgumentParser
    analyze(repo, ref=None, path=None, client=None, *, no_cache=False, org=True, llm=None) -> Report
    report_from_snapshot(snapshot, repo, llm=None) -> (Report, ClaudeResult | None)
    main(argv=None) -> int            # the exit code (table in docs/schema.md)
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from . import __version__
from .classify import classify_documents
from .contract import (CLASSIFIER_CLAUDE, CLASSIFIER_RULES, CLASSIFIERS, EXIT_USAGE, FAIL_ON_CHOICES, FORMATS,
                       Report, Snapshot, build_report, exit_code)
from .fetch import FetchError, check_repo, fetch_snapshot, read_local_directory
from .llm import DEFAULT_MODEL, ClaudeClassifier, ClaudeResult, ClaudeUnavailable, estimate_cost
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
    parser.add_argument("--classifier", choices=CLASSIFIERS, default=CLASSIFIER_RULES,
                        help="rules (default, deterministic, no key) or claude (optional: Claude judges the "
                             "AI-policy paragraphs; needs the [claude] extra and credentials)")
    parser.add_argument("--model", metavar="ID", help=f"Claude model id for --classifier claude (default: {DEFAULT_MODEL})")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def report_from_snapshot(snapshot: Snapshot, repo: str,
                         llm: ClaudeClassifier | None = None) -> tuple[Report, ClaudeResult | None]:
    """Classify a fetched snapshot and build the public report. With `llm`, Claude picks the verdict and the ids of
    the deciding paragraphs; the deterministic result is the fallback when the model call fails."""
    rules = classify_documents(snapshot.documents, owner=repo.split("/")[0])
    result = None
    if llm is not None:
        result = llm.classify(snapshot.documents, rules, repo.split("/")[0])
        snapshot.warnings.extend(result.warnings)
        if result.failed or result.partial:
            snapshot.complete = False
    return build_report(snapshot, result.classification if result else rules), result


def analyze(repo: str, ref: str | None = None, path: str | None = None, client: Any = None, *,
            no_cache: bool = False, org: bool = True, llm: ClaudeClassifier | None = None) -> Report:
    """Fetch (or read locally), classify and build the public report.

    `no_cache` and `org` only affect a GitHub fetch; a local `--path` read has neither. `llm` selects the
    optional Claude classifier (see llm.py); without it the deterministic rules decide."""
    snapshot = (read_local_directory(path, repo) if path
                else fetch_snapshot(repo, ref, client, no_cache=no_cache, org=org))
    return report_from_snapshot(snapshot, repo, llm)[0]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.path and args.ref:
        parser.error("--ref cannot be combined with --path")
    if args.model and args.classifier != CLASSIFIER_CLAUDE:
        parser.error("--model needs --classifier claude")
    llm = None
    try:
        if args.classifier == CLASSIFIER_CLAUDE:  # before any fetch: a missing SDK should cost nothing
            llm = ClaudeClassifier.create(args.model or DEFAULT_MODEL)
        report = analyze(args.repo, args.ref, args.path, no_cache=args.no_cache, org=not args.no_org, llm=llm)
        output = render(report, args.fmt)
    except (FetchError, ClaudeUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if llm is not None and llm.usage.calls:
        cost = estimate_cost(llm.usage.input_tokens, llm.usage.output_tokens, llm.model)
        print(f"claude: {llm.usage.input_tokens} input / {llm.usage.output_tokens} output tokens"
              + (f", about ${cost:.4f} at list price" if cost is not None else ""), file=sys.stderr)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(output)
    return exit_code(report["verdict"], report["complete"], args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
