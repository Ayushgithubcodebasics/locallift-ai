/**
 * LocalLift AI — Google Sheet ledger + Gmail bridge.
 * Paste into your Sheet: Extensions -> Apps Script, replace everything, set SECRET, Save,
 * Deploy -> New deployment -> type "Web app" -> Execute as: Me -> Who has access: Anyone -> Deploy.
 * Copy the /exec URL into Render as APPS_SCRIPT_URL and the SECRET as APPS_SCRIPT_SECRET.
 */
const SECRET = 'CHANGE_ME_TO_A_LONG_RANDOM_STRING';
const SHEET_NAME = 'Ledger';
const HEADERS = ['Task ID', 'Timestamp', 'Assigned By', 'Agent Name', 'Task Description', 'Priority',
                 'Status', 'Output / Result Link', 'Completion Time'];

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.secret !== SECRET) return out({ok: false, error: 'bad secret'});
    if (d.action === 'ping') { getSheet(); return out({ok: true}); }
    if (d.action === 'upsert') return out(upsert(d.row));
    if (d.action === 'email') {
      MailApp.sendEmail({to: d.to, subject: d.subject, htmlBody: d.html, name: d.name || 'LocalLift AI'});
      return out({ok: true});
    }
    return out({ok: false, error: 'unknown action'});
  } catch (err) {
    return out({ok: false, error: String(err)});
  }
}

function doGet() { return out({ok: true, service: 'LocalLift ledger bridge'}); }

function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) sh = ss.insertSheet(SHEET_NAME, 0);
  if (sh.getLastRow() === 0) {
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
    sh.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold').setBackground('#312e81').setFontColor('#ffffff');
    sh.setColumnWidth(5, 380); sh.setColumnWidth(8, 380);
  }
  return sh;
}

function upsert(r) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const sh = getSheet();
    const vals = [r.task_id, r.timestamp, r.assigned_by, r.agent, r.description, r.priority, r.status, r.output,
                  r.completion_time];
    const n = sh.getLastRow();
    const ids = n > 1 ? sh.getRange(2, 1, n - 1, 1).getValues().map(x => String(x[0])) : [];
    const i = ids.indexOf(String(r.task_id));
    let rowNum;
    if (i >= 0) { rowNum = i + 2; sh.getRange(rowNum, 1, 1, vals.length).setValues([vals]); }
    else { sh.appendRow(vals); rowNum = sh.getLastRow(); }
    const colors = {'Queued': '#e0e7ff', 'In Progress': '#fef3c7', 'Blocked': '#fee2e2', 'Completed': '#dcfce7'};
    sh.getRange(rowNum, 7).setBackground(colors[r.status] || '#ffffff');
    return {ok: true, row: rowNum};
  } finally {
    lock.releaseLock();
  }
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

/** Run this once from the editor (select testSetup -> Run) to authorise Sheets + Gmail. */
function testSetup() {
  getSheet();
  MailApp.sendEmail(Session.getActiveUser().getEmail(), 'LocalLift AI — Apps Script connected', 'Ledger + email bridge is ready.');
}
