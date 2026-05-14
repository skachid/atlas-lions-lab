"""Poisson model for match outcomes."""
from __future__ import annotations
import math
from typing import Iterable, Dict, Tuple


def poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def compute_team_strengths(matches: Iterable[dict]) -> Tuple[Dict[int, dict], float, float]:
    home_goals_for, home_goals_against, home_games = {}, {}, {}
    away_goals_for, away_goals_against, away_games = {}, {}, {}
    total_home_goals = total_away_goals = total_matches = 0
    for m in matches:
        if m["home_score"] is None or m["away_score"] is None:
            continue
        h, a = int(m["home_team_id"]), int(m["away_team_id"])
        hs, as_ = int(m["home_score"]), int(m["away_score"])
        home_goals_for[h] = home_goals_for.get(h, 0) + hs
        home_goals_against[h] = home_goals_against.get(h, 0) + as_
        home_games[h] = home_games.get(h, 0) + 1
        away_goals_for[a] = away_goals_for.get(a, 0) + as_
        away_goals_against[a] = away_goals_against.get(a, 0) + hs
        away_games[a] = away_games.get(a, 0) + 1
        total_home_goals += hs
        total_away_goals += as_
        total_matches += 1
    if total_matches == 0:
        raise ValueError("No completed matches found.")
    league_home_avg = total_home_goals / total_matches
    league_away_avg = total_away_goals / total_matches
    all_team_ids = set(home_games) | set(away_games)
    strengths = {}
    for tid in all_team_ids:
        hg, ag = home_games.get(tid, 0), away_games.get(tid, 0)
        strengths[tid] = {
            "home_attack": (home_goals_for.get(tid, 0) / hg) / league_home_avg if hg > 0 else 1.0,
            "home_defense": (home_goals_against.get(tid, 0) / hg) / league_away_avg if hg > 0 else 1.0,
            "away_attack": (away_goals_for.get(tid, 0) / ag) / league_away_avg if ag > 0 else 1.0,
            "away_defense": (away_goals_against.get(tid, 0) / ag) / league_home_avg if ag > 0 else 1.0,
        }
    return strengths, league_home_avg, league_away_avg


def expected_goals(home_team_id, away_team_id, strengths, league_home_avg, league_away_avg):
    h = strengths.get(home_team_id, {"home_attack": 1.0, "home_defense": 1.0})
    a = strengths.get(away_team_id, {"away_attack": 1.0, "away_defense": 1.0})
    return league_home_avg * h["home_attack"] * a["away_defense"], league_away_avg * a["away_attack"] * h["home_defense"]


def match_outcome_probabilities(lambda_home, lambda_away, max_goals=8):
    p_home_win = p_draw = p_away_win = 0.0
    score_grid = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(h, lambda_home) * poisson_pmf(a, lambda_away)
            score_grid[(h, a)] = p
            if h > a: p_home_win += p
            elif h < a: p_away_win += p
            else: p_draw += p
    return {"home_win": p_home_win, "draw": p_draw, "away_win": p_away_win,
            "score_grid": score_grid, "expected_home_goals": lambda_home, "expected_away_goals": lambda_away}
