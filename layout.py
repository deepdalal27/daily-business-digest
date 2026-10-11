"""Institutional-style PDF layout for the Daily Market Briefing.

Page 1 is a dashboard (masthead, story counts, headline tone, top three
stories, theme heat-map). High and Medium stories follow as colour-coded
cards; Low stories close the brief as a two-column headline list.
"""

import html
import re
from datetime import date, datetime
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    HRFlowable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

import importance

FIRST_EDITION = date(2026, 10, 11)
PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
WIDTH = PAGE_W - 2 * MARGIN

NAVY = colors.HexColor("#0B1F3A")
NAVY_2 = colors.HexColor("#14305A")
GOLD = colors.HexColor("#C9A227")
INK = colors.HexColor("#1A1D23")
MUTED = colors.HexColor("#667085")
LINE = colors.HexColor("#E4E7EC")
PANEL = colors.HexColor("#F4F6FA")
LINK = "#1F5FA8"
POS = colors.HexColor("#12805C")
NEG = colors.HexColor("#C0392B")
TIER = {
    importance.HIGH: colors.HexColor("#B42318"),
    importance.MEDIUM: colors.HexColor("#B54708"),
    importance.LOW: colors.HexColor("#475467"),
}
TIER_BLURB = {
    importance.HIGH: "Can move the Nifty, rates, the rupee or a large-cap today. Read these first.",
    importance.MEDIUM: "Stock-, sector- and IPO-specific news worth knowing before the open.",
    importance.LOW: "Background reading: headlines only, tap any to open.",
}

POSITIVE = re.compile(r"\b(rises?|rising|gains?|jumps?|surges?|soars?|rall(y|ies)|record high|beats?|upgrades?|"
                      r"strong(er)?|boosts?|recover(s|y)?|rebounds?|higher|eases?|cools?|inflows?|outperform|bullish|"
                      r"growth|expands?|wins?|bags?|approves?)\b", re.I)
NEGATIVE = re.compile(r"\b(falls?|drops?|declines?|slumps?|crash(es)?|plunges?|tumbles?|sinks?|sell-?off|outflows?|"
                      r"pull(s)? out|downgrades?|miss(es)?|weak(er)?|loss(es)?|record low|lower|probe|raids?|fraud|"
                      r"default|war|tariffs?|concerns?|fears?|worries|slowdown|bearish|cuts? (forecast|outlook)|penalty)\b", re.I)


# ---------------------------------------------------------------- fonts & styles

def _register_fonts():
    here = Path(__file__).parent / "fonts"
    try:
        pdfmetrics.registerFont(TTFont("Sans", here / "Carlito-Regular.ttf"))
        pdfmetrics.registerFont(TTFont("Sans-Bold", here / "Carlito-Bold.ttf"))
        pdfmetrics.registerFont(TTFont("Sans-Italic", here / "Carlito-Italic.ttf"))
        pdfmetrics.registerFont(TTFont("Serif-Bold", here / "Caladea-Bold.ttf"))
        pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Italic", boldItalic="Sans-Bold")
        return "Sans", "Sans-Bold", "Sans-Italic", "Serif-Bold"
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Times-Bold"


SANS, BOLD, ITALIC, SERIF = _register_fonts()


def _style(name, **kw):
    base = dict(fontName=SANS, fontSize=9.5, leading=12.5, textColor=INK)
    base.update(kw)
    return ParagraphStyle(name, **base)


ST = {
    "kicker": _style("kicker", fontName=BOLD, fontSize=7.5, leading=9, textColor=MUTED),
    "h2": _style("h2", fontName=SERIF, fontSize=15, leading=18, textColor=NAVY, spaceBefore=2, spaceAfter=2),
    "theme": _style("theme", fontName=SERIF, fontSize=12.5, leading=15, textColor=NAVY, spaceBefore=8),
    "chip": _style("chip", fontName=BOLD, fontSize=7, leading=9, textColor=MUTED),
    "headline": _style("headline", fontName=BOLD, fontSize=11.5, leading=14),
    "headline_m": _style("headline_m", fontName=BOLD, fontSize=10.5, leading=13),
    "top_headline": _style("top_headline", fontName=BOLD, fontSize=13.5, leading=16.5),
    "summary": _style("summary", fontSize=9.6, leading=12.8, textColor=colors.HexColor("#344054")),
    "meta": _style("meta", fontSize=7.8, leading=10, textColor=MUTED),
    "num": _style("num", fontName=SERIF, fontSize=26, leading=28, textColor=GOLD),
    "tile_label": _style("tile_label", fontName=BOLD, fontSize=7.5, leading=9, textColor=MUTED),
    "tile_value": _style("tile_value", fontName=SERIF, fontSize=24, leading=27, textColor=NAVY),
    "tile_sub": _style("tile_sub", fontSize=7.8, leading=9.5, textColor=MUTED),
    "band_title": _style("band_title", fontName=SERIF, fontSize=15, leading=18, textColor=colors.white),
    "band_right": _style("band_right", fontName=BOLD, fontSize=9, leading=11, textColor=colors.white, alignment=2),
    "low": _style("low", fontSize=8.6, leading=10.8, leftIndent=7, bulletIndent=0, spaceAfter=2.2),
    "low_theme": _style("low_theme", fontName=BOLD, fontSize=8.5, leading=11, textColor=NAVY, spaceBefore=6, spaceAfter=2),
    "fine": _style("fine", fontSize=7.3, leading=9.3, textColor=MUTED),
}


def esc(text):
    return html.escape(text, quote=True)


def shorten(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _link(it, text, colour=None):
    inner = f'<font color="{colour}">{text}</font>' if colour else text
    return f'<link href="{esc(it["link"])}">{inner}</link>'


# ---------------------------------------------------------------- page furniture

def _cover_page(now, edition):
    def draw(canvas, doc):
        canvas.saveState()
        band_h = 46 * mm
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - band_h, PAGE_W, band_h, stroke=0, fill=1)
        canvas.setFillColor(NAVY_2)
        canvas.rect(PAGE_W * 0.62, PAGE_H - band_h, PAGE_W * 0.38, band_h, stroke=0, fill=1)
        canvas.setFillColor(GOLD)
        canvas.rect(0, PAGE_H - band_h - 1.6 * mm, PAGE_W, 1.6 * mm, stroke=0, fill=1)

        x, top = MARGIN, PAGE_H - 13 * mm
        canvas.setFillColor(GOLD)
        canvas.setFont(BOLD, 8.5)
        canvas.drawString(x, top, "MORNING BRIEF  ·  INDIAN EQUITIES")
        canvas.setFillColor(colors.white)
        canvas.setFont(SERIF, 30)
        canvas.drawString(x, top - 14 * mm, "Daily Market Briefing")
        canvas.setFont(SANS, 10.5)
        canvas.setFillColor(colors.HexColor("#C8D3E6"))
        canvas.drawString(x, top - 22 * mm, now.strftime("%A, %d %B %Y") + "   |   Pre-market edition, 7:30 AM IST")

        rx = PAGE_W - MARGIN
        canvas.setFillColor(GOLD)
        canvas.setFont(BOLD, 8)
        canvas.drawRightString(rx, top, "EDITION")
        canvas.setFillColor(colors.white)
        canvas.setFont(SERIF, 26)
        canvas.drawRightString(rx, top - 12 * mm, f"No. {edition}")
        canvas.setFont(SANS, 8.5)
        canvas.setFillColor(colors.HexColor("#C8D3E6"))
        canvas.drawRightString(rx, top - 18 * mm, "The Economic Times · Business Standard")
        canvas.drawRightString(rx, top - 22 * mm, "Financial Express")
        canvas.restoreState()
        _footer(canvas, doc, now)
    return draw


def _body_page(now):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 11 * mm, PAGE_W, 11 * mm, stroke=0, fill=1)
        canvas.setFillColor(GOLD)
        canvas.rect(0, PAGE_H - 11.8 * mm, PAGE_W, 0.8 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont(SERIF, 10.5)
        canvas.drawString(MARGIN, PAGE_H - 7.2 * mm, "Daily Market Briefing")
        canvas.setFont(SANS, 8.5)
        canvas.setFillColor(colors.HexColor("#C8D3E6"))
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 7.2 * mm, now.strftime("%d %B %Y") + "  ·  Indian Equities")
        canvas.restoreState()
        _footer(canvas, doc, now)
    return draw


def _footer(canvas, doc, now):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
    canvas.setFont(SANS, 7.2)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 8 * mm, "News summary compiled from public RSS feeds and ranked by keyword rules. Not investment advice.")
    canvas.setFont(BOLD, 8)
    canvas.setFillColor(NAVY)
    canvas.drawRightString(PAGE_W - MARGIN, 8 * mm, f"{doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------- building blocks

def _tiles(tiers, read_minutes):
    def tile(label, value, sub, colour):
        return [Paragraph(label, ST["tile_label"]),
                Paragraph(f'<font color="{colour.hexval().replace("0x", "#")}">{value}</font>', ST["tile_value"]),
                Paragraph(sub, ST["tile_sub"])]

    data = [[
        tile("HIGH IMPORTANCE", len(tiers["High"]), "market-moving stories", TIER["High"]),
        "",
        tile("MEDIUM", len(tiers["Medium"]), "stock & sector news", TIER["Medium"]),
        "",
        tile("LOW", len(tiers["Low"]), "background reading", TIER["Low"]),
        "",
        tile("READ TIME", f"{read_minutes} min", "High + Medium only", NAVY),
    ]]
    gap = 3 * mm
    w = (WIDTH - 3 * gap) / 4
    t = Table(data, colWidths=[w, gap, w, gap, w, gap, w])
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
             ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]
    for col, colour in zip((0, 2, 4, 6), (TIER["High"], TIER["Medium"], TIER["Low"], GOLD)):
        style += [("BACKGROUND", (col, 0), (col, 0), PANEL), ("LINEABOVE", (col, 0), (col, 0), 3, colour)]
    t.setStyle(TableStyle(style))
    return t


def _tone_bar(stories):
    pos = sum(len(POSITIVE.findall(s["title"])) for s in stories)
    neg = sum(len(NEGATIVE.findall(s["title"])) for s in stories)
    total = pos + neg
    share = pos / total if total else 0.5
    if share >= 0.58:
        verdict, colour = "Positive-leaning", POS
    elif share <= 0.42:
        verdict, colour = "Negative-leaning", NEG
    else:
        verdict, colour = "Mixed", GOLD

    d = Drawing(WIDTH, 17 * mm)
    d.add(String(0, 13 * mm, "HEADLINE TONE", fontName=BOLD, fontSize=7.5, fillColor=MUTED))
    d.add(String(27 * mm, 13 * mm, verdict, fontName=BOLD, fontSize=9.5, fillColor=colour))
    d.add(String(WIDTH, 13 * mm, f"{pos} positive vs {neg} negative cues in High + Medium headlines",
                 fontName=SANS, fontSize=7.5, fillColor=MUTED, textAnchor="end"))
    bar_y, bar_h = 5 * mm, 5 * mm
    d.add(Rect(0, bar_y, WIDTH, bar_h, fillColor=NEG, strokeColor=None))
    d.add(Rect(0, bar_y, WIDTH * share, bar_h, fillColor=POS, strokeColor=None))
    d.add(Rect(WIDTH * share - 0.6, bar_y - 1.2 * mm, 1.2, bar_h + 2.4 * mm, fillColor=INK, strokeColor=None))
    d.add(String(1.5 * mm, bar_y + 1.5 * mm, f"{share:.0%} positive", fontName=BOLD, fontSize=7.5, fillColor=colors.white))
    d.add(String(WIDTH - 1.5 * mm, bar_y + 1.5 * mm, f"{1 - share:.0%} negative", fontName=BOLD, fontSize=7.5,
                 fillColor=colors.white, textAnchor="end"))
    d.add(String(0, 0.5 * mm, "Word-count gauge of headline wording, not a market forecast.", fontName=ITALIC, fontSize=6.8,
                 fillColor=MUTED))
    return d


def _heatmap(tiers):
    themes = list(importance.THEMES) + [importance.OTHER_THEME]
    rows = []
    for th in themes:
        h = sum(1 for s in tiers["High"] if s["theme"] == th)
        m = sum(1 for s in tiers["Medium"] if s["theme"] == th)
        if h or m:
            rows.append((th, h, m))
    if not rows:
        return None
    rows.sort(key=lambda r: (-(r[1] * 3 + r[2])))
    peak = max(h + m for _, h, m in rows)
    row_h, label_w = 6.2 * mm, 42 * mm
    bar_w = WIDTH - label_w - 16 * mm
    d = Drawing(WIDTH, row_h * len(rows) + 8 * mm)
    y = row_h * len(rows) + 2 * mm
    for i, tier in enumerate((importance.HIGH, importance.MEDIUM)):
        x = label_w + i * 18 * mm
        d.add(Rect(x, y + 1.3 * mm, 2.6 * mm, 2.6 * mm, fillColor=TIER[tier], strokeColor=None))
        d.add(String(x + 4 * mm, y + 1.5 * mm, tier, fontName=BOLD, fontSize=7.5, fillColor=TIER[tier]))
    for th, h, m in rows:
        y -= row_h
        d.add(String(0, y + 1.6 * mm, th, fontName=SANS, fontSize=8.8, fillColor=INK))
        d.add(Rect(label_w, y + 0.8 * mm, bar_w, row_h - 2 * mm, fillColor=PANEL, strokeColor=None))
        hw = bar_w * h / peak
        mw = bar_w * m / peak
        if hw:
            d.add(Rect(label_w, y + 0.8 * mm, hw, row_h - 2 * mm, fillColor=TIER["High"], strokeColor=None))
        if mw:
            d.add(Rect(label_w + hw, y + 0.8 * mm, mw, row_h - 2 * mm, fillColor=TIER["Medium"], strokeColor=None))
        d.add(String(WIDTH, y + 1.6 * mm, f"{h} / {m}", fontName=BOLD, fontSize=8.5, fillColor=NAVY, textAnchor="end"))
    return d


def _source(it, codes):
    papers = " + ".join([codes.get(it["paper"], it["paper"])] + [codes.get(p, p) for p in it["also"]])
    when = it["published"].astimezone(_IST).strftime("%d %b, %I:%M %p") if it["published"] else ""
    return papers, esc(it["section"]), when


def _chips(it, colour):
    hexc = colour.hexval().replace("0x", "#")
    cats = "  ·  ".join(esc(c.upper()) for c in it["cats"][:3])
    return Paragraph(f'<font color="{hexc}">{esc(it["theme"].upper())}</font>'
                     + (f'  <font color="#98A2B3">|</font>  {cats}' if cats else ""), ST["chip"])


def _card(it, tier, codes, top_rank=None):
    colour = TIER[tier]
    papers, section, when = _source(it, codes)
    if top_rank is not None:
        hstyle, limit = ST["top_headline"], 300
    elif tier == importance.HIGH:
        hstyle, limit = ST["headline"], 420
    else:
        hstyle, limit = ST["headline_m"], 220

    body = [_chips(it, colour), Spacer(1, 2), Paragraph(_link(it, esc(it["title"])), hstyle)]
    summary = it["summary"]
    if summary and _norm(summary) != _norm(it["title"]):
        body += [Spacer(1, 2), Paragraph(esc(shorten(summary, limit)), ST["summary"])]
    body += [Spacer(1, 3), Paragraph(
        f'<b>{esc(papers)}</b>  ·  {section}  ·  {when}'
        f'&nbsp;&nbsp;&nbsp;{_link(it, "Read full article →", LINK)}', ST["meta"])]

    if top_rank is not None:
        t = Table([[Paragraph(f"{top_rank:02d}", ST["num"]), body]], colWidths=[17 * mm, WIDTH - 17 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PANEL), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBEFORE", (0, 0), (0, 0), 3.5, colour),
            ("LEFTPADDING", (0, 0), (0, 0), 9), ("LEFTPADDING", (1, 0), (1, 0), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        return t

    t = Table([[body]], colWidths=[WIDTH])
    t.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, 0), 3, colour),
        ("BACKGROUND", (0, 0), (-1, -1), colors.white if tier == importance.MEDIUM else colors.HexColor("#FCFCFD")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def _band(title, right, colour):
    t = Table([[Paragraph(title, ST["band_title"]), Paragraph(right, ST["band_right"])]],
              colWidths=[WIDTH * 0.7, WIDTH * 0.3])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colour), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 2, GOLD),
    ]))
    return t


def _theme_heading(name, count):
    return [CondPageBreak(30 * mm),
            Paragraph(f'{esc(name)} <font name="{SANS}" size="8.5" color="#98A2B3">  {count}</font>', ST["theme"]),
            HRFlowable(width=22 * mm, thickness=1.6, color=GOLD, hAlign="LEFT", spaceBefore=2, spaceAfter=5)]


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _by_theme(items):
    order = list(importance.THEMES) + [importance.OTHER_THEME]
    groups = {t: [] for t in order}
    for it in items:
        groups[it["theme"]].append(it)
    return [(t, g) for t, g in groups.items() if g]


# ---------------------------------------------------------------- document

_IST = None


def build(tiers, errors, out_path, paper_codes, ist):
    global _IST
    _IST = ist
    now = datetime.now(ist)
    edition = (now.date() - FIRST_EDITION).days + 1
    high, medium, low = tiers[importance.HIGH], tiers[importance.MEDIUM], tiers[importance.LOW]

    words = sum(len((s["title"] + " " + s["summary"]).split()) for s in high + medium)
    read_minutes = max(1, round(words / 220))

    doc = BaseDocTemplate(str(out_path), pagesize=A4, title="Daily Market Briefing",
                          author="Daily Market Briefing", leftMargin=MARGIN, rightMargin=MARGIN)
    cover_frame = Frame(MARGIN, 16 * mm, WIDTH, PAGE_H - 16 * mm - 53 * mm, id="cover", leftPadding=0, rightPadding=0)
    body_frame = Frame(MARGIN, 16 * mm, WIDTH, PAGE_H - 16 * mm - 17 * mm, id="body", leftPadding=0, rightPadding=0)
    col_w = (WIDTH - 7 * mm) / 2
    col_frames = [Frame(MARGIN + i * (col_w + 7 * mm), 16 * mm, col_w, PAGE_H - 16 * mm - 17 * mm,
                        id=f"col{i}", leftPadding=0, rightPadding=0) for i in range(2)]
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=_cover_page(now, edition)),
        PageTemplate(id="body", frames=[body_frame], onPage=_body_page(now)),
        PageTemplate(id="twocol", frames=col_frames, onPage=_body_page(now)),
    ])

    story = [_tiles(tiers, read_minutes), Spacer(1, 5 * mm), _tone_bar(high + medium), Spacer(1, 3 * mm)]

    top = (high or medium)[:3]
    if top:
        story += [Paragraph("THE THREE STORIES THAT MATTER MOST TODAY", ST["kicker"]), Spacer(1, 2.5 * mm)]
        for i, it in enumerate(top, 1):
            story += [_card(it, it["tier"], paper_codes, top_rank=i), Spacer(1, 2.5 * mm)]

    heat = _heatmap(tiers)
    if heat:
        story += [Spacer(1, 2 * mm), CondPageBreak(45 * mm),
                  Paragraph("WHERE TODAY'S NEWS IS CONCENTRATED  (HIGH / MEDIUM STORIES BY THEME)", ST["kicker"]),
                  Spacer(1, 2 * mm), heat]
    if errors:
        story += [Spacer(1, 2 * mm), Paragraph("Feeds unavailable today: " + esc(", ".join(errors)), ST["fine"])]

    story.append(NextPageTemplate("body"))
    for tier, items in ((importance.HIGH, high), (importance.MEDIUM, medium)):
        if not items:
            continue
        story += [PageBreak(), _band(f"{tier} Importance", f"{len(items)} STORIES", TIER[tier]), Spacer(1, 2 * mm),
                  Paragraph(TIER_BLURB[tier], ST["meta"])]
        for theme, group in _by_theme(items):
            story += _theme_heading(theme, len(group))
            for it in group:
                story += [_card(it, tier, paper_codes), Spacer(1, 1.8 * mm)]

    if low:
        story += [NextPageTemplate("twocol"), PageBreak(),
                  _band_small("Low Importance", len(low), col_w), Spacer(1, 1.5 * mm),
                  Paragraph(TIER_BLURB[importance.LOW], ST["meta"])]
        for theme, group in _by_theme(low):
            story.append(Paragraph(f'{esc(theme).upper()}  <font color="#98A2B3">{len(group)}</font>', ST["low_theme"]))
            for it in group:
                papers, _, _ = _source(it, paper_codes)
                story.append(Paragraph(_link(it, esc(it["title"])) +
                                       f' <font size="6.8" color="#98A2B3">{esc(papers)}</font>',
                                       ST["low"], bulletText="›"))

    story.insert(0, NextPageTemplate("cover"))
    doc.build(story)


def _band_small(title, count, width):
    t = Table([[Paragraph(title, ST["band_title"]), Paragraph(f"{count} STORIES", ST["band_right"])]],
              colWidths=[width * 0.62, width * 0.38])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TIER[importance.LOW]), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, -1), 2, GOLD),
    ]))
    return t
