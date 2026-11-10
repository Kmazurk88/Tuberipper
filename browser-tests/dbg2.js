const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const p = await (await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true })).newPage();
  p.on('console', m => console.log('console', m.type(), m.text())); p.on('pageerror', e => console.log('pageerror', e.message));
  await p.addInitScript(`const _A = window.Audio; window.Audio = function (...a) { const x = new _A(...a); window.__aud = x; return x; };
    window.__dl = {}; window.TRNative = { offlineSupported: () => true, update() {}, clear() {}, changeServer() {}, dlStart: j => { const o = JSON.parse(j); window.__dl[o.id] = { id: o.id, status: 'done', pct: 100, total: 5e6, song: o.song, added: 1, cover: '' }; return 'ok'; },
    dlList: () => JSON.stringify(Object.values(window.__dl)), dlRemove() {}, dlRemoveAll() {}, dlUrl: id => (window.__dl[id] && window.__dl[id].status === 'done') ? 'http://tr-test:8000/api/nd/stream/' + id + '?local=1' : '', dlUsage: () => 1, dlCellular: () => false, dlSetCellular() {} };`);
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=search]').click(); await p.fill('#searchInput', 'piano man'); await p.waitForSelector('#searchBody .row[data-sid]');
  await p.locator('#searchBody .row[data-sid]').first().locator('button[aria-label=More]').click();
  await p.locator('.sheet .item', { hasText: /^Download$/ }).click(); await sleep(700);
  const sid = await p.evaluate(() => Object.keys(window.__dl)[0]);
  console.log('downloaded id', sid, '| dlUrl:', await p.evaluate(id => TRNative.dlUrl(id), sid));
  const rows = await p.evaluate(() => [...document.querySelectorAll('#searchBody .row[data-sid]')].map(r => r.dataset.sid + ' ' + r.querySelector('.t').textContent).slice(0, 4));
  console.log('rows:', rows);
  await p.locator('#searchBody .row[data-sid="' + sid + '"] .txt').click(); await sleep(3000);
  console.log('src now:', await p.evaluate(() => window.__aud.src));
  await b.close();
})();
