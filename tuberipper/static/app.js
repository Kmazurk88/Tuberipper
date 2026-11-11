'use strict';
(() => {

// =====================================================================
// Basics
// =====================================================================
const pathPrefix = location.pathname.replace(/\/$/, '').startsWith('/tuberipper') ? '/tuberipper' : '';
const api = p => pathPrefix + p;

// On the public address a session can expire: any "please sign in" answer sends you to the login page.
const nativeFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const r = await nativeFetch(...args);
  if (r.status === 401 && String(args[0]).includes('/api/') && !String(args[0]).includes('/api/auth/')) {
    location.href = api('/login') + '?next=' + encodeURIComponent(location.pathname);
  }
  noteShell(r.headers.get('x-tr-shell'));
  return r;
};

// The server names its current app version on every answer (X-TR-Shell). When it differs from the one this page
// was loaded with, a newer app was deployed: reload by itself the next time the app is in the background with
// nothing playing, so nobody has to press "Reload app" and no song is cut off. Once per version (no loops).
const SHELL = (() => { try { return new URL(document.currentScript.src).searchParams.get('v') || ''; } catch (e) { return ''; } })();
let newShell = '';
function noteShell(v) {
  if (!v || !SHELL || v === SHELL || v === newShell) return;
  try { if (sessionStorage.getItem('tr.reloadedFor') === v) return; } catch (e) { /* ignore */ }
  newShell = v;
}
document.addEventListener('visibilitychange', () => {
  if (!newShell || document.visibilityState !== 'hidden') return;
  setTimeout(() => {
    if (!newShell || document.visibilityState !== 'hidden' || !audio.paused) return;
    try { sessionStorage.setItem('tr.reloadedFor', newShell); } catch (e) { /* ignore */ }
    persistSession(true);
    location.reload();
  }, 1500);
});
const $ = (s, el = document) => el.querySelector(s);
const arr = x => (x == null ? [] : Array.isArray(x) ? x : [x]);
const LONG_SECONDS = 600; // anything longer than 10 min (or any podcast episode) remembers where you stopped
const BOOKMARK_EVERY_MS = 10000;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(2)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// replaceChildren/append stringify arrays and null; this drops empties and flattens.
const kidsOf = kids => kids.flat(3).filter(k => k != null && k !== false);
const put = (el, ...kids) => el.replaceChildren(...kidsOf(kids));
const sv = (inner, cls = '') => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${cls}">${inner}</svg>`;
const fsv = inner => `<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">${inner}</svg>`;
const I = {
  home: sv('<path d="M3 11l9-7.5L21 11v9a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>', 'fillable'),
  search: sv('<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/>'),
  library: sv('<path d="M4 4v16M9 4v16M14.5 5.5l4.5 14"/>', 'fillable'),
  plus: sv('<path d="M12 5v14M5 12h14"/>'),
  x: sv('<path d="M6 6l12 12M18 6L6 18"/>'),
  back: sv('<path d="M15 5l-7 7 7 7"/>'),
  down: sv('<path d="M5 9l7 7 7-7"/>'),
  more: fsv('<circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/>'),
  play: fsv('<path d="M7 4.5v15l13-7.5z"/>'),
  pause: fsv('<path d="M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z"/>'),
  next: fsv('<path d="M5 5v14l10-7zM17 5h2.5v14H17z"/>'),
  prev: fsv('<path d="M19 5v14L9 12zM4.5 5H7v14H4.5z"/>'),
  heart: sv('<path d="M12 20.5s-8-5.2-8-10.8A4.6 4.6 0 0 1 12 7a4.6 4.6 0 0 1 8 2.7c0 5.6-8 10.8-8 10.8z"/>'),
  heartOn: `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 20.5s-8-5.2-8-10.8A4.6 4.6 0 0 1 12 7a4.6 4.6 0 0 1 8 2.7c0 5.6-8 10.8-8 10.8z"/></svg>`,
  note: sv('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),
  mic: sv('<circle cx="12" cy="11" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 6.4M7.8 7.8a6 6 0 0 0 0 6.4M12 13v8"/>'),
  user: sv('<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4.5 4-6 8-6s7 1.5 8 6"/>'),
  disc: sv('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>'),
  list: sv('<path d="M4 6h16M4 12h16M4 18h16"/>'),
  grid: sv('<rect x="4" y="4" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1"/>'),
  sort: sv('<path d="M8 4v16M4 8l4-4 4 4M16 20V4M12 16l4 4 4-4"/>'),
  shuffle: sv('<path d="M3 17h2.5c1.4 0 2.7-.7 3.5-1.8l5-6.4c.8-1.1 2.1-1.8 3.5-1.8H21M18 5l3 3-3 3M3 7h2.5c1.4 0 2.7.7 3.5 1.8M21 17h-3c-1.4 0-2.7-.7-3.5-1.8M18 14l3 3-3 3"/>'),
  queue: sv('<path d="M4 6h16M4 12h16M4 18h8M17 15v6M14 18h6"/>'),
  pin: sv('<path d="M12 16v6M8 3h8l-1 6 3 4H6l3-4z"/>'),
  trash: sv('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  edit: sv('<path d="M4 20h4L19 9l-4-4L4 16zM13 7l4 4"/>'),
  check: sv('<path d="M5 12.5l4.5 4.5L19 7"/>'),
  upload: sv('<path d="M12 16V4M7 9l5-5 5 5M5 20h14"/>'),
  addsong: sv('<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21M10.5 8v5M8 10.5h5"/>'),
  playlist: sv('<path d="M3 6h12M3 11h12M3 16h7M17 12v7M13.5 15.5h7"/>'),
  rew: sv('<path d="M4 4v5h5"/><path d="M4.5 9A8.5 8.5 0 1 1 5 15"/><text x="12" y="15.5" text-anchor="middle" font-size="7.5" font-weight="700" fill="currentColor" stroke="none">15</text>'),
  fwd: sv('<path d="M20 4v5h-5"/><path d="M19.5 9A8.5 8.5 0 1 0 19 15"/><text x="12" y="15.5" text-anchor="middle" font-size="7.5" font-weight="700" fill="currentColor" stroke="none">30</text>'),
  tabCreate: sv('<path d="M12 4v16M4 12h16"/>'),
  moreV: fsv('<circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/>'),
  dl: sv('<circle cx="12" cy="12" r="10"/><path d="M12 7v8M8.5 11.5L12 15l3.5-3.5"/>'),
  dlDone: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="11" fill="#1ed760"/><path d="M12 7v8M8.5 11.5L12 15l3.5-3.5" fill="none" stroke="#000" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cloudOff: sv('<path d="M7 18h10a4 4 0 0 0 .7-7.9A6 6 0 0 0 6.3 9.3 4.4 4.4 0 0 0 7 18zM4 4l16 16"/>'),
  grip: sv('<path d="M5 9h14M5 15h14"/>'),
  moon: sv('<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>'),
  lyrics: sv('<path d="M5 6h14M5 11h14M5 16h9"/>'),
  speed: sv('<circle cx="12" cy="13" r="8"/><path d="M12 13l4-4M9 3h6"/>'),
  sliders: sv('<path d="M4 8h9M17 8h3M4 16h3M11 16h9"/><circle cx="15" cy="8" r="2"/><circle cx="9" cy="16" r="2"/>'),
};
const icon = (name, cls = '') => h('span', { class: cls, html: I[name] });

function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  sec = Math.floor(sec);
  const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60), ss = sec % 60;
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${mm}:${String(ss).padStart(2, '0')}`;
}
function fmtLong(sec) {
  sec = Math.round(sec);
  const hh = Math.floor(sec / 3600), mm = Math.round((sec % 3600) / 60);
  if (!hh) return `${Math.max(1, mm)} min`;
  return mm ? `${hh} hr ${mm} min` : `${hh} hr`;
}
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const shuffled = list => { const a = list.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const absUrl = u => new URL(u, location.href).href;

// =====================================================================
// Navidrome API (through our proxy)
// =====================================================================
// Navidrome shows a podcast episode as "Title (long description…)" when the file has a subtitle; show just the title.
function tidyEpisodeTitles(o) {
  if (!o || typeof o !== 'object') return;
  if (Array.isArray(o)) { o.forEach(tidyEpisodeTitles); return; }
  if (typeof o.title === 'string' && o.albumId && S.podAlbums.has(o.albumId) && o.title.endsWith(')')) {
    for (let i = o.title.indexOf(' ('); i > 0; i = o.title.indexOf(' (', i + 1)) {
      if (o.title.length - i >= 40) { o.title = o.title.slice(0, i); break; }
    }
  }
  for (const k in o) if (o[k] && typeof o[k] === 'object') tidyEpisodeTitles(o[k]);
}
async function nd(ep, params = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue;
    arr(v).forEach(x => q.append(k, x));
  }
  let r;
  try { r = await fetch(api(`/api/nd/rest/${ep}?${q}`)); } catch (e) { connLost(); throw new Error('Can’t reach the server.'); }
  connOk();
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  const body = data && data['subsonic-response'];
  if (!r.ok) throw new Error((body && body.error && body.error.message) || (data && data.detail) || `Server error ${r.status}`);
  if (!body || body.status !== 'ok') throw new Error((body && body.error && body.error.message) || 'Navidrome returned an error.');
  tidyEpisodeTitles(body);
  return body;
}
// A thin banner when Titan can't be reached, with Retry; goes away by itself when it answers again.
let connBanner = null, connFails = 0;
function connLost() {
  if (++connFails < 2 || connBanner) return;
  connBanner = h('div', { class: 'conn-banner' }, h('span', {}, navigator.onLine === false ? 'You’re offline' : 'Can’t reach Titan'),
    h('button', { onclick: async () => { try { await nd('ping'); toast('Back online', 'ok', 2000); if (curTab === 'home') renderHome(); } catch (e) { /* still down */ } } }, 'Retry'));
  document.body.append(connBanner);
}
function connOk() { connFails = 0; if (connBanner) { connBanner.remove(); connBanner = null; } }
window.addEventListener('offline', () => { connFails = 1; connLost(); });
window.addEventListener('online', () => { if (connBanner) nd('ping').catch(() => {}); });
const coverUrl = (id, size = 300) => (/^https?:/.test(id) ? id : api(`/api/nd/cover/${encodeURIComponent(id)}?size=${size}`));
const streamUrl = id => api(`/api/nd/stream/${encodeURIComponent(id)}`);
// A recommended song that isn't in the library yet streams from YouTube Music through Titan (/api/preview); everything else from Navidrome.
const srcFor = s => (s.online ? api(`/api/preview/${encodeURIComponent(s.videoId)}`) : (((FX.on && !S.offline) ? '' : offlineUrl(s.id)) || streamUrl(s.id)));

const memo = new Map();
function cached(key, ttl, fn) {
  const e = memo.get(key);
  if (e && Date.now() - e.t < ttl) return e.p;
  const p = fn();
  memo.set(key, { t: Date.now(), p });
  p.catch(() => memo.delete(key));
  return p;
}
const bust = prefix => { [...memo.keys()].filter(k => k.startsWith(prefix)).forEach(k => memo.delete(k)); if (prefix === 'pl') scheduleMembership(); };

const getPlaylists = () => cached('pl', 60000, async () => arr((await nd('getPlaylists')).playlists?.playlist));
const getPlaylist = async id => (await nd('getPlaylist', { id })).playlist;
// Which songs are in at least one playlist: drives the green dot on song rows (Liked Songs counts too, see hasDot).
let memberT = 0;
function scheduleMembership() { clearTimeout(memberT); memberT = setTimeout(refreshMembership, 1500); }
async function refreshMembership() {
  try {
    const lists = await getPlaylists();
    const got = await Promise.all(lists.map(p => getPlaylist(p.id).then(pl => arr(pl.entry).map(e => e.id))));
    S.inPl = new Set(got.flat());
    syncDots();
  } catch (e) { /* offline or a playlist failed to load: keep the last known dots */ }
}
const hasDot = song => !!song && !song.online && !isPodcastSong(song) && (S.inPl.has(song.id) || S.liked.has(song.id));
function syncDots() { document.querySelectorAll('.pl-dot[data-sid]').forEach(b => { b.hidden = !(S.inPl.has(b.dataset.sid) || S.liked.has(b.dataset.sid)); }); }
const getAlbum = id => cached('al:' + id, 120000, async () => (await nd('getAlbum', { id })).album);
const getArtist = id => cached('ar:' + id, 120000, async () => (await nd('getArtist', { id })).artist);
const albumList = async (type, size, extra = {}) => arr((await nd('getAlbumList2', { type, size, ...extra })).albumList2?.album);
const getStarred = async () => arr((await nd('getStarred2')).starred2?.song);
async function allAlbums(folderId) {
  return cached('allal:' + folderId, 300000, async () => {
    let out = [], offset = 0;
    for (let i = 0; i < 8; i++) {
      const page = await albumList('alphabeticalByName', 500, { musicFolderId: folderId, offset });
      out = out.concat(page);
      if (page.length < 500) break;
      offset += 500;
    }
    return out;
  });
}
async function allArtists(folderId) {
  return cached('allar:' + folderId, 300000, async () => {
    const idx = arr((await nd('getArtists', { musicFolderId: folderId })).artists?.index);
    return idx.flatMap(i => arr(i.artist));
  });
}

// Online info (YouTube Music / Wikipedia, via /api/discover/*)
const discover = (what, params) => cached(`disc:${what}:${JSON.stringify(params)}`, 600000, async () => {
  const r = await fetch(api(`/api/discover/${what}?${new URLSearchParams(params)}`));
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
  return r.json();
});
// Same idea as the server's title normaliser: "Piano Man - Remastered 2011" == "Piano Man".
const normT = s => (s || '').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/[\(\[].*?[\)\]]/g, ' ').replace(/\s+-\s+(?:remaster(?:ed)?|\d{4}\b|deluxe|live|single version|.*\bversion\b|.*\bedit\b).*$/, '')
  .replace(/&/g, ' and ').replace(/[^\p{L}\p{N} ]+/gu, ' ').replace(/\s+/g, ' ').trim();
const mainArtist = s => (s || '').split(/\s*(?:,|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b)\s*/i)[0].trim();

// =====================================================================
// App state
// =====================================================================
const S = {
  musicId: null, podId: null, podAlbums: new Set(),
  podGroups: new Map(),   // canonical album id -> {id, ids:[every Navidrome album of this show], count}
  podCanon: new Map(),    // any podcast album id -> canonical id
  liked: new Set(), inPl: new Set(), bookmarks: new Map(),
  pins: [], recents: [],
  chip: 'all',
  homeDirty: true,
  ready: false,
  finished: new Set(),   // podcast episodes marked/played to the end
  finishedAt: {},        // when each was marked (so Audiobookshelf's progress and ours can be compared)
  abs: false,            // Audiobookshelf is connected (episode deleting + progress sync)
  deleted: new Set(),    // songs/episodes deleted in this session (hidden until the servers catch up)
  dl: new Map(),          // downloads on this phone (Android app only): id -> {status, pct, song...}
  lufs: new Map(),        // song id -> loudness in LUFS (volume levelling)
  epDates: new Map(),     // podcast episode id -> {date, ts}
  offline: false,
  me: { auth: false, user: null },
};
// Navidrome treats an episode's tagged year as part of the album's identity, so one show can appear as several
// "albums" (e.g. 2025 and 2026 episodes). The app shows one podcast per (name, publisher).
function buildPodGroups(albums) {
  const by = new Map();
  albums.forEach(a => { const k = `${normT(a.name)}|${normT(a.artist || '')}`; if (!by.has(k)) by.set(k, []); by.get(k).push(a); });
  S.podGroups = new Map(); S.podCanon = new Map();
  for (const list of by.values()) {
    list.sort((a, b) => (b.songCount || 0) - (a.songCount || 0));
    const main = list[0];
    S.podGroups.set(main.id, { id: main.id, ids: list.map(a => a.id), count: list.reduce((n, a) => n + (a.songCount || 0), 0) });
    list.forEach(a => S.podCanon.set(a.id, main.id));
  }
}
const podCanonical = albums => albums.filter(a => !S.podCanon.has(a.id) || S.podCanon.get(a.id) === a.id);
const podGroupOf = albumId => S.podGroups.get(S.podCanon.get(albumId) || albumId) || { id: albumId, ids: [albumId], count: 0 };
// All episodes of a show (every Navidrome album of it), oldest first, with release dates loaded.
async function podcastShow(albumId, known) {
  const g = podGroupOf(albumId);
  const albums = await Promise.all(g.ids.map(id => (known && known.id === id ? known : getAlbum(id))));
  await Promise.all(g.ids.map(id => loadEpDates(id).catch(() => null)));
  const main = albums.find(a => a.id === g.id) || albums[0];
  const songs = albums.flatMap(a => arr(a.song)).filter(x => !S.deleted.has(x.id)).sort(byDateAsc);
  return { main, songs, ids: g.ids };
}
// ----- Deleting. Only offered on the home-network and Tailscale addresses; the internet address can't do it. -----
const canDelete = () => !S.me.auth;
function afterDelete(ids) {
  ids.forEach(id => { S.deleted.add(id); document.querySelectorAll(`[data-sid="${id}"]`).forEach(el => el.remove()); if (NATIVE_DL && dlStatus(id) !== 'none') removeDownload({ id }); });
  ['pl', 'allal', 'allar', 'al:', 'ar:', 'disc:'].forEach(bust);
  S.homeDirty = true;
}
async function deleteSongsFlow(songs, what) {
  const n = songs.length;
  if (!await confirmDialog(`Delete ${what}?`, `This permanently deletes ${n === 1 ? 'the file' : plural(n, 'file')} from Titan, so it disappears from every app. It can’t be undone.`, 'Delete', 'Cancel')) return false;
  const t = toast('Deleting…', '', 0);
  try {
    const r = await fetch(api('/api/library/delete'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: songs.map(x => x.id) }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
    afterDelete(d.deleted);
    setToast(t, d.failed.length ? `Deleted ${d.deleted.length}; ${d.failed.length} couldn’t be: ${d.failed[0].reason}` : `Deleted ${what}.`, d.failed.length ? 'bad' : 'ok', 6000);
    return d.deleted.length === n;
  } catch (e) { setToast(t, `Couldn’t delete: ${e.message}`, 'bad', 7000); return false; }
}
async function deleteEpisodeFlow(song) {
  if (!await confirmDialog('Delete this episode?', 'It is removed from Audiobookshelf and the file is deleted from Titan. It can’t be undone.', 'Delete', 'Cancel')) return false;
  const t = toast('Deleting…', '', 0);
  try {
    const r = await fetch(api('/api/podcast/delete'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ song: song.id }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
    afterDelete([song.id]); S.bookmarks.delete(song.id); S.finished.delete(song.id);
    setToast(t, 'Episode deleted.', 'ok', 4000);
    return true;
  } catch (e) { setToast(t, `Couldn’t delete: ${e.message}`, 'bad', 7000); return false; }
}

const isPodcastSong = s => !!s && S.podAlbums.has(s.albumId);
const isLong = s => !!s && (isPodcastSong(s) || (s.duration || 0) > LONG_SECONDS);

let saveTimer = null;
const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k) || 'null') ?? d; } catch (e) { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage full or disabled */ } };
// ----- Theme: dark (default), light, or follow the phone -----
const themeMode = () => lsGet('tr.theme', 'dark');
function applyTheme() {
  const m = themeMode();
  const light = m === 'light' || (m === 'auto' && window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches);
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.content = light ? '#ffffff' : '#121212';
}
applyTheme();
if (window.matchMedia) matchMedia('(prefers-color-scheme: light)').addEventListener('change', applyTheme);
function saveState() {
  const body = { pins: S.pins, recents: S.recents, finished: [...S.finished], finishedAt: S.finishedAt };
  lsSet('tr.state', body); lsSet('tr.stateDirty', 1);   // changes made offline are pushed when the server is reachable again
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    fetch(api('/api/app/state'), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(r => { if (r.ok) lsSet('tr.stateDirty', 0); }).catch(() => {});
  }, 800);
}
const keyOf = it => `${it.type}:${it.id}`;
function touchRecent(item) {
  if (!item || !item.id || item.type === 'liked') return;
  const rec = { type: item.type, id: item.id, name: item.name, sub: item.sub, cover: item.cover, ts: Date.now() };
  S.recents = [rec, ...S.recents.filter(r => keyOf(r) !== keyOf(rec))].slice(0, 40);
  S.homeDirty = true;
  saveState();
}
const isPinned = it => S.pins.some(p => keyOf(p) === keyOf(it));
function togglePin(it) {
  if (isPinned(it)) S.pins = S.pins.filter(p => keyOf(p) !== keyOf(it));
  else S.pins = [{ type: it.type, id: it.id, name: it.name, sub: it.sub, cover: it.cover }, ...S.pins];
  S.homeDirty = true;
  saveState();
  toast(isPinned(it) ? `Pinned “${it.name}”` : `Unpinned “${it.name}”`, 'ok', 2500);
}

const slimSong = e => ({ id: e.id, title: e.title, artist: e.artist, album: e.album, albumId: e.albumId, artistId: e.artistId, coverArt: e.coverArt, duration: e.duration, track: e.track, suffix: e.suffix, comment: e.comment });
function persistBookmarks() { lsSet('tr.bm', [...S.bookmarks].slice(-150).map(([id, b]) => [id, { position: b.position, changed: b.changed, entry: slimSong(b.entry) }])); }
let bookmarksBooted = false;
async function refreshBookmarks() {
  await flushPendingBookmarks();
  const list = arr((await nd('getBookmarks')).bookmarks?.bookmark);
  const local = bookmarksBooted ? null : S.bookmarks;   // what this device remembered from last time
  bookmarksBooted = true;
  S.bookmarks = new Map(list.filter(b => b.entry).map(b => [b.entry.id, { position: b.position, entry: b.entry, changed: b.changed }]));
  // Safety net: a place this device remembers that the server has lost (and that wasn't finished or reset on purpose) is put back.
  if (local) for (const [id, b] of local) {
    if (S.bookmarks.has(id) || S.finished.has(id) || !(b.position > 5000) || !(Date.parse(b.changed) > (S.finishedAt[id] || 0))) continue;
    S.bookmarks.set(id, b);
    nd('createBookmark', { id, position: b.position }).catch(() => queueBookmark(id, b.position));
  }
  persistBookmarks();
  bus.dispatchEvent(new Event('progress'));
}
// Progress saved while the server couldn't be reached (offline listening) is sent later.
async function flushPendingBookmarks() {
  const pend = lsGet('tr.bmpend', {});
  const ids = Object.keys(pend);
  if (!ids.length) return;
  lsSet('tr.bmpend', {});
  for (const id of ids) {
    try { await nd(pend[id] === null ? 'deleteBookmark' : 'createBookmark', pend[id] === null ? { id } : { id, position: pend[id] }); }
    catch (e) { const again = lsGet('tr.bmpend', {}); again[id] = pend[id]; lsSet('tr.bmpend', again); }
  }
}
const queueBookmark = (id, ms) => { const p = lsGet('tr.bmpend', {}); p[id] = ms; lsSet('tr.bmpend', p); };

// =====================================================================
// Layers (pages, sheets, dialogs) tied to the Android back button
// =====================================================================
const layers = [];
let pendingBack = 0, afterBack = [];
history.replaceState({ d: 0 }, '');
function pushLayer(close) { layers.push(close); history.pushState({ d: layers.length }, ''); }
function closeTop() {
  const fn = layers.pop();
  if (!fn) return;
  fn();
  pendingBack++;
  history.back();
}
function closeAllLayers() {
  const n = layers.length;
  if (!n) return;
  while (layers.length) layers.pop()();
  pendingBack++;
  history.go(-n);
}
const whenSettled = cb => (pendingBack ? afterBack.push(cb) : cb());
const closeThen = cb => { closeTop(); whenSettled(cb); };
window.addEventListener('popstate', e => {
  if (pendingBack) {
    pendingBack--;
    if (!pendingBack) afterBack.splice(0).forEach(f => f());
    return;
  }
  const d = (e.state && e.state.d) || 0;
  while (layers.length > d) layers.pop()();
});

function openLayer(el, onClose) {
  $('#layers').append(el);
  pushLayer(() => { el.remove(); if (onClose) onClose(); });
  return el;
}

let curTab = 'home';
const roots = {};
function openPage(build) {
  const tabEl = $('#tab-' + curTab);
  const page = h('div', { class: 'page' });
  tabEl.append(page);
  pushLayer(() => page.remove());
  build(page);
  return page;
}
const backBar = (title, ...extra) => h('div', { class: 'backbar' },
  h('button', { class: 'icon-btn', html: I.back, 'aria-label': 'Back', onclick: () => closeTop() }), ...extra);

// =====================================================================
// UI primitives
// =====================================================================
function toast(text, cls = '', ttl = 5000, onclick) {
  const el = h('div', { class: 'toast' + (cls ? ' ' + cls : ''), onclick: () => { el.remove(); if (onclick) onclick(); } }, text);
  $('#toasts').append(el);
  if (ttl) setTimeout(() => el.remove(), ttl);
  return el;
}
function setToast(el, text, cls = '', ttl = 0) {
  el.textContent = text;
  el.className = 'toast' + (cls ? ' ' + cls : '');
  if (!el.isConnected) $('#toasts').append(el);
  if (ttl) setTimeout(() => el.remove(), ttl);
}

function cover(id, cls = 'sm', size = 150, opts = {}) {
  const el = h('div', { class: `cover ${cls}${opts.round ? ' round' : ''}${opts.liked ? ' liked' : ''}` });
  el.innerHTML = opts.liked ? I.heartOn : I[opts.fallback || 'note'];
  if (id && !opts.liked) {
    const img = h('img', { loading: 'lazy', alt: '', src: opts.url || coverUrl(id, size) });
    img.onerror = () => img.remove();
    el.append(img);
  }
  return el;
}

function sheet({ title, sub, coverEl, items }) {
  const back = h('div', { class: 'scrim light', onclick: () => closeTop() });
  const sh = h('div', { class: 'sheet' });
  if (title) sh.append(h('div', { class: 'head' }, coverEl, h('div', { style: 'min-width:0' }, h('div', { class: 't' }, title), sub && h('div', { class: 's' }, sub))));
  items.filter(Boolean).forEach(it => sh.append(it.header ? h('div', { class: 'sheet-h' }, it.header)
    : h('button', { class: 'item' + (it.bad ? ' bad' : ''), onclick: () => closeThen(it.run) }, it.icon && h('span', { html: I[it.icon] }), it.label)));
  return openLayer(h('div', { style: 'position:absolute;inset:0' }, back, sh));
}

function dialog(build) {
  const box = h('div', { class: 'dialog' });
  const wrap = h('div', { style: 'position:absolute;inset:0' }, h('div', { class: 'scrim', onclick: () => closeTop() }), box);
  build(box);
  openLayer(wrap);
  return box;
}
function promptDialog(title, { value = '', placeholder = '', ok = 'Save' } = {}) {
  return new Promise(resolve => {
    let done = false;
    const finish = v => { if (done) return; done = true; closeTop(); whenSettled(() => resolve(v)); };
    dialog(box => {
      const input = h('input', { type: 'text', value, placeholder, maxlength: 120 });
      input.addEventListener('keydown', e => { if (e.key === 'Enter') finish(input.value.trim() || null); });
      box.append(h('h3', {}, title), input,
        h('div', { class: 'btns' }, h('button', { class: 'pillbtn dark', onclick: () => finish(null) }, 'Cancel'),
          h('button', { class: 'pillbtn', onclick: () => finish(input.value.trim() || null) }, ok)));
      setTimeout(() => { input.focus(); input.select(); }, 50);
    });
  });
}
function confirmDialog(title, message, ok = 'OK', cancel = 'Cancel') {
  return new Promise(resolve => {
    dialog(box => {
      const finish = v => { closeTop(); whenSettled(() => resolve(v)); };
      box.append(h('h3', {}, title), h('p', { class: 'note' }, message),
        h('div', { class: 'btns' }, h('button', { class: 'pillbtn dark', onclick: () => finish(false) }, cancel),
          h('button', { class: 'pillbtn', onclick: () => finish(true) }, ok)));
    });
  });
}

function onLongPress(el, fn) {
  let timer = null, fired = false;
  const cancel = () => { clearTimeout(timer); timer = null; };
  el.addEventListener('touchstart', () => { fired = false; timer = setTimeout(() => { fired = true; fn(); }, 520); }, { passive: true });
  el.addEventListener('touchmove', cancel, { passive: true });
  el.addEventListener('touchend', cancel);
  el.addEventListener('touchcancel', cancel);
  el.addEventListener('contextmenu', e => { e.preventDefault(); fn(); });
  el.addEventListener('click', e => { if (fired) { e.stopImmediatePropagation(); e.preventDefault(); fired = false; } }, true);
}

// Loading placeholders (grey shapes that shimmer) instead of a bare spinner
const skelRows = (n = 6) => h('div', { class: 'skel' }, Array.from({ length: n }, () => h('div', { class: 'sk-row' }, h('div', { class: 'sk sk-cover' }), h('div', { class: 'sk-txt' }, h('div', { class: 'sk sk-l1' }), h('div', { class: 'sk sk-l2' })))));
const skelCards = (n = 4) => h('div', { class: 'skel sk-hrow' }, Array.from({ length: n }, () => h('div', { class: 'sk-card' }, h('div', { class: 'sk sk-sq' }), h('div', { class: 'sk sk-l1' }), h('div', { class: 'sk sk-l2' }))));
const spinner = () => skelRows(6);
const skelHome = () => h('div', {}, h('div', { class: 'sk-tiles' }, Array.from({ length: 6 }, () => h('div', { class: 'sk sk-tile' }))), skelCards(4), skelCards(4));
// A short buzz on important taps (native in the Android app, the browser's own elsewhere)
const haptic = (kind = 'tick') => { try { if (window.TRNative && TRNative.haptic) TRNative.haptic(kind); else if (navigator.vibrate) navigator.vibrate(kind === 'heavy' ? 25 : 8); } catch (e) { /* not supported */ } };
// "Done. Undo" toast: the action stays undoable for a few seconds
function toastUndo(text, undo, ttl = 7000) {
  const el = h('div', { class: 'toast ok undo' }, h('span', {}, text), h('button', { class: 'undo-btn', onclick: () => { el.remove(); undo(); } }, 'Undo'));
  $('#toasts').append(el);
  setTimeout(() => el.remove(), ttl);
  return el;
}
const errBox = (msg, retry) => h('div', { class: 'empty err' }, h('div', { class: 'err-ic', html: I.cloudOff }), h('div', { class: 'err-t' }, /reach|network|fetch/i.test(msg) ? 'Can’t reach Titan' : 'Something went wrong'),
  h('div', { class: 'err-s' }, /reach|network|fetch/i.test(msg) ? 'Check your Wi-Fi or Tailscale, then try again.' : msg), retry && h('button', { class: 'pillbtn', onclick: retry }, 'Try again'));

// =====================================================================
// Likes (Navidrome stars)
// =====================================================================
async function toggleLike(song) {
  if (song.online) { addOnline(song); return; }
  const was = S.liked.has(song.id);
  if (was) S.liked.delete(song.id); else S.liked.add(song.id);
  syncHearts();
  if (!was) { haptic(); document.querySelectorAll(`.heart[data-sid="${song.id}"]`).forEach(b => { b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); }); }
  try { await nd(was ? 'unstar' : 'star', { id: song.id }); S.homeDirty = true; }
  catch (e) {
    if (was) S.liked.add(song.id); else S.liked.delete(song.id);
    syncHearts();
    toast(`Couldn’t update Liked Songs: ${e.message}`, 'bad');
  }
}
function heartBtn(song, cls = 'icon-btn heart') {
  const b = h('button', { class: cls, 'data-sid': song.id, 'aria-label': 'Like', onclick: e => { e.stopPropagation(); toggleLike(song); } });
  paintHeart(b);
  return b;
}
function paintHeart(b) {
  const on = S.liked.has(b.dataset.sid);
  b.classList.toggle('on', on);
  b.innerHTML = on ? I.heartOn : I.heart;
}
function syncHearts() { document.querySelectorAll('.heart[data-sid]').forEach(paintHeart); syncDots(); }

// =====================================================================
// Player
// =====================================================================
const bus = new EventTarget();
const AUDS = [new Audio(), new Audio()];
AUDS.forEach(a => { a.preload = 'auto'; });
let audio = AUDS[0];   // the one that is playing; the other one only ever holds the next song for gapless / crossfade
const idleAudio = () => (AUDS[0] === audio ? AUDS[1] : AUDS[0]);
// only the playing element's events count
const onAudio = (type, fn) => AUDS.forEach(a => a.addEventListener(type, e => { if (a === audio) fn(e); }));
const P = { list: [], orig: null, shuffle: false, index: -1, ctx: null, tok: 0, loaded: false, starting: false, lastSave: 0, scrobbled: false, errors: 0, pendingSeek: 0, lastPersist: 0 };
const cur = () => P.list[P.index] || null;

// ----- Podcast playback speed (remembered per show) -----
const SPEEDS = [0.8, 1, 1.2, 1.5, 1.75, 2];
const speedFor = s => (s && isLong(s) ? (lsGet('tr.speed', {})[s.albumId] || 1) : 1);
function applySpeed() {
  const r = speedFor(cur());
  audio.defaultPlaybackRate = r; audio.playbackRate = r;
  try { audio.preservesPitch = true; } catch (e) { /* default */ }
}
function setSpeed(r) { const m = lsGet('tr.speed', {}), s = cur(); if (!s) return; if (r === 1) delete m[s.albumId]; else m[s.albumId] = r; lsSet('tr.speed', m); applySpeed(); bus.dispatchEvent(new Event('speed')); }
function speedSheet() {
  const s = cur(), r = speedFor(s);
  sheet({ title: 'Playback speed', sub: s && s.album ? `Remembered for ${s.album}` : '', coverEl: h('div', { class: 'cover md', html: I.speed }),
    items: SPEEDS.map(v => ({ icon: v === r ? 'check' : null, label: v === 1 ? 'Normal (1×)' : `${v}×`, run: () => setSpeed(v) })) });
}

// ----- Sleep timer: stop after N minutes, or when this song/episode ends -----
const SLEEP = { until: 0, endOfTrack: false, timer: null };
const sleepOn = () => SLEEP.until > Date.now() || SLEEP.endOfTrack;
function setSleep(mode) {   // minutes, 'end', or 0 = off
  clearTimeout(SLEEP.timer);
  SLEEP.until = 0; SLEEP.endOfTrack = false;
  if (mode === 'end') SLEEP.endOfTrack = true;
  else if (mode > 0) { SLEEP.until = Date.now() + mode * 60000; SLEEP.timer = setTimeout(sleepCheck, mode * 60000 + 200); }
  haptic();
  toast(mode === 'end' ? 'Will stop when this ends' : mode > 0 ? `Sleep timer: ${mode} min` : 'Sleep timer off', 'ok', 2200);
  bus.dispatchEvent(new Event('sleep'));
}
function sleepCheck() {
  if (!SLEEP.until || Date.now() < SLEEP.until) return;
  SLEEP.until = 0;
  const v0 = audio.volume, t0 = Date.now();   // fade out over ~5 s, then stop
  const iv = setInterval(() => {
    const k = (Date.now() - t0) / 5000;
    if (k >= 1 || audio.paused) { clearInterval(iv); audio.pause(); applyVolume(); toast('Sleep timer: stopped playback', 'ok', 4000); bus.dispatchEvent(new Event('sleep')); }
    else audio.volume = Math.max(0, v0 * (1 - k));
  }, 200);
}
const sleepLabel = () => SLEEP.endOfTrack ? 'End' : SLEEP.until > Date.now() ? `${Math.max(1, Math.ceil((SLEEP.until - Date.now()) / 60000))}m` : '';
function sleepSheet() {
  const song = cur(), what = song && isPodcastSong(song) ? 'episode' : 'song';
  sheet({ title: 'Sleep timer', sub: sleepOn() ? `Stopping ${SLEEP.endOfTrack ? `after this ${what}` : 'in ' + sleepLabel()}` : 'Stop playing after…', coverEl: h('div', { class: 'cover md', html: I.moon }),
    items: [...[5, 15, 30, 45, 60, 90, 120].map(m => ({ icon: 'moon', label: `${m} minutes`, run: () => setSleep(m) })),
      { icon: 'moon', label: `End of this ${what}`, run: () => setSleep('end') },
      sleepOn() && { icon: 'x', label: 'Turn off timer', bad: true, run: () => setSleep(0) }] });
}

const compact = s => ({ id: s.id, title: s.title, artist: s.artist, album: s.album, albumId: s.albumId, artistId: s.artistId,
  coverArt: s.coverArt, duration: s.duration, track: s.track, ...(s.online ? { online: true, videoId: s.videoId, pay: s.pay } : {}) });
function persistSession(withList) {
  try {
    if (withList) localStorage.setItem('tr.queue', JSON.stringify({ list: P.list.slice(0, 600).map(compact), index: P.index, ctx: P.ctx }));
    localStorage.setItem('tr.pos', JSON.stringify({ id: cur() && cur().id, pos: audio.currentTime || P.pendingSeek || 0 }));
  } catch (e) { /* storage full or disabled */ }
}
function restoreSession() {
  try {
    const q = JSON.parse(localStorage.getItem('tr.queue') || 'null');
    const p = JSON.parse(localStorage.getItem('tr.pos') || 'null');
    if (!q || !q.list || !q.list.length || q.index < 0) return;
    P.list = q.list; P.index = Math.min(q.index, q.list.length - 1); P.ctx = q.ctx || null;
    if (p && p.id === cur().id) P.pendingSeek = p.pos || 0;
    bus.dispatchEvent(new Event('track'));
  } catch (e) { /* ignore */ }
}

function playQueue(list, index, ctx, opts = {}) {
  if (!list.length) return;
  P.list = list.slice();
  P.ctx = ctx || null;
  P.shuffle = !!opts.shuffle; P.orig = opts.orig ? opts.orig.slice() : null;
  persistSession(true);
  if (ctx) touchRecent(ctx);
  return loadTrack(index, opts.startAt);
}

// ----- Volume levelling: loud songs are turned down so the volume stays about the same from song to song -----
// Titan measures every song's loudness (LUFS) in the background; the app lowers anything louder than the target.
const LEVEL_TARGET = -14;
const levelOn = () => lsGet('tr.level', true);
const levelVol = song => {
  if (!song || !levelOn() || isLong(song)) return 1;
  const l = S.lufs.get(song.id);
  return l == null ? 1 : Math.max(0.15, Math.min(1, Math.pow(10, (LEVEL_TARGET - l) / 20)));
};
const applyVolume = () => { audio.volume = levelVol(cur()); };
let levelAsk = 0;
async function ensureLevels(songs) {
  const ids = songs.filter(x => x && !x.online && !isLong(x) && !S.lufs.has(x.id)).map(x => x.id);
  if (!ids.length || !levelOn() || S.offline) return;
  try {
    const r = await fetch(api(`/api/loudness?ids=${ids.join(',')}`));
    if (!r.ok) return;
    Object.entries((await r.json()).levels || {}).forEach(([id, l]) => S.lufs.set(id, l));
    applyVolume();
    if (ids.some(id => !S.lufs.has(id)) && ++levelAsk < 6) setTimeout(() => ensureLevels(songs), 20000);   // not measured yet: ask again shortly
  } catch (e) { /* levelling is optional */ }
}

// ----- Gapless / crossfade (off by default; Playback settings). The next song is loaded into the idle element and
// started just before the current one ends; with a crossfade the two overlap and swap volume. -----
const xfSec = () => lsGet('tr.xfade', 0);
const gaplessOn = () => lsGet('tr.gapless', false) || xfSec() > 0;
const XF = { nextId: null, busy: false, fading: false, failed: null };
function xfReset() {
  XF.nextId = null; XF.failed = null;
  const idle = idleAudio();
  if (!XF.fading && idle.src) { idle.pause(); idle.removeAttribute('src'); }
}
const xfNext = () => P.list[P.index + 1];
function xfEligible() {
  const s = cur(), n = xfNext();
  return gaplessOn() && !!s && !!n && !s.online && !n.online && !isLong(s) && !isLong(n) && P.loaded && !SLEEP.endOfTrack && XF.failed !== s.id;
}
function xfTick() {
  if (XF.busy || audio.paused || !isFinite(audio.duration) || !xfEligible()) return;
  const n = xfNext(), idle = idleAudio();
  if (!XF.fading && XF.nextId !== n.id && audio.currentTime > 2) {
    XF.nextId = n.id; idle.src = srcFor(n); idle.volume = 0; idle.load();
  }
  const lead = Math.max(xfSec(), 0) + 0.25;
  if (XF.nextId === n.id && audio.duration - audio.currentTime <= lead && idle.readyState >= 3) startCross(n);
}
setInterval(() => { if (gaplessOn() && !audio.paused) xfTick(); }, 120);
function startCross(n) {
  XF.busy = true;
  const old = audio, nw = idleAudio(), fade = xfSec(), s0 = cur();
  nw.currentTime = 0; nw.volume = fade > 0 ? 0 : levelVol(n);
  nw.defaultPlaybackRate = 1; nw.playbackRate = 1;
  nw.play().then(() => {
    P.index++; audio = nw; P.loaded = true; P.starting = false; P.scrobbled = false; P.pendingSeek = 0; P.tok++; P.errors = 0; XF.nextId = null;
    if (s0) clearBookmark(s0);
    bus.dispatchEvent(new Event('track')); bus.dispatchEvent(new Event('state'));
    setMediaSession(); persistSession(false);
    ensureLevels([xfNext(), P.list[P.index + 2]]);
    if (fade > 0) {
      XF.fading = true;
      const t0 = Date.now(), v0 = old.volume, v1 = levelVol(n);
      const iv = setInterval(() => {
        const k = Math.min(1, (Date.now() - t0) / (fade * 1000));
        old.volume = Math.max(0, v0 * Math.cos(k * Math.PI / 2)); nw.volume = Math.min(1, v1 * Math.sin(k * Math.PI / 2));
        if (k >= 1) { clearInterval(iv); old.pause(); old.removeAttribute('src'); XF.fading = false; }
      }, 80);
    } else { old.pause(); old.removeAttribute('src'); }
    XF.busy = false;
  }).catch(() => { XF.failed = s0 && s0.id; XF.busy = false; });   // the phone refused: this song ends normally and the usual path loads the next
}

// ----- Skip silence in podcasts: Titan finds the quiet gaps once per episode; the player jumps over them -----
const skipSilenceOn = () => lsGet('tr.skipsil', false);
const SIL = { id: null, ranges: null, tries: 0 };
async function loadSilences(song) {
  SIL.id = song.id; SIL.ranges = null; SIL.tries = 0;
  if (!skipSilenceOn() || !isPodcastSong(song) || S.offline) return;
  const poll = async () => {
    if (SIL.id !== song.id || !skipSilenceOn()) return;
    try {
      const r = await (await fetch(api(`/api/podcast/silences?id=${encodeURIComponent(song.id)}`))).json();
      if (r.ready) { if (SIL.id === song.id) SIL.ranges = r.ranges || []; return; }
    } catch (e) { /* retry */ }
    if (++SIL.tries < 30) setTimeout(poll, 15000);
  };
  poll();
}
function silenceTick() {
  const s = cur();
  if (!SIL.ranges || !s || SIL.id !== s.id || !skipSilenceOn()) return;
  const t = audio.currentTime;
  for (const [a, b] of SIL.ranges) {
    if (t < a) break;
    if (t >= a + 0.15 && t < b - 0.3) {   // keep a natural beat at each end of the gap
      audio.currentTime = b - 0.2;
      lsSet('tr.silsaved', (lsGet('tr.silsaved', 0) || 0) + (b - 0.2 - t));
      break;
    }
  }
}

async function loadTrack(i, startAt) {
  const song = P.list[i];
  if (!song) return;
  const tok = ++P.tok;
  P.index = i; P.loaded = false; P.starting = true; P.scrobbled = false; P.pendingSeek = 0;
  audio.pause();
  xfReset();
  bus.dispatchEvent(new Event('track'));
  let pos = startAt;
  if (pos == null && isLong(song)) {
    try { await refreshBookmarks(); } catch (e) { /* use what we have */ }
    const b = S.bookmarks.get(song.id);
    pos = b ? b.position / 1000 : 0;
    if (song.duration && pos > song.duration - 20) pos = 0;
  }
  if (tok !== P.tok) return;
  applySpeed(); applyVolume();
  ensureLevels([song, P.list[i + 1], P.list[i + 2]]); loadSilences(song);
  audio.src = srcFor(song);   // a downloaded copy (Android app) wins: no data used, works offline
  P.loaded = true;
  const el = audio;
  if (pos > 1) el.addEventListener('loadedmetadata', () => { try { el.currentTime = pos; } catch (e) { /* ignore */ } }, { once: true });
  try { await audio.play(); } catch (e) {
    // Blocked or superseded. If a song is still meant to be playing (auto-advance in the background), try again once it can play.
    if (tok === P.tok) audio.addEventListener('canplay', () => { if (tok === P.tok && audio.paused) audio.play().catch(() => {}); }, { once: true });
  }
  if (tok === P.tok) { P.starting = false; bus.dispatchEvent(new Event('state')); }
  setMediaSession();
  persistSession(false);
}

function togglePlay() {
  if (!cur()) return;
  if (!P.loaded) { loadTrack(P.index, P.pendingSeek || undefined); return; }
  if (audio.paused) audio.play().catch(() => {}); else audio.pause();
}
function next(auto) {
  if (P.index + 1 < P.list.length) loadTrack(P.index + 1);
  else if (auto) { audio.pause(); if (isPodcastSong(cur())) playUpNext().catch(() => {}); else autoplayExtend(); }
}
// When a podcast episode ends with nothing queued after it, carry on with the newest unplayed episode of each show you follow.
function followedShowIds() {
  const ids = new Set();
  S.pins.forEach(p => { if (p.type === 'album' && S.podAlbums.has(p.id)) ids.add(S.podCanon.get(p.id) || p.id); });
  Object.keys(lsGet('tr.autodl', {})).forEach(id => ids.add(id));
  S.recents.filter(r => r.type === 'album' && S.podAlbums.has(r.id)).slice(0, 6).forEach(r => ids.add(S.podCanon.get(r.id) || r.id));
  return [...ids];
}
async function upNextEpisodes(excludeId) {
  const out = [];
  for (const id of followedShowIds()) {
    const show = await podcastShow(id).catch(() => null);
    if (!show) continue;
    const ep = show.songs.filter(sg => sg.id !== excludeId && epInfo(sg).state === 'new').sort((a, b) => byDateAsc(b, a))[0];
    if (ep) out.push({ song: ep, al: show.main, songs: show.songs });
  }
  return out.sort((a, b) => epTs(b.song) - epTs(a.song));
}
async function playUpNext() {
  const list = await upNextEpisodes(cur() && cur().id);
  if (!list.length || (cur() && P.index + 1 < P.list.length)) return;
  toast(`Up next: ${list[0].song.title}`, 'ok', 3500);
  playQueue(list.map(x => x.song), 0, null);
}
function upNextCard({ song, al }) {
  return h('button', { class: 'card', onclick: () => { haptic(); playQueue([song], 0, null); } },
    cover(song.coverArt, '', 300), h('div', { class: 'nm' }, song.title), h('div', { class: 'sb' }, [al.name, fmtDate(epTs(song))].filter(Boolean).join(' • ')));
}
function prev() {
  if (audio.currentTime > 5 || P.index === 0) { audio.currentTime = 0; return; }
  loadTrack(P.index - 1);
}
function seekBy(sec) {
  if (!P.loaded) return;
  const d = audio.duration || (cur() && cur().duration) || 0;
  audio.currentTime = Math.max(0, Math.min(d || 1e9, audio.currentTime + sec));
}
function seekTo(sec) { if (P.loaded) audio.currentTime = sec; }
function enqueue(song, playNext) {
  if (!cur()) { playQueue([song], 0, null); return; }
  P.list.splice(playNext ? P.index + 1 : P.list.length, 0, song);
  persistSession(true);
  bus.dispatchEvent(new Event('queue'));
  haptic();
  toast(playNext ? 'Playing next' : 'Added to queue', 'ok', 2000);
}

function saveBookmark(song, pos) {
  if (!isLong(song) || pos < 3) return;
  const ms = Math.round(pos * 1000);
  S.bookmarks.set(song.id, { position: ms, entry: song, changed: new Date().toISOString() });
  S.homeDirty = true;
  persistBookmarks();
  if (S.finished.has(song.id)) { S.finished.delete(song.id); S.finishedAt[song.id] = Date.now(); saveState(); }   // started it again: no longer "played"
  absPush(song, ms, false);
  fetch(api(`/api/nd/rest/createBookmark?id=${encodeURIComponent(song.id)}&position=${ms}`), { keepalive: true })
    .then(r => { if (!r.ok) queueBookmark(song.id, ms); }).catch(() => queueBookmark(song.id, ms));
  bus.dispatchEvent(new Event('progress'));
}
function clearBookmark(song) {
  if (!S.bookmarks.has(song.id)) return;
  S.bookmarks.delete(song.id);
  S.homeDirty = true;
  persistBookmarks();
  fetch(api(`/api/nd/rest/deleteBookmark?id=${encodeURIComponent(song.id)}`), { keepalive: true })
    .then(r => { if (!r.ok) queueBookmark(song.id, null); }).catch(() => queueBookmark(song.id, null));
  bus.dispatchEvent(new Event('progress'));
}
// "Played" / "not started" for podcast episodes (kept with the rest of the app state, so it follows you between devices)
const snapOf = songs => songs.map(s => ({ s, fin: S.finished.has(s.id), b: S.bookmarks.get(s.id) }));
function restoreSnap(snap) {
  snap.forEach(({ s, fin, b }) => {
    if (fin) S.finished.add(s.id); else S.finished.delete(s.id);
    S.finishedAt[s.id] = Date.now();
    if (b) { S.bookmarks.set(s.id, b); persistBookmarks(); nd('createBookmark', { id: s.id, position: b.position }).catch(() => {}); absPush(s, b.position, false, true); }
    else { clearBookmark(s); absPush(s, 0, fin, true); }
  });
  saveState(); bus.dispatchEvent(new Event('progress'));
}
function markFinished(song) { S.finished.add(song.id); S.finishedAt[song.id] = Date.now(); clearBookmark(song); saveState(); absPush(song, 0, true); bus.dispatchEvent(new Event('progress')); }
function markNotStarted(song) { S.finished.delete(song.id); S.finishedAt[song.id] = Date.now(); clearBookmark(song); saveState(); absPush(song, 0, false); bus.dispatchEvent(new Event('progress')); }
function markMany(songs, finished) { songs.forEach(sg => { if (finished) S.finished.add(sg.id); else S.finished.delete(sg.id); S.finishedAt[sg.id] = Date.now(); clearBookmark(sg); absPush(sg, 0, finished, true); }); saveState(); bus.dispatchEvent(new Event('progress')); }

// ----- Audiobookshelf progress sync: the newer side wins, checked when the app opens/resumes and live while listening -----
const absLast = new Map();
function absPush(song, positionMs, finished, force) {
  if (!S.abs || S.offline || (!force && !isPodcastSong(song))) return;
  const now = Date.now();
  if (!finished && !force && now - (absLast.get(song.id) || 0) < 30000) return;
  absLast.set(song.id, now);
  fetch(api('/api/podcast/abs-progress'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
    body: JSON.stringify({ song: song.id, position: positionMs, duration: song.duration || 0, finished: !!finished }) }).catch(() => {});
}
let lastAbsPull = 0;
async function absPull(force) {
  if (!S.abs || S.offline || (!force && Date.now() - lastAbsPull < 60000)) return;
  lastAbsPull = Date.now();
  let list;
  try { const r = await fetch(api('/api/podcast/abs-progress')); if (!r.ok) return; list = (await r.json()).progress; } catch (e) { return; }
  let changed = false;
  const seen = new Set();
  for (const it of list) {
    seen.add(it.song);
    const b = S.bookmarks.get(it.song), localFin = S.finished.has(it.song);
    const localTs = Math.max(b ? Date.parse(b.changed) || 0 : 0, S.finishedAt[it.song] || 0);
    const same = it.finished ? localFin : (!localFin && b && Math.abs(b.position - it.position) < 3000);
    if (same) continue;
    if (it.ts > localTs + 1500) {   // Audiobookshelf is newer: take its state
      try {
        if (it.finished) { S.finished.add(it.song); if (b) await nd('deleteBookmark', { id: it.song }); S.bookmarks.delete(it.song); }
        else { S.finished.delete(it.song); await nd('createBookmark', { id: it.song, position: it.position }); }
        S.finishedAt[it.song] = it.ts; changed = true;
      } catch (e) { /* try again next time */ }
    } else if (localTs > it.ts + 1500) {   // ours is newer: send it
      absPush((b && b.entry) || { id: it.song, duration: it.duration }, b ? b.position : 0, localFin, true);
    }
  }
  // Progress we have that Audiobookshelf has never heard of (made before the two were linked)
  for (const [id, b] of S.bookmarks) if (!seen.has(id) && isPodcastSong(b.entry)) absPush(b.entry, b.position, false, true);
  if (changed) { saveState(); await refreshBookmarks().catch(() => {}); S.homeDirty = true; bus.dispatchEvent(new Event('progress')); }
}

onAudio('timeupdate', () => {
  const s = cur();
  if (!s || !P.loaded) return;
  const now = Date.now();
  if (!audio.paused && now - P.lastSave > BOOKMARK_EVERY_MS) { P.lastSave = now; saveBookmark(s, audio.currentTime); }
  if (!P.scrobbled && audio.currentTime > Math.min(30, (s.duration || 60) / 2)) {
    P.scrobbled = true;
    if (!s.online) nd('scrobble', { id: s.id, submission: true }).catch(() => {});
    logPlay(s);
  }
  if (now - P.lastPersist > 3000) { P.lastPersist = now; persistSession(false); }
  P.errors = 0;
  xfTick(); silenceTick();
  bus.dispatchEvent(new Event('time'));
});
onAudio('play', () => { bus.dispatchEvent(new Event('state')); if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing'; });
onAudio('pause', () => {
  bus.dispatchEvent(new Event('state'));
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'paused';
  const s = cur();
  if (s && P.loaded && !audio.ended) { saveBookmark(s, audio.currentTime); persistSession(false); }
});
onAudio('ended', () => {
  const s = cur();
  if (s) { if (isLong(s)) { markFinished(s); autoRemoveDownload(s); } else clearBookmark(s); }
  if (SLEEP.endOfTrack) { SLEEP.endOfTrack = false; bus.dispatchEvent(new Event('sleep')); return; }   // sleep timer: stop here
  next(true);
});
onAudio('error', () => {
  if (!P.loaded) return;
  P.errors++;
  const mini = $('#mini');
  mini.classList.add('err');
  $('#miniSub').textContent = 'Can’t play this file';
  setTimeout(() => { mini.classList.remove('err'); renderMini(); }, 2500);
  if (P.errors <= 3 && P.index + 1 < P.list.length) setTimeout(() => next(true), 800);
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    const s = cur();
    if (s && P.loaded) saveBookmark(s, audio.currentTime);
    persistSession(false);
  } else {
    if (NATIVE_DL) refreshDownloads();   // downloads may have finished while the app was in the background
    watchLibrary(); absPull();
    if (Date.now() - autoDlLast > 600000) autoDownloadPass();
    if (S.ready && curTab === 'home' && Date.now() - lastHome > 45000) renderHome();
  }
});
window.addEventListener('online', () => { if (S.offline) location.reload(); });
// Navidrome notes every time it notices songs/episodes added or removed; when that changes, refresh what's on screen.
let scanSig = null;
async function watchLibrary() {
  if (!S.ready || S.offline || document.visibilityState !== 'visible') return;
  try {
    const st = (await nd('getScanStatus')).scanStatus || {};
    const sig = `${st.lastScan}|${st.count}|${st.folderCount}`;
    const changed = scanSig && sig !== scanSig && !st.scanning;
    if (!st.scanning) scanSig = sig;
    if (changed) {
      ['pl', 'allal', 'allar', 'al:', 'ar:', 'genres', 'disc:', 'epd:'].forEach(bust);
      S.homeDirty = true;
      if (curTab === 'home') renderHome(); else if (curTab === 'library') renderLibrary();
    }
  } catch (e) { /* try again next time */ }
}
setInterval(watchLibrary, 40000);
window.addEventListener('pagehide', () => { const s = cur(); if (s && P.loaded) saveBookmark(s, audio.currentTime); persistSession(false); });

function setMediaSession() {
  if (!('mediaSession' in navigator)) return;
  const s = cur();
  if (!s) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({ title: s.title, artist: s.artist || '', album: s.album || '',
      artwork: s.coverArt ? [{ src: absUrl(coverUrl(s.coverArt, 512)), sizes: '512x512', type: 'image/jpeg' }] : [] });
  } catch (e) { /* ignore */ }
}
if ('mediaSession' in navigator) {
  const ms = navigator.mediaSession;
  const set = (a, f) => { try { ms.setActionHandler(a, f); } catch (e) { /* unsupported */ } };
  set('play', () => togglePlay());
  set('pause', () => audio.pause());
  set('previoustrack', () => prev());
  set('nexttrack', () => next());
  set('seekbackward', d => seekBy(-((d && d.seekOffset) || 15)));
  set('seekforward', d => seekBy((d && d.seekOffset) || 30));
  set('seekto', d => { if (d && d.seekTime != null) seekTo(d.seekTime); });
  onAudio('timeupdate', () => {
    if (!audio.duration || !isFinite(audio.duration)) return;
    try { ms.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration), playbackRate: audio.playbackRate }); } catch (e) { /* ignore */ }
  });
}

// ----- Android app wrapper (the APK): native notification, lock-screen and Bluetooth controls -----
if (window.TRNative) {
  const pushNative = () => {
    const s = cur();
    if (!s) { TRNative.clear(); return; }
    const d = audio.duration && isFinite(audio.duration) ? audio.duration : (s.duration || 0);
    TRNative.update(JSON.stringify({ id: s.id, title: s.title, artist: s.artist || '', album: s.album || '',
      art: s.coverArt ? absUrl(coverUrl(s.coverArt, 512)) : '',
      // a song that just ended with another one queued is about to start: don't report a pause in between
      playing: P.loaded ? (!audio.paused || (audio.ended && P.index + 1 < P.list.length)) : P.starting,
      pos: Math.round((P.loaded ? audio.currentTime : (P.pendingSeek || 0)) * 1000), dur: Math.round(d * 1000) }));
  };
  ['track', 'state'].forEach(e => bus.addEventListener(e, pushNative));
  onAudio('seeked', pushNative);
  setInterval(() => { if (!audio.paused) pushNative(); }, 15000);
  window.TRNative_cmd = c => {
    if (c === 'play') { if (audio.paused || !P.loaded) togglePlay(); }
    else if (c === 'pause') audio.pause();
    else if (c === 'next') next();
    else if (c === 'prev') prev();
    else if (c === 'ffwd') seekBy(30);
    else if (c === 'rew') seekBy(-15);
    else if (c.startsWith('seek:')) seekTo(Number(c.slice(5)) / 1000);
  };
}

// ----- Downloads / offline listening (Android app) -----
const NATIVE_DL = !!(window.TRNative && TRNative.offlineSupported && TRNative.offlineSupported());
function offlineUrl(id) { try { return NATIVE_DL ? TRNative.dlUrl(id) : ''; } catch (e) { return ''; } }
function readDownloads() { try { return NATIVE_DL ? JSON.parse(TRNative.dlList()) : []; } catch (e) { return []; } }
let dlTimer = null;
function refreshDownloads() {
  if (!NATIVE_DL) return;
  S.dl = new Map(readDownloads().map(d => [d.id, d]));
  bus.dispatchEvent(new Event('dl'));
  clearTimeout(dlTimer);
  if ([...S.dl.values()].some(d => ['queued', 'running', 'waiting'].includes(d.status))) dlTimer = setTimeout(refreshDownloads, 1500);
}
const dlStatus = id => (S.dl.get(id) || {}).status || 'none';
function startDownload(song) {
  if (!NATIVE_DL) return;
  const res = TRNative.dlStart(JSON.stringify({ id: song.id, url: absUrl(streamUrl(song.id)), coverUrl: song.coverArt ? absUrl(coverUrl(song.coverArt, 512)) : '', song: slimSong(song) }));
  if (res !== 'ok' && res !== 'exists') toast(`Couldn’t start the download: ${res}`, 'bad');
  refreshDownloads();
}
// Auto-download: shows you turn on get their newest unplayed episodes whenever the app is open (Wi-Fi only unless mobile data is allowed).
// Auto-remove: a finished episode's download is deleted (on by default; switch in Library > Downloaded).
const autoRm = () => lsGet('tr.autorm', true);
function autoRemoveDownload(song) { if (NATIVE_DL && autoRm() && isPodcastSong(song) && dlStatus(song.id) !== 'none') removeDownload(song); }
const autoDlOn = albumId => !!lsGet('tr.autodl', {})[S.podCanon.get(albumId) || albumId];
function setAutoDl(albumId, on) { const m = lsGet('tr.autodl', {}), k = S.podCanon.get(albumId) || albumId; if (on) m[k] = 1; else delete m[k]; lsSet('tr.autodl', m); if (on) autoDownloadPass(); }
let autoDlRunning = false, autoDlLast = 0;
async function autoDownloadPass() {
  if (!NATIVE_DL || S.offline || !S.ready || autoDlRunning) return;
  autoDlRunning = true; autoDlLast = Date.now();
  try {
    for (const id of Object.keys(lsGet('tr.autodl', {}))) {
      const show = await podcastShow(id).catch(() => null);
      if (!show) continue;
      const want = show.songs.filter(sg => epInfo(sg).state === 'new').sort((a, b) => byDateAsc(b, a)).slice(0, 5)
        .filter(sg => dlStatus(sg.id) === 'none');
      want.forEach(startDownload);
    }
  } finally { autoDlRunning = false; }
}
function removeDownload(song) { if (NATIVE_DL) { TRNative.dlRemove(song.id); refreshDownloads(); } }
function downloadMany(songs) {
  const todo = songs.filter(sg => dlStatus(sg.id) === 'none' || dlStatus(sg.id) === 'failed');
  todo.forEach(startDownload);
  toast(todo.length ? `Downloading ${plural(todo.length, 'song')}…` : 'Already downloaded', 'ok', 2500);
}
const fmtBytes = n => n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;

// ----- Mini player -----
function renderMini() {
  const s = cur();
  const mini = $('#mini');
  mini.classList.toggle('hidden', !s);
  if (!s) return;
  const c = $('#miniCover');
  c.replaceWith(Object.assign(cover(s.coverArt, 'sm', 100), { id: 'miniCover' }));
  $('#miniTitle').textContent = s.title;
  $('#miniSub').textContent = s.artist || '';
  const hb = $('#miniHeart');
  hb.dataset.sid = s.id;
  paintHeart(hb);
  paintMiniState();
}
function paintMiniState() { $('#miniPlay').innerHTML = !audio.paused && P.loaded ? I.pause : I.play; }
function paintMiniTime() {
  const d = audio.duration || (cur() && cur().duration) || 0;
  $('#miniBar').style.width = d ? `${Math.min(100, (audio.currentTime / d) * 100)}%` : '0';
}
$('#miniPlay').addEventListener('click', e => { e.stopPropagation(); togglePlay(); });
$('#miniHeart').addEventListener('click', e => { e.stopPropagation(); if (cur()) toggleLike(cur()); });
$('#miniBody').addEventListener('click', () => openNowPlaying());
onFlick($('#miniBody'), { left: () => { haptic(); next(); }, right: () => { haptic(); prev(); }, up: () => openNowPlaying() }, { min: 50 });
$('#miniPlay').innerHTML = I.play;

function markPlayingRows() {
  const s = cur();
  document.querySelectorAll('.row[data-sid]').forEach(r => r.classList.toggle('playing', !!s && r.dataset.sid === s.id));
}
bus.addEventListener('track', () => { renderMini(); markPlayingRows(); });
bus.addEventListener('state', () => { paintMiniState(); });
bus.addEventListener('time', () => { paintMiniTime(); sleepCheck(); });

// ----- Gestures -----
// Swipe a row to the right to add it to the queue (it slides, buzzes, and springs back).
function swipeToQueue(row, song) {
  let x0 = 0, y0 = 0, dx = 0, mode = null;
  row.addEventListener('touchstart', e => { const t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; dx = 0; mode = x0 < 28 ? 'no' : null; }, { passive: true });
  row.addEventListener('touchmove', e => {
    if (mode === 'no') return;
    const t = e.touches[0], mx = t.clientX - x0, my = t.clientY - y0;
    if (mode === null && (Math.abs(mx) > 12 || Math.abs(my) > 12)) mode = (mx > 0 && Math.abs(mx) > Math.abs(my) * 1.6) ? 'swipe' : 'no';
    if (mode !== 'swipe') return;
    e.preventDefault();
    dx = Math.min(110, Math.max(0, mx));
    row.style.transition = 'none'; row.style.transform = `translateX(${dx}px)`;
    row.classList.toggle('swipe-armed', dx > 70);
  }, { passive: false });
  const end = () => {
    if (mode === 'swipe') {
      row.style.transition = 'transform .18s'; row.style.transform = '';
      if (dx > 70) { enqueue(song, false); row.dataset.swiped = '1'; setTimeout(() => delete row.dataset.swiped, 400); }
      row.classList.remove('swipe-armed');
    }
    mode = null; dx = 0;
  };
  row.addEventListener('touchend', end); row.addEventListener('touchcancel', end);
  row.addEventListener('click', e => { if (row.dataset.swiped) { e.stopImmediatePropagation(); e.preventDefault(); } }, true);
}
// A one-direction flick on an element: up/down/left/right handlers, with a minimum distance.
function onFlick(el, handlers, { min = 60, ignore } = {}) {
  let x0 = 0, y0 = 0, on = false;
  el.addEventListener('touchstart', e => { on = !(ignore && e.target.closest(ignore)); const t = e.touches[0]; x0 = t.clientX; y0 = t.clientY; }, { passive: true });
  el.addEventListener('touchend', e => {
    if (!on) return;
    const t = e.changedTouches[0], dx = t.clientX - x0, dy = t.clientY - y0;
    const ax = Math.abs(dx), ay = Math.abs(dy);
    if (Math.max(ax, ay) < min) return;
    const dir = ax > ay * 1.4 ? (dx > 0 ? 'right' : 'left') : ay > ax * 1.4 ? (dy > 0 ? 'down' : 'up') : null;
    if (dir && handlers[dir]) { handlers[dir](); el.dataset.flicked = '1'; setTimeout(() => delete el.dataset.flicked, 350); }
  });
  el.addEventListener('click', e => { if (el.dataset.flicked) { e.stopImmediatePropagation(); e.preventDefault(); } }, true);
}

// ----- Colour from the cover art (Now Playing background) -----
const tintCache = new Map();
function coverTint(id) {
  if (!id) return Promise.resolve(null);
  if (tintCache.has(id)) return tintCache.get(id);
  const p = new Promise(res => {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement('canvas'); c.width = c.height = 24;
        const g = c.getContext('2d', { willReadFrequently: true }); g.drawImage(img, 0, 0, 24, 24);
        const d = g.getImageData(0, 0, 24, 24).data;
        let r = 0, gr = 0, b = 0, w = 0;
        for (let i = 0; i < d.length; i += 4) {
          const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
          const wt = 0.15 + (mx - mn) / 255;   // colourful pixels count more than grey ones
          r += d[i] * wt; gr += d[i + 1] * wt; b += d[i + 2] * wt; w += wt;
        }
        const k = 0.62 / Math.max(0.01, Math.max(r, gr, b) / w / 255 || 1);   // bring the colour to a comfortable, darkish brightness
        const f = v => Math.max(0, Math.min(255, Math.round(v / w * Math.min(k, 1.6))));
        res([f(r), f(gr), f(b)]);
      } catch (e) { res(null); }
    };
    img.onerror = () => res(null);
    img.src = coverUrl(id, 64);
  });
  tintCache.set(id, p);
  return p;
}

// ----- Lyrics (synced when the file has an .lrc / embedded timings) -----
const lyricsCache = new Map();
function getLyrics(song) {
  if (lyricsCache.has(song.id)) return lyricsCache.get(song.id);
  const p = (S.offline || song.online || isPodcastSong(song)) ? Promise.resolve(null) : nd('getLyricsBySongId', { id: song.id }).then(r => {
    const l = arr(r.lyricsList && r.lyricsList.structuredLyrics);
    const pick = l.find(x => x.synced) || l[0];
    const lines = pick ? arr(pick.line).map(x => ({ ms: x.start || 0, text: (x.value || '').trim() })) : [];
    return lines.some(x => x.text) ? { synced: !!pick.synced, lines } : null;
  }).catch(() => null);
  lyricsCache.set(song.id, p);
  return p;
}

// ----- Now Playing -----
let npOpen = false, npLyrics = false, npViz = false;
function openNowPlaying() {
  if (npOpen || !cur()) return;
  npOpen = true;
  const bg = h('div', { class: 'bg' });
  const artBox = h('div', { class: 'art' });
  const ttl = h('div', { class: 'ttl' }), artistEl = h('div', { class: 'art-name' });
  const heartHolder = h('div', { style: 'flex:none' });
  const range = h('input', { type: 'range', min: 0, max: 1000, value: 0, step: 1, 'aria-label': 'Seek' });
  const tCur = h('span', {}, '0:00'), tLeft = h('span', {}, '0:00');
  const playB = h('button', { class: 'icon-btn big', 'aria-label': 'Play or pause', onclick: togglePlay });
  const rewB = h('button', { class: 'icon-btn', html: I.rew, 'aria-label': 'Back 15 seconds', onclick: () => seekBy(-15) });
  const fwdB = h('button', { class: 'icon-btn', html: I.fwd, 'aria-label': 'Forward 30 seconds', onclick: () => seekBy(30) });
  const fromB = h('b');
  const sleepB = h('button', { class: 'chip-btn', onclick: sleepSheet, 'aria-label': 'Sleep timer' }, h('span', { html: I.moon }), h('b', {}));
  const speedB = h('button', { class: 'chip-btn', onclick: speedSheet, 'aria-label': 'Playback speed' }, h('span', { html: I.speed }), h('b', {}));
  const vizB = h('button', { class: 'chip-btn' + (FX.on ? '' : ' hidden'), onclick: () => { npViz = !npViz; if (npViz) npLyrics = false; paint(); }, 'aria-label': 'Visualizer' }, h('span', { html: I.wave }), h('b', {}, 'Visualizer'));
  const lyrB = h('button', { class: 'chip-btn hidden', onclick: () => { npLyrics = !npLyrics; if (npLyrics) npViz = false; paint(); }, 'aria-label': 'Lyrics' }, h('span', { html: I.lyrics }), h('b', {}, 'Lyrics'));
  const tint = h('div', { class: 'tint' });
  let dragging = false, lyr = null, lyrIdx = -1, lyrFor = null;

  const paint = () => {
    const s = cur();
    if (!s) return;
    bg.style.backgroundImage = s.coverArt ? `url(${coverUrl(s.coverArt, 200)})` : '';
    coverTint(s.coverArt).then(c => { if (c && cur() && cur().id === s.id) { tint.style.setProperty('--tint', `rgb(${c})`); tint.classList.remove('in'); void tint.offsetWidth; tint.classList.add('in'); bg.classList.add('dim'); } });
    lyrB.classList.add('hidden');
    lyrFor = s.id;
    getLyrics(s).then(L => { if (lyrFor !== s.id) return; lyr = L; lyrIdx = -1; lyrB.classList.toggle('hidden', !L); drawArt(); });
    lyr = null; drawArt();
    ttl.textContent = s.title;
    put(artistEl, s.artist ? h('button', { class: 'lnk', onclick: () => leaveNowPlayingThen(() => openArtist({ navId: s.artistId, name: s.artist })) }, s.artist) : null,
      s.album ? ` • ${s.album}` : null);
    heartHolder.replaceChildren(heartBtn(s));
    fromB.textContent = (P.ctx && P.ctx.name) || 'Queue';
    const long = isLong(s);
    rewB.classList.toggle('hidden', !long);
    fwdB.classList.toggle('hidden', !long);
    speedB.classList.toggle('hidden', !long);
    paintChips();
    paintState(); paintTime();
  };
  const paintChips = () => {
    const sl = sleepLabel(), sp = speedFor(cur());
    sleepB.classList.toggle('on', !!sl); sleepB.lastChild.textContent = sl;
    speedB.classList.toggle('on', sp !== 1); speedB.lastChild.textContent = sp === 1 ? '1×' : `${sp}×`;
    lyrB.classList.toggle('on', npLyrics); vizB.classList.toggle('on', npViz);
  };
  // the big area shows the cover, or the lyrics when that is switched on
  const drawArt = () => {
    const s = cur();
    if (!s) return;
    if (npLyrics && lyr) {
      const box = h('div', { class: 'lyrics' }, lyr.lines.map((l, i) => h('div', { class: 'ly' + (l.text ? '' : ' gap'), 'data-i': i, onclick: () => { if (lyr.synced && P.loaded) seekTo(l.ms / 1000); } }, l.text || '♪')));
      artBox.replaceChildren(box); lyrIdx = -1; paintLyrics(true);
    } else if (npViz && FX.on && FX.an) artBox.replaceChildren(vizCanvas());
    else artBox.replaceChildren(cover(s.coverArt, '', 700));
    paintChips();
  };
  const paintLyrics = force => {
    if (!npLyrics || !lyr || !lyr.synced) return;
    const t = (P.loaded ? audio.currentTime : 0) * 1000 + 250;
    let i = -1;
    for (let k = 0; k < lyr.lines.length && lyr.lines[k].ms <= t; k++) i = k;
    if (i === lyrIdx && !force) return;
    lyrIdx = i;
    const box = artBox.firstChild;
    if (!box || !box.classList || !box.classList.contains('lyrics')) return;
    box.querySelectorAll('.ly.on').forEach(e => e.classList.remove('on'));
    const el = box.querySelector(`.ly[data-i="${i}"]`);
    if (el) { el.classList.add('on'); box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2, behavior: force ? 'auto' : 'smooth' }); }
  };
  const paintState = () => { playB.innerHTML = !audio.paused && P.loaded ? I.pause : I.play; };
  const paintTime = () => {
    const s = cur();
    const d = audio.duration && isFinite(audio.duration) ? audio.duration : (s && s.duration) || 0;
    const t = P.loaded ? audio.currentTime : (P.pendingSeek || 0);
    if (!dragging) {
      range.value = d ? Math.round((t / d) * 1000) : 0;
      range.style.setProperty('--p', `${d ? (t / d) * 100 : 0}%`);
      tCur.textContent = fmtTime(t);
    }
    tLeft.textContent = d ? `-${fmtTime((d - t) / (audio.playbackRate || 1))}` : '';
    paintLyrics(); if (sleepOn()) paintChips();
  };
  range.addEventListener('input', () => {
    dragging = true;
    const d = audio.duration || (cur() && cur().duration) || 0;
    tCur.textContent = fmtTime((range.value / 1000) * d);
    range.style.setProperty('--p', `${range.value / 10}%`);
  });
  range.addEventListener('change', () => {
    const d = audio.duration || (cur() && cur().duration) || 0;
    const t = (range.value / 1000) * d;
    if (P.loaded) seekTo(t); else { P.pendingSeek = t; }
    dragging = false;
  });

  const el = h('div', { class: 'np' }, bg, tint,
    h('div', { class: 'bar' },
      h('button', { class: 'icon-btn', html: I.down, 'aria-label': 'Close', onclick: () => closeTop() }),
      h('div', { class: 'from' }, 'Playing from', fromB),
      h('div', { style: 'display:flex' },
        h('button', { class: 'icon-btn', html: I.more, 'aria-label': 'More', onclick: () => nowPlayingMenu() }),
        h('button', { class: 'icon-btn', html: I.queue, 'aria-label': 'Queue', onclick: () => openQueue() }))),
    artBox,
    h('div', { class: 'info' }, h('div', { class: 'txt' }, ttl, artistEl), heartHolder),
    h('div', { class: 'seek' }, range, h('div', { class: 'times' }, tCur, tLeft)),
    h('div', { class: 'ctrls' },
      rewB,
      h('button', { class: 'icon-btn', html: I.prev, 'aria-label': 'Previous', onclick: prev }),
      playB,
      h('button', { class: 'icon-btn', html: I.next, 'aria-label': 'Next', onclick: () => next() }),
      fwdB),
    h('div', { class: 'extras' }, lyrB, vizB, speedB, sleepB));
  const onTrack = () => paint(), onState = () => paintState(), onTime = () => paintTime(), onChip = () => paintChips();
  bus.addEventListener('track', onTrack); bus.addEventListener('state', onState); bus.addEventListener('time', onTime);
  ['sleep', 'speed'].forEach(e => bus.addEventListener(e, onChip));
  paint();
  // flick down on the cover or the title area to close the player
  onFlick(el, { down: () => closeTop() }, { min: 90, ignore: '.seek, .lyrics, .ctrls, .extras, input' });
  openLayer(el, () => {
    npOpen = false;
    bus.removeEventListener('track', onTrack); bus.removeEventListener('state', onState); bus.removeEventListener('time', onTime);
    ['sleep', 'speed'].forEach(e => bus.removeEventListener(e, onChip));
  });
}

const leaveNowPlayingThen = fn => { if (npOpen) { closeTop(); whenSettled(fn); } else fn(); };
function nowPlayingMenu() {
  const song = cur();
  if (!song) return;
  if (song.online) { onlineSongSheet(song.pay, true); return; }
  const pod = isPodcastSong(song);
  sheet({
    title: song.title, sub: song.artist, coverEl: cover(song.coverArt, 'md', 120),
    items: [
      !pod && (song.artistId || song.artist) && { icon: 'user', label: 'Go to artist', run: () => leaveNowPlayingThen(() => openArtist({ navId: song.artistId, name: song.artist })) },
      song.albumId && { icon: 'disc', label: pod ? 'Go to podcast' : 'Go to album', run: () => leaveNowPlayingThen(() => openAlbum(song.albumId)) },
      !pod && { icon: 'note', label: 'About this song', run: () => aboutSong(song) },
      !pod && { icon: 'radio', label: 'Start radio', run: () => startRadio(seedOf(song), song.title, song) },
      { icon: 'playlist', label: 'Add to playlist', run: () => playlistPicker(song) },
      { icon: S.liked.has(song.id) ? 'heartOn' : 'heart', label: S.liked.has(song.id) ? 'Remove from Liked Songs' : 'Add to Liked Songs', run: () => toggleLike(song) },
      { icon: 'moon', label: sleepOn() ? `Sleep timer (${sleepLabel()})` : 'Sleep timer', run: sleepSheet },
      isLong(song) && { icon: 'speed', label: `Playback speed (${speedFor(song)}×)`, run: speedSheet },
    ],
  });
}

function moveInQueue(from, to) {
  const [x] = P.list.splice(from, 1);
  P.list.splice(to, 0, x);
  let idx = P.index;
  if (from === idx) idx = to; else if (from < idx && to >= idx) idx--; else if (from > idx && to <= idx) idx++;
  P.index = idx;
  xfReset(); persistSession(true);
  bus.dispatchEvent(new Event('queue'));
}
function openQueue() {
  const el = h('div', { class: 'queue' });
  // drag the handle at the right of a row up or down to reorder; the rows in between slide out of the way
  const dragHandle = (grip, row, i) => {
    let y0 = null, rowH = 0, over = i;
    grip.addEventListener('touchstart', e => { e.stopPropagation(); y0 = e.touches[0].clientY; rowH = row.offsetHeight; over = i; row.classList.add('dragging'); haptic(); }, { passive: true });
    grip.addEventListener('touchmove', e => {
      if (y0 == null) return;
      e.preventDefault(); e.stopPropagation();
      const dy = e.touches[0].clientY - y0;
      row.style.transform = `translateY(${dy}px)`;
      over = Math.max(0, Math.min(P.list.length - 1, i + Math.round(dy / rowH)));
      el.querySelectorAll('.row[data-i]').forEach(r => {
        const j = +r.dataset.i;
        if (r === row) return;
        const shift = (i < over && j > i && j <= over) ? -rowH : (i > over && j < i && j >= over) ? rowH : 0;
        r.style.transform = shift ? `translateY(${shift}px)` : '';
      });
    }, { passive: false });
    const end = () => { if (y0 == null) return; y0 = null; if (over !== i) moveInQueue(i, over); else render(); };
    grip.addEventListener('touchend', end); grip.addEventListener('touchcancel', end);
    grip.addEventListener('click', e => e.stopPropagation());
  };
  const clearUpcoming = () => {
    const removed = P.list.splice(P.index + 1);
    if (!removed.length) return;
    xfReset(); persistSession(true); render();
    toastUndo(`Cleared ${plural(removed.length, 'song')} from the queue`, () => { P.list.push(...removed); persistSession(true); bus.dispatchEvent(new Event('queue')); });
  };
  const render = () => {
    const upcoming = P.list.length - P.index - 1;
    const rows = P.list.map((s, i) => {
      const row = h('div', { class: 'row' + (i === P.index ? ' playing' : ''), 'data-sid': s.id, 'data-i': i, onclick: () => { loadTrack(i); } },
        cover(s.coverArt, 'sm', 100), h('div', { class: 'txt' }, h('div', { class: 't' }, s.title), h('div', { class: 's' }, s.artist)),
        i === P.index ? null : h('button', { class: 'icon-btn dim', html: I.x, 'aria-label': 'Remove from queue', onclick: e => {
          e.stopPropagation();
          const at = i, song = P.list[i];
          P.list.splice(at, 1); if (at < P.index) P.index--;
          xfReset(); persistSession(true); render();
          toastUndo(`Removed “${song.title}”`, () => { P.list.splice(Math.min(at, P.list.length), 0, song); if (at <= P.index) P.index++; persistSession(true); bus.dispatchEvent(new Event('queue')); });
        } }),
        h('button', { class: 'icon-btn dim grip', html: I.grip, 'aria-label': 'Drag to reorder' }));
      dragHandle(row.querySelector('.grip'), row, i);
      return row;
    });
    el.replaceChildren(
      h('div', { class: 'backbar' }, h('button', { class: 'icon-btn', html: I.back, 'aria-label': 'Back', onclick: () => closeTop() }), h('b', {}, 'Queue'),
        h('span', { style: 'flex:1' }), upcoming > 0 ? h('button', { class: 'pillbtn dark', onclick: clearUpcoming }, 'Clear queue') : null),
      ...rows);
    const playing = $('.row.playing', el);
    if (playing) playing.scrollIntoView({ block: 'center' });
  };
  const onQ = () => render(), onT = () => render();
  bus.addEventListener('queue', onQ); bus.addEventListener('track', onT);
  render();
  openLayer(el, () => { bus.removeEventListener('queue', onQ); bus.removeEventListener('track', onT); });
}

// =====================================================================
// Lists of songs
// =====================================================================
function songRow(song, o = {}) {
  const sub = o.sub != null ? o.sub : [song.artist, o.showAlbum ? song.album : null].filter(Boolean).join(' • ');
  const left = o.number != null ? [h('div', { class: 'num' }, o.number), o.withCover ? cover(song.coverArt, 'sm', 100) : null] : (o.noCover ? null : cover(song.coverArt, 'sm', 100));
  const row = h('div', { class: 'row' + (cur() && cur().id === song.id ? ' playing' : ''), 'data-sid': song.id, onclick: () => o.onPlay && o.onPlay() },
    left,
    h('div', { class: 'txt' },
      h('div', { class: 't' + (o.wrap ? ' wrap' : '') }, song.title),
      sub ? h('div', { class: 's' }, sub) : null,
      o.progress != null ? h('div', { class: 'prog' }, h('div', { style: `width:${Math.round(o.progress * 100)}%` })) : null),
    o.right || null,
    o.noMenu || song.online || isPodcastSong(song) ? null : h('button', { class: 'pl-dot', 'data-sid': song.id, hidden: !hasDot(song), html: I.check, 'aria-label': 'Saved in playlists', onclick: e => { e.stopPropagation(); playlistPicker(song); } }),
    o.noMenu ? null : h('button', { class: 'icon-btn dim', html: I.more, 'aria-label': 'More', onclick: e => { e.stopPropagation(); songSheet(song, o.menu || {}); } }));
  onLongPress(row, () => songSheet(song, o.menu || {}));
  swipeToQueue(row, song);
  return row;
}

function songSheet(song, ctx = {}) {
  const liked = S.liked.has(song.id);
  sheet({
    title: song.title, sub: song.artist, coverEl: cover(song.coverArt, 'md', 120),
    items: [
      { icon: liked ? 'heartOn' : 'heart', label: liked ? 'Remove from Liked Songs' : 'Add to Liked Songs', run: () => toggleLike(song) },
      { icon: 'playlist', label: 'Add to playlist', run: () => playlistPicker(song) },
      { icon: 'queue', label: 'Play next', run: () => enqueue(song, true) },
      { icon: 'list', label: 'Add to queue', run: () => enqueue(song, false) },
      !isPodcastSong(song) && { icon: 'radio', label: 'Start radio', run: () => startRadio({ artist: song.artist, title: song.title }, song.title, song) },
      canDelete() && !isPodcastSong(song) && { icon: 'trash', label: 'Delete from library', bad: true, run: () => deleteSongsFlow([song], `“${song.title}”`) },
      NATIVE_DL && (dlStatus(song.id) === 'none' || dlStatus(song.id) === 'failed') && { icon: 'dl', label: 'Download', run: () => { startDownload(song); toast(`Downloading “${song.title}”`, 'ok', 2500); } },
      NATIVE_DL && dlStatus(song.id) === 'done' && { icon: 'trash', label: 'Remove download', run: () => { removeDownload(song); toast('Download removed', 'ok', 2000); } },
      ctx.playlistId && ctx.index != null && { icon: 'trash', label: 'Remove from this playlist', bad: true, run: () => removeFromPlaylist(ctx) },
      song.albumId && !ctx.noAlbumLink && { icon: 'disc', label: 'Go to album', run: () => openAlbum(song.albumId) },
      (song.artistId || song.artist) && !ctx.noArtistLink && { icon: 'user', label: 'Go to artist', run: () => openArtist({ navId: song.artistId, name: song.artist }) },
      !isPodcastSong(song) && { icon: 'note', label: 'About this song', run: () => aboutSong(song) },
    ],
  });
}

async function removeFromPlaylist(ctx) {
  try {
    await nd('updatePlaylist', { playlistId: ctx.playlistId, songIndexToRemove: ctx.index });
    bust('pl');
    toast('Removed from playlist', 'ok', 2000);
    if (ctx.reload) ctx.reload();
  } catch (e) { toast(`Couldn’t remove: ${e.message}`, 'bad'); }
}

// Adding a song that is already in the playlist is allowed, but ask first so it's never a surprise.
async function confirmNotDuplicate(playlistId, playlistName, songs) {
  let have;
  try { have = new Set(arr((await getPlaylist(playlistId)).entry).map(e => e.id)); } catch (e) { return true; } // can't tell: just add
  const dups = songs.filter(sg => have.has(sg.id));
  if (!dups.length) return true;
  const msg = songs.length === 1 ? `“${songs[0].title}” is already in “${playlistName}”. Add it again?`
    : `${dups.length === songs.length ? 'All' : `${dups.length} of the ${songs.length}`} songs ${dups.length === 1 ? 'is' : 'are'} already in “${playlistName}”. Add ${dups.length === 1 ? 'it' : 'them'} again?`;
  return confirmDialog('Already in this playlist', msg, 'Yes, add again', 'No');
}

// "Saved in": shows which playlists (and Liked Songs) already contain the song, lets you tick/untick
// others, and applies the changes on Done.
function playlistPicker(song) {
  const st = { items: [], want: new Map(), sort: 'recent', filter: '' };
  const SORT_LABEL = { recent: 'Recents', alpha: 'A–Z', size: 'Most songs' };
  const body = h('div', { class: 'pick-body' }, spinner());
  const finish = () => closeTop();
  const tog = on => h('span', { class: 'tog' + (on ? ' on' : ''), html: on ? I.check : I.plus });
  const row = it => h('div', { class: 'row', onclick: () => { st.want.set(it.key, !st.want.get(it.key)); render(); } },
    cover(it.cover, 'md', 120, { liked: it.key === 'liked', fallback: 'playlist' }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, it.name), h('div', { class: 's' }, it.sub)), tog(st.want.get(it.key)));
  const savedBox = h('div', {}), listBox = h('div', {});
  const filter = h('input', { type: 'text', placeholder: 'Find playlist' });
  filter.addEventListener('input', () => { st.filter = filter.value; render(); });
  const sortBtn = h('button', { class: 'pillbtn dark' }, 'Sort');
  sortBtn.addEventListener('click', () => { st.sort = st.sort === 'recent' ? 'alpha' : st.sort === 'alpha' ? 'size' : 'recent'; render(); });
  const render = () => {
    const saved = st.items.filter(i => i.initial);
    put(savedBox, saved.length ? saved.map(row) : h('div', { class: 'hint' }, 'Not in any playlist yet.'));
    sortBtn.textContent = SORT_LABEL[st.sort];
    const f = st.filter.trim().toLowerCase();
    const rest = st.items.filter(i => !i.initial && (!f || i.name.toLowerCase().includes(f)));
    rest.sort(st.sort === 'alpha' ? (a, c) => a.name.localeCompare(c.name, undefined, { sensitivity: 'base' })
      : st.sort === 'size' ? (a, c) => c.count - a.count : (a, c) => c.ts - a.ts);
    put(listBox, rest.map(row));
  };
  const done = async () => {
    const changes = st.items.filter(i => !!st.want.get(i.key) !== i.initial);
    closeTop();
    if (!changes.length) return;
    whenSettled(async () => {
      const notes = [];
      for (const it of changes) {
        try {
          if (it.key === 'liked') { await nd(st.want.get(it.key) ? 'star' : 'unstar', { id: song.id }); if (st.want.get(it.key)) S.liked.add(song.id); else S.liked.delete(song.id); syncHearts(); S.homeDirty = true; notes.push(st.want.get(it.key) ? 'Liked Songs' : 'removed from Liked Songs'); continue; }
          if (st.want.get(it.key)) { await nd('updatePlaylist', { playlistId: it.key, songIdToAdd: song.id }); notes.push(it.name); }
          else { await nd('updatePlaylist', { playlistId: it.key, songIndexToRemove: it.positions }); notes.push(`removed from ${it.name}`); }
        } catch (e) { toast(`Couldn’t update “${it.name}”: ${e.message}`, 'bad'); }
      }
      bust('pl'); S.homeDirty = true;
      if (st.items.some(i => st.want.get(i.key))) S.inPl.add(song.id); else S.inPl.delete(song.id);
      syncDots();
      if (notes.length) toast(`${song.title}: ${notes.join(', ')}`, 'ok', 3500);
    });
  };
  const newPlaylist = async () => {
    const name = await promptDialog('Name your playlist', { placeholder: 'My playlist', ok: 'Create' });
    if (!name) return;
    try {
      await nd('createPlaylist', { name, songId: song.id }); bust('pl'); S.homeDirty = true;
      toast(`Created “${name}” with ${song.title}`, 'ok', 3000);
      load();
    } catch (e) { toast(`Couldn’t create: ${e.message}`, 'bad'); }
  };
  const load = async () => {
    put(body, spinner());
    bust('pl'); // always the current playlists, not a cached copy
    const lists = await getPlaylists().catch(() => []);
    const entries = await Promise.all(lists.map(p => getPlaylist(p.id).then(pl => arr(pl.entry)).catch(() => [])));
    st.items = [{ key: 'liked', name: 'Liked Songs', sub: 'Playlist', cover: null, initial: S.liked.has(song.id), count: Infinity, ts: Infinity }];
    lists.forEach((p, i) => {
      const positions = entries[i].map((e, idx) => (e.id === song.id ? idx : -1)).filter(idx => idx >= 0);
      st.items.push({ key: p.id, name: p.name, sub: plural(p.songCount || 0, 'song'), cover: p.coverArt, initial: positions.length > 0, positions,
        count: p.songCount || 0, ts: Date.parse(p.changed || p.created) || 0 });
    });
    st.items.forEach(i => st.want.set(i.key, i.initial));
    put(body, h('div', { class: 'pick-title' }, h('h2', {}, 'Saved in'), h('button', { class: 'lnk-green', onclick: newPlaylist }, 'New playlist')),
      savedBox, h('div', { class: 'pick-tools' }, h('div', { class: 'filterbox', style: 'flex:1;margin:0' }, h('span', { html: I.search, style: 'display:flex' }), filter), sortBtn), listBox, h('div', { class: 'spacer' }));
    render();
  };
  openLayer(h('div', { style: 'position:absolute;inset:0' }, h('div', { class: 'scrim', onclick: finish }),
    h('div', { class: 'pick' }, h('div', { class: 'grab' }),
      h('div', { class: 'pick-bar' }, h('button', { onclick: finish }, 'Cancel'), h('button', { onclick: done }, 'Done')), body)));
  load();
}

// =====================================================================
// Detail pages: album / podcast, playlist, liked songs, artist
// =====================================================================
const songMeta = (songs) => { const t = songs.reduce((a, s) => a + (s.duration || 0), 0); return `${plural(songs.length, 'song')} • ${fmtLong(t)}`; };

function pinBtn(item) {
  const b = h('button', { class: 'icon-btn', 'aria-label': 'Pin' });
  const paint = () => { b.innerHTML = I.pin; b.style.color = isPinned(item) ? 'var(--accent)' : 'var(--dim)'; };
  b.addEventListener('click', () => { togglePin(item); paint(); });
  paint();
  return b;
}
function dlAllBtn(songs) {
  const b = h('button', { class: 'icon-btn', 'aria-label': 'Download all' });
  const paint = () => {
    if (!b.isConnected && b.dataset.live) { bus.removeEventListener('dl', paint); return; }
    b.dataset.live = '1';
    const st = songs.map(sg => dlStatus(sg.id));
    const done = st.filter(x => x === 'done').length, busy = st.some(x => ['queued', 'running', 'waiting'].includes(x));
    b.innerHTML = done === songs.length && songs.length ? I.dlDone : busy ? ringSvg(Math.round((done / Math.max(1, songs.length)) * 100), false) : I.dl;
    b.classList.toggle('dim', !done && !busy);
  };
  b.addEventListener('click', () => {
    const st = songs.map(sg => dlStatus(sg.id));
    if (st.every(x => x === 'done')) sheet({ title: 'Downloaded', sub: plural(songs.length, 'song'), coverEl: h('div', { class: 'cover md', html: I.dl }), items: [{ icon: 'trash', label: 'Remove downloads', bad: true, run: () => songs.forEach(removeDownload) }] });
    else downloadMany(songs);
  });
  bus.addEventListener('dl', paint);
  paint();
  return b;
}
// Big Play / Shuffle buttons on album, playlist, artist and mix pages. They follow the player: "here" means what is playing
// came from this page (same context, or for pages without one, a song of this page).
const playsHere = (item, songs) => {
  const c = cur(); if (!c) return false;
  if (item) return !!P.ctx && P.ctx.type === item.type && P.ctx.id === item.id;
  return songs.some(s => s.id === c.id);
};
function liveBtn(b, paint) {
  const run = () => { if (!b.isConnected && b.dataset.live) { ['state', 'track', 'queue'].forEach(e => bus.removeEventListener(e, run)); return; } b.dataset.live = '1'; paint(); };
  ['state', 'track', 'queue'].forEach(e => bus.addEventListener(e, run));
  paint();
  return b;
}
const playBtn = (fn, item, songs = []) => {
  const b = h('button', { class: 'playbtn', 'aria-label': 'Play' });
  b.addEventListener('click', () => { if (playsHere(item, songs) && P.loaded) { haptic(); togglePlay(); } else fn(); });
  return liveBtn(b, () => { const on = playsHere(item, songs) && !audio.paused && P.loaded; b.innerHTML = on ? I.pause : I.play; b.setAttribute('aria-label', on ? 'Pause' : 'Play'); });
};
// Shuffle is a real on/off switch: on = the queue is a shuffled copy of this page; pressing again puts the queue back in order.
const shuffleBtn = (songs, item) => {
  const b = h('button', { class: 'icon-btn shuf', html: I.shuffle, 'aria-label': 'Shuffle' });
  const lit = () => P.shuffle && playsHere(item, songs);
  b.addEventListener('click', () => {
    haptic();
    if (lit()) {
      const id = cur() && cur().id, at = P.orig.findIndex(x => x.id === id);
      P.shuffle = false;
      if (at >= 0) { P.list = P.orig.slice(); P.index = at; }
      persistSession(true); bus.dispatchEvent(new Event('queue'));
    } else playQueue(shuffled(songs), 0, item, { shuffle: true, orig: songs });
  });
  return liveBtn(b, () => { b.classList.toggle('on', !!lit()); b.setAttribute('aria-pressed', lit() ? 'true' : 'false'); });
};

function hero(coverEl, title, meta) {
  return h('div', { class: 'hero' }, coverEl, h('h1', {}, title), meta && h('div', { class: 'meta' }, meta));
}

function orderAlbumSongs(al) {
  const songs = arr(al.song).filter(x => !S.deleted.has(x.id));
  if (S.podAlbums.has(al.id)) return songs.sort((a, b) => (a.track || 0) - (b.track || 0) || String(a.created).localeCompare(String(b.created)));
  return songs.sort((a, b) => (a.discNumber || 1) - (b.discNumber || 1) || (a.track || 0) - (b.track || 0));
}

function albumItem(al) {
  const pod = S.podAlbums.has(al.id);
  return { type: 'album', id: al.id, name: al.name, sub: pod ? `Podcast • ${al.artist || ''}` : `Album • ${al.artist || ''}`, cover: al.coverArt };
}

function openAlbum(id) {
  openPage(async page => {
    page.append(backBar(), spinner());
    let al;
    try { al = await getAlbum(id); } catch (e) { page.replaceChildren(backBar(), errBox(e.message)); return; }
    const pod = S.podAlbums.has(al.id);
    const songs = orderAlbumSongs(al);
    if (pod) {   // podcasts have their own screen, and one show may be several Navidrome albums
      try { const show = await podcastShow(al.id, al); renderPodcastPage(page, show.main, show.songs, show.ids); }
      catch (e) { renderPodcastPage(page, al, songs, [al.id]); }
      return;
    }
    const item = albumItem(al);
    const ctx = { ...item };
    const display = pod ? songs.slice().reverse() : songs; // podcasts: newest first
    const playFrom = song => {
      // Podcasts play forward in time: the episode after this one is the next newer one.
      const i = songs.findIndex(s => s.id === song.id);
      playQueue(songs, i, ctx);
    };
    const resumeSong = () => {
      if (!pod) return songs[0];
      const withMark = songs.filter(s => S.bookmarks.has(s.id)).sort((a, b) => String(S.bookmarks.get(b.id).changed).localeCompare(String(S.bookmarks.get(a.id).changed)));
      return withMark[0] || songs[songs.length - 1];
    };
    const list = h('div', {});
    const render = () => {
      list.replaceChildren(...display.map((s, i) => {
        const b = S.bookmarks.get(s.id);
        const done = b ? b.position / 1000 : 0;
        const left = b && s.duration ? Math.max(1, Math.round((s.duration - done) / 60)) : null;
        return songRow(s, pod
          ? { noCover: true, wrap: true, sub: left ? `${left} min left` : fmtLong(s.duration || 0), progress: b && s.duration ? done / s.duration : null, onPlay: () => playFrom(s),
              menu: { noAlbumLink: true, noArtistLink: true } }
          : { number: s.track || i + 1, onPlay: () => playFrom(s), sub: s.artist !== al.artist ? s.artist : '', menu: { noAlbumLink: true } });
      }));
    };
    const extras = h('div', {});
    page.replaceChildren(backBar(),
      hero(cover(al.coverArt, '', 500), al.name, pod ? [al.artist, al.year, songMeta(songs)].filter(Boolean).join(' • ')
        : metaLine([al.artist ? linkEl(al.artist, () => openArtist({ navId: al.artistId, name: al.artist })) : null, al.year, songMeta(songs)])),
      h('div', { class: 'actions' }, pinBtn(item), NATIVE_DL ? dlAllBtn(songs) : null,
        canDelete() ? h('button', { class: 'icon-btn', html: I.moreV, 'aria-label': 'More', onclick: () => sheet({ title: al.name, sub: 'Album', coverEl: cover(al.coverArt, 'md', 120), items: [{ icon: 'trash', label: `Delete album (${plural(songs.length, 'song')})`, bad: true, run: async () => { if (await deleteSongsFlow(songs, `“${al.name}”`)) closeTop(); } }] }) }) : null,
        h('div', { class: 'grow' }),
        !pod && shuffleBtn(songs, ctx),
        playBtn(() => { const s = resumeSong(); if (s) playFrom(s); }, ctx, songs)),
      list, extras, h('div', { class: 'spacer' }));
    render();
    if (!pod && al.artist) showMissingTracks(extras, al, songs);
  });
}

function openLiked() {
  openPage(async page => {
    const item = { type: 'liked', id: 'liked', name: 'Liked Songs', sub: 'Playlist', cover: null };
    page.append(backBar(), spinner());
    let songs;
    try { songs = (await getStarred()).sort((a, b) => String(b.starred).localeCompare(String(a.starred))); S.liked = new Set(songs.map(s => s.id)); syncHearts(); }
    catch (e) { page.replaceChildren(backBar(), errBox(e.message)); return; }
    page.replaceChildren(backBar(),
      hero(cover(null, '', 0, { liked: true }), 'Liked Songs', songs.length ? songMeta(songs) : null),
      songs.length ? h('div', { class: 'actions' }, h('div', { class: 'grow' }),
        shuffleBtn(songs, item),
        playBtn(() => playQueue(songs, 0, item), item, songs)) : h('div', { class: 'empty' }, 'Tap the heart on any song to keep it here.'),
      ...songs.map((s, i) => songRow(s, { showAlbum: true, onPlay: () => playQueue(songs, i, item) })),
      h('div', { class: 'spacer' }));
  });
}

function openPlaylist(id) {
  openPage(async page => {
    const load = async () => {
      let pl;
      try { pl = await getPlaylist(id); } catch (e) { page.replaceChildren(backBar(), errBox(e.message, load)); return; }
      const songs = arr(pl.entry);
      const item = { type: 'playlist', id: pl.id, name: pl.name, sub: `Playlist • ${plural(songs.length, 'song')}`, cover: pl.coverArt };
      put(page, backBar(),
        hero(cover(pl.coverArt, '', 500, { fallback: 'playlist' }), pl.name, songs.length ? songMeta(songs) : 'Empty playlist'),
        h('div', { class: 'actions' },
          pinBtn(item), NATIVE_DL && songs.length ? dlAllBtn(songs) : null,
          h('button', { class: 'icon-btn', html: I.addsong, 'aria-label': 'Add songs', onclick: () => startAddTo(pl) }),
          h('button', { class: 'icon-btn', html: I.more, 'aria-label': 'More', onclick: () => playlistSheet(pl, item, load) }),
          h('div', { class: 'grow' }),
          songs.length ? [shuffleBtn(songs, item), playBtn(() => playQueue(songs, 0, item), item, songs)] : null),
        songs.length ? songs.map((s, i) => songRow(s, { onPlay: () => playQueue(songs, i, item), menu: { playlistId: pl.id, index: i, reload: load } }))
          : h('div', { class: 'empty' }, h('div', {}, 'Nothing here yet.'), h('button', { class: 'pillbtn', onclick: () => startAddTo(pl) }, 'Add songs')),
        h('div', { class: 'spacer' }));
    };
    page.append(backBar(), spinner());
    load();
  });
}

function playlistSheet(pl, item, reload) {
  sheet({
    title: pl.name, sub: 'Playlist', coverEl: cover(pl.coverArt, 'md', 120, { fallback: 'playlist' }),
    items: [
      { icon: 'addsong', label: 'Add songs', run: () => startAddTo(pl) },
      { icon: 'edit', label: 'Rename', run: async () => {
        const name = await promptDialog('Rename playlist', { value: pl.name });
        if (!name || name === pl.name) return;
        try { await nd('updatePlaylist', { playlistId: pl.id, name }); bust('pl'); S.pins = S.pins.map(p => keyOf(p) === keyOf(item) ? { ...p, name } : p); saveState(); reload(); }
        catch (e) { toast(`Couldn’t rename: ${e.message}`, 'bad'); }
      } },
      { icon: 'trash', label: 'Delete playlist', bad: true, run: async () => {
        if (!await confirmDialog('Delete playlist?', `“${pl.name}” will be deleted. The songs stay in your library.`, 'Delete')) return;
        try {
          await nd('deletePlaylist', { id: pl.id });
          bust('pl'); S.pins = S.pins.filter(p => keyOf(p) !== keyOf(item)); S.recents = S.recents.filter(p => keyOf(p) !== keyOf(item)); S.homeDirty = true; saveState();
          closeTop();
        } catch (e) { toast(`Couldn’t delete: ${e.message}`, 'bad'); }
      } },
    ],
  });
}

// ----- Artist: your library plus online info -----
const linkEl = (text, fn) => h('button', { class: 'lnk', onclick: e => { e.stopPropagation(); fn(); } }, text);
function metaLine(parts) {
  const out = [];
  parts.filter(Boolean).forEach((p, i) => { if (i) out.push(' • '); out.push(p); });
  return out;
}
const checkMark = () => h('span', { class: 'added', html: I.check, style: 'width:22px;height:22px;display:flex;margin:0 10px', title: 'In your library' });

// A "+" that adds the song/album through the normal add flow and then turns into a check.
function addBtn(payload, label) {
  const b = h('button', { class: 'icon-btn', html: I.plus, 'aria-label': 'Add to library' });
  b.addEventListener('click', e => {
    e.stopPropagation();
    if (b.disabled) return;
    b.disabled = true; b.innerHTML = I.more;
    quickAdd(payload, label, 'none', null, null, ok => { b.innerHTML = ok ? I.check : I.plus; b.disabled = !!ok; b.style.color = ok ? 'var(--accent)' : ''; });
  });
  return b;
}

function onlineSongRow(p, n) {
  return h('div', { class: 'row', onclick: () => onlineSongSheet(p) },
    n != null ? h('div', { class: 'num' }, n) : null,
    cover(p.artwork ? 'x' : null, 'sm', 100, { url: p.artwork }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, p.title), h('div', { class: 's' }, [p.artist, p.album].filter(Boolean).join(' • '))),
    p.in_library ? checkMark() : addBtn({ song: songPayload(p) }, p.title));
}
function onlineSongSheet(p, nowPlaying) {
  sheet({
    title: p.title, sub: [p.artist, p.album].filter(Boolean).join(' • '), coverEl: cover(p.artwork ? 'x' : null, 'md', 100, { url: p.artwork }),
    items: [
      !nowPlaying && p.video_id && { icon: 'play', label: p.in_library ? 'Play' : 'Play (without adding)', run: () => { haptic(); playQueue([onlineSong(p)], 0, null); } },
      nowPlaying && { icon: 'moon', label: sleepOn() ? `Sleep timer (${sleepLabel()})` : 'Sleep timer', run: sleepSheet },
      !p.in_library && { icon: 'plus', label: 'Add to library', run: () => (nowPlaying && cur() && cur().online ? addOnline(cur()) : quickAdd({ song: songPayload(p) }, p.title)) },
      !p.in_library && { icon: 'playlist', label: 'Add to playlist…', run: () => pickPlaylistTarget((mode, name, id) => quickAdd({ song: songPayload(p) }, p.title, mode, name, id)) },
      { icon: 'note', label: 'About this song', run: () => aboutSong(p) },
      p.video_id && { icon: 'radio', label: 'Start radio', run: () => startRadio({ video_id: p.video_id }, p.title, onlineSong(p)) },
      p.album && p.artist && { icon: 'disc', label: 'Go to album', run: () => openOnlineAlbum(p.ytm_album_id ? { browseId: p.ytm_album_id, artist: p.artist, title: p.album } : { artist: p.artist, title: p.album }) },
      p.artist && { icon: 'user', label: 'Go to artist', run: () => openArtist({ name: p.artist }) },
    ],
  });
}

async function findLocalArtist(name) {
  const r = await nd('search3', { query: name, artistCount: 5, albumCount: 0, songCount: 0 });
  let hit = arr(r.searchResult3?.artist).find(a => normT(a.name) === normT(name));
  if (!hit && mainArtist(name) !== name) {
    const r2 = await nd('search3', { query: mainArtist(name), artistCount: 5, albumCount: 0, songCount: 0 });
    hit = arr(r2.searchResult3?.artist).find(a => normT(a.name) === normT(mainArtist(name)));
  }
  return hit ? getArtist(hit.id) : null;
}

// arg: a Navidrome artist id, or { navId?, name?, browseId? }
function openArtist(arg) {
  const ref = typeof arg === 'string' ? { navId: arg } : (arg || {});
  openPage(page => {
    put(page, backBar(), spinner());
    const load = async () => {
      let local = null;
      try { local = ref.navId ? await getArtist(ref.navId) : (ref.name ? await findLocalArtist(ref.name) : null); } catch (e) { /* the library side is optional */ }
      const name = (local && local.name) || ref.name || '';
      const localAlbums = local ? podCanonical(arr(local.album)).slice().sort((a, b) => (b.year || 0) - (a.year || 0)) : [];
      const podcastish = localAlbums.length > 0 && localAlbums.every(a => S.podAlbums.has(a.id));
      const onlineP = podcastish ? Promise.resolve({ skipped: true })
        : (name || ref.browseId) ? discover('artist', ref.browseId ? { browse_id: ref.browseId } : { name }).catch(e => ({ error: e.message }))
        : Promise.resolve({ error: 'No artist given.' });
      const songsP = Promise.all(localAlbums.slice(0, 40).map(a => getAlbum(a.id).catch(() => null))).then(l => l.filter(Boolean).flatMap(a => orderAlbumSongs(a)));
      const [online, localSongs] = await Promise.all([onlineP, songsP]);
      renderArtistPage(page, { local, name, online, localAlbums, localSongs, retry: () => { put(page, backBar(), spinner()); load(); } });
    };
    load();
  });
}

function renderArtistPage(page, { local, name, online, localAlbums, localSongs, retry }) {
  const ok = online && !online.error && !online.skipped;
  if (!ok && !local) { put(page, backBar(), errBox(`Couldn’t load ${name || 'this artist'}: ${online && online.error ? online.error : 'not found'}`, retry)); return; }
  const item = local ? { type: 'artist', id: local.id, name: local.name, sub: 'Artist', cover: local.coverArt } : null;
  const heroImg = ok && online.banner ? online.banner : (local && local.coverArt ? coverUrl(local.coverArt, 700) : '');

  // Popular: songs you own play; the rest can be added.
  const popular = ok ? online.popular : [];
  const matched = popular.map(p => localSongs.find(sg => normT(sg.title) === normT(p.title)) || null);
  const playable = matched.filter(Boolean);
  let showAll = false;
  const popBox = h('div', {});
  const drawPop = () => put(popBox,
    popular.slice(0, showAll ? 10 : 5).map((p, i) => matched[i]
      ? songRow(matched[i], { number: i + 1, withCover: true, showAlbum: true, onPlay: () => playQueue(playable, playable.indexOf(matched[i]), item) })
      : onlineSongRow(p, i + 1)),
    popular.length > 5 ? h('button', { class: 'pillbtn dark', style: 'margin:10px 16px', onclick: () => { showAll = !showAll; drawPop(); } }, showAll ? 'Show less' : 'Show more') : null);
  drawPop();

  // Discography: online releases flagged with what you own; your own extras after.
  const pairs = {};
  const used = new Set();
  const pair = c => { const loc = localAlbums.find(a => normT(a.name) === normT(c.title)); if (loc) used.add(loc.id); return { c, loc }; };
  if (ok) { pairs.albums = online.albums.map(pair); pairs.singles = online.singles.map(pair); }
  const discRow = ({ c, loc }) => h('div', { class: 'row', onclick: () => (loc ? openAlbum(loc.id) : openOnlineAlbum({ browseId: c.browse_id, artist: c.artist, title: c.title })) },
    cover(c.artwork ? 'x' : null, 'md', 120, { url: c.artwork }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, c.title), h('div', { class: 's' }, [c.year, c.type].filter(Boolean).join(' • '))),
    loc || c.in_library ? checkMark() : addBtn({ album: albumPayload({ title: c.title, artist: c.artist, ytm_browse_id: c.browse_id }) }, c.title));
  let tab = 'albums', expanded = false;
  const discBox = h('div', {}), tabs = h('div', { class: 'chips', style: 'padding:0 16px 10px' });
  const drawDisc = () => {
    const list = pairs[tab] || [];
    put(tabs, [['albums', 'Albums'], ['singles', 'Singles & EPs']].filter(([k]) => (pairs[k] || []).length).map(([k, label]) =>
      h('button', { class: 'chip' + (tab === k ? ' on' : ''), onclick: () => { tab = k; expanded = false; drawDisc(); } }, label)));
    put(discBox, list.slice(0, expanded ? list.length : 6).map(discRow),
      list.length > 6 ? h('button', { class: 'pillbtn dark', style: 'margin:10px 16px', onclick: () => { expanded = !expanded; drawDisc(); } }, expanded ? 'Show less' : `Show all ${list.length}`) : null);
  };
  if (ok) { if (!pairs.albums.length && pairs.singles.length) tab = 'singles'; drawDisc(); }
  const mine = ok ? localAlbums.filter(a => !used.has(a.id)) : localAlbums;

  const bio = ok && online.bio ? h('div', { class: 'bio clamp' }, online.bio) : null;
  if (bio) bio.addEventListener('click', () => bio.classList.toggle('clamp'));

  put(page, backBar(),
    h('div', { class: 'artist-hero', style: heroImg ? `background-image:url("${heroImg}")` : '' },
      h('div', { class: 'ah-name' }, name),
      ok && online.listeners ? h('div', { class: 'ah-sub' }, `${online.listeners} monthly listeners`) : null),
    h('div', { class: 'actions' }, item ? pinBtn(item) : null, h('button', { class: 'icon-btn', html: I.radio, 'aria-label': 'Artist radio', onclick: () => startRadio({ artist: name }, name) }), h('div', { class: 'grow' }),
      localSongs.length ? [shuffleBtn(localSongs, item), playBtn(() => playQueue(localSongs, 0, item), item, localSongs)] : null),
    online && online.error ? h('div', { class: 'hint' }, `Online info isn’t available right now (${online.error}). `, h('button', { class: 'lnk', onclick: retry }, 'Retry')) : null,
    popular.length ? [h('h2', { class: 'sec' }, 'Popular'), popBox] : null,
    ok && (pairs.albums.length || pairs.singles.length) ? [h('h2', { class: 'sec' }, 'Discography'), tabs, discBox] : null,
    mine.length ? [h('h2', { class: 'sec' }, ok ? 'More in your library' : 'Albums'), mine.map(a => albumRow(a, { noArtist: true }))] : null,
    ok && online.related.length ? [h('h2', { class: 'sec' }, 'Fans also like'),
      h('div', { class: 'hrow' }, online.related.map(r => h('button', { class: 'card round', onclick: () => openArtist({ name: r.name, browseId: r.browse_id }) },
        cover(r.thumb ? 'x' : null, '', 300, { round: true, fallback: 'user', url: r.thumb }), h('div', { class: 'nm' }, r.name))))] : null,
    bio ? [h('h2', { class: 'sec' }, 'About'), h('div', { class: 'about' },
      online.thumb ? h('div', { class: 'about-img', style: `background-image:url("${online.thumb}")` }) : null,
      h('div', { class: 'about-body' }, online.listeners ? h('b', {}, `${online.listeners} monthly listeners`) : null, bio,
        online.bio_source ? h('div', { class: 'src' }, `From ${online.bio_source}`) : null))] : null,
    h('div', { class: 'spacer' }));
}

// ----- Album that isn't (fully) in the library -----
function openOnlineAlbum(ref) {
  openPage(page => {
    put(page, backBar(), spinner());
    const load = async () => {
      let d;
      try { d = await discover('album', ref.browseId ? { browse_id: ref.browseId } : { artist: ref.artist, title: ref.title }); }
      catch (e) { put(page, backBar(), errBox(e.message, () => { put(page, backBar(), spinner()); load(); })); return; }
      const missing = d.tracks.filter(t => !t.in_library);
      const addAll = h('button', { class: 'pillbtn' }, d.tracks.length === missing.length ? 'Add album to library' : `Add ${missing.length} missing song${missing.length === 1 ? '' : 's'}`);
      addAll.addEventListener('click', () => {
        addAll.disabled = true; addAll.textContent = 'Adding…';
        quickAdd({ album: albumPayload({ title: d.title, artist: d.artist, ytm_browse_id: d.browse_id }) }, d.title, 'none', null, null, ok => { addAll.textContent = ok ? '✓ Added' : 'Try again'; addAll.disabled = !!ok; });
      });
      put(page, backBar(),
        hero(cover(d.artwork ? 'x' : null, '', 600, { url: d.artwork }), d.title,
          metaLine([d.type, d.artist ? linkEl(d.artist, () => openArtist({ name: d.artist, browseId: d.artist_id })) : null, d.year, plural(d.tracks.length, 'song'), d.duration ? fmtLong(d.duration) : null])),
        h('div', { class: 'actions' }, h('div', { class: 'grow' }), missing.length ? addAll : h('span', { class: 'hint' }, 'All songs are in your library')),
        d.tracks.map(t => onlineSongRow(t, t.track_number)),
        h('div', { class: 'spacer' }));
    };
    load();
  });
}

// On a library album page: the official songs you don't have yet.
async function showMissingTracks(box, al, songs) {
  try {
    const d = await discover('album', { artist: al.artist, title: al.name });
    if (!d.tracks.length || d.tracks.length > 80) return;
    const have = new Set(songs.map(sg => normT(sg.title)));
    const missing = d.tracks.filter(t => !have.has(normT(t.title)));
    if (!missing.length) return;
    const addAll = h('button', { class: 'pillbtn dark' }, `Add all ${missing.length}`);
    addAll.addEventListener('click', () => {
      addAll.disabled = true; addAll.textContent = 'Adding…';
      quickAdd({ album: albumPayload({ title: d.title, artist: d.artist, ytm_browse_id: d.browse_id }) }, al.name, 'none', null, null, ok => { addAll.textContent = ok ? '✓ Added' : 'Try again'; addAll.disabled = !!ok; });
    });
    put(box, h('div', { class: 'rowsec' }, 'Not in your library', h('small', {}, `${missing.length} of ${d.tracks.length} songs on the official release`)),
      h('div', { class: 'pad', style: 'margin:4px 0 8px' }, addAll), missing.map(t => onlineSongRow(t, t.track_number)));
  } catch (e) { /* online info is a bonus */ }
}

// ----- About this song (Wikipedia, only when the song has an article) -----
function aboutSong(song) {
  dialog(box => {
    const body = h('div', {}, spinner());
    put(box, h('h3', {}, song.title), h('div', { class: 'note' }, [song.artist, song.album, song.year].filter(Boolean).join(' • ')), body,
      h('div', { class: 'btns' }, h('button', { class: 'pillbtn', onclick: () => closeTop() }, 'Close')));
    const facts = [song.duration ? `Length ${fmtTime(song.duration)}` : null, song.suffix ? `${String(song.suffix).toUpperCase()}${song.bitRate ? ' ' + song.bitRate + ' kbps' : ''}` : null].filter(Boolean).join(' • ');
    discover('song', { artist: song.artist || '', title: song.title }).then(r => {
      put(body, r.blurb
        ? [h('p', { class: 'note', style: 'color:#ddd;line-height:1.45' }, r.blurb.text),
           h('a', { href: r.blurb.url, target: '_blank', rel: 'noopener', style: 'color:var(--accent);font-size:13px' }, `From ${r.blurb.source}`)]
        : h('p', { class: 'note' }, 'There’s no write-up for this song.'),
        facts ? h('p', { class: 'note', style: 'margin-top:10px' }, facts) : null);
    }).catch(() => put(body, h('p', { class: 'note' }, 'Couldn’t load info right now.')));
  });
}

function albumRow(a, o = {}) {
  const pod = S.podAlbums.has(a.id);
  const row = h('button', { class: 'row', onclick: () => openAlbum(a.id) },
    cover(a.coverArt, 'md', 120),
    h('div', { class: 'txt' }, h('div', { class: 't' }, a.name), h('div', { class: 's' }, [pod ? 'Podcast' : 'Album', o.noArtist ? null : a.artist, a.year].filter(Boolean).join(' • '))));
  onLongPress(row, () => itemSheet(albumItem(a)));
  return row;
}

// ----- Browse (tiles on the Search tab) -----
function openBrowse(title, loader, { pageSize = 500 } = {}) {
  openPage(async page => {
    const list = h('div', {});
    const more = h('div', { class: 'pad' });
    page.append(backBar(), h('h2', { class: 'sec', style: 'margin-top:4px' }, title), list, more);
    let offset = 0, busy = false;
    const loadMore = async () => {
      if (busy) return;
      busy = true;
      more.replaceChildren(spinner());
      try {
        const items = await loader(offset);
        items.forEach(a => list.append(a.type === 'artist' ? artistRow(a) : albumRow(a)));
        offset += items.length;
        more.replaceChildren(items.length >= pageSize ? h('button', { class: 'pillbtn dark', onclick: loadMore }, 'Show more') : (list.children.length ? '' : h('div', { class: 'empty' }, 'Nothing here yet.')));
      } catch (e) { more.replaceChildren(errBox(e.message, loadMore)); }
      busy = false;
    };
    loadMore();
  });
}
function artistRow(a) {
  const row = h('button', { class: 'row', onclick: () => openArtist(a.id) },
    cover(a.coverArt, 'md', 120, { round: true, fallback: 'user' }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, a.name), h('div', { class: 's' }, 'Artist')));
  onLongPress(row, () => itemSheet({ type: 'artist', id: a.id, name: a.name, sub: 'Artist', cover: a.coverArt }));
  return row;
}

// ----- Generic "this library item" helpers -----
function openItem(it) {
  if (it.type === 'liked') openLiked();
  else if (it.type === 'playlist') openPlaylist(it.id);
  else if (it.type === 'artist') openArtist(it.id);
  else openAlbum(it.id);
}
function itemSheet(it) {
  const pinned = isPinned(it);
  sheet({
    title: it.name, sub: (it.sub || '').split(' • ')[0], coverEl: cover(it.cover, 'md', 120, { round: it.type === 'artist', liked: it.type === 'liked', fallback: it.type === 'artist' ? 'user' : 'note' }),
    items: [
      it.type !== 'liked' && { icon: 'pin', label: pinned ? 'Unpin' : 'Pin to top', run: () => { togglePin(it); renderLibrary(); } },
      { icon: 'play', label: 'Open', run: () => openItem(it) },
    ],
  });
}

// =====================================================================
// Podcasts: episode list with dates, progress, downloads (like Spotify)
// =====================================================================
const fmtEp = sec => { sec = Math.round(sec || 0); const hh = Math.floor(sec / 3600), mm = Math.round((sec % 3600) / 60); return hh ? `${hh}h ${mm}m` : `${Math.max(1, mm)}min`; };
function fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), o = { day: '2-digit', month: 'short' };
  if (d.getFullYear() !== new Date().getFullYear()) o.year = 'numeric';
  return d.toLocaleDateString('en-GB', o);
}
const epTs = sg => (S.epDates.get(sg.id) || {}).ts || 0;
const byDateAsc = (a, b) => epTs(a) - epTs(b) || (a.track || 0) - (b.track || 0);

function epInfo(song) {
  const b = S.bookmarks.get(song.id), dur = song.duration || 0;
  let state = 'new', left = dur, frac = 0;
  if (S.finished.has(song.id)) state = 'played';
  else if (b && b.position > 5000 && dur) { state = 'progress'; left = Math.max(60, dur - b.position / 1000); frac = Math.min(1, b.position / 1000 / dur); }
  return { state, left, frac, date: fmtDate(epTs(song)) };
}

const descCache = new Map();
function descText(song) {
  if (!song.comment) return '';
  if (!descCache.has(song.id)) {
    const html = song.comment.replace(/<br\s*\/?>/gi, '\n').replace(/<\/p>/gi, '\n\n');
    const t = (new DOMParser().parseFromString(html, 'text/html').body.textContent || '').replace(/[ \t]+/g, ' ').replace(/\n[ \t]+/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
    descCache.set(song.id, t);
  }
  return descCache.get(song.id);
}
const loadEpDates = albumId => cached('epd:' + albumId, 300000, async () => {
  const r = await fetch(api(`/api/podcast/dates?album=${encodeURIComponent(albumId)}`));
  if (r.ok) Object.entries((await r.json()).dates || {}).forEach(([id, d]) => S.epDates.set(id, d));
  return true;
});

function playEpisode(song, songs, ctx) {
  // Podcasts play forward in time: after this one comes the next newer episode.
  const ordered = songs.slice().sort(byDateAsc);
  playQueue(ordered, ordered.findIndex(x => x.id === song.id), ctx);
}
function togglePlayEpisode(song, songs, ctx) { if (cur() && cur().id === song.id) togglePlay(); else playEpisode(song, songs, ctx); }
const isPlayingNow = song => !!cur() && cur().id === song.id && !audio.paused && P.loaded;

// ----- download button (Android app only) -----
const ringSvg = (pct, waiting) => `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="none" stroke="#555" stroke-width="2"/><circle cx="12" cy="12" r="10" fill="none" stroke="#1ed760" stroke-width="2" stroke-linecap="round" ${waiting ? 'stroke-dasharray="3 4"' : `stroke-dasharray="${(pct * 0.6283).toFixed(1)} 62.83"`} transform="rotate(-90 12 12)"/><rect x="9.5" y="9.5" width="5" height="5" rx="1" fill="currentColor"/></svg>`;
function paintDl(b) {
  const st = S.dl.get(b.dataset.sid), status = st ? st.status : 'none';
  b.innerHTML = status === 'done' ? I.dlDone : ['queued', 'running', 'waiting'].includes(status) ? ringSvg(st.pct || 0, status === 'waiting') : I.dl;
  b.classList.toggle('dim', status === 'none' || status === 'failed');
  b.title = status === 'done' ? 'Downloaded' : status === 'waiting' ? 'Waiting for Wi-Fi' : status === 'running' ? `Downloading ${st.pct || 0}%` : 'Download';
}
bus.addEventListener('dl', () => document.querySelectorAll('.dlbtn[data-sid]').forEach(paintDl));
function dlButton(song) {
  const b = h('button', { class: 'icon-btn dlbtn', 'data-sid': song.id, 'aria-label': 'Download' });
  paintDl(b);
  b.addEventListener('click', e => { e.stopPropagation(); dlTap(song); });
  return b;
}
function dlTap(song) {
  const status = dlStatus(song.id);
  if (status === 'done') {
    sheet({ title: song.title, sub: 'Downloaded to this phone', coverEl: cover(song.coverArt, 'md', 120), items: [{ icon: 'trash', label: 'Remove download', bad: true, run: () => { removeDownload(song); toast('Download removed', 'ok', 2000); } }] });
  } else if (['queued', 'running', 'waiting'].includes(status)) {
    removeDownload(song); toast('Download cancelled', '', 2000);
  } else {
    startDownload(song);
    toast(`Downloading “${song.title}”${NATIVE_DL && !TRNative.dlCellular() ? ' (on Wi-Fi)' : ''}`, 'ok', 2500);
  }
}

// ----- one episode card -----
function episodeCard(song, al, songs, ctx) {
  const info = epInfo(song), playing = !!cur() && cur().id === song.id, desc = descText(song).split('\n')[0];
  const meta = h('div', { class: 'ep-meta' },
    info.date ? h('span', {}, info.date) : null, info.date ? h('span', { class: 'dot' }, '•') : null,
    info.state === 'played' ? h('span', { class: 'played' }, h('span', { class: 'ck', html: I.check }), 'Played')
      : info.state === 'progress' ? h('span', {}, `${fmtEp(info.left)} left`) : h('span', {}, fmtEp(song.duration)),
    info.state === 'progress' ? h('div', { class: 'ep-prog' }, h('div', { style: `width:${Math.round(info.frac * 100)}%` })) : null);
  const epEl = h('div', { class: 'ep' + (playing ? ' playing' : ''), 'data-sid': song.id, onclick: () => openEpisode(song, al, songs, ctx) },
    h('div', { class: 'ep-top' }, cover(song.coverArt, 'md', 150), h('div', { class: 'ep-title' }, song.title)),
    desc ? h('div', { class: 'ep-desc' }, desc) : null, meta,
    h('div', { class: 'ep-actions', onclick: e => e.stopPropagation() },
      h('button', { class: 'icon-btn dim', html: I.moreV, 'aria-label': 'More', onclick: () => episodeSheet(song, al, songs, ctx) }),
      NATIVE_DL ? dlButton(song) : null,
      h('button', { class: 'icon-btn dim', html: I.plus, 'aria-label': 'Add to playlist', onclick: () => playlistPicker(song) }),
      h('button', { class: 'ep-play', html: isPlayingNow(song) ? I.pause : I.play, 'aria-label': 'Play', onclick: () => togglePlayEpisode(song, songs, ctx) })));
  swipeToQueue(epEl, song);
  return epEl;
}

function episodeSheet(song, al, songs, ctx) {
  const info = epInfo(song), st = dlStatus(song.id);
  sheet({
    title: song.title, sub: `${al.name} • By ${al.artist || ''}`, coverEl: cover(song.coverArt, 'md', 120),
    items: [
      { icon: 'playlist', label: 'Add to playlist', run: () => playlistPicker(song) },
      NATIVE_DL && (st === 'none' || st === 'failed') && { icon: 'dl', label: 'Download', run: () => { startDownload(song); toast(`Downloading “${song.title}”`, 'ok', 2500); } },
      NATIVE_DL && st === 'done' && { icon: 'trash', label: 'Remove download', run: () => { removeDownload(song); toast('Download removed', 'ok', 2000); } },
      NATIVE_DL && ['queued', 'running', 'waiting'].includes(st) && { icon: 'x', label: 'Cancel download', run: () => removeDownload(song) },
      info.state !== 'played' && { icon: 'check', label: 'Mark as finished', run: () => { const sn = snapOf([song]); markFinished(song); autoRemoveDownload(song); toastUndo('Marked as played', () => restoreSnap(sn)); } },
      info.state !== 'new' && { icon: 'x', label: 'Mark as not started', run: () => { const sn = snapOf([song]); markNotStarted(song); toastUndo('Marked as not started', () => restoreSnap(sn)); } },
      { icon: 'queue', label: 'Add to Queue', run: () => enqueue(song, false) },
      { icon: 'list', label: 'Go to Queue', run: () => { if (cur()) openQueue(); else toast('The queue is empty', '', 2000); } },
      { icon: 'mic', label: 'Go to episode', run: () => openEpisode(song, al, songs, ctx) },
      canDelete() && S.abs && { icon: 'trash', label: 'Delete episode', bad: true, run: async () => { if (await deleteEpisodeFlow(song)) { const i = songs.indexOf(song); if (i >= 0) songs.splice(i, 1); bus.dispatchEvent(new Event('progress')); } } },
    ],
  });
}

async function deleteShowFlow(al, songs) {
  if (!await confirmDialog(`Delete “${al.name}”?`, `All ${plural(songs.length, 'episode')} are removed from Audiobookshelf and their files are deleted from Titan. It can’t be undone.`, 'Delete', 'Cancel')) return false;
  const t = toast('Deleting…', '', 0);
  try {
    const r = await fetch(api('/api/podcast/delete-show'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ songs: songs.map(x => x.id) }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
    afterDelete(songs.map(x => x.id)); songs.forEach(x => { S.bookmarks.delete(x.id); S.finished.delete(x.id); });
    S.homeDirty = true;
    setToast(t, 'Podcast deleted.', 'ok', 4000);
    return true;
  } catch (e) { setToast(t, `Couldn’t delete: ${e.message}`, 'bad', 7000); return false; }
}

// ----- the episode's own page -----
function openEpisode(song, al, songs, ctx) {
  openPage(page => {
    const draw = () => {
      const info = epInfo(song), desc = descText(song);
      put(page, backBar(),
        h('div', { class: 'hero' }, cover(song.coverArt, '', 500), h('h1', {}, song.title),
          h('div', { class: 'meta' }, linkEl(al.name, () => openAlbum(al.id))),
          h('div', { class: 'meta', style: 'margin-top:4px' }, [info.date, info.state === 'played' ? 'Played' : info.state === 'progress' ? `${fmtEp(info.left)} left` : fmtEp(song.duration)].filter(Boolean).join(' • '))),
        info.state === 'progress' ? h('div', { class: 'ep-prog wide' }, h('div', { style: `width:${Math.round(info.frac * 100)}%` })) : null,
        h('div', { class: 'actions' }, NATIVE_DL ? dlButton(song) : null,
          h('button', { class: 'icon-btn', html: I.plus, 'aria-label': 'Add to playlist', onclick: () => playlistPicker(song) }),
          h('button', { class: 'icon-btn', html: I.moreV, 'aria-label': 'More', onclick: () => episodeSheet(song, al, songs, ctx) }),
          h('div', { class: 'grow' }),
          h('button', { class: 'playbtn', html: isPlayingNow(song) ? I.pause : I.play, 'aria-label': 'Play', onclick: () => togglePlayEpisode(song, songs, ctx) })),
        desc ? h('div', { class: 'ep-full' }, desc.split('\n\n').map(par => h('p', {}, par))) : null,
        h('div', { class: 'spacer' }));
    };
    draw();
    let t = null;
    const again = () => { if (!page.isConnected) { ['progress', 'state', 'track', 'dl'].forEach(e => bus.removeEventListener(e, again)); return; } clearTimeout(t); t = setTimeout(draw, 200); };
    ['progress', 'state', 'track', 'dl'].forEach(e => bus.addEventListener(e, again));
  });
}

// ----- the podcast screen: Episodes / About / More like this -----
const EP_FILTERS = { all: 'All episodes', unplayed: 'Unplayed', progress: 'In progress', downloaded: 'Downloaded' };
const EP_SORTS = { newest: 'Newest', oldest: 'Oldest' };

function renderPodcastPage(page, al, songs, ids = [al.id]) {
  const item = albumItem(al), ctx = { ...item };
  const view = Object.assign({ filter: 'all', sort: 'newest' }, lsGet('tr.podview', {}));
  let tab = 'episodes';
  const listBox = h('div', {}), body = h('div', {}), tabsEl = h('div', { class: 'pod-tabs' });
  const filterLabel = h('b', {}), filterBtn = h('button', { class: 'filter-row' }, h('span', { html: I.sliders }), filterLabel);

  const passes = sg => {
    const i = epInfo(sg);
    return view.filter === 'all' || (view.filter === 'unplayed' && i.state === 'new') || (view.filter === 'progress' && i.state === 'progress')
      || (view.filter === 'downloaded' && dlStatus(sg.id) === 'done');
  };
  const drawEpisodes = () => {
    filterLabel.textContent = `${EP_FILTERS[view.filter]} • ${EP_SORTS[view.sort]}`;
    const list = songs.filter(passes).sort((a, b) => (view.sort === 'newest' ? byDateAsc(b, a) : byDateAsc(a, b)));
    put(listBox, list.length ? list.map(sg => episodeCard(sg, al, songs, ctx)) : h('div', { class: 'empty' }, view.filter === 'all' ? 'No episodes.' : 'No episodes match this filter.'));
  };
  filterBtn.addEventListener('click', () => sheet({
    title: 'Episodes', coverEl: h('div', { class: 'cover md', html: I.sliders }),
    items: [{ header: 'Show' }, ...Object.entries(EP_FILTERS).filter(([k]) => k !== 'downloaded' || NATIVE_DL).map(([k, label]) => ({ icon: view.filter === k ? 'check' : null, label, run: () => { view.filter = k; lsSet('tr.podview', view); drawEpisodes(); } })),
      { header: 'Sort by' }, ...Object.entries(EP_SORTS).map(([k, label]) => ({ icon: view.sort === k ? 'check' : null, label, run: () => { view.sort = k; lsSet('tr.podview', view); drawEpisodes(); } }))],
  }));

  const drawTab = () => {
    put(tabsEl, [['episodes', 'Episodes'], ['about', 'About'], ['more', 'More like this']].map(([k, label]) =>
      h('button', { class: 'pod-tab' + (tab === k ? ' on' : ''), onclick: () => { tab = k; drawTab(); } }, label)));
    if (tab === 'episodes') { put(body, filterBtn, listBox); drawEpisodes(); }
    else if (tab === 'about') renderPodAbout(body, al, songs);
    else renderPodMore(body, al);
  };
  const menu = () => sheet({
    title: al.name, sub: 'Podcast', coverEl: cover(al.coverArt, 'md', 120),
    items: [
      { icon: 'pin', label: isPinned(item) ? 'Unpin' : 'Pin to top', run: () => { togglePin(item); } },
      NATIVE_DL && { icon: 'dl', label: 'Download all unplayed', run: () => downloadMany(songs.filter(sg => epInfo(sg).state === 'new').sort((a, b) => byDateAsc(b, a)).slice(0, 25)) },
      NATIVE_DL && { icon: autoDlOn(al.id) ? 'check' : 'dl', label: autoDlOn(al.id) ? 'Auto-download: on (tap to turn off)' : 'Auto-download new episodes', run: () => { setAutoDl(al.id, !autoDlOn(al.id)); toast(autoDlOn(al.id) ? 'New episodes will download automatically' : 'Auto-download off', 'ok', 2500); } },
      NATIVE_DL && { icon: 'trash', label: 'Remove downloads for this podcast', bad: true, run: () => { songs.forEach(sg => { if (dlStatus(sg.id) !== 'none') removeDownload(sg); }); toast('Downloads removed', 'ok', 2000); } },
      { icon: 'check', label: 'Mark all as played', run: () => { const sn = snapOf(songs); markMany(songs, true); songs.forEach(autoRemoveDownload); toastUndo(`Marked ${plural(songs.length, 'episode')} as played`, () => restoreSnap(sn)); } },
      { icon: 'x', label: 'Mark all as not started', run: () => { const sn = snapOf(songs); markMany(songs, false); toastUndo('Marked all as not started', () => restoreSnap(sn)); } },
      canDelete() && S.abs && { icon: 'trash', label: 'Delete podcast', bad: true, run: async () => { if (await deleteShowFlow(al, songs)) closeTop(); } },
    ],
  });

  put(page, h('div', { class: 'pod-bar' },
    h('button', { class: 'icon-btn', html: I.back, 'aria-label': 'Back', onclick: () => closeTop() }),
    h('div', { class: 'pod-title' }, al.name),
    h('button', { class: 'icon-btn', html: I.moreV, 'aria-label': 'More', onclick: menu })),
    tabsEl, body, h('div', { class: 'spacer' }));
  drawTab();
  Promise.all(ids.map(loadEpDates)).then(() => { if (tab === 'episodes') drawEpisodes(); }).catch(() => {});
  absPull().then(() => { if (tab === 'episodes') drawEpisodes(); });

  let t = null;
  const again = () => {
    if (!page.isConnected) { ['progress', 'state', 'track', 'dl'].forEach(e => bus.removeEventListener(e, again)); return; }
    clearTimeout(t); t = setTimeout(() => { if (tab === 'episodes') drawEpisodes(); }, 250);
  };
  ['progress', 'state', 'track', 'dl'].forEach(e => bus.addEventListener(e, again));
}

async function renderPodAbout(box, al, songs) {
  put(box, spinner());
  let a = {};
  try {
    const r = await fetch(api(`/api/podcast/about?album=${encodeURIComponent(al.id)}`));
    if (r.ok) a = await r.json();
  } catch (e) { /* show what we know locally */ }
  const total = songs.reduce((x, sg) => x + (sg.duration || 0), 0);
  const tss = songs.map(epTs).filter(Boolean).sort((x, y) => x - y), firstTs = tss[0] || a.first_ts, lastTs = tss[tss.length - 1] || a.latest_ts;
  const desc = a.description || '';
  const more = h('div', { class: 'bio clamp' }, desc);
  more.addEventListener('click', () => more.classList.toggle('clamp'));
  put(box,
    h('div', { class: 'about-head' }, cover(al.coverArt, 'md', 150), h('div', { style: 'min-width:0' }, h('div', { class: 'ah-t' }, al.name), h('div', { class: 'dim' }, `By ${a.publisher || al.artist || ''}`))),
    desc ? [h('h2', { class: 'sec' }, 'About'), h('div', { class: 'pad' }, more, a.source ? h('div', { class: 'src' }, `From ${a.source}`) : null)] : null,
    (a.genres || []).length ? h('div', { class: 'chips pad', style: 'margin-top:14px;flex-wrap:wrap' }, a.genres.map(g => h('span', { class: 'chip' }, g))) : null,
    h('div', { class: 'facts' },
      h('div', {}, h('b', {}, plural(songs.length, 'episode')), ` • ${fmtLong(total)} in total`),
      lastTs ? h('div', {}, `Latest episode ${fmtDate(lastTs)}`) : null,
      firstTs ? h('div', {}, `First episode in your library ${fmtDate(firstTs)}`) : null,
      a.link ? h('a', { href: a.link, target: '_blank', rel: 'noopener', style: 'color:var(--accent)' }, 'Website') : null));
}

async function renderPodMore(box, al) {
  put(box, spinner());
  let others = [];
  try { others = podCanonical(await allAlbums(S.podId)).filter(x => x.id !== al.id); } catch (e) { /* none */ }
  put(box, others.length ? [h('h2', { class: 'sec' }, 'Other podcasts in your library'), h('div', { class: 'lib-grid' }, others.map(albumCard))]
    : h('div', { class: 'empty' }, 'No other podcasts in your library yet.'));
}

// =====================================================================
// Home
// =====================================================================
let lastHome = 0, homeTok = 0;
const chipFilter = { all: () => true, music: it => it.lib !== 'podcast', podcasts: it => it.lib === 'podcast' };

function recentTile(it) {
  const t = h('button', { class: 'rtile', onclick: () => openItem(it) },
    cover(it.cover, 'sm', 120, { round: it.type === 'artist', liked: it.type === 'liked', fallback: it.type === 'playlist' ? 'playlist' : it.type === 'artist' ? 'user' : 'note' }),
    h('span', {}, it.name));
  onLongPress(t, () => it.type === 'liked' ? null : itemSheet(it));
  return t;
}
function albumCard(a) {
  const c = h('button', { class: 'card', onclick: () => openAlbum(a.id) },
    cover(a.coverArt, '', 300), h('div', { class: 'nm' }, a.name), h('div', { class: 'sb' }, [a.artist, a.year].filter(Boolean).join(' • ')));
  onLongPress(c, () => itemSheet(albumItem(a)));
  return c;
}
function continueCard(b) {
  const s = b.entry, done = b.position / 1000;
  const left = s.duration ? Math.max(1, Math.round((s.duration - done) / 60)) : null;
  return h('button', { class: 'card', onclick: () => resumeEntry(s, done) },
    cover(s.coverArt, '', 300), h('div', { class: 'nm' }, s.title),
    h('div', { class: 'sb' }, [s.artist, left ? `${left} min left` : null].filter(Boolean).join(' • ')),
    s.duration ? h('div', { class: 'prog' }, h('div', { style: `width:${Math.min(100, Math.round((done / s.duration) * 100))}%` })) : null);
}
// The most recent thing you were listening to, as one big tap-to-resume card. The X hides it until you
// listen to something else (the saved position itself is kept: it stays under "Continue listening").
const heroKey = b => `${b.entry.id}:${b.position}`;
const heroHidden = b => lsGet('tr.heroHide', '') === heroKey(b);
function resumeHero(b) {
  const s = b.entry, done = b.position / 1000, left = s.duration ? Math.max(1, Math.round((s.duration - done) / 60)) : null;
  const go = () => { haptic(); resumeEntry(s, done); };
  const hide = e => {
    e.stopPropagation(); haptic();
    lsSet('tr.heroHide', heroKey(b)); el.remove(); lastHome = 0; renderHome();
    toastUndo('Card hidden', () => { lsSet('tr.heroHide', ''); lastHome = 0; renderHome(); });
  };
  const el = h('div', { class: 'hero-resume', role: 'button', tabindex: '0', onclick: go, onkeydown: e => { if (e.key === 'Enter') go(); } },
    cover(s.coverArt, 'md', 200),
    h('div', { class: 'hr-txt' }, h('small', {}, 'Pick up where you left off'), h('b', {}, s.title), h('span', {}, [s.artist, left ? `${left} min left` : null].filter(Boolean).join(' • ')),
      s.duration ? h('div', { class: 'ep-prog wide', style: 'margin:8px 0 0' }, h('div', { style: `width:${Math.min(100, Math.round((done / s.duration) * 100))}%` })) : null),
    h('span', { class: 'hr-play', html: I.play }),
    h('button', { class: 'hr-x', 'aria-label': 'Hide this card', onclick: hide, html: I.x }));
  return el;
}
async function resumeEntry(song, startAt) {
  try {
    let al, songs;
    if (S.podAlbums.has(song.albumId)) { const show = await podcastShow(song.albumId); al = show.main; songs = show.songs; }
    else { al = await getAlbum(song.albumId); songs = orderAlbumSongs(al); }
    const i = songs.findIndex(s => s.id === song.id);
    if (i >= 0) { playQueue(songs, i, albumItem(al), { startAt }); return; }
  } catch (e) { /* fall through to a single-item queue */ }
  playQueue([song], 0, null, { startAt });
}

// Pull down from the top of Home / Library to refresh
function pullRefresh(root, host, refresh) {
  const ind = h('div', { class: 'ptr', html: I.down });
  host.append(ind);
  let y0 = null, dy = 0, busy = false;
  const reset = () => { ind.style.transform = ''; ind.classList.remove('ready', 'spin'); ind.style.opacity = 0; };
  root.addEventListener('touchstart', e => { y0 = (root.scrollTop <= 0 && !busy && e.touches[0].clientX > 24) ? e.touches[0].clientY : null; dy = 0; }, { passive: true });
  root.addEventListener('touchmove', e => {
    if (y0 == null) return;
    dy = e.touches[0].clientY - y0;
    if (dy <= 0 || root.scrollTop > 0) { if (dy <= 0) { y0 = null; reset(); } return; }
    e.preventDefault();
    const pull = Math.min(dy * 0.5, 84);
    ind.style.opacity = Math.min(1, dy / 90); ind.style.transform = `translateY(${pull}px) rotate(${Math.min(dy, 120) * 1.5}deg)`;
    ind.classList.toggle('ready', dy > 110);
  }, { passive: false });
  const end = async () => {
    if (y0 == null) return;
    const go = dy > 110; y0 = null;
    if (!go) { reset(); return; }
    busy = true; haptic(); ind.classList.add('spin'); ind.style.transform = 'translateY(56px)';
    try { await refresh(); } catch (e) { /* the page shows its own error */ }
    busy = false; reset();
  };
  root.addEventListener('touchend', end); root.addEventListener('touchcancel', () => { y0 = null; reset(); });
}
const refreshAll = () => { ['pl', 'allal', 'allar', 'al:', 'ar:', 'genres', 'disc:', 'epd:'].forEach(bust); S.homeDirty = true; return nd('ping').then(() => connOk()); };

function topBar(withChips) {
  const bar = h('div', { class: 'top-chips' }, h('button', { class: 'avatar', onclick: openMenu, 'aria-label': 'Menu' }, 'T'));
  if (withChips && S.podId) {
    const chips = h('div', { class: 'chips' });
    [['all', 'All'], ['music', 'Music'], ['podcasts', 'Podcasts']].forEach(([k, label]) => chips.append(
      h('button', { class: 'chip' + (S.chip === k ? ' on' : ''), onclick: () => { S.chip = k; renderHome(); } }, label)));
    bar.append(chips);
  }
  return bar;
}
// How this phone reaches Titan: Home (on the home Wi-Fi), Tailscale, or Remote (the internet address).
// The home address also works from afar through Tailscale, so the app asks the phone whether its own Wi-Fi is the home one.
function connectionLabel() {
  if (S.me.auth) return 'Remote';
  const h4 = location.hostname.match(/^(\d+)\.(\d+)\.\d+\.\d+$/);
  const lan = h4 && (+h4[1] === 192 && +h4[2] === 168 || +h4[1] === 10 || +h4[1] === 172 && +h4[2] >= 16 && +h4[2] <= 31);
  if (!lan) return 'Tailscale';
  if (window.TRNative && !TRNative.onHomeWifi) return 'Home or Tailscale';   // older app version can't tell: update it
  if (window.TRNative) { try { return TRNative.onHomeWifi(location.hostname) ? 'Home' : 'Tailscale'; } catch (e) { return 'Home or Tailscale'; } }
  return 'Home';
}
// Playback & display settings (all stored on this phone)
function settingsSheet() {
  const on = v => (v ? 'On' : 'Off');
  const xf = xfSec(), saved = Math.round((lsGet('tr.silsaved', 0) || 0) / 60);
  sheet({ title: 'Playback & display', sub: 'Saved on this phone', coverEl: h('div', { class: 'cover md', html: I.sliders }),
    items: [
      { header: 'Playback' },
      { icon: levelOn() ? 'check' : null, label: `Volume leveling: ${on(levelOn())}`, run: () => { lsSet('tr.level', !levelOn()); S.lufs.clear(); applyVolume(); if (levelOn()) ensureLevels([cur(), P.list[P.index + 1]]); toast(`Volume leveling ${on(levelOn()).toLowerCase()}`, 'ok', 2200); } },
      { icon: gaplessOn() ? 'check' : null, label: `Gapless playback: ${xf > 0 ? 'On (crossfade)' : on(gaplessOn())}`, run: () => { if (xf > 0) { toast('Turn crossfade off to change this', '', 2500); return; } lsSet('tr.gapless', !lsGet('tr.gapless', false)); xfReset(); toast(`Gapless ${on(lsGet('tr.gapless', false)).toLowerCase()}`, 'ok', 2200); } },
      { icon: xf > 0 ? 'check' : null, label: `Crossfade: ${xf > 0 ? xf + ' seconds' : 'Off'}`, run: () => crossfadeSheet() },
      { icon: skipSilenceOn() ? 'check' : null, label: `Skip silence in podcasts: ${on(skipSilenceOn())}${saved > 0 ? ` (saved ${saved} min)` : ''}`, run: () => { lsSet('tr.skipsil', !skipSilenceOn()); if (cur()) loadSilences(cur()); toast(skipSilenceOn() ? 'Silences are skipped (analysed on Titan the first time an episode plays)' : 'Skip silence off', 'ok', 3200); } },
      { header: 'Music' },
      { icon: autoplayOn() ? 'check' : null, label: `Autoplay similar songs when the queue ends: ${on(autoplayOn())}`, run: () => { lsSet('tr.autoplay', !autoplayOn()); toast(`Autoplay ${on(autoplayOn()).toLowerCase()}`, 'ok', 2200); } },
      { icon: FX.on ? 'check' : null, label: `Audio effects (equalizer, visualizer): ${on(FX.on)}`, run: () => setFx(!FX.on) },
      { icon: 'eq', label: 'Equalizer', run: eqDialog },
      { header: 'Display' },
      { icon: 'sliders', label: `Theme: ${{ dark: 'Dark', light: 'Light', auto: 'Match phone' }[themeMode()]}`, run: () => themeSheet() },
    ] });
}
function crossfadeSheet() {
  const v = xfSec();
  sheet({ title: 'Crossfade', sub: 'Songs overlap and fade into each other', coverEl: h('div', { class: 'cover md', html: I.sliders }),
    items: [0, 2, 4, 6, 8].map(n => ({ icon: v === n ? 'check' : null, label: n ? `${n} seconds` : 'Off', run: () => { lsSet('tr.xfade', n); xfReset(); toast(n ? `Crossfade ${n} s (applies between songs, not podcasts)` : 'Crossfade off', 'ok', 2600); } })) });
}
function themeSheet() {
  const m = themeMode();
  sheet({ title: 'Theme', coverEl: h('div', { class: 'cover md', html: I.sliders }),
    items: [['dark', 'Dark'], ['light', 'Light'], ['auto', 'Match phone']].map(([k, label]) => ({ icon: m === k ? 'check' : null, label, run: () => { lsSet('tr.theme', k); applyTheme(); } })) });
}
function openMenu() {
  sheet({ title: 'tuberipper', sub: [S.me.user ? `Signed in as ${S.me.user}` : 'Music from your Navidrome library', `Connection: ${connectionLabel()}`].join(' • '), coverEl: h('div', { class: 'avatar' }, 'T'),
    items: [
      !S.me.auth && { icon: 'list', label: 'Classic TubeRipper view', run: () => { location.href = api('/classic'); } },
      window.TRNative && TRNative.changeServer && { icon: 'edit', label: 'Change server address', run: () => TRNative.changeServer() },
      { icon: 'chart', label: 'Your listening stats', run: openStats },
      { icon: 'health', label: 'Library health', run: openHealth },
      { icon: 'sliders', label: 'Playback & display', run: settingsSheet },
      { icon: 'down', label: 'Reload app', run: () => location.reload() },
      S.me.auth && { icon: 'x', label: 'Sign out', run: async () => { await nativeFetch(api('/api/auth/logout'), { method: 'POST' }); location.href = api('/login'); } },
    ] });
}

async function renderHome() {
  const root = roots.home;
  if (S.offline) { renderOfflineRoot(root); return; }
  const tok = ++homeTok;
  const scroll = root.scrollTop;
  if (!root.children.length) root.append(topBar(true), skelHome());
  const chip = S.chip;
  const folder = chip === 'music' ? S.musicId : chip === 'podcasts' ? S.podId : null;
  const [pls, newest, rnd, pods, upNext] = await Promise.all([
    getPlaylists().catch(() => []),
    albumList('newest', 24, folder ? { musicFolderId: folder } : {}).then(podCanonical).then(l => l.slice(0, 14)).catch(() => []),
    chip === 'podcasts' ? [] : albumList('random', 14, S.musicId ? { musicFolderId: S.musicId } : {}).catch(() => []),
    chip === 'podcasts' && S.podId ? allAlbums(S.podId).then(podCanonical).catch(() => []) : [],
    chip === 'podcasts' ? upNextEpisodes().catch(() => []) : [],
    refreshBookmarks().catch(() => null),
  ]);
  if (tok !== homeTok) return;

  const lib = it => (it.type === 'album' && S.podAlbums.has(it.id) ? 'podcast' : 'music');
  let tiles = S.recents.map(r => ({ ...r, lib: lib(r) })).filter(chipFilter[chip]);
  if (chip !== 'podcasts') tiles.unshift({ type: 'liked', id: 'liked', name: 'Liked Songs', lib: 'music' });
  if (tiles.length < 4 && chip !== 'podcasts') {
    pls.forEach(p => { if (!tiles.some(t => t.type === 'playlist' && t.id === p.id)) tiles.push({ type: 'playlist', id: p.id, name: p.name, cover: p.coverArt, lib: 'music' }); });
  }
  tiles = tiles.slice(0, 8);

  const marks = [...S.bookmarks.values()]
    .filter(b => chip === 'all' || (chip === 'podcasts') === isPodcastSong(b.entry))
    .sort((a, b) => String(b.changed).localeCompare(String(a.changed)));

  const out = [topBar(true)];
  if (tiles.length) out.push(h('div', { class: 'recents-grid' }, tiles.map(recentTile)));
  if (marks.length) {
    const hero = !heroHidden(marks[0]), rest = marks.slice(hero ? 1 : 0, 13);
    if (hero) out.push(resumeHero(marks[0]));
    if (rest.length) out.push(h('h2', { class: 'sec' }, 'Continue listening'), h('div', { class: 'hrow' }, rest.map(continueCard)));
  }
  if (chip === 'podcasts') {
    if (upNext.length) out.push(h('h2', { class: 'sec' }, 'Up next'), h('div', { class: 'hrow' }, upNext.map(upNextCard)));
    if (pods.length) out.push(h('h2', { class: 'sec' }, 'Your podcasts'), h('div', { class: 'hrow' }, pods.map(albumCard)));
  } else {
    const mixHost = h('div', { class: 'mixes' });
    out.push(mixHost); loadMixes(mixHost);
    if (newest.length) out.push(h('h2', { class: 'sec' }, 'Recently added'), h('div', { class: 'hrow' }, newest.map(albumCard)));
    const recHost = h('div', { class: 'recs' });
    out.push(recHost); loadRecs(recHost);
    if (rnd.length) out.push(h('h2', { class: 'sec' }, 'Rediscover'), h('div', { class: 'hrow' }, rnd.map(albumCard)));
  }
  if (out.length === 1) out.push(h('div', { class: 'empty' }, 'Nothing here yet. Search for a song and add it to your library.'));
  out.push(h('div', { class: 'spacer' }));
  root.replaceChildren(...out);
  root.scrollTop = scroll;
  lastHome = Date.now();
  S.homeDirty = false;
}

// =====================================================================
// Recommendations on Home: songs you don't own. Tap = play now (streamed through Titan) and add to the library at the same time.
// =====================================================================
const onlineSong = p => ({ id: 'yt:' + p.video_id, online: true, videoId: p.video_id, title: p.title, artist: p.artist, album: p.album || '',
  duration: p.duration || 0, coverArt: p.artwork || '', pay: songPayload(p) });
const ADDING = new Set();
async function findLibrarySong(p) {   // the song as Navidrome knows it, once its scan has seen the new file
  for (let i = 0; i < 10; i++) {
    try {
      const r = await nd('search3', { query: `${p.title} ${mainArtist(p.artist)}`, songCount: 20, artistCount: 0, albumCount: 0 });
      const hit = arr(r.searchResult3?.song).find(x => normT(x.title) === normT(p.title) && normT(mainArtist(x.artist)) === normT(mainArtist(p.artist)));
      if (hit) return hit;
    } catch (e) { /* try again */ }
    await new Promise(res => setTimeout(res, 4000));
  }
  return null;
}
function addOnline(song) {
  if (!song.online || ADDING.has(song.id)) return;
  const key = song.id;
  ADDING.add(key);
  quickAdd({ song: song.pay }, song.title, 'none', null, null, async ok => {
    if (ok) {
      REC.ts = 0; S.homeDirty = true;
      const lib = await findLibrarySong(song.pay);
      if (lib) {   // from now on this is a normal library song; what is playing right now just carries on streaming
        Object.assign(song, { id: lib.id, online: false, videoId: null, pay: null, albumId: lib.albumId, artistId: lib.artistId, album: lib.album || song.album,
          coverArt: lib.coverArt || song.coverArt, duration: lib.duration || song.duration, track: lib.track });
        persistSession(true); bus.dispatchEvent(new Event('queue')); if (cur() === song) bus.dispatchEvent(new Event('track'));
      }
    }
    ADDING.delete(key);
  });
}
function playOnline(songs, i) {
  const list = songs.slice(i).map(onlineSong);
  playQueue(list, 0, null);
  addOnline(list[0]);
}
function recCard(s, i, songs) {
  const more = h('button', { class: 'rec-more', html: I.moreV, 'aria-label': 'More', onclick: e => { e.stopPropagation(); onlineSongSheet(s); } });
  const c = h('div', { class: 'card rec', role: 'button', tabindex: '0', onclick: () => { haptic(); playOnline(songs, i); } },
    h('div', { class: 'rec-art' }, cover(s.artwork ? 'x' : null, '', 300, { url: s.artwork }), more),
    h('div', { class: 'nm' }, s.title), h('div', { class: 'sb' }, s.artist));
  onLongPress(c, () => onlineSongSheet(s));
  return c;
}
const REC = { data: null, ts: 0, loading: null };
function recShelves(d) {
  return (d.shelves || []).flatMap(sh => [h('h2', { class: 'sec' }, sh.title), h('div', { class: 'hrow' }, sh.songs.map((s, i) => recCard(s, i, sh.songs)))]);
}
async function loadRecs(host) {
  if (REC.data) host.replaceChildren(...recShelves(REC.data));
  if (REC.data && Date.now() - REC.ts < 10 * 60 * 1000) return;
  try {
    if (!REC.loading) REC.loading = fetch(api('/api/recommend/home')).then(r => (r.ok ? r.json() : null)).finally(() => { REC.loading = null; });
    const d = await REC.loading;
    if (d) { REC.data = d; REC.ts = Date.now(); host.replaceChildren(...recShelves(d)); }
  } catch (e) { /* recommendations are optional */ }
}

// =====================================================================
// Radio + autoplay, Made for you (Daily Mixes, Discover Weekly), listening stats, library health, audio effects
// =====================================================================
Object.assign(I, {
  radio: sv('<circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2"/>'),
  chart: sv('<path d="M5 20V10M12 20V4M19 20v-7"/>'),
  health: sv('<path d="M12 21s-8-4.8-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 10c0 6.2-8 11-8 11z"/><path d="M8 12.5h2l1.3-2.5 2 5 1.4-2.5H17"/>'),
  wave: sv('<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>'),
  eq: sv('<path d="M6 4v16M12 4v16M18 4v16"/><circle cx="6" cy="14" r="2" fill="currentColor"/><circle cx="12" cy="8" r="2" fill="currentColor"/><circle cx="18" cy="15" r="2" fill="currentColor"/>'),
});
const getJSON = async path => {
  const r = await fetch(api(path));
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
  return r.json();
};
const postJSON = async (path, body) => {
  const r = await fetch(api(path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
  return d;
};
const songKey = s => `${normT(mainArtist(s.artist))}|${normT(s.title)}`;

// ----- Every counted play goes to the stats log (30 s in, same moment as the scrobble) -----
function logPlay(s) {
  if (!s || isLong(s) || isPodcastSong(s)) return;
  const uid = (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}${Math.random().toString(36).slice(2)}`;
  fetch(api('/api/stats/play'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
    body: JSON.stringify({ uid, ts: Math.floor(Date.now() / 1000), title: s.title, artist: s.artist, album: s.album, nd_id: s.online ? '' : s.id,
      album_id: s.albumId, artist_id: s.artistId, cover: s.coverArt, duration: s.duration }) }).catch(() => {});
}

// ----- Radio ("more like this") and autoplay when the queue runs out -----
const autoplayOn = () => lsGet('tr.autoplay', true);
async function fetchRadio(seed) {
  const q = new URLSearchParams();
  ['video_id', 'artist', 'title'].forEach(k => { if (seed[k]) q.set(k, seed[k]); });
  return (await getJSON('/api/radio?' + q)).songs.map(x => (x.lib ? x.lib : onlineSong(x)));
}
const seedOf = s => (s.online ? { video_id: s.videoId } : { artist: s.artist, title: s.title });
async function startRadio(seed, label, first) {
  const t = toast(`Starting ${label} radio…`, '', 0);
  try {
    const more = await fetchRadio(seed);
    if (!more.length) throw new Error('nothing similar found');
    setToast(t, `${label} radio`, 'ok', 2000);
    playQueue(first ? [first, ...more.filter(x => songKey(x) !== songKey(first))] : more, 0, null);
  } catch (e) { setToast(t, `Couldn’t start radio: ${e.message}`, 'bad', 6000); }
}
let autoBusy = false;
async function autoplayExtend() {
  const s = cur();
  if (!s || autoBusy || !autoplayOn() || isLong(s) || S.offline) return;
  autoBusy = true;
  try {
    const songs = await fetchRadio(seedOf(s));
    const have = new Set(P.list.map(songKey));
    const fresh = songs.filter(x => !have.has(songKey(x))).slice(0, 15);
    if (!fresh.length || cur() !== s || P.index !== P.list.length - 1) return;   // nothing new, or you already moved on
    P.list.push(...fresh);
    persistSession(true); bus.dispatchEvent(new Event('queue'));
    toast(`Autoplay: more like “${s.title}”`, 'ok', 2800);
    loadTrack(P.index + 1);
  } catch (e) { /* the queue simply ends */ } finally { autoBusy = false; }
}

// ----- Made for you -----
const MIXES = { data: null, ts: 0, loading: null, year: null };
const MIX_COLORS = ['#2a4d8f', '#8f2a5c', '#1f7a5c', '#a0561a'];
const mixObj = x => (x.kind === 'online' ? onlineSong(x) : x);
function mosaic(covers) {
  const box = h('div', { class: 'mosaic n' + Math.min(4, covers.length) });
  covers.slice(0, 4).forEach(c => box.append(h('img', { loading: 'lazy', alt: '', src: coverUrl(c, 200), onerror: e => e.target.remove() })));
  return box;
}
function mixCard(m, i) {
  return h('button', { class: 'card mixcard', style: `--mix:${MIX_COLORS[i % 4]}`, onclick: () => openMix(m, i) },
    h('div', { class: 'mix-art' }, mosaic(m.covers), h('span', { class: 'mix-badge' }, m.title)),
    h('div', { class: 'nm' }, m.family), h('div', { class: 'sb' }, m.sub));
}
function weeklyCard(w) {
  return h('button', { class: 'card mixcard', style: '--mix:#6b2fb3', onclick: () => openWeekly(w) },
    h('div', { class: 'mix-art weekly' }, h('div', { class: 'weekly-ic', html: I.radio }), h('span', { class: 'mix-badge' }, 'Discover Weekly')),
    h('div', { class: 'nm' }, 'Discover Weekly'), h('div', { class: 'sb' }, `${plural(w.songs.length, 'new song')} for you`));
}
function wrappedBanner(y) {
  return h('button', { class: 'wrapped-banner', onclick: () => openWrapped() },
    h('div', {}, h('small', {}, 'Your year so far'), h('b', {}, `Your ${new Date().getFullYear()} in music`), h('span', {}, `${y.minutes.toLocaleString()} minutes • ${y.unique_artists} artists`)),
    h('span', { class: 'wb-go', html: I.chart }));
}
async function loadMixes(host) {
  const draw = () => {
    const d = MIXES.data || { mixes: [], weekly: null };
    const cards = [...(d.weekly ? [weeklyCard(d.weekly)] : []), ...d.mixes.map(mixCard)];
    const m = new Date().getMonth(), y = MIXES.year;
    host.replaceChildren(...[
      (m >= 10 || m === 0) && y && y.plays >= 10 ? wrappedBanner(y) : null,
      cards.length ? h('h2', { class: 'sec' }, 'Made for you') : null,
      cards.length ? h('div', { class: 'hrow' }, cards) : null].filter(Boolean));
  };
  if (MIXES.data) draw();
  if (MIXES.data && Date.now() - MIXES.ts < 10 * 60 * 1000) return;
  try {
    if (!MIXES.loading) MIXES.loading = Promise.all([getJSON('/api/mixes'), getJSON(`/api/stats?period=year&tz=${new Date().getTimezoneOffset()}`).catch(() => null)]).finally(() => { MIXES.loading = null; });
    const [d, y] = await MIXES.loading;
    MIXES.data = d; MIXES.year = y; MIXES.ts = Date.now();
    draw();
  } catch (e) { /* optional */ }
}
function mixRow(s, i, play) {
  if (!s.online) return songRow(s, { showAlbum: false, onPlay: () => play(i, true) });
  return h('div', { class: 'row' + (cur() && cur().id === s.id ? ' playing' : ''), 'data-sid': s.id, onclick: () => play(i, true) },
    cover(s.coverArt, 'sm', 100), h('div', { class: 'txt' }, h('div', { class: 't' }, s.title), h('div', { class: 's' }, ['New', s.artist].filter(Boolean).join(' • '))),
    addBtn({ song: s.pay }, s.title),
    h('button', { class: 'icon-btn dim', html: I.more, 'aria-label': 'More', onclick: e => { e.stopPropagation(); onlineSongSheet(s.pay); } }));
}
function mixPage({ title, sub, coverEl, songs, extra }) {
  openPage(page => {
    const objs = songs.map(mixObj);
    const play = (i, tapped) => { haptic(); playQueue(objs.slice(i), 0, null); if (tapped && objs[i].online) addOnline(objs[i]); };
    put(page, backBar(), hero(coverEl, title, sub),
      h('div', { class: 'actions' }, extra || null, h('div', { class: 'grow' }), shuffleBtn(objs, null), playBtn(() => play(0, false), null, objs)),
      objs.map((s, i) => mixRow(s, i, play)), h('div', { class: 'spacer' }));
  });
}
function openMix(m, i) {
  const art = h('div', { class: 'mix-art big', style: `--mix:${MIX_COLORS[i % 4]}` }, mosaic(m.covers), h('span', { class: 'mix-badge' }, m.title));
  mixPage({ title: m.title, sub: `${m.family} • ${m.sub} • ${plural(m.songs.length, 'song')}`, coverEl: art, songs: m.songs });
}
function openWeekly(w) {
  const art = h('div', { class: 'mix-art weekly big', style: '--mix:#6b2fb3' }, h('div', { class: 'weekly-ic', html: I.radio }), h('span', { class: 'mix-badge' }, 'Discover Weekly'));
  const add = h('button', { class: 'pillbtn dark', onclick: async () => { add.disabled = true; add.textContent = 'Adding…'; await addAllOnline(w.songs); add.textContent = '✓ Added'; } }, `Add all ${w.songs.length} to library`);
  mixPage({ title: 'Discover Weekly', sub: `New music picked for you • ${plural(w.songs.length, 'song')} • changes every week`, coverEl: art, songs: w.songs.map(s => ({ ...s, kind: 'online' })), extra: add });
}
async function addQuiet(p) {   // one add-to-library job, followed without a toast of its own
  const r = await postJSON('/api/jobs/catalog', { song: songPayload(p), add: { playlist_mode: 'none', notify: false } });
  for (let n = 0; n < 160; n++) {
    await new Promise(res => setTimeout(res, 2500));
    const job = await getJSON(`/api/jobs/${r.id}`);
    if (job.status === 'done') { if (job.tracks.some(t => t.status === 'failed')) throw new Error('failed'); return; }
    if (job.status === 'error' || job.status === 'awaiting_confirmation') throw new Error(job.error || 'needs checking');
  }
  throw new Error('timed out');
}
async function addAllOnline(songs) {
  const t = toast(`Adding ${plural(songs.length, 'song')}…`, '', 0);
  let done = 0, failed = 0, next_ = 0;
  const worker = async () => {
    while (next_ < songs.length) {
      const p = songs[next_++];
      try { await addQuiet(p); done++; } catch (e) { failed++; }
      setToast(t, `Adding… ${done + failed} of ${songs.length}`);
    }
  };
  await Promise.all([worker(), worker()]);
  setToast(t, `✓ Added ${plural(done, 'song')}${failed ? `, ${failed} couldn’t be added` : ''}`, failed ? 'bad' : 'ok', 9000);
  MIXES.ts = 0; REC.ts = 0; S.homeDirty = true;
}

// ----- Your listening (stats) and the year slideshow -----
const fmtMinutes = m => (m >= 120 ? `${Math.round(m / 60).toLocaleString()} hours` : `${m.toLocaleString()} minutes`);
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
function bars(values, labels, fmt) {
  const max = Math.max(1, ...values);
  return h('div', { class: 'bars-wrap' },
    h('div', { class: 'bars' }, values.map((v, i) => h('div', { class: 'bar', style: `height:${Math.max(3, Math.round((v / max) * 100))}%`, title: `${labels[i]}: ${fmt(v)}` }))),
    h('div', { class: 'bar-labels' }, labels.map((l, i) => h('span', {}, l))));
}
const hourLabel = i => (i === 0 ? '12a' : i < 12 ? `${i}a` : i === 12 ? '12p' : `${i - 12}p`);
function statTile(big, label) { return h('div', { class: 'stat-tile' }, h('b', {}, big), h('span', {}, label)); }
function statsBody(d, period) {
  if (!d.plays) {
    return h('div', { class: 'empty' }, h('div', {}, 'Nothing counted in this period yet.'),
      h('div', { class: 'dim', style: 'margin-top:8px' }, 'A play counts once you’ve listened for 30 seconds.'),
      d.history_since ? h('div', { class: 'dim', style: 'margin-top:8px' }, `History starts ${new Date(d.history_since * 1000).toLocaleDateString()}.`) : null);
  }
  const artistRow = (a, i) => h('div', { class: 'row', onclick: () => openArtist({ name: a.name }) },
    h('div', { class: 'num' }, i + 1), cover(a.cover, 'sm', 100, { round: true, fallback: 'user' }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, a.name), h('div', { class: 's' }, `${plural(a.plays, 'play')} • ${fmtMinutes(a.minutes)}`)));
  const songRow_ = (s, i) => h('div', { class: 'row', onclick: () => openArtist({ name: mainArtist(s.artist) }) },
    h('div', { class: 'num' }, i + 1), cover(s.cover, 'sm', 100),
    h('div', { class: 'txt' }, h('div', { class: 't' }, s.title), h('div', { class: 's' }, `${s.artist} • ${plural(s.plays, 'play')}`)));
  return h('div', {},
    h('div', { class: 'stat-grid' }, statTile(d.minutes.toLocaleString(), 'minutes'), statTile(d.plays.toLocaleString(), 'plays'), statTile(d.unique_artists, 'artists'), statTile(d.unique_songs, 'songs')),
    d.persona ? h('div', { class: 'persona' }, h('small', {}, 'You’re a'), h('b', {}, d.persona)) : null,
    d.top_artists.length ? [h('h2', { class: 'sec' }, 'Top artists'), d.top_artists.slice(0, 5).map(artistRow)] : null,
    d.top_songs.length ? [h('h2', { class: 'sec' }, 'Top songs'), d.top_songs.slice(0, 5).map(songRow_)] : null,
    d.top_albums.length ? [h('h2', { class: 'sec' }, 'Top albums'), h('div', { class: 'hrow' }, d.top_albums.map(a => h('button', { class: 'card', onclick: () => (a.album_id ? openAlbum(a.album_id) : openArtist({ name: mainArtist(a.artist) })) },
      cover(a.cover, '', 300), h('div', { class: 'nm' }, a.album), h('div', { class: 'sb' }, `${a.artist} • ${plural(a.plays, 'play')}`))))] : null,
    h('h2', { class: 'sec' }, 'When you listen'),
    bars(d.by_hour, d.by_hour.map((_, i) => (i % 3 === 0 ? hourLabel(i) : '')), v => plural(v, 'play')),
    bars(d.by_weekday, DAYS, v => plural(v, 'play')),
    h('div', { class: 'stat-notes' },
      d.busiest_day ? h('div', {}, 'Busiest day: ', h('b', {}, new Date(d.busiest_day.date + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })), ` (${plural(d.busiest_day.plays, 'play')})`) : null,
      d.longest_streak > 1 ? h('div', {}, 'Longest streak: ', h('b', {}, `${d.longest_streak} days in a row`)) : null,
      d.new_artists != null ? h('div', {}, 'New artists you started listening to: ', h('b', {}, d.new_artists)) : null,
      d.history_since ? h('div', { class: 'dim' }, `Counting since ${new Date(d.history_since * 1000).toLocaleDateString()}`) : null));
}
function openStats() {
  openPage(page => {
    let period = lsGet('tr.statsPeriod', 'month');
    const body = h('div', {}), chips = h('div', { class: 'chips', style: 'padding:0 16px 10px' });
    const draw = async () => {
      put(body, skelRows(4));
      const p = period;
      try { const d = await getJSON(`/api/stats?period=${p}&tz=${new Date().getTimezoneOffset()}`); if (p === period) put(body, statsBody(d, p)); }
      catch (e) { put(body, errBox(e.message, draw)); }
    };
    const drawChips = () => put(chips, [['week', '7 days'], ['month', '30 days'], ['year', 'This year'], ['all', 'All time']].map(([k, l]) =>
      h('button', { class: 'chip' + (period === k ? ' on' : ''), onclick: () => { period = k; lsSet('tr.statsPeriod', k); drawChips(); draw(); } }, l)));
    drawChips();
    put(page, backBar(), h('h1', { class: 'page-h' }, 'Your listening'),
      h('button', { class: 'wrapped-banner', onclick: () => openWrapped() }, h('div', {}, h('b', {}, `Replay your ${new Date().getFullYear()} in music`), h('span', {}, 'A slideshow of your year so far')), h('span', { class: 'wb-go', html: I.play })),
      chips, body, h('div', { class: 'spacer' }));
    draw();
  });
}
async function openWrapped() {
  let d;
  try { d = await getJSON(`/api/stats?period=year&tz=${new Date().getTimezoneOffset()}`); } catch (e) { toast(`Couldn’t load your stats: ${e.message}`, 'bad'); return; }
  if (!d.plays) { toast('Listen to a few songs first: plays count after 30 seconds.', '', 4000); return; }
  const yr = new Date().getFullYear(), ta = d.top_artists[0], ts = d.top_songs[0];
  const big = (kicker, main, sub, extra) => [h('small', {}, kicker), h('b', { class: 'w-big' }, main), sub ? h('span', { class: 'w-sub' }, sub) : null, extra || null];
  const list = (items, f) => h('ol', { class: 'w-list' }, items.map(x => h('li', {}, ...f(x))));
  const slides = [
    { bg: 'linear-gradient(160deg,#1ed760,#0d5c2e)', body: big(`${yr}`, 'Your year in music', `${d.plays.toLocaleString()} plays so far`) },
    { bg: 'linear-gradient(160deg,#8f2a5c,#2a0f3d)', body: big('You listened for', fmtMinutes(d.minutes), `across ${d.active_days} days${d.longest_streak > 1 ? `, with a ${d.longest_streak}-day streak` : ''}`) },
    ta && { bg: 'linear-gradient(160deg,#2a4d8f,#0e1a3a)', body: big('Your top artist', ta.name, `${plural(ta.plays, 'play')} • ${fmtMinutes(ta.minutes)}`, ta.cover ? h('div', { class: 'w-cover round' }, cover(ta.cover, '', 500, { round: true })) : null) },
    d.top_artists.length > 1 && { bg: 'linear-gradient(160deg,#a0561a,#3a1a05)', body: [h('small', {}, 'Your top artists'), list(d.top_artists.slice(0, 5), a => [h('b', {}, a.name), h('span', {}, plural(a.plays, 'play'))])] },
    ts && { bg: 'linear-gradient(160deg,#6b2fb3,#1d0c36)', body: big('Your top song', ts.title, `${ts.artist} • played ${plural(ts.plays, 'time')}`, ts.cover ? h('div', { class: 'w-cover' }, cover(ts.cover, '', 500)) : null) },
    d.top_songs.length > 1 && { bg: 'linear-gradient(160deg,#1f7a5c,#08261d)', body: [h('small', {}, 'Your top songs'), list(d.top_songs.slice(0, 5), s => [h('b', {}, s.title), h('span', {}, s.artist)])] },
    d.persona && { bg: 'linear-gradient(160deg,#c2185b,#3b0a1f)', body: big('You’re a', d.persona, 'by when you press play', bars(d.by_hour, d.by_hour.map((_, i) => (i % 6 === 0 ? hourLabel(i) : '')), v => plural(v, 'play'))) },
    d.new_artists != null && { bg: 'linear-gradient(160deg,#00838f,#002f33)', body: big('You discovered', `${d.new_artists} new artists`, 'that you hadn’t listened to before this year') },
    { bg: 'linear-gradient(160deg,#1ed760,#0d5c2e)', body: big('That’s your year so far', 'Keep listening', `${d.unique_songs.toLocaleString()} different songs by ${d.unique_artists.toLocaleString()} artists`) },
  ].filter(Boolean);
  let i = 0;
  const stage = h('div', { class: 'w-stage' }), dots = h('div', { class: 'w-dots' });
  const draw = () => {
    const s = slides[i];
    stage.style.background = s.bg;
    put(stage, h('div', { class: 'w-slide', key: i }, s.body));
    put(dots, slides.map((_, k) => h('span', { class: k === i ? 'on' : k < i ? 'done' : '' })));
    prevB.disabled = i === 0; nextB.textContent = i === slides.length - 1 ? 'Done' : 'Next';
  };
  const prevB = h('button', { class: 'w-btn', onclick: () => { if (i > 0) { i--; draw(); } } }, 'Back');
  const nextB = h('button', { class: 'w-btn main', onclick: () => { if (i < slides.length - 1) { i++; draw(); } else closeTop(); } }, 'Next');
  const root = h('div', { class: 'wrapped' }, stage, dots, h('button', { class: 'w-close icon-btn', html: I.x, 'aria-label': 'Close', onclick: () => closeTop() }), h('div', { class: 'w-nav' }, prevB, nextB));
  openLayer(root);
  draw();
}

// ----- Library health -----
function openHealth() {
  openPage(page => {
    const body = h('div', {}), fixable = canDelete();
    let job = null;
    const card = (title, desc, count, ...kids) => h('div', { class: 'hcard' + (count ? '' : ' ok') }, h('div', { class: 'hc-top' }, h('div', {}, h('b', {}, title), h('span', {}, desc)), h('div', { class: 'hc-n' }, count ? count : '✓')), ...kids);
    const fixBtn = (label, fn) => h('button', { class: 'pillbtn dark', onclick: async e => { const b = e.currentTarget; b.disabled = true; try { await fn(b); } catch (x) { toast(x.message, 'bad', 6000); } b.disabled = false; } }, label);
    const list = items => h('div', { class: 'hc-list' }, items.slice(0, 40), items.length > 40 ? h('div', { class: 'dim' }, `…and ${items.length - 40} more`) : null);
    const refresh = async () => { job = await getJSON(`/api/libhealth/scan/${job.id}`); draw(); };
    const draw = async () => {
      const d = job, s = d.summary;
      let trash = [];
      try { trash = (await getJSON('/api/libhealth/trash')).batches; } catch (e) { /* none */ }
      put(body,
        h('div', { class: 'hc-sum' }, `${s.songs.toLocaleString()} songs checked • ${(s.bytes / 1e9).toFixed(1)} GB music + ${(s.podcast_bytes / 1e9).toFixed(1)} GB podcasts = ${((s.bytes + s.podcast_bytes) / 1e9).toFixed(1)} GB • ${new Date(s.when * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`),
        card('Duplicate songs', 'Real copies of the same song (same album, or a greatest-hits copy). The best copy stays, the others can go to the trash.', s.duplicate_files,
          s.several_albums ? h('div', { class: 'hc-sum', style: 'padding:6px 0 0' }, `${plural(s.several_albums, 'song')} appear on more than one album. Those are left alone.`) : null,
          s.duplicate_files ? [list(d.duplicates.map(g => h('div', { class: 'hc-item' }, h('b', {}, g.keep.title), h('span', {}, `keeping ${g.keep.album} (${g.keep.bitrate} kbps) • removing ${g.remove.map(r => `${r.album} (${r.bitrate} kbps)`).join(', ')}`)))),
            fixable ? fixBtn(`Move ${plural(s.duplicate_files, 'file')} to trash`, async () => {
              if (!await confirmDialog('Move duplicates to the trash?', `${plural(s.duplicate_files, 'file')} will move to the trash. The best copy of each song stays. You can restore them below.`, 'Move to trash')) return;
              const r = await postJSON('/api/libhealth/fix', { job: job.id, kind: 'duplicates' }); toast(`Moved ${plural(r.moved, 'file')} to the trash`, 'ok'); S.homeDirty = true; await refresh();
            }) : null] : null),
        card('Albums stored twice', 'The same album under two folder names, often a lossless copy beside a downloaded one. You decide which to keep: open the one you don’t want and delete it from its album page.', s.twin_albums,
          s.twin_albums ? list(d.twin_albums.map(t => h('div', { class: 'hc-item' }, h('b', {}, t.artist),
            h('span', {}, t.albums.map(a => `${a.album}: ${plural(a.songs, 'song')}, ${a.formats.join('/')}${a.kbps ? `, ${a.kbps} kbps` : ''}`).join('  •  '))))) : null),
        card('Damaged or empty files', 'These can’t be played. Move them to the trash, then add the songs again from Search.', s.unreadable,
          s.unreadable ? [list(d.unreadable.map(f => h('div', { class: 'hc-item' }, h('b', {}, f.title), h('span', {}, `${f.artist} • ${f.album}`)))),
            fixable ? fixBtn(`Move ${plural(s.unreadable, 'file')} to trash`, async () => {
              if (!await confirmDialog('Move damaged files to the trash?', 'They can’t be played anyway. You can restore them below.', 'Move to trash')) return;
              const r = await postJSON('/api/libhealth/fix', { job: job.id, kind: 'unreadable' }); toast(`Moved ${plural(r.moved, 'file')} to the trash`, 'ok'); await refresh();
            }) : null] : null),
        card('Albums without a cover', 'No art at all. A cover is looked up and saved in the album’s folder.', s.no_cover,
          s.no_cover ? [list(d.no_cover.map(a => h('div', { class: 'hc-item' }, h('b', {}, a.album), h('span', {}, `${a.artist} • ${plural(a.songs, 'song')}`)))),
            fixable ? fixBtn('Find covers', async b => {
              b.textContent = 'Looking…';
              const r = await postJSON('/api/libhealth/fix', { job: job.id, kind: 'covers' });
              toast(r.fixed ? `Added ${plural(r.fixed, 'cover')}${r.couldnt ? `, ${r.couldnt} not found` : ''}` : 'No covers found online for these', r.fixed ? 'ok' : '', 5000); await refresh();
            }) : null] : null),
        card('Low quality', `Compressed below ${112} kbps. They sound thin. Search the song again to replace it.`, s.low_quality,
          s.low_quality ? list(d.low_quality.map(f => h('div', { class: 'hc-item' }, h('b', {}, f.title), h('span', {}, `${f.artist} • ${f.bitrate} kbps`)))) : null),
        card('Missing lyrics', 'Songs with no lyrics file. The repair pass looks them up (and fills in missing tags).', s.no_lyrics,
          s.no_lyrics && fixable ? fixBtn('Find lyrics & fix tags', async b => {
            b.textContent = 'Scanning…';
            let j = await postJSON('/api/library/scan');
            while (j.status === 'scanning') { await new Promise(r => setTimeout(r, 2000)); j = await getJSON(`/api/library/scan/${j.id}`); }
            const n = j.flagged.length;
            if (!n) { toast('Nothing to repair', 'ok'); return; }
            await postJSON(`/api/library/scan/${j.id}/repair`, {});
            while (j.status !== 'done') { await new Promise(r => setTimeout(r, 3000)); j = await getJSON(`/api/library/scan/${j.id}`); b.textContent = `Fixing ${j.progress.done} of ${j.progress.total}…`; }
            toast('Lyrics and tags repaired', 'ok'); S.homeDirty = true; b.textContent = 'Done';
          }) : null),
        h('h2', { class: 'sec' }, 'Trash'),
        trash.length ? trash.map(t => h('div', { class: 'hcard' }, h('div', { class: 'hc-top' }, h('div', {}, h('b', {}, `${plural(t.songs, 'song')} • ${new Date(t.when * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`), h('span', {}, t.examples.join(', '))),
          fixable ? fixBtn('Restore', async () => { const r = await postJSON('/api/libhealth/restore', { batch: t.batch }); toast(`Restored ${plural(r.restored, 'song')}${r.skipped ? ` (${r.skipped} already exist)` : ''}`, 'ok'); S.homeDirty = true; draw(); }) : null)))
          : h('div', { class: 'dim', style: 'padding:0 16px' }, 'The trash is empty.'),
        trash.length && fixable ? h('button', { class: 'pillbtn dark', style: 'margin:12px 16px', onclick: async () => {
          if (!await confirmDialog('Empty the trash?', 'This permanently deletes the files in it. You can’t restore them afterwards.', 'Delete forever')) return;
          const r = await postJSON('/api/libhealth/trash/empty'); toast(`Deleted ${plural(r.deleted, 'song')} for good`, 'ok'); draw();
        } }, 'Empty trash') : null,
        !fixable ? h('div', { class: 'dim', style: 'padding:14px 16px' }, 'Fixing is only available on the home network, so this view is read-only here.') : null);
    };
    const scan = async () => {
      put(body, h('div', { class: 'hc-progress' }, h('div', { class: 'bar' }, h('div', { id: 'hcBar', style: 'width:2%' })), h('span', { id: 'hcTxt' }, 'Reading your library…')));
      try {
        job = await postJSON('/api/libhealth/scan');
        while (job.status === 'scanning') {
          await new Promise(r => setTimeout(r, 700));
          job = await getJSON(`/api/libhealth/scan/${job.id}`);
          const bar = $('#hcBar'), txt = $('#hcTxt');
          if (bar && job.total) bar.style.width = `${Math.max(2, Math.round((job.scanned / job.total) * 100))}%`;
          if (txt) txt.textContent = `Checked ${job.scanned} of ${job.total} files…`;
        }
        if (job.status === 'error') throw new Error(job.error);
        draw();
      } catch (e) { put(body, errBox(e.message, scan)); }
    };
    put(page, backBar(), h('h1', { class: 'page-h' }, 'Library health'),
      h('div', { class: 'hc-intro' }, 'Looks for duplicates, damaged files, missing covers and low-quality songs. Nothing is changed until you press a fix button, and fixes go to a trash you can restore from.'),
      h('div', { style: 'padding:0 16px 12px' }, h('button', { class: 'pillbtn', onclick: scan }, 'Scan library')), body, h('div', { class: 'spacer' }));
  });
}

// ----- Audio effects: equalizer and visualizer (they share one Web Audio graph, opt-in) -----
const FX = { on: lsGet('tr.fx', false), ctx: null, filters: [], pre: null, an: null };
const EQ_BANDS = [[60, 'lowshelf', '60 Hz'], [230, 'peaking', '230 Hz'], [910, 'peaking', '900 Hz'], [3600, 'peaking', '3.6 kHz'], [14000, 'highshelf', '14 kHz']];
const EQ_PRESETS = { Flat: [0, 0, 0, 0, 0], 'Bass boost': [6, 4, 0, 0, 0], 'Treble boost': [0, 0, 0, 3, 6], Vocal: [-2, -1, 3, 4, 1], Rock: [4, 2, -2, 2, 4],
  'Hip-hop': [5, 3, 0, 1, 2], Acoustic: [3, 1, 1, 2, 2], Classical: [3, 2, -1, 2, 3], 'Late night': [-3, 0, 1, 1, -1] };
const eqState = () => lsGet('tr.eq', { preset: 'Flat', gains: [0, 0, 0, 0, 0] });
function applyEq() {
  if (!FX.ctx) return;
  const g = eqState().gains;
  g.forEach((v, i) => { FX.filters[i].gain.value = v; });
  FX.pre.gain.value = Math.pow(10, -Math.max(0, ...g) / 20);   // headroom so boosts don't clip
}
function initFx() {
  if (!FX.on || FX.ctx) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { FX.on = false; return; }
    const ctx = new Ctx();
    FX.filters = EQ_BANDS.map(([f, type]) => { const b = ctx.createBiquadFilter(); b.type = type; b.frequency.value = f; if (type === 'peaking') b.Q.value = 1; return b; });
    FX.pre = ctx.createGain();
    FX.an = ctx.createAnalyser(); FX.an.fftSize = 256; FX.an.smoothingTimeConstant = 0.8;
    FX.filters.reduce((a, b) => { a.connect(b); return b; }, FX.pre);
    FX.filters[FX.filters.length - 1].connect(FX.an); FX.an.connect(ctx.destination);
    AUDS.forEach(a => {
      // Once an element feeds Web Audio the browser ignores its own volume, so volume leveling and crossfade (which set audio.volume) drive a gain node instead.
      const g = ctx.createGain();
      g.gain.value = a.volume;
      ctx.createMediaElementSource(a).connect(g); g.connect(FX.pre);
      Object.defineProperty(a, 'volume', { configurable: true, get: () => g.gain.value, set: v => { g.gain.value = Math.max(0, Math.min(1, +v || 0)); } });
    });
    FX.ctx = ctx; applyEq();
    const wake = () => { if (ctx.state !== 'running') ctx.resume().catch(() => {}); };
    AUDS.forEach(a => a.addEventListener('play', wake));
    ['click', 'touchend'].forEach(e => document.addEventListener(e, wake, { passive: true }));
    document.addEventListener('visibilitychange', wake);
    ctx.addEventListener('statechange', () => { if (!audio.paused) wake(); });
  } catch (e) { FX.on = false; FX.ctx = null; console.warn('audio effects unavailable', e); }
}
function setFx(on) { lsSet('tr.fx', on); location.reload(); }
function eqDialog() {
  dialog(box => {
    box.append(h('h3', {}, 'Equalizer'));
    if (!FX.on) {
      box.append(h('p', { class: 'note' }, 'The equalizer and the visualizer need “audio effects”. Turning them on reloads the app once. If sound ever stops while your screen is off, turn them off again in Playback & display.'),
        h('div', { class: 'btns' }, h('button', { class: 'pillbtn dark', onclick: () => closeTop() }, 'Not now'), h('button', { class: 'pillbtn', onclick: () => setFx(true) }, 'Turn on')));
      return;
    }
    const st = eqState(), rows = [], chips = h('div', { class: 'chips wrap' }), sliders = h('div', { class: 'eq' });
    const save = (preset, gains) => { lsSet('tr.eq', { preset, gains }); applyEq(); };
    const drawChips = () => put(chips, [...Object.keys(EQ_PRESETS), ...(eqState().preset === 'Custom' ? ['Custom'] : [])].map(n =>
      h('button', { class: 'chip' + (eqState().preset === n ? ' on' : ''), onclick: () => { if (n === 'Custom') return; const g = EQ_PRESETS[n].slice(); save(n, g); rows.forEach((r, i) => { r.input.value = g[i]; r.val.textContent = fmtDb(g[i]); }); drawChips(); } }, n)));
    const fmtDb = v => `${v > 0 ? '+' : ''}${v} dB`;
    EQ_BANDS.forEach(([f, , label], i) => {
      const val = h('span', { class: 'eq-v' }, fmtDb(st.gains[i]));
      const input = h('input', { type: 'range', min: -12, max: 12, step: 1, value: st.gains[i], 'aria-label': label, oninput: () => {
        const g = eqState().gains.slice(); g[i] = +input.value; save('Custom', g); val.textContent = fmtDb(g[i]); drawChips(); } });
      rows.push({ input, val });
      sliders.append(h('div', { class: 'eq-row' }, h('span', { class: 'eq-f' }, label), input, val));
    });
    drawChips();
    box.append(chips, sliders, h('div', { class: 'btns' }, h('button', { class: 'pillbtn dark', onclick: () => setFx(false) }, 'Turn audio effects off'), h('button', { class: 'pillbtn', onclick: () => closeTop() }, 'Done')));
  });
}
function vizCanvas() {
  const c = h('canvas', { class: 'viz' });
  const g = c.getContext('2d'), data = new Uint8Array(FX.an.frequencyBinCount);
  const draw = () => {
    if (!c.isConnected) return;   // the player closed or switched back to the cover
    requestAnimationFrame(draw);
    if (document.hidden) return;
    const dpr = window.devicePixelRatio || 1, w = Math.round(c.clientWidth * dpr), ht = Math.round(c.clientHeight * dpr);
    if (!w || !ht) return;
    if (c.width !== w || c.height !== ht) { c.width = w; c.height = ht; }
    FX.an.getByteFrequencyData(data);
    g.clearRect(0, 0, w, ht);
    const n = 36, gap = 3 * dpr, bw = (w - gap * (n - 1)) / n;
    g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#1ed760';
    for (let i = 0; i < n; i++) {
      const v = data[Math.min(data.length - 1, Math.floor(Math.pow(i / n, 1.5) * data.length * 0.7))] / 255;
      const bh = Math.max(4 * dpr, v * ht * 0.92);
      g.globalAlpha = 0.45 + 0.55 * v;
      g.beginPath(); g.roundRect ? g.roundRect(i * (bw + gap), ht - bh, bw, bh, bw / 2) : g.rect(i * (bw + gap), ht - bh, bw, bh); g.fill();
    }
    g.globalAlpha = 1;
  };
  requestAnimationFrame(draw);
  return c;
}
initFx();

// =====================================================================
// Search (library first, then YouTube Music / iTunes to add)
// =====================================================================
const SR = { q: '', tok: 0, addTo: null, timer: null, catTimer: null };
const TILE_COLORS = ['#dc148c', '#006450', '#1e3264', '#8400e7', '#e8115b', '#148a08', '#bc5900', '#477d95', '#503750', '#af2896', '#608108', '#8d67ab', '#b02897', '#0d73ec'];
const recentSearches = () => { try { return JSON.parse(localStorage.getItem('tr.searches') || '[]'); } catch (e) { return []; } };
function rememberSearch(q) {
  if (q.length < 2) return;
  const l = [q, ...recentSearches().filter(x => x.toLowerCase() !== q.toLowerCase())].slice(0, 8);
  try { localStorage.setItem('tr.searches', JSON.stringify(l)); } catch (e) { /* ignore */ }
}

function buildSearchRoot() {
  const root = roots.search;
  const input = h('input', { type: 'search', placeholder: 'What do you want to listen to?', autocomplete: 'off', enterkeyhint: 'search', id: 'searchInput' });
  const clear = h('button', { class: 'hidden', html: I.x, 'aria-label': 'Clear', onclick: () => { input.value = ''; onInput(); input.focus(); } });
  clear.firstChild.style.cssText = 'width:20px;height:20px';
  const banner = h('div', { id: 'addBanner' });
  const body = h('div', { id: 'searchBody' });
  const onInput = () => {
    SR.q = input.value.trim();
    clear.classList.toggle('hidden', !input.value);
    clearTimeout(SR.timer);
    SR.timer = setTimeout(runSearch, 300);
  };
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { clearTimeout(SR.timer); runSearch(); input.blur(); } });
  root.replaceChildren(
    h('div', { class: 'page-title' }, h('button', { class: 'avatar', onclick: openMenu }, 'T'), h('h1', {}, 'Search')),
    h('div', { class: 'searchbox' }, h('span', { html: I.search, style: 'display:flex' }), input, clear),
    banner, body);
  renderBanner();
  renderSearchIdle();
}
function renderBanner() {
  const b = $('#addBanner');
  if (!b) return;
  b.replaceChildren();
  if (SR.addTo) {
    const target = SR.addTo;
    b.append(h('div', { class: 'banner' }, 'Adding to ', h('b', {}, target.name),
      h('button', { style: 'color:var(--text);font-weight:700', onclick: () => { SR.addTo = null; renderBanner(); runSearch(); showTab('library'); whenSettled(() => openPlaylist(target.id)); } }, 'Done')));
  }
}
function startAddTo(pl) {
  SR.addTo = { id: pl.id, name: pl.name };
  renderBanner();
  showTab('search');
  $('#searchInput').focus();
}

async function renderSearchIdle() {
  const body = $('#searchBody');
  if (!body) return;
  const tok = ++SR.tok;
  const out = [];
  const recent = recentSearches();
  if (recent.length) {
    out.push(h('div', { class: 'rowsec' }, 'Recent searches'),
      ...recent.map(q => h('div', { class: 'row', style: 'padding-top:4px;padding-bottom:4px', onclick: () => { $('#searchInput').value = q; SR.q = q; $('#searchInput').dispatchEvent(new Event('input')); } },
        h('div', { class: 'txt' }, h('div', { class: 't' }, q)),
        h('button', { class: 'icon-btn dim', html: I.x, 'aria-label': 'Remove', onclick: e => { e.stopPropagation(); localStorage.setItem('tr.searches', JSON.stringify(recentSearches().filter(x => x !== q))); renderSearchIdle(); } }))));
  }
  out.push(h('div', { class: 'rowsec' }, 'Browse all'));
  const grid = h('div', { class: 'tiles' });
  const tile = (label, i, big, onclick) => h('button', { class: 'tile', style: `background:${TILE_COLORS[i % TILE_COLORS.length]}`, onclick },
    label, big && h('span', { class: 'big', html: I[big] }));
  let n = 0;
  grid.append(tile('Music', n++, 'note', () => openBrowse('Music', off => albumList('alphabeticalByName', 500, { musicFolderId: S.musicId, offset: off }))));
  if (S.podId) grid.append(tile('Podcasts', n++, 'mic', () => openBrowse('Podcasts', async off => podCanonical(await albumList('alphabeticalByName', 500, { musicFolderId: S.podId, offset: off })))));
  out.push(grid);
  body.replaceChildren(...out, h('div', { class: 'spacer' }));
  try {
    const genres = arr((await cached('genres', 600000, () => nd('getGenres'))).genres?.genre)
      .filter(g => g.value && g.value !== 'Podcasts' && g.albumCount > 0).sort((a, b) => b.albumCount - a.albumCount).slice(0, 14);
    if (tok !== SR.tok) return;
    genres.forEach(g => grid.append(tile(g.value, n++, null,
      () => openBrowse(g.value, off => albumList('byGenre', 500, { genre: g.value, offset: off })))));
  } catch (e) { /* genre tiles are optional */ }
}

async function runSearch() {
  const q = SR.q;
  const body = $('#searchBody');
  if (!body) return;
  if (q.length < 2) { renderSearchIdle(); return; }
  const tok = ++SR.tok;
  rememberSearch(q);
  const libBox = h('div', {}, spinner());
  const catBox = h('div', {});
  body.replaceChildren(libBox, catBox, h('div', { class: 'spacer' }));

  // Library
  (async () => {
    try {
      const [r, pls] = await Promise.all([nd('search3', { query: q, songCount: 20, albumCount: 10, artistCount: 8 }), getPlaylists()]);
      if (tok !== SR.tok) return;
      const songs = arr(r.searchResult3?.song), albums = podCanonical(arr(r.searchResult3?.album)), artists = arr(r.searchResult3?.artist);
      const lists = pls.filter(p => p.name.toLowerCase().includes(q.toLowerCase()));
      const out = [];
      if (songs.length) {
        out.push(h('div', { class: 'rowsec' }, 'Songs in your library'));
        songs.forEach((s, i) => out.push(songRow(s, SR.addTo
          ? { showAlbum: true, noMenu: true, onPlay: () => addToTargetPlaylist(s),
              right: h('button', { class: 'icon-btn', html: I.plus, 'aria-label': 'Add', onclick: e => { e.stopPropagation(); addToTargetPlaylist(s); } }) }
          : { showAlbum: true, onPlay: () => playQueue(songs, i, null) })));
      }
      if (lists.length) out.push(h('div', { class: 'rowsec' }, 'Playlists'), ...lists.map(p => h('button', { class: 'row', onclick: () => openPlaylist(p.id) },
        cover(p.coverArt, 'md', 120, { fallback: 'playlist' }), h('div', { class: 'txt' }, h('div', { class: 't' }, p.name), h('div', { class: 's' }, `Playlist • ${plural(p.songCount || 0, 'song')}`)))));
      if (artists.length) out.push(h('div', { class: 'rowsec' }, 'Artists'), ...artists.map(artistRow));
      if (albums.length) out.push(h('div', { class: 'rowsec' }, 'Albums'), ...albums.map(a => albumRow(a)));
      libBox.replaceChildren(...(out.length ? out : [h('div', { class: 'hint' }, `Nothing in your library matches “${q}”.`)]));
    } catch (e) { if (tok === SR.tok) libBox.replaceChildren(h('div', { class: 'hint bad' }, e.message)); }
  })();

  // Catalog (slower: asks YouTube Music and iTunes)
  catBox.replaceChildren(h('div', { class: 'rowsec' }, 'Add to your library', h('small', {}, 'YouTube Music & iTunes')), spinner());
  try {
    await new Promise(res => setTimeout(res, 500));
    if (tok !== SR.tok) return;
    const r = await fetch(api(`/api/search?q=${encodeURIComponent(q)}`));
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Search failed (${r.status})`);
    const d = await r.json();
    if (tok !== SR.tok) return;
    const songs = (d.songs || []).slice(0, 14), albums = (d.albums || []).slice(0, 6);
    const out = [h('div', { class: 'rowsec' }, 'Add to your library', h('small', {}, 'YouTube Music & iTunes'))];
    songs.forEach(s => out.push(catalogRow(s, 'song')));
    if (albums.length) out.push(h('div', { class: 'rowsec' }, 'Albums to add'), ...albums.map(a => catalogRow(a, 'album')));
    if (!songs.length && !albums.length) out.push(h('div', { class: 'hint' }, 'No online results.'));
    catBox.replaceChildren(...out);
  } catch (e) {
    if (tok === SR.tok) catBox.replaceChildren(h('div', { class: 'rowsec' }, 'Add to your library'), h('div', { class: 'hint bad' }, `Online search failed: ${e.message}`));
  }
}

async function addToTargetPlaylist(song) {
  const t = SR.addTo;
  if (!t) return;
  if (!await confirmNotDuplicate(t.id, t.name, [song])) return;
  try { await nd('updatePlaylist', { playlistId: t.id, songIdToAdd: song.id }); bust('pl'); toast(`Added “${song.title}” to ${t.name}`, 'ok', 2500); }
  catch (e) { toast(`Couldn’t add: ${e.message}`, 'bad'); }
}

const songPayload = r => ({ title: r.title, artist: r.artist, album: r.album || '', duration: r.duration, explicit: r.explicit,
  video_id: r.video_id || null, artwork: r.artwork || null, year: r.year || null });
const albumPayload = r => ({ title: r.title, artist: r.artist, ytm_browse_id: r.ytm_browse_id || null, itunes_id: r.itunes_id || null });

function catalogRow(r, kind) {
  const label = r.title;
  const add = (mode, name, id) => quickAdd(kind === 'song' ? { song: songPayload(r) } : { album: albumPayload(r) }, label, mode, name, id);
  const defaultAdd = () => (SR.addTo ? add('existing', SR.addTo.name, SR.addTo.id) : add('none'));
  const sub = [kind === 'album' ? 'Album' : 'Song', r.artist, kind === 'song' ? r.album : r.year].filter(Boolean).join(' • ');
  const right = r.in_library
    ? h('span', { class: 'added', html: I.check + '' , style: 'width:22px;height:22px;display:flex;align-items:center;margin-right:10px', title: 'In your library' })
    : h('button', { class: 'icon-btn', html: I.plus, 'aria-label': 'Add to library', onclick: e => { e.stopPropagation(); defaultAdd(); } });
  const row = h('div', { class: 'row', onclick: () => catalogSheet(r, kind, add) },
    cover(r.artwork ? 'x' : null, 'sm', 100, { url: r.artwork }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, r.title), h('div', { class: 's' }, r.explicit ? h('span', { style: 'background:#777;color:#000;font-size:10px;padding:0 4px;border-radius:2px;font-weight:700' }, 'E') : null, sub)),
    right);
  return row;
}
function catalogSheet(r, kind, add) {
  sheet({
    title: r.title, sub: [r.artist, r.album || r.year].filter(Boolean).join(' • '), coverEl: cover(r.artwork ? 'x' : null, 'md', 100, { url: r.artwork }),
    items: [
      SR.addTo && { icon: 'plus', label: `Add to “${SR.addTo.name}”`, run: () => add('existing', SR.addTo.name, SR.addTo.id) },
      { icon: 'plus', label: r.in_library ? 'Add to library again' : (kind === 'album' ? 'Add album to library' : 'Add to library'), run: () => add('none') },
      { icon: 'playlist', label: 'Add to playlist…', run: () => pickPlaylistTarget((mode, name, id) => add(mode, name, id)) },
      kind === 'song' && { icon: 'note', label: 'About this song', run: () => aboutSong(r) },
      kind === 'album' && { icon: 'disc', label: 'Open album', run: () => openOnlineAlbum({ browseId: r.ytm_browse_id, artist: r.artist, title: r.title }) },
      r.artist && { icon: 'user', label: 'Go to artist', run: () => openArtist({ name: r.artist }) },
    ],
  });
}
async function pickPlaylistTarget(cb) {
  const lists = await getPlaylists().catch(() => []);
  sheet({
    title: 'Add to playlist', coverEl: h('div', { class: 'cover md', html: I.playlist }),
    items: [
      { icon: 'plus', label: 'New playlist', run: async () => { const n = await promptDialog('Name your playlist', { placeholder: 'My playlist', ok: 'Create' }); if (n) cb('new', n, null); } },
      ...lists.map(p => ({ icon: 'note', label: p.name, run: () => cb('existing', p.name, p.id) })),
    ],
  });
}

async function quickAdd(payload, label, mode = 'none', name = null, playlistId = null, onDone = null) {
  const where = mode === 'none' ? '' : ` to “${name}”`;
  const t = toast(`Adding “${label}”${where}…`, '', 0);
  try {
    const resp = await fetch(api('/api/jobs/catalog'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, add: { playlist_mode: mode, playlist_name: name, playlist_id: playlistId } }) });
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || `Error ${resp.status}`);
    followQuickAdd((await resp.json()).id, label, t, onDone);
  } catch (e) { setToast(t, `Couldn’t add “${label}”: ${e.message}`, 'bad', 8000); if (onDone) onDone(false); }
}

async function followQuickAdd(jobId, label, t, onDone) {
  let job = null;
  for (let i = 0; i < 900; i++) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const resp = await fetch(api(`/api/jobs/${jobId}`));
      if (resp.status === 404) { setToast(t, `“${label}”: the server restarted, so it stopped.`, 'bad', 8000); if (onDone) onDone(false); return; }
      if (resp.ok) job = await resp.json();
    } catch (e) { continue; }
    if (!job) continue;
    const tracks = job.tracks.filter(x => x.status !== 'skipped_not_accepted');
    const finished = tracks.filter(x => ['done', 'skipped_exists', 'failed'].includes(x.status)).length;
    if (job.status === 'error') { setToast(t, `Couldn’t add “${label}”: ${job.error || 'lookup failed'}`, 'bad', 10000); if (onDone) onDone(false); return; }
    if (job.status === 'awaiting_confirmation' && job.needs_check && job.tracks.length === 1) {
      const m = job.tracks[0].match || {};
      setToast(t, (m.title ? `Not sure about “${label}” (found “${m.artist} — ${m.title}”).` : `Couldn’t find “${label}”.`) + (S.me.auth ? ' It needs checking from the home network.' : ' Tap to check it in the classic view.'), 'bad');
      if (!S.me.auth) t.onclick = () => { location.href = api('/classic'); };
      if (onDone) onDone(false);
      return;
    }
    if (job.status === 'done') {
      const saved = tracks.filter(x => x.status === 'done').length, had = tracks.filter(x => x.status === 'skipped_exists').length;
      const failed = tracks.filter(x => x.status === 'failed');
      const parts = [];
      if (tracks.length === 1) parts.push(failed.length ? `Couldn’t add “${label}”: ${failed[0].detail || 'download failed'}` : (had ? `“${label}” was already in your library.` : `✓ Added “${label}”.`));
      else {
        if (saved) parts.push(`✓ Added ${plural(saved, 'song')} from “${label}”.`);
        if (had) parts.push(`${had} already there.`);
        if (failed.length) parts.push(`${failed.length} failed.`);
      }
      if (job.needs_check) parts.push(`${job.needs_check} skipped (unsure match).`);
      if (job.playlist_error) parts.push(job.playlist_error); else if (job.playlist_status) parts.push(job.playlist_status);
      setToast(t, parts.join(' '), failed.length || job.playlist_error ? 'bad' : 'ok', 9000);
      bust('pl'); bust('allal'); bust('allar'); bust('al:'); bust('ar:'); bust('disc:'); S.homeDirty = true;
      if (onDone) onDone(!failed.length && !job.playlist_error);
      return;
    }
    setToast(t, tracks.length > 1 ? `Adding “${label}”… ${finished} of ${tracks.length}` : `Adding “${label}”…`);
  }
}

// =====================================================================
// Library
// =====================================================================
const L = (() => { try { return { chip: null, sort: 'recent', view: 'list', filter: '', showFilter: false, ...JSON.parse(localStorage.getItem('tr.lib') || '{}') }; } catch (e) { return { chip: null, sort: 'recent', view: 'list', filter: '', showFilter: false }; } })();
L.filter = ''; L.showFilter = false;
const saveL = () => { try { localStorage.setItem('tr.lib', JSON.stringify({ chip: L.chip, sort: L.sort, view: L.view })); } catch (e) { /* ignore */ } };
const SORTS = { recent: 'Recents', added: 'Recently added', alpha: 'A–Z' };
let libTok = 0;

async function libraryItems() {
  const need = (...t) => !L.chip || t.includes(L.chip);
  const [pls, pods, albums, artists] = await Promise.all([
    need('playlists') ? getPlaylists().catch(() => []) : [],
    need('podcasts') && S.podId ? allAlbums(S.podId).catch(() => []) : [],
    L.chip === 'albums' ? allAlbums(S.musicId).catch(() => []) : [],
    L.chip === 'artists' ? allArtists(S.musicId).catch(() => []) : [],
  ]);
  const items = [];
  pls.forEach(p => items.push({ type: 'playlist', id: p.id, name: p.name, sub: `Playlist • ${plural(p.songCount || 0, 'song')}`, cover: p.coverArt, ts: Date.parse(p.changed || p.created) || 0, cls: 'playlist' }));
  podCanonical(pods).forEach(a => items.push({ type: 'album', id: a.id, name: a.name, sub: `Podcast • ${a.artist || ''}`, cover: a.coverArt, ts: Date.parse(a.created) || 0, cls: 'podcast' }));
  albums.filter(a => !S.podAlbums.has(a.id)).forEach(a => items.push({ type: 'album', id: a.id, name: a.name, sub: `Album • ${a.artist || ''}`, cover: a.coverArt, ts: Date.parse(a.created) || 0, cls: 'album' }));
  artists.forEach(a => items.push({ type: 'artist', id: a.id, name: a.name, sub: 'Artist', cover: a.coverArt, ts: 0, cls: 'artist' }));
  // Without a filter chip: also whatever you played or pinned recently (albums, artists), not the whole catalogue.
  if (!L.chip) {
    [...S.pins, ...S.recents].forEach(r => {
      if ((r.type === 'album' || r.type === 'artist') && !items.some(i => keyOf(i) === keyOf(r))) {
        items.push({ type: r.type, id: r.id, name: r.name, sub: r.sub || (r.type === 'artist' ? 'Artist' : 'Album'), cover: r.cover, ts: 0, cls: r.type === 'album' && S.podAlbums.has(r.id) ? 'podcast' : r.type });
      }
    });
  }
  if (!L.chip || L.chip === 'playlists') {
    items.unshift({ type: 'liked', id: 'liked', name: 'Liked Songs', sub: `Playlist • ${plural(S.liked.size, 'song')}`, cover: null, ts: Infinity, cls: 'playlist' });
  }
  const recAt = new Map(S.recents.map(r => [keyOf(r), r.ts || 0]));
  const f = L.filter.trim().toLowerCase();
  let list = f ? items.filter(i => i.name.toLowerCase().includes(f)) : items;
  const pinnedKeys = new Set(S.pins.map(keyOf));
  list.forEach(i => { i.pinned = pinnedKeys.has(keyOf(i)) || i.type === 'liked'; });
  const by = {
    recent: (a, b) => (recAt.get(keyOf(b)) || 0) - (recAt.get(keyOf(a)) || 0) || b.ts - a.ts,
    added: (a, b) => b.ts - a.ts,
    alpha: (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
  }[L.sort];
  list = [...list.filter(i => i.pinned).sort((a, b) => (a.type === 'liked' ? -1 : b.type === 'liked' ? 1 : by(a, b))), ...list.filter(i => !i.pinned).sort(by)];
  return list;
}

function libRow(it) {
  const ico = it.cls === 'playlist' ? 'playlist' : it.type === 'artist' ? 'user' : 'note';
  const row = h('div', { class: 'row', onclick: () => openItem(it) },
    cover(it.cover, 'md', 120, { round: it.type === 'artist', liked: it.type === 'liked', fallback: ico }),
    h('div', { class: 'txt' }, h('div', { class: 't' }, it.name),
      h('div', { class: 's' }, it.pinned ? h('span', { class: 'pin', html: I.pin }) : null, it.sub)),
    it.type === 'liked' ? null : h('button', { class: 'icon-btn dim', html: I.more, 'aria-label': 'More', onclick: e => { e.stopPropagation(); itemSheet(it); } }));
  if (it.type !== 'liked') onLongPress(row, () => itemSheet(it));
  return row;
}
function libCard(it) {
  const c = h('button', { class: 'card' + (it.type === 'artist' ? ' round' : ''), onclick: () => openItem(it) },
    cover(it.cover, '', 300, { round: it.type === 'artist', liked: it.type === 'liked', fallback: it.type === 'artist' ? 'user' : 'note' }),
    h('div', { class: 'nm' }, it.pinned ? [h('span', { class: 'pin', style: 'color:var(--accent);display:inline-block;width:12px;height:12px;vertical-align:-1px;margin-right:4px', html: I.pin }), it.name] : it.name),
    h('div', { class: 'sb' }, it.sub));
  if (it.type !== 'liked') onLongPress(c, () => itemSheet(it));
  return c;
}

async function renderLibrary() {
  const root = roots.library;
  if (!root) return;
  if (S.offline) { renderOfflineRoot(root); return; }
  const tok = ++libTok;
  const scroll = root.scrollTop;
  const first = !root.children.length;
  if (first) root.append(h('div', { class: 'page-title' }, h('h1', {}, 'Your Library')), spinner());

  const chipDefs = [['playlists', 'Playlists'], ...(S.podId ? [['podcasts', 'Podcasts']] : []), ['albums', 'Albums'], ['artists', 'Artists'], ...(NATIVE_DL ? [['downloaded', 'Downloaded']] : [])];
  const chips = h('div', { class: 'chips', style: 'padding:0 16px 8px' }, chipDefs.map(([k, label]) =>
    h('button', { class: 'chip' + (L.chip === k ? ' on' : ''), onclick: () => { L.chip = L.chip === k ? null : k; saveL(); renderLibrary(); } }, label)));
  const header = h('div', { class: 'page-title' }, h('button', { class: 'avatar', onclick: openMenu }, 'T'), h('h1', {}, 'Your Library'),
    h('button', { class: 'icon-btn', html: I.search, 'aria-label': 'Filter', onclick: () => { L.showFilter = !L.showFilter; if (!L.showFilter) L.filter = ''; renderLibrary().then(() => { const i = $('#libFilter'); if (i) i.focus(); }); } }),
    h('button', { class: 'icon-btn', html: I.plus, 'aria-label': 'Create', onclick: () => toggleCreate() }));
  const filterBox = L.showFilter ? h('div', { class: 'filterbox' }, h('span', { html: I.search, style: 'display:flex' }),
    Object.assign(h('input', { id: 'libFilter', type: 'text', placeholder: 'Find in Your Library', value: L.filter }), {
      oninput: e => { L.filter = e.target.value; clearTimeout(libFilterTimer); libFilterTimer = setTimeout(renderLibraryKeepFocus, 150); } })) : null;
  const tools = h('div', { class: 'lib-tools' },
    h('button', { onclick: () => sheet({ title: 'Sort by', items: Object.entries(SORTS).map(([k, label]) => ({ icon: L.sort === k ? 'check' : 'sort', label, run: () => { L.sort = k; saveL(); renderLibrary(); } })) }) },
      h('span', { html: I.sort }), SORTS[L.sort]),
    h('div', { class: 'grow' }),
    h('button', { 'aria-label': 'Change view', html: L.view === 'list' ? I.grid : I.list, onclick: () => { L.view = L.view === 'list' ? 'grid' : 'list'; saveL(); renderLibrary(); } }));

  if (L.chip === 'downloaded') {
    const box = h('div', {});
    put(root, header, chips, box);
    renderDownloads(box, false);
    return;
  }
  let items;
  try { items = await libraryItems(); } catch (e) { items = null; }
  if (tok !== libTok) return;
  const body = items === null ? errBox('Couldn’t load your library.', renderLibrary)
    : !items.length ? h('div', { class: 'empty' }, L.filter ? 'No matches.' : 'Nothing here yet.')
    : L.view === 'grid' ? h('div', { class: 'lib-grid' }, items.map(libCard)) : h('div', {}, items.map(libRow));
  put(root, header, chips, filterBox, tools, body, h('div', { class: 'spacer' }));
  root.scrollTop = first ? 0 : scroll;
}
let libFilterTimer = null;
async function renderLibraryKeepFocus() {
  const had = document.activeElement && document.activeElement.id === 'libFilter';
  const pos = had ? document.activeElement.selectionStart : 0;
  await renderLibrary();
  const i = $('#libFilter');
  if (had && i) { i.focus(); try { i.setSelectionRange(pos, pos); } catch (e) { /* ignore */ } }
}

// =====================================================================
// Downloads (Android app) and the offline view
// =====================================================================
function renderDownloads(box, offline) {
  const draw = () => {
    if (!box.isConnected && box.dataset.live) { bus.removeEventListener('dl', draw); return; }
    box.dataset.live = '1';
    const items = [...S.dl.values()].sort((a, b) => (b.added || 0) - (a.added || 0));
    const done = items.filter(d => d.status === 'done');
    const songs = done.map(d => d.song);
    const row = d => {
      const status = d.status === 'done' ? fmtBytes(d.total || 0) : d.status === 'waiting' ? 'Waiting for Wi-Fi…' : d.status === 'failed' ? 'Failed: tap to retry' : `Downloading ${d.pct || 0}%`;
      return h('div', { class: 'row', 'data-sid': d.id, onclick: () => {
        if (d.status === 'done') playQueue(songs, songs.findIndex(x => x.id === d.id), null);
        else if (d.status === 'failed') startDownload(d.song);
      } },
        cover(d.cover ? 'x' : null, 'md', 120, { url: d.cover, fallback: 'note' }),
        h('div', { class: 'txt' }, h('div', { class: 't' }, d.song.title), h('div', { class: 's' }, [d.song.artist, status].filter(Boolean).join(' • ')),
          d.status !== 'done' && d.status !== 'failed' ? h('div', { class: 'ep-prog wide', style: 'margin:6px 0 0' }, h('div', { style: `width:${d.pct || 0}%` })) : null),
        h('button', { class: 'icon-btn dim', html: I.x, 'aria-label': 'Remove', onclick: e => { e.stopPropagation(); removeDownload(d.song); } }));
    };
    const cell = h('input', { type: 'checkbox', class: 'switch' });
    cell.checked = TRNative.dlCellular();
    cell.addEventListener('change', () => { TRNative.dlSetCellular(cell.checked); toast(cell.checked ? 'Downloads may use mobile data' : 'Downloads only on Wi-Fi', 'ok', 2500); });
    const rmSw = h('input', { type: 'checkbox', class: 'switch' });
    rmSw.checked = autoRm();
    rmSw.addEventListener('change', () => { lsSet('tr.autorm', rmSw.checked); toast(rmSw.checked ? 'Played episodes are removed from this phone' : 'Played episodes are kept', 'ok', 2500); });
    const used = TRNative.dlUsage(), free = TRNative.freeBytes ? TRNative.freeBytes() : 0;
    put(box,
      offline ? h('div', { class: 'banner' }, 'You’re offline. Your downloads are below.', h('button', { onclick: () => location.reload() }, 'Try again')) : null,
      h('div', { class: 'dl-head' }, h('div', {}, h('b', {}, plural(done.length, 'download')), ` • ${fmtBytes(TRNative.dlUsage())} on this phone`),
        items.length ? h('button', { class: 'pillbtn dark', onclick: async () => { if (await confirmDialog('Remove all downloads?', 'They are deleted from this phone. You can download them again later.', 'Remove all')) { TRNative.dlRemoveAll(); refreshDownloads(); } } }, 'Remove all') : null),
      free > 0 ? h('div', { class: 'meter' }, h('div', { class: 'meter-bar' }, h('div', { style: `width:${Math.min(100, Math.max(1, Math.round(used / (used + free) * 100)))}%` })), h('small', {}, `${fmtBytes(used)} used by downloads • ${fmtBytes(free)} free on this phone`)) : null,
      h('label', { class: 'dl-opt' }, h('span', {}, 'Download over mobile data'), cell),
      h('label', { class: 'dl-opt' }, h('span', {}, 'Remove episodes after I play them'), rmSw),
      items.length ? items.map(row) : h('div', { class: 'empty' }, 'Nothing downloaded yet. Tap the download arrow on an episode or song and it will be here, ready to play without a connection.'),
      h('div', { class: 'spacer' }));
  };
  draw();
  bus.addEventListener('dl', draw);
  refreshDownloads();
}

function renderOfflineRoot(root) {
  const box = h('div', {});
  put(root, h('div', { class: 'page-title' }, h('h1', {}, 'Offline')), box);
  renderDownloads(box, true);
}

// =====================================================================
// Create menu, Spotify import
// =====================================================================
let createOpen = false;
function toggleCreate() {
  if (createOpen) { closeTop(); return; }
  createOpen = true;
  const btn = $('.tabbtn[data-tab="create"]');
  btn.classList.add('create-open');
  btn.querySelector('.ic').innerHTML = I.x;
  const tabbarH = $('#tabbar').offsetHeight + ($('#mini').classList.contains('hidden') ? 0 : $('#mini').offsetHeight + 6);
  const mk = (ico, title, sub, run) => h('button', { class: 'item', onclick: () => closeThen(run) },
    h('span', { class: 'circ', html: I[ico] }), h('div', {}, h('div', { class: 't' }, title), h('div', { class: 's' }, sub)));
  const box = h('div', { class: 'create-sheet', style: `bottom:${tabbarH + 8}px` },
    mk('note', 'Playlist', 'Create a playlist with songs or episodes', createPlaylistFlow),
    mk('addsong', 'Add song or album', 'Search YouTube Music and add it to your library', () => { SR.addTo = null; renderBanner(); showTab('search'); $('#searchInput').focus(); }),
    mk('upload', 'Import Spotify playlist', 'Upload an Exportify .csv file', importFlow));
  const el = h('div', { style: `position:absolute;inset:0;bottom:${$('#tabbar').offsetHeight}px` }, h('div', { class: 'scrim', onclick: () => closeTop() }), box);
  box.style.bottom = (tabbarH - $('#tabbar').offsetHeight) + 8 + 'px';
  openLayer(el, () => { createOpen = false; btn.classList.remove('create-open'); btn.querySelector('.ic').innerHTML = I.tabCreate; });
}

async function createPlaylistFlow() {
  const name = await promptDialog('Name your playlist', { placeholder: 'My playlist', ok: 'Create' });
  if (!name) return;
  try {
    const body = await nd('createPlaylist', { name });
    bust('pl'); S.homeDirty = true;
    const pl = body.playlist || (await getPlaylists()).find(p => p.name === name);
    if (pl) startAddTo(pl); else { showTab('library'); }
  } catch (e) { toast(`Couldn’t create the playlist: ${e.message}`, 'bad'); }
}

let importPoll = null;
function importFlow() {
  dialog(async box => {
    const nameIn = h('input', { type: 'text', placeholder: 'Playlist name', maxlength: 120 });
    const file = h('input', { type: 'file', accept: '*/*', style: 'margin-bottom:12px;width:100%' });
    const sel = h('select', {}, h('option', { value: '' }, 'Create a new playlist'));
    const note = h('p', { class: 'note' }, 'Export your playlist from Spotify with Exportify, then choose the .csv here. Songs are found on YouTube Music and added to your library; this can take a while for long playlists and keeps running if you close this.');
    const go = h('button', { class: 'pillbtn', onclick: async () => {
      if (!file.files[0]) { toast('Choose a .csv file first.', 'bad', 3000); return; }
      const name = nameIn.value.trim() || file.files[0].name.replace(/\.csv$/i, '');
      go.disabled = true;
      try {
        const text = await file.files[0].text();
        const r = await fetch(api('/api/app/import'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ csv: text, name, playlist_id: sel.value || null }) });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || `Error ${r.status}`);
        try { localStorage.setItem('tr.import', d.id); } catch (e) { /* ignore */ }
        closeTop();
        whenSettled(() => followImport(d.id));
      } catch (e) { toast(`Couldn’t start the import: ${e.message}`, 'bad'); go.disabled = false; }
    } }, 'Import');
    file.addEventListener('change', () => { if (!nameIn.value && file.files[0]) nameIn.value = file.files[0].name.replace(/\.csv$/i, ''); });
    box.append(h('h3', {}, 'Import Spotify playlist'), note, file, nameIn, sel,
      h('div', { class: 'btns' }, h('button', { class: 'pillbtn dark', onclick: () => closeTop() }, 'Cancel'), go));
    (await getPlaylists().catch(() => [])).forEach(p => sel.append(h('option', { value: p.id }, `Add to “${p.name}”`)));
    sel.addEventListener('change', () => { if (sel.value) nameIn.value = sel.options[sel.selectedIndex].text.replace(/^Add to “|”$/g, ''); });
  });
}

function followImport(id) {
  clearInterval(importPoll);
  const t = toast('Importing…', '', 0);
  let last = null;
  t.onclick = () => { if (last) showImportDetails(last); };
  const tick = async () => {
    try {
      const r = await fetch(api(`/api/app/import/${id}`));
      if (r.status === 404) { clearInterval(importPoll); setToast(t, 'The import stopped (the server restarted).', 'bad', 8000); try { localStorage.removeItem('tr.import'); } catch (e) { /* ignore */ } return; }
      const d = await r.json();
      last = d;
      if (d.status === 'running') { setToast(t, `Importing “${d.name}”: ${d.done} of ${d.total}${d.current ? ' — ' + d.current : ''}`); return; }
      clearInterval(importPoll);
      try { localStorage.removeItem('tr.import'); } catch (e) { /* ignore */ }
      const c = d.counts;
      setToast(t, `Import finished: ${c.added} added, ${c.already} already there${d.problems.length ? `, ${d.problems.length} need attention` : ''}. Tap for details.`, d.problems.length || d.status === 'error' ? 'bad' : 'ok');
      t.onclick = () => showImportDetails(d);
      bust('pl'); bust('allal'); bust('allar'); S.homeDirty = true;
      if (curTab === 'library') renderLibrary();
    } catch (e) { /* transient: try again next tick */ }
  };
  importPoll = setInterval(tick, 3000);
  tick();
}
function showImportDetails(d) {
  dialog(box => {
    const c = d.counts;
    put(box, h('h3', {}, `Import: ${d.name}`),
      h('div', { class: 'bar' }, h('div', { style: `width:${d.total ? Math.round((d.done / d.total) * 100) : 0}%` })),
      h('div', { class: 'stat' }, 'Added', h('b', {}, c.added)), h('div', { class: 'stat' }, 'Already in library', h('b', {}, c.already)),
      h('div', { class: 'stat' }, 'Needs a look', h('b', {}, c.review)), h('div', { class: 'stat' }, 'Not found', h('b', {}, c.nomatch)), h('div', { class: 'stat' }, 'Failed', h('b', {}, c.failed)),
      d.playlist ? h('p', { class: 'note', style: 'margin-top:10px' }, d.playlist) : null,
      d.problems.length ? h('ul', {}, d.problems.slice(0, 30).map(p => h('li', {}, `${p.song}${p.result === 'nomatch' ? ' (not found)' : p.detail ? ` (${p.detail})` : ''}`))) : null,
      h('div', { class: 'btns' }, h('button', { class: 'pillbtn', onclick: () => closeTop() }, 'Close')));
  });
}

// =====================================================================
// Tabs + boot
// =====================================================================
function showTab(name) {
  if (name === 'create') { toggleCreate(); return; }
  if (createOpen) { closeTop(); whenSettled(() => showTab(name)); return; }
  const same = name === curTab;
  closeAllLayers();
  curTab = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + name));
  document.querySelectorAll('.tabbtn[data-tab]').forEach(b => { if (b.dataset.tab !== 'create') b.classList.toggle('active', b.dataset.tab === name); });
  // closeAllLayers removes the pages; drop any stragglers right away too.
  document.querySelectorAll('.tab').forEach(t => [...t.children].slice(1).forEach(p => p.remove()));
  if (name === 'home' && (S.homeDirty || Date.now() - lastHome > 30000)) renderHome();
  if (name === 'library') renderLibrary();
  if (same) roots[name].scrollTo({ top: 0, behavior: 'smooth' });
}

async function boot() {
  const icons = { home: I.home, search: I.search, library: I.library, create: I.tabCreate };
  document.querySelectorAll('.tabbtn').forEach(b => {
    b.querySelector('.ic').innerHTML = icons[b.dataset.tab];
    b.addEventListener('click', () => showTab(b.dataset.tab));
  });
  ['home', 'search', 'library'].forEach(n => { roots[n] = h('div', { class: 'page' }); $('#tab-' + n).append(roots[n]); });
  $('#tab-home').classList.add('active');
  $('.tabbtn[data-tab="home"]').classList.add('active');
  roots.home.append(topBar(false), skelHome());
  pullRefresh(roots.home, $('#tab-home'), async () => { await refreshAll(); await renderHome(); });
  pullRefresh(roots.library, $('#tab-library'), async () => { await refreshAll(); await renderLibrary(); });

  S.bookmarks = new Map(lsGet('tr.bm', []));   // what we knew last time, in case the server can't be reached
  try { S.me = await nativeFetch(api('/api/auth/me')).then(r => r.json()); } catch (e) { /* keep the default */ }
  const [folders, state, starred] = await Promise.allSettled([
    nd('getMusicFolders'),
    fetch(api('/api/app/state')).then(r => r.json()),
    getStarred(),
  ]);
  if (folders.status === 'rejected') {
    refreshDownloads();
    if (NATIVE_DL && S.dl.size) {   // no server, but there is music on the phone: offline mode
      S.offline = true; S.ready = true;
      const local = lsGet('tr.state', null); if (local) { S.finished = new Set(local.finished || []); S.pins = local.pins || []; S.recents = local.recents || []; }
      S.bookmarks = new Map(lsGet('tr.bm', []));
      roots.search.append(h('div', { class: 'empty' }, 'Search needs a connection.'));
      roots.library && renderOfflineRoot(roots.library);
      restoreSession(); renderMini(); renderOfflineRoot(roots.home);
      return;
    }
    roots.home.replaceChildren(topBar(false), errBox(`Can’t reach Navidrome: ${folders.reason.message}`, () => location.reload()));
    return;
  }
  const fl = arr(folders.value.musicFolders?.musicFolder);
  const pod = fl.find(f => /podcast/i.test(f.name));
  S.podId = pod ? pod.id : null;
  S.musicId = (fl.find(f => f !== pod) || {}).id ?? null;
  const local = lsGet('tr.state', null);
  if (state.status === 'fulfilled' && !(local && lsGet('tr.stateDirty', 0))) { S.pins = state.value.pins || []; S.recents = state.value.recents || []; S.finished = new Set(state.value.finished || []); S.finishedAt = state.value.finishedAt || {}; lsSet('tr.state', { pins: S.pins, recents: S.recents, finished: [...S.finished], finishedAt: S.finishedAt }); }
  else if (local) { S.pins = local.pins || []; S.recents = local.recents || []; S.finished = new Set(local.finished || []); S.finishedAt = local.finishedAt || {}; if (state.status === 'fulfilled') saveState(); }  // changes made offline go back up
  if (starred.status === 'fulfilled') S.liked = new Set(starred.value.map(s => s.id));
  refreshMembership();
  if (S.podId) { try { const pa = await allAlbums(S.podId); S.podAlbums = new Set(pa.map(a => a.id)); buildPodGroups(pa); } catch (e) { /* podcasts just won't be recognised */ } }
  S.ready = true;
  try { S.abs = !!(await (await nativeFetch(api('/api/podcast/capabilities'))).json()).abs; } catch (e) { /* not connected */ }
  if (S.abs) refreshBookmarks().then(() => absPull(true)).catch(() => {});
  watchLibrary();
  if (NATIVE_DL) { refreshDownloads(); autoDownloadPass(); }
  buildSearchRoot();
  restoreSession();
  renderMini();
  await renderHome();
  const pending = (() => { try { return localStorage.getItem('tr.import'); } catch (e) { return null; } })();
  if (pending) followImport(pending);
}

// Browser tests only (localStorage 'tr.debug'): expose the pieces they drive
if (localStorage.getItem('tr.debug')) Object.assign(window, { P, S, FX, cur, playQueue, onlineSong, nd, arr, api, lsSet, closeAllLayers });
boot();
})();
