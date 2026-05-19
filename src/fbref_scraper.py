"""
Scrape current-season club stats for Moroccan players from FBref.

Covers the Big 5 European leagues plus Süper Lig and Saudi Pro League,
filtering all tables for Nation == 'MAR'.

Populates: player_season_stats
"""
from __future__ import annotations

import time
from io import StringIO
from typing import Optional

import certifi
import pandas as pd
import requests

from src.db import get_connection, init_schema

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
FETCH_DELAY = 5.0
MOROCCO_CODE = "MAR"

# Single session so cookies persist across requests
_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(HEADERS)
        # Seed cookies with a homepage visit
        try:
            _SESSION.get("https://fbref.com/en/", timeout=20, verify=certifi.where())
            time.sleep(FETCH_DELAY)
        except Exception:
            pass
    return _SESSION

# (league_label, fbref_comp_id, url_slug)
LEAGUES = [
    ("Ligue 1",             13,  "Ligue-1-Stats"),
    ("Premier League",       9,  "Premier-League-Stats"),
    ("Bundesliga",          20,  "Bundesliga-Stats"),
    ("La Liga",             12,  "La-Liga-Stats"),
    ("Serie A",             11,  "Serie-A-Stats"),
    ("Süper Lig",           26,  "Super-Lig-Stats"),
    ("Saudi Pro League",    70,  "Saudi-Professional-League-Stats"),
    ("Primeira Liga",       32,  "Primeira-Liga-Stats"),
    ("Eredivisie",          23,  "Eredivisie-Stats"),
]

FBREF_BASE = "https://fbref.com/en/comps"


def _fetch_standard_stats(comp_id: int, slug: str) -> Optional[pd.DataFrame]:
    url = f"{FBREF_BASE}/{comp_id}/stats/{slug}"
    session = _get_session()
    try:
        resp = session.get(url, timeout=25, verify=certifi.where())
        resp.raise_for_status()
        time.sleep(FETCH_DELAY)
    except requests.RequestException as exc:
        print(f"      HTTP error: {exc}")
        return None

    try:
        tables = pd.read_html(StringIO(resp.text), attrs={"id": "stats_standard"})
        if not tables:
            return None
        df = tables[0].copy()
    except Exception:
        # Fall back to scanning all tables for a Player column
        try:
            all_tables = pd.read_html(StringIO(resp.text))
            df = next(
                (t for t in all_tables if "Player" in str(t.columns.tolist())),
                None,
            )
            if df is None:
                return None
        except Exception as exc2:
            print(f"      parse error: {exc2}")
            return None

    # Flatten MultiIndex columns (FBref uses two-level headers)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [
            b if a.startswith("Unnamed") else f"{a}_{b}"
            for a, b in df.columns
        ]

    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise column names and drop totals rows."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Drop repeated header rows and squad totals
    if "Player" in df.columns:
        df = df[df["Player"].notna() & (df["Player"] != "Player")]

    return df.reset_index(drop=True)


def _col(df: pd.DataFrame, *candidates) -> Optional[str]:
    """Return the first candidate column name that exists in df."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _to_int(val) -> Optional[int]:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _to_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _extract_moroccan_rows(df: pd.DataFrame, league: str, season: str) -> list[dict]:
    df = _clean(df)

    nation_col = _col(df, "Nation", "Unnamed: 2_level_1", "Performance_Nation")
    player_col = _col(df, "Player", "Unnamed: 1_level_1")
    squad_col  = _col(df, "Squad",  "Unnamed: 3_level_1")
    pos_col    = _col(df, "Pos",    "Unnamed: 4_level_1")
    age_col    = _col(df, "Age",    "Unnamed: 5_level_1")

    if not nation_col or not player_col:
        return []

    # Filter to Moroccan players
    mask = df[nation_col].astype(str).str.contains(MOROCCO_CODE, na=False)
    moroccan = df[mask].copy()
    if moroccan.empty:
        return []

    rows = []
    for _, r in moroccan.iterrows():
        player = str(r[player_col]).strip()
        club   = str(r[squad_col]).strip() if squad_col else ""
        pos    = str(r[pos_col]).strip()   if pos_col  else None
        age    = _to_int(r[age_col])       if age_col  else None

        # Playing time
        mp   = _to_int(r.get(_col(df, "Playing Time_MP",   "MP",   "Unnamed: 6_level_1")))
        st   = _to_int(r.get(_col(df, "Playing Time_Starts","Starts","Unnamed: 7_level_1")))
        mins = _to_int(r.get(_col(df, "Playing Time_Min",  "Min",  "Unnamed: 8_level_1")))

        # Performance
        g   = _to_int(r.get(_col(df, "Performance_Gls", "Gls")))
        a   = _to_int(r.get(_col(df, "Performance_Ast", "Ast")))
        sh  = _to_int(r.get(_col(df, "Performance_Sh",  "Sh")))
        sot = _to_int(r.get(_col(df, "Performance_SoT", "SoT")))

        # Expected
        xg  = _to_float(r.get(_col(df, "Expected_xG",  "xG")))
        xag = _to_float(r.get(_col(df, "Expected_xAG", "xAG")))

        # Progression
        pc  = _to_int(r.get(_col(df, "Progression_PrgC", "PrgC")))
        pp  = _to_int(r.get(_col(df, "Progression_PrgP", "PrgP")))
        pr  = _to_int(r.get(_col(df, "Progression_PrgR", "PrgR")))

        rows.append({
            "player_name":          player,
            "season":               season,
            "club":                 club,
            "league":               league,
            "nationality":          MOROCCO_CODE,
            "position":             pos,
            "age":                  age,
            "apps":                 mp,
            "starts":               st,
            "minutes":              mins,
            "goals":                g,
            "assists":              a,
            "xg":                   round(xg, 3) if xg is not None else None,
            "xag":                  round(xag, 3) if xag is not None else None,
            "shots":                sh,
            "shots_on_target":      sot,
            "progressive_carries":  pc,
            "progressive_passes":   pp,
            "progressive_receptions": pr,
        })
    return rows


def _write_rows(rows: list[dict]) -> int:
    inserted = 0
    with get_connection() as conn:
        for r in rows:
            cur = conn.execute(
                "INSERT OR REPLACE INTO player_season_stats "
                "(player_name, season, club, league, nationality, position, age, "
                " apps, starts, minutes, goals, assists, xg, xag, shots, shots_on_target, "
                " progressive_carries, progressive_passes, progressive_receptions) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["player_name"], r["season"], r["club"], r["league"],
                    r["nationality"], r["position"], r["age"],
                    r["apps"], r["starts"], r["minutes"],
                    r["goals"], r["assists"], r["xg"], r["xag"],
                    r["shots"], r["shots_on_target"],
                    r["progressive_carries"], r["progressive_passes"],
                    r["progressive_receptions"],
                ),
            )
            inserted += cur.rowcount
    return inserted


def ingest_all(season: str = "2024-25") -> None:
    init_schema()
    print(f"\n=== FBref Club Stats — Moroccan Players ({season}) ===\n")
    total = 0

    for league, comp_id, slug in LEAGUES:
        print(f"  {league}...")
        df = _fetch_standard_stats(comp_id, slug)
        if df is None:
            print("    → skipped (fetch failed)")
            continue
        rows = _extract_moroccan_rows(df, league, season)
        if not rows:
            print("    → no Moroccan players found")
            continue
        n = _write_rows(rows)
        for r in rows:
            print(f"    {r['player_name']:<28} {r['club']:<25} {r['apps'] or 0:>3} apps  "
                  f"{r['goals'] or 0}G {r['assists'] or 0}A  xG={r['xg']}")
        print(f"    → {n} rows written")
        total += n

    print(f"\nTotal Moroccan players found: {total}")
    print("Done.")
