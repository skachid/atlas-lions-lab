"""2026 FIFA World Cup Monte Carlo simulator.

Uses Poisson goal model for group stage (scores matter for GD tiebreakers)
and Elo win probabilities for knockout stage.

Usage:
    from src.wc_simulator import run_wc_simulations
    summary = run_wc_simulations(n_simulations=10000)
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.db import get_connection
from src.elo import fit_ratings, DEFAULT_RATING, HOME_ADVANTAGE
from src.poisson import compute_team_strengths

# ── Tournament structure ───────────────────────────────────────────────────────

WC_2026_GROUPS: Dict[str, List[str]] = {
    "A": ["Mexico", "South Africa", "South Korea", "Czech Republic"],
    "B": ["Canada", "Bosnia and Herzegovina", "Qatar", "Switzerland"],
    "C": ["Brazil", "Morocco", "Haiti", "Scotland"],
    "D": ["United States", "Paraguay", "Australia", "Turkey"],
    "E": ["Germany", "Curaçao", "Ivory Coast", "Ecuador"],
    "F": ["Netherlands", "Japan", "Sweden", "Tunisia"],
    "G": ["Belgium", "Egypt", "Iran", "New Zealand"],
    "H": ["Spain", "Cape Verde", "Saudi Arabia", "Uruguay"],
    "I": ["France", "Senegal", "Iraq", "Norway"],
    "J": ["Argentina", "Algeria", "Austria", "Jordan"],
    "K": ["Portugal", "DR Congo", "Uzbekistan", "Colombia"],
    "L": ["England", "Croatia", "Ghana", "Panama"],
}

# R32 bracket: each entry is (slot_a, slot_b) where slot is a group-position code
# Codes: "W-X" = winner of group X, "RU-X" = runner-up of group X, "3RD" = best 3rd-place
# Match numbers map to list indices (M73=0 … M88=15)
R32_SLOTS: List[Tuple[str, str]] = [
    ("RU-A", "RU-B"),   # M73 → R32[0]
    ("W-E",  "3RD"),    # M74 → R32[1]
    ("W-F",  "RU-C"),   # M75 → R32[2]
    ("W-C",  "RU-F"),   # M76 → R32[3]  ← Group C winner here
    ("RU-E", "RU-I"),   # M78 → R32[4]
    ("W-I",  "3RD"),    # M77 → R32[5]
    ("W-A",  "3RD"),    # M79 → R32[6]
    ("W-L",  "3RD"),    # M80 → R32[7]
    ("W-D",  "3RD"),    # M81 → R32[8]
    ("W-G",  "3RD"),    # M82 → R32[9]
    ("RU-K", "RU-L"),   # M83 → R32[10]
    ("W-H",  "RU-J"),   # M84 → R32[11]
    ("W-B",  "3RD"),    # M85 → R32[12]
    ("W-J",  "RU-H"),   # M86 → R32[13]
    ("W-K",  "3RD"),    # M87 → R32[14]
    ("RU-D", "RU-G"),   # M88 → R32[15]
]

# R16 pairs by R32 index
R16_SLOTS = [(1,5),(0,2),(3,4),(6,7),(10,11),(8,9),(13,15),(12,14)]
# QF pairs by R16 index
QF_SLOTS  = [(0,1),(4,5),(2,3),(6,7)]
# SF pairs by QF index
SF_SLOTS  = [(0,1),(2,3)]


# International competition IDs — used to filter Poisson training data
INTERNATIONAL_COMP_IDS = {
    43,    # FIFA World Cup
    55,    # UEFA Euro
    53,    # UEFA Women's Euro
    72,    # Women's World Cup
    223,   # Copa America
    1267,  # AFCON
    1470,  # FIFA U20 World Cup
    2001,  # CAF WC 2026 Qualifiers
    2002,  # CONMEBOL WC 2026 Qualifiers
    2003,  # UEFA WC 2026 Qualifiers
    2004,  # CONCACAF WC 2026 Qualifiers
}


# ── Data loading ───────────────────────────────────────────────────────────────

def _build_canonical_ids() -> Tuple[Dict[str, int], Dict[int, int]]:
    """
    Many teams appear twice — once from StatsBomb (small int ID) and once from
    Wikipedia scraping (large hash-based ID). Build a canonical mapping so all
    match history for the same team name is combined.

    Returns:
        name_to_canonical  — team_name → preferred team_id
        alias_to_canonical — every team_id → its canonical team_id
    """
    with get_connection() as conn:
        team_rows = conn.execute("SELECT team_id, team_name FROM teams").fetchall()
        counts = {
            tid: conn.execute(
                "SELECT COUNT(*) FROM matches WHERE home_team_id=? OR away_team_id=?",
                (tid, tid)
            ).fetchone()[0]
            for tid, _ in team_rows
        }

    name_to_ids: Dict[str, List[int]] = defaultdict(list)
    for tid, name in team_rows:
        name_to_ids[name].append(tid)

    name_to_canonical: Dict[str, int] = {}
    alias_to_canonical: Dict[int, int] = {}
    for name, ids in name_to_ids.items():
        canonical = max(ids, key=lambda i: counts.get(i, 0))
        name_to_canonical[name] = canonical
        for i in ids:
            alias_to_canonical[i] = canonical

    return name_to_canonical, alias_to_canonical


def _load_all_matches(alias_map: Dict[int, int]) -> List[dict]:
    """All matches with team IDs remapped to canonical IDs."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT home_team_id, away_team_id, home_score, away_score "
            "FROM matches WHERE home_score IS NOT NULL AND away_score IS NOT NULL"
        ).fetchall()
    return [{
        "home_team_id": alias_map.get(r[0], r[0]),
        "away_team_id": alias_map.get(r[1], r[1]),
        "home_score": r[2], "away_score": r[3],
    } for r in rows]


def _load_international_matches(alias_map: Dict[int, int]) -> List[dict]:
    """International-only matches (qualifiers + tournaments) for Poisson calibration."""
    ids = ",".join(str(i) for i in INTERNATIONAL_COMP_IDS)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT home_team_id, away_team_id, home_score, away_score "
            f"FROM matches WHERE competition_id IN ({ids}) "
            f"AND home_score IS NOT NULL AND away_score IS NOT NULL"
        ).fetchall()
    return [{
        "home_team_id": alias_map.get(r[0], r[0]),
        "away_team_id": alias_map.get(r[1], r[1]),
        "home_score": r[2], "away_score": r[3],
    } for r in rows]


def _neutral_lambdas(a_id: Optional[int], b_id: Optional[int],
                     strengths: dict, avg_goals: float) -> Tuple[float, float]:
    """Expected goals for a neutral-venue match."""
    a = strengths.get(a_id, {})
    b = strengths.get(b_id, {})
    a_att = (a.get("home_attack", 1.0) + a.get("away_attack", 1.0)) / 2
    a_def = (a.get("home_defense", 1.0) + a.get("away_defense", 1.0)) / 2
    b_att = (b.get("home_attack", 1.0) + b.get("away_attack", 1.0)) / 2
    b_def = (b.get("home_defense", 1.0) + b.get("away_defense", 1.0)) / 2
    return avg_goals * a_att * b_def, avg_goals * b_att * a_def


# ── Match simulation ───────────────────────────────────────────────────────────

def _sim_group_match(a_id, b_id, strengths, avg_goals, rng) -> Tuple[int, int]:
    la, lb = _neutral_lambdas(a_id, b_id, strengths, avg_goals)
    return int(rng.poisson(la)), int(rng.poisson(lb))


def _sim_knockout_match(a: str, b: str, name_id: Dict[str, int],
                        elo: Dict[int, float], rng) -> str:
    """Simulate a knockout match; returns winner name. Draws go to pens (Elo-weighted)."""
    a_id = name_id.get(a)
    b_id = name_id.get(b)
    ra = elo.get(a_id, DEFAULT_RATING)
    rb = elo.get(b_id, DEFAULT_RATING)
    # Neutral venue: no home advantage
    p_a = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
    roll = rng.random()
    if roll < p_a:
        return a
    elif roll < p_a + (1 - p_a) * 0.5:   # rough draw band → penalty coin weighted by Elo
        return a if rng.random() < p_a else b
    else:
        return b


# ── Group stage ────────────────────────────────────────────────────────────────

def _sim_group(teams: List[str], name_id: Dict[str, int],
               strengths: dict, avg_goals: float, rng) -> List[dict]:
    """Simulate a 4-team group. Returns standings sorted by pts/GD/GF."""
    table: Dict[str, dict] = {
        t: {"team": t, "pts": 0, "played": 0,
            "wins": 0, "draws": 0, "losses": 0,
            "gf": 0, "ga": 0, "gd": 0}
        for t in teams
    }
    pairs = [(teams[i], teams[j]) for i in range(4) for j in range(i+1, 4)]
    for home, away in pairs:
        h_id = name_id.get(home)
        a_id = name_id.get(away)
        hg, ag = _sim_group_match(h_id, a_id, strengths, avg_goals, rng)
        for t, gf, ga in [(home, hg, ag), (away, ag, hg)]:
            table[t]["played"] += 1
            table[t]["gf"] += gf
            table[t]["ga"] += ga
            table[t]["gd"] += gf - ga
        if hg > ag:
            table[home]["pts"] += 3; table[home]["wins"] += 1; table[away]["losses"] += 1
        elif hg < ag:
            table[away]["pts"] += 3; table[away]["wins"] += 1; table[home]["losses"] += 1
        else:
            table[home]["pts"] += 1; table[home]["draws"] += 1
            table[away]["pts"] += 1; table[away]["draws"] += 1

    return sorted(table.values(), key=lambda r: (r["pts"], r["gd"], r["gf"]), reverse=True)


# ── Full tournament simulation ─────────────────────────────────────────────────

def _sim_tournament(name_id: Dict[str, int], strengths: dict,
                    avg_goals: float, elo: Dict[int, float], rng) -> Dict[str, str]:
    """
    Returns dict: team_name → furthest_round reached.
    Rounds: 'Group', 'R32', 'R16', 'QF', 'SF', 'Final', 'Champion'
    """
    reached: Dict[str, str] = {t: "Group" for g in WC_2026_GROUPS.values() for t in g}

    # ── Group stage ────────────────────────────────────────────────────────────
    group_results: Dict[str, List[dict]] = {}
    third_place_teams: List[dict] = []

    for grp, teams in WC_2026_GROUPS.items():
        standings = _sim_group(teams, name_id, strengths, avg_goals, rng)
        group_results[grp] = standings
        third_place_teams.append({**standings[2], "group": grp})

    # ── Best 8 third-place teams ────────────────────────────────────────────────
    third_place_teams.sort(key=lambda r: (r["pts"], r["gd"], r["gf"]), reverse=True)
    advancing_thirds = [r["team"] for r in third_place_teams[:8]]
    # Mark non-advancing 3rd/4th place teams as eliminated at group stage (already default)

    # ── Resolve bracket slots ──────────────────────────────────────────────────
    def resolve(slot: str, third_pool: List[str], third_idx: list) -> str:
        if slot.startswith("W-"):
            return group_results[slot[2:]]["0"]["team"] if False else group_results[slot[2:]][0]["team"]
        elif slot.startswith("RU-"):
            return group_results[slot[3:]][1]["team"]
        else:  # "3RD" — assign next available best third-place team
            t = third_pool[third_idx[0]]
            third_idx[0] += 1
            return t

    # Shuffle 3rd-place pool for random bracket assignment
    thirds_pool = advancing_thirds.copy()
    rng.shuffle(thirds_pool)
    third_idx = [0]

    r32_pairs = [(resolve(a, thirds_pool, third_idx), resolve(b, thirds_pool, third_idx))
                 for a, b in R32_SLOTS]

    # ── Knockout rounds ────────────────────────────────────────────────────────
    def play_round(pairs, round_name):
        winners = []
        for a, b in pairs:
            w = _sim_knockout_match(a, b, name_id, elo, rng)
            loser = b if w == a else a
            reached[w] = round_name
            winners.append(w)
        return winners

    # Mark advancing teams before knockout begins
    for grp, standings in group_results.items():
        for pos, row in enumerate(standings):
            t = row["team"]
            if pos == 0 or pos == 1:
                reached[t] = "R32"
    for t in advancing_thirds:
        reached[t] = "R32"

    r32_winners = play_round(r32_pairs, "R16")
    r16_pairs   = [(r32_winners[a], r32_winners[b]) for a, b in R16_SLOTS]
    r16_winners = play_round(r16_pairs, "QF")
    qf_pairs    = [(r16_winners[a], r16_winners[b]) for a, b in QF_SLOTS]
    qf_winners  = play_round(qf_pairs, "SF")
    sf_pairs    = [(qf_winners[a], qf_winners[b]) for a, b in SF_SLOTS]
    sf_winners  = play_round(sf_pairs, "Final")

    champion = _sim_knockout_match(sf_winners[0], sf_winners[1], name_id, elo, rng)
    reached[champion] = "Champion"

    return reached


# ── Monte Carlo runner ─────────────────────────────────────────────────────────

def run_wc_simulations(n_simulations: int = 10_000, seed: int = 42) -> Dict[str, dict]:
    """
    Run Monte Carlo simulations of the 2026 FIFA World Cup.

    Returns dict: team_name → {p_r32, p_r16, p_qf, p_sf, p_final, p_champion}
    """
    rng = np.random.default_rng(seed)

    print("Building canonical team ID map...")
    name_id, alias_map = _build_canonical_ids()

    print("Loading match data...")
    all_matches = _load_all_matches(alias_map)
    intl_matches = _load_international_matches(alias_map)

    print(f"  All matches: {len(all_matches):,}  |  International: {len(intl_matches):,}")

    print("Computing Elo ratings (all matches)...")
    elo = fit_ratings(all_matches)

    print("Computing Poisson strengths (international matches only)...")
    strengths, home_avg, away_avg = compute_team_strengths(intl_matches)
    avg_goals = (home_avg + away_avg) / 2
    print(f"  avg goals/game: {avg_goals:.2f}")

    all_teams = [t for g in WC_2026_GROUPS.values() for t in g]
    counts: Dict[str, Dict[str, int]] = {t: defaultdict(int) for t in all_teams}

    print(f"Running {n_simulations:,} simulations...")
    for i in range(n_simulations):
        result = _sim_tournament(name_id, strengths, avg_goals, elo, rng)
        for team, round_reached in result.items():
            counts[team][round_reached] += 1

    ROUND_ORDER = ["Group", "R32", "R16", "QF", "SF", "Final", "Champion"]

    summary = {}
    for team in all_teams:
        c = counts[team]
        total = sum(c.values())
        # p_X = probability of reaching AT LEAST round X
        cum = 0
        p = {}
        for rnd in reversed(ROUND_ORDER):
            cum += c.get(rnd, 0)
            p[f"p_{rnd.lower().replace(' ', '_')}"] = round(cum / n_simulations, 4)
        summary[team] = p

    return summary


def print_summary(summary: Dict[str, dict], focus_teams: Optional[List[str]] = None) -> None:
    """Print simulation results table sorted by champion probability."""
    focus = set(focus_teams or [])
    ordered = sorted(summary.keys(), key=lambda t: -summary[t]["p_champion"])

    header = f"{'Team':<30} {'Champion':>9} {'Final':>7} {'SF':>7} {'QF':>7} {'R16':>7} {'R32':>7}"
    print(header)
    print("-" * len(header))
    for team in ordered:
        s = summary[team]
        marker = " ◀" if team in focus else ""
        print(f"{team:<30} {s['p_champion']:>8.1%} {s['p_final']:>6.1%} "
              f"{s['p_sf']:>6.1%} {s['p_qf']:>6.1%} {s['p_r16']:>6.1%} "
              f"{s['p_r32']:>6.1%}{marker}")
