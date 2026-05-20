"""Poisson model for match outcomes."""
from __future__ import annotations
import math
from typing import Dict, Iterable, List, Optional, Tuple


def poisson_pmf(k: int, lam: float) -> float:
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def compute_team_strengths(
    matches: Iterable[dict],
    weights: Optional[List[Tuple[float, float]]] = None,
) -> Tuple[Dict[int, dict], float, float]:
    """Compute attack/defense indices for each team.

    weights: per-match list of (w_home, w_away).  w_home down-weights the home
    team's stats when the away opponent is weak; w_away does the same for the
    away team.  When None every match is weighted 1.0.  Weighted league averages
    are used so attack/defense indices stay correctly normalised.
    """
    home_goals_for: Dict[int, float] = {}
    home_goals_against: Dict[int, float] = {}
    home_games: Dict[int, float] = {}
    away_goals_for: Dict[int, float] = {}
    away_goals_against: Dict[int, float] = {}
    away_games: Dict[int, float] = {}
    total_home_goals = total_home_weight = 0.0
    total_away_goals = total_away_weight = 0.0

    for i, m in enumerate(matches):
        if m["home_score"] is None or m["away_score"] is None:
            continue
        w_h, w_a = weights[i] if weights is not None else (1.0, 1.0)
        h, a = int(m["home_team_id"]), int(m["away_team_id"])
        hs, as_ = int(m["home_score"]), int(m["away_score"])

        home_goals_for[h]     = home_goals_for.get(h, 0.0)     + hs  * w_h
        home_goals_against[h] = home_goals_against.get(h, 0.0) + as_ * w_h
        home_games[h]         = home_games.get(h, 0.0)         + w_h

        away_goals_for[a]     = away_goals_for.get(a, 0.0)     + as_ * w_a
        away_goals_against[a] = away_goals_against.get(a, 0.0) + hs  * w_a
        away_games[a]         = away_games.get(a, 0.0)         + w_a

        total_home_goals  += hs  * w_h
        total_home_weight += w_h
        total_away_goals  += as_ * w_a
        total_away_weight += w_a

    if total_home_weight == 0 or total_away_weight == 0:
        raise ValueError("No completed matches found.")

    league_home_avg = total_home_goals / total_home_weight
    league_away_avg = total_away_goals / total_away_weight

    all_team_ids = set(home_games) | set(away_games)
    strengths = {}
    for tid in all_team_ids:
        hg, ag = home_games.get(tid, 0.0), away_games.get(tid, 0.0)
        strengths[tid] = {
            "home_attack":   (home_goals_for.get(tid, 0.0)     / hg) / league_home_avg if hg > 0 else 1.0,
            "home_defense":  (home_goals_against.get(tid, 0.0) / hg) / league_away_avg if hg > 0 else 1.0,
            "away_attack":   (away_goals_for.get(tid, 0.0)     / ag) / league_away_avg if ag > 0 else 1.0,
            "away_defense":  (away_goals_against.get(tid, 0.0) / ag) / league_home_avg if ag > 0 else 1.0,
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
