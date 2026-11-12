#!/bin/bash
# Build TubeRipper and put it live on both instances (main + mom), safely.
#   deploy.sh            after a code change: quick build, refuses to restart while a download is running
#   deploy.sh --refresh  weekly (cron): full rebuild with the newest yt-dlp/Deno/ffmpeg; waits until nobody is
#                        listening and no download runs; tells Discord what happened
# Either way: tests must pass on the new image first, and if an instance does not come back healthy the
# previous image is put back.
set -u
BUILD=/mnt/user/appdata/tuberipper/build
PROJECTS=/boot/config/plugins/compose.manager/projects
NOTIFY=/mnt/user/appdata/notify/discord-notify.sh
PORTS="8765 8766"
REFRESH=0; [ "${1:-}" = "--refresh" ] && REFRESH=1

exec 9>/tmp/tuberipper-deploy.lock
flock -n 9 || { echo "another deploy is running"; exit 1; }

say()  { echo "$(date '+%F %T') $*"; }
tell() { say "$*"; [ $REFRESH = 1 ] && [ -x $NOTIFY ] && $NOTIFY "TubeRipper update: $*" >/dev/null 2>&1; return 0; }
fail() { tell "FAILED, nothing changed. $*"; exit 1; }
ytv()  { docker run --rm --entrypoint python "$1" -c "import yt_dlp.version as v; print(v.__version__)" 2>/dev/null; }
busy() { # prints why a restart would hurt right now; empty = free
  for p in $PORTS; do
    b=$(curl -s --max-time 5 localhost:$p/api/busy) || continue        # not running: nothing to interrupt
    case "$b" in
      *'"job":true'*) echo "a download is running on :$p"; return;;
      *'"streams":0'*) ;;
      *'"streams":'*) [ $REFRESH = 1 ] && { echo "someone is listening on :$p"; return; };;
      *) [ "$(curl -s --max-time 5 localhost:$p/api/jobs/active)" = "null" ] || { echo "a download is running on :$p"; return; };;
    esac
  done
}
up() {
  (cd $PROJECTS/arr && docker compose -p arr -f compose.yaml up -d tuberipper) >/dev/null 2>&1
  (cd $PROJECTS/music-mom && docker compose -p music-mom -f compose.yaml up -d tuberipper-mom) >/dev/null 2>&1
}
healthy() {
  for _ in $(seq 30); do
    ok=1; for p in $PORTS; do curl -sf --max-time 5 localhost:$p/api/health >/dev/null || ok=0; done
    [ $ok = 1 ] && return 0; sleep 3
  done; return 1
}

# 1. build the candidate (the live image is untouched until step 4)
flags="-q"; [ $REFRESH = 1 ] && flags="-q --pull --no-cache"
docker build $flags -t tuberipper:candidate "$BUILD" >/dev/null || fail "The image did not build."

# 2. it must pass the tests, and must not be worse at reaching YouTube than what is live
docker run --rm --user 99:100 -e HOME=/tmp -e PIP_TARGET=/tmp/pp -e PYTHONPATH=/tmp/pp \
  -v $BUILD/tests:/app/tests:ro -w /app tuberipper:candidate \
  sh -c "pip install -q pytest httpx ruff 2>/dev/null; python -m ruff check --no-cache --select E9,F63,F7,F82,F811 tuberipper tests && python -m pytest -q -p no:cacheprovider tests" >/tmp/tuberipper-deploy-tests.log 2>&1 \
  || fail "Lint or tests failed on the new image (/tmp/tuberipper-deploy-tests.log)."   # ruff: real mistakes only (syntax errors, undefined names), not style
if [ $REFRESH = 1 ]; then
  probe='yt-dlp -q --no-warnings --simulate -f bestaudio --print id "ytsearch1:rick astley never gonna give you up"'
  if ! docker run --rm tuberipper:candidate sh -c "$probe" >/dev/null 2>&1 \
     && docker run --rm tuberipper:local sh -c "$probe" >/dev/null 2>&1; then
    fail "The new yt-dlp cannot reach YouTube but the current one can; keeping the current one."
  fi
fi
old=$(ytv tuberipper:local); new=$(ytv tuberipper:candidate)

# 3. never restart in the middle of a download (or, for the weekly run, someone's music)
tries=1; [ $REFRESH = 1 ] && tries=144          # weekly: keep checking every 5 min for up to 12 h
for i in $(seq $tries); do
  why=$(busy); [ -z "$why" ] && break
  [ $i = $tries ] && fail "Gave up: $why."
  sleep 300
done

# 4. swap, check, roll back if needed
docker tag tuberipper:local tuberipper:previous
docker tag tuberipper:candidate tuberipper:local
up
if healthy; then
  docker rmi tuberipper:candidate >/dev/null 2>&1
  if [ "$old" != "$new" ]; then tell "done. yt-dlp $old -> $new."; else say "done (yt-dlp $new, unchanged)."; fi
else
  docker tag tuberipper:previous tuberipper:local; up
  healthy && fail "The new version did not start; the previous one is running again." \
          || fail "The new version did not start and the previous one is not healthy either. Needs a look."
fi
