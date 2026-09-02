#!/usr/bin/env python3
"""
fetch_posters.py — haalt filmposter-paden op bij TMDB voor elke film die in
filmgenres.html staat, en schrijft het resultaat naar data/posters.json.

Waarom dit script bestaat
--------------------------
filmgenres.html toont bij mouse-over (of tik, op mobiel) een filmposter.
De TMDB API-key mag NIET in de pagina zelf staan — die staat straks publiek
op GitHub Pages, en dan is alles in de HTML/JS voor iedereen leesbaar.

Omdat de filmlijst in filmgenres.html vast staat (geen vrije zoekopdracht
van bezoekers, altijd dezelfde ~150 films/series), hoeft de opzoek-actie
niet live in de browser te gebeuren. Dit script draait één keer (of
periodiek, via GitHub Actions) MET de geheime key, zoekt elke film op bij
TMDB, en schrijft alleen het resultaat (het "poster_path" — geen key, geen
gevoelige data) naar data/posters.json. filmgenres.html leest dat bestand
uit zonder ooit zelf bij TMDB te hoeven aankloppen met een key.

Zelfde patroon als scripts/scrape.py bij de Amsterdamse bioscoopagenda:
periodiek een script draaien dat een statisch JSON-bestand ververst.

Gebruik
-------
  TMDB_API_KEY=... python3 scripts/fetch_posters.py

Zonder key: het script slaat het ophalen over en laat een bestaande
data/posters.json (indien aanwezig) ongemoeid, in plaats van te crashen —
zelfde afspraak als bij de bioscoopagenda.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Dit script heeft beautifulsoup4 nodig: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "filmgenres.html"
OUTPUT_PATH = ROOT / "data" / "posters.json"

TMDB_BASE = "https://api.themoviedb.org/3"


# ---------------------------------------------------------------------------
# Stap 1: film-titel + jaartal uit filmgenres.html halen
#
# Precies dezelfde twee markup-patronen als de getInfo()-functie in de
# pagina zelf gebruikt:
#   - zombie-paneel:            .film-title  +  .film-year
#   - horror/scifi/western:     .film-name   +  .film-year-col
# ---------------------------------------------------------------------------

def extract_films(html_path):
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    films = []
    seen_keys = set()

    for row in soup.select(".film-row, .film-card"):
        name_el = row.select_one(".film-name") or row.select_one(".film-title")
        year_el = row.select_one(".film-year-col") or row.select_one(".film-year")
        if not name_el or not year_el:
            continue
        title = name_el.get_text(strip=True)
        year = year_el.get_text(strip=True)
        if not title:
            continue
        # Zelfde cache-sleutel als de browser gebruikt: ruwe titel + '_' + ruwe jaar-tekst.
        key = f"{title}_{year}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        films.append({"title": title, "year": year, "key": key})

    return films


# ---------------------------------------------------------------------------
# Stap 2: per film opzoeken bij TMDB — zelfde volgorde/logica als voorheen
# in de browser-JS: eerst film + jaartal, dan film zonder jaartal, dan tv.
# ---------------------------------------------------------------------------

def tmdb_search(path, params, api_key, timeout=10):
    params = dict(params)
    params["api_key"] = api_key
    params.setdefault("language", "en-US")
    url = f"{TMDB_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "filmgenres-poster-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_poster_path(title, year_text, api_key):
    # Haalt haakjes-toevoegingen weg voor de zoekterm, bv.
    # "The Ring (Ringu / remake)" -> "The Ring" — zelfde regex als de
    # browser-JS gebruikte.
    query = re.sub(r"\s*\(.*?\)\s*", " ", title).strip()

    year_match = re.match(r"\s*(\d{4})", year_text)
    year = int(year_match.group(1)) if year_match else None

    attempts = [
        ("/search/movie", {"query": query, **({"year": year} if year else {})}),
        ("/search/movie", {"query": query}),
        ("/search/tv", {"query": query}),
    ]
    for path, params in attempts:
        try:
            data = tmdb_search(path, params, api_key)
        except Exception as exc:  # netwerkfout, timeout, TMDB-storing, etc.
            print(f"  ! fout bij opzoeken '{title}' ({path}): {exc}", file=sys.stderr)
            continue
        results = data.get("results") or []
        if results and results[0].get("poster_path"):
            return results[0]["poster_path"]
    return None


def main():
    api_key = os.environ.get("TMDB_API_KEY")

    films = extract_films(HTML_PATH)
    print(f"{len(films)} unieke films/series gevonden in {HTML_PATH.name}.")

    if not api_key:
        print(
            "Geen TMDB_API_KEY gevonden in de omgeving — poster-ophalen wordt "
            "overgeslagen. Een eventueel bestaande data/posters.json blijft "
            "ongewijzigd staan (zie DEPLOY-INSTRUCTIES.md om de secret in te stellen)."
        )
        if not OUTPUT_PATH.exists():
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT_PATH.write_text("{}\n", encoding="utf-8")
            print(f"Leeg {OUTPUT_PATH} aangemaakt zodat de pagina niet breekt.")
        return

    # Hergebruik resultaten uit een eerdere run zodat films die al eerder
    # gevonden zijn niet opnieuw worden opgezocht (scheelt TMDB-aanroepen).
    existing = {}
    if OUTPUT_PATH.exists():
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    result = dict(existing)
    found, skipped_cached, not_found = 0, 0, 0

    for i, film in enumerate(films, 1):
        key = film["key"]
        if key in existing:
            skipped_cached += 1
            continue
        poster_path = find_poster_path(film["title"], film["year"], api_key)
        result[key] = poster_path
        if poster_path:
            found += 1
            print(f"[{i}/{len(films)}] OK   {film['title']} ({film['year']}) -> {poster_path}")
        else:
            not_found += 1
            print(f"[{i}/{len(films)}] GEEN POSTER   {film['title']} ({film['year']})")
        time.sleep(0.25)  # aardig zijn voor de gratis TMDB-rate limit

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"\nKlaar. {found} nieuw gevonden, {skipped_cached} al gecachet uit een "
        f"eerdere run, {not_found} zonder poster. Totaal {len(result)} entries "
        f"in {OUTPUT_PATH}."
    )


if __name__ == "__main__":
    main()
