# main.py
import re
import requests
from typing import Any, Dict, Optional, Tuple
from fastapi import FastAPI, Query
from pydantic import BaseModel

# -----------------------------
# Config
# -----------------------------
SOILGRIDS_API = "https://rest.isric.org/soilgrids/v2.0/properties/query"
PROPERTIES = ["soc", "phh2o", "sand", "silt", "clay", "bdod", "ocs"]

# Default values if SoilGrids returns null
DEFAULT_VALUES = {
    "soc": 15.0,      # g/kg
    "phh2o": 6.5,     # -
    "sand": 30.0,     # %
    "silt": 40.0,     # %
    "clay": 30.0,     # %
    "bdod": 1.3,      # kg/dm³
    "ocs": 4.0        # kg/m²
}

_depth_label_re = re.compile(r"(\d+\.?\d*)\s*[-–]\s*(\d+\.?\d*)")


# -----------------------------
# Utility functions
# -----------------------------
def _extract_numeric_from_values(values: Dict[str, Any], d_factor: float = 1) -> Optional[float]:
    if not isinstance(values, dict):
        return None
    prefer = ["mean", "Q0.5", "median", "Q0.05", "Q0.95"]
    for k in prefer:
        v = values.get(k)
        if v is not None:
            try:
                return float(v) / d_factor
            except Exception:
                continue
    for k, v in values.items():
        if v is None:
            continue
        try:
            return float(v) / d_factor
        except Exception:
            continue
    return None


def _extract_unit(layer: Dict[str, Any]) -> Optional[str]:
    um = layer.get("unit_measure") or {}
    unit = um.get("target_units") or um.get("mapped_units") or um.get("unit")
    return unit


def _fetch_value(lat: float, lon: float, prop: str) -> Tuple[Optional[float], Optional[str]]:
    params = {"lat": lat, "lon": lon, "property": prop}
    try:
        r = requests.get(SOILGRIDS_API, params=params, timeout=25)
    except requests.RequestException:
        return None, None

    if r.status_code != 200:
        return None, None

    try:
        data = r.json()
    except Exception:
        return None, None

    layers = data.get("properties", {}).get("layers")
    layer_obj = None
    if isinstance(layers, dict):
        layer_obj = layers.get(prop)
    elif isinstance(layers, list):
        for item in layers:
            if isinstance(item, dict) and item.get("name") == prop:
                layer_obj = item
                break

    if not layer_obj:
        return None, None

    depths = layer_obj.get("depths") or []
    unit = _extract_unit(layer_obj)
    d_factor = layer_obj.get("unit_measure", {}).get("d_factor", 1)

    for d in depths:
        vals = d.get("values") or {}
        numeric = _extract_numeric_from_values(vals, d_factor=d_factor)
        if numeric is not None:
            return numeric, unit
    return None, unit


def fetch_property_for_point(lat: float, lon: float, prop: str) -> Tuple[Optional[float], Optional[str]]:
    # Try primary point
    val, unit = _fetch_value(lat, lon, prop)
    if val is not None:
        return val, unit

    # Option A: try nearby points with small delta
    delta = [0.01, -0.01, 0.02, -0.02]
    for dlat in delta:
        for dlon in delta:
            val, unit = _fetch_value(lat + dlat, lon + dlon, prop)
            if val is not None:
                return val, unit

    # Option B: fallback to default
    return DEFAULT_VALUES[prop], ""


def fetch_soil_data_all(lat: float, lon: float) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for p in PROPERTIES:
        val, unit = fetch_property_for_point(lat, lon, p)
        out[p] = {"value": val, "unit": unit}
    return out


# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(
    title="SoilGrids API Wrapper",
    version="1.0",
    description="API to fetch soil properties from ISRIC SoilGrids with fallback for missing data."
)


class SoilDataResponse(BaseModel):
    property: str
    value: float
    unit: str


@app.get("/health")
def health_check():
    return {"status": "ok", "message": "SoilGrids API wrapper running"}


@app.get("/soil-data")
def get_soil_data(lat: float = Query(..., description="Latitude"),
                  lon: float = Query(..., description="Longitude")):
    """
    Fetch soil properties for a given latitude and longitude.
    Includes fallback to nearby points and default values if SoilGrids data is missing.
    """
    data = fetch_soil_data_all(lat, lon)
    return {"lat": lat, "lon": lon, "soil_properties": data}
