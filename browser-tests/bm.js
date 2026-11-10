// Fake bookmark store for tests: the real Navidrome account (the user's!) is never written to or wiped.
module.exports = async function mockBookmarks(page) {
  const store = new Map();
  const ok = { 'subsonic-response': { status: 'ok', version: '1.16.1' } };
  await page.route('**/rest/createBookmark*', r => { const u = new URL(r.request().url()); store.set(u.searchParams.get('id'), { position: +u.searchParams.get('position'), changed: new Date().toISOString() }); r.fulfill({ json: ok }); });
  await page.route('**/rest/deleteBookmark*', r => { store.delete(new URL(r.request().url()).searchParams.get('id')); r.fulfill({ json: ok }); });
  await page.route('**/rest/getBookmarks*', async r => {
    const origin = new URL(r.request().url()).origin, out = [];
    for (const [id, b] of store) { const s = await (await page.request.get(origin + '/api/nd/rest/getSong?id=' + id)).json(); out.push({ position: b.position, changed: b.changed, created: b.changed, entry: s['subsonic-response'].song }); }
    r.fulfill({ json: { 'subsonic-response': { status: 'ok', version: '1.16.1', bookmarks: out.length ? { bookmark: out } : {} } } });
  });
  page.__bookmarks = store;
};
