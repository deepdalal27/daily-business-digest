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
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

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

def register_fonts():
    base = Path("/usr/share/fonts/truetype/dejavu")
    try:
        pdfmetrics.registerFont(TTFont("Body", base / "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("Body-Bold", base / "DejaVuSans-Bold.ttf"))
        return "Body", "Body-Bold"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


def esc(text):
    return html.escape(text, quote=True)


def shorten(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


READ_MORE = '<font color="#1f5fa8">Read full article →</font>'
TIER_COLOURS = {importance.HIGH: "#b42318", importance.MEDIUM: "#b54708", importance.LOW: "#475467"}
TIER_NOTES = {
    importance.HIGH: "Can move the Nifty, rates, the rupee or a large-cap today. Read these first.",
    importance.MEDIUM: "Stock-, sector- or IPO-specific news worth knowing before the open.",
    importance.LOW: "Background reading. Headlines only; tap to open.",
}


def build_pdf(tiers, errors, out_path):
    font, bold = register_fonts()
    navy = colors.HexColor("#14213d")
    grey = colors.HexColor("#555555")
    s = {
        "masthead": ParagraphStyle("m", fontName=bold, fontSize=22, leading=26, alignment=TA_CENTER, textColor=navy),
        "sub": ParagraphStyle("sb", fontName=bold, fontSize=11, leading=14, alignment=TA_CENTER, textColor=grey),
        "dateline": ParagraphStyle("d", fontName=font, fontSize=9, leading=13, alignment=TA_CENTER, textColor=grey),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=13, leading=17, textColor=navy, spaceBefore=6, spaceAfter=3),
        "tier": ParagraphStyle("t", fontName=bold, fontSize=17, leading=21, spaceAfter=2),
        "note": ParagraphStyle("n", fontName=font, fontSize=8.5, leading=11, textColor=grey, spaceAfter=4),
        "theme": ParagraphStyle("th", fontName=bold, fontSize=11.5, leading=15, textColor=navy, spaceBefore=8, spaceAfter=4),
        "headline": ParagraphStyle("h", fontName=bold, fontSize=10.2, leading=13.2),
        "summary": ParagraphStyle("b", fontName=font, fontSize=8.8, leading=11.8, textColor=colors.HexColor("#222222")),
        "meta": ParagraphStyle("mt", fontName=font, fontSize=7.3, leading=9.5, textColor=grey),
        "brief": ParagraphStyle("br", fontName=font, fontSize=9.3, leading=12.5, leftIndent=10, bulletIndent=0, spaceAfter=2),
        "low": ParagraphStyle("lo", fontName=font, fontSize=8.3, leading=11, leftIndent=10, bulletIndent=0, spaceAfter=1),
        "body": ParagraphStyle("bd", fontName=font, fontSize=9.5, leading=13),
    }

    def link(it, text):
        return f'<link href="{esc(it["link"])}">{text}</link>'

    def source(it):
        papers = [PAPER_CODES.get(it["paper"], it["paper"])] + [PAPER_CODES.get(p, p) for p in it["also"]]
        when = it["published"].astimezone(IST).strftime("%d %b %I:%M %p") if it["published"] else ""
        return f'{" + ".join(papers)} · {esc(it["section"])} · {when}'

    def tags(it):
        return " · ".join(c for c in it["cats"][:3])

    now = datetime.now(IST)
    counts = {t: len(v) for t, v in tiers.items()}
    story = [
        Paragraph("Daily Market Briefing", s["masthead"]),
        Paragraph("for the Indian equity investor", s["sub"]),
        Paragraph(now.strftime("%A, %d %B %Y") + " &nbsp;|&nbsp; The Economic Times · Business Standard · Financial Express", s["dateline"]),
        Spacer(1, 3 * mm),
        HRFlowable(width="100%", thickness=1.5, color=navy),
        Spacer(1, 2 * mm),
        Paragraph(
            f'<font color="{TIER_COLOURS["High"]}"><b>{counts["High"]} High</b></font> &nbsp;·&nbsp; '
            f'<font color="{TIER_COLOURS["Medium"]}"><b>{counts["Medium"]} Medium</b></font> &nbsp;·&nbsp; '
            f'<font color="{TIER_COLOURS["Low"]}"><b>{counts["Low"]} Low</b></font> importance stories '
            "from the last 24 hours (same story in several papers is shown once).", s["body"]),
    ]

    # Page 1: the morning-meeting view, the top stories per theme.
    story.append(Paragraph("Before the market opens: what matters today", s["h1"]))
    top = tiers["High"][:15] or tiers["Medium"][:10]
    for theme, items in by_theme(top):
        story.append(Paragraph(esc(theme), s["theme"]))
        for it in items:
            story.append(Paragraph(link(it, esc(it["title"])) +
                                   f' <font size="7" color="#667085">({source(it).split(" · ")[0]})</font>',
                                   s["brief"], bulletText="•"))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "How stories are ranked: each headline is scored by rules a fund manager uses to triage news. "
        "RBI policy, inflation/GDP, FII flows, SEBI and tax rules, the US Fed, the rupee and Nifty-50 earnings "
        "rank highest; personal finance, gadgets and lifestyle rank lowest. Keyword based, so verify before acting. "
        "This is a news summary, not investment advice.", s["meta"]))
    if errors:
        story.append(Paragraph("Feeds unavailable today: " + esc(", ".join(errors)), s["meta"]))

    for tier in (importance.HIGH, importance.MEDIUM, importance.LOW):
        items = tiers[tier]
        if not items:
            continue
        story.append(PageBreak())
        story.append(Paragraph(f'<font color="{TIER_COLOURS[tier]}">{tier} Importance</font> '
                               f'<font size="11" color="#667085">({len(items)} stories)</font>', s["tier"]))
        story.append(Paragraph(TIER_NOTES[tier], s["note"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor(TIER_COLOURS[tier])))

        for theme, group in by_theme(items):
            story.append(Paragraph(esc(theme), s["theme"]))
            for it in group:
                if tier == importance.LOW:
                    story.append(Paragraph(link(it, esc(it["title"])) +
                                           f' <font size="6.8" color="#667085">{source(it)}</font>',
                                           s["low"], bulletText="–"))
                    continue
                limit = 480 if tier == importance.HIGH else 260
                block = [Paragraph(link(it, esc(it["title"])), s["headline"])]
                summary = it["summary"]
                if summary and normalise(summary) != normalise(it["title"]):
                    block.append(Paragraph(esc(shorten(summary, limit)), s["summary"]))
                block.append(Paragraph(f'{source(it)} &nbsp;|&nbsp; <i>{esc(tags(it))}</i> &nbsp; '
                                       f'{link(it, READ_MORE)}', s["meta"]))
                block.append(Spacer(1, 2.2 * mm))
                story.append(KeepTogether(block))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(grey)
        canvas.drawString(15 * mm, 10 * mm, "Daily Market Briefing · " + now.strftime("%d %b %Y") + " · Not investment advice")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=18 * mm, title="Daily Market Briefing")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return sum(counts.values())


# ---------------------------------------------------------------- email

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
