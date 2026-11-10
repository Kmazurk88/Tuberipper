# Deploying tuberipper

tuberipper is one Docker container: FastAPI backend + a phone-friendly web page. No database, no login. It writes songs into a folder (your music library) that a player such as [Navidrome](https://www.navidrome.org/) reads.

> **There is no login.** Never expose it to the public internet. Use it on your home network or through a VPN such as Tailscale/WireGuard.

## Quick start: GitHub + Unraid (step by step)

**A. Put the code on your GitHub (one time)**

1. Create a free account at github.com, then **New repository** (name it `tuberipper`, any visibility). Leave "Add a README" unticked.
2. Unzip this project. On the new repo's page click **uploading an existing file**, drag in **all the contents** of the unzipped folder (including the hidden `.github` folder), and commit to the branch `main`/`master`. If your default branch is called `main`, open `.github/workflows/docker-publish.yml` and change `branches: [master]` to `branches: [main]`.
   (Or with git: `git init -b master`, `git add .`, `git commit -m init`, `git remote add origin <your repo url>`, `git push -u origin master`.)
3. Open the repo's **Actions** tab. A run called "Build and publish Docker image" starts by itself (about 3-5 minutes). Wait for the green check. If it asks you to enable workflows, click enable.
4. Make the image downloadable by Unraid: on your GitHub profile open **Packages -> tuberipper -> Package settings -> Change visibility -> Public**. (Public is fine: it contains no passwords, only the app. This skips needing a login on the server.)

**B. Run it on Unraid**

1. Unraid web UI -> **Docker** tab -> **Add Container**.
2. Fill in:
   - **Name:** `tuberipper`
   - **Repository:** `ghcr.io/<your-github-username>/tuberipper:latest` (username in lowercase)
   - **Network Type:** Bridge
3. Click **Add another Path, Port, Variable...** and add:
   - **Port:** Container Port `8000`, Host Port `8000`
   - **Path:** Container Path `/music`, Host Path = the share your music server reads, e.g. `/mnt/user/Music` (Access mode: Read/Write)
   - **Variable:** Key `TUBERIPPER_OUTPUT_DIR`, Value `/music`
   - Optional, for Navidrome playlists: Variable `NAVIDROME_URL` = `http://<unraid-ip>:4533`, plus `NAVIDROME_USER` and `NAVIDROME_PASSWORD`.
4. Click **Apply**. When it's running, open `http://<unraid-ip>:8000/` on your phone or computer (same Wi-Fi). Add it to your phone's home screen if you like.
5. Check it works: `http://<unraid-ip>:8000/api/health` should say ok.

**C. Updating later**

Push changes to GitHub, wait for the green check in Actions, then on Unraid's Docker tab click the tuberipper icon -> **Force update** (or use "Check for updates"). The image is also rebuilt every Monday to pick up the newest yt-dlp, so force-update now and then if YouTube downloads start failing.

**Tips:** if downloads fail with "permission denied", make sure the music share allows writing (Unraid's default `nobody` user works; in the container add Extra Parameters `--user 99:100` if needed). Don't expose port 8000 to the internet; there is no login. Use Tailscale/WireGuard to reach it away from home.


## Other ways to run it

### 1. Get the image

**Option A: build it yourself**

```
docker build -t tuberipper .
```

**Option B: let GitHub build it for you.** Put this folder in your own GitHub repo (branch `master`). `.github/workflows/docker-publish.yml` runs the tests and publishes `ghcr.io/<your-github-user>/tuberipper:latest` on every push (and weekly, to pick up the newest yt-dlp). If the package is private, create a Personal Access Token with `read:packages` and log in on the server: `docker login ghcr.io`.

### 2. Run it (plain Docker)

```
docker run -d --name tuberipper --restart unless-stopped \
  -p 8000:8000 \
  -v /path/to/your/music:/music \
  -e TUBERIPPER_OUTPUT_DIR=/music \
  tuberipper
```

Then open `http://<server-ip>:8000/`. Health check: `http://<server-ip>:8000/api/health`.

- `/path/to/your/music` is the folder your music player reads. Songs are filed as `Artist/Album/NN - Title.m4a`.
- Docker UIs (Portainer, Unraid, Synology, TrueNAS...) work the same: image, port 8000, one host-path volume mapped to `/music`, env var `TUBERIPPER_OUTPUT_DIR=/music`. Make sure the container's user can write to that folder.
- To update: pull the new image and restart/recreate the container. Docker UIs often don't notice a new image behind the same `latest` tag, so pull manually.

## 3. Optional settings (environment variables)

| Variable | What it does |
|---|---|
| `TUBERIPPER_OUTPUT_DIR` | Library folder inside the container (default `/music`). |
| `TUBERIPPER_CACHE_DIR` | Where lookup caches live (default `<library>/.tuberipper`). |
| `NAVIDROME_URL` | e.g. `http://<server-ip>:4533`. Enables Navidrome playlists and library scans. |
| `NAVIDROME_USER` / `NAVIDROME_PASSWORD` | The Navidrome account whose playlists are listed/edited. |

Without Navidrome: "New playlist" writes `<library>/Playlists/<name>.m3u8`, which Navidrome imports on its next scan.

In the page's Settings you can set a Home URL and a Remote URL (e.g. your VPN address); the app picks the faster one automatically.

## 4. Running outside Docker

Needs Python 3.12, `ffmpeg`, and [Deno](https://deno.com/) (yt-dlp needs a JavaScript runtime for YouTube).

```
pip install -r requirements.txt
export TUBERIPPER_OUTPUT_DIR=~/Music        # Windows: set TUBERIPPER_OUTPUT_DIR=D:\Music
python -m uvicorn tuberipper.server:app --host 0.0.0.0 --port 8000
```

## How it finds metadata

1. **Deezer** (no key, fast): album, track/disc number, cover, genre, date. The page fills in right away.
2. **MusicBrainz** (~1 request/s) then picks the *original* album (not a compilation or reissue) and provides the ids written into the tags. **Pick a different album** lists the alternatives.
3. iTunes is the fallback; lyrics come from LRCLIB (synced lyrics are saved only when their timing matches the audio).

Audio: the YouTube Music "Topic" upload matching the official length is preferred over the music video. Songs are saved as `.m4a` (YouTube's AAC copied as-is). Tags include MusicBrainz ids so Navidrome groups albums correctly. Lookups are cached in SQLite (30 days).

## Notes

- YouTube sometimes blocks datacenter/VPS IP addresses (searches return nothing, downloads give HTTP 403). A home connection works best.
- Previews of unplayed songs take ~15 s to start: YouTube delays its stream links; the player shows a countdown.
- Tests: `pip install pytest httpx` then `python -m pytest -q tests` (offline, needs ffmpeg).
