#!/bin/bash
# Daily "can TubeRipper still download?" check. Runs the steps a real add does, inside the live container
# (same yt-dlp/Deno/ffmpeg): YouTube Music search -> pick the audio stream (Opus preferred) -> fetch the first
# bytes of it. Silent when fine. Discord message when it starts failing (once, not every day) and when it
# recovers. A failure must repeat 10 minutes later before anyone is told (filters out a network blip).
# TR_CHECK_VIDEO=<id> tests the failure path with another video; TR_CHECK_NOTIFY=0 never posts.
set -u
STATE=/mnt/user/appdata/tuberipper/download-check.state
LOG=/mnt/user/appdata/tuberipper/download-check.log
NOTIFY=/mnt/user/appdata/notify/discord-notify.sh
VIDEO=${TR_CHECK_VIDEO:-dQw4w9WgXcQ}

probe() {
timeout 180 docker exec -i -e V="$VIDEO" tuberipper python - <<'PY' 2>&1
import os, sys, requests, yt_dlp
v = os.environ["V"]
try:
    from ytmusicapi import YTMusic
    if not YTMusic().search("Rick Astley Never Gonna Give You Up", filter="songs", limit=3):
        sys.exit("YouTube Music search returned nothing")
except SystemExit:
    raise
except Exception as e:
    sys.exit(f"YouTube Music search failed: {type(e).__name__}: {str(e)[:150]}")
import glob, tempfile
d = tempfile.mkdtemp(prefix="trcheck-")
try:
    # the real thing: the same download call a new song goes through (a 3 MB song, kept in /tmp only)
    from tuberipper import downloader
    path = downloader.download_audio({"id": v, "url": f"https://www.youtube.com/watch?v={v}"}, d)
    size = os.path.getsize(path)
    if size < 100_000:
        sys.exit(f"the download came back too small ({size} bytes)")
    print(f"ok: {os.path.splitext(path)[1]} {size // 1024} KB, yt-dlp {yt_dlp.version.__version__}")
except Exception as e:
    sys.exit(f"the download failed: {str(e)[:200]}")
finally:
    for f in glob.glob(d + "/*"):
        os.remove(f)
    os.rmdir(d)
PY
}
say() { [ "${TR_CHECK_NOTIFY:-1}" = 1 ] && [ -x "$NOTIFY" ] && "$NOTIFY" "$1" >/dev/null 2>&1; return 0; }

out=$(probe); rc=$?
if [ $rc -ne 0 ]; then sleep "${TR_CHECK_WAIT:-600}"; out=$(probe); rc=$?; fi
last=$(echo "$out" | tr '\r' '\n' | grep -v "^\[download\]" | grep . | tail -1 | cut -c1-220)
echo "$(date '+%F %T') rc=$rc $last" >> "$LOG"; tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

prev=$(cat "$STATE" 2>/dev/null || echo ok)
if [ $rc -eq 0 ]; then
  [ "$prev" = fail ] && say "TubeRipper downloads are working again. $last"
  echo ok > "$STATE"
else
  [ "$prev" != fail ] && say "TubeRipper downloads are FAILING (checked twice, 10 min apart): $last. New songs may not arrive. The weekly update (Monday 04:45) usually fixes YouTube changes; or ask Claude to look."
  echo fail > "$STATE"
fi
exit 0
