"""Run me: python scripts/load_wikipedia.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.wikipedia_scraper import ingest_all

if __name__ == "__main__":
    ingest_all()
