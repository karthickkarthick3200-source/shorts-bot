import asyncio, base64, json, os, pathlib, random, re, subprocess, time, urllib.parse
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
PIXABAY = os.environ.get("PIXABAY_API_KEY", "")
# Optional: a free Pollinations key (sent as a Bearer token) and a custom endpoint if their docs change.
POLL_KEY = os.environ.get("POLLINATIONS_KEY", "").strip()
POLL_URL = os.environ.get("POLLINATIONS_URL", "").strip() or "https://image.pollinations.ai/prompt/"
if not POLL_URL.endswith("/"):
    POLL_URL += "/"
# Free AI pictures from Cloudflare Workers AI (FLUX.1 schnell): free account + API token, no card.
CF_ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
# One look for every picture. Change the IMAGE_STYLE variable in GitHub to change the whole channel's style.
STYLE_SUFFIX = os.environ.get("IMAGE_STYLE", "").strip() or (
    "3D Pixar-style cartoon illustration, vibrant saturated colors, expressive characters, "
    "soft cinematic lighting, highly detailed, no text, no letters, no watermark")
CHARACTER = ""        # recurring main character, written by the AI for each video so faces stay consistent
cf_dead = False
cf_fail = 0
used_videos = set()
used_pb = set()
pollinations_dead = False   # set to True on 401/402/403 so we stop wasting time on retries


# Caption looks. The AI picks one per video to match the topic. Fonts come from the system (see workflow apt step);
# if a font is missing, the renderer quietly falls back to a default font.
STYLES = {
    "impact":   {"font": "Oswald",           "scale": 1.15, "bold": True, "color": "&H0000E6FF", "upper": True},
    "clean":    {"font": "Open Sans",        "scale": 1.00, "bold": True, "color": "&H00FFFFFF", "upper": True},
    "modern":   {"font": "Montserrat",       "scale": 0.90, "bold": True, "color": "&H00FFFFFF", "upper": True},
    "friendly": {"font": "Comic Neue",       "scale": 1.10, "bold": True, "color": "&H0080F0FF", "upper": False},
    "serif":    {"font": "Liberation Serif", "scale": 1.10, "bold": True, "color": "&H00E6F5FF", "upper": False},
    "mono":     {"font": "DejaVu Sans Mono", "scale": 0.85, "bold": True, "color": "&H0080FF80", "upper": False},
}
CUR_STYLE = STYLES["clean"]
POP = "{\\fscx85\\fscy85\\t(0,120,\\fscx105\\fscy105)\\t(120,220,\\fscx100\\fscy100)\\fad(40,0)}"


def ass_header():
    st = CUR_STYLE
    size, margin = (66, 110) if KIND == "long" else (92, 520)
    size = int(size * st["scale"])
    bold = -1 if st["bold"] else 0
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{st['font']},{size},{st['color']},&H000000FF,&H00000000,&H64000000,{bold},0,0,0,100,100,0,0,1,9,2,2,60,60,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def run(cmd):
    subprocess.run(cmd, check=True)


RULES_COMMON = """- Only state facts you are certain are true. No made-up statistics, no made-up tools, no made-up prices. Only name tools that really exist and are free. If you are not sure a tool exists, teach a general technique instead.
- The title may ONLY promise what the video actually explains. Never use: secret, hack, hidden, shocking, they don't want you to know, or any claim about earning money, getting paid or getting rich.
- Narration is plain spoken English, no emojis, no stage directions.
- The first scene must confirm what the title promises in its first sentence and open a question the viewer wants answered. Good hook shapes: name a mistake the viewer is probably making right now; state common advice and then reject it; show the end result first, then say how. Never invent a number, a result or a source."""

JSON_KEYS = """Return ONLY JSON with keys:
title (max 70 chars, clear and honest, no clickbait),
description (1-2 sentences, no income claims),
hashtags (array of 5 strings, no #),
character (ONE sentence describing a friendly cartoon main character who appears in most scenes: age, hair, clothes, e.g. "a young Indian woman with a black ponytail wearing a yellow hoodie and round glasses"),
style (exactly one of: impact, clean, modern, friendly, serif, mono. Pick the caption look that fits the topic mood: impact for urgent tips and news, clean for tutorials, modern for AI and tech, friendly for casual beginner topics, serif for history and stories, mono for coding),
scenes (array of {n} objects with: "narration", "search_query" (2-3 simple concrete English words describing real stock footage, e.g. "ocean waves", "city night"), "image_prompts" (array of exactly 2 picture descriptions. Each must show exactly what that scene's narration is saying: a concrete subject, an action and a setting, featuring the main character whenever a person fits. The two must be DIFFERENT moments or camera angles. Never ask for text, letters, logos or screens with readable words in the pictures))."""


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


def pixabay_clip(query, path):
    """Free stock footage from Pixabay (needs a free PIXABAY_API_KEY). Tries the full query, then its first word."""
    if not PIXABAY or not query:
        return False
    words = query.split()
    tries = [query] + ([words[0]] if len(words) > 1 else [])
    order = ["large", "medium", "small"] if KIND == "long" else ["medium", "large", "small"]
    for q in tries:
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                             params={"key": PIXABAY, "q": q, "per_page": 20, "safesearch": "true"}, timeout=30)
            r.raise_for_status()
            hits = [h for h in r.json().get("hits", []) if h["id"] not in used_pb and h.get("duration", 0) >= 3]
            random.shuffle(hits)
            for h in hits[:5]:
                v = h.get("videos", {})
                url = next((v[k]["url"] for k in order if v.get(k, {}).get("url")), None)
                if not url:
                    continue
                d = requests.get(url, timeout=180); d.raise_for_status()
                path.write_bytes(d.content)
                used_pb.add(h["id"])
                return True
        except Exception as e:
            print("pixabay failed:", e)
    return False


def video_size(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)])
    w, h = out.decode().strip().split(",")[:2]
    return int(w), int(h)


last_good_image = None

PALETTES = [((18, 24, 58), (88, 60, 160)), ((10, 40, 60), (20, 130, 150)), ((40, 16, 50), (190, 70, 110)),
            ((16, 28, 20), (60, 150, 100)), ((30, 30, 30), (200, 120, 40)), ((12, 18, 40), (40, 110, 200))]


def gradient_card(seed_text, path):
    """Free, offline background: a vertical gradient with soft circles. Never fails the pipeline."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"color=c=0x{random.randint(0x101010, 0x444444):06x}:s={W}x{H}", "-frames:v", "1", str(path)])
        return
    rnd = random.Random(seed_text)
    c1, c2 = rnd.choice(PALETTES)
    col = Image.new("RGB", (1, H))
    for y in range(H):
        col.putpixel((0, y), tuple(int(c1[j] + (c2[j] - c1[j]) * y / H) for j in range(3)))
    img = col.resize((W, H)).convert("RGBA")
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for _ in range(5):
        r = rnd.randint(min(W, H) // 6, min(W, H) // 3)
        x, y = rnd.randint(0, W), rnd.randint(0, H)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rnd.randint(12, 28)))
    Image.alpha_composite(img, overlay).convert("RGB").save(path, quality=92)


def cf_image(prompt, path):
    """Cloudflare Workers AI FLUX.1 schnell. Returns True when a picture was saved."""
    global cf_dead, cf_fail
    if cf_dead or not (CF_ACCOUNT and CF_TOKEN):
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    for attempt in range(3):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"},
                              json={"prompt": prompt[:1900], "steps": 6}, timeout=120)
            if r.status_code in (401, 403):
                print(f"cloudflare refused ({r.status_code}): check CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN")
                cf_dead = True
                return False
            if r.status_code == 429:
                print("cloudflare limit reached:", r.text[:160]); time.sleep(5 * (attempt + 1)); continue
            r.raise_for_status()
            path.write_bytes(base64.b64decode(r.json()["result"]["image"]))
            cf_fail = 0
            return True
        except Exception as e:
            print("cloudflare image retry:", e); time.sleep(5 * (attempt + 1))
    cf_fail += 1
    if cf_fail >= 2:   # two pictures in a row failed (often the free daily limit): stop wasting time
        print("cloudflare images switched off for this run")
        cf_dead = True
    return False


def make_image(prompt, path):
    global last_good_image, pollinations_dead
    full = ((CHARACTER + ", ") if CHARACTER else "") + prompt + ", " + STYLE_SUFFIX
    if cf_image(full, path):
        last_good_image = path
        return True
    if not pollinations_dead:
        headers = {"Authorization": f"Bearer {POLL_KEY}"} if POLL_KEY else {}
        for attempt in range(3):
            try:
                u = (POLL_URL + urllib.parse.quote(full)
                     + f"?width={W}&height={H}&nologo=true&seed={random.randint(1, 10**6)}")
                r = requests.get(u, headers=headers, timeout=90)
                if r.status_code in (401, 402, 403):
                    # Payment/auth problems do not fix themselves: stop retrying for the whole run.
                    print(f"pollinations refused ({r.status_code}); not using it again this run")
                    pollinations_dead = True
                    break
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("image"):
                    path.write_bytes(r.content); last_good_image = path; return True
            except Exception as e:
                print("image retry:", e); time.sleep(6 * (attempt + 1))
    return False


def ai_images_available():
    cf_ok = bool(CF_ACCOUNT and CF_TOKEN) and not cf_dead
    return cf_ok or not pollinations_dead


def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "csv=p=0", str(path)])
    return float(out.decode().strip())


def clean_text(text):
    """Remove emojis, markdown and odd symbols that can make edge-tts return no audio."""
    text = re.sub(r"[*_`#~<>\[\]{}|\\^=+]", " ", str(text))
    text = re.sub(r"[^\w\s.,!?'\-:;%$&()/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def tts(text, path):
    global VOICE
    text = clean_text(text)
    if not text:
        raise SystemExit("TTS failed: empty narration")
    # Multilingual voices do not always send word timings, so we fall back step by step.
    configs = [(VOICE, "WordBoundary"), (VOICE, "SentenceBoundary"),
               ("en-US-GuyNeural", "WordBoundary"), ("en-US-AriaNeural", "SentenceBoundary")]
    for attempt in range(6):
        voice, boundary = configs[min(attempt, len(configs) - 1)]
        try:
            words, got = [], False
            comm = edge_tts.Communicate(text, voice, rate="+4%", boundary=boundary)
            with open(path, "wb") as f:
                async for ch in comm.stream():
                    if ch["type"] == "audio":
                        f.write(ch["data"]); got = True
                    elif ch["type"] in ("WordBoundary", "SentenceBoundary"):
                        s = ch["offset"] / 1e7
                        e = (ch["offset"] + ch["duration"]) / 1e7
                        toks = ch["text"].split()
                        if ch["type"] == "WordBoundary" or len(toks) <= 1:
                            words.append((s, e, ch["text"]))
                        else:  # a whole sentence: spread its words evenly across its time
                            step = (e - s) / len(toks)
                            words += [(s + i * step, s + (i + 1) * step, t) for i, t in enumerate(toks)]
            if not got or path.stat().st_size == 0:
                raise RuntimeError("empty audio file")
            VOICE = voice  # keep the same voice for the rest of the video
            return words
        except Exception as e:
            print(f"tts retry ({voice}, {boundary}):", e); await asyncio.sleep(3 * (attempt + 1))
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
        text = " ".join(w[2] for w in ch).replace("{", "").replace("}", "")
        if CUR_STYLE["upper"]:
            text = text.upper()
        text = POP + text
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},Default,,0,0,0,,{text}")
    path.write_text(ass_header() + "\n".join(lines) + "\n", encoding="utf-8")


def render_stock(vid, aud, dur, sub, clip):
    """Stock clip fills the frame; if its shape does not match, it sits sharp over a blurred copy of itself."""
    w, h = video_size(vid)
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if abs(w / h - W / H) < 0.3:
        fc = f"[0:v]{fit},fps=30,{sub},format=yuv420p[vout]"
    else:
        fc = (f"[0:v]split[a][b];[a]{fit},boxblur=30:5[bg];"
              f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30,{sub},format=yuv420p[vout]")
    run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(vid), "-i", str(aud),
         "-filter_complex", fc, "-map", "[vout]", "-map", "1:a", "-t", f"{dur + 0.2:.2f}", "-r", "30",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
         "-shortest", str(clip)])


def gradient_video(seed_text, dur, aud, sub, clip):
    """Free animated background: slowly moving colour gradient (no API, no download)."""
    rnd = random.Random(seed_text)
    c1, c2 = rnd.choice(PALETTES)
    mid = tuple(max(0, min(255, (a + b) // 2 + rnd.randint(-20, 50))) for a, b in zip(c1, c2))
    hx = lambda c: "0x%02x%02x%02x" % tuple(c)
    src = (f"gradients=s={W}x{H}:c0={hx(c1)}:c1={hx(mid)}:c2={hx(c2)}:nb_colors=3:"
           f"speed=0.04:rate=30:duration={dur + 0.3:.2f}")
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-i", str(aud),
         "-vf", f"{sub},format=yuv420p", "-map", "0:v", "-map", "1:a", "-t", f"{dur + 0.2:.2f}", "-r", "30",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
         "-shortest", str(clip)])


def render_clip(i, sc, dur, ass):
    vid, aud, clip = OUT / f"{i}_src.mp4", OUT / f"{i}.mp3", OUT / f"{i}.mp4"
    sub = f"subtitles={ass.as_posix()}"
    prompts = sc.get("image_prompts") or ([sc["image_prompt"]] if sc.get("image_prompt") else [])
    prompts = [p for p in prompts if p][:3] or [sc.get("narration", "abstract background")]
    # 1) AI pictures that match what the scene says, each with its own camera move, joined by crossfades.
    if ai_images_available():
        imgs = []
        for k, p in enumerate(prompts):
            if k > 0 and not ai_images_available():
                break
            ip = OUT / f"{i}_{k}.jpg"
            if make_image(p, ip):
                imgs.append(ip)
                time.sleep(1)
        if imgs:
            build_shots(imgs, dur + 0.2, aud, sub, clip, i)
            return clip
    # 2) Free stock footage, if a Pexels or Pixabay key is set.
    q = sc.get("search_query", "")
    if pexels_clip(q, vid) or pixabay_clip(q, vid):
        render_stock(vid, aud, dur, sub, clip)
        return clip
    # 3) Last resort: a moving colour gradient.
    try:
        gradient_video(prompts[0], dur, aud, sub, clip)
    except Exception as e:
        print("gradient video failed, using still card:", e)
        ip = OUT / f"{i}_0.jpg"
        gradient_card(prompts[0], ip)
        build_shots([ip], dur + 0.2, aud, sub, clip, i)
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
    global CUR_STYLE, CHARACTER
    hist = json.loads(HIST.read_text()) if HIST.exists() else []
    data = plan(hist)
    CUR_STYLE = STYLES.get(str(data.get("style", "")).strip().lower(), STYLES["clean"])
    CHARACTER = str(data.get("character", "")).strip()[:300]
    print("caption style:", CUR_STYLE["font"], "| character:", CHARACTER)
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
