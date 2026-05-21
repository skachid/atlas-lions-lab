"""Elo rating system for soccer teams."""
from __future__ import annotations
import math
from collections import defaultdict
from typing import Dict, Iterable

DEFAULT_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE = 70.0


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def goal_difference_multiplier(goal_diff: int, rating_diff: float) -> float:
    abs_diff = abs(goal_diff)
    if abs_diff == 0:
        return 1.0
    return math.log(abs_diff + 1) * (2.2 / (rating_diff * 0.001 + 2.2))


def update_ratings(rating_home, rating_away, home_score, away_score, k=K_FACTOR, home_adv=HOME_ADVANTAGE):
    effective_home = rating_home + home_adv
    e_home = expected_score(effective_home, rating_away)
    e_away = 1.0 - e_home
    if home_score > away_score:
        s_home, s_away = 1.0, 0.0
    elif home_score < away_score:
        s_home, s_away = 0.0, 1.0
    else:
        s_home, s_away = 0.5, 0.5
    g = goal_difference_multiplier(home_score - away_score, effective_home - rating_away)
    return rating_home + k * g * (s_home - e_home), rating_away + k * g * (s_away - e_away)


def fit_ratings(matches: Iterable[dict], initial_ratings: Dict[int, float] | None = None) -> Dict[int, float]:
    ratings: Dict[int, float] = defaultdict(lambda: DEFAULT_RATING)
    if initial_ratings:
        ratings.update(initial_ratings)
    for m in matches:
        if m["home_score"] is None or m["away_score"] is None:
            continue
        h_id, a_id = int(m["home_team_id"]), int(m["away_team_id"])
        h_new, a_new = update_ratings(ratings[h_id], ratings[a_id], int(m["home_score"]), int(m["away_score"]))
        ratings[h_id] = h_new
        ratings[a_id] = a_new
    return dict(ratings)


def predict_win_probability(rating_home, rating_away, home_adv=HOME_ADVANTAGE):
    return expected_score(rating_home + home_adv, rating_away)
