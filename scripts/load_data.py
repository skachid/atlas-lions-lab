"""Run me: python scripts/load_data.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.ingest import ingest_all

if __name__ == "__main__":
    ingest_all(include_lineups="--with-lineups" in sys.argv)
