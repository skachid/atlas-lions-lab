"""Print a structured post-match data report for a completed Morocco match.

Usage:
    python scripts/post_match_report.py <match_id>

Reads only from the local DB — no API calls. Run post_match_ingest.py first.

Report covers:
  - Final score, possession, shots, xG for both teams
  - Every Moroccan player's stats, sorted by Sofascore rating
  - Set piece goals flagged separately
  - Disciplinary summary (yellows, reds)
  - Comparison to Morocco's average stats across previous matches in DB
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import get_connection


# ── Stat key → display label mappings ────────────────────────────────────────

# API-Football stat_name values used in match_stats table
_TEAM_STAT_ROWS = [
    ("Ball Possession",  "Possession"),
    ("Total Shots",      "Shots"),
    ("Shots on Goal",    "Shots on Target"),
    ("Shots off Goal",   "Shots off Target"),
    ("Blocked Shots",    "Shots Blocked"),
    ("expected_goals",   "xG"),
    ("Passes accurate",  "Passes Completed"),
    ("Total passes",     "Total Passes"),
    ("Passes %",         "Pass Accuracy"),
    ("Fouls",            "Fouls"),
    ("Corner Kicks",     "Corners"),
    ("Offsides",         "Offsides"),
]

# Same keys to compare against historical averages (numeric-safe only)
_COMPARE_ROWS = [
    ("Ball Possession", "Possession",        "%"),
    ("Total Shots",     "Shots",             ""),
    ("Shots on Goal",   "Shots on Target",   ""),
    ("expected_goals",  "xG",                ""),
    ("Passes accurate", "Passes Completed",  ""),
    ("Fouls",           "Fouls",             ""),
    ("Corner Kicks",    "Corners",           ""),
    ("Offsides",        "Offsides",          ""),
]

SET_PIECE_DETAILS = frozenset({"Free Kick", "Direct Free Kick", "Corner", "Penalty"})


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_match(match_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT m.match_id, m.match_date, m.competition_stage, m.stadium, m.referee,
                   c.competition_name, c.season_name,
                   th.team_name AS home_name, th.team_id AS home_id,
                   ta.team_name AS away_name, ta.team_id AS away_id,
                   m.home_score, m.away_score
            FROM matches m
            JOIN competitions c ON c.competition_id = m.competition_id
                                AND c.season_id     = m.season_id
            JOIN teams th ON th.team_id = m.home_team_id
            JOIN teams ta ON ta.team_id = m.away_team_id
            WHERE m.match_id = ?
            """,
            (match_id,),
        ).fetchone()
    if not row:
        raise SystemExit(f"Match {match_id} not found. Run post_match_ingest.py first.")
    return dict(row)


def _team_stats(match_id: int, team_id: int) -> dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT stat_name, stat_value FROM match_stats "
            "WHERE match_id=? AND team_id=?",
            (match_id, team_id),
        ).fetchall()
    return {r[0]: (r[1] or "") for r in rows}


def _events(match_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.minute, e.extra_minute, e.event_type, e.detail, e.comments,
                   e.team_id,
                   p.player_name,
                   rp.player_name AS related_name
            FROM match_events e
            LEFT JOIN players p  ON p.player_id  = e.player_id
            LEFT JOIN players rp ON rp.player_id = e.related_player_id
            WHERE e.match_id = ?
            ORDER BY e.minute, COALESCE(e.extra_minute, 0)
            """,
            (match_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _morocco_player_stats(match_id: int, morocco_id: int) -> list[dict]:
    """Moroccan players sorted by rating desc, then minutes desc."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.player_name,
                   COALESCE(l.position, pms.position)  AS pos,
                   pms.is_starter,
                   pms.minutes_played,
                   pms.goals,
                   pms.assists,
                   pms.shots,
                   pms.shots_on_target,
                   pms.key_passes,
                   pms.dribbles_completed,
                   pms.turnovers           AS possession_lost,
                   COALESCE(pms.aerials_won,     0) AS aerials_won,
                   COALESCE(pms.fouls_drawn,     0) AS fouls_drawn,
                   COALESCE(pms.fouls_committed, 0) AS fouls_committed,
                   COALESCE(pms.yellow_cards,    0) AS yellow_cards,
                   COALESCE(pms.red_cards,       0) AS red_cards,
                   pms.rating
            FROM player_match_stats pms
            JOIN players p ON p.player_id = pms.player_id
            LEFT JOIN lineups l ON l.match_id  = pms.match_id
                               AND l.team_id   = pms.team_id
                               AND l.player_id = pms.player_id
            WHERE pms.match_id = ? AND pms.team_id = ?
            ORDER BY pms.rating DESC, pms.minutes_played DESC
            """,
            (match_id, morocco_id),
        ).fetchall()
    return [dict(r) for r in rows]


def _morocco_averages(current_match_id: int, morocco_id: int) -> tuple[dict[str, float], int]:
    """
    Average team stats for Morocco across all other matches in DB.
    Returns (averages_dict, n_matches).
    """
    with get_connection() as conn:
        match_ids = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT match_id FROM matches "
                "WHERE (home_team_id=? OR away_team_id=?) AND match_id != ?",
                (morocco_id, morocco_id, current_match_id),
            ).fetchall()
        ]
        if not match_ids:
            return {}, 0

        ph = ",".join("?" * len(match_ids))
        rows = conn.execute(
            f"""
            SELECT stat_name,
                   AVG(CAST(REPLACE(REPLACE(stat_value, '%', ''), ' ', '') AS REAL))
            FROM match_stats
            WHERE match_id IN ({ph})
              AND team_id = ?
              AND stat_value IS NOT NULL
              AND stat_value != ''
              AND stat_value != 'null'
            GROUP BY stat_name
            """,
            (*match_ids, morocco_id),
        ).fetchall()
    avgs = {r[0]: r[1] for r in rows if r[1] is not None}
    return avgs, len(match_ids)


# ── Formatting helpers ────────────────────────────────────────────────────────

def _min_str(minute, extra) -> str:
    return f"{minute}+{extra}'" if extra else f"{minute}'"


def _sep(width: int = 70) -> str:
    return "-" * width


def _header(title: str, width: int = 70) -> str:
    return f"\n{title}\n{_sep(width)}"


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(match_id: int) -> None:
    match    = _get_match(match_id)
    home_id  = match["home_id"]
    away_id  = match["away_id"]
    home_nm  = match["home_name"]
    away_nm  = match["away_name"]

    # Identify Morocco's side
    morocco_id   = None
    opponent_id  = None
    opponent_nm  = None
    for tid, tname in ((home_id, home_nm), (away_id, away_nm)):
        if tname.strip().lower() == "morocco":
            morocco_id  = tid
        else:
            opponent_id = tid
            opponent_nm = tname

    home_stats = _team_stats(match_id, home_id)
    away_stats = _team_stats(match_id, away_id)
    all_events = _events(match_id)

    # ── Header ─────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"  {match['competition_name']}  ·  {match['season_name']}")
    print(f"  {match['competition_stage']}  ·  {match['match_date']}")
    if match["stadium"]:
        print(f"  {match['stadium']}")
    if match["referee"]:
        print(f"  Referee: {match['referee']}")
    print()
    print(f"  {home_nm:>28}  {match['home_score']}–{match['away_score']}  {away_nm}")
    print("=" * 70)

    # ── Team stats ──────────────────────────────────────────────────────────
    print(_header("TEAM STATS"))
    col_w = 20
    print(f"  {'':23} {home_nm:>{col_w}}   {away_nm:<{col_w}}")
    print("  " + _sep(66))
    for api_key, label in _TEAM_STAT_ROWS:
        hv = home_stats.get(api_key, "")
        av = away_stats.get(api_key, "")
        if hv or av:
            print(f"  {label:<23} {hv:>{col_w}}   {av:<{col_w}}")

    # ── Goals ───────────────────────────────────────────────────────────────
    goals_all = [e for e in all_events if e["event_type"] in ("goal", "own_goal")]
    print(_header("GOALS"))
    if goals_all:
        for g in goals_all:
            side    = home_nm if g["team_id"] == home_id else away_nm
            og_tag  = " [OG]"      if g["event_type"] == "own_goal"      else ""
            sp_tag  = " [SET PIECE]" if g["detail"] in SET_PIECE_DETAILS else ""
            ast_str = f"  (assist: {g['related_name']})" if g["related_name"] else ""
            print(
                f"  {_min_str(g['minute'], g['extra_minute']):<8}"
                f"  {(g['player_name'] or '?'):<26}  ({side})"
                f"{og_tag}{sp_tag}{ast_str}"
            )
    else:
        print("  No goals recorded")

    # ── Set piece goals ─────────────────────────────────────────────────────
    sp_goals = [g for g in goals_all if g["detail"] in SET_PIECE_DETAILS]
    if sp_goals:
        print(_header("SET PIECE GOALS"))
        for g in sp_goals:
            side = home_nm if g["team_id"] == home_id else away_nm
            print(
                f"  {_min_str(g['minute'], g['extra_minute']):<8}"
                f"  {(g['player_name'] or '?'):<26}  {g['detail']}  ({side})"
            )

    # ── Moroccan player stats ───────────────────────────────────────────────
    if morocco_id is not None:
        players = _morocco_player_stats(match_id, morocco_id)
        if players:
            print(_header("MOROCCAN PLAYER STATS  (Sofascore rating, highest → lowest)"))
            hdr = (
                f"  {'Player':<22} {'Pos':>4} {'Min':>4}  "
                f"{'G':>2} {'A':>2} {'KP':>3} {'Drb':>3} "
                f"{'Aer':>3} {'FD':>3} {'FC':>3} {'YC':>3} {'RC':>3}  {'Rating':>6}"
            )
            print(hdr)
            print("  " + _sep(hdr.__len__() - 2))
            for p in players:
                rating_s = f"{p['rating']:.2f}" if p["rating"] is not None else "   —"
                starter_s = "" if p["is_starter"] else "*"
                print(
                    f"  {(p['player_name'] or '')[:22]:<22}{starter_s:<1}"
                    f" {(p['pos'] or '')[:4]:>4} {(p['minutes_played'] or 0):>4}  "
                    f"{(p['goals'] or 0):>2} {(p['assists'] or 0):>2} "
                    f"{(p['key_passes'] or 0):>3} {(p['dribbles_completed'] or 0):>3} "
                    f"{p['aerials_won']:>3} {p['fouls_drawn']:>3} "
                    f"{p['fouls_committed']:>3} {p['yellow_cards']:>3} "
                    f"{p['red_cards']:>3}  {rating_s:>6}"
                )
            print("  * = substitute")
        else:
            print(_header("MOROCCAN PLAYER STATS"))
            print("  No player stats found — run post_match_ingest.py first")

    # ── Disciplinary ────────────────────────────────────────────────────────
    print(_header("DISCIPLINARY"))
    yellows = [e for e in all_events if e["event_type"] == "yellow_card"]
    reds    = [e for e in all_events if e["event_type"] in ("red_card", "yellow_red_card")]
    if yellows or reds:
        for e in yellows:
            side = home_nm if e["team_id"] == home_id else away_nm
            print(f"  YC  {_min_str(e['minute'], e['extra_minute']):<8}  {e['player_name']}  ({side})")
        for e in reds:
            kind = "2Y" if e["event_type"] == "yellow_red_card" else "RC"
            side = home_nm if e["team_id"] == home_id else away_nm
            print(f"  {kind}  {_min_str(e['minute'], e['extra_minute']):<8}  {e['player_name']}  ({side})")
    else:
        print("  No cards")

    # ── Substitutions ───────────────────────────────────────────────────────
    print(_header("SUBSTITUTIONS"))
    subs = [e for e in all_events if e["event_type"] == "subst"]
    if subs:
        for e in subs:
            side = home_nm if e["team_id"] == home_id else away_nm
            print(
                f"  {_min_str(e['minute'], e['extra_minute']):<8}  "
                f"{side:<22}  ↑ {e['related_name'] or '?'}   ↓ {e['player_name'] or '?'}"
            )
    else:
        print("  No substitutions recorded")

    # ── Historical comparison ───────────────────────────────────────────────
    if morocco_id is not None:
        avgs, n_matches = _morocco_averages(match_id, morocco_id)
        if avgs:
            cur_stats = home_stats if morocco_id == home_id else away_stats
            print(_header(f"COMPARISON TO MOROCCO AVERAGES  ({n_matches} previous matches in DB)"))
            col = 15
            print(f"  {'Stat':<23} {'This Match':>{col}}   {'Avg (prev.)':>{col}}")
            print("  " + _sep(60))
            for api_key, label, unit in _COMPARE_ROWS:
                cur_val = cur_stats.get(api_key, "—")
                avg_val = avgs.get(api_key)
                avg_s   = f"{avg_val:.1f}{unit}" if avg_val is not None else "—"
                print(f"  {label:<23} {str(cur_val):>{col}}   {avg_s:>{col}}")
        else:
            print(_header("COMPARISON TO MOROCCO AVERAGES"))
            print("  No previous match stats in DB for comparison yet")

    print()
    print("=" * 70)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Print structured post-match report (DB-only, no API calls)"
    )
    parser.add_argument("match_id", type=int, help="API-Football fixture ID")
    args = parser.parse_args()
    print_report(args.match_id)
