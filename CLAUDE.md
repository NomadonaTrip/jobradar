# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Autonomous resume-tailoring and job application pipeline for Canadian job seekers (Scrum Masters, Product Managers). Fetches jobs from multiple APIs, tailors resumes/cover letters using Claude AI, and emails digest reports to candidates. Supports multiple customers with isolated state.

## Commands

```bash
# Activate virtual environment first
source .venv/bin/activate

# Full pipeline (fetch → tailor → notify) for all customers
python manage.py run-all

# Single customer pipeline
python manage.py run tayo-fasunon
python manage.py run tayo-fasunon --fetch-only
python manage.py run tayo-fasunon --no-email

# Customer management
python manage.py list
python manage.py status tayo-fasunon
python manage.py import onboarding_John_Doe_1234567890.json

# Individual phases (operate on PIPELINE_WORKDIR or root)
python fetcher.py --dry-run              # Preview jobs without saving
python fetcher.py --source remotive      # Single source
python tailor.py --limit 3               # Tailor first 3 untailored JDs
python tailor.py --jd "CGI*.md"          # Match specific JD pattern
python notify.py --no-email              # Generate HTML digest only

# Auto-import from Google Drive
python auto_import.py --dry-run
```

No test framework or linter is configured.

## Architecture

### Three-Phase Pipeline

`run_pipeline.py` orchestrates three phases as subprocesses:

1. **Fetch** (`fetcher.py`) — Queries 5 job APIs (JSearch/RapidAPI, Remotive, Adzuna, RemoteOK, The Muse), deduplicates via SHA256 fingerprints of `title|company`, saves JD markdown files to `customers/{slug}/JDs/`
2. **Tailor** (`tailor.py`) — For each new JD, calls `claude` CLI (Sonnet model) three times to generate: tailored resume (.md + .docx), cover letter, and match analysis report. Outputs go to `customers/{slug}/output/{Company}_{Role}/`
3. **Notify** (`notify.py`) — Builds HTML digest of new packages, sends via Gmail/Zoho SMTP to the candidate

### Multi-Tenant Customer Model

`manage.py` handles customer lifecycle. Each customer lives in `customers/{slug}/` with:

- `config.yaml` — search queries, API keys, notification settings, lifecycle dates
- `base_resume.md` — master resume from onboarding
- `base_cover_letter.md` — optional voice sample for cover letter generation
- `JDs/` — fetched job descriptions
- `output/` — tailored packages (resume + cover letter + report per job)
- `state.json`, `tailor_state.json`, `notify_state.json` — phase tracking

Scripts detect their working directory via `PIPELINE_WORKDIR` env var (set by `manage.py`) or fall back to the repo root:

```python
ROOT = Path(os.environ["PIPELINE_WORKDIR"]) if "PIPELINE_WORKDIR" in os.environ else Path(__file__).resolve().parent
```

### Onboarding Flow

`web/onboarding.html` → Google Apps Script (`web/apps_script.js`) → Google Drive `Onboarding_Inbox` → `auto_import.py` polls Drive → `manage.py import` creates customer directory.

### Claude AI Integration

`tailor.py` calls Claude via subprocess:

```
claude -p <prompt> --model sonnet --no-session-persistence --output-format text
```

- 5-minute timeout per call, max 2 retries
- Three separate prompts: resume tailoring, cover letter, match report
- Cover letter prompt uses candidate's base cover letter (if provided) as a voice reference
- Prompts enforce truthfulness — never fabricate experience, only reframe existing accomplishments

### State Management

JSON files (no database). Each phase writes its own state file tracking what has been processed. Fingerprint-based deduplication prevents reprocessing across sources.

### Graceful Degradation

Missing API keys skip that source. Rate limits (429) trigger backoff. Missing python-docx skips DOCX generation. Each phase can fail independently without blocking others.

## File Naming Conventions

- JDs: `{Company}_{Role}.md` (sanitized, max 80 chars)
- Outputs: `{Name}_{Company}_{Role}_Resume.md`, `_CoverLetter.md`, `_Report.md`, `_Resume.docx`
- Customer slugs: lowercase hyphenated (`tayo-fasunon`)

## Key Configuration

- `config.yaml` at root is the master template (contains API keys — gitignored)
- Per-customer `config.yaml` in `customers/{slug}/` overrides search, notification, and lifecycle settings
- `service_account.json` required for Google Drive auto-import (not in repo)
- `.claude/settings.local.json` defines Claude CLI permission sandbox

## Memory Management

When you discover or establish something that would be valuable in future sessions — architectural decisions, bug fixes, gotchas, patterns, environment quirks —
immediately append it to .claude/MEMORY.md

Don't wait for me to ask. Don't wait for session end.

Keep entries short: date, what, why. Read this file at the start of every session.

## Workflow Orchestration

### 1. Plan Mode Default

- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately – don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy

- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop

- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done

- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)

- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes – don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing

- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests – then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First:** Write plan to `tasks/todo.md` with checkable items
2. **Verify Plans:** Review plan before starting implementation
3. **Track Progress:** Mark items complete as you go
4. **Explain Changes:** High-level summary at each step
5. **Document Results:** Add review section to `tasks/todo.md`
6. **Capture Lessons:** Update `tasks/lessons.md` after corrections

## Core Principles

- **Simplicity First:** Make every change as simple as possible. Impact minimal code.
- **No Laziness:** Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact:** Changes should only touch what's necessary. Avoid introducing bugs.
