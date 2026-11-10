const { chromium } = require('playwright');
const BASE = 'http://tr-test:8000', OUT = '/out';
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };
(async () => {
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const page = await ctx.newPage(); const errors = [];
  page.on('pageerror', e => errors.push('pageerror ' + e.message));
  page.on('response', r => { if (r.status() >= 400 && !r.url().includes('/cover/') && !r.url().includes('googleusercontent')) errors.push(r.status() + ' ' + r.url()); });
  await page.goto(BASE + '/'); await page.waitForSelector('.recents-grid');
  const top = '#tab-search .page:last-child';

  // library search -> artist
  await page.locator('.tabbtn[data-tab=search]').click();
  await page.fill('#searchInput', 'billy joel');
  await page.waitForSelector('#searchBody .rowsec:has-text("Artists")');
  await page.locator('#searchBody .row', { hasText: 'Billy Joel' }).filter({ hasText: /^Billy Joel\s*Artist/ }).first().click();
  await page.waitForSelector(top + ' .artist-hero', { timeout: 40000 });
  await page.waitForSelector(top + ' h2.sec:has-text("About")', { timeout: 40000 });
  await sleep(2500);
  await page.screenshot({ path: OUT + '/30-artist-top.png' });
  ok((await page.locator(top + ' .ah-name').innerText()) === 'Billy Joel', 'artist page header');
  ok((await page.locator(top + ' .ah-sub').innerText()).includes('monthly listeners'), 'monthly listeners shown');
  const heads = await page.locator(top + ' h2.sec').allInnerTexts();
  ok(['Popular', 'Discography', 'Fans also like', 'About'].every(h => heads.includes(h)), 'sections: ' + heads.join(', '));
  const popRows = top + ' h2.sec:has-text("Popular") + div > .row';
  ok(await page.locator(popRows).count() === 5, '5 popular songs');
  const localPop = await page.locator(popRows + '[data-sid]').count();
  const onlinePop = await page.locator(popRows + ':not([data-sid]) button[aria-label="Add to library"]').count();
  ok(localPop >= 1 && onlinePop >= 1, `popular: ${localPop} playable from library, ${onlinePop} online-only with +`);
  await page.locator(top + ' h2.sec:has-text("Discography")').scrollIntoViewIfNeeded();
  await sleep(1500);
  await page.screenshot({ path: OUT + '/31-artist-discography.png' });
  const discRows = top + ' h2.sec:has-text("Discography") ~ div > .row';
  ok(await page.locator(discRows).count() >= 5, 'discography rows');
  ok(await page.locator(discRows + ' .added').count() >= 1 && await page.locator(discRows + ' button[aria-label="Add to library"]').count() >= 1, 'discography marks owned albums and offers + for the rest');
  await page.locator(top + ' h2.sec:has-text("About")').scrollIntoViewIfNeeded();
  await sleep(1200);
  await page.screenshot({ path: OUT + '/32-artist-about.png' });

  // play a local popular song
  await page.locator(top).evaluate(el => el.scrollTo(0, 0));
  await page.locator(popRows + '[data-sid]').first().click();
  await page.waitForFunction(() => !document.querySelector('#mini').classList.contains('hidden'));
  ok(true, 'tapping an owned popular song plays it');
  await page.locator('#miniPlay').click();

  // online album (first row with a + button)
  await page.locator(discRows + ':has(button[aria-label="Add to library"])').first().scrollIntoViewIfNeeded();
  await page.locator(discRows + ':has(button[aria-label="Add to library"])').first().locator('.t').click();
  await page.waitForSelector(top + ':last-child .hero h1', { timeout: 40000 });
  await sleep(2000);
  await page.screenshot({ path: OUT + '/33-online-album.png' });
  const albumTitle = await page.locator('#tab-search .page:last-child .hero h1').innerText();
  ok(await page.locator('#tab-search .page:last-child .row button[aria-label="Add to library"]').count() >= 1, 'online album "' + albumTitle + '" lists tracks with +');
  ok(await page.locator('#tab-search .page:last-child .pillbtn').count() === 1, 'has an Add album button');
  await page.goBack(); await sleep(400);

  // owned album from the discography -> local page + missing-songs section (if any)
  await page.locator(discRows + ':has(.added)').first().locator('.t').click();
  await page.waitForSelector('#tab-search .page:last-child .hero h1');
  await sleep(6000);
  const missingSec = await page.locator('#tab-search .page:last-child .rowsec:has-text("Not in your library")').count();
  console.log('local album page "' + await page.locator('#tab-search .page:last-child .hero h1').innerText() + '" missing-section shown:', missingSec === 1);
  await page.screenshot({ path: OUT + '/34-local-album.png' });
  ok(await page.locator('#tab-search .page:last-child .hero .lnk').count() === 1, 'artist name on the album page is a link');
  await page.goBack(); await sleep(300);

  // an album with songs missing from the library
  await page.locator('.tabbtn[data-tab=search]').click(); await sleep(500);
  await page.fill('#searchInput', 'appetite for destruction');
  await page.waitForSelector('#searchBody .rowsec:has-text("Albums")');
  await page.locator('#searchBody .row', { hasText: /^Appetite For Destruction/i }).first().click();
  await page.waitForSelector('#tab-search .page:last-child .hero h1');
  await page.waitForSelector('#tab-search .page:last-child .rowsec:has-text("Not in your library")', { timeout: 40000 });
  await sleep(2500);
  const lp = '#tab-search .page:last-child';
  await page.locator(lp + ' .rowsec:has-text("Not in your library")').scrollIntoViewIfNeeded();
  await sleep(800);
  await page.screenshot({ path: OUT + '/38-missing-tracks.png' });
  ok(await page.locator(lp + ' .rowsec:has-text("Not in your library") ~ .row button[aria-label="Add to library"]').count() === 9, 'album page lists the 9 missing songs with +');
  ok((await page.locator(lp + ' .pillbtn').innerText()).startsWith('Add all'), '"Add all" button');
  await page.goBack(); await sleep(300);

  // Now playing -> menu -> About this song + Go to artist
  await page.locator('#miniBody').click({ position: { x: 100, y: 20 } });
  await page.waitForSelector('.np'); await sleep(700);
  await page.locator('.np button[aria-label=More]').click();
  await page.waitForSelector('.sheet'); await sleep(300);
  await page.screenshot({ path: OUT + '/35-np-menu.png' });
  await page.locator('.sheet .item', { hasText: 'About this song' }).click();
  await page.waitForSelector('.dialog');
  await page.waitForFunction(() => !document.querySelector('.dialog .spinner'), null, { timeout: 30000 });
  await sleep(300);
  await page.screenshot({ path: OUT + '/36-about-song.png' });
  const dlg = await page.locator('.dialog').innerText();
  ok(dlg.includes('From Wikipedia') || dlg.includes('no write-up'), 'About this song resolved: ' + dlg.replace(/\n+/g, ' ').slice(0, 100));
  await page.locator('.dialog .pillbtn').click(); await sleep(400);
  await page.locator('.np button[aria-label=More]').click(); await page.waitForSelector('.sheet');
  await page.locator('.sheet .item', { hasText: 'Go to artist' }).click();
  await page.waitForFunction(() => !document.querySelector('.np'));
  await page.waitForSelector(top + ' .artist-hero', { timeout: 40000 });
  ok(true, 'Go to artist from Now Playing closes it and opens the artist');

  // Fans also like -> online-only artist
  await page.waitForSelector(top + ' h2.sec:has-text("Fans also like")', { timeout: 30000 });
  await page.locator(top + ' h2.sec:has-text("Fans also like") + .hrow .card').first().click();
  await page.waitForSelector('#tab-search .page:last-child .artist-hero', { timeout: 40000 });
  await page.waitForSelector('#tab-search .page:last-child h2.sec:has-text("Popular")', { timeout: 40000 });
  await sleep(2000);
  await page.screenshot({ path: OUT + '/37-related-artist.png' });
  ok(await page.locator('#tab-search .page:last-child .playbtn').count() === 0, 'online-only artist has no play button (nothing to play yet)');
  ok(await page.locator('#tab-search .page:last-child button[aria-label="Add to library"]').count() >= 3, 'online-only artist: songs/albums can be added');

  // podcast: no online lookup
  await page.locator('.tabbtn[data-tab=library]').click();
  await page.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await page.waitForSelector('#tab-library .row:has-text("Conan")');
  console.log('errors:', JSON.stringify(errors.slice(0, 8)));
  console.log(failures ? failures + ' FAILED' : 'ALL PASSED');
  await browser.close(); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 6).join('\n')); process.exit(2); });
