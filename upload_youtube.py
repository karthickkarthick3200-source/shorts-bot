import json, os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

meta = json.load(open("out/meta.json"))
creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                    scopes=["https://www.googleapis.com/auth/youtube.upload"])
yt = build("youtube", "v3", credentials=creds)
body = {"snippet": {"title": meta["title"], "description": meta["description"],
                    "tags": meta["tags"], "categoryId": "27"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
req = yt.videos().insert(part="snippet,status", body=body,
                         media_body=MediaFileUpload("out/final.mp4", mimetype="video/mp4", resumable=True))
resp = None
while resp is None:
    status, resp = req.next_chunk()
print("YouTube uploaded:", resp["id"])
