const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const BASE = process.argv[2];
const radioSongs = [1, 2, 3].map(i => ({ title: `Radio ${i}`, artist: `Radio Artist ${i}`, album: 'R', duration: 200, video_id: `radio00000${i}`.slice(0, 11), artwork: '', year: '', in_library: false, lib: null }));
const weekly = { week: '2026-W46', title: 'Discover Weekly', songs: [1, 2, 3, 4].map(i => ({ title: `Weekly ${i}`, artist: `Weekly Artist ${i}`, album: 'W', duration: 190, video_id: `weekly0000${i}`.slice(0, 11), artwork: '', year: '' })) };
async function newPage(b, init) {
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const p = await ctx.newPage();
  p.errs = []; p.on('pageerror', e => p.errs.push(e.message));
  await require('./bm.js')(p);
  await p.route('**/api/preview/**', () => {});
  await p.route('**/api/radio*', r => r.fulfill({ json: { seed: 'x', songs: radioSongs } }));
  await p.route('**/api/mixes*', async r => { const real = await (await r.fetch()).json(); await r.fulfill({ json: { ...real, weekly } }); });
  if (init) await p.addInitScript(init);
  await p.addInitScript(() => { localStorage.setItem('tr.debug', '1'); const A = window.Audio; window.__as = []; window.Audio = class extends A { constructor() { super(); window.__as.push(this); } }; window.__act = () => window.__as.find(a => a.src && !a.paused) || window.__as.find(a => a.src) || window.__as[0]; });
  await p.goto(BASE); await p.waitForSelector('.recents-grid');
  return p;
}
const menu = async (p, label) => { await p.locator('.avatar').first().click(); await p.locator('.sheet .item', { hasText: label }).click(); };
(async () => {
  const step = m => console.log('... ' + m);
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  let p = await newPage(b);

  // ---- Made for you
  await p.waitForSelector('.mixcard', { timeout: 20000 });
  const cards = await p.locator('.mixcard').count();
  ok(await p.locator('h2.sec', { hasText: 'Made for you' }).count() === 1 && cards >= 2, `"Made for you" shelf on Home with ${cards} cards (Discover Weekly + daily mixes from the real library)`);
  ok(await p.locator('.mixcard .mix-badge', { hasText: 'Discover Weekly' }).count() === 1, 'Discover Weekly card');
  await p.locator('.mixcard', { hasText: 'Daily Mix 1' }).first().click();
  await p.waitForSelector('.page .row');
  const nrows = await p.locator('.page .row').count();
  ok(nrows >= 10, `a Daily Mix page lists songs (${nrows})`);
  await p.screenshot({ path: '/t/x-mix.png' });
  await p.locator('.page .playbtn').click(); await sleep(1500);
  ok(await p.evaluate(() => P.list.length > 10 && !!cur()), 'Play starts the mix with a full queue');
  await p.evaluate(() => closeAllLayers()); await sleep(400);

  // ---- Discover Weekly: tap an item = play it now and start adding it
  const posted = []; await p.route('**/api/jobs/catalog', async r => { posted.push(JSON.parse(r.request().postData())); await r.fulfill({ json: { id: 'job1', status: 'processing', tracks: [] } }); });
  await p.route('**/api/jobs/job1', r => r.fulfill({ json: { id: 'job1', status: 'done', tracks: [{ status: 'done' }] } }));
  await p.locator('.mixcard', { hasText: 'Discover Weekly' }).first().click();
  await p.waitForSelector('.page .row');
  ok(await p.locator('.page .row').count() === 4 && await p.locator('.page .pillbtn', { hasText: 'Add all 4' }).count() === 1, 'Discover Weekly page: 4 songs and an "Add all" button');
  await p.screenshot({ path: '/t/x-weekly.png' });
  await p.locator('.page .row').nth(1).click(); await sleep(1200);
  const tsrc = await p.evaluate(() => (window.__as || []).map(a => (a.src || '').replace(/^.*\/api/, '/api') + (a.paused ? ' (paused)' : '')).join(' | '));
  const q0 = await p.evaluate(() => ({ first: P.list[0] && P.list[0].videoId, n: P.list.length }));
  ok(q0.first === 'weekly00002' && q0.n === 3 && /\/api\/preview\/weekly0000[23]/.test(tsrc) && posted.length === 1 && posted[0].song.title === 'Weekly 2', 'tapping a new song streams it and adds it: ' + tsrc + ' | posted=' + JSON.stringify(posted.map(x => x.song && x.song.title)));
  await p.locator('.page .pillbtn', { hasText: 'Add all 4' }).click(); await sleep(7000);
  ok(posted.length >= 5 && posted.slice(1).every(x => x.add && x.add.notify === false), `"Add all" queues the songs quietly (${posted.length - 1} jobs, no per-song Discord messages)`);
  await p.evaluate(() => closeAllLayers());

  // ---- Radio + autoplay
  step('radio');
  await p.evaluate(() => { playQueue([onlineSong({ title: 'Seed', artist: 'Seed Artist', album: '', duration: 100, video_id: 'seed0000001', artwork: '' })], 0, null); }); await sleep(800);
  step('ended1'); await p.evaluate(() => window.__act().dispatchEvent(new Event('ended'))); step('ended2'); await sleep(2500);
  const ap = await p.evaluate(() => ({ n: P.list.length, i: P.index, online: cur() && cur().online, t: cur() && cur().title }));
  ok(ap.n === 4 && ap.i === 1 && ap.online && /Radio 1/.test(ap.t), `queue ended -> autoplay appended similar songs and moved on (${ap.n} in queue, now "${ap.t}")`);
  await p.evaluate(() => lsSet('tr.autoplay', false)); await p.evaluate(() => { playQueue([onlineSong({ title: 'Seed2', artist: 'A', album: '', duration: 100, video_id: 'seed0000002', artwork: '' })], 0, null); }); await sleep(500);
  await p.evaluate(() => window.__act().dispatchEvent(new Event('ended'))); await sleep(1500);
  ok(await p.evaluate(() => P.list.length) === 1, 'autoplay off: the queue just ends');
  await p.evaluate(() => lsSet('tr.autoplay', true));
  await p.evaluate(() => { playQueue([onlineSong({ title: 'Seed3', artist: 'A', album: '', duration: 100, video_id: 'seed0000003', artwork: '' })], 0, null); }); await sleep(500);
  await p.locator('#miniBody').click(); await p.waitForSelector('.np');
  await p.locator('.np .icon-btn[aria-label="More"], .np [aria-label="More"]').first().click().catch(() => {});
  await sleep(500);
  const hasRadio = await p.locator('.sheet .item', { hasText: 'Start radio' }).count();
  ok(hasRadio === 1, 'now-playing menu has "Start radio"');
  await p.locator('.sheet .item', { hasText: 'Start radio' }).click(); await sleep(1500);
  ok(await p.evaluate(() => P.list.length) >= 3 && await p.evaluate(() => cur().title) === 'Seed3', 'Start radio queues the radio behind the seed');
  await p.evaluate(() => closeAllLayers());

  // ---- Stats
  const now = Math.floor(Date.now() / 1000);
  for (let i = 0; i < 12; i++) await p.evaluate(({ i, now }) => fetch(api('/api/stats/play'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid: 't' + i + now, ts: now - i * 3600, title: 'Song ' + (i % 4), artist: 'Artist ' + (i % 3), album: 'Alb', duration: 200 }) }), { i, now });
  await menu(p, 'Your listening stats'); await p.waitForSelector('.stat-tile', { timeout: 8000 });
  const tiles = await p.locator('.stat-tile b').allTextContents();
  ok(+tiles[1].replace(/,/g, '') >= 12 && +tiles[2] >= 3, `stats tiles: ${tiles.join(' / ')} (plays / artists)`);
  ok(await p.locator('.bars .bar').count() === 31, 'hour + weekday charts drawn');
  await p.screenshot({ path: '/t/x-stats.png' });
  await p.locator('.chip', { hasText: 'All time' }).click(); await sleep(800);
  ok(await p.locator('.stat-tile b').first().isVisible(), 'switching period works');
  await p.locator('.wrapped-banner', { hasText: 'Replay your' }).click(); await p.waitForSelector('.wrapped');
  const n0 = await p.locator('.w-dots span').count();
  await p.screenshot({ path: '/t/x-wrapped1.png' });
  for (let k = 1; k < n0; k++) await p.locator('.w-btn.main').click();
  ok(n0 >= 5 && await p.locator('.w-btn.main').textContent() === 'Done', `year slideshow: ${n0} slides, ends with Done`);
  await p.screenshot({ path: '/t/x-wrapped2.png' });
  await p.locator('.w-btn.main').click(); await sleep(300);
  ok(await p.locator('.wrapped').count() === 0, 'Done closes the slideshow');
  await p.evaluate(() => closeAllLayers());

  // ---- Library health (read-only scan of the real library)
  await menu(p, 'Library health'); await p.locator('.pillbtn', { hasText: 'Scan library' }).click();
  await p.waitForSelector('.hcard', { timeout: 120000 });
  const hc = await p.locator('.hcard').count();
  const title = await p.locator('.hc-sum').first().textContent();
  ok(hc >= 5, `health report drawn (${hc} cards): ${title}`);
  await p.screenshot({ path: '/t/x-health.png' });
  await p.evaluate(() => closeAllLayers());
  ok(p.errs.length === 0, 'no page errors' + (p.errs.length ? ': ' + p.errs.join(' | ') : ''));
  await p.context().close();

  // ---- Audio effects on
  p = await newPage(b, () => localStorage.setItem('tr.fx', 'true'));
  const fx = await p.evaluate(() => ({ on: FX.on, ctx: !!FX.ctx, f: FX.filters.length }));
  ok(fx.on && fx.ctx && fx.f === 5, 'audio effects graph is built when switched on');
  await menu(p, 'Playback & display');
  ok(await p.locator('.sheet .item', { hasText: 'Audio effects' }).textContent().then(t => /On/.test(t)), 'settings shows Audio effects: On');
  await p.locator('.sheet .item', { hasText: /^Equalizer$/ }).click(); await p.waitForSelector('.eq-row');
  await p.locator('.chip', { hasText: 'Bass boost' }).click();
  ok(await p.evaluate(() => FX.filters[0].gain.value) === 6 && await p.evaluate(() => FX.pre.gain.value) < 0.6, 'preset moves the filters (bass +6 dB, headroom lowered)');
  await p.locator('.eq-row input').nth(4).evaluate(e => { e.value = '-5'; e.dispatchEvent(new Event('input')); });
  ok(await p.evaluate(() => FX.filters[4].gain.value) === -5 && await p.locator('.dialog .chip.on').textContent() === 'Custom', 'moving a slider changes that band and the preset becomes Custom');
  await p.screenshot({ path: '/t/x-eq.png' });
  await p.evaluate(() => closeAllLayers());
  const mp3 = await p.evaluate(async () => { const r = await nd('search3', { query: 'Soul Kitchen Doors', songCount: 20, artistCount: 0, albumCount: 0 }); return (arr(r.searchResult3.song).find(s => s.suffix === 'mp3') || {}); });
  if (mp3.id) {
    await p.evaluate(m => { playQueue([m], 0, null); }, mp3); await sleep(3000);
    const lvl = await p.evaluate(() => { const d = new Uint8Array(FX.an.frequencyBinCount); FX.an.getByteFrequencyData(d); return { max: Math.max(...d), t: window.__act().currentTime, state: FX.ctx.state }; });
    ok(lvl.state === 'running' && lvl.t > 1 && lvl.max > 0, `a real mp3 plays through the effects graph (ctx ${lvl.state}, ${lvl.t.toFixed(1)}s, analyser peak ${lvl.max})`);
    await p.evaluate(() => { window.__act().volume = 0; }); await sleep(1200);
    const quiet = await p.evaluate(async () => { const d = new Uint8Array(FX.an.frequencyBinCount); for (let i = 0; i < 25; i++) { FX.an.getByteFrequencyData(d); await new Promise(r => setTimeout(r, 60)); } return Math.max(...d); });
    ok(quiet < 5, `element volume still applies through the graph (peak with volume 0: ${quiet})`);
    await p.evaluate(() => { window.__act().volume = 1; });
    await p.locator('#miniBody').click(); await p.waitForSelector('.np');
    await p.locator('.chip-btn', { hasText: 'Visualizer' }).click(); await p.waitForSelector('canvas.viz');
    await sleep(900);
    const px = await p.locator('canvas.viz').evaluate(c => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; });
    ok(px > 200, `visualizer draws bars on the canvas (${px} painted pixels)`);
    await p.screenshot({ path: '/t/x-viz.png' });
  } else console.log('SKIP no mp3 found for the audio-graph checks');
  ok(p.errs.length === 0, 'no page errors with effects on' + (p.errs.length ? ': ' + p.errs.join(' | ') : ''));
  await b.close();
  console.log(fails ? fails + ' FAILED' : 'ALL PASSED'); process.exit(fails ? 1 : 0);
})();
