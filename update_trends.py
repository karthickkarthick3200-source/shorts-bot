"""Weekly: collect this week's real headlines (Google News RSS, India edition, no API key) into trends_auto.txt.
Only the headline, source and date are written. Nothing is invented or summarised by an AI, so generate.py can
only use facts that a real news site published. Your own lines in trends.txt are never touched."""
import datetime, email.utils, re, sys, urllib.parse, xml.etree.ElementTree as ET
import requests

# (topic label used in the file, search query). Edit freely.
TOPICS = [
    ("AI tools", "new AI tool launch"),
    ("AI news", "ChatGPT OR Gemini OR Claude AI update"),
    ("Gaming", "GTA 6"),
    ("Gaming", "video game release this week"),
    ("Tech news", "smartphone launch India"),
    ("Tech news", "technology India"),
    ("Viral", "viral video trending India"),
]
PER_TOPIC = 2
OUT = "trends_auto.txt"


def fetch(query):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:7d")
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return parse(r.text)


def parse(xml_text):
    out = []
    for it in ET.fromstring(xml_text).iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            day = email.utils.parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except Exception:
            day = ""
        title = re.sub(r"\s+", " ", title.replace("|", "-"))
        if title:
            out.append((title, source or "news", day))
    return out


def main():
    lines, seen = [], set()
    for label, q in TOPICS:
        try:
            items = fetch(q)
        except Exception as e:
            print("skip", q, e)
            continue
        n = 0
        for title, source, day in items:
            key = re.sub(r"\W+", "", title.lower())[:50]
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"{label} | Headline: {title} ({source}, {day})")
            n += 1
            if n >= PER_TOPIC:
                break
    if not lines:
        print("no headlines fetched; keeping the old file")
        sys.exit(0)
    head = (f"# Auto-collected {datetime.date.today().isoformat()} from Google News (India). "
            "Headlines only: the script may use nothing beyond these words.\n")
    open(OUT, "w", encoding="utf-8").write(head + "\n".join(lines) + "\n")
    print(f"wrote {len(lines)} lines")


if __name__ == "__main__":
    main()
