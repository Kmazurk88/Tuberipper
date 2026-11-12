import sys, time, requests
B, jid = "http://tr-test:8000", sys.argv[1]
end = time.time() + 300
while time.time() < end:
    try:
        j = requests.get(f"{B}/api/jobs/{jid}", timeout=10)
        if j.status_code == 404: print("JOB LOST after restart"); sys.exit(1)
        j = j.json()
        if j["status"] in ("done", "error"):
            print("final:", j["status"], j["tracks"][0]["status"], j["tracks"][0]["detail"]); sys.exit(0)
    except requests.RequestException:
        pass
    time.sleep(1)
print("TIMEOUT"); sys.exit(1)
