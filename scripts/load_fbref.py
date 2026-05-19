"""Run me: python scripts/load_fbref.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.fbref_scraper import ingest_all

if __name__ == "__main__":
    ingest_all(season="2024-25")
