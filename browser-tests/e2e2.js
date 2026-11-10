const { chromium } = require('playwright');
const BASE = 'http://tr-test:8000', OUT = '/out';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };
const ndp = (page, ep) => page.evaluate(async ep => (await (await fetch('api/nd/rest/' + ep)).json())['subsonic-response'], ep);
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage(); const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => d.dismiss());
  await page.goto(BASE + '/'); await page.waitForSelector('.recents-grid');
  for (const p of ((await ndp(page, 'getPlaylists')).playlists.playlist || []).filter(p => p.name.startsWith('ZZ '))) await page.evaluate(id => fetch('api/nd/rest/deletePlaylist?id=' + id), p.id);

  // --- Create > Playlist -> straight to search in "adding to" mode
  await page.locator('.tabbtn[data-tab=create]').click();
  await page.locator('.create-sheet .item', { hasText: 'Playlist' }).first().click();
  await page.waitForSelector('.dialog input');
  await page.fill('.dialog input', 'ZZ UI Test');
  await page.locator('.dialog .pillbtn', { hasText: 'Create' }).click();
  await page.waitForSelector('#addBanner .banner');
  ok((await page.locator('#addBanner').innerText()).includes('ZZ UI Test'), 'new playlist opens Search in "Adding to ZZ UI Test" mode');
  ok(await page.locator('.tabbtn[data-tab=search].active').count() === 1, 'switched to the Search tab');
  await page.fill('#searchInput', 'piano man');
  await page.waitForSelector('#searchBody .row[data-sid]');
  await page.locator('#searchBody .row[data-sid]').first().locator('button[aria-label=Add]').click();
  await sleep(800);
  let pls = (await ndp(page, 'getPlaylists')).playlists.playlist || [];
  let pl = pls.find(p => p.name === 'ZZ UI Test');
  ok(pl && pl.songCount === 1, 'tapping + added the song to the new Navidrome playlist');
  await page.screenshot({ path: OUT + '/20-adding-to.png' });
  await page.locator('#addBanner button', { hasText: 'Done' }).click();
  await page.waitForSelector('.hero h1:has-text("ZZ UI Test")');
  await page.waitForSelector('#tab-library .page:last-child .row[data-sid]');
  ok(await page.locator('#tab-library .page:last-child .row[data-sid]').count() === 1, 'Done opens the playlist showing 1 song');

  // --- pin from playlist page, appears first in Library
  await page.locator('#tab-library .page:last-child .actions .icon-btn').first().click();
  await sleep(1200);
  const st = await (await fetch(BASE + '/api/app/state')).json();
  ok(st.pins.some(p => p.name === 'ZZ UI Test'), 'pin saved on the server');

  // --- song menu: remove from playlist
  await page.locator('#tab-library .page:last-child .row[data-sid] button[aria-label=More]').click();
  await page.waitForSelector('.sheet');
  await sleep(300);
  await page.screenshot({ path: OUT + '/21-song-sheet.png' });
  await page.locator('.sheet .item', { hasText: 'Remove from this playlist' }).click();
  await sleep(1200);
  pl = ((await ndp(page, 'getPlaylists')).playlists.playlist || []).find(p => p.name === 'ZZ UI Test');
  ok(pl.songCount === 0, 'removed the song from the playlist');

  // --- library shows it pinned at top, then delete it through the UI
  await page.goBack(); await sleep(400);
  await page.locator('.tabbtn[data-tab=library]').click();
  await page.waitForSelector('#tab-library .row');
  await sleep(700);
  const names = await page.locator('#tab-library .row .t').allInnerTexts();
  ok(names[0] === 'Liked Songs' && names[1] === 'ZZ UI Test', 'pinned playlist sits right under Liked Songs: ' + names.slice(0, 3).join(' | '));
  await page.screenshot({ path: OUT + '/22-library-pinned.png' });
  await page.locator('#tab-library .row', { hasText: 'ZZ UI Test' }).locator('button[aria-label=More]').click();
  await page.locator('.sheet .item', { hasText: 'Open' }).click();
  await page.waitForSelector('.hero h1:has-text("ZZ UI Test")');
  await page.locator('#tab-library .page:last-child .actions .icon-btn[aria-label=More]').click();
  await page.locator('.sheet .item', { hasText: 'Delete playlist' }).click();
  await page.waitForSelector('.dialog');
  await page.locator('.dialog .pillbtn', { hasText: 'Delete' }).click();
  await sleep(1500);
  pls = (await ndp(page, 'getPlaylists')).playlists.playlist || [];
  ok(!pls.some(p => p.name === 'ZZ UI Test'), 'delete playlist works and removed it from Navidrome');
  ok(await page.locator('.backbar').count() === 0, 'page closes after deleting');
  const st2 = await (await fetch(BASE + '/api/app/state')).json();
  ok(!st2.pins.some(p => p.name === 'ZZ UI Test'), 'its pin was cleaned up');

  // --- Spotify CSV import (songs already in library: nothing is downloaded)
  const csv = 'Track URI,Track Name,Artist Name(s),Duration (ms)\nspotify:track:1,Piano Man,Billy Joel,339000\nspotify:track:2,Uptown Girl,Billy Joel,197000\nspotify:track:3,Zzzz Nonexistent Song Qqq,Nobody Atall,200000\n';
  require('fs').writeFileSync('/tmp/imp.csv', csv);
  await page.locator('.tabbtn[data-tab=create]').click();
  await page.locator('.create-sheet .item', { hasText: 'Import Spotify' }).click();
  await page.waitForSelector('.dialog input[type=file]');
  await page.setInputFiles('.dialog input[type=file]', '/tmp/imp.csv');
  await page.fill('.dialog input[type=text]', 'ZZ Import Test');
  await sleep(300);
  await page.screenshot({ path: OUT + '/23-import-dialog.png' });
  await page.locator('.dialog .pillbtn', { hasText: 'Import' }).last().click();
  await page.waitForSelector('#toasts .toast');
  let done = false;
  for (let i = 0; i < 40 && !done; i++) { await sleep(3000); done = (await page.locator('#toasts').innerText()).includes('Import finished'); }
  ok(done, 'import finished');
  console.log('toast:', (await page.locator('#toasts').innerText()).replace(/\n/g, ' '));
  await page.screenshot({ path: OUT + '/24-import-done.png' });
  await page.locator('#toasts .toast').last().click();
  await page.waitForSelector('.dialog');
  await sleep(300);
  await page.screenshot({ path: OUT + '/25-import-details.png' });
  pls = (await ndp(page, 'getPlaylists')).playlists.playlist || [];
  const ip = pls.find(p => p.name === 'ZZ Import Test');
  ok(ip && ip.songCount === 2, 'import made a Navidrome playlist with the 2 matched songs (' + (ip ? ip.songCount : 'none') + ')');
  if (ip) await page.evaluate(id => fetch('api/nd/rest/deletePlaylist?id=' + id), ip.id);
  console.log('errors:', errors); console.log(failures ? failures + ' FAILED' : 'ALL PASSED');
  await browser.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e); process.exit(2); });
