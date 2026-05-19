"""Run me: API_FOOTBALL_KEY=your_key python scripts/load_api_football.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.api_football import ingest_all

if __name__ == "__main__":
    ingest_all()
