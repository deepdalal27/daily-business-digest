"""Rank news stories the way an active Indian equity fund manager would triage them.

Each rule is (category, weight, regex). A story earns each category's weight at
most once: in full when the headline matches, half when only the summary does.
The total score decides the tier:

    HIGH   >= 7   moves the index, rates, flows or a large-cap: read first
    MEDIUM >= 3   stock- or sector-specific, worth knowing today
    LOW    <  3   background reading, personal finance, lifestyle

The rules are plain keyword patterns, so the ranking is free, transparent and
easy to tune: change a weight or add a pattern below.
"""

import re

HIGH, MEDIUM, LOW = "High", "Medium", "Low"
HIGH_AT, MEDIUM_AT = 7, 3

# Theme each category belongs to, in the order themes appear in the PDF.
THEMES = {
    "RBI & Macro": ["RBI Policy", "Macro Data", "Fiscal & Govt Policy"],
    "Markets & Flows": ["Index Move", "FII/DII Flows", "Rupee & Bonds"],
    "Regulation & Tax": ["SEBI/Exchange", "Market Rules & Tax"],
    "Global Cues": ["US Fed", "Global Risk", "Crude & Commodities"],
    "Earnings & Corporate": ["Earnings", "Corporate Action", "Governance Risk", "Large Cap", "Brokerage View"],
    "IPOs & Primary Market": ["IPO"],
    "Sectors & Business": ["Sector Data", "Orders & Capex", "Mutual Funds"],
}
CATEGORY_THEME = {c: t for t, cats in THEMES.items() for c in cats}
OTHER_THEME = "Other News"

RULES = [
    # --- domestic macro and policy
    ("RBI Policy", 7, r"\brepo rate\b|\bmpc\b|monetary policy|rate (cut|hike)|\bcrr\b|policy rate|rbi (policy|governor|keeps|holds|cuts|hikes)|\bsdf\b"),
    ("Macro Data", 7, r"\bgdp\b|\bcpi\b|\bwpi\b|retail inflation|wholesale inflation|inflation (data|rate|eases|rises|cools|at)"),
    ("Macro Data", 4, r"\biip\b|industrial (output|production)|\bpmi\b|current account|trade deficit|forex reserves|core sector|fiscal deficit|gst (collection|mop)"),
    ("Fiscal & Govt Policy", 7, r"union budget|budget 20\d\d|\bfinance minister\b|nirmala sitharaman|gst council|disinvestment"),
    ("Fiscal & Govt Policy", 3, r"\bcabinet (approves|clears)\b|\bpli\b|government (approves|plans|notifies)|\bnotification\b"),
    ("RBI Policy", 4, r"\brbi\b|reserve bank"),
    # --- markets and flows
    ("Index Move", 4, r"\bsensex\b|\bnifty\b|bank nifty|dalal street|stock market today|market (crash|rally|wrap|close)|india vix"),
    ("Sharp Move", 2, r"\b(crash|plunge|tank|bloodbath|sell-?off|record (high|low)|all-time (high|low)|lifetime low|surge|soar|tumble|jump)s?\b"),
    ("FII/DII Flows", 5, r"\bfiis?\b|\bfpis?\b|\bdiis?\b|foreign (portfolio )?investors?|foreign (fund )?(outflow|inflow)s?"),
    ("Rupee & Bonds", 5, r"\brupee\b|\binr\b|bond yields?|\bg-?secs?\b|10-year|treasury yield"),
    # --- regulation and tax
    ("SEBI/Exchange", 5, r"\bsebi\b|\bnse\b|\bbse\b|stock exchange|market regulator"),
    ("Market Rules & Tax", 4, r"\bf&o\b|futures and options|derivatives?|\bltcg\b|\bstcg\b|\bstt\b|capital gains tax|securities transaction tax|margin rules|\bt\+[01]\b|circular|new (rules|framework)"),
    # --- global cues
    ("US Fed", 7, r"\bus fed\b|federal reserve|\bfomc\b|\bfed (rate|cut|hike|chair|policy|meeting)|powell"),
    ("Global Risk", 4, r"\btariffs?\b|trade war|recession|\bwar\b|geopolitic|sanction|china (economy|stimulus|slowdown)|wall street|global (markets|cues|sell-off)|dollar index|\bus (inflation|jobs|economy|yields)\b"),
    ("Crude & Commodities", 4, r"crude|brent|oil prices?|\bopec\b"),
    ("Crude & Commodities", 2, r"\bgold\b|\bsilver\b|copper|steel prices|metal prices|natural gas"),
    # --- companies
    ("Earnings", 4, r"\bq[1-4]\b|quarterly (results|earnings)|net profit|\bpat\b|\bebitda\b|earnings|results? (preview|review|today)|profit (rises|falls|jumps|drops|surges|declines|up|down)|revenue (rises|falls|grows)|guidance"),
    ("Corporate Action", 3, r"merger|acquisition|acquires?\b|\bstake (sale|buy)|block deal|bulk deal|open offer|delist|demerger|\bqip\b|buyback|bonus (issue|shares)|stock split|record date|dividend|fund ?rais"),
    ("Governance Risk", 4, r"fraud|\bed raids?\b|enforcement directorate|\bcbi\b|income tax (raid|search)|default|insolvency|\bnclt\b|\bibc\b|downgrade[sd]?\b|rating (cut|action)|resigns?|steps down|auditor|whistleblower|penalty|show-cause"),
    ("Brokerage View", 2, r"target price|upgrades?|price target|\bbuy rating\b|\bsell rating\b|overweight|underweight|brokerage|morgan stanley|goldman sachs|jefferies|\bclsa\b|nomura|motilal oswal|kotak institutional|stocks? to (buy|watch)"),
    ("IPO", 3, r"\bipos?\b|\bgmp\b|grey market premium|listing (day|gains|price)|\bdrhp\b|anchor investors|subscription status|\bsme ipo\b"),
    ("Sector Data", 2, r"auto sales|monthly sales|vehicle (sales|registrations)|credit growth|deposit growth|npa|asset quality|housing sales|air traffic|power demand|monsoon|kharif|rabi"),
    ("Orders & Capex", 2, r"order (win|worth|book|inflow)|bags? (order|contract)|wins? (order|contract)|capex|new plant|capacity expansion|defence (order|contract)"),
    ("Mutual Funds", 3, r"\bamfi\b|(sip|mf|mutual fund|equity fund) (inflows?|outflows?|flows?)"),
    ("Mutual Funds", 1, r"mutual funds?|\bsips?\b|\bnfo\b|\baum\b"),
]

# Nifty 50 constituents and other bellwethers: news on these moves the index.
LARGE_CAPS = (
    r"reliance|hdfc bank|icici bank|infosys|\btcs\b|tata consultancy|bharti airtel|\bairtel\b|\bsbi\b|state bank|\bitc\b|"
    r"larsen|\bl&t\b|kotak mahindra|axis bank|hindustan unilever|\bhul\b|bajaj finance|bajaj finserv|maruti|"
    r"mahindra & mahindra|\bm&m\b|sun pharma|hcl tech|\bwipro\b|tech mahindra|\bntpc\b|power grid|\bongc\b|coal india|"
    r"tata motors|tata steel|jsw steel|hindalco|ultratech|asian paints|\btitan\b|nestle|adani|grasim|\bcipla\b|"
    r"dr reddy|apollo hospitals|eicher|hero motocorp|bajaj auto|\bbpcl\b|indusind|sbi life|hdfc life|shriram finance|"
    r"\btrent\b|bharat electronics|\bbel\b|tata consumer|jio financial|zomato|eternal|\blic\b|\bhal\b|vedanta|\bdmart\b|avenue supermarts"
)
RULES.append(("Large Cap", 2, LARGE_CAPS))

# Stories an investor can safely skip today.
NOISE = re.compile(
    r"horoscope|recipe|bollywood|celebrity|cricket|\bipl\b|\bmovie\b|box office|web series|\bott\b|smartphone|iphone|"
    r"\bgadget|launch(ed)? in india at|price in india|review:|\btravel\b|tourism|fashion|wedding|lifestyle|"
    r"health tips|weight loss|how to (save|apply|check|file|link)|step-by-step|explained:|\bquiz\b|in pics|photos|"
    r"credit card offers?|fd rates|fixed deposit rates|savings account|home loan (emi|rates)|personal loan|"
    r"wishes|quotes|greetings|holiday list|bank holiday|weather",
    re.I,
)
NOISE_PENALTY = 6

_COMPILED = [(cat, w, re.compile(p, re.I)) for cat, w, p in RULES]


def score(title, summary=""):
    """Return (score, tier, categories sorted by contribution, theme)."""
    best = {}
    for cat, weight, rx in _COMPILED:
        if rx.search(title):
            pts = weight
        elif summary and rx.search(summary):
            pts = weight / 2
        else:
            continue
        best[cat] = max(best.get(cat, 0), pts)

    total = sum(best.values())
    # Results of an index heavyweight matter more than results of a small-cap.
    if "Earnings" in best and "Large Cap" in best:
        total += 2
    if NOISE.search(title):
        total -= NOISE_PENALTY

    tier = HIGH if total >= HIGH_AT else MEDIUM if total >= MEDIUM_AT else LOW
    cats = sorted(best, key=lambda c: -best[c])
    theme_cats = [c for c in cats if c not in ("Large Cap", "Sharp Move")]
    theme = CATEGORY_THEME.get(theme_cats[0], OTHER_THEME) if theme_cats else OTHER_THEME
    return total, tier, cats, theme
