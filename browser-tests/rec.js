const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
const BASE = process.argv[2];
const songs = ['One', 'Two', 'Three', 'Four', 'Five'].map((t, i) => ({ title: 'Song ' + t, artist: 'Test Artist ' + i, album: 'Alb', duration: 200, video_id: 'vid' + t, artwork: '', year: '2001', because: 'Seed' }));
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await require('./bm.js')(p);
  const posted = [];
  await p.route('**/api/recommend/home*', r => r.fulfill({ json: { lastfm: true, shelves: [{ id: 'discover', title: 'Discover', sub: '', songs }, { id: 'because:seed', title: 'Because you listened to Seed', sub: '', songs: songs.slice(0, 4) }] } }));
  await p.route('**/api/jobs/catalog', async r => { posted.push(JSON.parse(r.request().postData())); await r.fulfill({ json: { id: 'job1', status: 'processing', tracks: [] } }); });
  await p.route('**/api/jobs/job1', r => r.fulfill({ json: { id: 'job1', status: 'done', tracks: [{ status: 'done' }] } }));
  await p.route('**/api/preview/**', () => {});          // no real YouTube from the test: the stream just never answers
  await p.addInitScript(() => { const A = window.Audio; window.__as = []; window.Audio = class extends A { constructor() { super(); window.__as.push(this); } };
    window.__act = () => window.__as.find(a => a.src && a.src.includes('/api/preview')) || window.__as[0]; });
  await p.goto(BASE);
  await p.waitForSelector('.recents-grid');
  await p.waitForSelector('.card.rec', { timeout: 8000 });
  ok(await p.locator('h2.sec', { hasText: /^Discover$/ }).count() === 1 && await p.locator('h2.sec', { hasText: 'Because you listened to Seed' }).count() === 1, 'both recommendation shelves are on Home');
  ok(await p.locator('.card.rec').count() === 9, 'cards: ' + await p.locator('.card.rec').count());
  await p.locator('.card.rec').nth(1).scrollIntoViewIfNeeded();
  await p.screenshot({ path: '/t/rec-home.png' });

  // tap the second card: plays from the second song on, and adds only that song
  await p.locator('.card.rec').nth(1).click();
  await sleep(1500);
  const src = await p.evaluate(() => (window.__act() || {}).src || '');
  ok(/\/api\/preview\/vidTwo$/.test(src), 'streams the tapped song straight away: ' + src.replace(/^.*\/api/, '/api'));
  ok(posted.length === 1 && posted[0].song.title === 'Song Two' && posted[0].song.video_id === 'vidTwo', 'add-to-library started at the same time: ' + JSON.stringify(posted.map(x => x.song && x.song.title)));
  ok(await p.evaluate(() => document.querySelector('#miniTitle, #mini .t, #mini') && document.querySelector('#mini').textContent.includes('Song Two')), 'mini player shows the song');
  await p.locator('#miniBody').click();
  await p.waitForSelector('.np');
  await p.screenshot({ path: '/t/rec-np.png' });
  // the queue holds the rest of the shelf
  const q = JSON.parse(await p.evaluate(() => localStorage.getItem('tr.queue')));
  ok(q.list.length === 4 && q.list.every(s => s.online && s.videoId), 'queue = rest of the shelf, all streaming (' + q.list.length + ')');
  // heart on a streaming song adds it (here: already adding, so nothing new is posted)
  const before = posted.length;
  await p.locator('.np .heart').first().click().catch(() => {});
  await sleep(300);
  ok(posted.length === before, 'tapping the heart while it is already being added does not add twice');
  // sheet for a streaming song
  await p.keyboard.press('Escape');
  ok(errs.length === 0, 'no page errors' + (errs.length ? ': ' + errs.join(' | ') : ''));
  await b.close();
  console.log(fails ? fails + ' FAILED' : 'ALL PASSED'); process.exit(fails ? 1 : 0);
})();
