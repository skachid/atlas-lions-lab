"""Scrape match results from Wikipedia into SQLite.

Covers:
  - AFCON 2025                (competition_id=1267, season_id=2025)
  - CAF WC 2026 Quals         (competition_id=2001, season_id=2024)
  - FIFA U20 WC 2025          (competition_id=1470, season_id=2025)
  - CONMEBOL WC 2026 Quals    (competition_id=2002, season_id=2024)
  - UEFA WC 2026 Quals        (competition_id=2003, season_id=2024)
  - CONCACAF WC 2026 Quals    (competition_id=2004, season_id=2024)

Three table patterns exist on Wikipedia:
  Pattern A — score-in-header:  cols = [home, 'X–Y', away]        (AFCON, U20 WC, CONMEBOL)
  Pattern B — H2H grid:         standings table with result grid   (CAF, UEFA, CONCACAF quals)
  Pattern C — score-col:        cols include 'Score','Home team'   (UEFA/CAF playoffs)
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from io import StringIO
from typing import Optional

import certifi
import pandas as pd
import requests

from src.db import get_connection, init_schema

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; football-data-bot/1.0; research)"}
FETCH_DELAY = 2.0

AFCON_COMP_ID      = 1267
CAF_QUAL_COMP_ID   = 2001
U20_WC_COMP_ID     = 1470
CONMEBOL_COMP_ID   = 2002
UEFA_COMP_ID       = 2003
CONCACAF_COMP_ID   = 2004

SCORE_RE   = re.compile(r"^(\d+)\s*[\-–]\s*(\d+)(?:\s*\(.*\))?$")
BRACKET_RE = re.compile(r"\[.*?\]|\(.*?\)")   # strip footnotes like [a], [c]

AFCON_GROUP_URLS = {
    f"Group {g}": f"https://en.wikipedia.org/wiki/2025_Africa_Cup_of_Nations_Group_{g}"
    for g in "ABCDEF"
}

URLS = {
    **AFCON_GROUP_URLS,
    "AFCON 2025 Knockout":      "https://en.wikipedia.org/wiki/2025_Africa_Cup_of_Nations_knockout_stage",
    "CAF WC 2026 Qualifiers":   "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_qualification_(CAF)",
    "FIFA U20 WC 2025":         "https://en.wikipedia.org/wiki/2025_FIFA_U-20_World_Cup",
    "CONMEBOL WC 2026 Quals":   "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_qualification_(CONMEBOL)",
    "UEFA WC 2026 Quals":       "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_qualification_(UEFA)",
    "CONCACAF WC 2026 Quals":   "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_qualification_(CONCACAF)",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fetch_tables(url: str) -> list[pd.DataFrame]:
    resp = requests.get(url, headers=HEADERS, timeout=20, verify=certifi.where())
    resp.raise_for_status()
    time.sleep(FETCH_DELAY)
    return pd.read_html(StringIO(resp.text))


def _parse_score(cell) -> Optional[tuple[int, int]]:
    if not isinstance(cell, str):
        return None
    m = SCORE_RE.match(str(cell).strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _clean_team_name(name: str) -> str:
    name = str(name).strip()
    name = BRACKET_RE.sub("", name).strip()   # remove [a], (c) footnotes
    name = name.removesuffix("vte").strip()   # Wikipedia adds "vte" to some team names
    return name


def _make_match_id(comp_id: int, season: int, home: str, away: str) -> int:
    key = f"{comp_id}:{season}:{home}:{away}"
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % (9 * 10 ** 8) + 10 ** 8


def _team_id(name: str) -> int:
    return int(hashlib.md5(name.strip().encode()).hexdigest(), 16) % (9 * 10 ** 5) + 10 ** 5


def _ensure_team(conn, name: str) -> int:
    tid = _team_id(name)
    conn.execute("INSERT OR IGNORE INTO teams (team_id, team_name) VALUES (?, ?)", (tid, name))
    return tid


def _ensure_competition(conn, comp_id: int, season: int, name: str, country: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO competitions "
        "(competition_id, season_id, competition_name, country_name, season_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (comp_id, season, name, country, str(season)),
    )


def _insert_match(conn, *, match_id, comp_id, season, home_id, away_id,
                  home_score, away_score, stage=None) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO matches "
        "(match_id, competition_id, season_id, home_team_id, away_team_id, "
        " home_score, away_score, competition_stage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (match_id, comp_id, season, home_id, away_id, home_score, away_score, stage),
    )
    return cur.rowcount > 0


def _write_matches(raw_matches: list[dict], comp_id: int, season: int,
                   comp_name: str, country: str) -> int:
    inserted = 0
    seen = set()
    with get_connection() as conn:
        _ensure_competition(conn, comp_id, season, comp_name, country)
        for m in raw_matches:
            key = (m["home"], m["away"])
            if key in seen:
                continue
            seen.add(key)
            home_id = _ensure_team(conn, m["home"])
            away_id = _ensure_team(conn, m["away"])
            mid = _make_match_id(comp_id, season, m["home"], m["away"])
            if _insert_match(conn,
                             match_id=mid, comp_id=comp_id, season=season,
                             home_id=home_id, away_id=away_id,
                             home_score=m["home_score"], away_score=m["away_score"],
                             stage=m.get("stage")):
                inserted += 1
    return inserted


# ── Pattern A: score-in-header tables ─────────────────────────────────────────
# cols = [home_team_name, 'X–Y', away_team_name]  (shape usually 1×3)

def _extract_score_header(tables: list[pd.DataFrame], stage: str) -> list[dict]:
    matches = []
    for t in tables:
        cols = [str(c) for c in t.columns]
        if len(cols) != 3:
            continue
        score = _parse_score(cols[1])
        if score is None:
            continue
        home = _clean_team_name(cols[0])
        away = _clean_team_name(cols[2])
        if not home or not away:
            continue
        matches.append({
            "home": home, "away": away,
            "home_score": score[0], "away_score": score[1],
            "stage": stage,
        })
    return matches


# ── Pattern B: H2H grid in standings table ─────────────────────────────────────
# 18-col tables: [Pos, Teamvte, Pld, W, D, L, GF, GA, GD, Pts, Qual, blank, T0, T1, T2, T3, T4, T5]
# Row i / col (12+j) = home team[i] result vs away team[j]

def _extract_h2h_grid(tables: list[pd.DataFrame], stage: str) -> list[dict]:
    matches = []
    for t in tables:
        if "Teamvte" not in t.columns or t.shape[1] < 14:
            continue
        teams_raw = list(t["Teamvte"])
        teams = [_clean_team_name(n) for n in teams_raw]
        n = len(teams)

        # Find the head-to-head columns: last n columns of the DataFrame
        h2h_cols = list(t.columns)[-n:]

        for i, home in enumerate(teams):
            if not home:
                continue
            for j, away in enumerate(teams):
                if i == j or not away:
                    continue
                cell = str(t.iloc[i][h2h_cols[j]]).strip()
                score = _parse_score(cell)
                if score is None:
                    continue
                matches.append({
                    "home": home, "away": away,
                    "home_score": score[0], "away_score": score[1],
                    "stage": stage,
                })
    return matches


# ── Pattern C: score-col tables (playoffs) ────────────────────────────────────
# cols include 'Score' + either ('Team 1','Team 2') or ('Home team','Away team')

def _extract_score_col(tables: list[pd.DataFrame], stage: str) -> list[dict]:
    matches = []
    for t in tables:
        cols = list(t.columns)
        if "Score" not in cols:
            continue
        home_col = next((c for c in ("Home team", "Team 1") if c in cols), None)
        away_col = next((c for c in ("Away team", "Team 2") if c in cols), None)
        if not home_col or not away_col:
            continue
        for _, row in t.iterrows():
            score = _parse_score(str(row["Score"]))
            if score is None:
                continue
            home = _clean_team_name(str(row[home_col]))
            away = _clean_team_name(str(row[away_col]))
            if home and away:
                matches.append({
                    "home": home, "away": away,
                    "home_score": score[0], "away_score": score[1],
                    "stage": stage,
                })
    return matches


# ── Per-competition ingest ─────────────────────────────────────────────────────

def ingest_afcon_2025() -> None:
    print("\n=== AFCON 2025 ===")
    raw: list[dict] = []

    for stage, url in {**AFCON_GROUP_URLS, "Knockout Stage": URLS["AFCON 2025 Knockout"]}.items():
        print(f"  Fetching {stage}...")
        try:
            tables = _fetch_tables(url)
        except Exception as e:
            print(f"    FAILED ({e})")
            continue
        found = _extract_score_header(tables, stage)
        print(f"    → {len(found)} matches")
        raw.extend(found)

    n = _write_matches(raw, AFCON_COMP_ID, 2025, "Africa Cup of Nations", "Africa")
    print(f"  Total inserted: {n}")


def ingest_caf_wc_qualifiers() -> None:
    print("\n=== CAF WC 2026 Qualifiers ===")
    url = URLS["CAF WC 2026 Qualifiers"]
    print(f"  Fetching {url}...")
    try:
        tables = _fetch_tables(url)
    except Exception as e:
        print(f"  FAILED ({e})")
        return

    raw = _extract_h2h_grid(tables, "Qualifying")
    print(f"  Found {len(raw)} matches from H2H grid")
    playoffs = _extract_score_col(tables, "Playoff")
    print(f"  Found {len(playoffs)} playoff matches")
    raw.extend(playoffs)

    n = _write_matches(raw, CAF_QUAL_COMP_ID, 2024, "CAF WC 2026 Qualifiers", "Africa")
    print(f"  Total inserted: {n}")


def ingest_u20_wc_2025() -> None:
    print("\n=== FIFA U20 World Cup 2025 ===")
    url = URLS["FIFA U20 WC 2025"]
    print(f"  Fetching {url}...")
    try:
        tables = _fetch_tables(url)
    except Exception as e:
        print(f"  FAILED ({e})")
        return

    raw = _extract_score_header(tables, "U20 WC 2025")
    print(f"  Found {len(raw)} matches")
    n = _write_matches(raw, U20_WC_COMP_ID, 2025, "FIFA U20 World Cup", "International")
    print(f"  Total inserted: {n}")


def ingest_conmebol_wc_qualifiers() -> None:
    print("\n=== CONMEBOL WC 2026 Qualifiers ===")
    url = URLS["CONMEBOL WC 2026 Quals"]
    print(f"  Fetching {url}...")
    try:
        tables = _fetch_tables(url)
    except Exception as e:
        print(f"  FAILED ({e})")
        return
    raw = _extract_score_header(tables, "Qualifying")
    print(f"  Found {len(raw)} matches")
    n = _write_matches(raw, CONMEBOL_COMP_ID, 2024,
                       "CONMEBOL WC 2026 Qualifiers", "South America")
    print(f"  Total inserted: {n}")


def ingest_uefa_wc_qualifiers() -> None:
    print("\n=== UEFA WC 2026 Qualifiers ===")
    url = URLS["UEFA WC 2026 Quals"]
    print(f"  Fetching {url}...")
    try:
        tables = _fetch_tables(url)
    except Exception as e:
        print(f"  FAILED ({e})")
        return
    raw = _extract_h2h_grid(tables, "Group Stage")
    print(f"  Found {len(raw)} group stage matches from H2H grid")
    playoffs = _extract_score_col(tables, "Playoff")
    print(f"  Found {len(playoffs)} playoff matches")
    raw.extend(playoffs)
    n = _write_matches(raw, UEFA_COMP_ID, 2024,
                       "UEFA WC 2026 Qualifiers", "Europe")
    print(f"  Total inserted: {n}")


def ingest_concacaf_wc_qualifiers() -> None:
    print("\n=== CONCACAF WC 2026 Qualifiers ===")
    url = URLS["CONCACAF WC 2026 Quals"]
    print(f"  Fetching {url}...")
    try:
        tables = _fetch_tables(url)
    except Exception as e:
        print(f"  FAILED ({e})")
        return
    raw = _extract_h2h_grid(tables, "Qualifying")
    print(f"  Found {len(raw)} matches from H2H grid")
    n = _write_matches(raw, CONCACAF_COMP_ID, 2024,
                       "CONCACAF WC 2026 Qualifiers", "North/Central America")
    print(f"  Total inserted: {n}")


def ingest_all() -> None:
    init_schema()
    ingest_afcon_2025()
    ingest_caf_wc_qualifiers()
    ingest_u20_wc_2025()
    ingest_conmebol_wc_qualifiers()
    ingest_uefa_wc_qualifiers()
    ingest_concacaf_wc_qualifiers()
    print("\nDone.")
