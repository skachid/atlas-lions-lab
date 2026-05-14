"""Monte Carlo season simulation."""
from __future__ import annotations
from collections import defaultdict
from typing import Dict, List
import numpy as np
from src.poisson import expected_goals


def simulate_one_season(completed_matches, remaining_matches, strengths, league_home_avg, league_away_avg, rng):
    table: Dict[int, dict] = defaultdict(lambda: {"team_id": None, "points": 0, "played": 0,
        "wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0})
    def record(home_id, away_id, hs, as_):
        for tid in (home_id, away_id):
            table[tid]["team_id"] = tid
        h, a = table[home_id], table[away_id]
        h["played"] += 1; a["played"] += 1
        h["goals_for"] += hs; h["goals_against"] += as_
        a["goals_for"] += as_; a["goals_against"] += hs
        if hs > as_: h["points"] += 3; h["wins"] += 1; a["losses"] += 1
        elif hs < as_: a["points"] += 3; a["wins"] += 1; h["losses"] += 1
        else: h["points"] += 1; a["points"] += 1; h["draws"] += 1; a["draws"] += 1
    for m in completed_matches:
        record(int(m["home_team_id"]), int(m["away_team_id"]), int(m["home_score"]), int(m["away_score"]))
    for m in remaining_matches:
        h_id, a_id = int(m["home_team_id"]), int(m["away_team_id"])
        lam_h, lam_a = expected_goals(h_id, a_id, strengths, league_home_avg, league_away_avg)
        record(h_id, a_id, int(rng.poisson(lam_h)), int(rng.poisson(lam_a)))
    rows = list(table.values())
    for r in rows: r["goal_diff"] = r["goals_for"] - r["goals_against"]
    rows.sort(key=lambda r: (r["points"], r["goal_diff"], r["goals_for"]), reverse=True)
    return rows


def run_simulations(completed_matches, remaining_matches, strengths, league_home_avg, league_away_avg, n_simulations=10000, seed=42):
    rng = np.random.default_rng(seed)
    all_team_ids = set()
    for m in completed_matches: all_team_ids.add(int(m["home_team_id"])); all_team_ids.add(int(m["away_team_id"]))
    for m in remaining_matches: all_team_ids.add(int(m["home_team_id"])); all_team_ids.add(int(m["away_team_id"]))
    aggregates = {tid: {"positions": [], "points": []} for tid in all_team_ids}
    for _ in range(n_simulations):
        final_table = simulate_one_season(completed_matches, remaining_matches, strengths, league_home_avg, league_away_avg, rng)
        for position, row in enumerate(final_table, start=1):
            tid = row["team_id"]
            aggregates[tid]["positions"].append(position)
            aggregates[tid]["points"].append(row["points"])
    summary = {}
    for tid, agg in aggregates.items():
        positions = np.array(agg["positions"]); points = np.array(agg["points"])
        if len(positions) == 0: continue
        n_teams = positions.max()
        summary[tid] = {"team_id": tid, "avg_position": float(positions.mean()),
            "median_position": int(np.median(positions)), "avg_points": float(points.mean()),
            "p_champion": float((positions == 1).mean()), "p_top4": float((positions <= 4).mean()),
            "p_relegated": float((positions >= n_teams - 2).mean()),
            "best_position": int(positions.min()), "worst_position": int(positions.max())}
    return summary
