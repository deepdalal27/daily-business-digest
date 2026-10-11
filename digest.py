"""Build a daily PDF digest of business news from public RSS feeds and email it.

Sources: The Economic Times, Business Standard, Financial Express (their own
public RSS feeds). Each story is listed with headline, the publisher's feed
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
USER_AGENT = "Mozilla/5.0 (compatible; DailyBusinessDigest/1.0; personal RSS reader)"
SUMMARY_CHARS = 420


# ---------------------------------------------------------------- fetching

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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


def parse_feed(data):
    root = ET.fromstring(data)
    items = []
    for item in root.iter("item"):
        title = clean_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        summary = clean_text(item.findtext("description"))
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
        for section, url in sections.items():
            try:
                items = parse_feed(fetch(url))
            except Exception as exc:  # one bad feed must not stop the digest
                errors.append(f"{paper} / {section}: {exc}")
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


def build_pdf(paper_sections, errors, out_path):
    font, bold = register_fonts()
    navy = colors.HexColor("#14213d")
    accent = colors.HexColor("#c0392b")
    grey = colors.HexColor("#555555")
    s = {
        "masthead": ParagraphStyle("m", fontName=bold, fontSize=24, leading=28, alignment=TA_CENTER, textColor=navy),
        "dateline": ParagraphStyle("d", fontName=font, fontSize=10, leading=14, alignment=TA_CENTER, textColor=grey),
        "paper": ParagraphStyle("p", fontName=bold, fontSize=18, leading=22, textColor=navy, spaceAfter=4),
        "section": ParagraphStyle("s", fontName=bold, fontSize=12, leading=16, textColor=accent, spaceBefore=10, spaceAfter=4),
        "headline": ParagraphStyle("h", fontName=bold, fontSize=10.5, leading=13.5, textColor=colors.black),
        "summary": ParagraphStyle("b", fontName=font, fontSize=9, leading=12, textColor=colors.HexColor("#222222")),
        "meta": ParagraphStyle("t", fontName=font, fontSize=7.5, leading=10, textColor=grey),
        "toc": ParagraphStyle("c", fontName=font, fontSize=10.5, leading=16),
    }

    now = datetime.now(IST)
    story = [
        Paragraph("Daily Business Digest", s["masthead"]),
        Paragraph(now.strftime("%A, %d %B %Y") + " &nbsp;|&nbsp; The Economic Times · Business Standard · Financial Express", s["dateline"]),
        Spacer(1, 4 * mm),
        HRFlowable(width="100%", thickness=1.5, color=navy),
        Spacer(1, 4 * mm),
    ]

    total = sum(len(v) for sec in paper_sections.values() for v in sec.values())
    story.append(Paragraph(f"<b>{total} stories</b> from the last 24 hours", s["toc"]))
    for paper, sections in paper_sections.items():
        count = sum(len(v) for v in sections.values())
        parts = ", ".join(f"{name} ({len(v)})" for name, v in sections.items()) or "no stories retrieved"
        story.append(Paragraph(f"<b>{esc(paper)}</b> — {count}: {esc(parts)}", s["toc"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "Headlines and summaries come from each publisher's free public RSS feed. "
        "Tap any headline to read the full article on the publisher's website.", s["meta"]))
    if errors:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("Feeds unavailable today: " + esc("; ".join(errors)), s["meta"]))

    for paper, sections in paper_sections.items():
        if not sections:
            continue
        story.append(PageBreak())
        story.append(Paragraph(esc(paper), s["paper"]))
        story.append(HRFlowable(width="100%", thickness=1, color=navy))
        for section, items in sections.items():
            story.append(Paragraph(esc(section), s["section"]))
            for it in items:
                summary = it["summary"]
                if len(summary) > SUMMARY_CHARS:
                    summary = summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
                when = it["published"].astimezone(IST).strftime("%d %b, %I:%M %p IST") if it["published"] else ""
                block = [Paragraph(f'<link href="{esc(it["link"])}">{esc(it["title"])}</link>', s["headline"])]
                if summary and normalise(summary) != normalise(it["title"]):
                    block.append(Paragraph(esc(summary), s["summary"]))
                block.append(Paragraph(f'{when} &nbsp; <link href="{esc(it["link"])}" color="#1f5fa8">Read full article →</link>', s["meta"]))
                block.append(Spacer(1, 2.5 * mm))
                story.append(KeepTogether(block))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(grey)
        canvas.drawString(15 * mm, 10 * mm, "Daily Business Digest · " + now.strftime("%d %b %Y"))
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=18 * mm, title="Daily Business Digest")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return total


# ---------------------------------------------------------------- email

def send_email(pdf_path, paper_sections, total):
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not sender or not password:
        print("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set; skipping email.")
        return False
    recipients = [r.strip() for r in os.environ.get("MAIL_TO", sender).split(",") if r.strip()]
    today = datetime.now(IST).strftime("%d %b %Y")

    lines = [f"Good morning! Today's digest has {total} stories.", ""]
    for paper, sections in paper_sections.items():
        lines.append(f"{paper}:")
        for section, items in sections.items():
            for it in items[:3]:
                lines.append(f"  • [{section}] {it['title']}")
        lines.append("")
    lines.append("The full digest with summaries and links is attached as a PDF.")

    msg = EmailMessage()
    msg["Subject"] = f"Daily Business Digest – {today} ({total} stories)"
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
        print("WARN", e)

    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)
    pdf_path = out_dir / f"Business-Digest-{datetime.now(IST):%Y-%m-%d}.pdf"
    total = build_pdf(paper_sections, errors, pdf_path)
    print(f"Built {pdf_path} with {total} stories")
    if total == 0:
        print("No stories fetched; not sending an empty digest.")
        return 1
    send_email(pdf_path, paper_sections, total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
