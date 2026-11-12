import sys, time, requests
B = "http://tr-test:8000"
csv = "Track Name,Artist Name(s),Duration (ms)\nNever Gonna Give You Up,Rick Astley,213573\nTake On Me,a-ha,225280\nTogether Forever,Rick Astley,205000\n"
imp = requests.post(f"{B}/api/app/import", json={"csv": csv, "name": "Restart test"}, timeout=60).json()
end = time.time() + 240
while time.time() < end:
    s = requests.get(f"{B}/api/app/import/{imp['id']}", timeout=60).json()
    if s["done"] >= 1:
        print(imp["id"], s["done"], s["counts"], flush=True); sys.exit(0)
    time.sleep(0.3)
print(imp["id"], "TIMEOUT"); sys.exit(1)
