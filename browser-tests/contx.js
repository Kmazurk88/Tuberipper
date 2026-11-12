// Continue listening: the X on a card removes it (deleteBookmark, fake store only), Undo puts it back.
const { chromium } = require('playwright');
(async () => {
  const base = process.argv[2];
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 400, height: 860 } });
  const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) process.exitCode = 1; };
  const song = (id, t) => ({ id, title: t, artist: 'Some Show', album: 'Some Show', albumId: 'al' + id, duration: 3600, suffix: 'mp3', isDir: false, type: 'music', path: 'p/' + id + '.mp3' });
  const store = new Map([['s1', 600000], ['s2', 1200000], ['s3', 300000]]);
  const calls = [];
  const sub = extra => ({ 'subsonic-response': { status: 'ok', version: '1.16.1', ...extra } });
  // Navidrome is entirely faked: nothing reaches the user's real account.
  await p.route('**/api/nd/rest/**', r => {
    const u = new URL(r.request().url()), name = u.pathname.split('/').pop(), id = u.searchParams.get('id');
    if (name === 'getBookmarks') {
      const out = [...store].map(([sid, pos], i) => ({ position: pos, changed: `2026-11-1${i}T00:00:00Z`, created: '2026-11-01T00:00:00Z', entry: song(sid, 'Episode ' + sid) }));
      return r.fulfill({ json: sub({ bookmarks: out.length ? { bookmark: out } : {} }) });
    }
    if (name === 'deleteBookmark') { calls.push('del ' + id); store.delete(id); }
    if (name === 'createBookmark') { calls.push('add ' + id + ' ' + u.searchParams.get('position')); store.set(id, +u.searchParams.get('position')); }
    if (name === 'getMusicFolders') return r.fulfill({ json: sub({ musicFolders: { musicFolder: [{ id: 1, name: 'Music' }] } }) });
    return r.fulfill({ json: sub({}) });
  });
  await p.route('**/api/{recommend,mixes,radio,stats,podcast,abs}/**', r => r.fulfill({ json: {} }));
  await p.goto(base); await p.waitForTimeout(3500);
  const cards = async () => p.$$eval('.cont-card .nm', els => els.map(e => e.textContent));
  const before = await cards();
  ok(before.length === 2, `two cards under Continue listening (newest is the big card): ${before}`);
  const x = await p.$$('.cont-x');
  ok(x.length === 2, 'each card has an X');
  const box = await x[0].boundingBox(), card = await (await p.$('.cont-card')).boundingBox();
  ok(box && card && box.x + box.width > card.x + card.width - 12 && box.y < card.y + 12, 'X sits in the top right corner');
  await p.screenshot({ path: '/t/contx.png' });
  const removed = before[0].replace('Episode ', '');
  await x[0].click(); await p.waitForTimeout(1500);
  ok(calls.includes('del ' + removed), `deleted the saved position of ${removed}`);
  const after = await cards();
  ok(!after.includes(before[0]) && after.length === 1, `card gone: ${after}`);
  ok(await p.isVisible('text=Removed from Continue listening'), 'undo toast shown');
  await p.click('#toasts button'); await p.waitForTimeout(1500);
  ok(calls.some(c => c.startsWith('add ' + removed)), 'Undo saved the position again');
  // Navidrome dates a restored position "now", so it comes back as the newest: the big card or a row card.
  const home = await p.$$eval('.cont-card .nm, .hero-resume b', els => els.map(e => e.textContent));
  ok(home.includes(before[0]), `back on Home after Undo: ${home}`);
  await b.close();
})();
