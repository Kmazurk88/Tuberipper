// The page side of the new (native) player, against a fake app: the fake follows the protocol of Engine.java.
const { chromium } = require('playwright');
(async () => {
  const base = process.argv[2];
  const b = await chromium.launch();
  const ok = (c, m) => { console.log((c ? 'PASS ' : 'FAIL ') + m); if (!c) process.exitCode = 1; };
  const sub = extra => ({ 'subsonic-response': { status: 'ok', version: '1.16.1', ...extra } });

  // A fake of Engine.java in the page: songs are 20 s long and a "second" passes every 100 ms x 5 (so tests are quick).
  const fake = () => {
    const E = { list: [], idx: -1, wants: false, pos: 0, rate: 1, cmds: [], pauseAtEnd: false };
    const saved = JSON.parse(localStorage.getItem('__fake') || 'null');
    if (saved) Object.assign(E, saved);
    const cur = () => E.list[E.idx];
    const snap = () => ({ hasPlayer: true, id: cur() ? cur().id : '', playing: E.wants && !!cur(), wantsPlay: E.wants, state: 'ready', pos: Math.round(E.pos * 1000), dur: 20000, rate: E.rate });
    const send = m => window.TRPlayer_event && window.TRPlayer_event(m);
    const state = () => send({ e: 'state', ...snap() });
    setInterval(() => {   // position only moves; songs change when a test says so (advance / finish)
      if (!E.wants || !cur()) return;
      E.pos = Math.min(19, E.pos + 0.5 * E.rate);
      send({ e: 'time', id: cur().id, pos: Math.round(E.pos * 1000), dur: 20000 });
      localStorage.setItem('__fake', JSON.stringify({ list: E.list, idx: E.idx, wants: E.wants, pos: E.pos, rate: E.rate }));
    }, 100);
    const advance = () => { E.idx++; E.pos = 0; send({ e: 'transition', id: cur().id, reason: 'auto' }); state(); };
    const finish = () => { E.pos = 20; E.wants = false; send({ e: 'ended', id: cur().id }); };
    window.__native = { E, emit: send, cmds: E.cmds, advance, finish };
    window.TRNative = {
      playerVersion: () => 2, offlineSupported: () => false, onHomeWifi: () => false, clear() {}, update() {}, haptic() {}, freeBytes: () => 1e10, changeServer() {}, dlList: () => '[]', dlUsage: () => 0, dlCellular: () => false,
      playerState: () => JSON.stringify(snap()),
      player(json) {
        const c = JSON.parse(json); E.cmds.push(c);
        if (c.c === 'load') { E.list = [c.item, ...c.upcoming]; E.idx = 0; E.pos = c.pos / 1000; E.wants = c.play; E.rate = c.item.speed || 1; state(); }
        else if (c.c === 'upcoming') E.list = E.list.slice(0, E.idx + 1).concat(c.items);
        else if (c.c === 'play') { E.wants = true; state(); }
        else if (c.c === 'pause') { E.wants = false; state(); }
        else if (c.c === 'seek') { E.pos = c.pos / 1000; }
        else if (c.c === 'speed') E.rate = c.v;
        else if (c.c === 'sleep') E.pauseAtEnd = c.mode === 'end';
        else if (c.c === 'stop') { E.list = []; E.idx = -1; E.wants = false; }
      },
    };
  };

  const song = (n, extra = {}) => ({ id: 's' + n, title: 'Song ' + n, artist: 'Artist', album: 'Album', albumId: 'al1', artistId: 'ar1', duration: 20, suffix: 'mp3', track: n, ...extra });
  const open = async (native, init) => {
    const p = await b.newPage({ viewport: { width: 400, height: 860 } });
    await p.addInitScript(() => { localStorage.setItem('tr.debug', '1'); });
    if (native) await p.addInitScript(() => localStorage.setItem('tr.native', 'true'));
    await p.addInitScript(fake);
    if (init) await p.addInitScript(init);
    await p.route('**/api/nd/rest/**', r => {
      const name = new URL(r.request().url()).pathname.split('/').pop();
      if (name === 'getMusicFolders') return r.fulfill({ json: sub({ musicFolders: { musicFolder: [{ id: 1, name: 'Music' }] } }) });
      return r.fulfill({ json: sub({}) });
    });
    await p.route('**/api/{recommend,mixes,radio,stats,podcast,abs,loudness}**', r => r.fulfill({ json: { levels: {} } }));
    await p.goto(base); await p.waitForTimeout(2500);
    return p;
  };
  const cmds = p => p.evaluate(() => window.__native.cmds.slice());
  const last = (arr, c) => arr.filter(x => x.c === c).pop();

  // 1) the old player is untouched when the setting is off
  let p = await open(false);
  await p.evaluate(songs => playQueue(songs, 0, null), [song(1), song(2)]);
  await p.waitForTimeout(500);
  ok((await cmds(p)).filter(c => c.c === 'load').length === 0, 'setting off: the browser player is used, nothing sent to the app');
  await p.close();

  // 2) setting on: load with the upcoming songs
  p = await open(true);
  await p.evaluate(songs => playQueue(songs, 0, null), [song(1), song(2), song(3)]);
  await p.waitForTimeout(600);
  let c = await cmds(p), load = last(c, 'load');
  ok(load && load.item.id === 's1' && load.play === true && load.pos === 0, 'playQueue sends load for the first song');
  ok(load && load.upcoming.map(x => x.id).join() === 's2,s3', `the next songs go with it: ${load && load.upcoming.map(x => x.id)}`);
  ok(load && /^https?:\/\/.*\/api\/nd\/stream\/s1/.test(load.item.url), `the address is absolute: ${load && load.item.url}`);
  ok(await p.evaluate(() => !getAudio().paused && getAudio().readyState === 4), 'the page sees it as playing');

  // 3) the app moves on by itself: the page follows
  await p.evaluate(() => window.__native.advance());
  await p.waitForTimeout(200);
  ok(await p.evaluate(() => cur().id === 's2' && P.index === 1), 'automatic advance: the page is on song 2');
  c = await cmds(p);
  ok(!last(c, 'load') || c.filter(x => x.c === 'load').length === 1, 'no second load: the audio was not touched');
  await p.waitForTimeout(300);
  c = await cmds(p);
  ok(last(c, 'upcoming') && last(c, 'upcoming').items.map(x => x.id).join() === 's3', `the app's list is topped up: ${JSON.stringify(last(c, 'upcoming') && last(c, 'upcoming').items.map(x => x.id))}`);

  // 4) queue edits reach the app
  await p.evaluate(() => enqueue({ id: 's4', title: 'Song 4', artist: 'Artist', album: 'Album', albumId: 'al1', duration: 20, suffix: 'mp3' }));
  await p.waitForTimeout(400);
  c = await cmds(p);
  ok(last(c, 'upcoming').items.map(x => x.id).join() === 's3,s4', 'a song added to the queue is sent');
  await p.evaluate(() => enqueue({ id: 'ep1', title: 'Episode', artist: 'Show', album: 'Show', albumId: 'pod', duration: 3000, suffix: 'mp3' }));
  await p.waitForTimeout(400);
  c = await cmds(p);
  ok(last(c, 'upcoming').items.map(x => x.id).join() === 's3,s4', 'a podcast episode is not given to the app (the page handles it)');

  // 5) controls
  await p.evaluate(() => getAudio().pause());
  await p.waitForTimeout(300);
  ok(last(await cmds(p), 'pause') && await p.evaluate(() => getAudio().paused), 'pause from the page');
  await p.evaluate(() => window.__native.emit({ e: 'state', id: cur().id, playing: true, wantsPlay: true, state: 'ready', pos: 3000, dur: 20000, rate: 1 }));
  ok(await p.evaluate(() => !getAudio().paused && Math.abs(getAudio().currentTime - 3) < 1), 'play from the notification or headset is seen');
  await p.evaluate(() => window.__native.emit({ e: 'state', id: cur().id, playing: false, wantsPlay: false, state: 'ready', pos: 4000, dur: 20000, rate: 1 }));
  ok(await p.evaluate(() => getAudio().paused), 'pause from the notification or headset is seen');
  await p.evaluate(() => { getAudio().currentTime = 12; });
  ok((last(await cmds(p), 'seek') || {}).pos === 12000, 'seeking sends the position in ms');
  await p.evaluate(() => { getAudio().volume = 0.5; });
  ok((last(await cmds(p), 'gain') || {}).v === 0.5, 'volume levelling is sent as gain');
  const before = (await cmds(p)).filter(x => x.c === 'load').length;
  await p.evaluate(() => window.__native.emit({ e: 'cmd', cmd: 'next' }));
  await p.waitForTimeout(500);
  c = await cmds(p);
  ok(c.filter(x => x.c === 'load').length === before + 1 && last(c, 'load').item.id === 's3', 'Next from the notification (nothing queued in the app) is done by the page');
  await p.evaluate(() => window.__native.emit({ e: 'transition', id: 's4', reason: 'user' }));
  ok(await p.evaluate(() => cur().id === 's4'), 'Next done by the app itself is followed');

  // 6) sleep timer belongs to the app
  await p.evaluate(() => setSleep(15));
  ok(JSON.stringify(last(await cmds(p), 'sleep')) === JSON.stringify({ c: 'sleep', mode: 'timer', ms: 900000 }), 'sleep timer: the app gets the time');
  await p.evaluate(() => window.__native.emit({ e: 'sleepDone' }));
  ok(await p.isVisible('text=Sleep timer: stopped playback'), 'sleep timer done: the page says so');
  await p.evaluate(() => setSleep('end'));
  ok((last(await cmds(p), 'sleep') || {}).mode === 'end', 'sleep at the end of the song is set in the app');
  const idxBefore = await p.evaluate(() => P.index);
  await p.evaluate(() => window.__native.emit({ e: 'ended', id: cur().id, sleep: true }));
  await p.waitForTimeout(300);
  ok(await p.evaluate(i => P.index === i, idxBefore), 'the song ended for the sleep timer: the page does not go on to another');

  // 7) equalizer
  await p.evaluate(() => { lsSet('tr.eq', { preset: 'Custom', gains: [3, 0, 0, 0, 2] }); applyEq(); });
  ok(JSON.stringify(last(await cmds(p), 'eq')) === JSON.stringify({ c: 'eq', on: true, gains: [3, 0, 0, 0, 2] }), 'equalizer gains go to the app');
  await p.evaluate(() => { lsSet('tr.eq', { preset: 'Flat', gains: [0, 0, 0, 0, 0] }); applyEq(); });
  ok((last(await cmds(p), 'eq') || {}).on === false, 'a flat equalizer is switched off');

  // 8) switching back stops the app's player
  await p.evaluate(() => toggleNativePlayer());
  ok(!!last(await cmds(p), 'stop'), 'turning the new player off stops the app');
  await p.close();

  // 9) a reloaded page picks up what the app is playing (no restart of the song)
  p = await open(true);
  await p.evaluate(songs => playQueue(songs, 0, null), [song(1), song(2), song(3)]);
  await p.evaluate(() => window.__native.advance());
  await p.waitForTimeout(1200);   // into song 2, a few seconds in
  const idBefore = await p.evaluate(() => cur().id);
  await p.waitForTimeout(150);
  await p.reload(); await p.waitForTimeout(2500);
  c = await cmds(p);
  ok(c.filter(x => x.c === 'load').length === 0, `after a reload no load is sent (cmds: ${c.map(x => x.c).join()})`);
  ok(await p.evaluate(id => cur() && P.loaded && !getAudio().paused && getAudio().currentTime > 0, idBefore), 'the page carries on from the app\'s state');
  await p.evaluate(() => getAudio().pause()); await p.waitForTimeout(200);
  ok(!!last(await cmds(p), 'pause'), 'and controls it');
  await p.close();
  await b.close();
})();
