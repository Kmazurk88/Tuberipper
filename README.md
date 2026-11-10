# tuberipper

Self-hosted web app (FastAPI + a single-page phone UI) that adds songs to a Navidrome music library from YouTube, with proper metadata, cover art and synced lyrics. Runs as a Docker container on any home server (NAS, Raspberry Pi, VPS) — see [DEPLOYMENT.md](DEPLOYMENT.md).

## How a download works

1. **Search** the music catalogs (YouTube Music + iTunes) and pick a song or album — or paste a YouTube video/playlist link (or use the YouTube tab).
2. **Look up** each song on MusicBrainz (iTunes fallback): album, album artist, track/disc number, original year, official length.
3. **Find the audio**: the YouTube upload whose length matches the official length — normally the album-audio "Topic" upload, not the music video (`tuberipper/sourcing.py`).
4. **Confirm**: pick the album, see which audio will be used per song (switch to your own video if you want), choose a playlist option.
5. **Download, tag, file** as `Artist/Album/NN - Title.m4a` (YouTube's AAC audio kept as-is, no re-encode), fetch lyrics from LRCLIB (synced only when the timing matches the audio), optionally add to a Navidrome playlist.

## Tests

```
pip install -r requirements.txt pytest httpx
python -m pytest -q tests
```

Offline (every network call is faked); needs `ffmpeg` on PATH. CI runs them before every image build.
