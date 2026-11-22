"""Trend digest agent: collects AI/dev news, summarizes with Gemini (free tier),
sends content ideas to Telegram. Designed to run on GitHub Actions for free."""
import html
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
TOP_N = int(os.environ.get("TOP_N", "6"))

SEEN_FILE = Path("seen.json")
UA = {"User-Agent": "trend-digest-bot/1.0"}
KEYWORDS = ["ai", "llm", "agent", "openai", "anthropic", "claude", "gemini",
            "github", "gpt", "mcp", "copilot", "cursor", "deepseek", "nvidia",
            "open source", "model", "coding"]

RSS_FEEDS = {
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "r/LocalLLaMA": "https://www.reddit.com/r/LocalLLaMA/top/.rss?t=day",
    "r/MachineLearning": "https://www.reddit.com/r/MachineLearning/top/.rss?t=day",
}


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except Exception:
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-3000:]))


def fetch_hn():
    since = int(time.time()) - 24 * 3600
    items = {}
    for q in ["AI", "LLM", "agent", "OpenAI", "Anthropic", "Claude", "Gemini", "GitHub"]:
        try:
            r = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": q, "tags": "story",
                        "numericFilters": f"points>60,created_at_i>{since}",
                        "hitsPerPage": 15},
                timeout=20)
            for h in r.json().get("hits", []):
                url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
                items[url] = {"title": h["title"], "url": url, "source": "Hacker News",
                              "score": h.get("points", 0) + 2 * h.get("num_comments", 0)}
        except Exception as e:
            print("HN error", q, e)
    return list(items.values())


def fetch_github():
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    headers = dict(UA)
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    items = []
    for topic in ["llm", "ai-agents", "mcp", "generative-ai"]:
        try:
            r = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": f"topic:{topic} created:>{since} stars:>100",
                        "sort": "stars", "order": "desc", "per_page": 8},
                headers=headers, timeout=20)
            for repo in r.json().get("items", []):
                desc = repo.get("description") or ""
                items.append({"title": f"{repo['full_name']} - {desc}"[:200],
                              "url": repo["html_url"], "source": "GitHub",
                              "score": repo["stargazers_count"] / 8})
        except Exception as e:
            print("GitHub error", topic, e)
    return items


def fetch_rss():
    items = []
    cutoff = time.time() - 48 * 3600
    for name, url in RSS_FEEDS.items():
        try:
            resp = requests.get(url, headers=UA, timeout=20)
            feed = feedparser.parse(resp.content)
            for e in feed.entries[:15]:
                ts = time.mktime(e.published_parsed) if getattr(e, "published_parsed", None) else time.time()
                if ts < cutoff:
                    continue
                age_h = (time.time() - ts) / 3600
                base = 120 if name.startswith("r/") else 90
                items.append({"title": e.title, "url": e.link, "source": name,
                              "score": max(base - age_h * 2, 10)})
        except Exception as e:
            print("RSS error", name, e)
    return items


def relevant(item):
    t = item["title"].lower()
    return any(k in t for k in KEYWORDS)


def pick_top(items, seen):
    fresh = [i for i in items if i["url"] not in seen and relevant(i)]
    # light title dedupe
    uniq, titles = [], set()
    for i in sorted(fresh, key=lambda x: x["score"], reverse=True):
        key = i["title"].lower()[:40]
        if key in titles:
            continue
        titles.add(key)
        uniq.append(i)
    # keep some source variety: max 3 per source
    out, count = [], {}
    for i in uniq:
        if count.get(i["source"], 0) >= 3:
            continue
        count[i["source"]] = count.get(i["source"], 0) + 1
        out.append(i)
        if len(out) == TOP_N:
            break
    return out


def summarize(items):
    if not GEMINI_KEY:
        return None
    listing = "\n".join(f"{n}. [{i['source']}] {i['title']} ({i['url']})"
                        for n, i in enumerate(items, 1))
    prompt = (
        "You help a tech YouTuber find video topics. For each news item below return JSON: "
        'a list of objects {"n": int, "summary": "2 short sentences, plain English", '
        '"hook": "punchy first line for a YouTube Short, under 15 words", '
        '"format": "Short" or "Long" or "Both", '
        '"why": "one short line on why viewers will care"}. '
        "Only use what the title implies; do not invent facts.\n\n" + listing)
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json"}},
            timeout=60)
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return {d["n"]: d for d in json.loads(text)}
    except Exception as e:
        print("Gemini error", e)
        return None


def build_message(items, notes):
    esc = html.escape
    now = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b, %I:%M %p")
    lines = [f"<b>Trend digest</b> | {now}\n"]
    for n, i in enumerate(items, 1):
        lines.append(f"<b>{n}. {esc(i['title'])}</b>")
        lines.append(f"<i>{esc(i['source'])}</i>")
        d = (notes or {}).get(n)
        if d:
            lines.append(esc(d.get("summary", "")))
            lines.append(f"Hook: <i>{esc(d.get('hook', ''))}</i>")
            lines.append(f"Format: {esc(d.get('format', ''))} | {esc(d.get('why', ''))}")
        lines.append(f"<a href=\"{esc(i['url'])}\">Open</a>\n")
    return "\n".join(lines)


def send_telegram(text):
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20)
        r.raise_for_status()


def main():
    seen = load_seen()
    items = fetch_hn() + fetch_github() + fetch_rss()
    print(f"collected {len(items)} items")
    top = pick_top(items, seen)
    if not top:
        print("nothing new")
        return
    notes = summarize(top)
    send_telegram(build_message(top, notes))
    seen.update(i["url"] for i in top)
    save_seen(seen)


if __name__ == "__main__":
    main()
