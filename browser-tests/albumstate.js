// Search: albums show one of three symbols (missing = +, partly there, complete = check); songs keep + or check.
const { chromium } = require('playwright');
(async () => {
  const base = process.argv[2];
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 400, height: 900 } });
  const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) process.exitCode = 1; };
  const sub = extra => ({ 'subsonic-response': { status: 'ok', version: '1.16.1', ...extra } });
  await p.route('**/api/nd/rest/**', r => {
    const name = new URL(r.request().url()).pathname.split('/').pop();
    if (name === 'getMusicFolders') return r.fulfill({ json: sub({ musicFolders: { musicFolder: [{ id: 1, name: 'Music' }] } }) });
    return r.fulfill({ json: sub({}) });
  });
  await p.route('**/api/{recommend,mixes,radio,stats,podcast,abs,loudness}**', r => r.fulfill({ json: {} }));
  await p.route('**/api/suggest**', r => r.fulfill({ json: [] }));
  await p.route('**/api/library/find**', r => r.fulfill({ json: { songs: [], albums: [], artists: [] } }));
  const album = (title, state, n, total) => ({ title, artist: 'Band', year: '2001', type: 'Album', artwork: null, ytm_browse_id: 'B' + title, track_count: total || null,
    library_count: n, in_library: n > 0, library_state: state });
  await p.route('**/api/search**', r => r.fulfill({ json: {
    songs: [{ title: 'Owned Song', artist: 'Band', album: 'X', in_library: true }, { title: 'New Song', artist: 'Band', album: 'X', in_library: false }],
    albums: [album('Not Here', 'none', 0, 10), album('Just One', 'partial', 1, 20), album('Everything', 'full', 12, 12), album('Unknown Total', 'partial', 4, null)] } }));
  await p.goto(base); await p.waitForTimeout(2000);
  await p.click('.tabbtn[data-tab="search"]');
  await p.fill('#searchInput', 'band'); await p.waitForTimeout(2500);
  const row = title => p.locator('.row', { has: p.locator(`.t:text-is("${title}")`) }).first();
  const kind = async title => {
    const r = row(title);
    if (await r.locator('.added.partial').count()) return 'partial';
    if (await r.locator('.added').count()) return 'full';
    if (await r.locator('button.icon-btn').count()) return 'plus';
    return '?';
  };
  ok(await kind('Not Here') === 'plus', 'album not in the library: plus button');
  ok(await kind('Just One') === 'partial', 'album with 1 of 20: partial symbol, not a check');
  ok(await kind('Everything') === 'full', 'complete album: check');
  ok(await kind('Unknown Total') === 'partial', 'partly owned album with an unknown total: partial');
  ok(await kind('Owned Song') === 'full' && await kind('New Song') === 'plus', 'songs: check or plus as before');
  const text = async title => (await row(title).locator('.s').innerText());
  ok((await text('Just One')).includes('1 of 20 in library'), `shows how much: "${await text('Just One')}"`);
  ok((await text('Unknown Total')).includes('4 songs in library'), `unknown total: "${await text('Unknown Total')}"`);
  ok(!(await text('Everything')).includes('in library') && !(await text('Not Here')).includes('in library'), 'no count text for none/complete');
  ok((await row('Just One').locator('.added.partial').getAttribute('title')).includes('Part of this album'), 'tooltip explains the symbol');
  const color = async title => row(title).locator('.added').evaluate(e => getComputedStyle(e).color);
  ok((await color('Just One')) !== (await color('Everything')), 'partial and complete look different');
  await p.screenshot({ path: '/t/albumstate.png' });
  await row('Just One').click(); await p.waitForTimeout(600);
  ok(await p.isVisible('text=Add the 19 missing songs'), 'partial album sheet offers the missing songs');
  await b.close();
})();
