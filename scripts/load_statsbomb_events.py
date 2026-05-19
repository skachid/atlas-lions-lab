"""Run me: python scripts/load_statsbomb_events.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.statsbomb_events import ingest_all

if __name__ == "__main__":
    ingest_all()
