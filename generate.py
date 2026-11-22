import asyncio, json, os, pathlib, random, subprocess, time, urllib.parse
import edge_tts, requests

KIND = os.environ.get("KIND", "short").strip().lower()      # "short" (vertical) or "long" (landscape)
if KIND not in ("short", "long"):
    KIND = "short"
W, H = (1920, 1080) if KIND == "long" else (1080, 1920)
OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
HIST = pathlib.Path("history.json")
MUSIC_DIR = pathlib.Path("music")
NICHE = os.environ.get("NICHE") or "surprising science and history facts"
VOICE = os.environ.get("VOICE", "en-US-AndrewMultilingualNeural")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
PEXELS = os.environ.get("PEXELS_API_KEY", "")
used_videos = set()

def ass_header():
    size, margin = (66, 110) if KIND == "long" else (92, 520)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,9,2,2,60,60,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def run(cmd):
    subprocess.run(cmd, check=True)


RULES_COMMON = """- Only state facts you are certain are true. No made-up statistics, no made-up tools, no made-up prices. Only name tools that really exist and are free. If you are not sure a tool exists, teach a general technique instead.
- The title may ONLY promise what the video actually explains. Never use: secret, hack, hidden, shocking, they don't want you to know, or any claim about earning money, getting paid or getting rich.
- Narration is plain spoken English, no emojis, no stage directions."""

JSON_KEYS = """Return ONLY JSON with keys:
title (max 70 chars, clear and honest, no clickbait),
description (1-2 sentences, no income claims),
hashtags (array of 5 strings, no #),
scenes (array of {n} objects with: "narration", "search_query" (2-3 simple concrete English words describing real stock footage, e.g. "ocean waves", "city night"), "image_prompts" (array of exactly 2 vivid visual descriptions of two DIFFERENT views or moments for this scene, no text in the images))."""


def build_prompt(hist):
    recent = json.dumps(hist[-40:])
    if KIND == "long":
        return f"""You write honest, useful YouTube tutorial videos of 4 minutes. Theme: {NICHE}.
Pick ONE fresh, specific topic a beginner can use today. Do NOT reuse any of these earlier titles: {recent}.
Structure (22 scenes):
- Scene 1: the problem the viewer has, as a question. No greetings.
- Scenes 2-3: what the tool or technique is, and who it is for.
- Scenes 4-17: a numbered step-by-step walkthrough. One clear action per scene.
- Scenes 18-20: three common mistakes and how to avoid them.
- Scenes 21-22: a short recap of the one thing to remember.
Rules:
- Exactly 22 scenes. Each narration is 26-32 words (two or three plain sentences), ONE idea per scene.
{RULES_COMMON}
{JSON_KEYS.format(n=22)}"""
    return f"""You write honest, useful YouTube Shorts. Theme: {NICHE}.
Pick ONE fresh, specific topic. Do NOT reuse any of these earlier titles: {recent}.
Rules:
- Scene 1 is a hook that creates curiosity in under 3 seconds: a question or a clear problem the viewer has. No greetings, no "did you know".
- Exactly 8 scenes. Each narration is 11-15 words, short plain sentences, ONE idea per scene.
- The middle scenes must teach one clear, practical step the viewer can try today.
- The last scene sums up the one thing to remember (or loops back to the hook).
{RULES_COMMON}
{JSON_KEYS.format(n=8)}"""


def length_ok(data):
    """Strict check on scene count and spoken length (about 2.6 words per second)."""
    scenes = data["scenes"]
    words = sum(len(str(sc.get("narration", "")).split()) for sc in scenes)
    if KIND == "long":
        return 18 <= len(scenes) <= 26 and 540 <= words <= 780
    return 7 <= len(scenes) <= 9 and 85 <= words <= 125


def plan(hist):
    prompt = build_prompt(hist)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={os.environ['GEMINI_API_KEY']}"
    best = None
    for attempt in range(5):
        try:
            r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}],
                                         "generationConfig": {"responseMimeType": "application/json"}}, timeout=180)
            r.raise_for_status()
            data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            assert len(data["scenes"]) >= 3 and all(sc.get("narration") for sc in data["scenes"])
            if length_ok(data):
                return data
            best = data  # usable, but the wrong length: try again, keep it as a fallback
            print("Script length off, retrying", attempt + 1)
            time.sleep(3)
        except Exception as e:
            print("LLM retry:", e); time.sleep(5 * (attempt + 1))
    if best:
        return best
    raise SystemExit("LLM failed")


def pexels_clip(query, path):
    if not PEXELS or not query:
        return False
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS},
                         params={"query": query, "orientation": "landscape" if KIND == "long" else "portrait", "per_page": 15}, timeout=30)
        r.raise_for_status()
        vids = [v for v in r.json().get("videos", []) if v["id"] not in used_videos and v.get("duration", 0) >= 3]
        random.shuffle(vids)
        for v in vids:
            files = [f for f in v["video_files"] if f.get("width") and f.get("height") and ((f["width"] > f["height"]) if KIND == "long" else (f["height"] > f["width"]))]
            if not files:
                continue
            good = [f for f in files if f["height"] >= (720 if KIND == "long" else 1280)]
            f = min(good, key=lambda x: x["height"]) if good else max(files, key=lambda x: x["height"])
            d = requests.get(f["link"], timeout=120); d.raise_for_status()
            path.write_bytes(d.content)
            used_videos.add(v["id"])
            return True
    except Exception as e:
        print("pexels failed:", e)
    return False


last_good_image = None


def make_image(prompt, path):
    global last_good_image
    for attempt in range(6):
        try:
            u = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt + ", vertical, cinematic, highly detailed")
                 + f"?width={W}&height={H}&nologo=true&seed={random.randint(1, 10**6)}")
            r = requests.get(u, timeout=120); r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image"):
                path.write_bytes(r.content); last_good_image = path; return
        except Exception as e:
            print("image retry:", e); time.sleep(6 * (attempt + 1))
    # All retries failed: reuse the last good picture so we never show a plain colour block.
    if last_good_image and last_good_image.exists():
        path.write_bytes(last_good_image.read_bytes()); return
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
    path.write_text(ass_header() + "\n".join(lines) + "\n", encoding="utf-8")


def render_clip(i, sc, dur, ass):
    vid, img, aud, clip = OUT / f"{i}_src.mp4", OUT / f"{i}.jpg", OUT / f"{i}.mp3", OUT / f"{i}.mp4"
    sub = f"subtitles={ass.as_posix()}"
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if pexels_clip(sc.get("search_query", ""), vid):
        inp = ["-stream_loop", "-1", "-i", str(vid)]
        vf = f"{fit},fps=30,{sub},format=yuv420p"
        run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-i", str(aud), "-vf", vf,
             "-map", "0:v", "-map", "1:a", "-t", f"{dur + 0.2:.2f}", "-r", "30",
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-shortest", str(clip)])
        return clip
    # No stock clip: build several images per scene, each with a different camera move, joined by crossfades.
    prompts = sc.get("image_prompts") or ([sc["image_prompt"]] if sc.get("image_prompt") else [])
    prompts = [p for p in prompts if p][:3] or [sc.get("narration", "abstract background")]
    imgs = []
    for k, p in enumerate(prompts):
        ip = OUT / f"{i}_{k}.jpg"
        make_image(p, ip)
        imgs.append(ip)
        time.sleep(1)
    build_shots(imgs, dur + 0.2, aud, sub, clip, i)
    return clip


MOVES = ["in", "pan_right", "out", "pan_left"]


def zoom_expr(move, frames):
    cx, cy = "x='iw/2-(iw/zoom/2)'", "y='ih/2-(ih/zoom/2)'"
    if move == "in":
        return f"z='min(zoom+0.0012,1.2)':{cx}:{cy}"
    if move == "out":
        return f"z='if(eq(on,1),1.2,max(zoom-0.0012,1.0))':{cx}:{cy}"
    if move == "pan_right":
        return f"z=1.2:x='(iw-iw/zoom)*on/{frames}':{cy}"
    return f"z=1.2:x='(iw-iw/zoom)*(1-on/{frames})':{cy}"


def build_shots(imgs, total, aud, sub, clip, scene_no):
    n, fade = len(imgs), 0.4
    if n == 1:
        fade = 0
    d = (total + (n - 1) * fade) / n          # length of each shot, so the joined total equals `total`
    frames = int(d * 30) + 2
    start = scene_no % len(MOVES)              # vary the first move from scene to scene
    inp, parts = [], []
    for k, ip in enumerate(imgs):
        inp += ["-loop", "1", "-t", f"{d + 0.5:.2f}", "-i", str(ip)]
        mv = MOVES[(start + k) % len(MOVES)]
        parts.append(f"[{k}:v]scale={int(W * 1.5)}:{int(H * 1.5)}:force_original_aspect_ratio=increase,"
                     f"crop={int(W * 1.5)}:{int(H * 1.5)},"
                     f"zoompan={zoom_expr(mv, frames)}:d={frames}:s={W}x{H}:fps=30,"
                     f"setsar=1,format=yuv420p,trim=duration={d:.3f},setpts=PTS-STARTPTS[v{k}]")
    last = "v0"
    for k in range(1, n):
        off = k * (d - fade)
        parts.append(f"[{last}][v{k}]xfade=transition=fade:duration={fade}:offset={off:.3f}[x{k}]")
        last = f"x{k}"
    parts.append(f"[{last}]{sub},format=yuv420p[vout]")
    run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-i", str(aud),
         "-filter_complex", ";".join(parts),
         "-map", "[vout]", "-map", f"{n}:a", "-t", f"{total:.2f}", "-r", "30",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
         "-shortest", str(clip)])


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
    total = 0.0
    for i, sc in enumerate(data["scenes"]):
        aud, ass = OUT / f"{i}.mp3", OUT / f"{i}.ass"
        words = asyncio.run(tts(sc["narration"], aud))
        dur = duration(aud)
        write_ass(words, sc["narration"], dur, ass)
        total += dur
        clips.append(render_clip(i, sc, dur, ass))
    lst = OUT / "list.txt"
    lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = OUT / "joined.mp4"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(joined)])
    add_music(joined, OUT / "final.mp4")
    tags = " ".join("#" + t.replace(" ", "") for t in data.get("hashtags", []))
    shorts_tag = " #Shorts" if KIND == "short" else ""
    meta = {"title": data["title"][:95], "description": f"{data['description']}\n\n{tags}{shorts_tag}",
            "caption": f"{data['title']}\n\n{data['description']}\n\n{tags}",
            "tags": data.get("hashtags", [])}
    (OUT / "meta.json").write_text(json.dumps(meta))
    HIST.write_text(json.dumps((hist + [data["title"]])[-100:]))
    print(f"Done ({KIND}, {total:.0f}s):", meta["title"])


main()
