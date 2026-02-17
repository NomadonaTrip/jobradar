# Product Requirements Document (PRD)

## ResumeGen — Autonomous Resume Tailoring & Job Application Pipeline

| Field | Detail |
|-------|--------|
| **Product Name** | ResumeGen |
| **Version** | 1.0 |
| **Author** | Tayo Fasunon |
| **Date** | 2026-02-14 |
| **Status** | In Production (Early Customers) |

---

## 1. Executive Summary

ResumeGen is an autonomous pipeline that finds relevant job postings, tailors resumes and cover letters to each one using AI, and delivers ready-to-submit application packages directly to candidates via email. It eliminates the most time-consuming bottleneck in job searching: customizing application materials for every posting.

The system currently serves Canadian job seekers targeting Scrum Master, Product Manager, and Agile leadership roles. It supports multiple customers with isolated profiles, preferences, and lifecycle management.

---

## 2. Problem Statement

### The Job Seeker's Dilemma

A serious job search in the Canadian tech market requires a candidate to:

1. **Monitor 5+ job boards daily** — postings appear and disappear within days.
2. **Read and evaluate each posting** — determining fit against their background.
3. **Customize their resume for every application** — mirroring the employer's language, reordering experience by relevance, and highlighting the right metrics.
4. **Write a unique cover letter each time** — connecting their story to the specific role.
5. **Track what they've applied to** — avoiding duplicates and managing follow-ups.

This process takes 45-90 minutes per application. A candidate applying to 5 roles per day spends 4-7 hours on mechanics, leaving almost no time for networking, interview prep, or skill-building.

### What Exists Today

- **Job aggregators** (Indeed, LinkedIn) solve discovery but not tailoring.
- **Resume builders** (Zety, Novoresume) provide templates but not per-job customization.
- **AI writing tools** (ChatGPT, Jasper) can draft content but require manual prompting for every job, have no pipeline, and frequently fabricate experience.

No existing product connects the full loop: discover jobs, tailor materials with truthfulness constraints, and deliver packaged applications.

---

## 3. Product Vision

**Enable any job seeker to wake up to a daily email containing tailored, truthful, ready-to-submit resumes and cover letters for every relevant new posting — without lifting a finger.**

### Design Principles

1. **Truthfulness above all** — The system never fabricates experience. It reframes existing accomplishments using the employer's language, but every claim traces back to the candidate's real background.
2. **Graceful degradation** — Every component can fail independently without blocking the rest. A missing API key skips one source. A failed AI call skips one job. The pipeline always moves forward.
3. **Tenant isolation** — Each customer's data, configuration, and state are fully separated. No cross-contamination.
4. **Candidate-in-the-loop** — The system produces materials for review. The candidate decides what to submit. It is an assistant, not an autopilot.

---

## 4. Target Users

### Primary Persona: Mid-Career Canadian Professional

- **Role types:** Scrum Master, Product Manager, Product Owner, Agile Coach, Delivery Lead, Technical PM
- **Experience level:** 5-15 years
- **Geography:** Canada (Ontario focus, expanding)
- **Situation:** Actively job searching or passively monitoring the market
- **Pain point:** Spending hours per day on application mechanics instead of preparation and networking

### Secondary Persona: Career Changer

- Transitioning into PM/Scrum from adjacent roles (e.g., developer, business analyst)
- Needs the system to strategically reframe transferable experience
- Benefits from the match analysis report, which identifies gaps and suggests mitigation

### Operator Persona: Pipeline Administrator

- Manages customer onboarding, lifecycle, and system health
- Runs the daily pipeline (currently manual or cron-scheduled)
- Monitors delivery counts and customer expiry dates

---

## 5. System Architecture

### 5.1 High-Level Pipeline

```
ONBOARD → FETCH → TAILOR → NOTIFY
```

| Phase | Input | Output | Frequency |
|-------|-------|--------|-----------|
| **Onboard** | Web form submission | Customer profile + base resume | One-time |
| **Fetch** | Search queries + API keys | Job description markdown files | Daily |
| **Tailor** | JD + base resume | Tailored resume, cover letter, match report | Per new JD |
| **Notify** | Tailored packages | HTML digest email with DOCX attachments | After tailoring |

### 5.2 Component Overview

#### Onboarding (`web/onboarding.html` + `web/apps_script.js` + `auto_import.py`)

A six-step web form collects candidate information and feeds it into the pipeline:

- **Step 1:** Identity — name, email, phone, location, LinkedIn URL
- **Step 2:** Resume & Voice — file upload or paste for resume (supports .txt, .md), plus an optional cover letter sample for voice matching
- **Step 3:** Search preferences — target roles, locations, salary floor, work arrangement (remote/hybrid/onsite), company exclusions
- **Step 4:** Discovery interview — eight structured questions designed to surface undocumented achievements (team sizes managed, budgets owned, metrics improved, certifications held, toughest challenges overcome, tools mastered, industries worked in, hidden strengths)
- **Step 5:** Review — candidate confirms all data before submission
- **Step 6:** Confirmation — success message with next-steps guidance

**Data flow:** The form POSTs to a Google Apps Script webhook, which saves the submission as a JSON file in a Google Drive inbox folder, logs it to a Google Sheet, and emails the admin. A Python import script (`auto_import.py`) polls the Drive inbox, downloads new submissions, and creates the customer directory with all necessary config and state files.

#### Job Fetcher (`fetcher.py`)

Queries five job APIs and saves deduplicated results as structured markdown files.

| Source | Auth Required | Coverage | Strengths |
|--------|--------------|----------|-----------|
| **JSearch** (RapidAPI) | API key | Global (Google for Jobs aggregator) | Broadest coverage, salary data |
| **Remotive** | None | Remote-only | Curated, high-quality remote roles |
| **Adzuna** | App ID + Key | 16+ countries | Strong Canadian market, salary data |
| **RemoteOK** | None | Remote-only | Tech-focused, single JSON feed |
| **The Muse** | None | Curated companies | Good for PM/leadership roles |

**Deduplication:** Each job is fingerprinted using SHA-256 of `title|company` (truncated to 16 characters). The fingerprint is checked against `state.json` to prevent reprocessing across sources and runs.

**Filtering:** Jobs can be filtered by minimum salary, required keywords, excluded keywords, excluded companies, and Canadian province/location.

**Output format:** Each job is saved as a markdown file containing a metadata table (company, location, type, remote status, posted date, source, apply URL, salary) followed by the full job description and qualifications.

#### Resume Tailor (`tailor.py`)

For each new JD, calls Claude AI (Sonnet model via CLI) three times to produce:

1. **Tailored Resume** — Mirrors the JD's exact terminology. Reorders experience bullets by relevance to the target role. Preserves all real metrics (percentages, dollar amounts, team sizes). Output in both Markdown and DOCX formats.

2. **Cover Letter** — Written in the candidate's authentic voice (if a base cover letter was provided during onboarding) or a professional warm tone. Opens with a compelling hook tied to the specific company/role. Highlights 3-4 achievement stories with quantified impact. Closes with clear enthusiasm and next steps.

3. **Match Analysis Report** — A strategic document for the candidate containing:
   - Target role summary and key requirements
   - Content mapping (which resume bullets address which JD requirements)
   - Key reframings applied (how experience was repositioned)
   - Gap analysis (what the JD asks for that the candidate lacks, with mitigation strategies)
   - Key differentiators (what makes this candidate stand out)
   - Interview preparation (likely questions, STAR stories to prepare)

**Truthfulness enforcement:** Every prompt includes explicit instructions: *"NEVER fabricate experience — only reframe existing accomplishments using JD-aligned language."* All metrics, dates, company names, and role titles must come directly from the candidate's base resume.

**Resilience:** 5-minute timeout per Claude call, up to 2 retries with 5-second backoff. DOCX generation fails gracefully if `python-docx` is not installed.

#### Email Notifier (`notify.py`)

Builds a responsive HTML digest and delivers it with attachments.

**Email contents:**
- Summary statistics (number of matches, resumes ready, cover letters, apply links)
- Table of new opportunities with: company, role, location, match confidence (color-coded), salary range, download buttons for resume/cover letter, and direct apply links
- DOCX resumes and Markdown cover letters as file attachments

**SMTP support:** Auto-detects server from sender domain. Supports both STARTTLS (port 587) and SSL (port 465). Tested with Gmail and Zoho Mail.

### 5.3 Multi-Tenant Customer Model

Each customer is isolated under `customers/{slug}/`:

```
customers/tayo-fasunon/
  config.yaml          # Search queries, API keys, notification settings, lifecycle
  base_resume.md       # Master resume (source of truth for tailoring)
  base_cover_letter.md # Sample cover letter for voice matching (optional)
  discovery_notes.md   # Onboarding insights and hidden achievements
  onboarding.json      # Raw onboarding submission
  state.json           # Fetcher state (seen jobs by fingerprint)
  tailor_state.json    # Tailor state (processed JDs)
  notify_state.json    # Notify state (sent emails)
  JDs/                 # Fetched job descriptions (markdown)
  output/              # Tailored packages
    CGI_Scrum_Master/
      jd.md
      Tayo_Fasunon_CGI_Scrum_Master_Resume.md
      Tayo_Fasunon_CGI_Scrum_Master_Resume.docx
      Tayo_Fasunon_CGI_Scrum_Master_CoverLetter.md
      Tayo_Fasunon_CGI_Scrum_Master_Report.md
```

**Lifecycle management:**
- `started_at` — When the customer was onboarded
- `expires_at` — When the service window closes (default: 20 days)
- `daily_limit` — Maximum JDs to tailor per run
- `total_delivered` — Running count of packages sent

The `manage.py run-all` command automatically skips expired customers.

### 5.4 State Management

All state is stored in JSON files (no database). Each pipeline phase writes its own state file, tracking what has been processed. This design was chosen for:

- **Simplicity** — No database server to install or manage
- **Portability** — The entire customer directory is self-contained and can be archived or moved
- **Debuggability** — State can be inspected and edited with a text editor
- **Idempotency** — Each run picks up where the last left off, processing only new items

---

## 6. Functional Requirements

### 6.1 Onboarding

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| ON-1 | Multi-step web form collects candidate identity, resume, search preferences, and discovery answers | P0 | Done |
| ON-2 | Form submissions are saved to Google Drive as JSON for automated import | P0 | Done |
| ON-3 | Admin is notified via email when a new submission arrives | P1 | Done |
| ON-4 | Submissions are logged to a Google Sheet for historical tracking | P2 | Done |
| ON-5 | `auto_import.py` polls Drive inbox and creates customer directories automatically | P0 | Done |
| ON-6 | Duplicate submissions are detected and handled gracefully | P1 | Done |
| ON-7 | Discovery questions surface undocumented achievements for richer tailoring | P1 | Done |
| ON-8 | Optional base cover letter collected during onboarding for voice matching | P1 | Done |

### 6.2 Job Fetching

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FE-1 | Query at least 3 independent job APIs per run | P0 | Done (5 sources) |
| FE-2 | Deduplicate jobs across sources using stable fingerprints | P0 | Done |
| FE-3 | Save each job as a structured Markdown file with metadata table | P0 | Done |
| FE-4 | Support configurable search queries, locations, and date ranges | P0 | Done |
| FE-5 | Filter by minimum salary, required/excluded keywords, and excluded companies | P1 | Done |
| FE-6 | Handle rate limits (429) and auth failures (403) with graceful backoff | P0 | Done |
| FE-7 | Skip sources with missing API keys without blocking the pipeline | P0 | Done |
| FE-8 | Support `--dry-run` mode to preview without saving | P1 | Done |
| FE-9 | Support `--source` flag to run a single source in isolation | P2 | Done |

### 6.3 Resume Tailoring

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| TA-1 | Generate a tailored resume for each new JD using Claude AI | P0 | Done |
| TA-2 | Generate a personalized cover letter for each new JD | P0 | Done |
| TA-3 | Generate a match analysis report with gap analysis and interview prep | P1 | Done |
| TA-4 | Enforce truthfulness: never fabricate experience, only reframe | P0 | Done |
| TA-5 | Output resume in both Markdown and DOCX formats | P0 | Done |
| TA-6 | Mirror JD terminology in resume (ATS keyword optimization) | P0 | Done |
| TA-7 | Preserve all real metrics from the base resume | P0 | Done |
| TA-8 | Support `--limit` flag to cap the number of JDs processed per run | P1 | Done |
| TA-9 | Support `--jd` glob pattern to target specific JDs | P2 | Done |
| TA-10 | Retry failed Claude calls up to 2 times with backoff | P1 | Done |
| TA-11 | Cover letter generation uses candidate's voice from base cover letter sample when available | P1 | Done |

### 6.4 Notification

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| NO-1 | Build a responsive HTML email digest summarizing new packages | P0 | Done |
| NO-2 | Attach DOCX resumes and cover letters to the email | P0 | Done |
| NO-3 | Color-code match confidence scores (green/amber/red) | P1 | Done |
| NO-4 | Include direct "Apply" links to the original posting | P1 | Done |
| NO-5 | Send via SMTP with TLS (supports Gmail, Zoho) | P0 | Done |
| NO-6 | Support `--no-email` mode for local preview only | P1 | Done |
| NO-7 | Track delivered packages to prevent duplicate notifications | P0 | Done |
| NO-8 | Open HTML digest in local browser (WSL-aware) | P2 | Done |
| NO-9 | Increment `lifecycle.total_delivered` counter on successful send | P1 | Done |

### 6.5 Customer Management

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| CM-1 | Create customer from onboarding JSON with isolated directory structure | P0 | Done |
| CM-2 | List all customers with summary stats | P1 | Done |
| CM-3 | Show detailed status for a single customer | P1 | Done |
| CM-4 | Run pipeline for a single customer | P0 | Done |
| CM-5 | Run pipeline for all active (non-expired) customers | P0 | Done |
| CM-6 | Renew customer by extending expiry date | P1 | Done |
| CM-7 | Lifecycle tracking with expiry dates and delivery counts | P1 | Done |
| CM-8 | Support per-customer config overrides (queries, sources, filters) | P1 | Done |

---

## 7. Non-Functional Requirements

### 7.1 Performance

- **Fetching:** Complete all 5 API sources within 5 minutes (300s timeout)
- **Tailoring:** Process each JD within 5 minutes (Claude call timeout). Default batch limit of 5 JDs per run.
- **Notification:** Build and send digest within 60 seconds

### 7.2 Reliability

- Each pipeline phase can fail independently without blocking other phases
- State is persisted after each operation (crash-safe)
- Missing dependencies (API keys, python-docx) degrade gracefully
- Retries on transient failures (Claude timeouts, API rate limits)

### 7.3 Scalability

- JSON state files support approximately 50 concurrent customers before file I/O becomes a bottleneck
- No shared state between customers (horizontally partitioned)
- Pipeline runs sequentially per customer but could be parallelized across customers

### 7.4 Security

- API keys stored in gitignored config files, never logged
- Resume content processed locally and via Claude API only (no third-party storage)
- Email sent via TLS-encrypted SMTP
- Google Drive access via service account (scoped to Drive API only)
- No user-facing authentication (admin-operated system)

### 7.5 Portability

- Runs on Linux, macOS, and Windows (WSL)
- WSL-aware browser opening for digest preview
- Python 3.x with minimal dependencies (5 packages in `requirements.txt`)
- No database server required

---

## 8. Data Model

### 8.1 Onboarding Submission (JSON)

```
firstName, lastName, email, phone, location, linkedin
resumeText (pasted) | resumeFile (uploaded)
coverLetterText — optional sample cover letter for voice matching
roles[] — target job titles
locations[] — target geographies
exclude[] — companies to skip
minSalary — salary floor
workArrangement — remote | hybrid | onsite | any
prefNotes — free-text preferences
discovery {
  teamSize, budget, metrics, certs, challenge,
  tools, industries, hidden
}
submittedAt — ISO 8601 timestamp
```

### 8.2 Job Description (Markdown)

```
H1: Role — Company
Metadata table: Company, Location, Type, Remote, Posted, Source, Apply URL
Salary (if available)
Full job description (HTML stripped to plain text)
Qualifications, Responsibilities, Benefits sections
```

### 8.3 Tailored Package (per JD)

```
jd.md — Copy of original JD
{Name}_{Company}_{Role}_Resume.md — Tailored resume (Markdown)
{Name}_{Company}_{Role}_Resume.docx — Tailored resume (Word)
{Name}_{Company}_{Role}_CoverLetter.md — Personalized cover letter
{Name}_{Company}_{Role}_Report.md — Match analysis and interview prep
```

### 8.4 State Files

| File | Scope | Key | Tracks |
|------|-------|-----|--------|
| `state.json` | Fetcher | Job fingerprint (SHA-256) | Seen jobs with title, company, source, timestamp |
| `tailor_state.json` | Tailor | JD filename | Processed JDs with timestamp and output directory |
| `notify_state.json` | Notify | JD filename | Notified packages with timestamp, company, role |

---

## 9. Integration Points

### 9.1 External APIs (Job Sources)

| API | Protocol | Auth Method | Rate Limits |
|-----|----------|-------------|-------------|
| JSearch (RapidAPI) | REST/HTTPS | API key in header | 1 req/sec, quota-based |
| Remotive | REST/HTTPS | None | ~2 req/min |
| Adzuna | REST/HTTPS | App ID + Key in URL | Generous (free tier) |
| RemoteOK | REST/HTTPS | None (User-Agent required) | Generous |
| The Muse | REST/HTTPS | None | Generous |

### 9.2 Claude AI (Anthropic)

- **Interface:** Claude CLI (`claude` binary) called via subprocess
- **Model:** Sonnet
- **Calls per JD:** 3 (resume, cover letter, report)
- **Timeout:** 5 minutes per call
- **Retries:** Up to 2 with 5-second backoff
- **Flags:** `--no-session-persistence --output-format text`

### 9.3 Google Workspace

- **Google Drive:** Service account access for onboarding JSON inbox/processed folders
- **Google Sheets:** Submission log (created by Apps Script)
- **Google Apps Script:** Webhook endpoint for form submissions

### 9.4 Email (SMTP)

- **Providers tested:** Gmail (App Passwords), Zoho Mail
- **Protocol:** SMTP with STARTTLS (port 587) or SMTP_SSL (port 465)
- **Content:** MIME multipart with HTML body + file attachments

---

## 10. User Journeys

### 10.1 New Customer Onboarding

```
1. Candidate visits onboarding URL
2. Fills out 6-step form (identity, resume, preferences, discovery)
3. Reviews and submits
4. Google Apps Script saves JSON to Drive + emails admin
5. Admin runs auto_import.py (or waits for cron)
6. System creates customer directory with config, resume, and state files
7. Admin runs first pipeline: python manage.py run {slug}
8. Candidate receives first email digest within ~30 minutes
```

### 10.2 Daily Pipeline Run

```
1. Cron triggers: python manage.py run-all
2. For each active customer:
   a. FETCH: Query 5 APIs → deduplicate → save new JDs
   b. TAILOR: For each new JD → call Claude 3x → save resume + letter + report
   c. NOTIFY: Build HTML digest → send email with attachments
3. Expired customers are skipped automatically
4. Failures are logged but don't block other customers
```

### 10.3 Candidate Receives Digest

```
1. Email arrives with subject: "Job Pipeline: N new tailored application(s) ready"
2. HTML body shows summary stats and a table of opportunities
3. Each row shows: company, role, location, match %, salary, download links
4. Candidate reviews match report to understand fit and gaps
5. Candidate downloads DOCX resume and cover letter
6. Candidate applies directly using the "Apply" link
7. Candidate uses interview prep section from the report to prepare
```

---

## 11. AI Prompt Strategy

### 11.1 Resume Tailoring Prompt

The resume prompt instructs Claude to:
- Mirror the JD's exact terminology throughout the resume
- Reorder experience bullets so the most relevant appear first
- Preserve every real metric (percentages, dollar amounts, team sizes, timelines)
- Add JD-aligned keywords naturally without fabricating experience
- Output clean Markdown with no commentary or preamble

### 11.2 Cover Letter Prompt

The cover letter prompt instructs Claude to:
- **Match the candidate's authentic voice** when a base cover letter sample is available — studying their sentence structure, warmth level, formality, and storytelling approach
- Fall back to a professional but warm generic tone when no voice sample exists
- Open with a hook specific to the company and role
- Highlight 3-4 achievement stories with quantified impact
- Close with clear enthusiasm and a call to action
- Avoid generic filler and cliched phrases
- Mirror the voice, not the content — the sample is for style reference only

### 11.3 Match Report Prompt

The report prompt instructs Claude to produce:
- A target role summary identifying key requirements
- A content mapping showing which resume bullets address which JD requirements
- A list of key reframings (how experience was repositioned)
- A gap analysis identifying missing qualifications with mitigation strategies
- Key differentiators that make the candidate stand out
- Interview preparation with likely questions and STAR stories

### 11.4 Truthfulness Constraints (All Prompts)

Every prompt includes explicit guardrails:
- **Never fabricate** experience, skills, certifications, or metrics
- **Never invent** companies, roles, or dates not present in the base resume
- **Only reframe** existing accomplishments using JD-aligned language
- **Preserve** all original metrics exactly as stated

---

## 12. Current Limitations

| Limitation | Impact | Mitigation Path |
|-----------|--------|-----------------|
| No database — JSON state files | Scales to ~50 customers | Migrate to SQLite or Postgres if growth demands |
| Sequential processing | One customer at a time | Parallelize with multiprocessing or task queue |
| No resume versioning | Base resume overwrites on re-onboard | Add version history or Git tracking |
| No candidate dashboard | Candidates can't self-serve | Build web UI for package browsing and status |
| Manual operator actions | Admin runs pipeline and imports | Full cron automation + self-service onboarding |
| No duplicate JD content detection | Similar JDs from different sources with different titles pass dedup | Add semantic similarity check |
| No application tracking | No feedback loop on what candidates actually submitted | Add apply-tracking integration |
| Claude CLI dependency | Requires Claude CLI installed on host | Migrate to Anthropic API SDK for portability |
| Local filesystem only | No cloud backup or remote access | Add S3/GCS sync for production deployment |

---

## 13. Future Roadmap

### Phase 2: Full Automation
- Daily cron scheduling with monitoring and alerting
- Self-service onboarding (hosted form with auto-provisioning)
- Lifecycle enforcement (daily limits, auto-expiry notifications)

### Phase 3: Candidate Experience
- Web dashboard for candidates to browse and download their packages
- Application tracking (mark jobs as applied, track responses)
- Preference tuning (adjust search criteria via dashboard)

### Phase 4: Scale & Intelligence
- Additional job sources (Indeed, LinkedIn, Glassdoor)
- Semantic deduplication (detect similar JDs with different titles)
- Learning from outcomes (which tailoring patterns lead to interviews)
- Resume A/B testing (multiple tailoring strategies per JD)
- Batch parallelization (process multiple customers concurrently)

### Phase 5: Platform
- API-first architecture for third-party integrations
- White-label capability for career coaches and recruiters
- Support for additional markets beyond Canada
- Support for additional role families beyond PM/Scrum

---

## 14. Technical Dependencies

| Dependency | Version | Purpose |
|-----------|---------|---------|
| Python | 3.x | Runtime |
| requests | >= 2.31.0 | HTTP client for job API calls |
| pyyaml | >= 6.0 | YAML configuration parsing |
| python-docx | >= 1.1.0 | DOCX resume generation (optional, graceful fallback) |
| google-api-python-client | >= 2.100.0 | Google Drive API for onboarding import |
| google-auth | >= 2.23.0 | Google service account authentication |
| Claude CLI | Latest | AI model access for resume tailoring |

---

## 15. Success Metrics

| Metric | Definition | Target |
|--------|-----------|--------|
| **Jobs fetched per run** | Unique, deduplicated JDs saved per daily run | 10-30 per customer |
| **Tailoring success rate** | % of JDs that produce all 3 artifacts without error | > 95% |
| **Email delivery rate** | % of digests successfully sent via SMTP | > 99% |
| **Match confidence distribution** | % of tailored packages scoring 75%+ match | > 60% |
| **Time to first digest** | Elapsed time from onboarding submission to first email | < 1 hour |
| **Pipeline completion time** | Total wall-clock time for full run-all across all customers | < 30 min for 5 customers |
| **Customer satisfaction** | Candidate reports the tailored materials are usable as-is or with minor edits | > 80% |

---

## 16. Glossary

| Term | Definition |
|------|-----------|
| **Base resume** | The candidate's master resume, used as the source of truth for all tailoring |
| **JD** | Job Description — a markdown file containing the full posting and metadata |
| **Tailored package** | The set of artifacts produced for one JD: resume, cover letter, and report |
| **Fingerprint** | SHA-256 hash of `title|company` used for deduplication |
| **Slug** | URL-safe, lowercase-hyphenated identifier for a customer (e.g., `tayo-fasunon`) |
| **Discovery notes** | Structured insights gathered during onboarding to surface hidden achievements |
| **Pipeline** | The three-phase process: Fetch, Tailor, Notify |
| **Digest** | The HTML email sent to candidates summarizing new tailored packages |
| **Match confidence** | AI-assessed percentage indicating how well the candidate's background fits the JD |
| **Lifecycle** | The time-bounded service window for a customer (start date, expiry, delivery limits) |
