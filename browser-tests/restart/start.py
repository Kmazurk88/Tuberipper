# Starts a quick add on tr-test and returns as soon as its song is downloading (or later), printing the job id.
import sys, time, requests
B = "http://tr-test:8000"
song = {"title": "Never Gonna Give You Up", "artist": "Rick Astley", "album": "Whenever You Need Somebody", "duration": 213,
        "video_id": "dQw4w9WgXcQ", "explicit": False, "artwork": "", "year": "1987"}
job = requests.post(f"{B}/api/jobs/catalog", json={"song": song, "add": {"playlist_mode": "none", "notify": False}}, timeout=60).json()
jid = job["id"]
end = time.time() + 180
while time.time() < end:
    j = requests.get(f"{B}/api/jobs/{jid}", timeout=10).json()
    st = j["tracks"][0]["status"]
    if j["status"] == "processing" and st in ("queued_download", "downloading", "tagging", "fetching_lyrics", "done"):
        print(jid, j["status"], st, flush=True); sys.exit(0)
    time.sleep(0.2)
print(jid, "TIMEOUT", j["status"], st); sys.exit(1)
