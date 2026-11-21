import json, os, sys, time, requests

tok = os.environ.get("IG_ACCESS_TOKEN"); uid = os.environ.get("IG_USER_ID")
if not tok or not uid:
    print("Instagram not configured, skipping."); sys.exit(0)
meta = json.load(open("out/meta.json"))
G = "https://graph.facebook.com/v21.0"

r = requests.post(f"{G}/{uid}/media", data={"media_type": "REELS", "video_url": os.environ["VIDEO_URL"],
                  "caption": meta["caption"], "share_to_feed": "true", "access_token": tok}, timeout=60)
if not r.ok: raise SystemExit(r.text)
cid = r.json()["id"]
for _ in range(60):
    s = requests.get(f"{G}/{cid}", params={"fields": "status_code", "access_token": tok}, timeout=30).json()
    if s.get("status_code") == "FINISHED": break
    if s.get("status_code") == "ERROR": raise SystemExit(f"IG processing error: {s}")
    time.sleep(10)
else:
    raise SystemExit("IG processing timed out")
p = requests.post(f"{G}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60)
if not p.ok: raise SystemExit(p.text)
print("Instagram published:", p.json())
