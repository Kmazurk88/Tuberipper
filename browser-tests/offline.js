const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };
const fake = (preloaded) => `
  const _A = window.Audio; window.Audio = function (...a) { const x = new _A(...a); window.__aud = x; return x; };
  window.__dl = ${JSON.stringify(preloaded)};
  window.TRNative = { offlineSupported: () => true, update() {}, clear() {}, changeServer() {},
    dlStart: j => { const o = JSON.parse(j); window.__dl[o.id] = { id: o.id, status: 'done', pct: 100, bytes: 5e6, total: 5e6, song: o.song, added: Date.now(), cover: '' }; return 'ok'; },
    dlList: () => JSON.stringify(Object.values(window.__dl)), dlRemove: id => { delete window.__dl[id]; }, dlRemoveAll: () => { window.__dl = {}; },
    dlUrl: id => (window.__dl[id] && window.__dl[id].status === 'done') ? 'http://tr-test:8000/api/nd/stream/' + id + '?local=1' : '',
    dlUsage: () => 5000000, dlCellular: () => false, dlSetCellular: () => {} };`;
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  // --- 1. a downloaded copy is what plays
  let ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  let p = await ctx.newPage();
  await require('./bm.js')(p); const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.addInitScript(fake({}));
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  const nd = ep => p.evaluate(async ep => (await (await fetch('api/nd/rest/' + ep)).json())['subsonic-response'], ep);
  await p.locator('.tabbtn[data-tab=library]').click(); await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row:has-text("Conan")').first().click(); await p.waitForSelector('#tab-library .page:last-child .ep');
  const card = p.locator('#tab-library .page:last-child .ep').first();
  await card.locator('.dlbtn').click(); await sleep(700);
  const sid = await p.evaluate(() => Object.keys(window.__dl)[0]);
  ok(!!sid && (await card.locator('.dlbtn svg circle[fill="#1ed760"]').count()) === 1, 'download button on the episode starts and completes a download');
  await card.locator('.ep-play').click(); await sleep(3000);
  const src = await p.evaluate(() => window.__aud && window.__aud.src);
  ok(src && src.includes('?local=1'), 'playback uses the downloaded copy: ...' + (src || '').slice(-45));
  await p.evaluate(() => window.__aud.pause());
  const keep = await p.evaluate(() => window.__dl);
  const queue = await p.evaluate(() => localStorage.getItem('tr.queue'));
  await ctx.close();

  // --- 2. no server at all: offline mode from downloads
  ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  p = await ctx.newPage(); p.on('pageerror', e => errs.push(e.message));
  await require('./bm.js')(p);
  await p.addInitScript(fake(keep));
  await p.addInitScript(q => { if (q) localStorage.setItem('tr.queue', q); }, queue);
  await p.route('**/api/nd/**', r => { if (r.request().url().includes('local=1')) r.continue(); else r.abort(); });
  await p.route('**/api/auth/**', r => r.abort()); await p.route('**/api/app/**', r => r.abort());
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.dl-head', { timeout: 20000 }); await sleep(800);
  const txt = await p.locator('#tab-home').innerText();
  ok(txt.includes('You’re offline') && txt.includes('1 download'), 'offline mode shows the downloads: ' + txt.replace(/\n+/g, ' | ').slice(0, 110));
  await p.screenshot({ path: '/out/80-offline.png' });
  ok(await p.locator('.tabbtn').count() === 4, 'tabs are still there');
  await p.locator('.tabbtn[data-tab=search]').click(); await sleep(400);
  ok((await p.locator('#tab-search').innerText()).includes('Search needs a connection'), 'Search explains it needs a connection');
  await p.locator('.tabbtn[data-tab=home]').click();
  await p.locator('#tab-home .row').first().click(); await sleep(2500);
  const src2 = await p.evaluate(() => window.__aud && window.__aud.src);
  ok(src2 && src2.includes('?local=1') && !(await p.locator('#mini').getAttribute('class')).includes('hidden'), 'tapping a download plays it with no server');
  await p.screenshot({ path: '/out/81-offline-playing.png' });
  console.log('errors:', errs); await b.close(); console.log(failures ? failures + ' FAILED' : 'ALL PASSED'); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 6).join('\n')); process.exit(2); });
