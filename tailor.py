#!/usr/bin/env python3
"""
Auto-Tailor — Phase 2 of the autonomous resume-tailoring pipeline.

For each new JD in the JDs/ folder, generates:
  1. Tailored resume (Markdown + DOCX)
  2. Cover letter (Markdown)
  3. Match analysis report (Markdown)

Uses the `claude` CLI (your existing subscription) as the AI backend.

Usage:
    python tailor.py                    # process all untailored JDs
    python tailor.py --limit 3          # process only 3 JDs
    python tailor.py --jd "CGI*.md"     # process specific JD(s) by glob
    python tailor.py --dry-run          # preview what would be processed
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------
ROOT = Path(os.environ["PIPELINE_WORKDIR"]) if "PIPELINE_WORKDIR" in os.environ else Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
TAILOR_STATE_FILE = ROOT / "tailor_state.json"


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


def save_tailor_state(state: dict):
    with open(TAILOR_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# DOCX generation from Markdown
# ---------------------------------------------------------------------------
def md_to_docx(md_path: Path, docx_path: Path):
    """Convert a Markdown resume to a simple DOCX file using python-docx."""
    try:
        from docx import Document
        from docx.shared import Pt, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        print(f"    [DOCX] python-docx not installed, skipping DOCX generation.")
        return

    doc = Document()

    # Set narrow margins
    for section in doc.sections:
        section.top_margin = Inches(0.6)
        section.bottom_margin = Inches(0.6)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)

    md_text = md_path.read_text(encoding="utf-8")

    for line in md_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # H1 — Name
        if stripped.startswith("# "):
            p = doc.add_heading(stripped[2:], level=0)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # H2 — Section headers
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=1)
        # H3 — Job titles
        elif stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=2)
        # Bullet points
        elif stripped.startswith("- "):
            doc.add_paragraph(stripped[2:], style="List Bullet")
        # Bold lines (like dates, certifications)
        elif stripped.startswith("**") and stripped.endswith("**"):
            p = doc.add_paragraph()
            run = p.add_run(stripped.strip("*"))
            run.bold = True
            run.font.size = Pt(10)
        # Horizontal rules
        elif stripped == "---":
            continue
        # Regular text
        else:
            # Strip markdown bold/italic for plain text
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)
            clean = re.sub(r"\*(.+?)\*", r"\1", clean)
            clean = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", clean)
            if clean:
                p = doc.add_paragraph(clean)
                p.paragraph_format.space_after = Pt(2)

    doc.save(str(docx_path))


# ---------------------------------------------------------------------------
# Claude CLI integration
# ---------------------------------------------------------------------------
def call_claude(prompt: str, max_retries: int = 2) -> str:
    """Call the claude CLI in non-interactive mode and return the response."""
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--model", "sonnet",
                    "--no-session-persistence",
                    "--output-format", "text",
                ],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout per call
                cwd=str(ROOT),
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            elif result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                print(f"    [Claude] Attempt {attempt+1} failed (exit {result.returncode}): {err[:200]}")
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                return ""
        except subprocess.TimeoutExpired:
            print(f"    [Claude] Attempt {attempt+1} timed out.")
            if attempt < max_retries:
                time.sleep(5)
                continue
            return ""
    return ""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def build_resume_prompt(base_resume: str, jd_text: str, candidate_name: str) -> str:
    return f"""You are an expert resume writer specializing in ATS optimization and strategic keyword matching for senior Scrum Master and Product Manager roles.

TASK: Tailor the candidate's base resume to match the target job description. Output ONLY the tailored resume in Markdown format — no commentary, no explanations, no preamble.

RULES:
1. TRUTHFULNESS IS PARAMOUNT — never fabricate experience, metrics, or skills. Only reframe existing experience using JD-aligned language.
2. Mirror the JD's exact terminology wherever truthful (e.g., if JD says "high-performing teams" use that phrase, if JD says "processing components" use that phrase).
3. Reorder bullet points so the most JD-relevant ones come first under each role.
4. Adjust the Professional Summary to directly address the target role's key requirements.
5. Keep all quantified metrics (78%, 30%, $15M, etc.) — they are powerful differentiators.
6. Maintain the same resume structure: Name → Professional Summary → Certifications → Key Skills → Professional Experience → Education.
7. In Key Skills, add any JD-mentioned skills the candidate genuinely has but hasn't listed.
8. Keep to 1-2 pages. Be concise but impactful.
9. Do NOT include the company name or job title in the resume header — it should work as a general submission.

BASE RESUME:
---
{base_resume}
---

TARGET JOB DESCRIPTION:
---
{jd_text}
---

Output the tailored resume in Markdown now:"""


def build_cover_letter_prompt(base_resume: str, jd_text: str, tailored_resume: str, candidate_name: str, company: str, role: str, cover_letter_voice: str = "") -> str:
    voice_section = ""
    if cover_letter_voice:
        voice_section = f"""
VOICE REFERENCE — Match this candidate's authentic writing style, tone, and personality.
Study the sentence structure, warmth level, formality, and storytelling approach in the sample below.
Mirror how they open letters, transition between ideas, and express enthusiasm. Do NOT copy content — only match the voice.
---
{cover_letter_voice}
---

"""
    return f"""You are writing a cover letter for {candidate_name} applying to the {role} position at {company}.
{voice_section}VOICE & TONE:
- {"Match the candidate's voice from the reference above" if cover_letter_voice else "Professional but warm and human"} — not robotic or generic
- Show genuine interest in the company's mission and culture
- Weave in specific achievements with metrics
- Keep it to 3-4 paragraphs, under 400 words
- Address "Dear Hiring Manager" unless a specific name is in the JD
- End with a confident but not arrogant call to action

STRUCTURE:
1. "Dear Hiring Manager," (or specific name if in JD)
2. Opening paragraph — Hook connecting candidate's passion/experience to the company's mission. Reference something specific about the company.
3. Body (1-2 paragraphs) — 3-4 strongest achievements that directly map to JD requirements. Use specific numbers.
4. Closing paragraph — Express enthusiasm, mention willingness to discuss further.
5. "Sincerely," followed by candidate name.

IMPORTANT: Do NOT include the candidate's address, contact info, or resume header. Start directly with the greeting.

CANDIDATE'S RESUME:
---
{tailored_resume}
---

JOB DESCRIPTION:
---
{jd_text}
---

Output ONLY the cover letter text in Markdown format — no commentary:"""


def build_report_prompt(base_resume: str, jd_text: str, tailored_resume: str, company: str, role: str) -> str:
    return f"""You are a career strategist analyzing the fit between a candidate and a target role.

Generate a Match Analysis Report in Markdown with these exact sections:

## Target Role Summary
Company, position, location, salary (if mentioned), key details from JD.

## Content Mapping Summary
Table showing: total bullets, direct matches (%), transferable matches (%), adjacent matches (%), gaps (%).
Overall JD Coverage percentage.

## Key Reframings Applied
Table: Original phrasing → Reframed phrasing → Reason for change.
Note that all reframings are truthful.

## Gap Analysis
### Identified Gaps
Table: Gap | Severity (Low/Medium/High) | Mitigation strategy.
### Strengths vs. JD
Table: JD Requirement | Resume Evidence | Confidence (%).

## Key Differentiators
Numbered list of 4-6 things that make this candidate stand out for THIS specific role.

## Recommendations for Interview Prep
### Stories to Prepare
5-7 specific STAR-format stories to prepare, drawn from the resume.
### Questions to Expect
5-6 likely interview questions specific to this role.
### Gaps to Address Proactively
How to spin each gap positively in conversation.

BASE RESUME (before tailoring):
---
{base_resume}
---

TAILORED RESUME (after tailoring):
---
{tailored_resume}
---

JOB DESCRIPTION:
---
{jd_text}
---

Output the full report in Markdown now:"""


# ---------------------------------------------------------------------------
# Process a single JD
# ---------------------------------------------------------------------------
def process_jd(jd_path: Path, base_resume: str, config: dict, dry_run: bool = False, cover_letter_voice: str = "") -> bool:
    """Process a single JD file: generate tailored resume, cover letter, and report."""
    jd_text = jd_path.read_text(encoding="utf-8")
    candidate = config["candidate"]
    candidate_name = candidate["name"]

    # Extract company and role from JD content or filename
    company = "Unknown"
    role = "Unknown"

    # Strategy 1: Parse the H1 heading (JSearch-fetched JDs use "# Role — Company")
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

    # Strategy 2: Look for Company in the metadata table
    if company == "Unknown":
        for line in jd_text.split("\n"):
            if "**Company**" in line and "|" in line:
                company = line.split("|")[2].strip().strip("*")
                break

    # Strategy 3: Fall back to filename (format: "Company_Role.md")
    if company == "Unknown" or role == "Unknown":
        stem = jd_path.stem
        parts = stem.split("_", 1)
        if company == "Unknown":
            company = parts[0].replace("-", " ") if parts else "Unknown"
        if role == "Unknown" and len(parts) > 1:
            role = parts[1].replace("_", " ")

    company = company.strip()
    role = role.strip()

    print(f"\n  Processing: {role} @ {company}")
    print(f"  JD file: {jd_path.name}")

    if dry_run:
        print(f"  [DRY RUN] Would generate: resume, cover letter, report")
        return True

    # Create output directory
    safe_name = re.sub(r'[<>:"/\\|?*]', "", f"{company}_{role}")
    safe_name = re.sub(r"\s+", "_", safe_name)[:100]
    output_dir = ROOT / "output" / safe_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save JD copy to output
    (output_dir / "jd.md").write_text(jd_text, encoding="utf-8")

    # Step 1: Tailored resume
    print(f"  [1/3] Generating tailored resume...")
    resume_prompt = build_resume_prompt(base_resume, jd_text, candidate_name)
    tailored_resume = call_claude(resume_prompt)
    if not tailored_resume:
        print(f"  ERROR: Failed to generate tailored resume. Skipping.")
        return False

    resume_md_path = output_dir / f"{candidate_name.replace(' ', '_')}_{safe_name}_Resume.md"
    resume_md_path.write_text(tailored_resume, encoding="utf-8")
    print(f"    Saved: {resume_md_path.name}")

    # Generate DOCX
    docx_path = resume_md_path.with_suffix(".docx")
    md_to_docx(resume_md_path, docx_path)
    print(f"    Saved: {docx_path.name}")

    # Step 2: Cover letter
    print(f"  [2/3] Generating cover letter...")
    cl_prompt = build_cover_letter_prompt(base_resume, jd_text, tailored_resume, candidate_name, company, role, cover_letter_voice)
    cover_letter = call_claude(cl_prompt)
    if cover_letter:
        cl_path = output_dir / f"{candidate_name.replace(' ', '_')}_{safe_name}_CoverLetter.md"
        cl_path.write_text(cover_letter, encoding="utf-8")
        print(f"    Saved: {cl_path.name}")
    else:
        print(f"    WARNING: Cover letter generation failed.")

    # Step 3: Match report
    print(f"  [3/3] Generating match report...")
    report_prompt = build_report_prompt(base_resume, jd_text, tailored_resume, company, role)
    report = call_claude(report_prompt)
    if report:
        report_path = output_dir / f"{candidate_name.replace(' ', '_')}_{safe_name}_Report.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"    Saved: {report_path.name}")
    else:
        print(f"    WARNING: Report generation failed.")

    return True


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def get_unprocessed_jds(jd_dir: Path, state: dict, jd_glob: str | None = None) -> list[Path]:
    """Find JD files that haven't been tailored yet."""
    all_jds = sorted(jd_dir.glob("*.md"))

    if jd_glob:
        all_jds = [p for p in all_jds if fnmatch.fnmatch(p.name, jd_glob)]

    unprocessed = []
    for jd in all_jds:
        if jd.name not in state.get("tailored", {}):
            unprocessed.append(jd)

    return unprocessed


def run(dry_run: bool = False, limit: int | None = None, jd_glob: str | None = None):
    print("=" * 60)
    print(f"  Auto-Tailor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    config = load_config()
    state = load_tailor_state()

    # Load base resume
    base_resume_path = ROOT / config["candidate"]["base_resume"]
    if not base_resume_path.exists():
        print(f"ERROR: Base resume not found: {base_resume_path}")
        sys.exit(1)
    base_resume = base_resume_path.read_text(encoding="utf-8")

    # Load base cover letter for voice matching (optional)
    cover_letter_voice = ""
    cl_voice_file = config["candidate"].get("base_cover_letter")
    if cl_voice_file:
        cl_voice_path = ROOT / cl_voice_file
        if cl_voice_path.exists():
            cover_letter_voice = cl_voice_path.read_text(encoding="utf-8")
            print(f"  Voice reference: {cl_voice_path.name}")

    # Find unprocessed JDs
    jd_dir = ROOT / config["output"]["jd_directory"]
    unprocessed = get_unprocessed_jds(jd_dir, state, jd_glob)

    if limit:
        unprocessed = unprocessed[:limit]

    print(f"\n  Base resume: {base_resume_path.name}")
    print(f"  JDs to process: {len(unprocessed)}")
    if not unprocessed:
        print("\n  No new JDs to tailor. Run fetcher.py first or use --jd to specify a pattern.")
        return

    print(f"  Output directory: {ROOT / 'output'}/")
    print()

    # Process each JD
    success = 0
    failed = 0
    for i, jd_path in enumerate(unprocessed, 1):
        print(f"  [{i}/{len(unprocessed)}]", end="")
        ok = process_jd(jd_path, base_resume, config, dry_run=dry_run, cover_letter_voice=cover_letter_voice)

        if ok and not dry_run:
            state["tailored"][jd_path.name] = {
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "output_dir": str(ROOT / "output"),
            }
            save_tailor_state(state)
            success += 1
        elif ok and dry_run:
            success += 1
        else:
            failed += 1

    # Summary
    print("\n" + "=" * 60)
    print(f"  TAILORING COMPLETE")
    print("=" * 60)
    print(f"  Processed: {success + failed}")
    print(f"  Success:   {success}")
    print(f"  Failed:    {failed}")
    if not dry_run:
        print(f"  Output at: {ROOT / 'output'}/")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-tailor resumes to fetched JDs")
    parser.add_argument("--dry-run", action="store_true", help="Preview without generating")
    parser.add_argument("--limit", type=int, help="Max number of JDs to process")
    parser.add_argument("--jd", type=str, help="Glob pattern to match specific JD files (e.g. 'CGI*.md')")
    args = parser.parse_args()

    run(dry_run=args.dry_run, limit=args.limit, jd_glob=args.jd)
