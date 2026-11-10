const { chromium } = require('playwright');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true })).newPage();
  // simulate a phone that cached the stylesheet from before the picker existed
  await p.route('**/static/app.css*', async route => { const r = await route.fetch(); let t = await r.text(); t = t.slice(0, t.indexOf('/* "Saved in" playlist picker */')); await route.fulfill({ response: r, body: t }); });
  await p.goto('http://tr-test:8000/'); await p.waitForSelector('.recents-grid');
  await p.locator('.tabbtn[data-tab=search]').click(); await p.fill('#searchInput', 'piano man'); await p.waitForSelector('#searchBody .row[data-sid]');
  await p.locator('#searchBody .row[data-sid]').first().locator('button[aria-label=More]').click();
  await p.locator('.sheet .item', { hasText: 'Add to playlist' }).click(); await sleep(2500);
  await p.screenshot({ path: '/out/60-stale-css.png' });
  await b.close();
})();
