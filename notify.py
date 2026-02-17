#!/usr/bin/env python3
"""
Notify — Phase 3 of the autonomous resume-tailoring pipeline.

Generates a digest of newly tailored application packages and delivers via:
  1. Local HTML report (auto-opens in browser)
  2. Email via Gmail SMTP (requires App Password)

Usage:
    python notify.py                      # notify about all recent tailoring
    python notify.py --since 2026-02-10   # only packages since a date
    python notify.py --no-email           # skip email, HTML only
    python notify.py --no-html            # skip HTML, email only
"""

import argparse
import json
import os
import platform
import smtplib
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------
ROOT = Path(os.environ["PIPELINE_WORKDIR"]) if "PIPELINE_WORKDIR" in os.environ else Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
TAILOR_STATE_FILE = ROOT / "tailor_state.json"
NOTIFY_STATE_FILE = ROOT / "notify_state.json"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_tailor_state() -> dict:
    if TAILOR_STATE_FILE.exists():
        with open(TAILOR_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("tailored", {})
        return data
    return {"tailored": {}}


def load_notify_state() -> dict:
    if NOTIFY_STATE_FILE.exists():
        with open(NOTIFY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("notified", {})
        return data
    return {"notified": {}}


def save_notify_state(state: dict):
    with open(NOTIFY_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Gather data about tailored packages
# ---------------------------------------------------------------------------
def get_package_info(jd_name: str, tailor_entry: dict) -> dict:
    """Extract key info from a tailored package for the digest."""
    output_base = ROOT / "output"

    # Find the output directory for this JD
    # The JD filename maps to a directory name via the tailor.py logic
    jd_stem = Path(jd_name).stem
    parts = jd_stem.split("_", 1)
    company = parts[0].replace("-", " ") if parts else "Unknown"
    role = parts[1].replace("_", " ") if len(parts) > 1 else "Unknown"

    # Find matching output dir
    output_dir = None
    for d in output_base.iterdir():
        if d.is_dir() and company.replace(" ", "_") in d.name:
            output_dir = d
            break

    if not output_dir:
        # Try broader match
        for d in output_base.iterdir():
            if d.is_dir():
                # Check if JD is inside
                jd_copy = d / "jd.md"
                if jd_copy.exists():
                    output_dir = d
                    # Verify it's the right one by checking name overlap
                    if parts[0] in d.name:
                        break

    # Read JD for metadata
    apply_url = ""
    location = ""
    salary = ""
    jd_path = ROOT / "JDs" / jd_name
    if jd_path.exists():
        jd_text = jd_path.read_text(encoding="utf-8")
        for line in jd_text.split("\n"):
            if "**Apply**" in line and "|" in line:
                apply_url = line.split("|")[2].strip().strip("*")
            if "**Location**" in line and "|" in line:
                location = line.split("|")[2].strip().strip("*")
            if "**Salary**" in line:
                salary = line.replace("**Salary:**", "").strip()

        # Try H1 for better role/company
        for line in jd_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# "):
                heading = stripped[2:]
                if " — " in heading:
                    role, company = heading.split(" — ", 1)
                elif " - " in heading:
                    role, company = heading.split(" - ", 1)
                else:
                    role = heading
                break

    # Read report for confidence score
    confidence = "N/A"
    if output_dir:
        for f in output_dir.glob("*Report.md"):
            report_text = f.read_text(encoding="utf-8")
            for line in report_text.split("\n"):
                if "Overall Confidence" in line or "Overall JD Coverage" in line:
                    # Extract percentage
                    import re
                    pcts = re.findall(r"(\d+)%", line)
                    if pcts:
                        confidence = f"{pcts[0]}%"
                    break
            break

    # Collect file info
    files = {}
    cover_letter_text = ""
    resume_docx_name = ""
    cover_letter_name = ""
    if output_dir:
        files = {
            "resume_md": bool(list(output_dir.glob("*Resume.md"))),
            "resume_docx": bool(list(output_dir.glob("*Resume.docx"))),
            "cover_letter": bool(list(output_dir.glob("*CoverLetter.md"))),
            "report": bool(list(output_dir.glob("*Report.md"))),
        }
        # Read cover letter content for inline display
        for cl in output_dir.glob("*CoverLetter.md"):
            cover_letter_text = cl.read_text(encoding="utf-8").strip()
            cover_letter_name = cl.name
            break
        # Get resume DOCX filename
        for docx in output_dir.glob("*Resume.docx"):
            resume_docx_name = docx.name
            break

    return {
        "company": company.strip(),
        "role": role.strip(),
        "location": location,
        "salary": salary,
        "apply_url": apply_url,
        "confidence": confidence,
        "output_dir": str(output_dir) if output_dir else "",
        "processed_at": tailor_entry.get("processed_at", ""),
        "files": files,
        "jd_name": jd_name,
        "cover_letter_text": cover_letter_text,
        "resume_docx_name": resume_docx_name,
        "cover_letter_name": cover_letter_name,
    }


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------
def _confidence_color(confidence: str) -> str:
    try:
        val = int(confidence.replace("%", ""))
        if val >= 90:
            return "#22c55e"
        if val >= 75:
            return "#f59e0b"
        return "#ef4444"
    except (ValueError, AttributeError):
        return "#6b7280"


def _render_cover_letter_html(text: str) -> str:
    """Convert plain-text cover letter to HTML paragraphs."""
    import html as html_mod
    paragraphs = text.strip().split("\n\n")
    parts = []
    for p in paragraphs:
        clean = html_mod.escape(p.strip().replace("\n", " "))
        parts.append(f'<p style="margin:0 0 12px;line-height:1.6;">{clean}</p>')
    return "".join(parts)


def generate_html(packages: list[dict], candidate_name: str) -> str:
    now = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Build table rows — one per role
    rows = ""
    for i, pkg in enumerate(packages):
        conf_color = _confidence_color(pkg["confidence"])

        # Resume download button (cid: link to attachment)
        resume_btn = '<span style="color:#94a3b8;font-size:0.85em;">—</span>'
        if pkg.get("resume_docx_name"):
            cid = f"resume-{i}"
            resume_btn = f'<a href="cid:{cid}" style="display:inline-block;padding:6px 14px;background:#1e40af;color:white;text-decoration:none;border-radius:5px;font-size:0.8em;font-weight:600;white-space:nowrap;">Download Resume</a>'

        # Cover letter download button (cid: link to attachment)
        cl_btn = '<span style="color:#94a3b8;font-size:0.85em;">—</span>'
        if pkg.get("cover_letter_name"):
            cid = f"coverletter-{i}"
            cl_btn = f'<a href="cid:{cid}" style="display:inline-block;padding:6px 14px;background:#047857;color:white;text-decoration:none;border-radius:5px;font-size:0.8em;font-weight:600;white-space:nowrap;">Download Cover Letter</a>'

        # Apply button
        apply_btn = '<span style="color:#94a3b8;font-size:0.85em;">—</span>'
        if pkg["apply_url"] and pkg["apply_url"] != "N/A":
            apply_btn = f'<a href="{pkg["apply_url"]}" target="_blank" style="display:inline-block;padding:6px 14px;background:#1e293b;color:white;text-decoration:none;border-radius:5px;font-size:0.8em;font-weight:600;white-space:nowrap;">Apply &rarr;</a>'

        salary_display = pkg.get("salary") or "—"
        bg = "#ffffff" if i % 2 == 0 else "#f8fafc"

        rows += f"""
            <tr style="background:{bg};">
                <td style="padding:12px 10px;font-weight:600;color:#1e293b;font-size:0.9em;">{pkg['company']}</td>
                <td style="padding:12px 10px;color:#334155;font-size:0.9em;">{pkg['role']}</td>
                <td style="padding:12px 10px;color:#64748b;font-size:0.85em;">{pkg['location']}</td>
                <td style="padding:12px 10px;text-align:center;font-weight:700;color:{conf_color};font-size:0.95em;">{pkg['confidence']}</td>
                <td style="padding:12px 10px;color:#64748b;font-size:0.85em;">{salary_display}</td>
                <td style="padding:12px 10px;text-align:center;">{resume_btn}</td>
                <td style="padding:12px 10px;text-align:center;">{cl_btn}</td>
                <td style="padding:12px 10px;text-align:center;">{apply_btn}</td>
            </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Job Pipeline Digest &mdash; {now}</title>
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f1f5f9;color:#1e293b;">
    <div style="max-width:960px;margin:0 auto;padding:24px;">

        <!-- Header -->
        <div style="background:linear-gradient(135deg,#1e293b 0%,#334155 100%);color:white;padding:32px;border-radius:12px;margin-bottom:24px;">
            <h1 style="font-size:1.6em;margin:0 0 6px;">Job Pipeline Digest</h1>
            <p style="color:#94a3b8;font-size:0.95em;margin:0;">{candidate_name} &mdash; {now}</p>
            <p style="color:#cbd5e1;font-size:0.85em;margin:8px 0 0;">{len(packages)} new tailored application{'s' if len(packages) != 1 else ''} ready</p>
        </div>

        <!-- Stats -->
        <div style="display:flex;gap:12px;margin-bottom:24px;">
            <div style="flex:1;background:white;border-radius:8px;padding:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                <div style="font-size:1.8em;font-weight:700;color:#1e293b;">{len(packages)}</div>
                <div style="font-size:0.8em;color:#64748b;">Matches</div>
            </div>
            <div style="flex:1;background:white;border-radius:8px;padding:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                <div style="font-size:1.8em;font-weight:700;color:#1e293b;">{sum(1 for p in packages if p['files'].get('resume_docx'))}</div>
                <div style="font-size:0.8em;color:#64748b;">Resumes Ready</div>
            </div>
            <div style="flex:1;background:white;border-radius:8px;padding:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                <div style="font-size:1.8em;font-weight:700;color:#1e293b;">{sum(1 for p in packages if p['files'].get('cover_letter'))}</div>
                <div style="font-size:0.8em;color:#64748b;">Cover Letters</div>
            </div>
            <div style="flex:1;background:white;border-radius:8px;padding:16px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                <div style="font-size:1.8em;font-weight:700;color:#1e293b;">{sum(1 for p in packages if p.get('apply_url') and p['apply_url'] != 'N/A')}</div>
                <div style="font-size:0.8em;color:#64748b;">Apply Links</div>
            </div>
        </div>

        <!-- Table -->
        <div style="background:white;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.1);overflow:hidden;">
            <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-family:inherit;">
                <thead>
                    <tr style="background:#1e293b;">
                        <th style="padding:12px 10px;text-align:left;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Company</th>
                        <th style="padding:12px 10px;text-align:left;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Role</th>
                        <th style="padding:12px 10px;text-align:left;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Location</th>
                        <th style="padding:12px 10px;text-align:center;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Match</th>
                        <th style="padding:12px 10px;text-align:left;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Salary</th>
                        <th style="padding:12px 10px;text-align:center;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Resume</th>
                        <th style="padding:12px 10px;text-align:center;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Cover Letter</th>
                        <th style="padding:12px 10px;text-align:center;color:white;font-size:0.8em;text-transform:uppercase;letter-spacing:0.06em;">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        </div>

        <!-- Footer -->
        <div style="margin-top:24px;text-align:center;color:#94a3b8;font-size:0.8em;padding:16px 0;">
            Click the download buttons above to save each resume and cover letter directly from this email.
        </div>

    </div>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------
def send_email(config: dict, html: str, packages: list[dict]):
    """Send digest email with tailored DOCX resumes attached."""
    notif = config.get("notifications", {}).get("email", {})
    sender = notif.get("sender", "")
    password = notif.get("app_password", "")
    recipient = notif.get("recipient", config["candidate"]["email"])

    if not sender or not password:
        print("  [Email] No sender/app_password configured — skipping email.")
        print("  To enable: add notifications.email section to config.yaml")
        return False

    package_count = len(packages)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Job Pipeline: {package_count} new tailored application{'s' if package_count != 1 else ''} ready"
    msg["From"] = sender
    msg["To"] = recipient

    # HTML body
    body = MIMEMultipart("alternative")
    text = f"{package_count} new tailored application packages are ready for your review.\n\nSee the attached resumes and cover letters."
    body.attach(MIMEText(text, "plain"))
    body.attach(MIMEText(html, "html"))
    msg.attach(body)

    # Attach DOCX resumes and cover letters with Content-ID headers
    # so that cid: links in the HTML body can reference them.
    attached = 0
    for i, pkg in enumerate(packages):
        output_dir = pkg.get("output_dir")
        if not output_dir:
            continue
        out = Path(output_dir)
        if not out.exists():
            continue
        for docx in out.glob("*Resume.docx"):
            with open(docx, "rb") as f:
                att = MIMEApplication(f.read(), _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document")
                att.add_header("Content-Disposition", "attachment", filename=docx.name)
                att.add_header("Content-ID", f"<resume-{i}>")
                msg.attach(att)
                attached += 1
        for cl in out.glob("*CoverLetter.md"):
            with open(cl, "rb") as f:
                att = MIMEApplication(f.read(), _subtype="octet-stream")
                att.add_header("Content-Disposition", "attachment", filename=cl.name)
                att.add_header("Content-ID", f"<coverletter-{i}>")
                msg.attach(att)
                attached += 1

    print(f"  [Email] {attached} files attached.")

    # Resolve SMTP server from config or auto-detect from sender domain
    smtp_host = notif.get("smtp_host", "")
    smtp_port = int(notif.get("smtp_port", 0))
    if not smtp_host:
        domain = sender.split("@")[-1]
        smtp_host = f"smtp.{domain}"
        smtp_port = smtp_port or 465

    try:
        if smtp_port == 587:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(sender, password)
                server.sendmail(sender, recipient, msg.as_string())
        else:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(sender, password)
                server.sendmail(sender, recipient, msg.as_string())
        print(f"  [Email] Sent to {recipient}")
        return True
    except Exception as e:
        print(f"  [Email] Failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Open HTML in browser
# ---------------------------------------------------------------------------
def open_html_report(html: str) -> Path:
    """Save HTML report and open in browser."""
    report_path = ROOT / "digest.html"
    report_path.write_text(html, encoding="utf-8")

    # Open in browser (WSL-aware)
    try:
        if "microsoft" in platform.uname().release.lower():
            # WSL — use Windows browser
            win_path = subprocess.check_output(
                ["wslpath", "-w", str(report_path)], text=True
            ).strip()
            subprocess.Popen(["cmd.exe", "/c", "start", "", win_path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(f"file://{report_path}")
    except Exception:
        pass  # Silent fail — file is still saved

    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(no_email: bool = False, no_html: bool = False, since: str | None = None):
    print("=" * 60)
    print(f"  Notify — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    config = load_config()
    tailor_state = load_tailor_state()
    notify_state = load_notify_state()
    candidate_name = config["candidate"]["name"]

    # Find packages not yet notified
    new_packages = []
    for jd_name, entry in tailor_state.get("tailored", {}).items():
        if jd_name in notify_state.get("notified", {}):
            continue
        if since:
            proc_date = entry.get("processed_at", "")[:10]
            if proc_date < since:
                continue
        pkg = get_package_info(jd_name, entry)
        new_packages.append(pkg)

    if not new_packages:
        print("\n  No new packages to notify about.")
        return

    print(f"\n  {len(new_packages)} new package(s) to notify about.\n")

    # Generate HTML digest
    html = generate_html(new_packages, candidate_name)

    # Deliver
    if not no_html:
        report_path = open_html_report(html)
        print(f"  [HTML] Saved: {report_path}")
        print(f"  [HTML] Opened in browser.")

    email_sent = False
    if not no_email:
        email_sent = send_email(config, html, new_packages)

    # Only mark as notified if the email was actually sent.
    # --no-email is treated as a preview — don't lock out future sends.
    if email_sent:
        for pkg in new_packages:
            notify_state.setdefault("notified", {})[pkg["jd_name"]] = {
                "notified_at": datetime.now(timezone.utc).isoformat(),
                "company": pkg["company"],
                "role": pkg["role"],
            }
        save_notify_state(notify_state)

        # Increment total_delivered in customer lifecycle
        try:
            config["lifecycle"]["total_delivered"] = config.get("lifecycle", {}).get("total_delivered", 0) + len(new_packages)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        except (KeyError, TypeError):
            pass  # No lifecycle section yet — skip

    # Summary
    print(f"\n  Notified about {len(new_packages)} packages:")
    for pkg in new_packages:
        print(f"    - {pkg['role']} @ {pkg['company']} (Match: {pkg['confidence']})")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Notify about tailored application packages")
    parser.add_argument("--no-email", action="store_true", help="Skip email notification")
    parser.add_argument("--no-html", action="store_true", help="Skip HTML report")
    parser.add_argument("--since", type=str, help="Only notify about packages since this date (YYYY-MM-DD)")
    args = parser.parse_args()

    run(no_email=args.no_email, no_html=args.no_html, since=args.since)
