"""
Scrape 2024-25 club stats for Moroccan players from the Sofascore API.

Steps:
  1. Load all Morocco national-team player names from our DB.
  2. Search Sofascore for each player → get sofascore_id + current club.
  3. Fetch that player's available seasons → find the 2024-25 season in
     their primary club league.
  4. Fetch season statistics and write to player_season_stats.

Sofascore gives: appearances, minutes, goals, assists, xG, xAG,
shots, shots on target, key passes, successful dribbles, tackles,
interceptions, big chances created, and an overall rating.
"""
from __future__ import annotations

import time
from typing import Optional

import certifi
import requests

from src.db import get_connection, init_schema

BASE = "https://api.sofascore.com/api/v1"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
}
DELAY = 1.5   # seconds between requests
TARGET_SEASON = "24/25"   # Sofascore season name substring to match

# Tournament names that indicate cup/knockout competitions — skip in favour of league
CUP_KEYWORDS = (
    "Cup", "Pokal", "Copa", "Coppa", "Coupe", "Taca", "Carabao",
    "Champions League", "Europa League", "Conference League",
    "Super Cup", "Supercup",
    "AFC Champions", "CAF Champions", "CONCACAF Champions",
)


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(path: str) -> Optional[dict]:
    try:
        r = requests.get(f"{BASE}{path}", headers=HEADERS, timeout=15, verify=certifi.where())
        time.sleep(DELAY)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


# ── Player discovery ──────────────────────────────────────────────────────────

def _search_player(name: str) -> Optional[dict]:
    """
    Search Sofascore for a player by name.
    Returns the best football-player result, or None.
    """
    # Use first two words to avoid name-format mismatches (e.g. "Hakimi Mouh")
    query = " ".join(name.split()[:2])
    data = _get(f"/search/all?q={requests.utils.quote(query)}&page=0")
    if not data:
        return None

    for result in data.get("results", []):
        entity = result.get("entity", {})
        entity_type = result.get("type", "")
        if entity_type != "player":
            continue
        # Confirm it's a football player (sport slug)
        sport = (entity.get("sport") or {}).get("slug", "")
        if sport and sport != "football":
            continue
        return entity

    return None


def _get_season_info(player_id: int) -> Optional[tuple[int, int, str]]:
    """
    Return (tournament_id, season_id, league_name) for the player's
    2024/25 domestic league season — cups and international comps excluded.
    Falls back to any non-cup 24/25 competition if no league is found.
    """
    data = _get(f"/player/{player_id}/statistics/seasons")
    if not data:
        return None

    NATIONAL_KEYWORDS = (
        "Cup of Nations", "World Cup", "Nations League",
        "Qualif", "Olympics", "Continental", "Arab Cup",
    )

    league_match = None
    fallback_match = None

    for block in data.get("uniqueTournamentSeasons", []):
        tournament = block.get("uniqueTournament", {})
        t_id = tournament.get("id")
        t_name = tournament.get("name", "")

        if any(kw in t_name for kw in NATIONAL_KEYWORDS):
            continue

        is_cup = any(kw in t_name for kw in CUP_KEYWORDS)

        for season in block.get("seasons", []):
            if TARGET_SEASON not in season.get("name", ""):
                continue
            entry = (t_id, season["id"], t_name)
            if not is_cup:
                if league_match is None:
                    league_match = entry
            else:
                if fallback_match is None:
                    fallback_match = entry

    return league_match or fallback_match


# ── Stats fetch ────────────────────────────────────────────────────────────────

def _fetch_stats(player_id: int, tournament_id: int, season_id: int) -> Optional[dict]:
    data = _get(
        f"/player/{player_id}/unique-tournament/{tournament_id}"
        f"/season/{season_id}/statistics/overall"
    )
    return data.get("statistics") if data else None


# ── DB write ──────────────────────────────────────────────────────────────────

def _write(row: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO player_season_stats "
            "(player_name, season, club, league, position, apps, starts, minutes, "
            " goals, assists, xg, xag, shots, shots_on_target, key_passes, "
            " successful_dribbles, tackles, interceptions, big_chances_created, rating) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row["player_name"], row["season"], row["club"], row["league"],
                row["position"], row["apps"], row["starts"], row["minutes"],
                row["goals"], row["assists"], row["xg"], row["xag"],
                row["shots"], row["shots_on_target"], row["key_passes"],
                row["successful_dribbles"], row["tackles"], row["interceptions"],
                row["big_chances_created"], row["rating"],
            ),
        )


# ── Main ingest ────────────────────────────────────────────────────────────────

# Players not in StatsBomb national-team data but part of the Morocco squad
EXTRA_PLAYERS = [
    "Brahim Díaz",
    "Ayyoub Bouaddi",
]


def _get_moroccan_player_names() -> list[str]:
    """Return distinct player names who appeared for Morocco in our DB, plus extras."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT p.player_name "
            "FROM player_match_stats s "
            "JOIN players p  ON s.player_id = p.player_id "
            "JOIN teams   t  ON s.team_id   = t.team_id "
            "WHERE t.team_name = 'Morocco' "
            "ORDER BY p.player_name"
        ).fetchall()
    names = [r[0] for r in rows]
    for name in EXTRA_PLAYERS:
        if name not in names:
            names.append(name)
    return names


def ingest_all(season_label: str = "2024-25") -> None:
    init_schema()
    print(f"\n=== Sofascore Club Stats — Moroccan Players ({season_label}) ===\n")

    players = _get_moroccan_player_names()
    print(f"  Found {len(players)} Moroccan players in DB\n")

    success = 0
    for name in players:
        print(f"  {name}...")

        entity = _search_player(name)
        if not entity:
            print(f"    ✗ not found on Sofascore")
            continue

        player_id   = entity["id"]
        current_team = entity.get("team", {}).get("name", "?")

        season_info = _get_season_info(player_id)
        if not season_info:
            print(f"    ✗ no 2024/25 club season found  (currently at {current_team})")
            continue

        tournament_id, season_id, league_name = season_info

        stats = _fetch_stats(player_id, tournament_id, season_id)
        if not stats:
            print(f"    ✗ stats fetch failed  ({current_team}, {league_name})")
            continue

        def _i(key): return int(stats.get(key) or 0)
        def _f(key): return round(float(stats.get(key) or 0), 3)

        row = {
            "player_name":          name,
            "season":               season_label,
            "club":                 current_team,
            "league":               league_name,
            "position":             entity.get("position", {}).get("name") if isinstance(entity.get("position"), dict) else entity.get("position"),
            "apps":                 _i("appearances"),
            "starts":               _i("matchesStarted"),
            "minutes":              _i("minutesPlayed"),
            "goals":                _i("goals"),
            "assists":              _i("assists"),
            "xg":                   _f("expectedGoals"),
            "xag":                  _f("expectedAssists"),
            "shots":                _i("totalShots"),
            "shots_on_target":      _i("shotsOnTarget"),
            "key_passes":           _i("keyPasses"),
            "successful_dribbles":  _i("successfulDribbles"),
            "tackles":              _i("tackles"),
            "interceptions":        _i("interceptions"),
            "big_chances_created":  _i("bigChancesCreated"),
            "rating":               _f("rating"),
        }

        _write(row)
        print(
            f"    ✓ {current_team:<25} {league_name:<20} "
            f"{row['apps']:>3}G  {row['goals']}G {row['assists']}A  "
            f"xG={row['xg']}  xAG={row['xag']}  rating={row['rating']}"
        )
        success += 1

    print(f"\n  {success}/{len(players)} players written successfully.")
    print("Done.")
