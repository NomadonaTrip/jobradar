#!/usr/bin/env python3
"""
Job Pipeline Orchestrator — runs the full autonomous workflow:

  1. FETCH  — Pull new PM & Scrum Master openings from JSearch + Remotive
  2. TAILOR — Generate tailored resume + cover letter + report for each new JD
  3. NOTIFY — Send digest via email + HTML report

Usage:
    python run_pipeline.py                    # full run
    python run_pipeline.py --fetch-only       # just fetch, don't tailor or notify
    python run_pipeline.py --tailor-limit 5   # limit tailoring to 5 JDs
    python run_pipeline.py --no-email         # skip email notification
    python run_pipeline.py --skip-fetch       # skip fetching, tailor + notify only

Schedule this with cron (Linux/Mac) or Task Scheduler (Windows):
    # Daily at 8am:
    # crontab: 0 8 * * * cd /path/to/RESUME_GEN && .venv/bin/python run_pipeline.py >> pipeline.log 2>&1
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = str(ROOT / ".venv" / "bin" / "python")


def run_step(name: str, cmd: list[str], timeout: int = 600) -> bool:
    """Run a pipeline step and return success/failure."""
    print(f"\n{'='*60}")
    print(f"  STEP: {name}")
    print(f"{'='*60}\n")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            timeout=timeout,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"\n  TIMEOUT: {name} exceeded {timeout}s limit.")
        return False
    except Exception as e:
        print(f"\n  ERROR: {name} failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Run the full job pipeline")
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch, skip tailoring and notification")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip fetching, only tailor and notify")
    parser.add_argument("--tailor-limit", type=int, default=5, help="Max JDs to tailor per run (default: 5)")
    parser.add_argument("--no-email", action="store_true", help="Skip email notification")
    parser.add_argument("--no-html", action="store_true", help="Skip HTML report")
    args = parser.parse_args()

    start = time.time()
    print(f"\n{'#'*60}")
    print(f"  JOB PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'#'*60}")

    results = {}

    # --- Step 1: FETCH ---
    if not args.skip_fetch:
        ok = run_step("FETCH new job openings", [PYTHON, "fetcher.py"], timeout=300)
        results["fetch"] = ok
        if not ok:
            print("\n  Fetch failed. Continuing to tailor existing JDs...")
    else:
        print("\n  [Skipping fetch]")
        results["fetch"] = "skipped"

    if args.fetch_only:
        print("\n  --fetch-only: stopping here.")
        return

    # --- Step 2: TAILOR ---
    ok = run_step(
        f"TAILOR resumes (limit: {args.tailor_limit})",
        [PYTHON, "tailor.py", "--limit", str(args.tailor_limit)],
        timeout=args.tailor_limit * 300,  # ~5 min per JD
    )
    results["tailor"] = ok

    # --- Step 3: NOTIFY ---
    notify_cmd = [PYTHON, "notify.py"]
    if args.no_email:
        notify_cmd.append("--no-email")
    if args.no_html:
        notify_cmd.append("--no-html")

    ok = run_step("NOTIFY about new packages", notify_cmd, timeout=60)
    results["notify"] = ok

    # --- Summary ---
    elapsed = time.time() - start
    print(f"\n{'#'*60}")
    print(f"  PIPELINE COMPLETE — {elapsed:.0f}s elapsed")
    print(f"{'#'*60}")
    for step, status in results.items():
        icon = "OK" if status is True else ("SKIP" if status == "skipped" else "FAIL")
        print(f"  [{icon}] {step}")
    print()


if __name__ == "__main__":
    main()
