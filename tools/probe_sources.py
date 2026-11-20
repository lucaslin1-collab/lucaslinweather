"""Check which forecast sources return archived data for each city, and from when.
Writes data/source_probe.json."""
import io
import json
import os

import pandas as pd
import requests

PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
PLACES = {
    "New York (KNYC)": (40.7789, -73.9692), "Chicago (KMDW)": (41.786, -87.7524),
    "London (EGLL)": (51.4706, -0.4619), "Paris (LFPG)": (49.0097, 2.5479),
    "Amsterdam (EHAM)": (52.3086, 4.7639), "Hong Kong (VHHH)": (22.3080, 113.9185),
    "Beijing (ZBAA)": (40.0801, 116.5846), "Shanghai (ZSPD)": (31.1434, 121.8052),
}
MODELS = ["gfs_seamless", "ecmwf_ifs025", "ecmwf_ifs", "ecmwf_aifs025_single", "icon_seamless", "icon_d2",
          "icon_eu", "gem_seamless", "ukmo_seamless", "ukmo_uk_deterministic_2km", "ukmo_global_deterministic_10km",
          "meteofrance_seamless", "meteofrance_arome_france", "meteofrance_arome_france_hd", "meteofrance_arpege_europe",
          "knmi_seamless", "knmi_harmonie_arome_netherlands", "knmi_harmonie_arome_europe", "dmi_seamless",
          "dmi_harmonie_arome_europe", "cma_grapes_global", "jma_seamless", "jma_gsm", "jma_msm",
          "ncep_nbm_conus", "gfs_hrrr", "ncep_hrrr_conus", "kma_seamless", "metno_seamless", "bom_access_global"]
WINDOWS = {"2024-02": ("2024-02-01", "2024-02-02"), "2025-01": ("2025-01-10", "2025-01-11"),
           "recent": ((pd.Timestamp.utcnow() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
                      (pd.Timestamp.utcnow() - pd.Timedelta(days=8)).strftime("%Y-%m-%d"))}

from concurrent.futures import ThreadPoolExecutor


def probe(place, model, wname):
    lat, lon = PLACES[place]
    s, e = WINDOWS[wname]
    try:
        r = requests.get(PREV, params={"latitude": lat, "longitude": lon, "models": model,
                                       "hourly": "temperature_2m_previous_day1", "timezone": "GMT",
                                       "start_date": s, "end_date": e}, timeout=25)
        if r.status_code != 200:
            return place, model, wname, f"HTTP {r.status_code}: {r.text[:80]}"
        vals = r.json().get("hourly", {}).get("temperature_2m_previous_day1", [])
        return place, model, wname, sum(v is not None for v in vals)
    except Exception as err:  # noqa: BLE001
        return place, model, wname, f"error {err}"[:100]


out = {"open_meteo": {p: {m: {} for m in MODELS} for p in PLACES}, "iem_mos": {}, "hko": None}
jobs = [(p, m, w) for p in PLACES for m in MODELS for w in WINDOWS]
with ThreadPoolExecutor(max_workers=6) as ex:
    for place, model, wname, val in ex.map(lambda a: probe(*a), jobs):
        out["open_meteo"][place][model][wname] = val
print("open-meteo probes:", len(jobs))

# NWS station guidance (MOS / NBM text) archived by Iowa State
for model in ["NBS", "NBE", "GFS", "MEX", "LAV", "NAM"]:
    for when in ["2024-02-01", "2025-06-01", (pd.Timestamp.utcnow() - pd.Timedelta(days=3)).strftime("%Y-%m-%d")]:
        try:
            r = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py",
                             params={"station": "KNYC", "model": model, "sts": f"{when}T00:00Z",
                                     "ets": f"{when}T23:59Z", "format": "csv"}, timeout=60)
            txt = r.text
            df = pd.read_csv(io.StringIO(txt)) if r.status_code == 200 and "," in txt[:200] else None
            out["iem_mos"][f"{model} {when}"] = {
                "http": r.status_code, "rows": None if df is None else len(df),
                "columns": None if df is None else list(df.columns)[:30],
                "head": txt[:400] if df is None else df.head(3).to_dict("records")}
        except Exception as err:  # noqa: BLE001
            out["iem_mos"][f"{model} {when}"] = f"error {err}"[:200]

# Hong Kong Observatory open data: daily max at the observatory / airport
try:
    r = requests.get("https://data.weather.gov.hk/weatherAPI/opendata/opendata.php",
                     params={"dataType": "CLMMAXT", "rformat": "csv", "station": "HKA"}, timeout=60)
    out["hko"] = {"http": r.status_code, "head": r.text[:600]}
except Exception as err:  # noqa: BLE001
    out["hko"] = f"error {err}"

os.makedirs("data", exist_ok=True)
json.dump(out, open("data/source_probe.json", "w"), indent=2, default=str)
print("saved")
