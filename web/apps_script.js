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
//   - Appends a row to a Google Sheet (auto-created on first run)
//   - Emails you the full JSON as an attachment
// ============================================================

const NOTIFY_EMAIL = 'YOUR_EMAIL_HERE';   // ← your email
const SHEET_NAME   = 'Onboarding Submissions';

function doPost(e) {
  try {
    const data = JSON.parse(e.postData.contents);

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
    const folder = getOrCreateFolder('jobRadar_Inbox');
    const filename = 'onboarding_' + data.firstName + '_' + data.lastName + '_' + Date.now() + '.json';
    folder.createFile(filename, JSON.stringify(data, null, 2), 'application/json');

    // ── 3. Save binary resume file to Drive (admin visibility) ──
    if (data.resumeFileData && data.resumeFileName) {
      try {
        var ext = data.resumeFileName.split('.').pop().toLowerCase();
        var mimeType = ext === 'pdf' ? 'application/pdf'
                     : ext === 'docx' ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                     : 'application/octet-stream';
        var binaryFilename = 'resume_' + data.firstName + '_' + data.lastName + '_' + Date.now() + '.' + ext;
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
        var clBinaryFilename = 'cover_letter_' + data.firstName + '_' + data.lastName + '_' + Date.now() + '.' + clExt;
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
      JSON.stringify(data, null, 2),
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
    return ContentService
      .createTextOutput(JSON.stringify({ status: 'error', message: err.toString() }))
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
