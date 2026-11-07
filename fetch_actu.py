#!/usr/bin/env python3
"""Revue de marché quotidienne d'izily.fr, SANS IA ni clé API.

Sources : cotations de clôture (Yahoo Finance, repli Stooq) + titres de la presse financière
(Boursorama, Zonebourse, Les Échos, Investir) via les flux Google Actualités.
Les commentaires sont produits par des règles fixes. Résultat écrit dans data/actu.json.
"""
import os, re, sys, json, time, html, datetime, urllib.request, urllib.parse, urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

OUT = os.path.join("data", "actu.json")
UA = "Mozilla/5.0 (compatible; izily-actu/1.0; +https://izily.fr)"
ARCHIVE_MAX = 30
NB = " "

# clé, libellé, symbole Yahoo, symbole Stooq (repli), groupe, type
SYMBOLS = [
    ("cac", "CAC 40", "^FCHI", "^cac", "eu", "idx"),
    ("dax", "DAX", "^GDAXI", "^dax", "eu", "idx"),
    ("stoxx", "Euro Stoxx 50", "^STOXX50E", None, "eu", "idx"),
    ("spx", "S&P 500", "^GSPC", "^spx", "us", "idx"),
    ("ndx", "Nasdaq Composite", "^IXIC", "^ndq", "us", "idx"),
    ("dji", "Dow Jones", "^DJI", "^dji", "us", "idx"),
    ("ndx100", "Nasdaq 100", "^NDX", None, "idx2", "idx"),
    ("vix", "VIX", "^VIX", None, "idx2", "idx"),
    ("tnx", "Taux américain 10 ans (%)", "^TNX", None, "macro", "yield"),
    ("fr10", "Taux français 10 ans – OAT (%)", None, ["10yfry.b"], "macro", "yield"),
    ("de10", "Taux allemand 10 ans – Bund (%)", None, ["10ydey.b"], "macro", "yield"),
    ("wti", "Pétrole WTI ($)", "CL=F", "cl.f", "macro", "px"),
    ("brent", "Pétrole Brent ($)", "BZ=F", None, "macro", "px"),
    ("gold", "Or ($ l'once)", "GC=F", "gc.f", "macro", "px"),
    ("eur", "EUR/USD", "EURUSD=X", "eurusd", "macro", "fx"),
    ("btc", "Bitcoin ($)", "BTC-USD", None, "macro", "px"),
]

NEWS_SOURCES = [
    ("Boursorama", "boursorama.com"),
    ("Zonebourse", "zonebourse.com"),
    ("Les Échos", "lesechos.fr"),
    ("Investir", "investir.lesechos.fr"),
]

# ---------------------------------------------------------------- réseau
def http_get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read()
        except Exception as e:
            print(f"  ! {url[:80]}... essai {i+1}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None

def yahoo(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=10d&interval=1d"
    raw = http_get(url)
    if not raw:
        return None
    try:
        res = json.loads(raw)["chart"]["result"][0]
        off = res["meta"].get("gmtoffset", 0)
        closes = res["indicators"]["quote"][0]["close"]
        bars = []
        for ts, c in zip(res["timestamp"], closes):
            if c is not None:
                d = (datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) + datetime.timedelta(seconds=off)).date()
                bars.append((d, float(c)))
        return (bars[-2:], res["meta"].get("regularMarketTime"), [round(c, 2) for _, c in bars[-12:]]) if len(bars) >= 2 else None
    except Exception as e:
        print(f"  ! Yahoo {sym}: {e}", file=sys.stderr)
        return None

def stooq(sym):
    raw = http_get(f"https://stooq.com/q/d/l/?s={urllib.parse.quote(sym)}&i=d")
    if not raw:
        return None
    try:
        rows = [l.split(",") for l in raw.decode("utf-8", "ignore").strip().splitlines()[1:] if l.count(",") >= 4]
        bars = [(datetime.date.fromisoformat(r[0]), float(r[4])) for r in rows[-2:]]
        return bars if len(bars) == 2 else None
    except Exception:
        return None

def get_quotes():
    quotes = {}
    for key, label, ysym, ssym, grp, kind in SYMBOLS:
        bars, mt, src, series = None, None, None, None
        if key in ZB_URLS:
            bars, src = bond_bars(key, ssym)
        if ysym:
            r = yahoo(ysym)
            if r:
                bars, mt, series = r
        if not bars and ssym:
            for sym in (ssym if isinstance(ssym, list) else [ssym]):
                bars = stooq(sym)
                if bars:
                    break
        if not bars:
            print(f"  - pas de donnée : {label}", file=sys.stderr)
            continue
        (d0, prev), (d1, last) = bars
        quotes[key] = {"label": label, "grp": grp, "kind": kind, "last": last, "prev": prev,
                       "chg": (last / prev - 1) * 100 if prev else 0.0, "date": d1.isoformat(), "t": mt, "src": src, "s": series}
    return quotes


# ---------------------------------------------------------------- taux souverains (OAT / Bund)
ZB_URLS = {
    "fr10": "https://www.zonebourse.com/cours/taux-interet/FRANCE-10Y-CASH-146043504/",
    "de10": "https://www.zonebourse.com/cours/taux-interet/GERMANY-10Y-CASH-146043340/",
}
IDEAL_URL = "https://www.ideal-investisseur.fr/en/markets/oat-bund-spread.html"
MONTHS_EN = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

def _clean_text(raw):
    s = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s)).replace(" ", " ")
    return re.sub(r"\s+", " ", s)

def _weekday_on_or_before(d):
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d

def parse_zonebourse(raw):
    """Retourne ([(date_veille, cloture_veille), (date, dernier)], 'zonebourse') ou None."""
    s = raw.decode("utf-8", "ignore")
    m = re.search(r'"price"\s*:\s*"?(\d+(?:\.\d+)?)"?', s)
    price = float(m.group(1)) if m else None
    txt = _clean_text(s)
    if price is None:
        m = re.search(r"(\d{1,2},\d{3})\s*\^?\s*%", txt)
        price = float(m.group(1).replace(",", ".")) if m else None
    if price is None or not (0 < price < 25):
        return None
    hm = re.search(r"\d{2}:\d{2}:\d{2}\s+(\d{2})/(\d{2})/(\d{4})", txt)
    rows = [(datetime.date(2000 + int(y), int(mo), int(d)), float(v.replace(",", ".")))
            for d, mo, y, v in re.findall(r"(\d{2})/(\d{2})/(\d{2})(?!\d)\s*\|?\s*(\d{1,2},\d{3,4})\s*%", txt)]
    if hm:
        cur = datetime.date(int(hm.group(3)), int(hm.group(2)), int(hm.group(1)))
    elif rows:
        cur = max(d for d, _ in rows)
    else:
        cur = datetime.date.today()
    cur = _weekday_on_or_before(cur)
    older = [(d, v) for d, v in rows if d < cur and d.weekday() < 5]
    if not older:
        return None
    d0, v0 = max(older)
    return [(d0, v0), (cur, price)]

def parse_ideal(raw):
    """Banque de France / Bundesbank via ideal-investisseur : {'fr10': bars, 'de10': bars} (1 jour de décalage)."""
    txt = _clean_text(raw)
    m = re.search(r"As of (\w{3})\w* (\d{1,2}), (\d{4})", txt)
    o = re.search(r"OAT yields (\d\.\d+)%, versus (\d\.\d+)%", txt)
    if not (m and o and m.group(1) in MONTHS_EN):
        return None
    d1 = datetime.date(int(m.group(3)), MONTHS_EN[m.group(1)], int(m.group(2)))
    fr, de = float(o.group(1)), float(o.group(2))
    prev = (d1, fr, de)
    for mm in re.finditer(r"(\w{3})\w* (\d{1,2}), (\d{4})\s*\|?\s*(\d\.\d{1,3})\s*%?\s*\|?\s*(\d\.\d{1,3})", txt):
        if mm.group(1) in MONTHS_EN:
            d = datetime.date(int(mm.group(3)), MONTHS_EN[mm.group(1)], int(mm.group(2)))
            if d < d1 and (prev[0] == d1 or d > prev[0]):
                prev = (d, float(mm.group(4)), float(mm.group(5)))
    return {"fr10": [(prev[0], prev[1]), (d1, fr)], "de10": [(prev[0], prev[2]), (d1, de)]}

def bond_bars(key, stooq_syms):
    src = []
    raw = http_get(ZB_URLS[key], tries=2)
    if raw:
        b = parse_zonebourse(raw)
        if b:
            return b, "Zonebourse"
        print(f"  - Zonebourse illisible pour {key}", file=sys.stderr)
    raw = http_get(IDEAL_URL, tries=2)
    if raw:
        r = parse_ideal(raw)
        if r and key in r:
            return r[key], "Banque de France / Bundesbank"
    for sym in stooq_syms or []:
        b = stooq(sym)
        if b:
            return b, "Stooq"
    return None, None

def get_news(per_source=5):
    out = []
    seen = set()
    for name, dom in NEWS_SOURCES:
        q = f"site:{dom} (bourse OR marchés OR CAC OR \"Wall Street\" OR Fed OR BCE OR taux) when:1d"
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=fr&gl=FR&ceid=FR:FR"
        raw = http_get(url, tries=2)
        items = []
        if raw:
            try:
                for it in ET.fromstring(raw).iter("item"):
                    title = (it.findtext("title") or "").strip()
                    link = (it.findtext("link") or "").strip()
                    title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)  # retire " - Source"
                    key = re.sub(r"\W+", "", title.lower())[:60]
                    if title and link.startswith("http") and key not in seen:
                        seen.add(key)
                        items.append({"title": title, "link": link})
                    if len(items) >= per_source:
                        break
            except ET.ParseError as e:
                print(f"  ! flux {name}: {e}", file=sys.stderr)
        out.append({"source": name, "items": items})
    return out

# ---------------------------------------------------------------- formats
def num(x, d=0):
    return f"{x:,.{d}f}".replace(",", NB).replace(".", ",")

def pct(x, d=2):
    s = f"{x:+.{d}f}".replace(".", ",").replace("-", "−")
    return s + NB + "%"

def level(q):
    k = q["kind"]
    if k == "yield": return num(q["last"], 2) + NB + "%"
    if k == "fx": return num(q["last"], 4)
    if k == "idx": return num(q["last"], 0 if q["last"] >= 1000 else 2)
    return num(q["last"], 0 if q["last"] >= 10000 else 2)

def change(q):
    if q["kind"] == "yield":
        return f"{(q['last']-q['prev'])*100:+.0f}".replace("-", "−") + NB + "pb"
    return pct(q["chg"])

def cls(q):
    c = (q["last"] - q["prev"]) if q["kind"] == "yield" else q["chg"]
    return "hausse" if c > 0.0001 else "baisse" if c < -0.0001 else "stable"

def verb(c):
    return "progresse" if c >= 0.15 else "recule" if c <= -0.15 else "stagne"

def e(s):
    return html.escape(str(s), quote=True)

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre"]

def label_fr(d):
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month-1]} {d.year}"

# ---------------------------------------------------------------- rédaction par règles
def mv(q):
    return f"{q['label']} {verb(q['chg'])} de {pct(abs(q['chg']))} à {level(q)}"

def build_title(Q):
    p = []
    if "cac" in Q: p.append(f"CAC 40 {pct(Q['cac']['chg'])} à {level(Q['cac'])} pts")
    if "spx" in Q: p.append(f"S&P 500 {pct(Q['spx']['chg'])}")
    if "ndx" in Q: p.append(f"Nasdaq {pct(Q['ndx']['chg'])}")
    return "Bourse : " + ", ".join(p)

def synthese(Q):
    pts = []
    eu = [Q[k] for k in ("cac", "dax", "stoxx") if k in Q]
    if eu:
        pts.append("<strong>Europe :</strong> " + ", ".join(
            (mv(q) if q["label"] == "CAC 40" else f"{q['label']} {pct(q['chg'])}") for q in eu) + ".")
    us = [Q[k] for k in ("spx", "ndx", "dji") if k in Q]
    if us:
        pts.append("<strong>États-Unis :</strong> " + ", ".join(f"{q['label']} {pct(q['chg'])} ({level(q)})" for q in us) + ".")
    mac = []
    if "tnx" in Q: mac.append(f"taux américain à 10 ans {level(Q['tnx'])} ({change(Q['tnx'])})")
    if "fr10" in Q: mac.append(f"OAT 10 ans {level(Q['fr10'])} ({change(Q['fr10'])})")
    if "fr10" in Q and "de10" in Q:
        mac.append(f"écart OAT–Bund {(Q['fr10']['last']-Q['de10']['last'])*100:.0f}{NB}pb")
    if "wti" in Q: mac.append(f"pétrole WTI {level(Q['wti'])} $ ({pct(Q['wti']['chg'])})")
    if "gold" in Q: mac.append(f"or {level(Q['gold'])} $ ({pct(Q['gold']['chg'])})")
    if "eur" in Q: mac.append(f"EUR/USD {level(Q['eur'])}")
    if mac:
        pts.append("<strong>Taux, matières premières, devises :</strong> " + ", ".join(mac) + ".")
    return "<section class=\"synthese\"><h2>Synthèse exécutive</h2><ul>" + "".join(f"<li>{p}</li>" for p in pts) + "</ul></section>"

def table(Q, grp, title):
    rows = [q for q in Q.values() if q["grp"] == grp]
    if not rows: return ""
    body = "".join(
        f"<tr><td>{e(q['label'])}</td><td>{level(q)}</td><td class=\"{cls(q)}\">{change(q)}</td></tr>" for q in rows)
    return (f"<h3>{e(title)}</h3><table class=\"cotations\"><thead><tr><th>Valeur</th><th>Clôture</th>"
            f"<th>Variation</th></tr></thead><tbody>{body}</tbody></table>")

def lecture(Q):
    out = []
    g = lambda k: Q.get(k)
    c, s, n = g("cac"), g("spx"), g("ndx")
    if c and s and c["chg"] * s["chg"] < 0 and min(abs(c["chg"]), abs(s["chg"])) >= 0.3:
        out.append("Europe et États-Unis ont évolué en sens inverse : les marchés ne partagent pas la même lecture du jour, ce qui invite à ne pas tirer de conclusion hâtive d'une seule séance.")
    if n and abs(n["chg"]) >= 2:
        out.append(f"Le Nasdaq a varié de {pct(n['chg'])} en une séance : amplitude élevée, typique d'une phase de volatilité sur les valeurs technologiques.")
    if n and s and n["chg"] - s["chg"] >= 1:
        out.append("La technologie surperforme nettement l'indice large : le mouvement est porté par un nombre restreint de valeurs.")
    if n and s and s["chg"] - n["chg"] >= 1:
        out.append("La technologie sous-performe nettement l'indice large : les investisseurs se désengagent des valeurs de croissance.")
    t = g("tnx")
    if t and t["last"] >= 4.5:
        out.append(f"Le rendement américain à 10 ans, à {level(t)}, reste élevé : il concurrence les actions et pèse sur les valorisations les plus exigeantes.")
    if t and (t["last"] - t["prev"]) * 100 >= 8:
        out.append("Les taux longs américains ont nettement monté sur la séance, ce qui peut peser sur les actions de croissance et les obligations déjà détenues.")
    fr, de = g("fr10"), g("de10")
    if fr and de:
        out.append(f"L'écart entre l'OAT et le Bund à 10 ans ({(fr['last']-de['last'])*100:.0f}{NB}pb) mesure la prime de risque exigée sur la dette française : une hausse durable renchérit le coût de financement de l'État, des banques et des crédits immobiliers.")
    w = g("wti")
    if w and abs(w["chg"]) >= 3:
        out.append(f"Le pétrole WTI a bougé de {pct(w['chg'])} : à surveiller pour son effet sur l'inflation et les taux.")
    go = g("gold")
    if go and abs(go["chg"]) >= 1.5:
        out.append(f"L'or varie de {pct(go['chg'])} : mouvement de valeur refuge ou de repricing des taux réels.")
    eu = g("eur")
    if eu and abs(eu["chg"]) >= 0.7:
        out.append(f"L'euro bouge de {pct(eu['chg'])} face au dollar, ce qui joue sur la performance en euros des placements en actifs américains.")
    if not out:
        out.append("Séance sans mouvement extrême : rien qui justifie de modifier une allocation pensée pour le long terme.")
    return "<h2>Lecture de la séance</h2><ul>" + "".join(f"<li>{x}</li>" for x in out) + "</ul>"

def impact(Q):
    cond = []
    n, c, t = Q.get("ndx"), Q.get("cac"), Q.get("tnx")
    if n and n["chg"] <= -2 or (c and c["chg"] <= -1.5):
        cond.append("<li><strong>Forte baisse du jour :</strong> une séance à −2 % ne change pas un horizon de 15 ou 20 ans. Évitez les ventes dictées par l'émotion et vérifiez surtout que votre exposition correspond à votre tolérance au risque.</li>")
    if n and n["chg"] >= 2:
        cond.append("<li><strong>Forte hausse du jour :</strong> ne courez pas après le marché. Rééquilibrer vers vos pondérations cibles est plus sain que d'augmenter le risque par enthousiasme.</li>")
    if t and t["last"] >= 4.5:
        cond.append("<li><strong>Taux longs élevés :</strong> les obligations offrent à nouveau un rendement attractif, mais restent sensibles à une hausse des taux. Maîtrisez la duration.</li>")
    base = [
        "<li><strong>Gardez le cap :</strong> avec des ETF diversifiés et des versements réguliers (DCA), le bruit quotidien ne devrait pas modifier votre plan.</li>",
        "<li><strong>Contrôlez la concentration :</strong> un ETF World ou S&amp;P 500 est déjà très exposé à la technologie. Vérifiez qu'ajouter un ETF Nasdaq ou thématique ne double pas ce risque.</li>",
        "<li><strong>Protégez votre épargne de précaution :</strong> 6 à 12 mois de dépenses hors marchés évitent de vendre au mauvais moment.</li>",
        "<li><strong>Vigilance macro :</strong> suivez les publications d'inflation, les réunions des banques centrales et l'évolution des taux longs, qui conditionnent les valorisations.</li>",
    ]
    return ("<section class=\"impact\"><h2>Ce que cela change pour votre portefeuille</h2><ul>"
            + "".join(cond + base) + "</ul>"
            "<p class=\"avertissement\">Revue générée automatiquement à partir des cotations de clôture et des titres de la presse "
            "financière. Contenu pédagogique, non constitutif d'un conseil en investissement personnalisé.</p></section>")

def news_block(news):
    parts = []
    for blk in news:
        if not blk["items"]: continue
        lis = "".join(f"<li><a href=\"{e(i['link'])}\" target=\"_blank\" rel=\"noopener noreferrer\">{e(i['title'])}</a></li>" for i in blk["items"])
        parts.append(f"<h3>{e(blk['source'])}</h3><ul class=\"actus\">{lis}</ul>")
    if not parts:
        return ""
    return "<h2>À la une de la presse financière</h2>" + "".join(parts)

def build_article(Q, news):
    srcs = ["Yahoo Finance (cotations)"] + sorted({q["src"] for q in Q.values() if q.get("src")}) + [b["source"] for b in news if b["items"]]
    return ("<article>"
            f"<h1>{e(build_title(Q))}</h1>"
            + synthese(Q)
            + "<h2>Les marchés en chiffres</h2>"
            + table(Q, "eu", "Europe") + table(Q, "us", "États-Unis") + table(Q, "macro", "Taux, matières premières, devises")
            + lecture(Q) + news_block(news) + impact(Q)
            + f"<footer class=\"sources\"><p><strong>Sources :</strong> {e(', '.join(srcs))}.</p></footer></article>")

# ---------------------------------------------------------------- main
def write_entry(Q, news, now):
    d = datetime.date.fromisoformat((Q.get("cac") or Q.get("spx"))["date"])
    art = build_article(Q, news)
    title = build_title(Q)
    entry = {"date": d.isoformat(), "label": label_fr(d), "title": title, "html": art,
             "updated": now.isoformat(timespec="seconds")}
    return entry

def serialize(key, q):
    return {"key": key, "label": q["label"], "grp": q["grp"], "level": level(q),
            "change": change(q), "cls": cls(q), "date": q["date"], "t": q.get("t"), "src": q.get("src"),
            "value": round(q["last"], 4), "s": q.get("s")}

def live():
    now = datetime.datetime.now(datetime.timezone.utc)
    Q = get_quotes()
    if len(Q) < 4:
        sys.exit(f"Trop peu de cotations ({len(Q)}) : fichier non modifié.")
    news = get_news(per_source=4)
    quotes = [serialize(k, q) for k, q in Q.items()]
    if "fr10" in Q and "de10" in Q:
        sp, sp0 = (Q["fr10"]["last"] - Q["de10"]["last"]) * 100, (Q["fr10"]["prev"] - Q["de10"]["prev"]) * 100
        quotes.append({"key": "spread", "label": "Écart OAT–Bund 10 ans", "grp": "macro", "level": f"{sp:.0f}{NB}pb",
                       "change": f"{sp - sp0:+.0f}".replace("-", "−") + NB + "pb",
                       "cls": "baisse" if sp - sp0 > 0.5 else "hausse" if sp - sp0 < -0.5 else "stable",
                       "date": Q["fr10"]["date"], "t": None})
    out = {"updated": now.isoformat(timespec="seconds"), "quotes": quotes, "news": news}
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "live.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("OK live :", len(Q), "cotations,", sum(len(b["items"]) for b in news), "titres")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "live":
        return live()
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.date().weekday() >= 5 and not os.environ.get("FORCE"):
        print("Week-end : rien à publier."); return
    Q = get_quotes()
    if len(Q) < 4 or not ("cac" in Q or "spx" in Q):
        sys.exit(f"Trop peu de cotations ({len(Q)}) : fichier non modifié.")
    ref = datetime.date.fromisoformat((Q.get("cac") or Q["spx"])["date"])
    if (now.date() - ref).days > 4:
        sys.exit("Cotations trop anciennes : fichier non modifié.")
    news = get_news()
    entry = write_entry(Q, news, now)
    archive = []
    if os.path.exists(OUT):
        try: archive = json.load(open(OUT, encoding="utf-8")).get("archive", [])
        except Exception: archive = []
    archive = ([entry] + [a for a in archive if a.get("date") != entry["date"]])[:ARCHIVE_MAX]
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": entry["updated"], "latest": entry, "archive": archive}, f, ensure_ascii=False)
    print("OK :", entry["title"], "|", sum(len(b["items"]) for b in news), "titres de presse")

if __name__ == "__main__":
    main()
