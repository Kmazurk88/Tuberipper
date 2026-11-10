const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  const reqs = []; p.on('request', r => reqs.push(r.url()));
  await require('./bm.js')(p);
  await p.addInitScript(() => { window.__as = []; const A = window.Audio; window.Audio = class extends A { constructor() { super(); window.__as.push(this); } };
    window.__act = () => window.__as.find(a => !a.paused && a.src) || window.__as.find(a => a.src) || window.__as[0]; });
  const cdp = await ctx.newCDPSession(p);
  const drag = async (x0, y0, x1, y1, steps = 10) => {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: x0, y: y0 }] });
    for (let i = 1; i <= steps; i++) await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: x0 + (x1 - x0) * i / steps, y: y0 + (y1 - y0) * i / steps }] });
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  };
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');

  // ---- settings sheet + theme
  await p.locator('.avatar').first().click();
  await p.locator('.sheet .item', { hasText: 'Playback & display' }).click();
  ok(await p.locator('.sheet .item', { hasText: 'Volume leveling' }).count() === 1 && await p.locator('.sheet .item', { hasText: 'Crossfade' }).count() === 1 && await p.locator('.sheet .item', { hasText: 'Skip silence' }).count() === 1, 'settings sheet lists leveling, crossfade, skip silence');
  await p.locator('.sheet .item', { hasText: 'Theme' }).click();
  await p.locator('.sheet .item', { hasText: /^Light$/ }).click(); await sleep(300);
  ok(await p.evaluate(() => document.documentElement.dataset.theme) === 'light', 'light theme applied');
  await p.screenshot({ path: '/t/light-home.png' });
  await p.locator('.tabbtn[data-tab=library]').click(); await sleep(1500); await p.screenshot({ path: '/t/light-lib.png' });
  await p.locator('.tabbtn[data-tab=search]').click(); await p.locator('#searchInput').fill('Hells Bells'); await p.waitForSelector('#searchBody .row[data-sid]'); await sleep(800);
  await p.screenshot({ path: '/t/light-search.png' });

  // ---- play mp3 songs (this Chromium can decode mp3): leveling, queue, crossfade
  await p.evaluate(() => { localStorage.setItem('tr.xfade', '4'); });
  await p.reload(); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=search]').click(); await p.locator('#searchInput').fill('AC/DC Hells Bells');
  await p.waitForSelector('#searchBody .row[data-sid]');
  const titles = await p.locator('#searchBody .row[data-sid] .t').allTextContents(); console.log('rows:', titles.slice(0, 4));
  const hb = p.locator('#searchBody .row[data-sid]', { hasText: 'Hells Bells' }).first();
  await hb.locator('.txt').click(); await sleep(2500);
  ok(await p.evaluate(() => { const a = window.__act(); return !a.paused && a.currentTime > 0.5; }), 'an mp3 song really plays in the test browser');
  // queue another mp3 via the song menu
  await p.locator('#searchInput').fill('AC/DC Shoot To Thrill'); await p.waitForSelector('#searchBody .row[data-sid]');
  const st = p.locator('#searchBody .row[data-sid]', { hasText: 'Shoot To Thrill' }).first();
  await st.locator('.icon-btn.dim').click(); await p.locator('.sheet .item', { hasText: 'Add to Queue' }).click(); await sleep(500);
  const t0 = await p.locator('#miniTitle').textContent();
  // volume levelling: wait for Titan to measure the song, the player lowers it
  let vol = 1; for (let i = 0; i < 12 && vol >= 0.999; i++) { await sleep(3000); vol = await p.evaluate(() => window.__act().volume); }
  ok(vol < 0.999, 'volume leveling turned the loud song down to ' + vol.toFixed(2));
  // crossfade: jump to 3 s before the end
  const dur = await p.evaluate(() => window.__act().duration);
  await p.evaluate(d => { window.__act().currentTime = d - 6; }, dur);
  await sleep(2500);
  const mid = await p.evaluate(() => window.__as.map(a => ({ paused: a.paused, v: +a.volume.toFixed(2), src: !!a.getAttribute('src') })));
  console.log('elements during crossfade:', JSON.stringify(mid));
  await sleep(6000);
  const t1 = await p.locator('#miniTitle').textContent();
  ok(t0 !== t1 && /Shoot/i.test(t1), `crossfade moved on to the next song by itself (${t0} -> ${t1})`);
  ok(await p.evaluate(() => window.__as.filter(a => !a.paused).length) === 1, 'only one element is playing after the fade');

  // ---- queue: reorder, clear + undo
  await p.locator('#searchInput').fill('AC/DC Back In Black'); await p.waitForSelector('#searchBody .row[data-sid]');
  await p.locator('#searchBody .row[data-sid]').first().locator('.icon-btn.dim').click(); await p.locator('.sheet .item', { hasText: 'Add to Queue' }).click(); await sleep(400);
  await p.locator('#searchInput').fill('AC/DC Thunderstruck'); await p.waitForSelector('#searchBody .row[data-sid]');
  await p.locator('#searchBody .row[data-sid]').first().locator('.icon-btn.dim').click(); await p.locator('.sheet .item', { hasText: 'Add to Queue' }).click(); await sleep(400);
  await p.locator('#miniBody').click(); await p.waitForSelector('.np');
  await p.locator('.np .bar .icon-btn[aria-label=Queue]').click(); await p.waitForSelector('.queue .row');
  const before = await p.locator('.queue .row .t').allTextContents();
  ok(before.length >= 3, 'queue has ' + before.length + ' songs: ' + before.map(x => x.slice(0, 14)).join(' | '));
  const g = await p.locator('.queue .row').nth(before.length - 1).locator('.grip').boundingBox();
  await drag(g.x + g.width / 2, g.y + g.height / 2, g.x + g.width / 2, g.y + g.height / 2 - 70);   // drag the last row up about one row
  await sleep(500);
  const after = await p.locator('.queue .row .t').allTextContents();
  ok(JSON.stringify(before) !== JSON.stringify(after) && after[after.length - 2] === before[before.length - 1], 'dragging the last row up reordered the queue');
  await p.screenshot({ path: '/t/queue.png' });
  await p.locator('.backbar .pillbtn', { hasText: 'Clear queue' }).click(); await sleep(400);
  const kept = await p.locator('.queue .row').count();
  ok(kept === (await p.locator('.queue .row.playing').evaluate(e => +e.dataset.i)) + 1, 'Clear queue removed everything after the playing song (kept ' + kept + ')');
  await p.locator('.toast .undo-btn').click(); await sleep(400);
  ok(await p.locator('.queue .row').count() === before.length, 'Undo restored the cleared queue');
  while (await p.locator('.np, .queue').count()) { await p.goBack(); await sleep(400); }

  // ---- sleep timer: 90 and 120
  await p.locator('#miniBody').click(); await p.waitForSelector('.np');
  await p.locator('.np .chip-btn[aria-label="Sleep timer"]').click();
  ok(await p.locator('.sheet .item', { hasText: /^90 minutes$/ }).count() === 1 && await p.locator('.sheet .item', { hasText: /^120 minutes$/ }).count() === 1, 'sleep timer has 90 and 120 minute options');
  await p.keyboard.press('Escape'); await p.goBack(); await sleep(300); await p.goBack(); await sleep(300);

  // ---- pull to refresh on Home
  await p.locator('.tabbtn[data-tab=home]').click(); await sleep(800);
  const n0 = reqs.filter(u => /getAlbumList2/.test(u)).length;
  await drag(200, 120, 200, 420);
  await sleep(2500);
  ok(reqs.filter(u => /getAlbumList2/.test(u)).length > n0, 'pull-down on Home refreshed the lists');

  // ---- podcasts: Up next row, skip silence
  await p.locator('.chip', { hasText: 'Podcasts' }).first().click(); await sleep(2500);
  ok(await p.locator('h2.sec', { hasText: 'Up next' }).count() === 1, 'Podcasts tab shows an "Up next" row');
  await p.screenshot({ path: '/t/upnext.png' });
  await p.locator('.avatar').first().click(); await p.locator('.sheet .item', { hasText: 'Playback & display' }).click();
  await p.locator('.sheet .item', { hasText: 'Skip silence' }).click(); await sleep(300);
  await p.locator('.tabbtn[data-tab=library]').click(); await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row', { hasText: 'Conan' }).first().click();
  await p.waitForSelector('#tab-library .page:last-child .ep');
  await p.locator('#tab-library .page:last-child .ep').first().locator('.ep-play').click(); await sleep(2500);
  let ranges = null;
  for (let i = 0; i < 20 && !ranges; i++) {
    const id = await p.evaluate(() => (JSON.parse(localStorage.getItem('tr.queue') || '{}').list || [])[JSON.parse(localStorage.getItem('tr.queue') || '{}').index || 0]?.id);
    const r = await (await p.request.get('http://tr-test:8000/api/podcast/silences?id=' + id)).json();
    if (r.ready) ranges = r.ranges; else await sleep(3000);
  }
  ok(ranges && ranges.length > 0, 'Titan found ' + (ranges ? ranges.length : 0) + ' silent gaps in the episode');
  if (ranges && ranges.length) {
    const mid = ranges.slice(0, -1).filter(r => r[0] > 30); const [a, e] = mid.find(r => r[1] - r[0] > 0.9) || mid[0];
    let t = 0;
    for (let k = 0; k < 8; k++) {   // the app picks the silences up on its next poll (every 15 s)
      await p.evaluate(a => { const x = window.__act(); x.currentTime = a + 0.4; x.dispatchEvent(new Event('timeupdate')); }, a);
      await sleep(700);
      t = await p.evaluate(() => window.__act().currentTime);
      if (t >= e - 0.5) break;
      await sleep(5000);
    }
    ok(t >= e - 0.5, `skip silence jumped over the gap ${a.toFixed(1)}-${e.toFixed(1)} s (now at ${t.toFixed(1)})`);
  }
  console.log('page errors:', errs.length ? errs.slice(0, 5) : 'none'); if (errs.length) fails++;
  await b.close(); console.log(fails ? 'FAILED ' + fails : 'ALL PASSED');
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 3).join(' | ')); process.exit(2); });
