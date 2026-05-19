"""Ingest match data from API-Football (api-football.com) into SQLite.

Requires API_FOOTBALL_KEY env var. Free tier: 100 requests/day.

Usage:
    python scripts/load_api_football.py

API-Football IDs for target competitions:
    AFCON 2025                  → league_id=6,    season=2025 (hosted by Morocco, Jan–Feb 2026)
    CAF WC 2026 Qualifiers      → league_id=29,   season=2024
    FIFA U20 World Cup 2025     → league_id=8,    season=2025
"""
from __future__ import annotations

import os
import time
import requests
from src.db import get_connection, init_schema

BASE_URL = "https://v3.football.api-sports.io"
RATE_LIMIT_DELAY = 6.0  # ~10 req/min to stay safely under free tier (100/day)

# Morocco's API-Football team_id
MOROCCO_TEAM_ID = 1

TARGET_COMPETITIONS = [
    {"league_id": 6,  "season": 2025, "name": "AFCON 2025"},
    {"league_id": 29, "season": 2024, "name": "CAF WC 2026 Qualifiers"},
    {"league_id": 8,  "season": 2025, "name": "FIFA U20 World Cup 2025"},
]


def _headers() -> dict:
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY environment variable not set.")
    return {"x-apisports-key": key}


def _get(endpoint: str, params: dict) -> dict:
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _ensure_team(conn, team_id: int, team_name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO teams (team_id, team_name) VALUES (?, ?)",
        (team_id, team_name),
    )


def _ensure_player(conn, player_id: int, player_name: str) -> None:
    if player_id is None:
        return
    conn.execute(
        "INSERT OR IGNORE INTO players (player_id, player_name) VALUES (?, ?)",
        (player_id, player_name),
    )


def _ensure_competition(conn, league_id: int, season: int, name: str, country: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO competitions "
        "(competition_id, season_id, competition_name, country_name, season_name) "
        "VALUES (?, ?, ?, ?, ?)",
        (league_id, season, name, country, str(season)),
    )


def ingest_fixtures(league_id: int, season: int, comp_name: str) -> list[int]:
    """Fetch all fixtures for a competition+season, insert into matches. Returns list of match_ids."""
    print(f"  Fetching fixtures for {comp_name} ({season})...")
    data = _get("fixtures", {"league": league_id, "season": season})
    time.sleep(RATE_LIMIT_DELAY)

    fixtures = data.get("response", [])
    match_ids = []

    with get_connection() as conn:
        for f in fixtures:
            league = f["league"]
            fixture = f["fixture"]
            teams = f["teams"]
            goals = f["goals"]

            _ensure_competition(conn, league_id, season, comp_name, league.get("country", ""))
            _ensure_team(conn, teams["home"]["id"], teams["home"]["name"])
            _ensure_team(conn, teams["away"]["id"], teams["away"]["name"])

            match_id = fixture["id"]
            conn.execute(
                "INSERT OR IGNORE INTO matches "
                "(match_id, competition_id, season_id, match_date, kick_off, "
                " home_team_id, away_team_id, home_score, away_score, stadium, referee, competition_stage) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    match_id,
                    league_id,
                    season,
                    fixture.get("date", "")[:10] if fixture.get("date") else None,
                    fixture.get("date", "")[11:16] if fixture.get("date") else None,
                    teams["home"]["id"],
                    teams["away"]["id"],
                    goals.get("home"),
                    goals.get("away"),
                    f["fixture"].get("venue", {}).get("name"),
                    fixture.get("referee"),
                    league.get("round"),
                ),
            )
            match_ids.append(match_id)

    print(f"    → {len(fixtures)} fixtures loaded.")
    return match_ids


def ingest_events(match_id: int) -> None:
    """Fetch and store goal/card/sub events for a single match."""
    data = _get("fixtures/events", {"fixture": match_id})
    time.sleep(RATE_LIMIT_DELAY)

    events = data.get("response", [])
    with get_connection() as conn:
        for e in events:
            team = e.get("team", {})
            player = e.get("player", {})
            assist = e.get("assist", {})

            team_id = team.get("id")
            player_id = player.get("id")
            related_id = assist.get("id")
            player_name = player.get("name", "")
            related_name = assist.get("name", "")

            if team_id:
                _ensure_team(conn, team_id, team.get("name", ""))
            if player_id and player_name:
                _ensure_player(conn, player_id, player_name)
            if related_id and related_name:
                _ensure_player(conn, related_id, related_name)

            raw_type = e.get("type", "").lower().replace(" ", "_")
            detail = e.get("detail")
            comments = e.get("comments")
            minute = e.get("time", {}).get("elapsed")
            extra = e.get("time", {}).get("extra")

            conn.execute(
                "INSERT INTO match_events "
                "(match_id, team_id, player_id, related_player_id, minute, extra_minute, "
                " event_type, detail, comments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (match_id, team_id, player_id, related_id, minute, extra, raw_type, detail, comments),
            )


def ingest_stats(match_id: int) -> None:
    """Fetch and store aggregate match statistics for both teams."""
    data = _get("fixtures/statistics", {"fixture": match_id})
    time.sleep(RATE_LIMIT_DELAY)

    with get_connection() as conn:
        for team_block in data.get("response", []):
            team_id = team_block["team"]["id"]
            for stat in team_block.get("statistics", []):
                stat_name = stat.get("type", "")
                stat_value = str(stat.get("value", "")) if stat.get("value") is not None else None
                conn.execute(
                    "INSERT OR IGNORE INTO match_stats (match_id, team_id, stat_name, stat_value) "
                    "VALUES (?, ?, ?, ?)",
                    (match_id, team_id, stat_name, stat_value),
                )


def ingest_lineups(match_id: int) -> None:
    """Fetch and store starting XI + substitutes for both teams."""
    data = _get("fixtures/lineups", {"fixture": match_id})
    time.sleep(RATE_LIMIT_DELAY)

    with get_connection() as conn:
        for team_block in data.get("response", []):
            team_id = team_block["team"]["id"]
            team_name = team_block["team"]["name"]
            _ensure_team(conn, team_id, team_name)

            for p in team_block.get("startXI", []):
                info = p.get("player", {})
                pid = info.get("id")
                name = info.get("name", "")
                if pid:
                    _ensure_player(conn, pid, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO lineups "
                        "(match_id, team_id, player_id, jersey_number, position, is_starter) "
                        "VALUES (?, ?, ?, ?, ?, 1)",
                        (match_id, team_id, pid, info.get("number"), info.get("pos")),
                    )

            for p in team_block.get("substitutes", []):
                info = p.get("player", {})
                pid = info.get("id")
                name = info.get("name", "")
                if pid:
                    _ensure_player(conn, pid, name)
                    conn.execute(
                        "INSERT OR IGNORE INTO lineups "
                        "(match_id, team_id, player_id, jersey_number, position, is_starter) "
                        "VALUES (?, ?, ?, ?, ?, 0)",
                        (match_id, team_id, pid, info.get("number"), info.get("pos")),
                    )


def ingest_competition(league_id: int, season: int, comp_name: str) -> None:
    """Full ingest for one competition: fixtures → events + stats + lineups per match."""
    match_ids = ingest_fixtures(league_id, season, comp_name)

    total = len(match_ids)
    for i, match_id in enumerate(match_ids, 1):
        print(f"  [{i}/{total}] match {match_id} — events, stats, lineups...", end=" ", flush=True)
        try:
            ingest_events(match_id)
            ingest_stats(match_id)
            ingest_lineups(match_id)
            print("done")
        except Exception as e:
            print(f"FAILED ({e})")


def ingest_all() -> None:
    init_schema()
    for comp in TARGET_COMPETITIONS:
        print(f"\n=== {comp['name']} ===")
        ingest_competition(comp["league_id"], comp["season"], comp["name"])
    print("\nDone.")
