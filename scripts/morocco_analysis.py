"""
Morocco 2026 World Cup Analysis
Generates charts and prints article-ready data for Morocco's WC 2026 chances.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from src.db import get_connection
from src.wc_simulator import run_wc_simulations, print_summary

OUT = Path(__file__).resolve().parent.parent / "output"
OUT.mkdir(exist_ok=True)

MOROCCO_RED  = "#C1272D"
MOROCCO_GREEN = "#006233"
ACCENT_GOLD  = "#B8860B"
DARK_BG      = "#1a1a2e"
LIGHT_GREY   = "#e8e8e8"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "figure.facecolor": DARK_BG,
    "axes.facecolor": "#16213e",
    "axes.edgecolor": "#444",
    "axes.labelcolor": "white",
    "xtick.color": "white",
    "ytick.color": "white",
    "text.color": "white",
    "grid.color": "#333",
    "grid.alpha": 0.5,
})


# ── 1. RUN SIMULATIONS ────────────────────────────────────────────────────────

print("Running simulations (baseline + adjusted)...")
baseline = run_wc_simulations(n_simulations=10_000, seed=42)
adjusted = run_wc_simulations(
    n_simulations=10_000,
    seed=42,
    overrides={
        "Morocco": {
            "elo_boost":      40,
            "attack_factor":  1.18,
            "defense_factor": 0.92,
        }
    },
)


# ── 2. CHART 1: TOP CONTENDERS PROBABILITY ────────────────────────────────────

TOP_TEAMS = [
    "Morocco", "Norway", "Spain", "Ivory Coast", "England",
    "Netherlands", "Argentina", "Senegal", "Brazil", "France",
    "Belgium", "Switzerland", "Algeria",
]

fig, ax = plt.subplots(figsize=(12, 7))
fig.suptitle("2026 FIFA World Cup — Championship Probability\nBaseline vs Evidence-Adjusted",
             fontsize=14, fontweight="bold", color="white", y=0.98)

y = np.arange(len(TOP_TEAMS))
h = 0.35

base_vals = [baseline[t]["p_champion"] * 100 for t in TOP_TEAMS]
adj_vals  = [adjusted[t]["p_champion"]  * 100 for t in TOP_TEAMS]

colors_base = [MOROCCO_RED if t == "Morocco" else "#4a7fb5" for t in TOP_TEAMS]
colors_adj  = [MOROCCO_GREEN if t == "Morocco" else "#2ecc71" for t in TOP_TEAMS]

bars1 = ax.barh(y + h/2, base_vals, h, color=colors_base, alpha=0.75, label="Baseline")
bars2 = ax.barh(y - h/2, adj_vals,  h, color=colors_adj,  alpha=0.9,  label="Adjusted (player evidence)")

for bar, val in zip(bars2, adj_vals):
    ax.text(val + 0.1, bar.get_y() + bar.get_height()/2,
            f"{val:.1f}%", va="center", ha="left", fontsize=8.5, color="white")

ax.set_yticks(y)
ax.set_yticklabels(TOP_TEAMS, fontsize=10)
ax.set_xlabel("Championship Probability (%)", fontsize=10)
ax.axvline(x=0, color="#555", linewidth=0.8)
ax.set_xlim(0, 14)
ax.grid(axis="x", alpha=0.3)
ax.legend(loc="lower right", fontsize=9)

# Highlight Morocco row
ax.axhspan(len(TOP_TEAMS)-1 - 0, len(TOP_TEAMS)-1 + 0.5, alpha=0.06, color=MOROCCO_RED)

plt.tight_layout()
plt.savefig(OUT / "1_championship_probability.png", dpi=150, bbox_inches="tight")
plt.close()
print("  ✓ Chart 1: championship probability")


# ── 3. CHART 2: KEY MOROCCAN PLAYERS — CLUB STATS 2024-25 ────────────────────

with get_connection() as conn:
    rows = conn.execute("""
        SELECT player_name, club, league, apps, goals, assists, xg, rating
        FROM player_season_stats
        WHERE player_name IN (
            'Ismael Saibari','Achraf Hakimi Mouh','Ayoub El Kaabi',
            'Youssef En-Nesyri','Brahim Díaz','Ayyoub Bouaddi',
            'Sofyan Amrabat','Noussair Mazraoui','Nayef Aguerd',
            'Abdessamad Ezzalzouli'
        )
        ORDER BY rating DESC
    """).fetchall()

players_data = [dict(r) for r in rows]

names   = [r["player_name"].split()[0] + " " + r["player_name"].split()[1]
           if len(r["player_name"].split()) >= 2 else r["player_name"]
           for r in players_data]
# Friendly short names
friendly = {
    "Achraf Hakimi": "Hakimi", "Ismael Saibari": "Saibari",
    "Ayoub El": "El Kaabi", "Youssef En-Nesyri": "En-Nesyri",
    "Brahim Díaz": "Brahim Díaz", "Ayyoub Bouaddi": "Bouaddi",
    "Sofyan Amrabat": "Amrabat", "Noussair Mazraoui": "Mazraoui",
    "Nayef Aguerd": "Aguerd", "Abdessamad Ezzalzouli": "Ezzalzouli",
}
labels = []
for r in players_data:
    n = r["player_name"]
    first_two = " ".join(n.split()[:2])
    labels.append(friendly.get(first_two, first_two))

goals   = [r["goals"] or 0 for r in players_data]
assists = [r["assists"] or 0 for r in players_data]
ratings = [r["rating"] or 0 for r in players_data]

x = np.arange(len(labels))
w = 0.3

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9))
fig.suptitle("Key Moroccan Players — 2024-25 Club Season Statistics",
             fontsize=13, fontweight="bold", color="white")

ax1.bar(x - w/2, goals,   w, color=MOROCCO_RED,   label="Goals",   alpha=0.85)
ax1.bar(x + w/2, assists, w, color=MOROCCO_GREEN,  label="Assists", alpha=0.85)
for i, (g, a) in enumerate(zip(goals, assists)):
    ax1.text(i - w/2, g + 0.2, str(g), ha="center", va="bottom", fontsize=8, color="white")
    ax1.text(i + w/2, a + 0.2, str(a), ha="center", va="bottom", fontsize=8, color="white")
ax1.set_xticks(x); ax1.set_xticklabels(labels, rotation=15, ha="right", fontsize=9)
ax1.set_ylabel("Count"); ax1.legend(fontsize=9); ax1.grid(axis="y", alpha=0.3)
ax1.set_title("Goals & Assists", fontsize=10, color=LIGHT_GREY)

colors_r = [MOROCCO_RED if r >= 7.4 else (ACCENT_GOLD if r >= 7.1 else "#4a7fb5")
            for r in ratings]
bars = ax2.bar(x, ratings, color=colors_r, alpha=0.85, zorder=3)
ax2.axhline(y=7.0, color="#aaa", linestyle="--", linewidth=0.8, label="7.0 threshold")
for bar, val in zip(bars, ratings):
    ax2.text(bar.get_x() + bar.get_width()/2, val + 0.01,
             f"{val:.2f}", ha="center", va="bottom", fontsize=8, color="white")
ax2.set_xticks(x); ax2.set_xticklabels(labels, rotation=15, ha="right", fontsize=9)
ax2.set_ylabel("Sofascore Rating"); ax2.set_ylim(6.3, 8.0)
ax2.grid(axis="y", alpha=0.3, zorder=0)
ax2.set_title("Sofascore Rating (red = elite ≥7.4, gold = very good ≥7.1)", fontsize=10, color=LIGHT_GREY)

elite_patch = mpatches.Patch(color=MOROCCO_RED,  label="Elite (≥7.4)")
good_patch  = mpatches.Patch(color=ACCENT_GOLD,  label="Very good (≥7.1)")
ok_patch    = mpatches.Patch(color="#4a7fb5",    label="Good")
ax2.legend(handles=[elite_patch, good_patch, ok_patch], fontsize=8, loc="lower right")

plt.tight_layout()
plt.savefig(OUT / "2_player_club_stats.png", dpi=150, bbox_inches="tight")
plt.close()
print("  ✓ Chart 2: player club stats")


# ── 4. CHART 3: MOROCCO TOURNAMENT JOURNEY ───────────────────────────────────

tournaments = [
    ("WC 2022\n(Qatar)", 6, 1, "Semi-final"),
    ("AFCON 2023\n(Ivory Coast)", 4, 2, "Group Stage"),
    ("WC 2026 Qual.\n(CAF)", 10, 2, "Qualified"),
    ("AFCON 2025\n(Morocco)", 9, 1, "Semi-final+"),
    ("U20 WC 2025\n(Chile)", 14, 6, "Champions"),
]

labels_t   = [t[0] for t in tournaments]
goals_for  = [t[1] for t in tournaments]
goals_ag   = [t[2] for t in tournaments]
outcomes   = [t[3] for t in tournaments]
x = np.arange(len(labels_t))
w = 0.35

fig, ax = plt.subplots(figsize=(12, 6))
fig.suptitle("Morocco — Tournament Performance Trajectory (2022-2025)",
             fontsize=13, fontweight="bold", color="white")

b1 = ax.bar(x - w/2, goals_for, w, color=MOROCCO_GREEN, label="Goals For",  alpha=0.85)
b2 = ax.bar(x + w/2, goals_ag,  w, color=MOROCCO_RED,   label="Goals Against", alpha=0.75)

for bar, val in zip(b1, goals_for):
    ax.text(bar.get_x() + bar.get_width()/2, val + 0.1, str(val),
            ha="center", va="bottom", fontsize=9, color="white", fontweight="bold")
for bar, val in zip(b2, goals_ag):
    ax.text(bar.get_x() + bar.get_width()/2, val + 0.1, str(val),
            ha="center", va="bottom", fontsize=9, color="white")

for i, outcome in enumerate(outcomes):
    ax.text(i, max(goals_for[i], goals_ag[i]) + 0.6, outcome,
            ha="center", va="bottom", fontsize=7.5, color=ACCENT_GOLD, fontstyle="italic")

ax.set_xticks(x); ax.set_xticklabels(labels_t, fontsize=9)
ax.set_ylabel("Goals"); ax.legend(fontsize=9)
ax.grid(axis="y", alpha=0.3)
ax.set_ylim(0, 18)
ax.annotate("★ U20 Champions", xy=(4, 14), xytext=(3.2, 16),
            color=ACCENT_GOLD, fontsize=9, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=ACCENT_GOLD))

plt.tight_layout()
plt.savefig(OUT / "3_tournament_journey.png", dpi=150, bbox_inches="tight")
plt.close()
print("  ✓ Chart 3: tournament journey")


# ── 5. CHART 4: U20 WC 2025 MATCH-BY-MATCH ───────────────────────────────────

u20_matches = [
    ("South Korea", 2, 1, "Group"),
    ("Spain",       2, 0, "Group"),
    ("Mexico",      0, 1, "Group"),
    ("USA",         3, 1, "R16"),
    ("Brazil",      2, 1, "QF"),
    ("France",      1, 1, "SF (pens)"),
    ("Argentina",   2, 0, "Final"),
]

fig, ax = plt.subplots(figsize=(12, 5))
fig.suptitle("Morocco U20 — FIFA U20 World Cup 2025: Match-by-Match",
             fontsize=13, fontweight="bold", color="white")

for_goals = [m[1] for m in u20_matches]
ag_goals  = [m[2] for m in u20_matches]
opponents = [f"{m[0]}\n({m[3]})" for m in u20_matches]
x = np.arange(len(u20_matches))
w = 0.35

bar_col = []
for m in u20_matches:
    if m[1] > m[2]: bar_col.append(MOROCCO_GREEN)
    elif m[1] < m[2]: bar_col.append(MOROCCO_RED)
    else: bar_col.append(ACCENT_GOLD)

b1 = ax.bar(x - w/2, for_goals, w, color=bar_col, alpha=0.9, label="Morocco Goals")
b2 = ax.bar(x + w/2, ag_goals,  w, color="#555",   alpha=0.75, label="Opponent Goals")

for bar, val in zip(b1, for_goals):
    ax.text(bar.get_x() + bar.get_width()/2, val + 0.05, str(val),
            ha="center", va="bottom", fontsize=10, color="white", fontweight="bold")
for bar, val in zip(b2, ag_goals):
    ax.text(bar.get_x() + bar.get_width()/2, val + 0.05, str(val),
            ha="center", va="bottom", fontsize=10, color="#ccc")

# Final marker
ax.annotate("CHAMPIONS", xy=(6, 2), xytext=(5.2, 3.2),
            color=ACCENT_GOLD, fontsize=10, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=ACCENT_GOLD))

ax.set_xticks(x); ax.set_xticklabels(opponents, fontsize=9)
ax.set_ylabel("Goals"); ax.set_ylim(0, 4.5)
ax.legend(fontsize=9)
ax.grid(axis="y", alpha=0.3)

won_patch  = mpatches.Patch(color=MOROCCO_GREEN, label="Win")
draw_patch = mpatches.Patch(color=ACCENT_GOLD,   label="Draw (adv. pens)")
loss_patch = mpatches.Patch(color=MOROCCO_RED,   label="Loss")
ax.legend(handles=[won_patch, draw_patch, loss_patch], fontsize=9, loc="upper left")

plt.tight_layout()
plt.savefig(OUT / "4_u20_wc_journey.png", dpi=150, bbox_inches="tight")
plt.close()
print("  ✓ Chart 4: U20 WC 2025 journey")


# ── 6. CHART 5: KNOCKOUT STAGE PROBABILITIES COMPARISON ──────────────────────

stage_teams = {
    "Morocco": adjusted,
    "Spain":   adjusted,
    "Argentina": adjusted,
    "Brazil":  adjusted,
    "France":  adjusted,
    "England": adjusted,
}
stages = ["p_r16", "p_qf", "p_sf", "p_final", "p_champion"]
stage_labels = ["Round of 16", "Quarter-final", "Semi-final", "Final", "Champion"]

fig, ax = plt.subplots(figsize=(12, 6))
fig.suptitle("2026 World Cup Knockout Probability by Round — Evidence-Adjusted",
             fontsize=13, fontweight="bold", color="white")

team_colors = {
    "Morocco":   MOROCCO_RED,
    "Spain":     "#FFDD00",
    "Argentina": "#74ACDF",
    "Brazil":    "#009C3B",
    "France":    "#0055A4",
    "England":   "#CF081F",
}

x = np.arange(len(stages))
for team, sim in stage_teams.items():
    vals = [sim[team][s] * 100 for s in stages]
    lw = 3 if team == "Morocco" else 1.5
    ls = "-" if team == "Morocco" else "--"
    ax.plot(x, vals, marker="o", linewidth=lw, linestyle=ls,
            color=team_colors[team], label=team, markersize=5 if team == "Morocco" else 4)

ax.set_xticks(x)
ax.set_xticklabels(stage_labels, fontsize=10)
ax.set_ylabel("Probability (%)")
ax.legend(fontsize=9, loc="upper right")
ax.grid(alpha=0.3)
ax.set_ylim(0, 75)

plt.tight_layout()
plt.savefig(OUT / "5_knockout_probability_comparison.png", dpi=150, bbox_inches="tight")
plt.close()
print("  ✓ Chart 5: knockout stage comparison")


# ── 7. PRINT ARTICLE DATA SUMMARY ─────────────────────────────────────────────

print()
print("=" * 70)
print("ARTICLE DATA SUMMARY")
print("=" * 70)

print("""
SIMULATION PARAMETERS
─────────────────────
Model:      Monte Carlo, 10,000 iterations
Group stage: Dixon-Coles Poisson (international matches only, avg 1.37 G/game)
Knockout:    Elo win probabilities (all 4,213 matches, neutral venue)
Overrides applied to Morocco:
  • Elo boost:      +40 points
  • Attack factor:  ×1.18
  • Defense factor: ×0.92
""")

print("TOP 10 CONTENDERS — ADJUSTED PROBABILITIES")
print(f"{'Team':<22} {'Champion':>9} {'Final':>7} {'SF':>6} {'QF':>6}")
print("-" * 50)
top10 = sorted(adjusted.keys(), key=lambda t: -adjusted[t]["p_champion"])[:10]
for t in top10:
    s = adjusted[t]
    marker = " ◀" if t == "Morocco" else ""
    print(f"{t:<22} {s['p_champion']:>8.1%} {s['p_final']:>6.1%} {s['p_sf']:>5.1%} {s['p_qf']:>5.1%}{marker}")

print("""
SAIBARI — AFCON 2025 STANDOUT STATS
─────────────────────────────────────
Appearances: 7  |  Goals: 1  |  Minutes: 592
Tackles: 15  |  Interceptions: 4  |  Dribbles: 5
Pass accuracy: 85%  |  Rating: 7.01
Also: 3 goals in 6 WC Qualifier appearances for Morocco

SAIBARI — 2024-25 CLUB (PSV Eindhoven, Eredivisie)
─────────────────────────────────────────────────────
Apps: 29  |  Goals: 11  |  Assists: 11  |  Rating: 7.43
Awarded: Eredivisie Player of the Year 2024-25

U20 STANDOUTS — MAAMA & ZABIRI
────────────────────────────────
Othmane Maama (Watford, on loan via Ligue 1):
  12 apps, 1G, 1A, Rating: 6.40 — developing U23 role player
Yassir Zabiri (Stade Rennais):
  5 apps, 1G, 0A, Rating: 6.73 — young rotation forward

NOTE: Neither Maama nor Zabiri was among the senior squad's
primary contributors in 2024-25 club football; they are
high-ceiling prospects. Their U20 WC performance was
achieved in a team context that also included the final
group cohort (Bouaddi played AGAINST Morocco for France U20).

KEY NARRATIVE CORRECTION
─────────────────────────
• Ayyoub Bouaddi (Lille, 0G/1A in 24 apps) played for
  France U20 at the tournament — Morocco beat him in the
  semi-final (1-1, Morocco adv. pens). He switched
  allegiance to Morocco AFTER the tournament ended.
  This makes Morocco's U20 WC win entirely independent of
  Bouaddi's addition to the senior squad.
""")

print("Charts saved to: output/")
