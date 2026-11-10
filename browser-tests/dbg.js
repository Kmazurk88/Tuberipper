const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true })).newPage();
  p.on('pageerror', e => console.log('pageerror', e.message)); p.on('console', m => console.log('console', m.type(), m.text()));
  p.on('response', r => { if (r.url().includes('Playlist')) console.log(r.status(), r.url()); });
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=library]').click(); await p.waitForSelector('#tab-library .row');
  await p.locator('#tab-library .chip', { hasText: 'Playlists' }).click(); await p.waitForTimeout(1000);
  await p.locator('#tab-library .row', { hasText: 'Commute' }).first().click(); await p.waitForTimeout(3000);
  console.log((await p.locator('#tab-library').innerText()).slice(0, 400));
  await p.screenshot({ path: '/out/dbg.png' }); await b.close();
})();
