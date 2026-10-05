"""
nasa_power.py — Optional NASA POWER / IMD solar-resource helper.

Role in the project
-------------------
This module is a **supporting service**, not part of the live simulation path. The
dashboard's generation figures come from the embedded SRRA/MNRE-style profile in
``Config.BASE_GENERATION_KW``; nothing in the energy balance calls into this module.

It exists so the dashboard can *validate* that embedded profile against an
independent, real-world irradiance source, and so a live fetch can be cached for
repeat use. It has two hard requirements:

* it must never raise or block the dashboard if the network is unavailable, and
* it must never invent bus route geometry. An earlier revision of this file shipped
  a ``generate_bus_routes()`` helper whose waypoints contradicted the authoritative
  corridors in ``Config.ROUTES``; that function has been removed, because two
  sources of truth for the same geometry is a defect, not a convenience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from src.config.settings import Config

# ─────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = _BASE_DIR / "data" / "raw"
CACHE_FILE = CACHE_DIR / "nasa_cache.json"

# Ukkadam Lake, Coimbatore. Sourced from Config so there is one copy of the site
# location; these previously existed as independent literals here and in
# ``src.models.bus.FALLBACK_COORD``, so moving the lake in settings would have left the
# NASA POWER query and the degenerate-route fallback silently pointing at the old spot.
LAT = Config.LAKE_LAT
LON = Config.LAKE_LON

#: Average daily global horizontal irradiance for Coimbatore, kWh/m²/day.
#: SRRA / MNRE / IMD normals, used when the NASA POWER service is unavailable.
FALLBACK_GHI_KWH_M2_DAY: Dict[int, float] = {
    1: 5.4, 2: 5.9, 3: 6.3, 4: 6.2, 5: 5.8,
    6: 4.1, 7: 3.8, 8: 3.9, 9: 4.3,
    10: 4.8, 11: 5.0, 12: 5.1,
}

#: Monthly mean air temperature for Coimbatore, °C (IMD climatological normals).
MONTHLY_TEMP_C: Dict[int, float] = {
    1: 25.5, 2: 27.0, 3: 30.0, 4: 32.5, 5: 33.0,
    6: 30.0, 7: 29.0, 8: 29.0, 9: 29.5,
    10: 28.0, 11: 26.5, 12: 25.0,
}

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _read_cache() -> Optional[dict]:
    """Return the cached NASA POWER payload, or ``None`` if absent or corrupt."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


def fetch_nasa_power(
    lat: float = LAT, lon: float = LON, year: int = 2023, timeout: float = 8.0
) -> Optional[dict]:
    """Fetch and cache monthly irradiance from the NASA POWER service.

    Returns the cached payload if present, otherwise attempts one network call.
    Returns ``None`` on any failure so callers can fall back without handling
    exceptions.
    """
    cached = _read_cache()
    if cached is not None:
        return cached

    url = (
        "https://power.larc.nasa.gov/api/temporal/monthly/point"
        "?parameters=ALLSKY_SFC_SW_DWN"
        "&community=RE"
        f"&longitude={lon}&latitude={lat}"
        f"&start={year}&end={year}"
        "&format=JSON"
    )
    try:
        import requests  # imported lazily; only needed on a cache miss

        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        ghi = payload["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]
        result = {
            "source": "NASA POWER",
            "year": year,
            "latitude": lat,
            "longitude": lon,
            "monthly_ghi": ghi,
        }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        return result
    except Exception:  # network, parsing, or missing dependency
        return None


def get_solar_irradiance(month: int, allow_fetch: bool = True) -> Dict[str, float]:
    """Return monthly irradiance with an explicit provenance tag.

    Returns:
        ``{"value": kWh/m²/day, "source": "NASA POWER" | "SRRA/MNRE fallback"}``
    """
    if allow_fetch:
        cached = fetch_nasa_power()
        if cached and "monthly_ghi" in cached:
            key = f"{cached['year']}{month:02d}"
            if key in cached["monthly_ghi"]:
                return {"value": round(float(cached["monthly_ghi"][key]), 2),
                        "source": "NASA POWER"}

    return {
        "value": FALLBACK_GHI_KWH_M2_DAY.get(month, 5.0),
        "source": "SRRA/MNRE fallback",
    }


def monthly_ghi_series(allow_fetch: bool = False) -> Dict[int, Dict[str, float]]:
    """Return ``{month: {"value", "source"}}`` for all twelve months.

    Defaults to the offline fallback so that building the dashboard never blocks on a
    network round trip.
    """
    return {month: get_solar_irradiance(month, allow_fetch=allow_fetch) for month in range(1, 13)}


def get_temperature(month: int) -> float:
    """Mean air temperature for a Coimbatore month, °C."""
    return MONTHLY_TEMP_C.get(month, 29.0)


def cache_status() -> Dict[str, object]:
    """Report whether a real NASA POWER cache is present, for display in the UI."""
    cached = _read_cache()
    return {
        "cache_path": str(CACHE_FILE),
        "cache_present": cached is not None,
        "year": cached.get("year") if cached else None,
        "source": cached.get("source") if cached else "not cached — using SRRA/MNRE fallback",
    }
