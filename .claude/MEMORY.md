# Memory

## 2026-02-21: Relevance Scoring System Implemented
- Added `compute_relevance_score()` to fetcher.py — density-weighted keyword matching against candidate focus areas
- Scoring math: breadth (70%) + density (30%) per focus area, weighted by priority, normalized 0-1
- Anti-signals in title = 0.5 penalty, in body = 0.1 penalty (reduced from plan's 0.2 to avoid false positives on common words like "sales")
- min_score calibration: a perfect single-area match tops out at ~50% (weight/total_weight). Default min_score = 0.3
- Relevance row added to JD metadata table, parsed by notify.py for digest display
- manage.py auto-populates relevance config from onboarding roles/certs via `_build_relevance_config()`
- `--min-relevance` flag added to notify.py and threaded through manage.py

## 2026-02-22: Bot Protection Added to Onboarding Form
- 5-layer defense: honeypot, timing check (15s), server-side validation, XSS escaping, client-side validation
- All server-side rejections return `{ status: 'ok' }` (silent) to prevent info leakage; catch block too
- Anti-bot fields `_hp` and `_elapsed` stripped from persisted JSON via cleanData
- Filename construction now uses `sanitizeFilename()` everywhere (was raw user input before)
- XSS: `escapeHtml()` via textContent→innerHTML pattern; `renderTags()` switched to DOM createElement
- Client validation gates steps 1-3; error divs `.step-error` toggle `.visible` class
- Apps Script must be redeployed after updating Code.gs for changes to take effect
