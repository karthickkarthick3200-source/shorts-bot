import asyncio, base64, json, os, pathlib, random, re, subprocess, time, urllib.parse
import edge_tts, requests

KIND = os.environ.get("KIND", "short").strip().lower()      # "short" (vertical) or "long" (landscape)
if KIND not in ("short", "long"):
    KIND = "short"
W, H = (1920, 1080) if KIND == "long" else (1080, 1920)
OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
HIST = pathlib.Path("history.json")
TRENDS = pathlib.Path("trends.txt")
TRENDS_AUTO = pathlib.Path("trends_auto.txt")
MUSIC_DIR = pathlib.Path("music")
DEFAULT_NICHE = ("Fun, useful trending content for Indian viewers in Tamil, with Thanglish captions: "
                 "AI tools explained as short skits, game facts and tech news. Original characters and scripts only.")
# CHANNEL_LANG: "en" (default) = simple Indian-friendly English, "ta" = Tamil voice + Thanglish captions.
CHANNEL_LANG = os.environ.get("CHANNEL_LANG", "").strip().lower()
if CHANNEL_LANG not in ("en", "ta"):
    CHANNEL_LANG = "en"
TA = CHANNEL_LANG == "ta"
DEFAULT_NICHE_EN = ("Fun, useful trending content for Indian viewers in simple English: AI tools explained as short skits, "
                    "game facts, tech news, motivation and gym tips. Original characters and scripts only.")
NICHE = os.environ.get("NICHE", "").strip() or (DEFAULT_NICHE if TA else DEFAULT_NICHE_EN)

# ---- Voice: free Edge voices. A voice variable of the wrong language is ignored on purpose. ----
TAMIL_VOICES = ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"] if TA else ["en-US-GuyNeural", "en-US-AriaNeural"]
_v = os.environ.get("VOICE", "").strip()
VOICE = _v if _v.startswith("ta-" if TA else "en-") else TAMIL_VOICES[0]
if _v and VOICE != _v:
    print(f"VOICE variable '{_v}' does not match CHANNEL_LANG={CHANNEL_LANG}, using {VOICE} instead")
TTS_RATE = os.environ.get("TTS_RATE", "").strip() or ("+0%" if TA else "+4%")       # e.g. "+8%" faster, "-5%" slower
TTS_PITCH = os.environ.get("TTS_PITCH", "").strip() or "+0Hz"    # e.g. "+4Hz" or "-3Hz"

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# Tried in order when the main model is overloaded (503/429/timeouts). Override with GEMINI_FALLBACKS="a,b".
FALLBACK_MODELS = [m.strip() for m in os.environ.get("GEMINI_FALLBACKS", "gemini-3.5-flash-lite,gemini-3.6-flash,gemini-3.1-flash-lite").split(",") if m.strip()]
PEXELS = os.environ.get("PEXELS_API_KEY", "")
PIXABAY = os.environ.get("PIXABAY_API_KEY", "")
# VISUALS=stock (default): real moving stock clips first, then AI pictures, then gradient.
# VISUALS=ai: AI pictures first (old behaviour).
VISUALS = os.environ.get("VISUALS", "stock").strip().lower()
# Optional: a free Pollinations key (sent as a Bearer token) and a custom endpoint if their docs change.
POLL_KEY = os.environ.get("POLLINATIONS_KEY", "").strip()
POLL_URL = os.environ.get("POLLINATIONS_URL", "").strip() or "https://image.pollinations.ai/prompt/"
if not POLL_URL.endswith("/"):
    POLL_URL += "/"


def clean_secret(name):
    """First word of a GitHub secret: survives stray spaces, line breaks and a pasted 'Bearer' prefix."""
    raw = os.environ.get(name, "")
    m = re.search(r"[Bb]earer\s+([A-Za-z0-9_\-]{20,})", raw)   # a whole pasted curl command: take the token inside it
    if m:
        return m.group(1)
    parts = [x for x in raw.split() if x.lower() not in ("bearer", "authorization:")]
    return parts[0] if parts else ""


CF_ACCOUNT = clean_secret("CLOUDFLARE_ACCOUNT_ID")
CF_TOKEN = clean_secret("CLOUDFLARE_API_TOKEN")
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

# Random scene-to-scene looks inside a scene (two shots joined). A failing one falls back to a plain fade.
TRANSITIONS = ["fade", "slideleft", "slideright", "wipeleft", "circleopen", "smoothleft",
               "pixelize", "radial", "dissolve", "zoomin"]

# ----------------------------------------------------------------------------------------------
# Caption looks. Colours are ASS &HAABBGGRR. Captions are Thanglish (Tamil written in English letters),
# so every font here is a Latin font. A missing font quietly falls back to a default font.
# fx = which animation function draws the captions (see FX at the bottom of the caption section).
# Set the CAPTION_STYLE variable in GitHub to force one style; otherwise a random one is picked per video.
# ----------------------------------------------------------------------------------------------
YEL, WHT, CYN, MAG, GRN, RED = "&H0000E6FF", "&H00FFFFFF", "&H00FFFF00", "&H00FF00FF", "&H0080FF80", "&H000000FF"
BLK, BOX = "&H00000000", "&H80101010"
STYLES = {
    # classic six
    "impact":   {"font": "Oswald",           "scale": 1.15, "color": YEL, "upper": True,  "fx": "basic"},
    "clean":    {"font": "Open Sans",        "scale": 1.00, "color": WHT, "upper": True,  "fx": "basic"},
    "modern":   {"font": "Montserrat",       "scale": 0.90, "color": WHT, "upper": True,  "fx": "basic"},
    "friendly": {"font": "Comic Neue",       "scale": 1.10, "color": "&H0080F0FF", "upper": False, "fx": "basic"},
    "serif":    {"font": "Liberation Serif", "scale": 1.10, "color": "&H00E6F5FF", "upper": False, "fx": "basic"},
    "mono":     {"font": "DejaVu Sans Mono", "scale": 0.85, "color": GRN, "upper": False, "fx": "basic"},
    # trending looks
    "kinetic-pop":      {"font": "Oswald",     "scale": 1.25, "color": YEL, "upper": True,  "fx": "kinetic", "chunk": 2},
    "karaoke-pill":     {"font": "Montserrat", "scale": 0.95, "color": YEL, "sec": WHT, "ocol": "&H40201008",
                         "bs": 3, "ow": 16, "sh": 0, "upper": False, "fx": "karaoke"},
    "neon-glow":        {"font": "Montserrat", "scale": 1.00, "color": WHT, "upper": True,  "fx": "neon"},
    "typewriter-hud":   {"font": "DejaVu Sans Mono", "scale": 0.85, "color": GRN, "upper": False, "fx": "typewriter"},
    "glitch-rgb":       {"font": "Oswald",     "scale": 1.15, "color": WHT, "upper": True,  "fx": "glitch"},
    "slide-wipe":       {"font": "Open Sans",  "scale": 1.00, "color": WHT, "upper": True,  "fx": "slide"},
    "blur-focus":       {"font": "Montserrat", "scale": 1.00, "color": WHT, "upper": False, "fx": "blur"},
    "minimal-box":      {"font": "Open Sans",  "scale": 0.85, "color": WHT, "ocol": BOX, "bs": 3, "ow": 14, "sh": 0,
                         "upper": False, "fx": "minimal"},
    "pull-back-reveal": {"font": "Oswald",     "scale": 1.15, "color": WHT, "upper": True,  "fx": "pullback"},
    "tech-widgets":     {"font": "DejaVu Sans Mono", "scale": 0.85, "color": CYN, "upper": True, "fx": "tech"},
}
CUR_NAME = "clean"
CUR_STYLE = STYLES["clean"]
POP = "{\\fscx85\\fscy85\\t(0,120,\\fscx105\\fscy105)\\t(120,220,\\fscx100\\fscy100)\\fad(40,0)}"


def margin_v():
    return 110 if KIND == "long" else 520


def ass_header():
    st = CUR_STYLE
    size = int((72 if KIND == "long" else 96) * st["scale"])
    sec, ocol, back = st.get("sec", "&H000000FF"), st.get("ocol", BLK), st.get("back", "&H64000000")
    bs, ow, sh = st.get("bs", 1), st.get("ow", 9), st.get("sh", 2)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{st['font']},{size},{st['color']},{sec},{ocol},{back},-1,0,0,0,100,100,0,0,{bs},{ow},{sh},2,60,60,{margin_v()},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def run(cmd):
    subprocess.run(cmd, check=True)


# ----------------------------------------------------------------------------------------------
# Script writing (Gemini)
# ----------------------------------------------------------------------------------------------
FORMATS = [
    "a funny skit: a talking animal or object (original character) explains it to a confused friend",
    "myth vs fact: the character reacts to a common wrong belief and corrects it",
    "mistake and fix: show a mistake people make, a funny fail, then the right way",
    "quick tour: the character shows three small useful points, each with a joke",
    "talking vegetable or fruit skit: the vegetable (original character) explains it in a funny way",
    "talking lion or other animal (original character) gives a short motivational-style explanation, with humour",
    "reaction: the character reacts with big emotions to one simple confirmed fact, then explains it",
]

EVERGREEN_IDEAS = [
    "a talking vegetable (original character) argues with its friends about one simple healthy habit",
    "a talking lion (original character) coaches a nervous friend on one simple way to improve a skill, like learning faster or staying focused",
    "how to use AI video tools in general: one beginner technique for writing a good prompt (no tool claims, no numbers)",
    "what AI 'plugins' or 'skills' are, explained simply as a skit, with one safe habit when installing anything (no named download links)",
    "how to use free AI tools safely: one habit that avoids a common mistake (no named tool claims)",
    "one simple study or practice trick, shown as a funny before/after skit",
    "one phone or gaming habit that wastes time, and the fix, as a skit with a talking animal",
    "one simple gym habit for beginners (warm up, good form, rest days) told by a talking animal coach, no promises about results and no numbers",
    "a motivational talk from a talking lion (original character) about showing up every day, kept short and funny",
    "how to start learning AI as a beginner: one small practice habit, no course or tool claims, no numbers",
    "one simple money-saving habit for beginners as a skit (no income claims, no investment advice, no numbers)",
]

EVERGREEN = ("EVERGREEN IDEA for this video (no news, no dates, no specific numbers, no named people, no release dates): {idea}")


def read_trends():
    """All your own lines (trends.txt) plus a random 12 of the auto-collected ones (trends_auto.txt), so every
    video sees a different mix of topics."""
    def lines_of(f):
        if not f.exists():
            return []
        return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    auto = lines_of(TRENDS_AUTO)
    random.shuffle(auto)
    return "\n".join(lines_of(TRENDS) + auto[:12])


RULES_TA = """- Only state facts you are certain are true, or that appear in the TRENDS list. No made-up statistics, no made-up tools, no made-up prices, no made-up release dates. Only name tools that really exist. If you are not sure, teach a general idea instead.
- The title may ONLY promise what the video actually explains. Never use: secret, hack, hidden, shocking, they don't want you to know, or any claim about earning money, getting paid or getting rich.
- No numbers of any kind in the script (no hours, days, percentages, amounts, counts) unless the exact number is written in the TRENDS line. Say 'a day' or 'a few minutes' only as a general suggestion, never as a fact or a result (do not say things like 'urges fade in 24 hours').
- Original characters and original script only. Never copy another creator, show or game character.
- narration_ta is the VOICE text: casual spoken Tamil in Tamil script, short plain sentences the way friends talk, not formal book Tamil. Write English words (tool names, game names) in Tamil script as they sound (for example ChatGPT as சாட்ஜிபிடி) so the Tamil voice can pronounce them. No emojis, no stage directions, no symbols.
- caption is the SAME sentence written in Thanglish (Tamil in English letters, how people text it). It must say the same thing as narration_ta, same order, about the same number of words. Tool and game names are spelled normally in English (ChatGPT, GTA). No emojis.
- Scene 1 is a hook of AT MOST 10 words. It does ONE of: names a mistake the viewer makes right now; rejects common advice; shows the end result first. No greeting, no tool name yet, no 'did you know'. Never invent a number or a source.
- Fitness, gym or health topics: general safe form and habit tips only. No medical claims, no weight-loss or muscle-gain promises, no diet plans, no numbers; beginners start light and stop if something hurts.
- Scene 1 caption and narration must match the title's promise."""

JSON_KEYS_TA = """Return ONLY JSON with keys:
title (max 70 chars, Thanglish with English tool or game names, clear and honest, no clickbait),
description (1-2 sentences in Thanglish plus one plain English sentence for search, no income claims),
hashtags (array of 5 strings, no #),
character (ONE sentence describing a friendly original cartoon main character who appears in most scenes: a person, animal or object with age/colour/clothes, e.g. "a cheeky orange cat wearing round glasses and a yellow hoodie"),
scenes (array of {n} objects with: "narration_ta" (Tamil script voice text), "caption" (the same sentence in Thanglish), "search_queries" (array of exactly 2 DIFFERENT 2-3 word English queries for real stock footage that fits the scene, e.g. ["typing laptop", "cooking vegetables"]; film-able objects and actions only, never abstract ideas, never people types), "image_prompts" (array of exactly 2 picture descriptions in English. Each must show exactly what that scene says: a concrete subject, an action and a setting, featuring the main character whenever it fits. The two must be DIFFERENT moments or camera angles. Never ask for text, letters, logos or screens with readable words in the pictures))."""


RULES_EN = """- Only state facts you are certain are true, or that appear in the TRENDS list. No made-up statistics, no made-up tools, no made-up prices, no made-up release dates. Only name tools that really exist. If you are not sure, teach a general idea instead.
- The title may ONLY promise what the video actually explains. Never use: secret, hack, hidden, shocking, they don't want you to know, or any claim about earning money, getting paid or getting rich.
- Original characters and original script only. Never copy another creator, show or game character.
- No numbers of any kind in the script (no hours, days, percentages, amounts, counts) unless the exact number is written in the TRENDS line.
- narration is plain spoken English for Indian viewers: short sentences, simple everyday words, friendly and a little funny. No emojis, no stage directions, no symbols.
- caption is the SAME sentence as narration, copied exactly.
- Fitness, gym or health topics: general safe form and habit tips only. No medical claims, no weight-loss or muscle-gain promises, no diet plans, no numbers; beginners start light and stop if something hurts.
- Scene 1 is a hook of AT MOST 10 words. It does ONE of: names a mistake the viewer makes right now; rejects common advice; shows the end result first. No greeting, no tool name yet, no 'did you know'. Never invent a number or a source."""

JSON_KEYS_EN = """Return ONLY JSON with keys:
title (max 70 chars, clear and honest, no clickbait),
description (1-2 sentences, no income claims),
hashtags (array of 5 strings, no #),
character (ONE sentence describing a friendly original cartoon main character who appears in most scenes, e.g. "a cheeky orange cat wearing round glasses and a yellow hoodie"),
scenes (array of {n} objects with: "narration" (spoken English), "caption" (the same sentence), "search_queries" (array of exactly 2 DIFFERENT 2-3 word English queries for real stock footage, e.g. ["typing laptop", "cooking vegetables"]; film-able objects and actions only, never abstract ideas, never people types), "image_prompts" (array of exactly 2 picture descriptions in English. Each must show exactly what that scene says: a concrete subject, an action and a setting, featuring the main character whenever it fits. The two must be DIFFERENT moments or camera angles. Never ask for text, letters, logos or screens with readable words in the pictures))."""

RULES_COMMON = RULES_TA if TA else RULES_EN
JSON_KEYS = JSON_KEYS_TA if TA else JSON_KEYS_EN
SPOKEN = "spoken in Tamil" if TA else "spoken in simple English"
VK = "narration_ta" if TA else "narration"
WS, WL = ("8-13", "18-26") if TA else ("11-15", "26-32")


def vt(sc):
    return sc.get("narration_ta") or sc.get("narration") or ""


def build_prompt(hist):
    recent = json.dumps(hist[-40:], ensure_ascii=False)
    trends = read_trends()
    use_evergreen = (not trends) or random.random() < 0.3      # about 1 video in 3 is a timeless skit
    topic_rule = (EVERGREEN.format(idea=random.choice(EVERGREEN_IDEAS)) if use_evergreen else
                  f"TRENDS (the only news or facts you may use; pick ONE topic from this list that is not covered by the earlier titles; "
                  f"use no detail that is not written here. If a line is only a title or headline, make a fun reaction or explainer skit "
                  f"around that topic without adding any claims about it):\n{trends}")
    fmt = random.choice(FORMATS)
    if KIND == "long":
        return f"""You write fun, useful YouTube videos of about 4 minutes for Indian viewers of all ages, {SPOKEN}. Channel: {NICHE}.
{topic_rule}
Do NOT reuse any of these earlier titles: {recent}.
Format for this video: {fmt}.
Structure (22 scenes):
- Scene 1: the hook (see rules).
- Scenes 2-3: what this is and who it helps.
- Scenes 4-17: the main part, one clear idea per scene, with jokes from the character.
- Scenes 18-20: three common mistakes and how to avoid them.
- Scenes 21-22: a short recap of the one thing to remember.
Rules:
- Exactly 22 scenes. Each {VK} is {WL} words, ONE idea per scene.
{RULES_COMMON}
{JSON_KEYS.format(n=22)}"""
    return f"""You write fun, useful YouTube Shorts for Indian viewers of all ages, {SPOKEN}. Channel: {NICHE}.
{topic_rule}
Do NOT reuse any of these earlier titles: {recent}.
Format for this video: {fmt}.
Rules:
- Exactly 8 scenes. Each {VK} is {WS} words, short sentences, ONE idea per scene. Scene 1 is at most 10 words.
- Scenes 2-6 give one clear point the viewer can use or remember, with a small joke from the character.
- The last scene sums up the one thing to remember, or loops back to the hook.
{RULES_COMMON}
{JSON_KEYS.format(n=8)}"""


def nwords(s):
    return len(str(s).split())


def length_ok(data):
    """Strict check on scene count, spoken length (Tamil is about 2.2 words per second) and the hook length."""
    scenes = data["scenes"]
    words = sum(nwords(vt(sc)) for sc in scenes)
    hook_ok = nwords(vt(scenes[0])) <= 12
    if KIND == "long":
        return 18 <= len(scenes) <= 26 and (380 if TA else 540) <= words <= (700 if TA else 780) and hook_ok
    return 7 <= len(scenes) <= 9 and (60 if TA else 85) <= words <= (112 if TA else 125) and hook_ok


def sane(data):
    sc = data["scenes"]
    return len(sc) >= 3 and all(vt(s) for s in sc)


def plan(hist):
    prompt = build_prompt(hist)
    key = os.environ["GEMINI_API_KEY"]
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    best = None
    for model in [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        for attempt in range(4):
            try:
                r = requests.post(url, json=body, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {r.status_code} from {model}")
                r.raise_for_status()
                data = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                assert sane(data), "scenes missing narration_ta or caption"
                if length_ok(data):
                    print(f"Script written by {model}")
                    return data
                best = data  # usable, but wrong length or hook: try again, keep as fallback
                print("Script length or hook off, retrying", attempt + 1)
                time.sleep(3)
            except Exception as e:
                wait = 5 * (2 ** attempt) + random.uniform(0, 3)  # ~5s, 10s, 20s, 40s
                print(f"LLM retry ({model}, attempt {attempt + 1}): {e}; waiting {wait:.0f}s")
                time.sleep(wait)
        if best:
            return best
        print(f"{model} gave no usable script, trying next model")
    raise SystemExit("LLM failed on all models")


# ----------------------------------------------------------------------------------------------
# Real stock footage (Pexels / Pixabay)
# ----------------------------------------------------------------------------------------------
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
    order = ["large", "medium"]            # never the small rendition: it looks pixelated when cropped to full screen

    def fits(h):  # does this clip already have the right shape (portrait for Shorts)?
        for k in order:
            v = h.get("videos", {}).get(k, {})
            if v.get("width") and v.get("height"):
                return (v["width"] > v["height"]) if KIND == "long" else (v["height"] > v["width"])
        return False

    for q in tries:
        try:
            r = requests.get("https://pixabay.com/api/videos/",
                             params={"key": PIXABAY, "q": q, "per_page": 20, "safesearch": "true"}, timeout=30)
            r.raise_for_status()
            hits = [h for h in r.json().get("hits", []) if h["id"] not in used_pb and h.get("duration", 0) >= 3]
            random.shuffle(hits)
            hits.sort(key=lambda h: not fits(h))          # right-shaped clips first, still random inside each group
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


def get_stock(queries, dur, scene_no):
    """Fetch 1 clip (2 for longer scenes, so the picture changes mid-scene). Returns a list of file paths."""
    want = 2 if dur >= 4.0 else 1
    got = []
    for k, q in enumerate(queries):
        if len(got) >= want:
            break
        p = OUT / f"{scene_no}_stock{k}.mp4"
        if pexels_clip(q, p) or pixabay_clip(q, p):
            got.append(p)
    return got


# ----------------------------------------------------------------------------------------------
# AI pictures (fallback)
# ----------------------------------------------------------------------------------------------
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


# ----------------------------------------------------------------------------------------------
# Voice (Edge Tamil, free)
# ----------------------------------------------------------------------------------------------
def clean_text(text):
    """Remove emojis, markdown and odd symbols that can make edge-tts return no audio. Keeps Tamil letters AND
    Tamil vowel signs (they are not matched by \\w, so the Tamil block is allowed explicitly)."""
    text = re.sub(r"[*_`#~<>\[\]{}|\\^=+]", " ", str(text))
    text = re.sub(r"[^\w\s.,!?'\-:;%$&()/஀-௿‌‍]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def tts(text, path):
    global VOICE
    text = clean_text(text)
    if not text:
        raise SystemExit("TTS failed: empty narration")
    other = next(v for v in TAMIL_VOICES if v != VOICE) if VOICE in TAMIL_VOICES else TAMIL_VOICES[0]
    # Multilingual voices do not always send word timings, so we fall back step by step. Always a Tamil voice.
    configs = [(VOICE, "WordBoundary"), (VOICE, "SentenceBoundary"), (other, "WordBoundary"), (other, "SentenceBoundary")]
    for attempt in range(6):
        voice, boundary = configs[min(attempt, len(configs) - 1)]
        try:
            words, got = [], False
            comm = edge_tts.Communicate(text, voice, rate=TTS_RATE, pitch=TTS_PITCH, boundary=boundary)
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


# ----------------------------------------------------------------------------------------------
# Captions (Thanglish) with animated looks
# ----------------------------------------------------------------------------------------------
def ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def clean_caption(text):
    text = re.sub(r"[{}\\]", "", str(text))
    text = re.sub(r"[^\w\s.,!?'\-:;%$&()/₹]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def caption_timings(words, caption, dur):
    """Thanglish words do not match the Tamil voice words one to one, so spread them across the
    time the voice is actually speaking, weighted by word length."""
    toks = caption.split()
    if not toks:
        return []
    t0 = words[0][0] if words else 0.0
    t1 = words[-1][1] if words else dur
    t1 = max(t1, t0 + 0.4)
    weights = [len(t) + 1.5 for t in toks]
    total = sum(weights)
    out, cur = [], t0
    for t, w in zip(toks, weights):
        span = (t1 - t0) * w / total
        out.append((cur, cur + span, t))
        cur += span
    return out


def D(layer, s, e, text):
    return f"Dialogue: {layer},{ts(s)},{ts(e)},Default,,0,0,0,,{text}"


def pos_tag():
    return f"\\an2\\pos({W // 2},{H - margin_v()})"


def fx_basic(ch, s, e):
    return [D(0, s, e, POP + " ".join(w[2] for w in ch))]


def fx_kinetic(ch, s, e):
    rot = random.choice([-5, -3, 3, 5])
    t = f"{{{pos_tag()}\\fscx60\\fscy60\\frz{rot}\\t(0,110,\\fscx118\\fscy118)\\t(110,210,\\fscx100\\fscy100\\frz0)\\fad(25,0)}}"
    return [D(0, s, e, t + " ".join(w[2] for w in ch))]


def fx_pullback(ch, s, e):
    t = f"{{{pos_tag()}\\fscx175\\fscy175\\alpha&HFF&\\t(0,320,\\fscx100\\fscy100\\alpha&H00&)}}"
    return [D(0, s, e, t + " ".join(w[2] for w in ch))]


def fx_blur(ch, s, e):
    t = f"{{{pos_tag()}\\blur14\\fscx108\\fscy108\\alpha&H80&\\t(0,280,\\blur0.6\\fscx100\\fscy100\\alpha&H00&)\\fad(0,60)}}"
    return [D(0, s, e, t + " ".join(w[2] for w in ch))]


def fx_slide(ch, s, e):
    x, y = W // 2, H - margin_v()
    t = f"{{\\an2\\move({x + 650},{y},{x},{y},0,190)\\fad(0,40)}}"
    return [D(0, s, e, t + " ".join(w[2] for w in ch))]


def fx_minimal(ch, s, e):
    t = f"{{{pos_tag()}\\fad(90,60)}}"
    return [D(0, s, e, t + " ".join(w[2] for w in ch))]


def fx_neon(ch, s, e):
    col = random.choice([CYN, MAG, "&H0000FF00", "&H000090FF"])
    text = " ".join(w[2] for w in ch)
    glow = f"{{{pos_tag()}\\1a&HFF&\\3c{col}&\\bord14\\blur16\\fad(60,0)}}"      # soft coloured halo behind the text
    main = f"{{{pos_tag()}\\3c{col}&\\bord4\\blur1\\shad0\\fad(60,0)}}"
    return [D(0, s, e, glow + text), D(1, s, e, main + text)]


def fx_glitch(ch, s, e):
    x, y = W // 2, H - margin_v()
    text = " ".join(w[2] for w in ch)
    red = f"{{\\an2\\move({x - 14},{y},{x - 4},{y},0,160)\\1c{RED}&\\3a&HFF&\\bord0\\shad0\\alpha&H30&}}"
    cyan = f"{{\\an2\\move({x + 14},{y},{x + 4},{y},0,160)\\1c{CYN}&\\3a&HFF&\\bord0\\shad0\\alpha&H30&}}"
    main = f"{{{pos_tag()}\\fad(20,0)}}"
    return [D(0, s, e, red + text), D(1, s, e, cyan + text), D(2, s, e, main + text)]


def fx_tech(ch, s, e):
    t = f"{{{pos_tag()}\\1c{WHT}&\\t(0,220,\\1c{CYN}&)\\fad(40,0)}}"
    return [D(0, s, e, t + "[ " + " ".join(w[2] for w in ch) + " ]")]


def fx_karaoke(ch, s, e):
    """Each word lights up (yellow) when it is spoken, inside a dark pill."""
    parts = []
    for i, (ws, we, wt) in enumerate(ch):
        nxt = ch[i + 1][0] if i + 1 < len(ch) else e
        cs = max(1, int(round((nxt - ws) * 100)))
        lead = pos_tag() if i == 0 else ""
        parts.append(f"{{{lead}\\k{cs}}}{wt}" + (" " if i + 1 < len(ch) else ""))
    return [D(0, s, e, "".join(parts))]


def fx_typewriter(ch, s, e):
    """Words appear one by one with a terminal prompt and a cursor."""
    out = []
    for k in range(len(ch)):
        ws = ch[k][0]
        we = ch[k + 1][0] if k + 1 < len(ch) else e
        shown = " ".join(w[2] for w in ch[:k + 1])
        out.append(D(0, ws, we, f"{{{pos_tag()}}}> {shown}_"))
    return out


FX = {"basic": fx_basic, "kinetic": fx_kinetic, "pullback": fx_pullback, "blur": fx_blur, "slide": fx_slide,
      "minimal": fx_minimal, "neon": fx_neon, "glitch": fx_glitch, "tech": fx_tech, "karaoke": fx_karaoke,
      "typewriter": fx_typewriter}


def write_ass(words, caption, dur, path):
    toks = caption_timings(words, clean_caption(caption), dur)
    if not toks:
        toks = [(0.0, dur, "...")]
    if CUR_STYLE["upper"]:
        toks = [(a, b, t.upper()) for a, b, t in toks]
    n = CUR_STYLE.get("chunk", 3 if KIND == "long" else 2)
    chunks = [toks[i:i + n] for i in range(0, len(toks), n)]
    fx = FX[CUR_STYLE["fx"]]
    lines = []
    for k, ch in enumerate(chunks):
        start = ch[0][0]
        end = chunks[k + 1][0][0] if k + 1 < len(chunks) else ch[-1][1] + 0.3
        lines += fx(ch, start, max(end, start + 0.2))
    path.write_text(ass_header() + "\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------------------------
# Video building
# ----------------------------------------------------------------------------------------------
def video_size(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)])
    w, h = out.decode().strip().split(",")[:2]
    return int(w), int(h)


def with_transition(build):
    """Try a random transition; if this ffmpeg does not know it, fall back to a plain fade."""
    tr = random.choice(TRANSITIONS)
    try:
        build(tr)
    except subprocess.CalledProcessError:
        if tr == "fade":
            raise
        print("transition", tr, "failed, using fade")
        build("fade")


def build_stock(vids, total, aud, sub, clip):
    """Real moving clips fill the whole frame (centre crop, no blurred bars). Two clips are joined by a random transition."""
    n, fade = len(vids), 0.35
    if n == 1:
        fade = 0

    def build(tr):
        d = (total + (n - 1) * fade) / n
        inp, parts = [], []
        for k, v in enumerate(vids):
            inp += ["-stream_loop", "-1", "-t", f"{d + 0.6:.2f}", "-i", str(v)]
            parts.append(f"[{k}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                         f"fps=30,setsar=1,format=yuv420p,trim=duration={d:.3f},setpts=PTS-STARTPTS[v{k}]")
        last = "v0"
        for k in range(1, n):
            off = k * (d - fade)
            parts.append(f"[{last}][v{k}]xfade=transition={tr}:duration={fade}:offset={off:.3f}[x{k}]")
            last = f"x{k}"
        parts.append(f"[{last}]{sub},format=yuv420p[vout]")
        run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-i", str(aud),
             "-filter_complex", ";".join(parts), "-map", "[vout]", "-map", f"{n}:a", "-t", f"{total:.2f}", "-r", "30",
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-shortest", str(clip)])
    with_transition(build) if n > 1 else build("fade")


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
    aud, clip = OUT / f"{i}.mp3", OUT / f"{i}.mp4"
    sub = f"subtitles={ass.as_posix()}"
    prompts = sc.get("image_prompts") or ([sc["image_prompt"]] if sc.get("image_prompt") else [])
    prompts = [p for p in prompts if p][:3] or [sc.get("narration_ta", "abstract background")]
    qs = [q for q in (sc.get("search_queries") or []) if q] or ([sc["search_query"]] if sc.get("search_query") else [])

    def stock():
        vids = get_stock(qs, dur, i) if qs else []
        if vids:
            build_stock(vids, dur + 0.2, aud, sub, clip)
            return True
        return False

    def ai():
        if not ai_images_available():
            return False
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
            return True
        return False

    order = [ai, stock] if VISUALS == "ai" else [stock, ai]
    for step in order:
        try:
            if step():
                return clip
        except Exception as e:
            print(f"{step.__name__} failed for scene {i}:", e)
    # Last resort: a moving colour gradient.
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

    def build(tr):
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
            parts.append(f"[{last}][v{k}]xfade=transition={tr}:duration={fade}:offset={off:.3f}[x{k}]")
            last = f"x{k}"
        parts.append(f"[{last}]{sub},format=yuv420p[vout]")
        run(["ffmpeg", "-y", "-loglevel", "error", *inp, "-i", str(aud),
             "-filter_complex", ";".join(parts),
             "-map", "[vout]", "-map", f"{n}:a", "-t", f"{total:.2f}", "-r", "30",
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "44100", "-ac", "2",
             "-shortest", str(clip)])
    with_transition(build) if n > 1 else build("fade")


def add_music(video, final):
    tracks = list(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []
    if not tracks:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
             "-c:v", "copy", "-c:a", "aac", "-ar", "44100", str(final)])
        return
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-stream_loop", "-1", "-i", str(random.choice(tracks)),
         "-filter_complex", "[1:a]volume=0.10[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)])


def main():
    global CUR_STYLE, CUR_NAME, CHARACTER
    print(f"generate.py v8 ({CHANNEL_LANG}) | voice {VOICE} rate {TTS_RATE} pitch {TTS_PITCH} | visuals {VISUALS or 'stock'} | "
          f"pexels {'yes' if PEXELS else 'no'} pixabay {'yes' if PIXABAY else 'no'} | trends {'yes' if read_trends() else 'no'}")
    hist = json.loads(HIST.read_text()) if HIST.exists() else []
    data = plan(hist)
    forced = os.environ.get("CAPTION_STYLE", "").strip().lower()
    pool = [k for k, v in STYLES.items() if v["fx"] != "basic"]     # the plain six only when forced
    CUR_NAME = forced if forced in STYLES else random.choice(pool)
    CUR_STYLE = STYLES[CUR_NAME]
    CHARACTER = str(data.get("character", "")).strip()[:300]
    print("caption style:", CUR_NAME, "| character:", CHARACTER)
    clips = []
    total = 0.0
    script_lines = [data["title"], ""]
    for i, sc in enumerate(data["scenes"]):
        aud, ass = OUT / f"{i}.mp3", OUT / f"{i}.ass"
        voice_text = sc.get("narration_ta") or sc.get("narration", "")
        caption = sc.get("caption") or voice_text
        words = asyncio.run(tts(voice_text, aud))
        dur = duration(aud)
        write_ass(words, caption, dur, ass)
        total += dur
        script_lines += [f"Scene {i + 1}", f"  Tamil:    {voice_text}", f"  Thanglish: {caption}"]
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
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    script_text = "\n".join(script_lines) + f"\n\ncaption style: {CUR_NAME}\n"
    (OUT / "script.txt").write_text(script_text, encoding="utf-8")
    print("----- SCRIPT -----\n" + script_text + "------------------")
    HIST.write_text(json.dumps((hist + [data["title"]])[-100:], ensure_ascii=False), encoding="utf-8")
    print(f"Done ({KIND}, {total:.0f}s):", meta["title"])


if __name__ == "__main__":
    main()
