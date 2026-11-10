const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 412, height: 915 }, hasTouch: true, isMobile: true })).newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('response', r => { if (r.status() >= 400) errs.push(r.status() + ' ' + r.url()); });
  await p.goto('http://tuberipper:8000/'); await p.waitForSelector('.recents-grid'); await p.waitForTimeout(2000);
  console.log('tiles:', await p.locator('.rtile').count(), '| sections:', (await p.locator('h2.sec').allInnerTexts()).join(', '), '| errors:', JSON.stringify(errs));
  await b.close();
})();
