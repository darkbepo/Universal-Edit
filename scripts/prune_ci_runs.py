"""Delete old GitHub Actions runs, keeping the newest few of every workflow.

Keeps, per workflow, the newest ``--keep`` runs (default 3), plus the newest
successful "Build Windows executable" run so a downloadable .exe always stays.
Runs that are still queued or in progress are never touched.

Uses the GitHub CLI (``gh``), which must be logged in. Deleting a run also
deletes its logs and artifacts and cannot be undone, so it only lists what it
would delete unless ``--yes`` is given:

    python scripts/prune_ci_runs.py          # dry run: shows what would go
    python scripts/prune_ci_runs.py --yes    # deletes
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = "darkbepo/Universal-Edit"
KEEP_SUCCESS = "Build Windows executable"  # its artifact is the .exe zip


def find_gh() -> str:
    gh = shutil.which("gh")
    if gh:
        return gh
    windows = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if windows.exists():
        return str(windows)
    sys.exit("The GitHub CLI (gh) isn't installed or not on PATH.")


def gh(*args: str) -> str:
    return subprocess.run([find_gh(), *args], check=True, capture_output=True, text=True).stdout


def all_runs(repo: str) -> list[dict]:
    out = gh(
        "api", "--paginate", f"repos/{repo}/actions/runs?per_page=100", "-q", ".workflow_runs[]"
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def runs_to_delete(runs: list[dict], keep: int) -> list[dict]:
    by_workflow: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        by_workflow[run["name"]].append(run)
    doomed = []
    for name, group in by_workflow.items():
        group.sort(key=lambda r: r["created_at"], reverse=True)
        kept = {r["id"] for r in group[:keep]}
        if name == KEEP_SUCCESS:
            success = next((r for r in group if r["conclusion"] == "success"), None)
            if success:
                kept.add(success["id"])
        doomed += [r for r in group if r["id"] not in kept and r["status"] == "completed"]
    return doomed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--keep", type=int, default=3, help="newest runs to keep per workflow (default 3)"
    )
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--yes", action="store_true", help="really delete (default: dry run)")
    args = parser.parse_args()
    if args.keep < 1:
        sys.exit("--keep must be at least 1.")

    runs = all_runs(args.repo)
    doomed = runs_to_delete(runs, args.keep)
    for run in doomed:
        when = run["created_at"][:10]
        verb = "deleting" if args.yes else "would delete"
        print(f"{verb} {run['id']}  {when}  {run['name']}  ({run['conclusion']})")
        if args.yes:
            gh("api", "-X", "DELETE", f"repos/{args.repo}/actions/runs/{run['id']}")
    total = "Deleted" if args.yes else "Would delete"
    print(f"{total} {len(doomed)} of {len(runs)} runs; kept the newest {args.keep} per workflow.")


if __name__ == "__main__":
    main()
