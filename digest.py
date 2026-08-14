import os
import re
import time
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

HN_KEYWORDS = ["ai agents", "rust", "self-hosted", "compiler"]
HN_MIN_POINTS = 30
MAX_AGE_HOURS = 24

JOB_KEYWORDS = ["backend", "python", "founding engineer", "software engineer",
                "ai", "machine learning", "ml", "data engineer", "data scientist",
                "junior", "new grad", "associate"]
JOB_LIMIT = 150

GREENHOUSE_BOARDS = ["stripe", "airbnb", "databricks", "dropbox", "cloudflare",
                     "gitlab", "twilio", "robinhood", "coinbase", "postman"]
LEVER_SITES = ["meesho", "zeta"]
ASHBY_BOARDS = ["ramp", "notion", "linear", "vanta", "deel"]

FILTER_LOCATION = True
LOCATION_KEEP = ["bengaluru", "bangalore", "mumbai", "hyderabad", "pune",
                 "delhi", "gurugram", "gurgaon", "noida", "chennai",
                 "kolkata", "ahmedabad", "india", "anywhere", "worldwide", "remote"]

ADZUNA_COUNTRIES = ["in"]
ADZUNA_WHERE = ""
ADZUNA_QUERIES = ["junior software engineer", "entry level data", "graduate developer",
                  "software engineer fresher", "machine learning engineer", "python developer"]
ADZUNA_MAX_DAYS = 1
ADZUNA_PAGES = 3

PAPER_TOPIC_POOL = ["large language models", "AI agents", "reinforcement learning",
                    "retrieval augmented generation", "multimodal models",
                    "model quantization efficiency", "LLM evaluation benchmarks",
                    "code generation models", "diffusion models", "AI alignment safety",
                    "graph neural networks", "speech recognition", "vector search",
                    "federated learning", "time series forecasting"]
PAPER_TOPICS_PER_DAY = 3
PAPER_WINDOW_DAYS = 120
PAPER_FIELDS = "title,url,year,publicationDate,citationCount,authors"
PAPER_PER_QUERY = 4

SENIOR_MARKERS = ["senior", "sr.", "sr ", "staff", "principal", "lead", "manager",
                  "director", "head of", "vp ", "vice president", " ii", " iii", " iv"]
ENTRY_MARKERS = ["junior", "jr.", "jr ", "entry", "new grad", "new graduate",
                 "graduate", "associate", "intern", "apprentice", "early career"]
LEVEL_LABEL = {0: "entry", 1: "mid", 2: "senior"}

RECIPIENT = os.environ["DIGEST_TO"]
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
GROQ_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"
ADZUNA_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_KEY = os.environ.get("ADZUNA_APP_KEY")
S2_KEY = os.environ.get("S2_API_KEY")


def cutoff_ts():
    return int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)).timestamp())


def seniority_rank(title):
    t = f" {title.lower()} "
    if any(m in t for m in ENTRY_MARKERS):
        return 0
    if any(m in t for m in SENIOR_MARKERS):
        return 2
    return 1


def job_matches(title):
    t = title.lower()
    return any(re.search(rf"\b{re.escape(k)}\b", t) for k in JOB_KEYWORDS)


def location_ok(loc):
    if not FILTER_LOCATION:
        return True
    t = (loc or "").lower()
    return any(re.search(rf"\b{re.escape(c)}\b", t) for c in LOCATION_KEEP)


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
            })
    seen, unique = set(), []
    for it in sorted(items, key=lambda x: x["points"], reverse=True):
        if it["url"] not in seen:
            seen.add(it["url"])
            unique.append(it)
    return unique


def fetch_greenhouse(token):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", timeout=20)
    r.raise_for_status()
    return [{
        "company": token,
        "title": j["title"],
        "location": (j.get("location") or {}).get("name", ""),
        "url": j["absolute_url"],
        "source": "greenhouse",
    } for j in r.json().get("jobs", []) if job_matches(j["title"])]


def fetch_lever(site):
    r = requests.get(f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"}, timeout=20)
    r.raise_for_status()
    return [{
        "company": site,
        "title": j["text"],
        "location": j.get("categories", {}).get("location", ""),
        "url": j["hostedUrl"],
        "source": "lever",
    } for j in r.json() if job_matches(j["text"])]


def fetch_ashby(board):
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", timeout=20)
    r.raise_for_status()
    return [{
        "company": board,
        "title": j["title"],
        "location": j.get("location", ""),
        "url": j["jobUrl"],
        "source": "ashby",
    } for j in r.json().get("jobs", []) if job_matches(j["title"])]


def fetch_adzuna():
    if not (ADZUNA_ID and ADZUNA_KEY):
        return []
    out = []
    for country in ADZUNA_COUNTRIES:
        for q in ADZUNA_QUERIES:
            for page in range(1, ADZUNA_PAGES + 1):
                params = {
                    "app_id": ADZUNA_ID,
                    "app_key": ADZUNA_KEY,
                    "what": q,
                    "max_days_old": ADZUNA_MAX_DAYS,
                    "results_per_page": 50,
                    "sort_by": "date",
                }
                if ADZUNA_WHERE:
                    params["where"] = ADZUNA_WHERE
                try:
                    r = requests.get(
                        f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
                        params=params, timeout=20,
                    )
                    r.raise_for_status()
                    results = r.json().get("results", [])
                except Exception as e:
                    print(f"adzuna {country} '{q}' page {page} failed: {e}")
                    break
                for j in results:
                    out.append({
                        "company": (j.get("company") or {}).get("display_name", ""),
                        "title": j.get("title", ""),
                        "location": (j.get("location") or {}).get("display_name", ""),
                        "url": j.get("redirect_url", ""),
                        "source": "adzuna",
                    })
                if len(results) < 50:
                    break
                time.sleep(0.5)
    return out


def fetch_jobs():
    jobs = []
    sources = (
        [(t, fetch_greenhouse) for t in GREENHOUSE_BOARDS]
        + [(s, fetch_lever) for s in LEVER_SITES]
        + [(b, fetch_ashby) for b in ASHBY_BOARDS]
        + [(None, fetch_adzuna)]
    )
    for arg, fn in sources:
        try:
            jobs += fn(arg) if arg is not None else fn()
        except Exception as e:
            print(f"{fn.__name__} {arg or ''} failed: {e}")
    seen, unique = set(), []
    for j in jobs:
        if j["url"] and j["url"] not in seen and location_ok(j.get("location", "")):
            seen.add(j["url"])
            j["level"] = seniority_rank(j["title"])
            unique.append(j)
    unique.sort(key=lambda j: j["level"])
    return unique[:JOB_LIMIT]


def todays_topics():
    n = len(PAPER_TOPIC_POOL)
    start = (dt.date.today().toordinal() * PAPER_TOPICS_PER_DAY) % n
    return [PAPER_TOPIC_POOL[(start + i) % n] for i in range(PAPER_TOPICS_PER_DAY)]


def fetch_papers():
    today = dt.date.today()
    since = today - dt.timedelta(days=PAPER_WINDOW_DAYS)
    base = {
        "publicationDateOrYear": f"{since.isoformat()}:{today.isoformat()}",
        "sort": "citationCount:desc",
        "fields": PAPER_FIELDS,
    }
    headers = {"x-api-key": S2_KEY} if S2_KEY else {}
    papers, seen = [], set()
    for q in todays_topics():
        data = None
        for _ in range(2):
            try:
                r = requests.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
                    params={**base, "query": q}, headers=headers, timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(3)
                    continue
                r.raise_for_status()
                data = r.json().get("data") or []
                break
            except Exception as e:
                print(f"papers '{q}' failed: {e}")
                break
        for p in (data or [])[:PAPER_PER_QUERY]:
            url = p.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            papers.append({
                "title": " ".join((p.get("title") or "").split()),
                "url": url,
                "date": p.get("publicationDate") or str(p.get("year") or ""),
                "cites": p.get("citationCount") or 0,
                "topic": q,
                "authors": ", ".join(a.get("name", "") for a in (p.get("authors") or [])[:3]),
            })
        time.sleep(1.2)
    papers.sort(key=lambda x: x["cites"], reverse=True)
    return papers


def summarize(hn):
    if not GROQ_KEY or not hn:
        return None
    listing = "\n".join(f"- {it['title']} ({it['points']} pts)" for it in hn)
    r = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
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
    return r.json()["choices"][0]["message"]["content"].strip()


def build_html(hn, papers, jobs, summary):
    today = dt.date.today().strftime("%A, %d %B %Y")
    p = ['<div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#1a1a1a">']
    p.append(f'<h1 style="font-size:20px">Morning digest &mdash; {today}</h1>')
    if summary:
        p.append(f'<p style="background:#f4f4f4;padding:10px;border-radius:6px;font-size:14px">{summary}</p>')

    p.append('<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px">Hacker News</h2>')
    if hn:
        for it in hn:
            p.append(
                f'<p style="margin:8px 0"><a href="{it["url"]}" style="color:#0a58ca;text-decoration:none;font-weight:bold">{it["title"]}</a>'
                f'<br><span style="color:#666;font-size:12px">{it["points"]} points &middot; '
                f'<a href="{it["comments"]}" style="color:#666">comments</a></span></p>'
            )
    else:
        p.append('<p style="color:#666">Nothing above threshold.</p>')

    p.append('<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px">Research papers (recent, most cited)</h2>')
    if papers:
        for pp in papers:
            p.append(
                f'<p style="margin:8px 0"><a href="{pp["url"]}" style="color:#0a58ca;text-decoration:none;font-weight:bold">{pp["title"]}</a>'
                f'<br><span style="color:#666;font-size:12px">{pp["cites"]} citations &middot; {pp["date"]} &middot; {pp["topic"]} &middot; {pp["authors"]}</span></p>'
            )
    else:
        p.append('<p style="color:#666">No papers today.</p>')

    p.append('<h2 style="font-size:16px;border-bottom:1px solid #ddd;padding-bottom:4px">Jobs (entry-level first)</h2>')
    if jobs:
        for j in jobs:
            level = LEVEL_LABEL[j["level"]]
            tag_color = {"entry": "#0a7c2f", "mid": "#666", "senior": "#999"}[level]
            p.append(
                f'<p style="margin:8px 0"><a href="{j["url"]}" style="color:#0a58ca;text-decoration:none;font-weight:bold">{j["title"]}</a>'
                f'<br><span style="color:#666;font-size:12px">{j["company"]} &middot; {j["location"]} &middot; {j["source"]} &middot; '
                f'<span style="color:{tag_color};font-weight:bold">{level}</span></span></p>'
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


def safe(fn, default):
    try:
        return fn()
    except Exception as e:
        print(f"{fn.__name__} failed: {e}")
        return default


def main():
    hn = safe(fetch_hackernews, [])
    papers = safe(fetch_papers, [])
    jobs = safe(fetch_jobs, [])
    summary = safe(lambda: summarize(hn), None)
    send_email(build_html(hn, papers, jobs, summary))
    print(f"sent {len(hn)} HN items, {len(papers)} papers, {len(jobs)} jobs")


if __name__ == "__main__":
    main()
