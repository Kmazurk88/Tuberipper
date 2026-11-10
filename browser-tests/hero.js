// "Pick up where you left off" card can be hidden (X), undone, and stays hidden across reloads
const { chromium } = require('playwright');
const BASE = process.argv[2]; let fails = 0;
const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) fails++; };
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(e.message));
  await require('./bm.js')(p);
  await p.addInitScript(() => localStorage.setItem('tr.debug', '1'));
  await p.goto(BASE); await p.waitForSelector('.recents-grid');
  // two saved positions (in the fake store): the newest becomes the big card
  const songs = (await (await p.request.get(BASE + 'api/nd/rest/getRandomSongs?size=2')).json())['subsonic-response'].randomSongs.song;
  p.__bookmarks.set(songs[0].id, { position: 20000, changed: '2026-01-01T00:00:00Z' });
  p.__bookmarks.set(songs[1].id, { position: 30000, changed: '2026-01-02T00:00:00Z' });
  await p.reload(); await p.waitForSelector('.hero-resume');
  const title = await p.locator('.hero-resume b').textContent();
  const before = await p.locator('.hrow .card .nm', { hasText: title }).count();
  await p.screenshot({ path: '/t/hero.png' });
  await p.locator('.hr-x').click();
  await p.waitForSelector('.hero-resume', { state: 'detached' });
  ok(true, 'X hides the card');
  await p.waitForTimeout(800);
  ok(await p.evaluate(() => !cur()), 'tapping X did not start playback');
  await p.waitForFunction(t => [...document.querySelectorAll('.hrow .card .nm')].some(e => e.textContent === t), title);
  ok(true, 'the item moved to "Continue listening" (was there before: ' + before + ')');
  await p.locator('.undo-btn').click();
  await p.waitForSelector('.hero-resume');
  ok(true, 'Undo brings it back');
  await p.locator('.hr-x').click(); await p.waitForSelector('.hero-resume', { state: 'detached' });
  await p.reload(); await p.waitForSelector('.recents-grid'); await p.waitForTimeout(2500);
  ok(await p.locator('.hero-resume').count() === 0, 'still hidden after reopening the app');
  await p.screenshot({ path: '/t/hero-hidden.png' });
  console.log('errors:', errs); console.log(fails || errs.length ? 'FAILED' : 'ALL PASSED');
  await b.close(); process.exit(fails || errs.length ? 1 : 0);
})();
