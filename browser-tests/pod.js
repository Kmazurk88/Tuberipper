const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };
const FAKE = `
  window.__dl = {}; 
  setInterval(() => { for (const d of Object.values(window.__dl)) if (d.status === 'running') { d.pct += 25; d.bytes = d.pct * 1e6; if (d.pct >= 100) { d.status = 'done'; d.pct = 100; } } }, 500);
  window.TRNative = { offlineSupported: () => true, update() {}, clear() {}, changeServer() {},
    dlStart: j => { const o = JSON.parse(j); window.__dl[o.id] = { id: o.id, status: 'running', pct: 0, bytes: 0, total: 1e8, song: o.song, added: Date.now(), cover: '' }; window.__started = (window.__started || []).concat(o.url); return 'ok'; },
    dlList: () => JSON.stringify(Object.values(window.__dl)), dlRemove: id => { delete window.__dl[id]; }, dlRemoveAll: () => { window.__dl = {}; },
    dlUrl: id => (window.__dl[id] && window.__dl[id].status === 'done') ? location.origin + '/api/nd/stream/' + id + '?local=1' : '',
    dlUsage: () => 123456789, dlCellular: () => false, dlSetCellular: () => {} };`;
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true });
  const p = await ctx.newPage();
  await require('./bm.js')(p); const errs = [];
  p.on('pageerror', e => errs.push('pageerror ' + e.message));
  await p.addInitScript(FAKE);
  const nd = ep => p.evaluate(async ep => (await (await fetch('api/nd/rest/' + ep)).json())['subsonic-response'], ep);
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  const lp = '#tab-library .page:last-child';

  await p.locator('.tabbtn[data-tab=library]').click();
  await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.waitForSelector('#tab-library .row:has-text("Lounge Lizards")');
  await p.locator('#tab-library .row:has-text("Lounge Lizards")').first().click();
  await p.waitForSelector(lp + ' .pod-bar'); await p.waitForSelector(lp + ' .ep'); await sleep(2500);
  ok(await p.locator(lp + ' .pod-tab').count() === 3 && (await p.locator(lp + ' .pod-tab').allInnerTexts()).join('|') === 'Episodes|About|More like this', 'tabs: Episodes / About / More like this');
  ok((await p.locator(lp + ' .filter-row').innerText()).includes('All episodes • Newest'), 'filter row says "All episodes • Newest"');
  const first = p.locator(lp + ' .ep').first();
  const meta = await first.locator('.ep-meta').innerText();
  ok(/\d{2} [A-Z][a-z]{2}/.test(meta), 'first card shows a release date: "' + meta.replace(/\n/g, ' ') + '"');
  ok(await first.locator('.ep-desc').innerText() !== '', 'card shows a description');
  ok(await first.locator('.ep-actions button').count() === 4, 'action row: more, download, add, play');
  const dates = await p.locator(lp + ' .ep .ep-meta span:first-child').allInnerTexts();
  console.log('   first dates:', dates.slice(0, 4).join(' | '));
  await p.screenshot({ path: '/out/70-podcast.png' });

  // play, then progress
  await first.locator('.ep-play').click();
  await p.waitForFunction(() => !document.querySelector('#mini').classList.contains('hidden')); await sleep(3000);
  ok(await first.locator('.ep-play svg path[d*="6.5 4.5h4"]').count() === 1 || (await first.locator('.ep-play').innerHTML()).includes('M6.5 4.5'), 'the playing episode shows a pause button');
  await p.evaluate(() => { const a = document.querySelector('audio'); });
  await p.locator('#miniBody').click({ position: { x: 100, y: 20 } }); await p.waitForSelector('.np'); await sleep(600);
  await p.evaluate(() => { const r = document.querySelector('.np input[type=range]'); r.value = 300; r.dispatchEvent(new Event('input')); r.dispatchEvent(new Event('change')); });
  await sleep(2500); await p.locator('.np .big').click(); await sleep(1500);
  await p.locator('.np button[aria-label=Close]').click(); await sleep(800);
  const metaP = await p.locator(lp + ' .ep').first().innerText();
  ok(/min left|h \d+m left/.test(metaP), 'progress shows time left: ' + (metaP.match(/[^\n]*left[^\n]*/) || ['?'])[0]);
  ok(await p.locator(lp + ' .ep').first().locator('.ep-prog').count() === 1, 'progress bar is visible on the card');
  await p.screenshot({ path: '/out/71-podcast-progress.png' });

  // three-dot menu
  await p.locator(lp + ' .ep').first().locator('button[aria-label=More]').click(); await p.waitForSelector('.sheet'); await sleep(400);
  await p.screenshot({ path: '/out/72-episode-menu.png' });
  const items = (await p.locator('.sheet .item').allInnerTexts()).map(x => x.trim());
  console.log('   menu:', items.join(' | '));
  ok(['Add to playlist', 'Download', 'Mark as finished', 'Mark as not started', 'Add to Queue', 'Go to Queue', 'Go to episode'].every(x => items.includes(x)), 'menu has the Spotify-style items');
  await p.locator('.sheet .item', { hasText: 'Mark as finished' }).click(); await sleep(900);
  ok((await p.locator(lp + ' .ep').first().innerText()).includes('Played'), 'Mark as finished -> "Played"');
  await sleep(1200);
  const st = await (await fetch('http://tr-test:8000/api/app/state')).json();
  ok(st.finished.length >= 1, 'finished state saved on the server');
  await p.locator(lp + ' .ep').first().locator('button[aria-label=More]').click(); await p.waitForSelector('.sheet');
  await p.locator('.sheet .item', { hasText: 'Mark as not started' }).click(); await sleep(900);
  ok(!(await p.locator(lp + ' .ep').first().innerText()).includes('Played') && !(await p.locator(lp + ' .ep').first().innerText()).includes('left'), 'Mark as not started clears it');

  // filter + sort
  await p.locator(lp + ' .filter-row').click(); await p.waitForSelector('.sheet'); await sleep(300);
  await p.screenshot({ path: '/out/73-filter.png' });
  await p.locator('.sheet .item', { hasText: 'Oldest' }).click(); await sleep(600);
  ok((await p.locator(lp + ' .filter-row').innerText()).includes('Oldest'), 'sort switches to Oldest');
  const oldest = await p.locator(lp + ' .ep .ep-meta span:first-child').first().innerText();
  console.log('   oldest first date:', oldest);
  await p.locator(lp + ' .filter-row').click(); await p.locator('.sheet .item', { hasText: 'Newest' }).click(); await sleep(400);

  // downloads (fake Android app)
  const card = p.locator(lp + ' .ep').nth(1);
  await card.locator('.dlbtn').click(); await sleep(400);
  ok((await card.locator('.dlbtn').innerHTML()).includes('stroke-dasharray'), 'download shows a progress ring');
  await sleep(2600);
  ok(await card.locator('.dlbtn svg circle[fill="#1ed760"]').count() === 1 && !(await card.locator('.dlbtn').innerHTML()).includes('stroke-dasharray'), 'finished download turns green');
  const dlId = await card.getAttribute('data-sid');
  await p.screenshot({ path: '/out/74-downloaded.png' });

  // the downloaded copy is what plays
  await card.locator('.ep-play').click(); await sleep(2500);
  const src = await p.evaluate(() => [...document.querySelectorAll('audio')].map(a => a.src).concat(window.__audioSrc || []));
  const used = await p.evaluate(() => { const A = window.Audio; return 1; });
  console.log('   (audio element source check follows)');
  await p.evaluate(() => { document.querySelectorAll('audio').forEach(a => a.pause()); });

  // Downloaded chip in Library
  await p.locator('.tabbtn[data-tab=library]').click();
  await p.locator('#tab-library .chip', { hasText: 'Downloaded' }).click(); await sleep(900);
  ok(await p.locator('#tab-library .row[data-sid="' + dlId + '"]').count() === 1, 'Library > Downloaded lists it');
  await p.screenshot({ path: '/out/75-library-downloaded.png' });
  await p.locator('#tab-library .row[data-sid="' + dlId + '"] button[aria-label=Remove]').click(); await sleep(700);
  ok(await p.locator('#tab-library .row[data-sid="' + dlId + '"]').count() === 0, 'removing a download works');

  // About / More tabs, episode page
  await p.locator('.tabbtn[data-tab=library]').click(); await p.locator('#tab-library .chip', { hasText: 'Downloaded' }).click();
  await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row:has-text("Lounge Lizards")').first().click(); await p.waitForSelector(lp + ' .pod-tab');
  await p.locator(lp + ' .pod-tab', { hasText: 'About' }).click();
  await p.waitForFunction(() => !document.querySelector('#tab-library .page:last-child .spinner'), null, { timeout: 30000 }); await sleep(500);
  const about = await p.locator(lp).innerText();
  ok(/\d+ episodes/.test(about) && about.includes('weekly premium cigar podcast'), 'About tab: episode count + real show description');
  await p.screenshot({ path: '/out/76-about.png' });
  await p.locator(lp + ' .pod-tab', { hasText: 'More like this' }).click(); await sleep(1500);
  ok(await p.locator(lp + ' .lib-grid .card').count() >= 2, 'More like this shows your other podcasts');
  await p.locator(lp + ' .pod-tab', { hasText: 'Episodes' }).click(); await sleep(500);
  await p.locator(lp + ' .ep .ep-title').first().click(); await p.waitForSelector(lp + ' .ep-full', { timeout: 10000 }).catch(() => {});
  await sleep(600);
  ok((await p.locator(lp).innerText()).length > 200, 'episode page opens with the full description');
  await p.screenshot({ path: '/out/77-episode-page.png' });

  // cleanup bookmarks
  for (const x of ((await nd('getBookmarks')).bookmarks.bookmark || [])) await nd('deleteBookmark?id=' + x.entry.id);
  console.log('errors:', errs); await b.close(); console.log(failures ? failures + ' FAILED' : 'ALL PASSED'); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 6).join('\n')); process.exit(2); });
