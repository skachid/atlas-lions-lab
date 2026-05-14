CREATE TABLE IF NOT EXISTS competitions (
    competition_id    INTEGER NOT NULL,
    season_id         INTEGER NOT NULL,
    country_name      TEXT,
    competition_name  TEXT,
    season_name       TEXT,
    PRIMARY KEY (competition_id, season_id)
);

CREATE TABLE IF NOT EXISTS teams (
    team_id    INTEGER PRIMARY KEY,
    team_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    match_id        INTEGER PRIMARY KEY,
    competition_id  INTEGER NOT NULL,
    season_id       INTEGER NOT NULL,
    match_date      TEXT,
    kick_off        TEXT,
    home_team_id    INTEGER NOT NULL,
    away_team_id    INTEGER NOT NULL,
    home_score      INTEGER,
    away_score      INTEGER,
    stadium         TEXT,
    referee         TEXT,
    competition_stage TEXT,
    FOREIGN KEY (competition_id, season_id) REFERENCES competitions(competition_id, season_id),
    FOREIGN KEY (home_team_id) REFERENCES teams(team_id),
    FOREIGN KEY (away_team_id) REFERENCES teams(team_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_competition_season ON matches(competition_id, season_id);
CREATE INDEX IF NOT EXISTS idx_matches_home_team ON matches(home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away_team ON matches(away_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);

CREATE TABLE IF NOT EXISTS players (
    player_id    INTEGER PRIMARY KEY,
    player_name  TEXT NOT NULL,
    nickname     TEXT,
    country      TEXT
);

CREATE TABLE IF NOT EXISTS lineups (
    match_id       INTEGER NOT NULL,
    team_id        INTEGER NOT NULL,
    player_id      INTEGER NOT NULL,
    jersey_number  INTEGER,
    PRIMARY KEY (match_id, team_id, player_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE INDEX IF NOT EXISTS idx_lineups_player ON lineups(player_id);
CREATE INDEX IF NOT EXISTS idx_lineups_match ON lineups(match_id);
