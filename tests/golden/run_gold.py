"""Gold-set accuracy runner: real pipeline (fetch_snapshot at the pinned SHA -> classify -> build_report).

    python tests/golden/run_gold.py --split dev                 # tune on this
    python tests/golden/run_gold.py --split holdout --summary   # aggregate numbers only
    python tests/golden/run_gold.py --split all --offline       # reproduce from the cache, no network
    python tests/golden/run_gold.py --cv --offline              # development-set report (see below)
    python tests/golden/run_gold.py --fetch-only                # fill the cache, print nothing about labels

Labels are pinned to the commit SHAs in gold.jsonl (scan date 2026-11-22); the tool is always run at
that SHA, never at HEAD. For rows with source "org" the repository SHA is taken from the note
"(repo sha xxxxxxxxxx)" and the owner's `.github` repository is pinned to the SHA in the row (or to
org_pins.json for repositories whose own text decides the verdict).

Every GitHub response (API JSON and raw bytes, including 404s) is stored under `_cache/`, so a rerun
is offline and a discovery change only fetches files it newly asks for. GET only. GITHUB_TOKEN is
read from the environment by the tool's own transport; nothing here prints it.

Metrics (see metrics()): overall accuracy counts a needs-review verdict as wrong; decided-row accuracy and the
abstain rate treat it as a correct abstention; a "dangerous error" is a gold label with a policy that the tool
reports as no-policy-found.

--cv: hand-written rules cannot be cross-validated (there is nothing to fit per fold), and the former
holdout has been seen during tuning, so all 151 rows are one development set. --cv prints that set
(an optimistic figure), the 50 former-holdout rows separately, per-class precision and recall for both,
and the misses grouped by cause (rule, fetch-org, label-scope; see MISS_CAUSES). The honest number for
unseen repositories is the one measured on a fresh blind set, not these.

The split is deterministic: per verdict, repositories are ordered by sha256("oss-gold-v1:" + repo)
and the first round(2n/3) are `dev`, the rest `holdout`. Misses of the holdout split are printed
only with --holdout-misses, to keep tuning honest.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
# OSS_GOLD_SRC points at another checkout of src/ (used to measure the baseline of an older commit).
sys.path.insert(0, os.environ.get("OSS_GOLD_SRC") or str(HERE.parent.parent / "src"))

from oss_ai_policy_check.classify import classify_documents  # noqa: E402
from oss_ai_policy_check.contract import NEEDS_REVIEW, NO_POLICY_FOUND, VERDICTS, build_report  # noqa: E402
from oss_ai_policy_check.fetch import FetchError, NotFoundError, default_client, fetch_snapshot  # noqa: E402

SPLIT_SEED = "oss-gold-v1:"
GOLD = HERE / "gold.jsonl"
PINS = HERE / "org_pins.json"


def load_gold(path: Path = GOLD) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def split_gold(rows: list[dict]) -> dict[str, str]:
    """repo -> 'dev' | 'holdout', stratified by verdict, ordered by a seeded hash of the repo name."""
    by_verdict: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        by_verdict[row["verdict"]].append(row["repo"])
    out: dict[str, str] = {}
    for repos in by_verdict.values():
        repos.sort(key=lambda r: hashlib.sha256((SPLIT_SEED + r).encode()).hexdigest())
        cut = round(len(repos) * 2 / 3)
        for i, repo in enumerate(repos):
            out[repo] = "dev" if i < cut else "holdout"
    return out


def target_ref(row: dict) -> str:
    """The commit of the repository itself (for org rows the row SHA is the org repo's)."""
    if row["source"] == "org":
        found = re.search(r"\(repo sha ([0-9a-f]{7,40})\)", row["note"])
        if found:
            return found.group(1)
    return row["commit_sha"]


def org_pins(rows: list[dict]) -> dict[str, str]:
    pins = {k: v for k, v in json.loads(PINS.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    for row in rows:
        if row["source"] == "org":
            pins[row["repo"].split("/")[0]] = row["commit_sha"]
    return pins


class CachingClient:
    """Wraps the real transport: every response is stored on disk; org HEAD is pinned."""

    def __init__(self, cache: Path, pins: dict[str, str], offline: bool = False):
        self.cache, self.pins, self.offline = cache, pins, offline
        self.inner = None
        self.network = 0
        self.misses = 0

    @property
    def calls(self) -> int:
        return self.network

    def _inner(self):
        if self.inner is None:
            self.inner = default_client()
        return self.inner

    def _path(self, kind: str, key: str) -> Path:
        return self.cache / kind / (hashlib.sha1(key.encode()).hexdigest() + ".gz")

    def _load(self, kind: str, key: str):
        target = self._path(kind, key)
        if target.exists():
            return json.loads(gzip.decompress(target.read_bytes()))
        return None

    def _store(self, kind: str, key: str, payload: dict) -> None:
        target = self._path(kind, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))

    @staticmethod
    def _replay(entry: dict):
        if entry["status"] == "ok":
            return entry["data"]
        if entry["status"] == "notfound":
            raise NotFoundError("HTTP 404")
        raise FetchError(entry["message"])

    def api(self, endpoint: str):
        found = re.fullmatch(r"repos/([^/]+)/\.github/commits/HEAD", endpoint)
        if found and found.group(1) in self.pins:
            return {"sha": self.pins[found.group(1)]}
        entry = self._load("api", endpoint)
        if entry is None:
            if self.offline:
                self.misses += 1
                raise FetchError("offline: not cached")
            self.network += 1
            try:
                entry = {"status": "ok", "data": self._inner().api(endpoint)}
            except NotFoundError:
                entry = {"status": "notfound"}
            except FetchError as exc:
                if str(exc) == "invalid API JSON" and endpoint.endswith("?recursive=1"):
                    # Transport bug found by this runner: a recursive tree over the 16 MB read cap
                    # (llvm, rust, firefox, nixpkgs) arrives cut off. Report it as `truncated` so the
                    # tool's own subtree fallback runs, as it does for GitHub's own truncation.
                    entry = {"status": "ok", "data": {"truncated": True, "tree": []}}
                elif str(exc) == "HTTP 409":
                    entry = {"status": "error", "message": str(exc)}
                else:
                    raise
            self._store("api", endpoint, entry)
        return self._replay(entry)

    def raw(self, repo: str, sha: str, path: str) -> bytes:
        key = f"{repo}|{sha}|{path}"
        entry = self._load("raw", key)
        if entry is None:
            if self.offline:
                self.misses += 1
                raise FetchError("offline: not cached")
            self.network += 1
            try:
                data = self._inner().raw(repo, sha, path)
                entry = {"status": "ok", "data": data.decode("latin-1")}
            except NotFoundError:
                entry = {"status": "notfound"}
            self._store("raw", key, entry)
        if entry["status"] == "ok":
            return entry["data"].encode("latin-1")
        raise NotFoundError("HTTP 404")


def run_row(row: dict, client: CachingClient) -> dict:
    snapshot = fetch_snapshot(row["repo"], target_ref(row), client)
    classification = classify_documents(snapshot.documents, owner=row["repo"].split("/")[0])
    report = build_report(snapshot, classification)
    return {"repo": row["repo"], "gold": row["verdict"], "pred": report["verdict"],
            "confidence": row["confidence"], "tool_confidence": report["confidence"],
            "review_reason": report["review_reason"], "complete": report["complete"],
            "evidence": [{**e, "rule": c["rule"]}
                         for e, c in zip(report["evidence"], classification["evidence"])][:4]
            or [{**e, "rule": "-"} for e in report["evidence"][:4]],
            "unclassified": len(report["unclassified_ai_lines"]),
            "files": [f["path"] for f in report["files_checked"] if f["status"] == "read"],
            "warnings": report["warnings"]}


def metrics(results: list[dict]) -> dict:
    """Overall accuracy counts needs-review as wrong. Decided-row accuracy and the abstain rate treat it as a
    correct abstention (the row leaves the denominator); a dangerous error is a repository whose gold label
    has a policy while the tool says no-policy-found (a needs-review is never dangerous)."""
    n = len(results)
    ok = sum(r["gold"] == r["pred"] for r in results)
    decided = [r for r in results if r["pred"] != NEEDS_REVIEW]
    abstained = n - len(decided)
    dangerous = [r["repo"] for r in results if r["gold"] != NO_POLICY_FOUND and r["pred"] == NO_POLICY_FOUND]
    sure = [r for r in decided if r.get("tool_confidence") == "high"]
    unsure = [r for r in decided if r.get("tool_confidence") == "low"]
    conf = Counter((r["gold"], r["pred"]) for r in results)
    per_class = {}
    for v in VERDICTS:
        tp = conf[(v, v)]
        pred = sum(c for (g, p), c in conf.items() if p == v)
        gold = sum(c for (g, p), c in conf.items() if g == v)
        per_class[v] = {"precision": tp / pred if pred else None, "recall": tp / gold if gold else None,
                        "support": gold, "predicted": pred}
    hi = [r for r in results if r["confidence"] == "high"]
    mh = [r for r in results if r["confidence"] in ("high", "medium")]

    def acc(rs):
        return (sum(r["gold"] == r["pred"] for r in rs) / len(rs)) if rs else None

    def dacc(rs):
        return (sum(r["gold"] == r["pred"] for r in rs) / len(rs)) if rs else None

    return {"n": n, "correct": ok, "accuracy": ok / n if n else None,
            "decided_n": len(decided), "decided_correct": sum(r["gold"] == r["pred"] for r in decided),
            "decided_accuracy": dacc(decided),
            "abstained": abstained, "abstain_rate": abstained / n if n else None,
            "dangerous": len(dangerous), "dangerous_repos": dangerous,
            "tool_high_n": len(sure), "tool_high_accuracy": dacc(sure),
            "tool_low_n": len(unsure), "tool_low_accuracy": dacc(unsure),
            "high_n": len(hi), "high_accuracy": acc(hi),
            "high_medium_n": len(mh), "high_medium_accuracy": acc(mh),
            "per_class": per_class, "confusion": {f"{g}>{p}": c for (g, p), c in sorted(conf.items())}}


# Why a repository is still missed, recorded by hand after reading the evidence (scan of 2026-11-22).
#   fetch-org    the policy is in a file the transport does not read (org .github repository, a doc outside
#                the scanned folders, a file a link points to); the classifier never sees the text
#   label-scope  the label rests on a file the labelling rubric does not scan, on an external page, on an
#                interpretation the rubric does not state, or on one weak sentence (confidence low)
# A miss that is listed nowhere is reported as "rule": the classifier saw the text and decided differently.
MISS_CAUSES = {
    # (empty since the AI-named file and AI-context link discovery: every labelled policy file is now fetched)
    # the classifier saw the text; the label rests on something else (see the row's note and RUBRIC.md)
    "scipy/scipy": "label-scope",              # the policy file is outside the folders the rubric scans
    "fastapi/fastapi": "label-scope",          # only an org-level PR template; the label says no policy
    "pnpm/pnpm": "label-scope",                # CONTRIBUTING has an agent disclosure rule; label: agent-instructions-only
    "pytest-dev/pytest": "label-scope",        # credit-trailer checkbox in the template; the label says permitted
    "RustPython/RustPython": "label-scope",    # AGENTS.md forbids an AI Co-authored-by trailer (RUBRIC: restricted)
    "honojs/hono": "label-scope",              # the label counts a maintainers-only PR section that is not AI-specific
    "cupy/cupy": "label-scope", "aaif-goose/goose": "label-scope", "python/cpython": "label-scope",
    "jquery/jquery": "label-scope", "celery/celery": "label-scope", "junegunn/fzf": "label-scope",  # confidence low
}


def pct(x) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}"


def print_metrics(title: str, results: list[dict]) -> None:
    m = metrics(results)
    print(f"\n== {title}: overall {m['correct']}/{m['n']} = {pct(m['accuracy'])}% (needs-review counted wrong)  "
          f"gold-high {pct(m['high_accuracy'])}% (n={m['high_n']})  "
          f"gold-high+medium {pct(m['high_medium_accuracy'])}% (n={m['high_medium_n']})")
    print(f"   (a) decided-row accuracy {m['decided_correct']}/{m['decided_n']} = {pct(m['decided_accuracy'])}%  "
          f"(b) abstain rate {m['abstained']}/{m['n']} = {pct(m['abstain_rate'])}%  "
          f"(c) dangerous errors {m['dangerous']} {m['dangerous_repos']}")
    print(f"   tool confidence on decided rows: high {pct(m['tool_high_accuracy'])}% (n={m['tool_high_n']}), "
          f"low {pct(m['tool_low_accuracy'])}% (n={m['tool_low_n']})")
    print(f"{'class':22} {'prec':>6} {'rec':>6} {'support':>8} {'predicted':>9}")
    for v in VERDICTS:
        c = m["per_class"][v]
        print(f"{v:22} {pct(c['precision']):>6} {pct(c['recall']):>6} {c['support']:>8} {c['predicted']:>9}")
    short = {"banned": "ban", "restricted": "restr", "disclosure-required": "discl", "permitted": "perm",
             "no-policy-found": "none", "needs-review": "review"}
    print("confusion (rows gold, cols predicted)")
    print(f"{'':8}" + "".join(f"{short[v]:>7}" for v in VERDICTS))
    for g in VERDICTS:
        if g == NEEDS_REVIEW:
            continue  # no gold label is needs-review
        print(f"{short[g]:8}" + "".join(
            f"{sum(1 for r in results if r['gold'] == g and r['pred'] == p):>7}" for p in VERDICTS))


def print_abstained(results: list[dict], notes: dict[str, str]) -> None:
    held = [r for r in results if r["pred"] == NEEDS_REVIEW]
    print(f"\nabstained, needs-review ({len(held)})")
    for r in sorted(held, key=lambda r: (r["gold"], r["repo"])):
        print(f"- {r['repo']}: gold={r['gold']} ({r['confidence']}) reason={r['review_reason']}")
        print(f"    note: {notes[r['repo']][:120]}")


def print_misses(results: list[dict], notes: dict[str, str]) -> None:
    misses = [r for r in results if r["gold"] != r["pred"] and r["pred"] != NEEDS_REVIEW]
    print(f"\nmisses on decided rows ({len(misses)}; needs-review rows are listed separately)")
    for r in sorted(misses, key=lambda r: (r["gold"], r["repo"])):
        print(f"- {r['repo']}: gold={r['gold']} ({r['confidence']}) pred={r['pred']} "
              f"read={len(r['files'])} complete={r['complete']}")
        print(f"    note: {notes[r['repo']][:150]}")
        for e in r["evidence"][:4]:
            print(f"    pred ev: {e['signal']}/{e['rule']} {e['file']}:{e['line']} {e['quote'][:90]}")


def print_causes(results: list[dict]) -> None:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        if r["gold"] != r["pred"] and r["pred"] != NEEDS_REVIEW:  # an abstention is listed by print_abstained
            groups[MISS_CAUSES.get(r["repo"], "rule")].append(r)
    print(f"\nmisses by cause ({sum(len(v) for v in groups.values())})")
    for cause in ("rule", "fetch-org", "label-scope"):
        rows = sorted(groups.get(cause, []), key=lambda r: (r["gold"], r["repo"]))
        print(f"- {cause}: {len(rows)}")
        for r in rows:
            print(f"    {r['repo']} [{r['split']}] gold={r['gold']} ({r['confidence']}) pred={r['pred']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    ap.add_argument("--offline", action="store_true", help="never touch the network; cache misses become read errors")
    ap.add_argument("--cache", type=Path, default=HERE / "_cache")
    ap.add_argument("--fetch-only", action="store_true", help="fill the cache, print no verdicts")
    ap.add_argument("--cv", action="store_true",
                    help="development-set report: all rows, former-holdout rows, per-class precision/recall, misses by cause")
    ap.add_argument("--summary", action="store_true", help="metrics only, no per-repo misses")
    ap.add_argument("--holdout-misses", action="store_true", help="also list holdout misses (do not use while tuning)")
    ap.add_argument("--out", type=Path, help="write per-repo results as JSON")
    args = ap.parse_args(argv)
    if args.cv:
        args.split = "all"

    rows = load_gold()
    split = split_gold(rows)
    chosen = [r for r in rows if args.split == "all" or split[r["repo"]] == args.split]
    client = CachingClient(args.cache, org_pins(rows), offline=args.offline)
    results = []
    for row in chosen:
        try:
            result = run_row(row, client)
        except Exception as exc:  # keep going; one bad repo must not hide the rest
            print(f"ERROR {row['repo']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            result = {"repo": row["repo"], "gold": row["verdict"], "pred": "error", "confidence": row["confidence"],
                      "complete": False, "evidence": [], "unclassified": 0, "files": [], "warnings": [str(exc)]}
        result["split"] = split[row["repo"]]
        results.append(result)
    if args.fetch_only:
        print(f"fetched {len(results)} repositories, network requests {client.network}")
        return 0
    notes = {r["repo"]: r["note"] for r in rows}
    if args.cv:
        print_metrics(f"development set, all {len(results)} rows (optimistic: the rules were tuned on every row)", results)
        held = [r for r in results if r["split"] == "holdout"]
        print_metrics(f"former holdout, {len(held)} rows (seen while tuning, not blind)", held)
        print_causes(results)
        print_abstained(results, notes)
    elif args.split == "all":
        for name in ("dev", "holdout"):
            print_metrics(name, [r for r in results if r["split"] == name])
        print_metrics("all", results)
    else:
        print_metrics(args.split, results)
    if not args.cv and not args.summary and (args.split != "holdout" or args.holdout_misses):
        shown = [r for r in results if r["split"] == "dev" or args.holdout_misses]
        print_misses(shown, notes)
        print_abstained(shown, notes)
    if client.misses:
        print(f"\nWARNING: {client.misses} cache misses in offline mode; numbers are not a faithful rerun")
    if args.out:
        args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nnetwork requests this run: {client.network}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
