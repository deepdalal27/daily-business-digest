"""Build a daily PDF market briefing from public business-news RSS feeds and email it.

Sources: The Economic Times, Business Standard, Financial Express. Every story
is ranked High / Medium / Low importance for an Indian equity investor (see
importance.py), grouped by theme, and listed with headline, the publisher's
summary, time and a link to the full article.

Environment variables:
  GMAIL_ADDRESS       Gmail account that sends the mail
  GMAIL_APP_PASSWORD  16-character Gmail App Password for that account
  MAIL_TO             recipient(s), comma separated (defaults to GMAIL_ADDRESS)
  LOOKBACK_HOURS      only include stories newer than this (default 26)
If the Gmail variables are missing, the PDF is built but not emailed.
"""

import html
import json
import os
import re
import smtplib
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

import importance
import layout

IST = timezone(timedelta(hours=5, minutes=30))
HERE = Path(__file__).parent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-IN,en;q=0.9",
}


# ---------------------------------------------------------------- fetching

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def clean_text(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt


def parse_feed(data, via_google_news=False):
    root = ET.fromstring(data)
    items = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        summary = clean_text(item.findtext("description"))
        if via_google_news:
            # Google News titles end with " - <Publisher>"; its description only repeats the title.
            source = clean_text(item.findtext("source"))
            if source and title.endswith(" - " + source):
                title = title[: -len(source) - 3].strip()
            summary = ""
        published = parse_date(item.findtext("pubDate"))
        if title and link:
            items.append({"title": title, "link": link, "summary": summary, "published": published})
    return items


def normalise(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def collect(feeds, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    paper_sections, errors = {}, []
    for paper, sections in feeds.items():
        seen = set()
        paper_sections[paper] = {}
        for section, urls in sections.items():
            # A section may list several URLs: the publisher's feed first, then fallbacks.
            items = None
            for url in [urls] if isinstance(urls, str) else urls:
                try:
                    items = parse_feed(fetch(url), via_google_news="news.google.com" in url)
                    break
                except Exception as exc:  # one bad feed must not stop the digest
                    print(f"WARN {paper} / {section}: {url}: {exc}")
            if items is None:
                errors.append(f"{paper} / {section}")
                continue
            kept = []
            for it in items:
                key = normalise(it["title"])
                if key in seen:
                    continue
                if it["published"] and it["published"] < cutoff:
                    continue
                seen.add(key)
                kept.append(it)
            kept.sort(key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            if kept:
                paper_sections[paper][section] = kept
    return paper_sections, errors



# ---------------------------------------------------------------- ranking

PAPER_CODES = {"The Economic Times": "ET", "Business Standard": "BS", "Financial Express": "FE"}
STOPWORDS = set("the a an of to in on for and or as at by with from is are was be its it this that after over amid says said rs crore lakh".split())


def words(title):
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2 and w not in STOPWORDS}


def rank(paper_sections):
    """Score every story, merge the same story carried by several papers, split into tiers."""
    stories = []
    for paper, sections in paper_sections.items():
        for section, items in sections.items():
            for it in items:
                sc, tier, cats, theme = importance.score(it["title"], it["summary"])
                stories.append({**it, "paper": paper, "section": section, "score": sc, "tier": tier,
                                "cats": cats, "theme": theme, "also": [], "words": words(it["title"])})

    stories.sort(key=lambda s: -s["score"])
    kept = []
    for st in stories:
        for k in kept:
            common = st["words"] & k["words"]
            if len(common) >= 3 and len(common) / max(1, min(len(st["words"]), len(k["words"]))) >= 0.6:
                if st["paper"] != k["paper"] and st["paper"] not in k["also"]:
                    k["also"].append(st["paper"])
                break
        else:
            kept.append(st)

    latest = datetime.min.replace(tzinfo=timezone.utc)
    tiers = {importance.HIGH: [], importance.MEDIUM: [], importance.LOW: []}
    for st in kept:
        tiers[st["tier"]].append(st)
    for tier_items in tiers.values():
        tier_items.sort(key=lambda s: (-s["score"], -(s["published"] or latest).timestamp()))
    return tiers


def by_theme(items):
    order = list(importance.THEMES) + [importance.OTHER_THEME]
    groups = {t: [] for t in order}
    for it in items:
        groups[it["theme"]].append(it)
    return [(t, g) for t, g in groups.items() if g]


# ---------------------------------------------------------------- PDF

def build_pdf(tiers, errors, out_path):
    layout.build(tiers, errors, out_path, PAPER_CODES, IST)
    return sum(len(v) for v in tiers.values())


# ---------------------------------------------------------------- email

def email_html(tiers):
    """A short, styled email body: counts and the high-importance headlines."""
    e = lambda t: html.escape(t, quote=True)
    high, medium, low = tiers[importance.HIGH], tiers[importance.MEDIUM], tiers[importance.LOW]
    today = datetime.now(IST).strftime("%A, %d %B %Y")

    def tile(label, n, colour):
        return (f'<td style="padding:10px 12px;background:#F4F6FA;border-top:3px solid {colour};">'
                f'<div style="font-size:10px;font-weight:bold;color:#667085;letter-spacing:.5px">{label}</div>'
                f'<div style="font-size:24px;font-weight:bold;color:{colour};font-family:Georgia,serif">{n}</div></td>')

    rows = "".join(
        f'<tr><td style="padding:10px 0 10px 12px;border-left:3px solid #B42318;border-bottom:1px solid #E4E7EC">'
        f'<div style="font-size:10px;font-weight:bold;color:#B42318;letter-spacing:.5px">{e(it["theme"].upper())}</div>'
        f'<a href="{e(it["link"])}" style="font-size:15px;font-weight:bold;color:#1A1D23;text-decoration:none">{e(it["title"])}</a>'
        f'<div style="font-size:11px;color:#667085">{e(PAPER_CODES.get(it["paper"], it["paper"]))}</div></td></tr>'
        for it in (high or medium)[:10])
    label = "High importance" if high else "Top stories"
    return f"""<html><body style="margin:0;background:#ffffff;font-family:Calibri,Arial,sans-serif;color:#1A1D23">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto">
<tr><td style="background:#0B1F3A;padding:20px 22px;border-bottom:4px solid #C9A227">
<div style="color:#C9A227;font-size:11px;font-weight:bold;letter-spacing:1px">MORNING BRIEF · INDIAN EQUITIES</div>
<div style="color:#ffffff;font-size:26px;font-family:Georgia,serif;font-weight:bold;margin-top:4px">Daily Market Briefing</div>
<div style="color:#C8D3E6;font-size:13px;margin-top:4px">{today}</div></td></tr>
<tr><td style="padding:16px 0"><table width="100%" cellspacing="6" cellpadding="0"><tr>
{tile("HIGH", len(high), "#B42318")}{tile("MEDIUM", len(medium), "#B54708")}{tile("LOW", len(low), "#475467")}
</tr></table></td></tr>
<tr><td style="font-size:12px;font-weight:bold;color:#667085;letter-spacing:.5px;padding-bottom:4px">{label.upper()}</td></tr>
<tr><td><table width="100%" cellspacing="0" cellpadding="0">{rows}</table></td></tr>
<tr><td style="padding:16px 0;font-size:13px">The full briefing, with every story ranked and summarised, is in the attached PDF.</td></tr>
<tr><td style="font-size:11px;color:#98A2B3;padding-bottom:20px">News summary from public RSS feeds, ranked by keyword rules. Not investment advice.</td></tr>
</table></body></html>"""


def send_email(pdf_path, tiers, total):
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not sender or not password:
        print("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set; skipping email.")
        return False
    recipients = [r.strip() for r in (os.environ.get("MAIL_TO") or sender).split(",") if r.strip()]
    today = datetime.now(IST).strftime("%d %b %Y")
    high = tiers[importance.HIGH]

    lines = [f"Good morning! {len(high)} high, {len(tiers[importance.MEDIUM])} medium and "
             f"{len(tiers[importance.LOW])} low importance stories today.", "", "HIGH IMPORTANCE"]
    for it in high[:12]:
        lines.append(f"  • {it['title']}  ({PAPER_CODES.get(it['paper'], it['paper'])})")
    if not high:
        lines.append("  (none today)")
    lines += ["", "The full briefing, with summaries and links, is attached as a PDF.",
              "This is a news summary, not investment advice."]

    msg = EmailMessage()
    msg["Subject"] = f"Market Briefing {today}: {len(high)} high-importance stories"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("\n".join(lines))
    msg.add_alternative(email_html(tiers), subtype="html")
    msg.add_attachment(pdf_path.read_bytes(), maintype="application", subtype="pdf", filename=pdf_path.name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    print(f"Emailed {pdf_path.name} to {', '.join(recipients)}")
    return True


def main():
    feeds = json.loads((HERE / "feeds.json").read_text())
    lookback = int(os.environ.get("LOOKBACK_HOURS", "26"))
    paper_sections, errors = collect(feeds, lookback)
    for e in errors:
        print("UNAVAILABLE", e)

    tiers = rank(paper_sections)
    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)
    pdf_path = out_dir / f"Market-Briefing-{datetime.now(IST):%Y-%m-%d}.pdf"
    total = build_pdf(tiers, errors, pdf_path)
    print(f"Built {pdf_path}: " + ", ".join(f"{len(v)} {k}" for k, v in tiers.items()))
    if total == 0:
        print("No stories fetched; not sending an empty digest.")
        return 1
    send_email(pdf_path, tiers, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
