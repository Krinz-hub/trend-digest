"""YouTube idea generator.

Finds fast-rising YouTube videos in your niche (views per hour, and views vs the
channel's size), mixes them with today's AI/dev news, and asks Gemini for video
ideas tailored to your channel. Sends the result to Telegram.

Free: YouTube Data API (10,000 units/day; this uses ~900), Gemini free tier.
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import main  # reuses news collectors and the Telegram sender

YT_KEY = os.environ["YOUTUBE_API_KEY"]
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
NUM_IDEAS = int(os.environ.get("NUM_IDEAS", "6"))
LANG = os.environ.get("VIDEO_LANGUAGE", "English")

CHANNEL_DESC = (
    "A solo creator who builds cool projects with trendy tech, tests trendy GitHub "
    "repos, covers AI news, and explores AI / LLM / ML hands-on, sharing the "
    "experience in an interesting, mind-blowing way. Makes both long videos and Shorts."
)

# Each search costs 100 quota units. 8 searches = 800 of your 10,000 daily units.
QUERIES = [
    "AI agents project",
    "open source LLM new",
    "GitHub trending AI repo",
    "I built with AI",
    "local LLM running",
    "MCP server tutorial",
    "AI coding agent",
    "machine learning project",
]

# Optional: add channel IDs of creators you want to track (looks like UCxxxx...).
TRACK_CHANNEL_IDS = []

SEEN_FILE = Path("seen_yt.json")


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except Exception:
        return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-2000:]))


def yt_get(endpoint, **params):
    params["key"] = YT_KEY
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def search_videos():
    after = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = set()
    for q in QUERIES:
        try:
            data = yt_get("search", part="id", q=q, type="video", order="viewCount",
                          publishedAfter=after, maxResults=15, relevanceLanguage="en")
            ids.update(i["id"]["videoId"] for i in data.get("items", []))
        except Exception as e:
            print("search error", q, e)
    for cid in TRACK_CHANNEL_IDS:
        try:
            data = yt_get("search", part="id", channelId=cid, type="video", order="date",
                          publishedAfter=after, maxResults=5)
            ids.update(i["id"]["videoId"] for i in data.get("items", []))
        except Exception as e:
            print("channel error", cid, e)
    return list(ids)


def details(video_ids):
    videos = []
    for i in range(0, len(video_ids), 50):
        data = yt_get("videos", part="snippet,statistics,contentDetails",
                      id=",".join(video_ids[i:i + 50]))
        videos.extend(data.get("items", []))
    channel_ids = list({v["snippet"]["channelId"] for v in videos})
    subs = {}
    for i in range(0, len(channel_ids), 50):
        data = yt_get("channels", part="statistics", id=",".join(channel_ids[i:i + 50]))
        for c in data.get("items", []):
            subs[c["id"]] = int(c["statistics"].get("subscriberCount", 0) or 0)
    now = datetime.now(timezone.utc)
    out = []
    for v in videos:
        views = int(v["statistics"].get("viewCount", 0) or 0)
        published = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        hours = max((now - published).total_seconds() / 3600, 1)
        s = subs.get(v["snippet"]["channelId"], 0)
        outlier = views / max(s, 1000)
        out.append({
            "id": v["id"],
            "title": v["snippet"]["title"],
            "channel": v["snippet"]["channelTitle"],
            "views": views,
            "vph": views / hours,
            "outlier": outlier,
            "subs": s,
            "score": (views / hours) * (1 + min(outlier, 20)),
            "url": f"https://youtu.be/{v['id']}",
        })
    return out


def pick_top(videos, seen, n=10):
    fresh = [v for v in videos if v["id"] not in seen and v["views"] >= 1500]
    return sorted(fresh, key=lambda v: v["score"], reverse=True)[:n]


def get_news(n=8):
    items = main.fetch_hn() + main.fetch_github() + main.fetch_rss()
    return main.pick_top(items, set())[:n]


def ask_gemini(top_videos, news):
    if not GEMINI_KEY:
        return None
    vids = "\n".join(
        f"- \"{v['title']}\" by {v['channel']} | {v['views']:,} views, "
        f"{v['vph']:.0f}/hr, {v['outlier']:.1f}x its channel size" for v in top_videos)
    news_txt = "\n".join(f"- [{i['source']}] {i['title']}" for i in news)
    prompt = f"""You are a YouTube strategist for this channel: {CHANNEL_DESC}
Video language: {LANG}.

TRENDING YOUTUBE VIDEOS (last 72h, fast-rising in the niche):
{vids}

TRENDING NEWS / REPOS TODAY:
{news_txt}

Create {NUM_IDEAS} mind-blowing video ideas this creator can actually make by building,
testing or exploring something hands-on. Mix formats. Prefer ideas where a topic is both
in the news AND already getting views. Do not copy existing videos: find a fresher angle.
Return ONLY a JSON list of objects with keys:
"title" (clickable, under 70 chars, no clickbait lies),
"thumbnail" (3-4 word thumbnail text),
"format" ("Short", "Long" or "Both"),
"hook" (opening line, under 20 words),
"plan" (2 short sentences on what exactly to build/test on camera),
"why" (one line: the trend signal behind it)."""
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json"}},
            timeout=90)
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as e:
        print("Gemini error", e)
        return None


def build_message(ideas, top_videos):
    esc = html.escape
    lines = ["<b>Video ideas for today</b>\n"]
    if ideas:
        for n, d in enumerate(ideas, 1):
            lines.append(f"<b>{n}. {esc(str(d.get('title', '')))}</b>")
            lines.append(f"Thumbnail: {esc(str(d.get('thumbnail', '')))} | {esc(str(d.get('format', '')))}")
            lines.append(f"Hook: <i>{esc(str(d.get('hook', '')))}</i>")
            lines.append(esc(str(d.get("plan", ""))))
            lines.append(f"Why now: {esc(str(d.get('why', '')))}\n")
    else:
        lines.append("(Gemini unavailable, showing raw trending videos only)\n")
    lines.append("<b>Fast-rising videos in your niche</b>")
    for v in top_videos[:5]:
        lines.append(
            f"- <a href=\"{esc(v['url'])}\">{esc(v['title'])}</a> "
            f"({v['views']:,} views, {v['vph']:.0f}/hr)")
    return "\n".join(lines)


def run():
    seen = load_seen()
    top = pick_top(details(search_videos()), seen)
    if not top:
        print("no fresh videos found")
        return
    ideas = ask_gemini(top, get_news())
    main.send_telegram(build_message(ideas, top))
    seen.update(v["id"] for v in top)
    save_seen(seen)


if __name__ == "__main__":
    run()
