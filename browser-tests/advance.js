const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const p = await (await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true })).newPage();
  await require('./bm.js')(p);
  await p.addInitScript(() => { window.__calls = []; window.TRNative = { update: j => window.__calls.push({ t: Date.now(), ...JSON.parse(j) }), clear: () => {} }; });
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=library]').click();
  await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click();
  await p.locator('#tab-library .row', { hasText: 'Conan' }).first().click();
  await p.waitForSelector('#tab-library .page:last-child .ep');
  await p.locator('#tab-library .page:last-child .ep').last().locator('.ep-play').click();   // oldest episode: the next one is queued after it
  await sleep(3000);
  const first = await p.evaluate(() => window.__calls.filter(c => c.playing).pop());
  await p.evaluate(ms => window.TRNative_cmd('seek:' + ms), first.dur - 4000);        // jump to 4 s before the end
  await sleep(500); const mark = await p.evaluate(() => window.__calls.length);
  await sleep(12000);                                                                  // it ends and the next song starts by itself
  const after = await p.evaluate(m => window.__calls.slice(m), mark);
  const titles = [...new Set(after.map(c => c.title))];
  const pauses = after.filter(c => !c.playing);
  console.log('updates around the song change:', after.length, '| titles seen:', titles.length);
  console.log(titles.length >= 2 ? 'PASS the next song started by itself' : 'FAIL no auto-advance');
  console.log(pauses.length === 0 ? 'PASS never reported "paused" between songs (so the phone keeps the service in the foreground)' : 'FAIL reported paused ' + pauses.length + ' time(s)');
  const last = after[after.length - 1]; console.log(last && last.playing ? 'PASS ends in the playing state' : 'FAIL ends paused');
  await p.evaluate(async () => { const b = (await (await fetch('api/nd/rest/getBookmarks')).json())['subsonic-response'].bookmarks.bookmark || []; for (const x of b) await fetch('api/nd/rest/deleteBookmark?id=' + x.entry.id); });
  await b.close();
})().catch(e => { console.error('CRASH', e.message.split('\n')[0]); process.exit(2); });
