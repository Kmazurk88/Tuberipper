// New app version on the server -> the page reloads itself once, only when hidden and paused.
const { chromium } = require('playwright');
(async () => {
  const base = process.argv[2];
  const b = await chromium.launch(); const p = await b.newPage();
  let fake = null, loads = 0;
  p.on('load', () => loads++);
  await p.route('**/api/**', async route => {
    const resp = await route.fetch();
    const headers = { ...resp.headers() };
    if (fake) headers['x-tr-shell'] = fake;
    await route.fulfill({ response: resp, headers });
  });
  const hide = v => p.evaluate(v => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => v }); document.dispatchEvent(new Event('visibilitychange')); }, v);
  await p.goto(base); await p.waitForTimeout(3000);
  const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) process.exitCode = 1; };
  const start = loads;
  await hide('hidden'); await p.waitForTimeout(2500);
  ok(loads === start, 'no reload when the version is unchanged');
  await hide('visible');
  fake = '999';
  await p.evaluate(() => fetch('api/health'));
  await hide('hidden'); await p.waitForTimeout(2500);
  ok(loads === start + 1, 'reloads once when hidden after a new version appears');
  await p.waitForTimeout(2000);
  await p.evaluate(() => fetch('api/health'));
  await hide('hidden'); await p.waitForTimeout(2500);
  ok(loads === start + 1, 'does not reload again for the same version');
  const tagged = await p.evaluate(() => sessionStorage.getItem('tr.reloadedFor'));
  ok(tagged === '999', 'remembers the version it reloaded for');
  await b.close();
})();
