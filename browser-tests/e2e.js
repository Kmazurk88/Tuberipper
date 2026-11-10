const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://tr-test:8000';
const OUT = '/out';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };

(async () => {
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await require('./bm.js')(page);
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  page.on('response', r => { if (r.status() >= 400 && !r.url().includes('/api/nd/cover/')) errors.push(`http ${r.status()} ${r.url()}`); });

  await page.goto(BASE + '/');
  await page.waitForSelector('.recents-grid', { timeout: 15000 });
  ok(await page.locator('.tabbtn').count() === 4, 'four bottom tabs');
  ok((await page.locator('.tabbtn').allInnerTexts()).join('|').replace(/\s+/g, ' ') === 'Home|Search|Your Library|Create', 'tab labels Home/Search/Your Library/Create');
  ok(await page.locator('.chip', { hasText: 'Podcasts' }).count() === 1, 'home chips present');
  await sleep(1500);
  await page.screenshot({ path: OUT + '/01-home.png' });

  // chips
  await page.locator('.chip', { hasText: 'Podcasts' }).first().click();
  await page.waitForSelector('h2.sec:has-text("Your podcasts")');
  await page.screenshot({ path: OUT + '/02-home-podcasts.png' });
  await page.locator('.chip', { hasText: 'All' }).first().click();
  await page.waitForSelector('h2.sec:has-text("Recently added")');

  // search idle
  await page.locator('.tabbtn[data-tab=search]').click();
  await page.waitForSelector('.tile');
  await sleep(800);
  ok(await page.locator('.tile', { hasText: 'Music' }).count() >= 1 && await page.locator('.tile', { hasText: 'Podcasts' }).count() === 1, 'search tiles: Music + Podcasts');
  ok(await page.locator('.tile').count() > 3, 'genre tiles present (' + await page.locator('.tile').count() + ' tiles)');
  await page.screenshot({ path: OUT + '/03-search.png' });

  // search typing: library results + online results
  await page.fill('#searchInput', 'billy joel');
  await page.waitForSelector('#searchBody .row[data-sid]', { timeout: 15000 });
  ok(true, 'library search returned songs');
  await page.waitForFunction(() => document.body.innerText.includes('Albums to add') || document.body.innerText.includes('Online search failed') || document.body.innerText.includes('No online results'), null, { timeout: 40000 });
  const online = await page.evaluate(() => document.body.innerText.includes('Online search failed'));
  ok(!online, 'online (YouTube Music/iTunes) search worked');
  await page.screenshot({ path: OUT + '/04-search-results.png' });

  // tile -> browse
  await page.fill('#searchInput', '');
  await page.waitForSelector('.tile');
  await page.locator('.tile', { hasText: 'Podcasts' }).click();
  await page.waitForSelector('.backbar');
  await page.waitForSelector('#tab-search .page:last-child .row');
  await sleep(1500);
  ok(await page.locator('#tab-search .page:last-child .row').count() >= 3, 'Podcasts browse lists shows');
  await page.screenshot({ path: OUT + '/05-browse-podcasts.png' });
  await page.goBack();
  await sleep(400);
  ok(await page.locator('.backbar').count() === 0, 'Android/browser back closes the page');

  // library
  await page.locator('.tabbtn[data-tab=library]').click();
  await page.waitForSelector('#tab-library .row');
  await sleep(800);
  ok((await page.locator('#tab-library .row .t').first().innerText()) === 'Liked Songs', 'library: Liked Songs first');
  await page.screenshot({ path: OUT + '/06-library.png' });
  await page.locator('#tab-library .chip', { hasText: 'Albums' }).click();
  await page.waitForFunction(() => document.querySelectorAll('#tab-library .row').length > 50, null, { timeout: 15000 });
  ok(true, 'Albums chip lists all albums');
  await page.locator('.lib-tools button[aria-label="Change view"]').click();
  await page.waitForSelector('.lib-grid');
  await page.screenshot({ path: OUT + '/07-library-grid.png' });
  await page.locator('.lib-tools button[aria-label="Change view"]').click();
  await page.locator('#tab-library .chip', { hasText: 'Albums' }).click(); // deselect
  await page.locator('#tab-library .chip', { hasText: 'Playlists' }).click();
  await page.waitForSelector('#tab-library .row:has-text("Commute")');
  ok(true, 'Playlists chip shows existing playlists');

  // playlist page
  await page.locator('#tab-library .row', { hasText: 'Commute' }).first().click();
  await page.waitForSelector('.hero h1:has-text("Commute")');
  await page.waitForSelector('#tab-library .page:last-child .row[data-sid]');
  await sleep(1200);
  await page.screenshot({ path: OUT + '/08-playlist.png' });
  await page.goBack();

  // create menu
  await page.locator('.tabbtn[data-tab=create]').click();
  await page.waitForSelector('.create-sheet');
  ok(await page.locator('.create-sheet .item').count() === 3, 'create menu has 3 items');
  await sleep(700);
  await page.screenshot({ path: OUT + '/09-create.png' });
  await page.locator('.scrim').first().click();
  await sleep(300);
  ok(await page.locator('.create-sheet').count() === 0, 'create menu closes');

  // podcast playback + resume
  await page.locator('.tabbtn[data-tab=library]').click();
  await page.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await page.waitForSelector('#tab-library .row:has-text("Conan")');
  await page.locator('#tab-library .row', { hasText: 'Conan' }).first().click();
  await page.waitForSelector('#tab-library .page:last-child .pod-bar'); await sleep(1500);
  await page.screenshot({ path: OUT + '/10-podcast.png' });
  await page.locator('#tab-library .page:last-child .ep').first().locator('.ep-play').click();
  await page.waitForFunction(() => document.querySelector('#mini') && !document.querySelector('#mini').classList.contains('hidden'));
  await sleep(2500);
  const t0 = await page.evaluate(() => { const a = [...document.querySelectorAll('audio')]; return null; });
  await sleep(500);
  await page.screenshot({ path: OUT + '/11-mini-player.png' });
  await page.locator('#miniBody').click({ position: { x: 100, y: 20 } });
  await page.waitForSelector('.np');
  await sleep(700);
  await page.screenshot({ path: OUT + '/12-now-playing.png' });
  ok(await page.locator('.np button[aria-label="Back 15 seconds"]').isVisible(), 'now playing shows skip buttons for a podcast');
  // seek to ~25 s via the slider, pause, and look at the bookmark Navidrome holds
  const info = await page.evaluate(() => ({ title: document.querySelector('.np .ttl').textContent }));
  console.log('playing:', info.title.slice(0, 60));
  await page.evaluate(() => { const r = document.querySelector('.np input[type=range]'); r.value = 4; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change')); });
  await sleep(2500);
  await page.locator('.np .big').click(); // pause -> saves bookmark
  await sleep(1200);
  const marks = await page.evaluate(async () => (await (await fetch('api/nd/rest/getBookmarks')).json())['subsonic-response'].bookmarks.bookmark || []);
  ok(marks.length === 1 && marks[0].position > 5000, 'pausing saved a bookmark (' + (marks[0] ? Math.round(marks[0].position / 1000) + 's' : 'none') + ')');
  const savedPos = marks[0] ? marks[0].position / 1000 : 0;
  const savedId = marks[0] && marks[0].entry.id;

  // reload the app: it should offer "Continue listening" and resume at the saved spot
  await page.reload();
  await page.waitForSelector('.recents-grid');
  await page.waitForSelector('h2.sec:has-text("Continue listening")');
  await sleep(800);
  await page.screenshot({ path: OUT + '/13-home-continue.png' });
  await page.locator('.hrow .card').first().click();
  await sleep(3500);
  const pos = await page.evaluate(() => { const t = document.querySelector('#miniBar'); return t ? parseFloat(t.style.width) : -1; });
  const now = await page.evaluate(async () => (await (await fetch('api/nd/rest/getBookmarks')).json())['subsonic-response'].bookmarks.bookmark?.[0]?.position);
  ok(pos > 0, 'resumed playback shows progress (' + pos.toFixed(1) + '%)');
  ok(now / 1000 >= savedPos - 1, `resumed from about ${Math.round(savedPos)}s, not from 0 (bookmark now ${Math.round(now / 1000)}s)`);
  await page.locator('#miniPlay').click(); // pause
  await sleep(500);

  // like toggle
  await page.locator('#miniHeart').click();
  await sleep(800);
  const liked = await page.evaluate(async () => (await (await fetch('api/nd/rest/getStarred2')).json())['subsonic-response'].starred2.song?.length || 0);
  ok(liked === 1, 'heart stars the song in Navidrome');
  await page.locator('#miniHeart').click();
  await sleep(800);
  const liked2 = await page.evaluate(async () => (await (await fetch('api/nd/rest/getStarred2')).json())['subsonic-response'].starred2.song?.length || 0);
  ok(liked2 === 0, 'heart again un-stars it');

  // cleanup the test bookmark
  if (savedId) await page.evaluate(id => fetch('api/nd/rest/deleteBookmark?id=' + id), savedId);

  console.log('\nERRORS (' + errors.length + '):'); errors.slice(0, 15).forEach(e => console.log('  ' + e));
  console.log(failures ? `\n${failures} FAILED` : '\nALL PASSED');
  await browser.close();
  process.exit(failures || errors.some(e => e.startsWith('pageerror')) ? 1 : 0);
})().catch(e => { console.error('TEST CRASHED', e); process.exit(2); });
