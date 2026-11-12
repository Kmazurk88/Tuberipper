import sys, time, requests
B, iid = "http://tr-test:8000", sys.argv[1]
end = time.time() + 900
while time.time() < end:
    try:
        r = requests.get(f"{B}/api/app/import/{iid}", timeout=60)
        if r.status_code == 404: print("IMPORT LOST"); sys.exit(1)
        s = r.json()
        if s["status"] != "running":
            print("final:", s["status"], s["done"], "/", s["total"], s["counts"], s["problems"], s["playlist"]); sys.exit(0)
    except requests.RequestException:
        pass
    time.sleep(2)
print("TIMEOUT"); sys.exit(1)
