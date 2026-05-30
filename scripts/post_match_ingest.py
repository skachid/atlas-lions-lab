"""Ingest a single completed match from API-Football + Sofascore ratings.

Usage:
    python scripts/post_match_ingest.py <match_id>

Ingests:
  - Fixture result and metadata
  - Match events (goals, cards, substitutions) with minutes
  - Team statistics (possession, shots, xG, fouls, corners, etc.)
  - Lineups (starting XI + substitutes for both teams)
  - Player match stats for all players via /fixtures/players
  - Sofascore per-match ratings for Moroccan players

After this runs, post_match_report.py and post_match_charts.py need no
additional API calls.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
import unicodedata
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.db import get_connection, init_schema

# ── Load .env ─────────────────────────────────────────────────────────────────
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Constants ─────────────────────────────────────────────────────────────────
AF_BASE  = "https://v3.football.api-sports.io"
AF_DELAY = 6.0   # seconds — stays safely under 100 req/day free tier

SS_BASE  = "https://api.sofascore.com/api/v1"
SS_DELAY = 1.5
SS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.sofascore.com/",
    "Accept": "application/json",
}

# ── Schema migration ──────────────────────────────────────────────────────────

_NEW_COLS = [
    ("rating",          "REAL    DEFAULT NULL"),
    ("aerials_won",     "INTEGER DEFAULT 0"),
    ("fouls_drawn",     "INTEGER DEFAULT 0"),
    ("fouls_committed", "INTEGER DEFAULT 0"),
    ("yellow_cards",    "INTEGER DEFAULT 0"),
    ("red_cards",       "INTEGER DEFAULT 0"),
]


def _migrate_schema() -> None:
    with get_connection() as conn:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(player_match_stats)")}
        for col, typedef in _NEW_COLS:
            if col not in existing:
                conn.execute(f"ALTER TABLE player_match_stats ADD COLUMN {col} {typedef}")
                print(f"  + Added column player_match_stats.{col}")


# ── API-Football helpers ──────────────────────────────────────────────────────

def _af_headers() -> dict:
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY environment variable not set.")
    return {"x-apisports-key": key}


def _af_get(endpoint: str, params: dict) -> dict:
    resp = requests.get(
        f"{AF_BASE}/{endpoint}", headers=_af_headers(), params=params, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def _upsert_team(conn, team_id: int, team_name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO teams (team_id, team_name) VALUES (?, ?)",
        (team_id, team_name),
    )


def _upsert_player(conn, player_id: int, player_name: str) -> None:
    if not player_id:
        return
    conn.execute(
        "INSERT OR IGNORE INTO players (player_id, player_name) VALUES (?, ?)",
        (player_id, player_name),
    )


# ── 1. Fixture ────────────────────────────────────────────────────────────────

def ingest_fixture(match_id: int) -> None:
    print("  Fetching fixture metadata...")
    data = _af_get("fixtures", {"id": match_id})
    time.sleep(AF_DELAY)

    response = data.get("response", [])
    if not response:
        raise ValueError(f"No fixture found for id={match_id}")

    f       = response[0]
    league  = f["league"]
    fixture = f["fixture"]
    teams   = f["teams"]
    goals   = f["goals"]
    date_s  = fixture.get("date", "") or ""

    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO competitions "
            "(competition_id, season_id, competition_name, country_name, season_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                league["id"], league["season"], league["name"],
                league.get("country", ""), str(league["season"]),
            ),
        )
        _upsert_team(conn, teams["home"]["id"], teams["home"]["name"])
        _upsert_team(conn, teams["away"]["id"], teams["away"]["name"])

        conn.execute(
            "INSERT OR REPLACE INTO matches "
            "(match_id, competition_id, season_id, match_date, kick_off, "
            " home_team_id, away_team_id, home_score, away_score, "
            " stadium, referee, competition_stage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                match_id,
                league["id"], league["season"],
                date_s[:10] if date_s else None,
                date_s[11:16] if date_s else None,
                teams["home"]["id"], teams["away"]["id"],
                goals.get("home"), goals.get("away"),
                (fixture.get("venue") or {}).get("name"),
                fixture.get("referee"),
                league.get("round"),
            ),
        )

    print(
        f"    → {teams['home']['name']} {goals.get('home')} – "
        f"{goals.get('away')} {teams['away']['name']}"
    )


# ── 2. Events ─────────────────────────────────────────────────────────────────

def ingest_events(match_id: int) -> None:
    print("  Fetching events...")
    data = _af_get("fixtures/events", {"fixture": match_id})
    time.sleep(AF_DELAY)

    events = data.get("response", [])
    with get_connection() as conn:
        # Delete first so re-runs are idempotent
        conn.execute("DELETE FROM match_events WHERE match_id = ?", (match_id,))
        for e in events:
            team   = e.get("team",   {}) or {}
            player = e.get("player", {}) or {}
            assist = e.get("assist", {}) or {}

            tid    = team.get("id")
            pid    = player.get("id")
            rid    = assist.get("id")

            if tid:
                _upsert_team(conn, tid, team.get("name", ""))
            if pid and player.get("name"):
                _upsert_player(conn, pid, player["name"])
            if rid and assist.get("name"):
                _upsert_player(conn, rid, assist["name"])

            conn.execute(
                "INSERT INTO match_events "
                "(match_id, team_id, player_id, related_player_id, "
                " minute, extra_minute, event_type, detail, comments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    match_id, tid, pid, rid,
                    (e.get("time") or {}).get("elapsed"),
                    (e.get("time") or {}).get("extra"),
                    e.get("type", "").lower().replace(" ", "_"),
                    e.get("detail"),
                    e.get("comments"),
                ),
            )
    print(f"    → {len(events)} events stored")


# ── 3. Team stats ─────────────────────────────────────────────────────────────

def ingest_stats(match_id: int) -> None:
    print("  Fetching team stats...")
    data = _af_get("fixtures/statistics", {"fixture": match_id})
    time.sleep(AF_DELAY)

    count = 0
    with get_connection() as conn:
        for block in data.get("response", []):
            tid = block["team"]["id"]
            for stat in block.get("statistics", []):
                name  = stat.get("type", "")
                value = str(stat.get("value", "")) if stat.get("value") is not None else None
                conn.execute(
                    "INSERT OR REPLACE INTO match_stats "
                    "(match_id, team_id, stat_name, stat_value) VALUES (?, ?, ?, ?)",
                    (match_id, tid, name, value),
                )
                count += 1
    print(f"    → {count} stats stored")


# ── 4. Lineups ────────────────────────────────────────────────────────────────

def ingest_lineups(match_id: int) -> None:
    print("  Fetching lineups...")
    data = _af_get("fixtures/lineups", {"fixture": match_id})
    time.sleep(AF_DELAY)

    count = 0
    with get_connection() as conn:
        for block in data.get("response", []):
            tid   = block["team"]["id"]
            tname = block["team"]["name"]
            _upsert_team(conn, tid, tname)

            for starter in block.get("startXI", []):
                info = (starter.get("player") or {})
                pid  = info.get("id")
                if pid:
                    _upsert_player(conn, pid, info.get("name", ""))
                    conn.execute(
                        "INSERT OR REPLACE INTO lineups "
                        "(match_id, team_id, player_id, jersey_number, position, is_starter) "
                        "VALUES (?, ?, ?, ?, ?, 1)",
                        (match_id, tid, pid, info.get("number"), info.get("pos")),
                    )
                    count += 1

            for sub in block.get("substitutes", []):
                info = (sub.get("player") or {})
                pid  = info.get("id")
                if pid:
                    _upsert_player(conn, pid, info.get("name", ""))
                    conn.execute(
                        "INSERT OR REPLACE INTO lineups "
                        "(match_id, team_id, player_id, jersey_number, position, is_starter) "
                        "VALUES (?, ?, ?, ?, ?, 0)",
                        (match_id, tid, pid, info.get("number"), info.get("pos")),
                    )
                    count += 1
    print(f"    → {count} lineup entries stored")


# ── 5. Player match stats ─────────────────────────────────────────────────────

def ingest_player_stats(match_id: int) -> None:
    """Fetch per-player stats from /fixtures/players (requires API-Football plan)."""
    print("  Fetching player stats...")
    try:
        data = _af_get("fixtures/players", {"fixture": match_id})
        time.sleep(AF_DELAY)
    except requests.HTTPError as exc:
        print(f"    ✗ /fixtures/players unavailable ({exc}); skipping player stats")
        return

    response = data.get("response", [])
    if not response:
        print("    ✗ No player stats returned (match may not be finished or plan limitation)")
        return

    def _i(v):
        return int(v) if v not in (None, "") else 0

    def _f(v):
        return float(v) if v not in (None, "") else None

    count = 0
    with get_connection() as conn:
        for block in response:
            tid = block["team"]["id"]
            for entry in block.get("players", []):
                player = entry.get("player") or {}
                pid    = player.get("id")
                if not pid:
                    continue

                _upsert_player(conn, pid, player.get("name", ""))

                stats   = ((entry.get("statistics") or [{}]) or [{}])[0] or {}
                games   = stats.get("games")    or {}
                goals_s = stats.get("goals")    or {}
                shots_s = stats.get("shots")    or {}
                passes_s= stats.get("passes")   or {}
                drbs    = stats.get("dribbles")  or {}
                duels   = stats.get("duels")     or {}
                fouls   = stats.get("fouls")     or {}
                cards   = stats.get("cards")     or {}

                minutes      = _i(games.get("minutes"))
                rating_raw   = games.get("rating")
                rating       = _f(rating_raw)

                goals        = _i(goals_s.get("total"))
                assists      = _i(goals_s.get("assists"))
                shots_tot    = _i(shots_s.get("total"))
                shots_on     = _i(shots_s.get("on"))
                key_passes   = _i(passes_s.get("key"))
                dribbles     = _i(drbs.get("success"))
                aerials_won  = _i(duels.get("won"))   # total duels won (proxy)
                fouls_drawn  = _i(fouls.get("drawn"))
                fouls_comm   = _i(fouls.get("committed"))
                yellows      = _i(cards.get("yellow"))
                reds         = _i(cards.get("red"))
                position     = games.get("position") or ""

                # Pull is_starter from the lineup we already ingested
                lu = conn.execute(
                    "SELECT is_starter FROM lineups "
                    "WHERE match_id=? AND team_id=? AND player_id=?",
                    (match_id, tid, pid),
                ).fetchone()
                is_starter = lu[0] if lu else 1

                conn.execute(
                    "INSERT OR REPLACE INTO player_match_stats "
                    "(player_id, match_id, team_id, is_starter, minutes_played, "
                    " goals, assists, shots, shots_on_target, key_passes, "
                    " dribbles_completed, turnovers, position, "
                    " rating, aerials_won, fouls_drawn, fouls_committed, "
                    " yellow_cards, red_cards) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pid, match_id, tid, is_starter, minutes,
                        goals, assists, shots_tot, shots_on, key_passes,
                        dribbles, 0, position,
                        rating, aerials_won, fouls_drawn, fouls_comm,
                        yellows, reds,
                    ),
                )
                count += 1

    print(f"    → {count} player stats stored")


# ── 6. Sofascore per-match ratings ────────────────────────────────────────────

def _ss_get(path: str) -> dict | None:
    try:
        r = requests.get(f"{SS_BASE}{path}", headers=SS_HEADERS, timeout=15)
        time.sleep(SS_DELAY)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _find_morocco_ss_id() -> int | None:
    """Locate the Morocco national football team on Sofascore."""
    data = _ss_get(f"/search/all?q={urllib.parse.quote('Morocco')}&page=0")
    if not data:
        return None
    for result in data.get("results", []):
        if result.get("type") != "team":
            continue
        entity = result.get("entity") or {}
        sport  = (entity.get("sport") or {}).get("slug", "")
        if entity.get("name", "").strip().lower() == "morocco" and sport == "football":
            return entity.get("id")
    return None


def _find_ss_event(morocco_ss_id: int, match_date: str) -> int | None:
    """Search Morocco's recent Sofascore events for one on match_date (YYYY-MM-DD)."""
    target = datetime.date.fromisoformat(match_date)
    for page in range(8):  # up to 80 most-recent matches
        data = _ss_get(f"/team/{morocco_ss_id}/events/last/{page}")
        if not data:
            break
        events = data.get("events", [])
        if not events:
            break
        for ev in events:
            ts      = ev.get("startTimestamp", 0)
            ev_date = datetime.date.fromtimestamp(ts)
            if ev_date == target:
                return ev.get("id")
            # Events arrive newest-first; stop scanning once we've gone past
            if ev_date < target - datetime.timedelta(days=14):
                return None
    return None


def ingest_sofascore_ratings(match_id: int) -> None:
    """Scrape Sofascore per-match ratings for Moroccan players; UPDATE rating column."""
    print("  Fetching Sofascore match ratings...")

    with get_connection() as conn:
        match_row = conn.execute(
            """
            SELECT m.match_date,
                   th.team_name, th.team_id,
                   ta.team_name, ta.team_id
            FROM matches m
            JOIN teams th ON th.team_id = m.home_team_id
            JOIN teams ta ON ta.team_id = m.away_team_id
            WHERE m.match_id = ?
            """,
            (match_id,),
        ).fetchone()

    if not match_row:
        print(f"    ✗ Match {match_id} not in DB — run ingest_fixture first")
        return

    match_date, home_name, home_id, away_name, away_id = match_row

    # Identify Morocco's DB team_id
    morocco_db_id = None
    for tid, tname in ((home_id, home_name), (away_id, away_name)):
        if tname.strip().lower() == "morocco":
            morocco_db_id = tid
            break

    if morocco_db_id is None:
        print(f"    ✗ Morocco not in this match ({home_name} vs {away_name})")
        return

    # Load Morocco players already in player_match_stats for this match
    with get_connection() as conn:
        mr_players = conn.execute(
            """
            SELECT pms.player_id, p.player_name
            FROM player_match_stats pms
            JOIN players p ON p.player_id = pms.player_id
            WHERE pms.match_id = ? AND pms.team_id = ?
            """,
            (match_id, morocco_db_id),
        ).fetchall()

    if not mr_players:
        print("    ✗ No Moroccan player_match_stats rows yet — run ingest_player_stats first")
        return

    # Locate Morocco on Sofascore
    morocco_ss_id = _find_morocco_ss_id()
    if not morocco_ss_id:
        print("    ✗ Could not find Morocco national team on Sofascore")
        return
    print(f"    Morocco Sofascore ID: {morocco_ss_id}")

    if not match_date:
        print("    ✗ No match_date in DB")
        return

    event_id = _find_ss_event(morocco_ss_id, match_date)
    if not event_id:
        print(f"    ✗ No Sofascore event found for {match_date}")
        return
    print(f"    Sofascore event ID: {event_id}")

    # Get event lineups (includes per-player ratings)
    data = _ss_get(f"/event/{event_id}/lineups")
    if not data:
        print(f"    ✗ Could not fetch Sofascore lineups for event {event_id}")
        return

    # Build normalized_name → rating map from both sides of the lineup
    ss_ratings: dict[str, float] = {}
    for side in ("home", "away"):
        side_data = data.get(side) or {}
        for entry in side_data.get("players", []):
            pinfo = entry.get("player") or {}
            pname = pinfo.get("name", "") or pinfo.get("shortName", "")
            sstats = entry.get("statistics") or {}
            raw_r  = sstats.get("rating")
            if pname and raw_r is not None:
                ss_ratings[_normalize(pname)] = float(raw_r)

    if not ss_ratings:
        print("    ✗ No ratings found in Sofascore lineup response")
        return

    updated = 0
    for player_id, player_name in mr_players:
        key    = _normalize(player_name)
        rating = ss_ratings.get(key)

        # Fallback: match on last name alone
        if rating is None:
            parts = key.split()
            last  = parts[-1] if parts else key
            for ss_key, ss_r in ss_ratings.items():
                ss_parts = ss_key.split()
                if last and (last in ss_parts or (ss_parts and ss_parts[-1] == last)):
                    rating = ss_r
                    break

        if rating is not None:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE player_match_stats SET rating = ? "
                    "WHERE player_id = ? AND match_id = ?",
                    (rating, player_id, match_id),
                )
            updated += 1
            print(f"      {player_name}: {rating:.2f}")
        else:
            print(f"      {player_name}: no match in Sofascore lineup")

    print(f"    → {updated}/{len(mr_players)} Sofascore ratings stored")


# ── Main ──────────────────────────────────────────────────────────────────────

def ingest_one(match_id: int) -> None:
    init_schema()
    _migrate_schema()
    print(f"\n=== Post-match ingest: fixture {match_id} ===\n")
    ingest_fixture(match_id)
    ingest_events(match_id)
    ingest_stats(match_id)
    ingest_lineups(match_id)
    ingest_player_stats(match_id)
    ingest_sofascore_ratings(match_id)
    print(f"\nDone. Match {match_id} fully ingested.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest a completed API-Football match into the DB"
    )
    parser.add_argument("match_id", type=int, help="API-Football fixture ID")
    args = parser.parse_args()
    ingest_one(args.match_id)
