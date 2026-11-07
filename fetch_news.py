#!/usr/bin/env python3
"""Actualités économiques pour la page d'accueil d'izily.fr (sans IA, sans clé API).

Lit les flux RSS publics de la presse économique française, garde titre + lien + source + date + vignette
(pas d'article recopié) et écrit data/news.json.
Usage : python fetch_news.py
"""
import os, re, sys, json, time, html, datetime, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

OUT = os.path.join("data", "news.json")
UA = "Mozilla/5.0 (compatible; izily-actu/1.0; +https://izily.fr)"
MAX_TOTAL, PER_SOURCE, MAX_AGE_H = 12, 4, 72

# (nom affiché, [URLs de flux essayées dans l'ordre])
FEEDS = [
    ("Le Figaro Éco", ["https://www.lefigaro.fr/rss/figaro_economie.xml"]),
    ("BFM Business", ["https://www.bfmtv.com/rss/economie/", "https://www.bfmtv.com/rss/economie/bfmbusiness/"]),
    ("Les Échos", ["https://www.lesechos.fr/rss/rss_finance-marches.xml", "https://www.lesechos.fr/rss/rss_economie.xml"]),
    ("Le Monde Éco", ["https://www.lemonde.fr/economie/rss_full.xml"]),
    ("Boursorama", ["https://www.boursorama.com/rss/actualites/economie/"]),
]

NS = {"media": "http://search.yahoo.com/mrss/", "content": "http://purl.org/rss/1.0/modules/content/",
      "atom": "http://www.w3.org/2005/Atom"}

def http_get(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read()
        except Exception as e:
            print(f"  ! {url[:80]} essai {i+1}: {e}", file=sys.stderr)
            time.sleep(2 * (i + 1))
    return None

def clean(t):
    t = html.unescape(re.sub(r"<[^>]+>", " ", t or ""))
    return re.sub(r"\s+", " ", t).strip()

def https(u):
    if not u:
        return None
    u = html.unescape(u.strip())
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("http://"):
        u = "https://" + u[7:]
    return u if u.startswith("https://") else None

def find_image(it):
    for tag in ("media:thumbnail", "media:content"):
        for el in it.findall(tag, NS):
            u = https(el.get("url"))
            if u and (el.get("medium") in (None, "image") or re.search(r"\.(jpe?g|png|webp)", u, re.I)):
                return u
    for el in it.findall("enclosure"):
        if (el.get("type") or "").startswith("image") or re.search(r"\.(jpe?g|png|webp)", el.get("url") or "", re.I):
            u = https(el.get("url"))
            if u:
                return u
    for tag in ("description", "content:encoded"):
        el = it.find(tag, NS) if ":" in tag else it.find(tag)
        if el is not None and el.text:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', html.unescape(el.text), re.I)
            if m and https(m.group(1)):
                return https(m.group(1))
    return None

def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s.strip())
    except Exception:
        try:
            d = datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.astimezone(datetime.timezone.utc)

def parse_feed(raw, source):
    out = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  ! XML invalide ({source}): {e}", file=sys.stderr)
        return out
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = https((it.findtext("link") or "").strip())
        d = parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        if title and link and d:
            out.append({"title": title, "link": link, "source": source, "ts": d.isoformat(timespec="seconds"),
                        "img": find_image(it)})
    return out

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    items, ok = [], []
    for name, urls in FEEDS:
        for u in urls:
            raw = http_get(u)
            if not raw:
                continue
            got = parse_feed(raw, name)
            if got:
                items += got
                ok.append(name)
                print(f"  {name}: {len(got)} titres ({sum(1 for g in got if g['img'])} avec image)")
                break
    seen, final, count = set(), [], {}
    for a in sorted(items, key=lambda x: x["ts"], reverse=True):
        age = (now - datetime.datetime.fromisoformat(a["ts"])).total_seconds() / 3600
        key = re.sub(r"\W+", "", a["title"].lower())[:60]
        if age > MAX_AGE_H or age < -1 or key in seen or count.get(a["source"], 0) >= PER_SOURCE:
            continue
        seen.add(key); count[a["source"]] = count.get(a["source"], 0) + 1
        final.append(a)
        if len(final) >= MAX_TOTAL:
            break
    if len(final) < 3:
        print("Trop peu de titres, fichier existant conservé.", file=sys.stderr)
        sys.exit(1)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"updated": now.isoformat(timespec="seconds"), "items": final}, f, ensure_ascii=False, indent=1)
    print("OK :", len(final), "titres de", ", ".join(ok))

if __name__ == "__main__":
    main()
