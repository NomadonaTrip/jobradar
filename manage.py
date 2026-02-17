#!/usr/bin/env python3
"""
Customer Management CLI — concierge operations for multiple customers.

Usage:
    python manage.py import onboarding_Tayo_Fasunon.json   # import from onboarding form
    python manage.py list                                   # list all customers
    python manage.py status john-doe                        # show customer status
    python manage.py run john-doe                           # run pipeline for one customer
    python manage.py run-all                                # run pipeline for all customers
    python manage.py run-all --fetch-only                   # just fetch for everyone
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CUSTOMERS_DIR = ROOT / "customers"
PYTHON = str(ROOT / ".venv" / "bin" / "python")
TEMPLATE_CONFIG = ROOT / "config.yaml"


def slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ---------------------------------------------------------------------------
# Import from onboarding JSON
# ---------------------------------------------------------------------------
def do_import(data: dict, force: bool = False) -> Path:
    """Core import logic — create a customer directory from onboarding JSON data.

    Returns the customer directory path. Used by both cmd_import (CLI) and
    auto_import.py (Drive poller).
    """
    name = f"{data['firstName']} {data['lastName']}"
    slug = slugify(name)
    customer_dir = CUSTOMERS_DIR / slug

    if customer_dir.exists() and not force:
        raise FileExistsError(f"Customer '{slug}' already exists. Use force=True to overwrite.")

    customer_dir.mkdir(parents=True, exist_ok=True)

    # Save raw onboarding data
    (customer_dir / "onboarding.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Generate base resume from onboarding data
    resume_md = build_resume_from_onboarding(data)
    (customer_dir / "base_resume.md").write_text(resume_md, encoding="utf-8")

    # Save base cover letter if provided (used for voice matching in tailoring)
    cover_letter_text = data.get("coverLetterText", "")
    if cover_letter_text:
        (customer_dir / "base_cover_letter.md").write_text(cover_letter_text, encoding="utf-8")

    # Generate customer config
    config = build_customer_config(data, slug)
    with open(customer_dir / "config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    # Create subdirectories
    (customer_dir / "JDs").mkdir(exist_ok=True)
    (customer_dir / "output").mkdir(exist_ok=True)

    # Initialize state files
    for state_file in ["state.json", "tailor_state.json", "notify_state.json"]:
        state_path = customer_dir / state_file
        if not state_path.exists():
            state_path.write_text("{}", encoding="utf-8")

    # Save discovery notes
    discovery = data.get("discovery", {})
    if discovery and any(discovery.values()):
        notes = build_discovery_notes(data)
        (customer_dir / "discovery_notes.md").write_text(notes, encoding="utf-8")

    return customer_dir


def cmd_import(args):
    """Import a customer from onboarding form JSON."""
    json_path = Path(args.file)
    if not json_path.exists():
        print(f"ERROR: File not found: {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    try:
        customer_dir = do_import(data, force=args.force)
    except FileExistsError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    name = f"{data['firstName']} {data['lastName']}"
    slug = slugify(name)
    print(f"\n  Customer imported: {name}")
    print(f"  Slug: {slug}")
    print(f"  Directory: {customer_dir}")
    print(f"  Files created:")
    for f in sorted(customer_dir.rglob("*")):
        if f.is_file():
            print(f"    {f.relative_to(customer_dir)}")
    print(f"\n  Next: python manage.py run {slug}")


def build_resume_from_onboarding(data: dict) -> str:
    """Build a base resume markdown from onboarding data."""
    resume = data.get("resumeText", "")
    if resume:
        return resume

    # If no pasted resume, build a skeleton from discovery data
    name = f"{data['firstName']} {data['lastName']}"
    discovery = data.get("discovery", {})

    md = f"# {name}\n\n"
    md += f"**{data.get('location', '')}** | {data.get('email', '')} | {data.get('phone', '')}"
    if data.get("linkedin"):
        md += f" | [{data['linkedin']}]({data['linkedin']})"
    md += "\n\n---\n\n"

    md += "## Professional Summary\n\n"
    md += f"[To be refined based on discovery data]\n\n"

    if discovery.get("certs"):
        md += f"## Certifications\n\n{discovery['certs']}\n\n"

    if discovery.get("tools"):
        md += f"## Key Skills\n\n{discovery['tools']}\n\n"

    md += "## Professional Experience\n\n"
    md += "[Experience details to be extracted from uploaded resume file]\n\n"

    if discovery.get("metrics"):
        md += "## Key Achievements\n\n"
        for line in discovery["metrics"].split("\n"):
            line = line.strip()
            if line:
                if not line.startswith("-"):
                    line = f"- {line}"
                md += f"{line}\n"
        md += "\n"

    md += "## Education\n\n[To be filled from resume]\n"

    return md


def build_customer_config(data: dict, slug: str) -> dict:
    """Generate a customer-specific config.yaml."""
    # Load the master config as template for API keys
    master = {}
    if TEMPLATE_CONFIG.exists():
        with open(TEMPLATE_CONFIG, "r", encoding="utf-8") as f:
            master = yaml.safe_load(f) or {}

    work_pref = data.get("workArrangement", "any")
    remote_only = work_pref == "remote"

    # Parse salary
    min_salary = 0
    sal_str = data.get("minSalary", "")
    if sal_str:
        nums = re.findall(r"[\d,]+", sal_str.replace(",", ""))
        if nums:
            min_salary = int(nums[0])

    return {
        "candidate": {
            "name": f"{data['firstName']} {data['lastName']}",
            "location": data.get("location", ""),
            "email": data.get("email", ""),
            "base_resume": "base_resume.md",
            "base_cover_letter": "base_cover_letter.md",
        },
        "search": {
            "queries": data.get("roles", ["Scrum Master", "Product Manager"]),
            "locations": data.get("locations", ["Canada"]),
            "remote_only": remote_only,
            "date_posted": "week",
            "results_per_query": 10,
        },
        "api_keys": {
            "jsearch_rapidapi": master.get("api_keys", {}).get("jsearch_rapidapi", ""),
            "adzuna_app_id": master.get("api_keys", {}).get("adzuna_app_id", ""),
            "adzuna_app_key": master.get("api_keys", {}).get("adzuna_app_key", ""),
        },
        "sources": master.get("sources", {
            "jsearch": {"enabled": True, "base_url": "https://jsearch.p.rapidapi.com/search"},
            "remotive": {"enabled": True, "base_url": "https://remotive.com/api/remote-jobs"},
            "adzuna": {"enabled": True, "base_url": "https://api.adzuna.com/v1/api/jobs", "country": "ca"},
            "remoteok": {"enabled": True, "base_url": "https://remoteok.com/api"},
            "themuse": {"enabled": True, "base_url": "https://www.themuse.com/api/public/jobs"},
        }),
        "output": {
            "jd_directory": "JDs",
            "state_file": "state.json",
        },
        "filters": {
            "exclude_companies": data.get("exclude", []),
            "min_salary": min_salary,
            "must_contain": [],
            "exclude_keywords": [],
        },
        "notifications": master.get("notifications", {
            "email": {
                "sender": "",
                "app_password": "",
                "recipient": data.get("email", ""),
            }
        }),
        "lifecycle": {
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "daily_limit": 10,
            "total_delivered": 0,
        },
    }


def build_discovery_notes(data: dict) -> str:
    """Generate discovery notes markdown from onboarding data."""
    d = data.get("discovery", {})
    name = f"{data['firstName']} {data['lastName']}"

    md = f"# Discovery Notes: {name}\n\n"
    md += f"**Date:** {data.get('submittedAt', datetime.now().isoformat())[:10]}\n\n---\n\n"

    fields = [
        ("Team Leadership Scale", d.get("teamSize")),
        ("Largest Project/Budget", d.get("budget")),
        ("Measurable Improvements", d.get("metrics")),
        ("Certifications", d.get("certs")),
        ("Challenge Overcome", d.get("challenge")),
        ("Tools & Technologies", d.get("tools")),
        ("Industries Worked In", d.get("industries")),
        ("Underrepresented Experience", d.get("hidden")),
    ]

    for label, value in fields:
        if value:
            md += f"## {label}\n\n{value}\n\n"

    notes = data.get("prefNotes", "")
    if notes:
        md += f"## Additional Notes\n\n{notes}\n\n"

    return md


# ---------------------------------------------------------------------------
# List customers
# ---------------------------------------------------------------------------
def cmd_list(args):
    """List all customers and their status."""
    if not CUSTOMERS_DIR.exists():
        print("  No customers yet. Import one with: python manage.py import <file.json>")
        return

    print(f"\n  {'CUSTOMER':<25} {'ROLES':<30} {'JDs':<6} {'SENT':<6} {'STATUS'}")
    print(f"  {'─'*25} {'─'*30} {'─'*6} {'─'*6} {'─'*18}")

    now = datetime.now(timezone.utc)
    for cdir in sorted(CUSTOMERS_DIR.iterdir()):
        if not cdir.is_dir():
            continue

        config_path = cdir / "config.yaml"
        if not config_path.exists():
            continue

        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        name = config.get("candidate", {}).get("name", cdir.name)
        roles = ", ".join(config.get("search", {}).get("queries", []))[:28]

        # Count JDs
        jd_count = len(list((cdir / "JDs").glob("*.md"))) if (cdir / "JDs").exists() else 0

        # Lifecycle info
        lifecycle = config.get("lifecycle", {})
        total_delivered = lifecycle.get("total_delivered", 0)
        expires_at_str = lifecycle.get("expires_at", "")

        if expires_at_str:
            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            days_left = (expires_at - now).days
            if days_left < 0:
                status = "EXPIRED"
            else:
                status = f"ACTIVE ({days_left}d left)"
        else:
            status = "NO LIFECYCLE"

        print(f"  {name:<25} {roles:<30} {jd_count:<6} {total_delivered:<6} {status}")

    print()


# ---------------------------------------------------------------------------
# Customer status
# ---------------------------------------------------------------------------
def cmd_status(args):
    """Show detailed status for a customer."""
    slug = args.customer
    cdir = CUSTOMERS_DIR / slug

    if not cdir.exists():
        print(f"ERROR: Customer '{slug}' not found.")
        sys.exit(1)

    config_path = cdir / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    name = config.get("candidate", {}).get("name", slug)

    print(f"\n  Customer: {name}")
    print(f"  Directory: {cdir}")
    print(f"  Email: {config.get('candidate', {}).get('email', 'N/A')}")
    print(f"  Roles: {', '.join(config.get('search', {}).get('queries', []))}")
    print(f"  Locations: {', '.join(config.get('search', {}).get('locations', []))}")

    jd_count = len(list((cdir / "JDs").glob("*.md"))) if (cdir / "JDs").exists() else 0
    print(f"\n  JDs fetched: {jd_count}")

    tailor_state_path = cdir / "tailor_state.json"
    if tailor_state_path.exists():
        ts = json.loads(tailor_state_path.read_text(encoding="utf-8"))
        tailored = ts.get("tailored", {})
        print(f"  Tailored: {len(tailored)}")
    else:
        print(f"  Tailored: 0")

    output_dir = cdir / "output"
    if output_dir.exists():
        packages = [d for d in output_dir.iterdir() if d.is_dir()]
        print(f"  Output packages: {len(packages)}")
        for pkg in sorted(packages)[:10]:
            print(f"    - {pkg.name}")
        if len(packages) > 10:
            print(f"    ... and {len(packages) - 10} more")

    # Lifecycle
    lifecycle = config.get("lifecycle", {})
    if lifecycle:
        now = datetime.now(timezone.utc)
        started = lifecycle.get("started_at", "N/A")
        expires_str = lifecycle.get("expires_at", "")
        daily_limit = lifecycle.get("daily_limit", "N/A")
        total_delivered = lifecycle.get("total_delivered", 0)

        print(f"\n  Lifecycle:")
        print(f"    Started: {started[:10] if started != 'N/A' else 'N/A'}")
        if expires_str:
            expires_at = datetime.strptime(expires_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            days_left = (expires_at - now).days
            if days_left < 0:
                print(f"    Expires: {expires_str[:10]} (EXPIRED)")
            else:
                print(f"    Expires: {expires_str[:10]} ({days_left} days remaining)")
        print(f"    Daily limit: {daily_limit}")
        print(f"    Total delivered: {total_delivered}")

    print()


# ---------------------------------------------------------------------------
# Run pipeline for a customer
# ---------------------------------------------------------------------------
def cmd_run(args):
    """Run the pipeline for a specific customer."""
    slug = args.customer
    cdir = CUSTOMERS_DIR / slug

    if not cdir.exists():
        print(f"ERROR: Customer '{slug}' not found.")
        sys.exit(1)

    print(f"\n  Running pipeline for: {slug}")
    print(f"  Directory: {cdir}\n")

    # Scripts check PIPELINE_WORKDIR env var to override ROOT
    env = {**os.environ, "PIPELINE_WORKDIR": str(cdir)}

    # Run the pipeline
    steps = []
    if not args.skip_fetch:
        steps.append(("FETCH", [PYTHON, str(ROOT / "fetcher.py")]))
    if not args.fetch_only:
        tailor_cmd = [PYTHON, str(ROOT / "tailor.py"), "--limit", str(args.tailor_limit)]
        steps.append(("TAILOR", tailor_cmd))
        notify_cmd = [PYTHON, str(ROOT / "notify.py")]
        if args.no_email:
            notify_cmd.append("--no-email")
        steps.append(("NOTIFY", notify_cmd))

    for name, cmd in steps:
        print(f"  [{name}]")
        result = subprocess.run(cmd, cwd=str(cdir), env=env, timeout=1800)
        if result.returncode != 0:
            print(f"  [{name}] FAILED (exit {result.returncode})")
        else:
            print(f"  [{name}] OK")
        print()


# ---------------------------------------------------------------------------
# Run all customers
# ---------------------------------------------------------------------------
def cmd_run_all(args):
    """Run the pipeline for all customers."""
    if not CUSTOMERS_DIR.exists():
        print("  No customers yet.")
        return

    customers = [d for d in sorted(CUSTOMERS_DIR.iterdir()) if d.is_dir() and (d / "config.yaml").exists()]
    print(f"\n  Running pipeline for {len(customers)} customer(s)...\n")

    now = datetime.now(timezone.utc)
    for cdir in customers:
        slug = cdir.name

        # Check lifecycle expiry
        with open(cdir / "config.yaml", "r", encoding="utf-8") as f:
            cust_config = yaml.safe_load(f)
        lifecycle = cust_config.get("lifecycle", {})
        expires_at_str = lifecycle.get("expires_at", "")
        if expires_at_str:
            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if now > expires_at:
                print(f"  SKIPPING {slug} — expired on {expires_at_str[:10]}")
                print()
                continue

        print(f"{'='*60}")
        print(f"  CUSTOMER: {slug}")
        print(f"{'='*60}")

        # Reuse cmd_run logic
        args.customer = slug
        try:
            cmd_run(args)
        except Exception as e:
            print(f"  ERROR: {e}")
        print()


# ---------------------------------------------------------------------------
# Renew customer
# ---------------------------------------------------------------------------
def cmd_renew(args):
    """Extend a customer's expiry date."""
    slug = args.customer
    cdir = CUSTOMERS_DIR / slug

    if not cdir.exists():
        print(f"ERROR: Customer '{slug}' not found.")
        sys.exit(1)

    config_path = cdir / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    lifecycle = config.get("lifecycle", {})
    if not lifecycle:
        # Initialize lifecycle if missing
        lifecycle = {
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "daily_limit": 10,
            "total_delivered": 0,
        }
        config["lifecycle"] = lifecycle
    else:
        # Extend from current expiry (or now if already expired)
        expires_str = lifecycle.get("expires_at", "")
        if expires_str:
            current_expiry = datetime.strptime(expires_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            base = max(current_expiry, datetime.now(timezone.utc))
        else:
            base = datetime.now(timezone.utc)
        new_expiry = base + timedelta(days=args.days)
        lifecycle["expires_at"] = new_expiry.strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"\n  Renewed {slug} by {args.days} days.")
    print(f"  New expiry: {lifecycle['expires_at'][:10]}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Customer management for job pipeline")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # import
    p_import = subparsers.add_parser("import", help="Import customer from onboarding JSON")
    p_import.add_argument("file", help="Path to onboarding JSON file")
    p_import.add_argument("--force", action="store_true", help="Overwrite existing customer")

    # list
    subparsers.add_parser("list", help="List all customers")

    # status
    p_status = subparsers.add_parser("status", help="Show customer status")
    p_status.add_argument("customer", help="Customer slug (e.g. john-doe)")

    # run
    p_run = subparsers.add_parser("run", help="Run pipeline for a customer")
    p_run.add_argument("customer", help="Customer slug")
    p_run.add_argument("--fetch-only", action="store_true")
    p_run.add_argument("--skip-fetch", action="store_true")
    p_run.add_argument("--tailor-limit", type=int, default=10)
    p_run.add_argument("--no-email", action="store_true")

    # run-all
    p_all = subparsers.add_parser("run-all", help="Run pipeline for all customers")
    p_all.add_argument("--fetch-only", action="store_true")
    p_all.add_argument("--skip-fetch", action="store_true")
    p_all.add_argument("--tailor-limit", type=int, default=10)
    p_all.add_argument("--no-email", action="store_true")

    # renew
    p_renew = subparsers.add_parser("renew", help="Extend customer expiry date")
    p_renew.add_argument("customer", help="Customer slug")
    p_renew.add_argument("--days", type=int, default=20, help="Days to extend (default: 20)")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "run-all":
        cmd_run_all(args)
    elif args.command == "renew":
        cmd_renew(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
