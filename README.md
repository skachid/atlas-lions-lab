# Atlas Lions Lab

Personal soccer-data analysis & forecasting project. Pulls **StatsBomb Open Data**, loads it into a local **SQLite** database, and runs forecasting math (Poisson, Elo, Monte Carlo) on top.

## First-time setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python scripts/load_data.py
jupyter notebook
```

When you're done: `deactivate`. Next time: `cd` in, `source venv/bin/activate`, you're back.

## Layout

- `data/` — SQLite database (gitignored)
- `notebooks/` — Jupyter notebooks
- `src/` — Reusable Python modules (db, ingest, schema, elo, poisson, monte_carlo)
- `scripts/load_data.py` — Run this to populate the DB
