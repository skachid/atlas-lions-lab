"""Pulls data from StatsBomb open-data and inserts into SQLite."""
import pandas as pd
from statsbombpy import sb
from src.db import get_connection, init_schema


def ingest_competitions() -> pd.DataFrame:
    print("Fetching competitions list...")
    comps = sb.competitions()
    cols = ["competition_id", "season_id", "country_name", "competition_name", "season_name"]
    comps_subset = comps[cols]
    with get_connection() as conn:
        # Staging-table pattern preserves the real competitions table's
        # primary key, which the matches foreign key depends on.
        comps_subset.to_sql("_competitions_staging", conn, if_exists="replace", index=False)
        conn.execute(
            "INSERT OR IGNORE INTO competitions "
            "(competition_id, season_id, country_name, competition_name, season_name) "
            "SELECT competition_id, season_id, country_name, competition_name, season_name "
            "FROM _competitions_staging"
        )
        conn.execute("DROP TABLE _competitions_staging")
    print(f"  Loaded {len(comps_subset)} competition+season pairs.")
    return comps_subset


def ingest_matches_for(competition_id: int, season_id: int) -> pd.DataFrame:
    matches = sb.matches(competition_id=competition_id, season_id=season_id)
    matches_clean = pd.DataFrame({
        "match_id": matches["match_id"],
        "competition_id": competition_id,
        "season_id": season_id,
        "match_date": matches["match_date"],
        "kick_off": matches.get("kick_off"),
        "home_team_id": matches["home_team_id"] if "home_team_id" in matches.columns else matches["home_team"].apply(_extract_team_id),
        "away_team_id": matches["away_team_id"] if "away_team_id" in matches.columns else matches["away_team"].apply(_extract_team_id),
        "home_score": matches["home_score"],
        "away_score": matches["away_score"],
        "stadium": matches.get("stadium"),
        "referee": matches.get("referee"),
        "competition_stage": matches.get("competition_stage"),
    })
    teams_rows = []
    for _, row in matches.iterrows():
        for side in ("home_team", "away_team"):
            team_field = row.get(side)
            if isinstance(team_field, dict):
                teams_rows.append({
                    "team_id": team_field.get(f"{side}_id") or team_field.get("id"),
                    "team_name": team_field.get(f"{side}_name") or team_field.get("name"),
                })
            else:
                teams_rows.append({
                    "team_id": row.get(f"{side}_id"),
                    "team_name": row.get(f"{side}_name") or row.get(side),
                })
    teams_df = pd.DataFrame(teams_rows).dropna().drop_duplicates(subset=["team_id"])
    teams_df["team_id"] = teams_df["team_id"].astype(int)
    with get_connection() as conn:
        teams_df.to_sql("_teams_staging", conn, if_exists="replace", index=False)
        conn.execute("INSERT OR IGNORE INTO teams (team_id, team_name) SELECT team_id, team_name FROM _teams_staging")
        conn.execute("DROP TABLE _teams_staging")
        matches_clean.to_sql("_matches_staging", conn, if_exists="replace", index=False)
        conn.execute(
            "INSERT OR IGNORE INTO matches "
            "(match_id, competition_id, season_id, match_date, kick_off, "
            " home_team_id, away_team_id, home_score, away_score, stadium, referee, competition_stage) "
            "SELECT match_id, competition_id, season_id, match_date, kick_off, "
            "       home_team_id, away_team_id, home_score, away_score, stadium, referee, competition_stage "
            "FROM _matches_staging"
        )
        conn.execute("DROP TABLE _matches_staging")
    return matches_clean


def _extract_team_id(team_field):
    if isinstance(team_field, dict):
        return team_field.get("home_team_id") or team_field.get("away_team_id") or team_field.get("id")
    return None


def ingest_all(include_lineups: bool = False) -> None:
    init_schema()
    comps = ingest_competitions()
    print(f"\nFetching matches for {len(comps)} competition+season pairs...")
    for _, comp in comps.iterrows():
        cid, sid = int(comp["competition_id"]), int(comp["season_id"])
        label = f"{comp['competition_name']} ({comp['season_name']})"
        try:
            matches_df = ingest_matches_for(cid, sid)
            print(f"  {label}: {len(matches_df)} matches")
        except Exception as e:
            print(f"  {label}: FAILED ({e})")
    print("\nDone.")
