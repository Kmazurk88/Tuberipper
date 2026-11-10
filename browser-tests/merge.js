const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0; const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) failures++; };
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const p = await (await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  const lp = '#tab-library .page:last-child';
  await p.locator('.tabbtn[data-tab=library]').click(); await p.locator('#tab-library .chip', { hasText: 'Podcasts' }).click(); await sleep(1200);
  const names = await p.locator('#tab-library .row .t').allInnerTexts();
  console.log('   library podcasts:', names.filter(n => /Lounge|Conan|Weekly|Smoke/.test(n)).join(' | '));
  ok(names.filter(n => n.startsWith('Lounge Lizards')).length === 1, 'Library lists ONE Lounge Lizards podcast');
  await p.locator('#tab-library .row:has-text("Lounge Lizards")').first().click(); await p.waitForSelector(lp + ' .ep'); await sleep(3000);
  const n = await p.locator(lp + ' .ep').count();
  ok(n === Number(process.env.EXPECT || 47), "the podcast page lists every episode of the show together (' + n + ')');
  const dates = await p.locator(lp + ' .ep .ep-meta span:first-child').allInnerTexts();
  console.log('   newest first:', dates.slice(0, 2).join(' | '), '... oldest (end of list):', dates.slice(-2).join(' | '));
  ok(/Nov 2026|^0\d Nov$|\d{2} Nov$/.test(dates[0]) && /2025/.test(dates[dates.length - 1]), 'newest is Nov this year, oldest are from 2025: a single timeline across both old albums');
  await p.screenshot({ path: '/out/90-merged.png' });
  await p.locator(lp + ' .pod-tab', { hasText: 'About' }).click();
  await p.waitForFunction(() => !document.querySelector('#tab-library .page:last-child .spinner'), null, { timeout: 30000 }); await sleep(500);
  const about = await p.locator(lp).innerText();
  ok(about.includes((process.env.EXPECT || 47) + " episodes"), 'About says 53 episodes: ' + (about.match(/\d+ episodes[^\n]*/) || ['?'])[0]);
  console.log('   ' + (about.match(/Latest episode[^\n]*/) || [''])[0] + ' | ' + (about.match(/First episode[^\n]*/) || [''])[0]);
  // More like this doesn't list the show itself twice
  await p.locator(lp + ' .pod-tab', { hasText: 'More like this' }).click(); await sleep(1500);
  const more = await p.locator(lp + ' .lib-grid .card .nm').allInnerTexts();
  ok(!more.some(x => x.startsWith('Lounge Lizards')), 'More like this does not list Lounge Lizards again: ' + more.join(' | '));
  // search + browse tile
  await p.locator('.tabbtn[data-tab=search]').click(); await p.fill('#searchInput', 'lounge lizards'); await p.waitForSelector('#searchBody .rowsec:has-text("Albums")'); await sleep(1200);
  const albumsSec = await p.locator('#searchBody').innerText();
  console.log('   search "Albums" occurrences of the show:', (albumsSec.match(/Lounge Lizards - a Cigar/g) || []).length);
  await p.fill('#searchInput', ''); await p.waitForSelector('.tile'); await p.locator('.tile', { hasText: 'Podcasts' }).click(); await sleep(1500);
  const browse = await p.locator('#tab-search .page:last-child .row .t').allInnerTexts();
  ok(browse.filter(x => x.startsWith('Lounge Lizards')).length === 1, 'Search > Podcasts tile lists the show once: ' + browse.join(' | '));
  console.log('errors:', errs); await b.close(); console.log(failures ? failures + ' FAILED' : 'ALL PASSED'); process.exit(failures ? 1 : 0);
})().catch(e => { console.error('CRASH', e.message.split('\n').slice(0, 5).join('\n')); process.exit(2); });
