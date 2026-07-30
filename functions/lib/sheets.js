const SHEET_NAME = 'Sheet1';
const COLS = ['email','first_name','last_name','status','date_from','date_to','added_at','last_login'];

async function getToken(saEmail, privateKeyPem) {
  const now = Math.floor(Date.now() / 1000);
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');
  const claim = btoa(JSON.stringify({
    iss: saEmail,
    scope: 'https://www.googleapis.com/auth/spreadsheets',
    aud: 'https://oauth2.googleapis.com/token',
    exp: now + 3600, iat: now
  })).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');

  const pem = privateKeyPem.replace(/-----BEGIN PRIVATE KEY-----/,'').replace(/-----END PRIVATE KEY-----/,'').replace(/\n/g,'');
  const der = Uint8Array.from(atob(pem), c => c.charCodeAt(0));
  const key = await crypto.subtle.importKey('pkcs8', der, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign']);
  const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(`${header}.${claim}`));
  const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig))).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');

  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=${header}.${claim}.${sigB64}`
  });
  const data = await res.json();
  return data.access_token;
}

export async function sheetsGet(sheetId, saEmail, privateKey) {
  const token = await getToken(saEmail, privateKey);
  const res = await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${sheetId}/values/${SHEET_NAME}`,
    { headers: { Authorization: `Bearer ${token}` } }
  );
  const data = await res.json();
  return data.values || [];
}

export async function getUserByEmail(sheetId, saEmail, privateKey, email) {
  const rows = await sheetsGet(sheetId, saEmail, privateKey);
  const header = rows[0] || COLS;
  const idx = r => header.indexOf(r);
  for (let i = 1; i < rows.length; i++) {
    if ((rows[i][idx('email')] || '').toLowerCase() === email.toLowerCase()) {
      return { row: i + 1, data: Object.fromEntries(header.map((h,j) => [h, rows[i][j] || ''])) };
    }
  }
  return null;
}

export async function upsertUser(sheetId, saEmail, privateKey, { email, first_name, last_name, status }) {
  const token = await getToken(saEmail, privateKey);
  const existing = await getUserByEmail(sheetId, saEmail, privateKey, email);
  const now = new Date().toISOString().slice(0,10);

  if (existing) {
    // update last_login; keep existing status/names unless provided
    const d = existing.data;
    const updated = [
      email,
      first_name || d.first_name || '',
      last_name  || d.last_name  || '',
      status     || d.status     || 'paid',
      d.date_from || now,
      d.date_to   || '',
      d.added_at  || now,
      now
    ];
    const range = `${SHEET_NAME}!A${existing.row}:H${existing.row}`;
    await fetch(
      `https://sheets.googleapis.com/v4/spreadsheets/${sheetId}/values/${encodeURIComponent(range)}?valueInputOption=RAW`,
      { method: 'PUT', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ range, majorDimension: 'ROWS', values: [updated] }) }
    );
    return status || d.status || 'paid';
  } else {
    const row = [email, first_name || '', last_name || '', status || 'paid', now, '', now, now];
    await fetch(
      `https://sheets.googleapis.com/v4/spreadsheets/${sheetId}/values/${SHEET_NAME}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS`,
      { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ majorDimension: 'ROWS', values: [row] }) }
    );
    return status || 'paid';
  }
}
