// ============================================================
// Google Apps Script — Onboarding Webhook
// ============================================================
//
// Setup:
//   1. Go to https://script.google.com → New Project
//   2. Paste this entire file into Code.gs
//   3. Update YOUR_EMAIL below
//   4. Click Deploy → New Deployment → Web App
//      - Execute as: Me
//      - Who has access: Anyone
//   5. Copy the URL → paste into onboarding.html (APPS_SCRIPT_URL)
//
// What it does:
//   - Receives onboarding JSON from the form
//   - Validates submission (honeypot, timing, required fields)
//   - Appends a row to a Google Sheet (auto-created on first run)
//   - Emails you the full JSON as an attachment
// ============================================================

const NOTIFY_EMAIL = 'YOUR_EMAIL_HERE';   // ← your email
const SHEET_NAME   = 'Onboarding Submissions';

// ── Silent rejection: always return fake-ok to prevent info leakage ──

function silentReject(reason) {
  Logger.log('REJECTED: ' + reason);
  return ContentService
    .createTextOutput(JSON.stringify({ status: 'ok' }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Sanitize filename: strip anything that isn't alphanumeric, hyphen, underscore, or dot ──

function sanitizeFilename(str) {
  return str.replace(/[^a-zA-Z0-9_\-\.]/g, '_').substring(0, 200);
}

// ── Server-side validation ──

function validateSubmission(data) {
  // Required fields
  if (!data.firstName || typeof data.firstName !== 'string' || !data.firstName.trim()) {
    return 'missing firstName';
  }
  if (!data.lastName || typeof data.lastName !== 'string' || !data.lastName.trim()) {
    return 'missing lastName';
  }
  if (!data.email || typeof data.email !== 'string' || !data.email.trim()) {
    return 'missing email';
  }

  // Email format
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email.trim())) {
    return 'invalid email format';
  }

  // Resume: must have text or file
  var hasResumeText = data.resumeText && typeof data.resumeText === 'string' && data.resumeText.trim().length > 0;
  var hasResumeFile = data.resumeFileData && typeof data.resumeFileData === 'string' && data.resumeFileData.length > 0;
  if (!hasResumeText && !hasResumeFile) {
    return 'missing resume';
  }

  // At least one role
  if (!data.roles || !Array.isArray(data.roles) || data.roles.length === 0) {
    return 'missing roles';
  }

  // Length limits
  if (data.firstName.length > 100) return 'firstName too long';
  if (data.lastName.length > 100) return 'lastName too long';
  if (data.email.length > 254) return 'email too long';
  if (data.resumeText && data.resumeText.length > 100000) return 'resumeText too long';
  if (data.resumeFileData && data.resumeFileData.length > 10 * 1024 * 1024) return 'resumeFileData too large';
  if (data.coverLetterText && data.coverLetterText.length > 100000) return 'coverLetterText too long';
  if (data.roles && data.roles.length > 20) return 'too many roles';
  if (data.locations && data.locations.length > 20) return 'too many locations';
  if (data.exclude && data.exclude.length > 20) return 'too many excludes';

  return null; // valid
}

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);

    // ── Layer 1: Honeypot check ──
    if (data._hp && data._hp.trim() !== '') {
      return silentReject('honeypot filled: ' + data._hp);
    }

    // ── Layer 2: Timing check (< 15 seconds = bot) ──
    if (typeof data._elapsed === 'number' && data._elapsed < 15000) {
      return silentReject('too fast: ' + data._elapsed + 'ms');
    }

    // ── Layer 3: Server-side validation ──
    var validationError = validateSubmission(data);
    if (validationError) {
      return silentReject('validation: ' + validationError);
    }

    // ── 1. Save to Google Sheet ──
    const ss = getOrCreateSheet();
    const sheet = ss.getSheetByName('Submissions');

    const resumeStatus = data.resumeText
      ? 'Text (' + data.resumeText.length + ' chars)'
      : data.resumeFileData
        ? 'File: ' + (data.resumeFileName || 'unknown')
        : 'No';

    sheet.appendRow([
      new Date(),
      data.firstName,
      data.lastName,
      data.email,
      data.phone || '',
      data.location || '',
      data.linkedin || '',
      (data.roles || []).join(', '),
      (data.locations || []).join(', '),
      data.minSalary || '',
      data.workArrangement || '',
      resumeStatus,
      data.coverLetterText ? 'Yes (' + data.coverLetterText.length + ' chars)' : 'No',
      data.discovery ? data.discovery.certs || '' : '',
      data.submittedAt || '',
    ]);

    // ── 2. Save full JSON to Drive (for auto-import) ──
    var safeName = sanitizeFilename(data.firstName) + '_' + sanitizeFilename(data.lastName);
    const folder = getOrCreateFolder('jobRadar_Inbox');
    const filename = 'onboarding_' + safeName + '_' + Date.now() + '.json';

    // Strip anti-bot fields before persisting
    var cleanData = JSON.parse(JSON.stringify(data));
    delete cleanData._hp;
    delete cleanData._elapsed;

    folder.createFile(filename, JSON.stringify(cleanData, null, 2), 'application/json');

    // ── 3. Save binary resume file to Drive (admin visibility) ──
    if (data.resumeFileData && data.resumeFileName) {
      try {
        var ext = data.resumeFileName.split('.').pop().toLowerCase();
        var mimeType = ext === 'pdf' ? 'application/pdf'
                     : ext === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                     : 'application/octet-stream';
        var binaryFilename = 'resume_' + safeName + '_' + Date.now() + '.' + sanitizeFilename(ext);
        var decoded = Utilities.newBlob(
          Utilities.base64Decode(data.resumeFileData),
          mimeType,
          binaryFilename
        );
        folder.createFile(decoded);
      } catch (fileErr) {
        Logger.log('Binary file save failed: ' + fileErr.toString());
      }
    }

    // ── 4. Save binary cover letter file to Drive (if uploaded) ──
    if (data.coverLetterFileData && data.coverLetterFileName) {
      try {
        var clExt = data.coverLetterFileName.split('.').pop().toLowerCase();
        var clMimeType = clExt === 'pdf' ? 'application/pdf'
                       : clExt === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                       : 'application/octet-stream';
        var clBinaryFilename = 'cover_letter_' + safeName + '_' + Date.now() + '.' + sanitizeFilename(clExt);
        var clDecoded = Utilities.newBlob(
          Utilities.base64Decode(data.coverLetterFileData),
          clMimeType,
          clBinaryFilename
        );
        folder.createFile(clDecoded);
      } catch (clFileErr) {
        Logger.log('Cover letter file save failed: ' + clFileErr.toString());
      }
    }

    // ── 5. Email you the full JSON ──
    const jsonBlob = Utilities.newBlob(
      JSON.stringify(cleanData, null, 2),
      'application/json',
      filename
    );

    MailApp.sendEmail({
      to: NOTIFY_EMAIL,
      subject: 'New Onboarding: ' + data.firstName + ' ' + data.lastName,
      body: 'New customer submitted the onboarding form.\n\n'
          + 'Name: ' + data.firstName + ' ' + data.lastName + '\n'
          + 'Email: ' + data.email + '\n'
          + 'Roles: ' + (data.roles || []).join(', ') + '\n'
          + 'Locations: ' + (data.locations || []).join(', ') + '\n'
          + 'Resume: ' + resumeStatus + '\n\n'
          + 'Full JSON attached. Run:\n'
          + '  python manage.py import ' + filename + '\n',
      attachments: [jsonBlob],
    });

    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok' }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    // Even errors return ok to prevent info leakage
    Logger.log('doPost error: ' + err.toString());
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'ok' }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

// Handle GET requests (for testing the URL works)
function doGet() {
  return ContentService
    .createTextOutput('Onboarding webhook is live.')
    .setMimeType(ContentService.MimeType.TEXT);
}

// Get or create a named Drive folder (for auto-import inbox/processed)
function getOrCreateFolder(name) {
  const folders = DriveApp.getFoldersByName(name);
  if (folders.hasNext()) {
    return folders.next();
  }
  return DriveApp.createFolder(name);
}

// Create the spreadsheet + headers on first run
function getOrCreateSheet() {
  const files = DriveApp.getFilesByName(SHEET_NAME);
  if (files.hasNext()) {
    return SpreadsheetApp.open(files.next());
  }

  const ss = SpreadsheetApp.create(SHEET_NAME);
  const sheet = ss.getActiveSheet();
  sheet.setName('Submissions');
  sheet.appendRow([
    'Submitted', 'First', 'Last', 'Email', 'Phone',
    'Location', 'LinkedIn', 'Roles', 'Locations',
    'Min Salary', 'Arrangement', 'Resume', 'Cover Letter', 'Certs', 'Timestamp',
  ]);
  sheet.setFrozenRows(1);
  sheet.getRange('1:1').setFontWeight('bold');
  return ss;
}
