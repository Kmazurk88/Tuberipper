const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const mk = async (auth) => {
    const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
    const p = await ctx.newPage();
  await require('./bm.js')(p); p.errs = []; p.on('pageerror', e => p.errs.push(e.message));
    p.mock = { deletes: [], epDeletes: [], absPosts: [], abs: [], scan: 'a', scanCalls: 0, homeCalls: 0 };
    await p.route('**/api/podcast/capabilities', r => r.fulfill({ json: { abs: true } }));
    await p.route('**/api/podcast/abs-progress', async r => { if (r.request().method() === 'POST') { p.mock.absPosts.push(JSON.parse(r.request().postData())); r.fulfill({ json: { ok: true } }); } else r.fulfill({ json: { progress: p.mock.abs } }); });
    await p.route('**/api/library/delete', r => { p.mock.deletes.push(JSON.parse(r.request().postData()).ids); r.fulfill({ json: { deleted: JSON.parse(r.request().postData()).ids, failed: [] } }); });
    await p.route('**/api/podcast/delete', r => { p.mock.epDeletes.push(JSON.parse(r.request().postData()).song); r.fulfill({ json: { ok: true } }); });
    await p.route('**/rest/getScanStatus*', r => { p.mock.scanCalls++; r.fulfill({ json: { 'subsonic-response': { status: 'ok', scanStatus: { scanning: false, count: 1, folderCount: 1, lastScan: p.mock.scan } } } }); });
    await p.route('**/rest/getAlbumList2*type=newest*', r => { p.mock.homeCalls++; r.continue(); });
    if (auth) await p.route('**/api/auth/me', r => r.fulfill({ json: { auth: true, user: 'mom' } }));
    return p;
  };
  const nd = (p, ep) => p.evaluate(async ep => (await (await fetch('api/nd/rest/' + ep)).json())['subsonic-response'], ep);

  // ---------- 1. delete options on the home / Tailscale addresses ----------
  let p = await mk(false);
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=search]').click(); await p.fill('#searchInput', 'piano man'); await p.waitForSelector('#searchBody .row[data-sid]');
  const first = p.locator('#searchBody .row[data-sid]').first(); const sid = await first.getAttribute('data-sid');
  await first.locator('button[aria-label=More]').click();
  const items = (await p.locator('.sheet .item').allInnerTexts()).map(x => x.trim());
  ok(items.includes('Delete from library'), 'song menu offers "Delete from library" on the home/Tailscale address');
  await p.locator('.sheet .item.bad', { hasText: 'Delete from library' }).click(); await p.waitForSelector('.dialog'); await sleep(300);
  await p.screenshot({ path: '/out/95-delete-confirm.png' });
  ok((await p.locator('.dialog').innerText()).includes('permanently'), 'asks for confirmation: "' + (await p.locator('.dialog p').innerText()).slice(0, 70) + '…"');
  await p.locator('.dialog .pillbtn', { hasText: 'Cancel' }).click(); await sleep(500);
  ok(p.mock.deletes.length === 0, 'Cancel deletes nothing');
  await first.locator('button[aria-label=More]').click(); await p.locator('.sheet .item.bad', { hasText: 'Delete from library' }).click(); await p.waitForSelector('.dialog');
  await p.locator('.dialog .pillbtn', { hasText: 'Delete' }).click(); await sleep(900);
  ok(p.mock.deletes.length === 1 && p.mock.deletes[0][0] === sid, 'confirming sends the delete for that song');
  ok(await p.locator('#searchBody .row[data-sid="' + sid + '"]').count() === 0, 'the row disappears from the screen straight away');

  // album page menu
  await p.locator('#searchBody .row:has(.t)', { hasText: 'Billy Joel' }).first().click().catch(() => {});
  await p.fill('#searchInput', 'storm front'); await p.waitForSelector('#searchBody .rowsec:has-text("Albums")');
  await p.locator('#searchBody .row', { hasText: /^Storm Front\s*Album/ }).first().click(); await p.waitForSelector('#tab-search .page:last-child .hero h1');
  await p.waitForSelector('#tab-search .page:last-child .row[data-sid]'); await sleep(600);
  await p.locator('#tab-search .page:last-child .actions button[aria-label=More]').click();
  ok((await p.locator('.sheet .item.bad').innerText()).includes('Delete album'), 'album page has a menu with "Delete album (N songs)": ' + (await p.locator('.sheet .item.bad').innerText()).trim());
  await p.locator('.sheet .item.bad').click(); await p.waitForSelector('.dialog'); await p.locator('.dialog .pillbtn', { hasText: 'Delete' }).click(); await sleep(1000);
  ok(p.mock.deletes.length === 2 && p.mock.deletes[1].length >= 8, 'deleting the album sends all its songs (' + p.mock.deletes[1].length + ')');
  ok(await p.locator('#tab-search .page:last-child .hero').count() === 0, 'the album page closes afterwards');

  // episode delete
  await p.locator('.tabbtn[data-tab=library]').click(); await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row:has-text("Conan")').first().click(); await p.waitForSelector('#tab-library .page:last-child .ep');
  const n0 = await p.locator('#tab-library .page:last-child .ep').count();
  const ep = p.locator('#tab-library .page:last-child .ep').first(); const eid = await ep.getAttribute('data-sid');
  await ep.locator('button[aria-label=More]').click();
  ok((await p.locator('.sheet .item.bad').innerText()).includes('Delete episode'), 'episode menu has "Delete episode" when Audiobookshelf is connected');
  await p.locator('.sheet .item.bad').click(); await p.waitForSelector('.dialog'); await p.locator('.dialog .pillbtn', { hasText: 'Delete' }).click(); await sleep(1200);
  ok(p.mock.epDeletes[0] === eid && await p.locator('#tab-library .page:last-child .ep').count() === n0 - 1, 'episode delete goes through Audiobookshelf and the card disappears');

  // ---------- 2. progress sync with Audiobookshelf ----------
  const eps = await p.locator('#tab-library .page:last-child .ep').evaluateAll(els => els.map(e => e.dataset.sid));
  const now = Date.now();
  p.mock.abs = [{ song: eps[0], position: 900000, duration: 3000, finished: false, ts: now + 60000 }, { song: eps[1], position: 0, duration: 3000, finished: true, ts: now + 60000 }];
  await p.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); // (visible) -> sync
  await p.reload(); await p.waitForSelector('.recents-grid'); await sleep(3500);
  await p.locator('.tabbtn[data-tab=library]').click(); await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row:has-text("Conan")').first().click(); await p.waitForSelector('#tab-library .page:last-child .ep'); await sleep(2500);
  const t0 = await p.locator('#tab-library .page:last-child .ep[data-sid="' + eps[0] + '"]').innerText();
  const t1 = await p.locator('#tab-library .page:last-child .ep[data-sid="' + eps[1] + '"]').innerText();
  ok(/left/.test(t0), 'progress made in Audiobookshelf shows up here: "' + (t0.match(/[^\n]*left[^\n]*/) || ['?'])[0] + '"');
  ok(t1.includes('Played'), 'an episode finished in Audiobookshelf shows as Played here');
  // ours -> Audiobookshelf
  await p.locator('#tab-library .page:last-child .ep[data-sid="' + eps[1] + '"] button[aria-label=More]').click();
  await p.locator('.sheet .item', { hasText: 'Mark as not started' }).click(); await sleep(1200);
  ok(p.mock.absPosts.some(x => x.song === eps[1] && x.finished === false && x.position === 0), 'marking "not started" here is sent to Audiobookshelf');
  const eps3 = await p.locator('#tab-library .page:last-child .ep').evaluateAll(els => els.map(e => e.dataset.sid));
  const third = eps3.find(x => x !== eps[0] && x !== eps[1]);
  await p.locator('#tab-library .page:last-child .ep[data-sid="' + third + '"] button[aria-label=More]').click();
  await p.locator('.sheet .item', { hasText: 'Mark as finished' }).click(); await sleep(1200);
  ok(p.mock.absPosts.some(x => x.song === third && x.finished === true), 'marking "finished" here is sent to Audiobookshelf');
  // listening pushes position
  await p.locator('#tab-library .page:last-child .ep[data-sid="' + eps[0] + '"] .ep-play').click(); await sleep(3500);
  await p.locator('#miniPlay').click(); await sleep(1500);
  ok(p.mock.absPosts.some(x => x.song === eps[0] && x.position > 800000 && x.finished === false), 'listening pushes your position to Audiobookshelf (resumed at ~15 min)');
  for (const x of ((await nd(p, 'getBookmarks')).bookmarks.bookmark || [])) await nd(p, 'deleteBookmark?id=' + x.entry.id);
  await p.context().close();

  // ---------- 3. auto-refresh ----------
  p = await mk(false);
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid'); await sleep(2500);
  const before = p.mock.homeCalls;
  await p.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await sleep(800);
  ok(p.mock.homeCalls === before, 'no refresh while nothing has changed');
  p.mock.scan = 'b';
  await p.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await sleep(2500);
  ok(p.mock.homeCalls > before, 'when Navidrome reports a change, Home refreshes by itself');
  await p.context().close();

  // ---------- 4. on the internet address nothing can be deleted ----------
  p = await mk(true);
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=search]').click(); await p.fill('#searchInput', 'piano man'); await p.waitForSelector('#searchBody .row[data-sid]');
  await p.locator('#searchBody .row[data-sid]').first().locator('button[aria-label=More]').click();
  ok(!(await p.locator('.sheet').innerText()).includes('Delete'), 'public address: no delete option in the song menu');
  await p.keyboard.press('Escape'); await p.goBack().catch(() => {});
  console.log('errors:', p.errs); await b.close(); console.log(failures ? failures + ' FAILED' : 'ALL PASSED'); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 6).join('\n')); process.exit(2); });
