"""Weekly: collect what is really trending (no AI guessing) into trends_auto.txt.
Sources, all free and without an API key:
  1. Google Trends India: today's trending searches (with the real traffic figure Google shows).
  2. Wikipedia: most-read pages yesterday (English and Tamil), a good sign of what the internet is talking about.
  3. Google News India: headlines from many categories (AI, free tools, games, movies, cricket, world, science,
     animals, food, skills, money saving...). Edit CATEGORIES below to add or remove anything.
Only titles, sources and dates are written. Nothing is invented or summarised, so generate.py can only use real facts.
Your own lines in trends.txt are never touched."""
import datetime, email.utils, re, sys, urllib.parse, xml.etree.ElementTree as ET
import requests

UA = {"User-Agent": "Mozilla/5.0 (shorts-bot trend collector)"}
PER_CATEGORY = 2
OUT = "trends_auto.txt"

# (label, Google News search). Add any topic you like.
CATEGORIES = [
    ("AI video tools",   "AI video generator new"),
    ("Free AI tools",    "free AI tool launch"),
    ("AI news",          "ChatGPT OR Gemini OR Claude OR Grok update"),
    ("AI coding",        "Claude Code OR Cursor OR Copilot AI coding"),
    ("AI plugins",       "AI agents plugins skills MCP"),
    ("Gaming",           "GTA 6"),
    ("Gaming",           "new game release trailer"),
    ("Tech news",        "new smartphone launch India"),
    ("Tech news",        "technology news this week"),
    ("Viral",            "viral video trending India"),
    ("Viral",            "viral AI video trend"),
    ("Movies",           "Tamil cinema new release"),
    ("Movies",           "OTT release this week India"),
    ("Cricket and sports", "cricket India match"),
    ("World news",       "world news today"),
    ("India news",       "India news today"),
    ("Science",          "science discovery space ISRO"),
    ("Animals",          "viral animal video"),
    ("Food",             "viral food trend India"),
    ("Skills and study", "how to learn faster students skills"),
    ("Health habits",    "healthy habits fitness India"),
    ("Money saving",     "personal finance saving tips India"),
]


def rss_items(url):
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    return ET.fromstring(r.text)


def clean(s):
    return re.sub(r"\s+", " ", str(s).replace("|", "-")).strip()


def google_news(query):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:7d")
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    return parse_news(rss_items(url))


def parse_news(root):
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            day = email.utils.parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except Exception:
            day = ""
        if title:
            out.append((clean(title), source or "news", day))
    return out


def google_trends():
    """Today's trending searches in India, with the traffic figure and one related headline if given."""
    root = rss_items("https://trends.google.com/trending/rss?geo=IN")
    return parse_trends(root)


def parse_trends(root):
    out = []
    for it in root.iter("item"):
        term = (it.findtext("title") or "").strip()
        traffic, news = "", ""
        for child in it:
            tag = child.tag.split("}")[-1]
            if tag == "approx_traffic":
                traffic = (child.text or "").strip()
            elif tag == "news_item":
                for sub in child:
                    if sub.tag.split("}")[-1] == "news_item_title" and not news:
                        news = clean(sub.text or "")
        if term:
            line = f"Trending search in India today: {clean(term)}"
            if traffic:
                line += f" (about {traffic} searches)"
            if news:
                line += f". Related headline: {news}"
            out.append(line)
    return out


SKIP_PAGES = ("Main_Page", "Special:", "Wikipedia:", "Portal:", "File:", "Help:", "Category:", "Template:",
              "-", "Talk:", "User:", "முதற்_பக்கம்", "சிறப்பு:", "விக்கிப்பீடியா:")


def wikipedia_top(lang):
    day = datetime.date.today() - datetime.timedelta(days=2)       # the data for 'yesterday' is not always ready
    url = (f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top/{lang}.wikipedia.org/all-access/"
           f"{day.year}/{day.month:02d}/{day.day:02d}")
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    arts = r.json()["items"][0]["articles"]
    out = []
    for a in arts:
        t = a["article"].replace("_", " ")
        if a["article"].startswith(SKIP_PAGES) or a["article"] in SKIP_PAGES:
            continue
        out.append(clean(t))
        if len(out) >= 6:
            break
    return out, day.isoformat()


def main():
    lines, seen = [], set()

    def add(label, text):
        key = re.sub(r"\W+", "", text.lower())[:50]
        if key in seen:
            return False
        seen.add(key)
        lines.append(f"{label} | {text}")
        return True

    try:
        for t in google_trends()[:10]:
            add("Google Trends India", t)
    except Exception as e:
        print("google trends skipped:", e)

    for lang, label in (("en", "Wikipedia English"), ("ta", "Wikipedia Tamil")):
        try:
            pages, day = wikipedia_top(lang)
            for p in pages[:4]:
                add(label, f"Many people read the Wikipedia page '{p}' ({day})")
        except Exception as e:
            print("wikipedia skipped:", lang, e)

    for label, q in CATEGORIES:
        try:
            n = 0
            for title, source, day in google_news(q):
                if add(label, f"Headline: {title} ({source}, {day})"):
                    n += 1
                if n >= PER_CATEGORY:
                    break
        except Exception as e:
            print("skip", q, e)

    if not lines:
        print("nothing fetched; keeping the old file")
        sys.exit(0)
    head = (f"# Auto-collected {datetime.date.today().isoformat()} (Google Trends India, Wikipedia, Google News India). "
            "Titles only: the script may use nothing beyond these words.\n")
    open(OUT, "w", encoding="utf-8").write(head + "\n".join(lines) + "\n")
    print(f"wrote {len(lines)} lines")


if __name__ == "__main__":
    main()
