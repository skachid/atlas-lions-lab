"""Generate post-match charts for a completed Morocco match.

Usage:
    python scripts/post_match_charts.py <match_id>

Reads only from the local DB — no API calls. Run post_match_ingest.py first.

Outputs to output/match_<match_id>/:
  1. player_ratings.png   — Moroccan player rating bar chart (tier colour-coded)
  2. shot_map.png         — Shot breakdown by outcome on a pitch schematic
  3. team_stats.png       — Key stats comparison (Morocco vs opponent)
  4. player_heatmap.png   — Moroccan players × key stats matrix
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from src.db import get_connection

# ── Colour palette (matches existing charts) ──────────────────────────────────
MOROCCO_RED   = "#C1272D"
MOROCCO_GREEN = "#006233"
ACCENT_GOLD   = "#B8860B"
DARK_BG       = "#1a1a2e"
PANEL_BG      = "#16213e"
GRID_COL      = "#333"
TEXT_COL      = "white"

OPPONENT_COL  = "#4a90d9"   # blue used for the opposing team
DIM_COL       = "#7a8899"   # muted colour for zero/null values

# Rating tier colours (matches chart 2 in morocco_analysis.py)
TIER_ELITE    = MOROCCO_RED      # >= 8.0
TIER_GOOD     = ACCENT_GOLD      # >= 7.0
TIER_AVERAGE  = OPPONENT_COL     # < 7.0
TIER_NONE     = DIM_COL          # no rating

PITCH_GREEN   = "#1a5c2e"
PITCH_LINE    = "white"

plt.rcParams.update({
    "font.family":      "DejaVu Sans",
    "figure.facecolor": DARK_BG,
    "axes.facecolor":   PANEL_BG,
    "axes.edgecolor":   "#444",
    "axes.labelcolor":  TEXT_COL,
    "xtick.color":      TEXT_COL,
    "ytick.color":      TEXT_COL,
    "text.color":       TEXT_COL,
    "grid.color":       GRID_COL,
    "grid.alpha":       0.4,
})


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_match(match_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT m.match_id, m.match_date, m.competition_stage,
                   c.competition_name,
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


def _team_stat(match_id: int, team_id: int, stat_name: str) -> float | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT stat_value FROM match_stats WHERE match_id=? AND team_id=? AND stat_name=?",
            (match_id, team_id, stat_name),
        ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return float(str(row[0]).replace("%", "").strip())
    except ValueError:
        return None


def _morocco_players(match_id: int, morocco_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.player_name,
                   COALESCE(l.position, pms.position) AS pos,
                   pms.is_starter,
                   pms.minutes_played,
                   pms.goals,
                   pms.assists,
                   pms.key_passes,
                   pms.dribbles_completed,
                   COALESCE(pms.fouls_committed, 0) AS fouls_committed,
                   COALESCE(pms.yellow_cards,    0) AS yellow_cards,
                   pms.shots,
                   pms.shots_on_target,
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


def _events(match_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.event_type, e.detail, e.team_id,
                   p.player_name
            FROM match_events e
            LEFT JOIN players p ON p.player_id = e.player_id
            WHERE e.match_id = ?
            """,
            (match_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Pitch drawing ─────────────────────────────────────────────────────────────

def _draw_pitch(ax: plt.Axes) -> None:
    """Draw a full football pitch schematic on ax (110×75 units)."""
    PL, PW = 110.0, 75.0    # pitch length, width

    ax.set_facecolor(PITCH_GREEN)
    ax.set_xlim(-4, PL + 4)
    ax.set_ylim(-4, PW + 4)
    ax.set_aspect("equal")
    ax.axis("off")

    lw = 1.5

    def rect(x, y, w, h, **kw):
        ax.add_patch(mpatches.Rectangle((x, y), w, h, fill=False,
                                         edgecolor=PITCH_LINE, linewidth=lw, **kw))

    # Outer boundary
    rect(0, 0, PL, PW)

    # Centre line & circle
    ax.axvline(PL / 2, color=PITCH_LINE, linewidth=lw)
    ax.add_patch(mpatches.Circle((PL / 2, PW / 2), 9.15,
                                  fill=False, edgecolor=PITCH_LINE, linewidth=lw))
    ax.plot(PL / 2, PW / 2, "o", color=PITCH_LINE, markersize=3)

    # Penalty boxes (16.5m deep, 40.32m wide)
    pbox_y = (PW - 40.32) / 2
    rect(0,       pbox_y, 16.5, 40.32)   # left
    rect(PL-16.5, pbox_y, 16.5, 40.32)   # right

    # Six-yard boxes (5.5m deep, 18.32m wide)
    sbox_y = (PW - 18.32) / 2
    rect(0,      sbox_y, 5.5, 18.32)
    rect(PL-5.5, sbox_y, 5.5, 18.32)

    # Penalty spots
    ax.plot(11, PW / 2, "o", color=PITCH_LINE, markersize=3)
    ax.plot(PL - 11, PW / 2, "o", color=PITCH_LINE, markersize=3)

    # Penalty arcs (only the portion outside the box)
    for cx, t1, t2 in ((11, -50, 50), (PL - 11, 130, 230)):
        theta = np.linspace(np.radians(t1), np.radians(t2), 60)
        ax.plot(cx + 9.15 * np.cos(theta), PW / 2 + 9.15 * np.sin(theta),
                color=PITCH_LINE, linewidth=lw)

    # Goals (7.32m wide, 2m deep)
    goal_y = (PW - 7.32) / 2
    rect(-2, goal_y, 2, 7.32)
    rect(PL, goal_y, 2, 7.32)

    # Corner arcs
    for cx, cy, a1, a2 in (
        (0,  0,  0,  90),
        (0,  PW, -90, 0),
        (PL, 0,  90, 180),
        (PL, PW, 180, 270),
    ):
        theta = np.linspace(np.radians(a1), np.radians(a2), 20)
        ax.plot(cx + 1 * np.cos(theta), cy + 1 * np.sin(theta),
                color=PITCH_LINE, linewidth=lw)


# ── Shared save helper ────────────────────────────────────────────────────────

def _save(fig: plt.Figure, path: Path, label: str) -> None:
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {label} → {path.name}")


# ── Chart 1: Player ratings ───────────────────────────────────────────────────

def chart_player_ratings(
    match_id: int, morocco_id: int, players: list[dict],
    match_label: str, out_dir: Path,
) -> None:
    if not players:
        print("  Skipping player_ratings.png — no player data")
        return

    # Sort: rated players first (descending), then unrated by minutes
    rated   = [p for p in players if p["rating"] is not None]
    unrated = [p for p in players if p["rating"] is None]
    sorted_players = sorted(rated, key=lambda p: p["rating"]) + unrated[::-1]

    names   = [(p["player_name"] or "")[:22] for p in sorted_players]
    ratings = [p["rating"] if p["rating"] is not None else 0.0 for p in sorted_players]
    colours = []
    for p in sorted_players:
        r = p["rating"]
        if r is None:
            colours.append(TIER_NONE)
        elif r >= 8.0:
            colours.append(TIER_ELITE)
        elif r >= 7.0:
            colours.append(TIER_GOOD)
        else:
            colours.append(TIER_AVERAGE)

    fig, ax = plt.subplots(figsize=(11, max(5, len(names) * 0.45 + 1.5)))
    fig.suptitle(
        f"Moroccan Player Ratings — {match_label}",
        fontsize=12, fontweight="bold", color=TEXT_COL, y=1.01,
    )

    y_pos = np.arange(len(names))
    bars  = ax.barh(y_pos, ratings, color=colours, edgecolor="#222", linewidth=0.6)

    # Value labels
    for bar, p in zip(bars, sorted_players):
        w = bar.get_width()
        lbl = f"{w:.2f}" if p["rating"] is not None else "no rating"
        ax.text(
            w + 0.05, bar.get_y() + bar.get_height() / 2,
            lbl, va="center", ha="left", fontsize=8.5, color=TEXT_COL,
        )

    ax.set_yticks(y_pos)
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Sofascore Rating", fontsize=10)
    ax.set_xlim(0, 10.5)
    ax.set_title("(* = substitute)", fontsize=8, color=DIM_COL, pad=4)
    ax.xaxis.grid(True, alpha=0.35)
    ax.set_axisbelow(True)

    # Mark substitute names with *
    for i, p in enumerate(sorted_players):
        if not p["is_starter"]:
            ax.get_yticklabels()[i].set_text(names[i] + " *")
            ax.get_yticklabels()[i].set_color(DIM_COL)

    # Legend
    legend_patches = [
        mpatches.Patch(color=TIER_ELITE,   label="Elite  ≥ 8.0"),
        mpatches.Patch(color=TIER_GOOD,    label="Good   ≥ 7.0"),
        mpatches.Patch(color=TIER_AVERAGE, label="Average < 7.0"),
        mpatches.Patch(color=TIER_NONE,    label="No rating"),
    ]
    ax.legend(handles=legend_patches, loc="lower right", fontsize=8,
              framealpha=0.3, facecolor=PANEL_BG, edgecolor="#444")

    fig.tight_layout()
    _save(fig, out_dir / "player_ratings.png", "player_ratings.png")


# ── Chart 2: Shot map ─────────────────────────────────────────────────────────

def chart_shot_map(
    match_id: int, home_id: int, away_id: int,
    home_name: str, away_name: str,
    home_score: int, away_score: int,
    match_label: str, out_dir: Path,
) -> None:
    """
    Pitch schematic with shot-outcome totals for each team.
    Uses aggregate counts (no per-shot positions available from API-Football).
    """
    def _stat(tid, key):
        v = _team_stat(match_id, tid, key)
        return int(v) if v is not None else 0

    # Gather shot totals
    h_total   = _stat(home_id, "Total Shots")
    h_on      = _stat(home_id, "Shots on Goal")
    h_off     = _stat(home_id, "Shots off Goal")
    h_blocked = _stat(home_id, "Blocked Shots")
    h_goals   = home_score or 0

    a_total   = _stat(away_id, "Total Shots")
    a_on      = _stat(away_id, "Shots on Goal")
    a_off     = _stat(away_id, "Shots off Goal")
    a_blocked = _stat(away_id, "Blocked Shots")
    a_goals   = away_score or 0

    fig, ax = plt.subplots(figsize=(13, 7))
    fig.patch.set_facecolor(DARK_BG)
    fig.suptitle(
        f"Shot Map — {match_label}\n"
        f"(aggregate counts; per-shot positions not available from API-Football)",
        fontsize=11, fontweight="bold", color=TEXT_COL, y=1.00,
    )

    _draw_pitch(ax)

    PL, PW = 110.0, 75.0

    # For each team, place shot-type indicators in their attacking half.
    # Home team attacks toward x=PL (right goal), away team toward x=0 (left goal).
    shot_configs = [
        # (team, cx_base, dx_direction, colour, name, counts)
        (
            "home", PL - 8, -1, MOROCCO_RED, home_name,
            {"Goals": h_goals, "On Target": h_on - h_goals, "Off Target": h_off, "Blocked": h_blocked},
        ),
        (
            "away", 8, +1, OPPONENT_COL, away_name,
            {"Goals": a_goals, "On Target": a_on - a_goals, "Off Target": a_off, "Blocked": a_blocked},
        ),
    ]

    category_colours = {
        "Goals":      ACCENT_GOLD,
        "On Target":  "#4cbb6a",
        "Off Target": DIM_COL,
        "Blocked":    "#e07b39",
    }

    # Vertical positions for the four categories (spread around penalty-spot height)
    cat_y_offsets = {"Goals": 10, "On Target": 4, "Off Target": -4, "Blocked": -10}

    for side, cx_base, dx, team_col, team_name, counts in shot_configs:
        # Team label above
        ax.text(
            cx_base, PW / 2 + 20, team_name,
            ha="center", va="center", fontsize=10, fontweight="bold",
            color=team_col,
            bbox=dict(boxstyle="round,pad=0.3", facecolor=DARK_BG, edgecolor=team_col, alpha=0.8),
        )

        for cat, count in counts.items():
            cy    = PW / 2 + cat_y_offsets[cat]
            ccat  = category_colours[cat]
            radius = max(1.5, min(4.5, 1.5 + count * 0.4))

            circle = mpatches.Circle(
                (cx_base, cy), radius, color=ccat,
                alpha=0.85, zorder=5,
            )
            ax.add_patch(circle)
            ax.text(
                cx_base, cy, str(count),
                ha="center", va="center", fontsize=9, fontweight="bold",
                color="black" if cat == "Goals" else TEXT_COL, zorder=6,
            )
            # Category label
            label_x = cx_base + dx * (radius + 2.5)
            ax.text(
                label_x, cy, cat,
                ha="left" if dx > 0 else "right",
                va="center", fontsize=7.5, color=ccat, zorder=6,
            )

    # Totals strip at the bottom of the pitch
    ax.text(
        PL * 0.25, -2.5,
        f"{home_name}: {h_total} shots  ({h_goals} goals)",
        ha="center", va="top", fontsize=9, color=MOROCCO_RED,
    )
    ax.text(
        PL * 0.75, -2.5,
        f"{away_name}: {a_total} shots  ({a_goals} goals)",
        ha="center", va="top", fontsize=9, color=OPPONENT_COL,
    )

    # Legend
    legend_patches = [mpatches.Patch(color=c, label=k) for k, c in category_colours.items()]
    ax.legend(
        handles=legend_patches, loc="upper center", ncol=4,
        bbox_to_anchor=(0.5, -0.04), fontsize=8,
        framealpha=0.35, facecolor=DARK_BG, edgecolor="#444",
    )

    fig.tight_layout()
    _save(fig, out_dir / "shot_map.png", "shot_map.png")


# ── Chart 3: Key stats comparison ─────────────────────────────────────────────

def chart_team_stats(
    match_id: int, home_id: int, away_id: int,
    home_name: str, away_name: str,
    match_label: str, out_dir: Path,
) -> None:
    stat_specs = [
        ("Ball Possession",  "Possession (%)",       1.0),
        ("Total Shots",      "Total Shots",           1.0),
        ("Shots on Goal",    "Shots on Target",       1.0),
        ("expected_goals",   "xG",                    1.0),
        ("Passes accurate",  "Passes Completed",      1.0),
        ("Passes %",         "Pass Accuracy (%)",     1.0),
        ("Fouls",            "Fouls",                 1.0),
        ("Corner Kicks",     "Corners",               1.0),
        ("Offsides",         "Offsides",              1.0),
    ]

    labels, h_vals, a_vals = [], [], []
    for api_key, label, _ in stat_specs:
        hv = _team_stat(match_id, home_id, api_key)
        av = _team_stat(match_id, away_id, api_key)
        if hv is not None or av is not None:
            labels.append(label)
            h_vals.append(hv or 0.0)
            a_vals.append(av or 0.0)

    if not labels:
        print("  Skipping team_stats.png — no team stat data")
        return

    # Identify which side is Morocco
    is_home_morocco = home_name.strip().lower() == "morocco"
    left_name   = home_name
    right_name  = away_name
    left_vals   = h_vals
    right_vals  = a_vals
    left_col    = MOROCCO_RED    if is_home_morocco else OPPONENT_COL
    right_col   = OPPONENT_COL  if is_home_morocco else MOROCCO_RED

    n   = len(labels)
    y   = np.arange(n)
    h   = 0.35

    fig, ax = plt.subplots(figsize=(12, max(5, n * 0.6 + 2)))
    fig.suptitle(
        f"Key Stats — {match_label}",
        fontsize=12, fontweight="bold", color=TEXT_COL, y=1.01,
    )

    b1 = ax.barh(y + h / 2, left_vals,  h, color=left_col,  alpha=0.88, label=left_name)
    b2 = ax.barh(y - h / 2, right_vals, h, color=right_col, alpha=0.88, label=right_name)

    for bar in b1:
        w = bar.get_width()
        if w > 0:
            ax.text(w + 0.3, bar.get_y() + bar.get_height() / 2,
                    f"{w:.1f}".rstrip("0").rstrip("."),
                    va="center", ha="left", fontsize=8, color=TEXT_COL)

    for bar in b2:
        w = bar.get_width()
        if w > 0:
            ax.text(w + 0.3, bar.get_y() + bar.get_height() / 2,
                    f"{w:.1f}".rstrip("0").rstrip("."),
                    va="center", ha="left", fontsize=8, color=TEXT_COL)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.xaxis.grid(True, alpha=0.35)
    ax.set_axisbelow(True)
    ax.legend(fontsize=9, loc="lower right", framealpha=0.3,
               facecolor=PANEL_BG, edgecolor="#444")

    fig.tight_layout()
    _save(fig, out_dir / "team_stats.png", "team_stats.png")


# ── Chart 4: Player stats heatmap ─────────────────────────────────────────────

def chart_player_heatmap(
    match_id: int, morocco_id: int, players: list[dict],
    match_label: str, out_dir: Path,
) -> None:
    if not players:
        print("  Skipping player_heatmap.png — no player data")
        return

    # Columns to show and their extract keys
    col_specs = [
        ("Min",    "minutes_played"),
        ("Goals",  "goals"),
        ("Assists","assists"),
        ("KeyP",   "key_passes"),
        ("Drb",    "dribbles_completed"),
        ("Shots",  "shots"),
        ("SoT",    "shots_on_target"),
        ("FC",     "fouls_committed"),
        ("YC",     "yellow_cards"),
        ("Rating", "rating"),
    ]

    # Sort players by rating desc, then minutes
    sorted_players = sorted(
        players,
        key=lambda p: (p["rating"] if p["rating"] is not None else -1, p["minutes_played"] or 0),
        reverse=True,
    )

    col_labels = [c[0] for c in col_specs]
    row_labels  = [(p["player_name"] or "")[:22] for p in sorted_players]

    # Build raw data matrix
    raw = np.zeros((len(sorted_players), len(col_specs)))
    for r, p in enumerate(sorted_players):
        for c, (_, key) in enumerate(col_specs):
            v = p.get(key)
            raw[r, c] = float(v) if v is not None else 0.0

    # Normalise each column independently (0→1) for colouring
    norm = np.zeros_like(raw)
    for c in range(raw.shape[1]):
        col_max = raw[:, c].max()
        if col_max > 0:
            norm[:, c] = raw[:, c] / col_max

    # Use a custom colormap: dark navy → Morocco red
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list(
        "atlas", [PANEL_BG, "#8B0000", MOROCCO_RED, ACCENT_GOLD], N=256
    )

    fig_h = max(5, len(sorted_players) * 0.55 + 2.5)
    fig_w = max(9, len(col_labels) * 0.9 + 3.5)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.suptitle(
        f"Player Stats Heatmap — {match_label}",
        fontsize=11, fontweight="bold", color=TEXT_COL, y=1.01,
    )

    im = ax.imshow(norm, aspect="auto", cmap=cmap, vmin=0, vmax=1)

    # Cell annotations (actual values, not normalised)
    for r in range(raw.shape[0]):
        for c in range(raw.shape[1]):
            val = raw[r, c]
            if col_specs[c][0] == "Rating":
                txt = f"{val:.2f}" if val > 0 else "—"
            elif col_specs[c][0] == "Min":
                txt = str(int(val)) if val > 0 else "0"
            else:
                txt = str(int(val))
            brightness = norm[r, c]
            txt_color  = "black" if brightness > 0.65 else TEXT_COL
            ax.text(c, r, txt, ha="center", va="center",
                    fontsize=8.5, color=txt_color, fontweight="bold")

    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, fontsize=9, rotation=30, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=9)
    ax.tick_params(length=0)

    # Mark substitutes
    for i, p in enumerate(sorted_players):
        if not p["is_starter"]:
            ax.get_yticklabels()[i].set_color(DIM_COL)

    # Thin grid lines between cells
    ax.set_xticks(np.arange(-0.5, len(col_labels), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(sorted_players), 1), minor=True)
    ax.grid(which="minor", color="#2a3a4a", linewidth=0.8)
    ax.tick_params(which="minor", bottom=False, left=False)

    fig.tight_layout()
    _save(fig, out_dir / "player_heatmap.png", "player_heatmap.png")


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_charts(match_id: int) -> None:
    match    = _get_match(match_id)
    home_id  = match["home_id"]
    away_id  = match["away_id"]
    home_nm  = match["home_name"]
    away_nm  = match["away_name"]

    # Identify Morocco
    morocco_id  = None
    for tid, tname in ((home_id, home_nm), (away_id, away_nm)):
        if tname.strip().lower() == "morocco":
            morocco_id = tid

    match_label = (
        f"{home_nm} {match['home_score']}–{match['away_score']} {away_nm} "
        f"| {match['match_date']}"
    )

    out_dir = Path(__file__).resolve().parent.parent / "output" / f"match_{match_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Charts for match {match_id} → {out_dir} ===\n")

    players = _morocco_players(match_id, morocco_id) if morocco_id else []

    chart_player_ratings(match_id, morocco_id, players, match_label, out_dir)
    chart_shot_map(
        match_id, home_id, away_id, home_nm, away_nm,
        match["home_score"], match["away_score"], match_label, out_dir,
    )
    chart_team_stats(match_id, home_id, away_id, home_nm, away_nm, match_label, out_dir)
    chart_player_heatmap(match_id, morocco_id, players, match_label, out_dir)

    print(f"\nAll charts saved to {out_dir}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate post-match charts (DB-only, no API calls)"
    )
    parser.add_argument("match_id", type=int, help="API-Football fixture ID")
    args = parser.parse_args()
    generate_charts(args.match_id)
