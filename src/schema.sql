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
    position       TEXT,
    is_starter     INTEGER DEFAULT 1,  -- 1 = starter, 0 = substitute
    PRIMARY KEY (match_id, team_id, player_id),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE INDEX IF NOT EXISTS idx_lineups_player ON lineups(player_id);
CREATE INDEX IF NOT EXISTS idx_lineups_match ON lineups(match_id);

-- Key in-match events: goals, assists, cards, substitutions
CREATE TABLE IF NOT EXISTS match_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL,
    team_id         INTEGER NOT NULL,
    player_id       INTEGER,
    related_player_id INTEGER,  -- assist on a goal, player coming on for a sub
    minute          INTEGER,
    extra_minute    INTEGER,    -- stoppage time (e.g. 90+3 → minute=90, extra=3)
    event_type      TEXT NOT NULL,  -- 'goal', 'own_goal', 'yellow_card', 'red_card', 'yellow_red_card', 'subst'
    detail          TEXT,           -- e.g. 'Normal Goal', 'Penalty', 'Missed Penalty', 'Direct Free Kick'
    comments        TEXT,
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    FOREIGN KEY (related_player_id) REFERENCES players(player_id)
);

CREATE INDEX IF NOT EXISTS idx_events_match ON match_events(match_id);
CREATE INDEX IF NOT EXISTS idx_events_player ON match_events(player_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON match_events(event_type);

-- Aggregate match statistics per team (possession, shots, corners, etc.)
CREATE TABLE IF NOT EXISTS match_stats (
    match_id    INTEGER NOT NULL,
    team_id     INTEGER NOT NULL,
    stat_name   TEXT NOT NULL,
    stat_value  TEXT,           -- TEXT to handle both numeric and percentage values
    PRIMARY KEY (match_id, team_id, stat_name),
    FOREIGN KEY (match_id) REFERENCES matches(match_id),
    FOREIGN KEY (team_id) REFERENCES teams(team_id)
);

CREATE INDEX IF NOT EXISTS idx_stats_match ON match_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_stats_team ON match_stats(team_id);

-- Per-player per-match aggregated stats from StatsBomb event data
CREATE TABLE IF NOT EXISTS player_match_stats (
    player_id           INTEGER NOT NULL,
    match_id            INTEGER NOT NULL,
    team_id             INTEGER NOT NULL,
    is_starter          INTEGER DEFAULT 1,
    minutes_played      INTEGER DEFAULT 0,
    goals               INTEGER DEFAULT 0,
    assists             INTEGER DEFAULT 0,
    shots               INTEGER DEFAULT 0,
    shots_on_target     INTEGER DEFAULT 0,
    xg                  REAL    DEFAULT 0.0,
    key_passes          INTEGER DEFAULT 0,
    progressive_carries INTEGER DEFAULT 0,
    dribbles_completed  INTEGER DEFAULT 0,
    pressures           INTEGER DEFAULT 0,
    turnovers           INTEGER DEFAULT 0,
    position            TEXT,
    PRIMARY KEY (player_id, match_id),
    FOREIGN KEY (player_id) REFERENCES players(player_id),
    FOREIGN KEY (match_id)  REFERENCES matches(match_id),
    FOREIGN KEY (team_id)   REFERENCES teams(team_id)
);

CREATE INDEX IF NOT EXISTS idx_pms_player ON player_match_stats(player_id);
CREATE INDEX IF NOT EXISTS idx_pms_match  ON player_match_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_pms_team   ON player_match_stats(team_id);

-- Season-level club stats (one row per player per season per club)
CREATE TABLE IF NOT EXISTS player_season_stats (
    player_name             TEXT    NOT NULL,
    season                  TEXT    NOT NULL,
    club                    TEXT    NOT NULL,
    league                  TEXT,
    position                TEXT,
    apps                    INTEGER,
    starts                  INTEGER,
    minutes                 INTEGER,
    goals                   INTEGER,
    assists                 INTEGER,
    xg                      REAL,
    xag                     REAL,
    shots                   INTEGER,
    shots_on_target         INTEGER,
    key_passes              INTEGER,
    successful_dribbles     INTEGER,
    tackles                 INTEGER,
    interceptions           INTEGER,
    big_chances_created     INTEGER,
    rating                  REAL,
    source                  TEXT    DEFAULT 'sofascore',
    PRIMARY KEY (player_name, season, club)
);
