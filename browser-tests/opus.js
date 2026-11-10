// An .opus library file is served with Range support and the browser can decode, play and seek it
const { chromium } = require('playwright');
const BASE = process.argv[2], FILE = process.argv[3];
(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  const p = await b.newPage(); await p.goto(BASE + 'static/icon.svg');
  const r = await p.evaluate(async src => {
    const a = new Audio(src); a.muted = true;
    await new Promise((ok, no) => { a.onloadedmetadata = ok; a.onerror = () => no(new Error('cannot load: ' + (a.error && a.error.message))); });
    a.currentTime = 100; await a.play(); await new Promise(ok => setTimeout(ok, 1500));
    return { duration: Math.round(a.duration), at: a.currentTime, canPlay: a.canPlayType('audio/ogg; codecs=opus') };
  }, BASE + 'api/library/file?path=' + encodeURIComponent(FILE));
  console.log(JSON.stringify(r)); const ok = r.duration > 200 && r.at > 100.5;
  console.log(ok ? 'ALL PASSED' : 'FAILED'); await b.close(); process.exit(ok ? 0 : 1);
})();
