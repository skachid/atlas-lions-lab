"""
Ingest player-level event data from StatsBomb open data.

Phase A — Morocco national team (WC 2022 + AFCON 2023):
    All 11 matches; populates lineups + player_match_stats for both teams.

Phase B — Club competitions:
    Ligue 1 2021/22 + 2022/23  (Hakimi at PSG, Ounahi at Marseille)
    La Liga 2019/20 + 2020/21  (Bounou at Sevilla)
    Only processes matches where identified Moroccan players appear.

Tables written: players, lineups, player_match_stats.
"""
from __future__ import annotations

import math
import warnings
from typing import Optional

import pandas as pd

warnings.filterwarnings("ignore")

from statsbombpy import sb
from src.db import get_connection, init_schema

# ── Competition targets ────────────────────────────────────────────────────────

# (comp_id, season_id) → list of Morocco match_ids
MOROCCO_NATIONAL: dict[tuple[int, int], list[int]] = {
    (43,   106): [3869486, 3869684, 3869552, 3869220, 3857283, 3857277, 3857276],
    (1267, 107): [3922243, 3920419, 3920405, 3920394],
}

# Club competitions to scan (comp_id, season_id, label, team_filter_or_None)
# team_filter limits scanning to matches involving those teams — use for large competitions.
CLUB_COMPS = [
    (7,  108, "Ligue 1 2021/22",  None),          # 26 matches — scan all; Hakimi at PSG
    (7,  235, "Ligue 1 2022/23",  None),           # 32 matches — scan all; Hakimi + Ounahi
    (11,  42, "La Liga 2019/20",  {"Sevilla"}),    # ~380 matches; only Sevilla for Bounou
    (11,  90, "La Liga 2020/21",  {"Sevilla"}),    # ~380 matches; only Sevilla for Bounou
]

MOROCCO_TEAM_NAME = "Morocco"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _name(val) -> str:
    """Unwrap StatsBomb dict-encoded value to its name string."""
    if isinstance(val, dict):
        return val.get("name", "")
    return str(val) if pd.notna(val) else ""


def _ensure_player(conn, player_id: int, player_name: str, country: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO players (player_id, player_name, country) VALUES (?, ?, ?)",
        (player_id, player_name, country),
    )


def _ensure_team(conn, team_id: int, team_name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO teams (team_id, team_name) VALUES (?, ?)",
        (team_id, team_name),
    )


def _starter_ids(lineup_df: pd.DataFrame, events: pd.DataFrame, team_name: str) -> set[int]:
    """Players NOT listed as substitution replacements are starters."""
    team_subs = events[
        (events["type"].apply(_name) == "Substitution") &
        (events["team"] == team_name)
    ]
    sub_replacement_col = "substitution_replacement_id"
    if sub_replacement_col in team_subs.columns:
        came_on = set(team_subs[sub_replacement_col].dropna().astype(int))
    else:
        came_on = set()
    all_players = set(lineup_df["player_id"].astype(int))
    return all_players - came_on


def _minutes_played(player_id: int, is_starter: bool, events: pd.DataFrame) -> int:
    """Estimate minutes from substitution events. Caps at 90 for simplicity."""
    subs = events[events["type"].apply(_name) == "Substitution"]

    came_on_min = None
    if not is_starter and "substitution_replacement_id" in subs.columns:
        came_on = subs[subs["substitution_replacement_id"] == player_id]
        if not came_on.empty:
            came_on_min = int(came_on.iloc[0]["minute"])

    subbed_off_min = None
    off = subs[subs["player_id"] == player_id]
    if not off.empty:
        subbed_off_min = int(off.iloc[0]["minute"])

    start = came_on_min if came_on_min is not None else 0
    end   = subbed_off_min if subbed_off_min is not None else 90
    return max(0, end - start)


def _is_progressive_carry(loc, end_loc) -> bool:
    if not (isinstance(loc, list) and isinstance(end_loc, list)):
        return False
    dist = math.sqrt((end_loc[0] - loc[0]) ** 2 + (end_loc[1] - loc[1]) ** 2)
    return dist >= 10 and (end_loc[0] - loc[0]) > 0


# ── Aggregation ────────────────────────────────────────────────────────────────

def _aggregate(
    lineup_df: pd.DataFrame,
    events: pd.DataFrame,
    match_id: int,
    team_id: int,
    team_name: str,
) -> list[dict]:
    """Return one player_match_stats dict per player in lineup_df."""
    starters = _starter_ids(lineup_df, events, team_name)
    rows = []

    for _, p in lineup_df.iterrows():
        pid = int(p["player_id"])
        is_starter = pid in starters

        # Starting position from lineup
        positions = p.get("positions", [])
        position = None
        if isinstance(positions, list) and positions:
            pos = positions[0]
            position = pos.get("position") if isinstance(pos, dict) else str(pos)

        mins = _minutes_played(pid, is_starter, events)
        pe = events[events["player_id"] == pid]

        # Shots
        shots_df = pe[pe["type"].apply(_name) == "Shot"]
        shots = len(shots_df)
        xg = float(
            pd.to_numeric(shots_df.get("shot_statsbomb_xg", pd.Series()), errors="coerce")
            .fillna(0).sum()
        )
        if "shot_outcome" in shots_df.columns:
            sot_names = shots_df["shot_outcome"].apply(_name)
            goals = int((sot_names == "Goal").sum())
            shots_on_target = int(sot_names.isin({"Goal", "Saved", "Saved To Post"}).sum())
        else:
            goals = shots_on_target = 0

        # Passes
        passes_df = pe[pe["type"].apply(_name) == "Pass"]
        assists    = int(passes_df.get("pass_goal_assist",  pd.Series(False, index=passes_df.index)).fillna(False).sum())
        key_passes = int(passes_df.get("pass_shot_assist",  pd.Series(False, index=passes_df.index)).fillna(False).sum())

        # Progressive carries
        carries_df = pe[pe["type"].apply(_name) == "Carry"]
        prog_carries = 0
        if not carries_df.empty and "carry_end_location" in carries_df.columns:
            prog_carries = int(
                carries_df.apply(
                    lambda r: _is_progressive_carry(r.get("location"), r.get("carry_end_location")),
                    axis=1,
                ).sum()
            )

        # Dribbles completed
        drib_df = pe[pe["type"].apply(_name) == "Dribble"]
        dribbles_completed = 0
        if not drib_df.empty and "dribble_outcome" in drib_df.columns:
            dribbles_completed = int((drib_df["dribble_outcome"].apply(_name) == "Complete").sum())

        pressures = int((pe["type"].apply(_name) == "Pressure").sum())
        turnovers = int(pe["type"].apply(_name).isin({"Miscontrol", "Dispossessed"}).sum())

        rows.append({
            "player_id":           pid,
            "match_id":            match_id,
            "team_id":             team_id,
            "is_starter":          1 if is_starter else 0,
            "minutes_played":      mins,
            "goals":               goals,
            "assists":             assists,
            "shots":               shots,
            "shots_on_target":     shots_on_target,
            "xg":                  round(xg, 4),
            "key_passes":          key_passes,
            "progressive_carries": prog_carries,
            "dribbles_completed":  dribbles_completed,
            "pressures":           pressures,
            "turnovers":           turnovers,
            "position":            position,
        })
    return rows


# ── Match ingest ───────────────────────────────────────────────────────────────

def _ingest_match(
    match_id: int,
    target_team_names: Optional[set[str]] = None,
    target_player_ids: Optional[set[int]] = None,
) -> set[int]:
    """
    Fetch and store lineups + player_match_stats for one match.
    target_team_names: if set, only process those teams (None = all teams).
    target_player_ids: if set, only process players in that set.
    Returns player_ids ingested.
    """
    try:
        raw_lineups = sb.lineups(match_id=match_id)
        events = sb.events(match_id=match_id)
    except Exception as exc:
        print(f"      ✗ match {match_id}: {exc}")
        return set()

    ingested: set[int] = set()

    with get_connection() as conn:
        for team_name, lineup_df in raw_lineups.items():
            if lineup_df.empty:
                continue

            if target_team_names and team_name not in target_team_names:
                continue

            # Team ID lookup
            row = conn.execute(
                "SELECT team_id FROM teams WHERE team_name = ?", (team_name,)
            ).fetchone()
            if row is None:
                continue
            tid = row[0]

            # For club scans, skip teams with no target players
            if target_player_ids is not None:
                players_here = set(lineup_df["player_id"].astype(int))
                if not players_here & target_player_ids:
                    continue
                lineup_df = lineup_df[lineup_df["player_id"].isin(target_player_ids)]

            # Persist players
            for _, p in lineup_df.iterrows():
                pid = int(p["player_id"])
                country = p["country"].get("name") if isinstance(p.get("country"), dict) else None
                _ensure_player(conn, pid, str(p["player_name"]), country)

                positions = p.get("positions", [])
                pos_name = None
                if isinstance(positions, list) and positions:
                    pos0 = positions[0]
                    pos_name = pos0.get("position") if isinstance(pos0, dict) else str(pos0)

                starters = _starter_ids(lineup_df, events, team_name)
                conn.execute(
                    "INSERT OR IGNORE INTO lineups "
                    "(match_id, team_id, player_id, jersey_number, position, is_starter) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (match_id, tid, pid, p.get("jersey_number"), pos_name, 1 if pid in starters else 0),
                )

            # Filter events to this team (team column is a plain string in open data)
            team_events = events[events["team"] == team_name]

            for sr in _aggregate(lineup_df, team_events, match_id, tid, team_name):
                conn.execute(
                    "INSERT OR IGNORE INTO player_match_stats "
                    "(player_id, match_id, team_id, is_starter, minutes_played, goals, assists, "
                    " shots, shots_on_target, xg, key_passes, progressive_carries, "
                    " dribbles_completed, pressures, turnovers, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sr["player_id"], sr["match_id"], sr["team_id"], sr["is_starter"],
                     sr["minutes_played"], sr["goals"], sr["assists"], sr["shots"],
                     sr["shots_on_target"], sr["xg"], sr["key_passes"],
                     sr["progressive_carries"], sr["dribbles_completed"],
                     sr["pressures"], sr["turnovers"], sr["position"]),
                )
                ingested.add(sr["player_id"])

    return ingested


# ── Phase A: Morocco national team ─────────────────────────────────────────────

def ingest_morocco_national() -> set[int]:
    """Ingest all Morocco national team matches. Returns Moroccan player ID set."""
    print("\n=== Phase A: Morocco National Team ===")
    moroccan_ids: set[int] = set()

    for (comp_id, season_id), match_ids in MOROCCO_NATIONAL.items():
        label = f"comp={comp_id} season={season_id}"
        print(f"  {label} — {len(match_ids)} matches")
        for mid in match_ids:
            ids = _ingest_match(mid)
            print(f"    match {mid}: {len(ids)} players")

        # Collect Morocco player IDs
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT l.player_id FROM lineups l "
                "JOIN teams t ON l.team_id = t.team_id "
                "JOIN matches m ON l.match_id = m.match_id "
                "WHERE t.team_name = ? AND m.competition_id = ? AND m.season_id = ?",
                (MOROCCO_TEAM_NAME, comp_id, season_id),
            ).fetchall()
        moroccan_ids |= {r[0] for r in rows}

    print(f"\n  Identified {len(moroccan_ids)} unique Moroccan players")
    return moroccan_ids


# ── Phase B: Club competitions ──────────────────────────────────────────────────

def ingest_club_comps(moroccan_ids: set[int]) -> None:
    """Scan club competitions; ingest events for matches where Moroccan players appear."""
    print("\n=== Phase B: Club Competitions ===")

    for comp_id, season_id, label, team_filter in CLUB_COMPS:
        print(f"\n  {label}...")
        try:
            matches_df = sb.matches(competition_id=comp_id, season_id=season_id)
        except Exception as exc:
            print(f"    FAILED to load matches: {exc}")
            continue

        # Narrow to matches involving filtered teams when specified
        if team_filter:
            def _involves_filter(row):
                ht = row["home_team"]
                at = row["away_team"]
                ht_name = ht.get("home_team_name") if isinstance(ht, dict) else str(ht)
                at_name = at.get("away_team_name") if isinstance(at, dict) else str(at)
                return ht_name in team_filter or at_name in team_filter
            matches_df = matches_df[matches_df.apply(_involves_filter, axis=1)]
            print(f"    (filtered to {len(matches_df)} matches involving {team_filter})")

        hit_count = 0
        for _, match_row in matches_df.iterrows():
            mid = int(match_row["match_id"])
            try:
                raw_lineups = sb.lineups(match_id=mid)
            except Exception:
                continue

            all_players = set()
            for _, ldf in raw_lineups.items():
                all_players |= set(ldf["player_id"].astype(int))

            if not all_players & moroccan_ids:
                continue

            ids = _ingest_match(mid, target_player_ids=moroccan_ids)
            if ids:
                ht = match_row["home_team"]
                at = match_row["away_team"]
                ht_name = ht.get("home_team_name") if isinstance(ht, dict) else str(ht)
                at_name = at.get("away_team_name") if isinstance(at, dict) else str(at)
                print(f"    ✓ {ht_name} vs {at_name}  ({len(ids)} Moroccan players)")
                hit_count += 1

        print(f"    → {hit_count} matches with Moroccan players")


# ── Entry point ────────────────────────────────────────────────────────────────

def ingest_all() -> None:
    init_schema()
    moroccan_ids = ingest_morocco_national()
    ingest_club_comps(moroccan_ids)
    print("\nDone.")
