const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push(m.text()); });
  await require('./bm.js')(p);
  await p.addInitScript(() => { window.__calls = []; window.TRNative = { update: j => window.__calls.push(JSON.parse(j)), clear: () => {} };
    const A = window.Audio; window.__as = []; window.Audio = class extends A { constructor() { super(); window.__as.push(this); } }; window.__act = () => window.__as.find(a => !a.paused && a.src) || window.__as.find(a => a.src) || window.__as[0]; });
  await p.goto('http://tr-test:8000/');
  const skel = await p.locator('.sk').count();
  await p.waitForSelector('.recents-grid');
  ok(skel > 0, 'loading placeholders were shown first (' + skel + ' shapes)');
  await p.screenshot({ path: '/t/home.png' });

  // ---- play a song that has synced lyrics
  await p.locator('.tabbtn[data-tab=search]').click();
  await p.locator('#searchInput').fill('Stairway to Heaven');
  await p.waitForSelector('#searchBody .row[data-sid]');
  await p.locator('#searchBody .row[data-sid]').first().locator('.txt').click();
  await sleep(2000);
  ok(await p.evaluate(() => !window.__act().paused), 'song is playing');
  await p.locator('#miniBody').click();
  await p.waitForSelector('.np');
  await p.waitForSelector('.np .chip-btn:has-text("Lyrics"):not(.hidden)', { timeout: 8000 }).then(() => ok(true, 'Lyrics button appears for a song that has lyrics')).catch(() => ok(false, 'Lyrics button missing'));
  ok(await p.locator('.np .tint').evaluate(e => !!e.style.getPropertyValue('--tint')), 'background tint taken from the cover: ' + await p.locator('.np .tint').evaluate(e => e.style.getPropertyValue('--tint')));
  await p.screenshot({ path: '/t/np.png' });
  await p.locator('.np .chip-btn:has-text("Lyrics")').click();
  await p.waitForSelector('.np .lyrics .ly');
  ok(await p.locator('.np .lyrics .ly').count() > 10, 'lyrics lines shown: ' + await p.locator('.np .lyrics .ly').count());
  await p.evaluate(() => { window.__act().currentTime = 100; window.__act().dispatchEvent(new Event('timeupdate')); }); await sleep(1200);   // (this headless Chromium can't decode the audio, so time updates are sent by hand)
  ok(await p.locator('.np .ly.on').count() === 1, 'one lyric line highlighted at 100 s: "' + (await p.locator('.np .ly.on').first().textContent().catch(() => '')) + '"');
  await p.screenshot({ path: '/t/lyrics.png' });
  const lineMs = await p.evaluate(() => 0);
  await p.locator('.np .ly').nth(3).click(); await p.evaluate(() => window.__act().dispatchEvent(new Event('timeupdate'))); await sleep(500);
  ok(await p.evaluate(() => window.__act().currentTime) < 100, 'tapping a lyric line seeks');
  await p.locator('.np .chip-btn:has-text("Lyrics")').click();
  ok(await p.locator('.np .art .cover').count() === 1, 'toggle back to cover art');

  // ---- sleep timer (with a fake clock)
  await p.locator('.np .chip-btn[aria-label="Sleep timer"]').click();
  await p.locator('.sheet .item', { hasText: /^5 minutes$/ }).click();
  await sleep(300);
  ok((await p.locator('.np .chip-btn[aria-label="Sleep timer"]').textContent()).includes('5m'), 'sleep chip shows 5m');
  await p.evaluate(() => { const real = Date.now; const off = 6 * 60000; Date.now = () => real() + off; });   // 6 minutes pass
  await p.evaluate(() => window.__act().dispatchEvent(new Event('timeupdate')));
  await sleep(7000);
  ok(await p.evaluate(() => window.__act().paused), 'sleep timer paused playback after the time was up');
  await p.locator('.np .chip-btn[aria-label="Sleep timer"]').click();
  ok(await p.locator('.sheet .item', { hasText: 'End of this song' }).count() === 1, 'sleep sheet offers "End of this song"');
  await p.locator('.sheet .item', { hasText: 'End of this song' }).click();
  await p.evaluate(() => { Date.now = Date.now; });

  // ---- flick down closes the player
  const cdp = await ctx.newCDPSession(p);
  const drag = async (x0, y0, x1, y1, steps = 8) => {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y: y0 }] });
    for (let i = 1; i <= steps; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * i / steps, y: y0 + (y1 - y0) * i / steps }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  };
  await drag(200, 200, 200, 520); await sleep(600);
  ok(await p.locator('.np').count() === 0, 'swiping down closed Now Playing');

  // ---- swipe a song row right -> queue
  await p.locator('.tabbtn[data-tab=search]').click();
  await p.locator('#searchInput').fill('Black Dog');
  await p.waitForSelector('#searchBody .row[data-sid]');
  const box = await p.locator('#searchBody .row[data-sid]').first().boundingBox();
  await drag(60, box.y + box.height / 2, 260, box.y + box.height / 2);
  await sleep(300);
  ok(await p.locator('.toast', { hasText: 'Added to queue' }).count() >= 1, 'swiping a song row right added it to the queue');

  // ---- queue remove + undo
  await p.locator('#mini #miniBody').click(); await p.waitForSelector('.np');
  await p.locator('.np .bar .icon-btn[aria-label=Queue]').click();
  await p.waitForSelector('.queue .row');
  const n0 = await p.locator('.queue .row').count();
  await p.locator('.queue .row').last().locator('button[aria-label="Remove from queue"]').click();
  ok(await p.locator('.queue .row').count() === n0 - 1, 'queue row removed');
  await p.locator('.toast .undo-btn').click(); await sleep(300);
  ok(await p.locator('.queue .row').count() === n0, 'Undo put the queue row back');
  await p.goBack().catch(() => {}); await sleep(300);

  // ---- mini player flick (fresh page; this Chromium can't decode songs, so the queue skips to its last song by itself: swipe right = previous)
  {
    const q = await ctx.newPage(); await require('./bm.js')(q);
    await q.addInitScript(() => { window.__calls = []; window.TRNative = { update: j => window.__calls.push(JSON.parse(j)), clear: () => {} }; });
    await q.goto('http://tr-test:8000/'); await q.waitForSelector('.recents-grid');
    await q.locator('.tabbtn[data-tab=search]').click(); await q.locator('#searchInput').fill('Black Dog');
    await q.waitForSelector('#searchBody .row[data-sid]');
    const rows = q.locator('#searchBody .row[data-sid]');
    await rows.nth(0).locator('.txt').click(); await sleep(1500);
    const bx = await rows.nth(1).boundingBox();
    const c2 = await ctx.newCDPSession(q);
    const drag2 = async (x0, y0, x1, y1, steps = 8) => { await c2.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y: y0 }] });
      for (let i = 1; i <= steps; i++) await c2.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * i / steps, y: y0 + (y1 - y0) * i / steps }] });
      await c2.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); };
    await drag2(60, bx.y + bx.height / 2, 260, bx.y + bx.height / 2); await sleep(2500);
    const t0 = await q.locator('#miniTitle').textContent(); const mark = await q.evaluate(() => window.__calls.length);
    const mb = await q.locator('#miniBody').boundingBox();
    await drag2(60, mb.y + 20, 330, mb.y + 20); await sleep(1500);
    const seen = await q.evaluate(m => window.__calls.slice(m).map(c => c.title), mark);
    ok(seen.some(t => t.startsWith('Black Dog')), `mini player: swipe right went to the previous song (titles seen after: ${[...new Set(seen)].map(t => t.slice(0, 18)).join(', ')})`);
    await q.close();
  }

  while (await p.locator('.np, .queue').count()) { await p.goBack(); await sleep(400); }

  // ---- podcast: speed chip + mark played with undo + resume card
  await p.locator('.tabbtn[data-tab=library]').click();
  await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row', { hasText: 'Conan' }).first().click();
  await p.waitForSelector('#tab-library .page:last-child .ep');
  const eps = p.locator('#tab-library .page:last-child .ep');
  await eps.first().locator('.ep-play').click(); await sleep(2500);
  await p.locator('#miniBody').click(); await p.waitForSelector('.np');
  await p.locator('.np .chip-btn[aria-label="Playback speed"]').click();
  await p.locator('.sheet .item', { hasText: '1.5×' }).click(); await sleep(300);
  ok(await p.evaluate(() => window.__act().playbackRate) === 1.5, 'podcast speed set to 1.5×');
  await p.evaluate(() => { window.__act().currentTime = 40; window.__act().dispatchEvent(new Event('timeupdate')); }); await sleep(1500);
  await p.screenshot({ path: '/t/podnp.png' });
  await p.goBack(); await sleep(400);
  await p.locator('#miniPlay').click(); await sleep(500);   // pause -> saves a bookmark
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  ok(await p.locator('.hero-resume').count() === 1, 'Home shows the "Pick up where you left off" card');
  const ht = await p.locator('.hero-resume b').textContent(); ok(!ht.includes('('), 'episode title has no long description appended: "' + ht + '"');
  await p.screenshot({ path: '/t/home2.png' });

  // ---- unreachable server banner
  await p.route('**/api/nd/rest/ping*', r => r.abort());
  await p.route('**/api/nd/rest/getAlbumList2*', r => r.abort());
  await p.locator('.tabbtn[data-tab=library]').click();
  await p.locator('.tabbtn[data-tab=home]').click();
  await p.evaluate(() => window.dispatchEvent(new Event('offline')));
  await sleep(500);
  ok(await p.locator('.conn-banner').count() === 1, 'connection banner shown when the server is unreachable');
  await p.screenshot({ path: '/t/banner.png' });

  console.log('page errors:', errs.length ? errs.slice(0, 5) : 'none');
  if (errs.length) fails++;
  await b.close();
  console.log(fails ? 'FAILED ' + fails : 'ALL PASSED');
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 3).join(' | ')); process.exit(2); });
