import os
import json
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

HN_KEYWORDS = ["ai agents", "rust", "self-hosted", "compiler"]
JOB_KEYWORDS = ["backend", "python", "founding engineer", "senior software", "AI", "machine learning", "ML", "data engineer", "data scientist"]
HN_MIN_POINTS = 30
MAX_AGE_HOURS = 24

GREENHOUSE_BOARDS = ["stripe", "airbnb"]
LEVER_SITES = ["netflix"]
ASHBY_BOARDS = ["ramp"]

RECIPIENT = os.environ["DIGEST_TO"]
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")


def cutoff_ts():
    return int((dt.datetime.utcnow() - dt.timedelta(hours=MAX_AGE_HOURS)).timestamp())


def fetch_hackernews():
    ts = cutoff_ts()
    items = []
    for kw in HN_KEYWORDS:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": kw,
                "tags": "story",
                "numericFilters": f"created_at_i>{ts},points>{HN_MIN_POINTS}",
            },
            timeout=20,
        )
        r.raise_for_status()
        for h in r.json().get("hits", []):
            oid = h["objectID"]
            items.append({
                "title": h.get("title") or h.get("story_title") or "",
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                "points": h.get("points", 0),
                "comments": f"https://news.ycombinator.com/item?id={oid}",
                "matched": kw,
            })
    seen, unique = set(), []
    for it in sorted(items, key=lambda x: x["points"], reverse=True):
        if it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)
    return unique


def job_matches(title):
    t = title.lower()
    return any(k in t for k in JOB_KEYWORDS)


def fetch_greenhouse(token):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", timeout=20)
    r.raise_for_status()
    return [{
        "company": token,
        "title": j["title"],
        "location": (j.get("location") or {}).get("name", ""),
        "url": j["absolute_url"],
    } for j in r.json().get("jobs", []) if job_matches(j["title"])]


def fetch_lever(site):
    r = requests.get(f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"}, timeout=20)
    r.raise_for_status()
    return [{
        "company": site,
        "title": j["text"],
        "location": j.get("categories", {}).get("location", ""),
        "url": j["hostedUrl"],
    } for j in r.json() if job_matches(j["text"])]


def fetch_ashby(board):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=20)
    r.raise_for_status()
    return [{
        "company": board,
        "title": j["title"],
        "location": j.get("location", ""),
        "url": j["jobUrl"],
    } for j in r.json().get("jobs", []) if job_matches(j["title"])]


def fetch_jobs():
    jobs = []
    for token, fn in (
        [(t, fetch_greenhouse) for t in GREENHOUSE_BOARDS]
        + [(s, fetch_lever) for s in LEVER_SITES]
        + [(b, fetch_ashby) for b in ASHBY_BOARDS]
    ):
        try:
            jobs += fn(token)
        except Exception as e:
            print(f"{fn.__name__} {token} failed: {e}")
    return jobs


def summarize_with_claude(hn):
    if not ANTHROPIC_KEY or not hn:
        return None
    listing = "\n".join(f"- {it['title']} ({it['points']} pts)" for it in hn)
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 400,
            "messages": [{
                "role": "user",
                "content": "In 3 sentences, tell me what today's tech stories are "
                           "collectively about. Be plain and specific.\n\n" + listing,
            }],
        },
        timeout=30,
    )
    r.raise_for_status()
    return "".join(b["text"] for b in r.json()["content"] if b["type"] == "text").strip()


def build_html(hn, jobs, summary):
    today = dt.date.today().strftime("%A, %d %B %Y")
    p = [f'<div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#1a1a1a">']
    p.append(f'<h1 style="font-size:20px">Morning digest &mdash; {today}</h1>')
    if summary:
        p.append(f'<p style="background:#f4f4f4;padding:10px;border-radius:6px;font-size:14px">{summary}</p>')

    p.append('<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px">Hacker News</h2>')
    if hn:
        for it in hn:
            p.append(
                f'<p style="margin:8px 0"><a href="{it["url"]}" style="color:#0a58ca;text-decoration:none;font-weight:bold">{it["title"]}</a>'
                f'<br><span style="color:#666;font-size:12px">{it["points"]} points &middot; '
                f'<a href="{it["comments"]}" style="color:#666">comments</a> &middot; matched &ldquo;{it["matched"]}&rdquo;</span></p>'
            )
    else:
        p.append('<p style="color:#666">Nothing above threshold.</p>')

    p.append('<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px">Jobs</h2>')
    if jobs:
        for j in jobs:
            p.append(
                f'<p style="margin:8px 0"><a href="{j["url"]}" style="color:#0a58ca;text-decoration:none;font-weight:bold">{j["title"]}</a>'
                f'<br><span style="color:#666;font-size:12px">{j["company"]} &middot; {j["location"]}</span></p>'
            )
    else:
        p.append('<p style="color:#666">No matching roles today.</p>')

    p.append('</div>')
    return "".join(p)


def send_email(html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Morning digest \u2014 {dt.date.today().isoformat()}"
    msg["From"] = SMTP_USER
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [RECIPIENT], msg.as_string())


def main():
    hn = fetch_hackernews()
    jobs = fetch_jobs()
    summary = summarize_with_claude(hn)
    send_email(build_html(hn, jobs, summary))
    print(f"sent {len(hn)} HN items and {len(jobs)} jobs")


if __name__ == "__main__":
    main()
