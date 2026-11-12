// Page side of problem reports: JS errors are reported once, the menu item exists only inside the app, error toasts show the request id.
const { chromium } = require('playwright');
(async () => {
  const base = process.argv[2];
  const b = await chromium.launch();
  const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) process.exitCode = 1; };

  // 1) a plain browser: no "Send diagnostics" item; a JS error is reported once
  const p = await b.newPage({ viewport: { width: 400, height: 860 } });
  await p.addInitScript(() => localStorage.setItem('tr.debug', '1'));
  const posted = [];
  p.on('request', r => { if (r.url().includes('/api/app/diag') && r.method() === 'POST') posted.push(JSON.parse(r.postData())); });
  await p.goto(base); await p.waitForTimeout(2500);
  await p.evaluate(() => { setTimeout(() => { throw new Error('test boom'); }, 0); setTimeout(() => { throw new Error('test boom'); }, 50); });
  await p.waitForTimeout(1500);
  ok(posted.length === 1 && posted[0].kind === 'js' && posted[0].js.message.includes('test boom'), `one JS error reported (${posted.length}): ${posted[0] && posted[0].js.message}`);
  ok(posted[0] && posted[0].app.name === 'web' && /^\d+$/.test(posted[0].js.shell) && posted[0].js.ua.length > 5, 'report names the page version and browser');
  await p.evaluate(() => { Promise.reject(new Error('rejected thing')); });
  await p.waitForTimeout(1000);
  ok(posted.length === 2 && posted[1].js.message.startsWith('Unhandled: rejected thing'), 'unhandled promise rejection reported');
  const list = await (await p.request.get(base + 'api/app/diag')).json();
  ok(list.length === 2 && list.every(x => x.kind === 'js'), `server kept both: ${list.map(x => x.summary).join(' | ')}`);
  await p.evaluate(() => document.querySelector('.avatar, #avatar, .top-avatar, [aria-label="Menu"]') && document.querySelector('.avatar, #avatar, .top-avatar, [aria-label="Menu"]').click());
  await p.waitForTimeout(500);
  ok(!(await p.isVisible('text=Send diagnostics')), 'no Send diagnostics item in a plain browser');

  // 2) error toast shows the server's request id (only right after a server error)
  await p.route('**/api/probe', r => r.fulfill({ status: 500, headers: { 'x-request-id': 'abc123def456' }, json: { detail: 'x' } }));
  await p.evaluate(() => fetch('api/probe'));
  await p.evaluate(() => toast('Couldn’t do it: server error', 'bad'));
  ok(await p.isVisible('text=(ref abc123def456)'), 'error toast after a 500 shows the ref');
  await p.evaluate(() => toast('Another problem', 'bad', 0));
  ok(await p.isVisible('text=Another problem (ref abc123def456)'), 'still within 10 s: same ref');
  await p.evaluate(() => toast('Fine now', 'ok'));
  ok(!(await p.isVisible('text=Fine now (ref')), 'ok toasts never get a ref');
  await p.close();

  // 3) inside the app: the menu item is there and the answer comes back as a toast
  const a = await b.newPage({ viewport: { width: 400, height: 860 } });
  await a.addInitScript(() => { window.__sent = []; window.TRNative = { sendDiagnostics: n => { window.__sent.push(n); }, onHomeWifi: () => false }; });
  await a.goto(base); await a.waitForTimeout(2500);
  const av = await a.$('.avatar, #avatar, .top-avatar, [aria-label="Menu"], .topbar button');
  if (av) await av.click();
  await a.waitForTimeout(600);
  const visible = await a.isVisible('text=Send diagnostics');
  ok(visible, 'Send diagnostics is in the menu inside the app');
  if (visible) {
    await a.click('text=Send diagnostics'); await a.waitForTimeout(500);
    ok((await a.evaluate(() => window.__sent.length)) === 1, 'tapping it calls TRNative.sendDiagnostics');
    await a.evaluate(() => window.TRNative_diag(true, 'Diagnostics sent. Ref abc.'));
    ok(await a.isVisible('text=Diagnostics sent. Ref abc.'), 'the answer is shown');
  }
  await b.close();
})();
