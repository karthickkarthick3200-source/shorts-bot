import asyncio, json, os, pathlib, random, subprocess, time, urllib.parse
import edge_tts, requests

W, H = 1080, 1920
OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
HIST = pathlib.Path("history.json")
MUSIC_DIR = pathlib.Path("music")
NICHE = os.environ.get("NICHE") or "surprising science and history facts"
VOICE = os.environ.get("VOICE", "en-US-AndrewMultilingualNeural")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
PEXELS = os.environ.get("PEXELS_API_KEY", "")
used_videos = set()

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,92,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,9,2,2,60,60,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def run(cmd):
    subprocess.run(cmd, check=True)


def plan(hist):
    prompt = f"""You write viral YouTube Shorts. Theme: {NICHE}.
Pick ONE fresh, specific, surprising topic. Do NOT reuse any of these earlier titles: {json.dumps(hist[-40:])}.
Rules:
- Scene 1 is a hook that creates curiosity in under 3 seconds: a bold claim or a question. No greetings, no "did you know".
- Exactly 6 scenes. Each narration is 10-14 words, short punchy plain sentences, ONE idea per scene.
- Only state facts you are certain are true. No made-up statistics.
- The last scene teases or loops back to the hook so people rewatch.
Return ONLY JSON with keys:
title (max 70 chars, curiosity-driven),
description (1-2 sentences),
hashtags (array of 5 strings, no #),
scenes (array of 6 objects with: "narration", "search_query" (2-3 simple concrete English words describing real stock footage, e.g. "ocean waves", "city night"), "image_prompt" (vivid visual description, no text in image))."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={os.environ['GEMINI_API_KEY']}"
    for attempt in range(4):
        try:
            r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}],
                                         "generationConfig": {"responseMimeType": "application/json"}}, timeout=90)
            r.raise_for_status()
            data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            assert len(data["scenes"]) >= 3
            return data
        except Exception as e:
            print("LLM retry:", e); time.sleep(5 * (attempt + 1))
    raise SystemExit("LLM failed")


def pexels_clip(query, path):
    if not PEXELS or not query:
        return False
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS},
                         params={"query": query, "orientation": "portrait", "per_page": 15}, timeout=30)
        r.raise_for_status()
        vids = [v for v in r.json().get("videos", []) if v["id"] not in used_videos and v.get("duration", 0) >= 3]
        random.shuffle(vids)
        for v in vids:
            files = [f for f in v["video_files"] if f.get("width") and f.get("height") and f["height"] > f["width"]]
            if not files:
                continue
            good = [f for f in files if f["height"] >= 1280]
            f = min(good, key=lambda x: x["height"]) if good else max(files, key=lambda x: x["height"])
            d = requests.get(f["link"], timeout=120); d.raise_for_status()
            path.write_bytes(d.content)
            used_videos.add(v["id"])
            return True
    except Exception as e:
        print("pexels failed:", e)
    return False


def make_image(prompt, path):
    for attempt in range(4):
        try:
            u = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt + ", vertical, cinematic, highly detailed")
                 + f"?width={W}&height={H}&nologo=true&seed={random.randint(1, 10**6)}")
            r = requests.get(u, timeout=120); r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image"):
                path.write_bytes(r.content); return
        except Exception as e:
            print("image retry:", e); time.sleep(6 * (attempt + 1))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c=0x{random.randint(0, 0x444444):06x}:s={W}x{H}", "-frames:v", "1", str(path)])


def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(path)])
    return float(out.decode().strip())


async def tts(text, path):
    for attempt in range(4):
        try:
            words = []
            comm = edge_tts.Communicate(text, VOICE, rate="+4%", boundary="WordBoundary")
            with open(path, "wb") as f:
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"])
                    elif ch["type"] == "WordBoundary":
                        words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
            return words
        except Exception as e:
            print("tts retry:", e); await asyncio.sleep(4 * (attempt + 1))
    raise SystemExit("TTS failed")


def ts(t):
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def write_ass(words, narration, dur, path):
    if not words:  # fallback: spread words evenly
        toks = narration.split()
        step = dur / max(len(toks), 1)
        words = [(i * step, (i + 1) * step, t) for i, t in enumerate(toks)]
    chunks = [words[i:i + 3] for i in range(0, len(words), 3)]
    lines = []
    for n, ch in enumerate(chunks):
        start = ch[0][0]
        end = chunks[n + 1][0][0] if n + 1 < len(chunks) else ch[-1][1] + 0.3
        text = " ".join(w[2] for w in ch).upper().replace("{", "").replace("}", "")
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,{text}")
    path.write_text(ASS_HEADER + "\n".join(lines) + "\n", encoding="utf-8")


def render_clip(i, sc, dur, ass):
    vid, img, aud, clip = OUT / f"{i}_src.mp4", OUT / f"{i}.jpg", OUT / f"{i}.mp3", OUT / f"{i}.mp4"
    sub = f"subtitles={ass.as_posix()}"
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if pexels_clip(sc.get("search_query", ""), vid):
        inp = ["-stream_loop", "-1", "-i", str(vid)]
        vf = f"{fit},fps=30,{sub},format=yuv420p"
    else:
        make_image(sc["image_prompt"], img)
        frames = int(dur * 30) + 15
        inp = ["-i", str(img)]
        vf = (f"{fit},zoompan=z='min(zoom+0.0008,1.15)':d={frames}:s={W}x{H}:fps=30,{sub},format=yuv420p")
    run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-i", str(aud), "-vf", vf,
         "-map", "0:v", "-map", "1:a", "-t", f"{dur + 0.2:.2f}", "-r", "30",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
         "-shortest", str(clip)])
    return clip


def add_music(video, final):
    tracks = list(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []
    if not tracks:
        video.replace(final); return
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-stream_loop", "-1", "-i", str(random.choice(tracks)),
         "-filter_complex", "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)])


def main():
    hist = json.loads(HIST.read_text()) if HIST.exists() else []
    data = plan(hist)
    clips = []
    for i, sc in enumerate(data["scenes"]):
        aud, ass = OUT / f"{i}.mp3", OUT / f"{i}.ass"
        words = asyncio.run(tts(sc["narration"], aud))
        dur = duration(aud)
        write_ass(words, sc["narration"], dur, ass)
        clips.append(render_clip(i, sc, dur, ass))
    lst = OUT / "list.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = OUT / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(joined)])
    add_music(joined, OUT / "final.mp4")
    tags = " ".join("#" + t.replace(" ", "") for t in data.get("hashtags", []))
    meta = {"title": data["title"][:95], "description": f"{data['description']}\n\n{tags} #Shorts",
            "caption": f"{data['title']}\n\n{data['description']}\n\n{tags}",
            "tags": data.get("hashtags", [])}
    (OUT / "meta.json").write_text(json.dumps(meta))
    HIST.write_text(json.dumps((hist + [data["title"]])[-100:]))
    print("Done:", meta["title"])


main()
