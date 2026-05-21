"""Load FiveThirtyEight SPI ratings and convert to ELO scale.

Data file: data/538_intl_rankings.csv
Source: archived Feb 2025 snapshot of FiveThirtyEight's international SPI rankings.
Columns: rank, name, confed, off, def, spi

SPI → ELO conversion: elo = 1500 + (spi - 60) * 15
This maps Brazil (93.08) → ~1996, average team (60) → 1500, Curacao (38.67) → ~1180.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "538_intl_rankings.csv"

# 538 uses different names for some teams; map to project names.
_538_TO_PROJECT: Dict[str, str] = {
    "USA":                 "United States",
    "Cape Verde Islands":  "Cape Verde",
    "Congo DR":            "DR Congo",
    "Curacao":             "Curaçao",
}

_SPI_CENTER = 60.0
_SPI_ELO_SCALE = 15.0
_ELO_CENTER = 1500.0


def spi_to_elo(spi: float) -> float:
    return _ELO_CENTER + (spi - _SPI_CENTER) * _SPI_ELO_SCALE


def load_spi_ratings() -> Dict[str, dict]:
    """Return dict keyed by *project* team name: {off, def, spi, elo}."""
    ratings: Dict[str, dict] = {}
    with open(_DATA_FILE, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = _538_TO_PROJECT.get(row["name"], row["name"])
            spi = float(row["spi"])
            ratings[name] = {
                "off":  float(row["off"]),
                "def":  float(row["def"]),
                "spi":  spi,
                "elo":  spi_to_elo(spi),
            }
    return ratings
