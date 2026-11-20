"""Check which forecast sources return archived data for each city, and from when.
Small, rate-limit friendly, saves partial results. Writes data/source_probe.json."""
import io
import json
import os
import time

import pandas as pd
import requests

PREV = "https://previous-runs-api.open-meteo.com/v1/forecast"
DEADLINE = time.time() + 9 * 60
US = {"New York (KNYC)": (40.7789, -73.9692)}
INTL = {"London (EGLL)": (51.4706, -0.4619), "Paris (LFPG)": (49.0097, 2.5479),
        "Hong Kong (VHHH)": (22.3080, 113.9185), "Tokyo (RJTT)": (35.5523, 139.7798),
        "Beijing (ZBAA)": (40.0801, 116.5846)}
COMMON = ["ecmwf_ifs025", "ecmwf_ifs", "ecmwf_aifs025_single", "gfs_seamless", "icon_seamless", "gem_seamless"]
REGIONAL = {
    "New York (KNYC)": ["ncep_nbm_conus", "gfs_hrrr", "ncep_hrrr_conus", "gfs_graphcast025"],
    "London (EGLL)": ["ukmo_seamless", "ukmo_uk_deterministic_2km", "knmi_seamless", "meteofrance_seamless", "icon_eu", "dmi_seamless"],
    "Paris (LFPG)": ["meteofrance_seamless", "meteofrance_arome_france_hd", "meteofrance_arome_france", "ukmo_seamless", "icon_eu"],
    "Hong Kong (VHHH)": ["cma_grapes_global", "jma_seamless", "ukmo_seamless"],
    "Tokyo (RJTT)": ["jma_seamless", "jma_msm", "jma_gsm", "kma_seamless"],
    "Beijing (ZBAA)": ["cma_grapes_global", "jma_seamless", "kma_seamless"],
}
recent = (pd.Timestamp.now("UTC") - pd.Timedelta(days=9)).strftime("%Y-%m-%d")
WINDOWS = {"2024-02-01": "2024-02-01", "2025-01-10": "2025-01-10", "recent": recent}
OUT = "data/source_probe.json"
out = {"open_meteo": {}, "iem_mos": {}, "hko": None}


def save():
    os.makedirs("data", exist_ok=True)
    json.dump(out, open(OUT, "w"), indent=2, default=str)


def get(url, params, timeout=30):
    for attempt in range(3):
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 429:
            time.sleep(20)
            continue
        return r
    return r


for place, (lat, lon) in {**US, **INTL}.items():
    out["open_meteo"][place] = {}
    for model in COMMON + REGIONAL[place]:
        if time.time() > DEADLINE:
            break
        row = {}
        for wname, day in WINDOWS.items():
            try:
                r = get(PREV, {"latitude": lat, "longitude": lon, "models": model,
                               "hourly": "temperature_2m_previous_day1", "timezone": "GMT",
                               "start_date": day, "end_date": day})
                if r.status_code != 200:
                    row[wname] = f"HTTP {r.status_code}: {r.text[:90]}"
                else:
                    vals = r.json().get("hourly", {}).get("temperature_2m_previous_day1", [])
                    row[wname] = sum(v is not None for v in vals)
            except Exception as err:  # noqa: BLE001
                row[wname] = f"error {err}"[:90]
            time.sleep(0.4)
        out["open_meteo"][place][model] = row
        print(place, model, row, flush=True)
        save()

for model in ["NBS", "NBE", "GFS", "MEX", "LAV"]:
    for day in ["2024-02-01", "2025-06-01", recent]:
        if time.time() > DEADLINE + 120:
            break
        try:
            r = get("https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py",
                    {"station": "KNYC", "model": model, "sts": f"{day}T00:00Z", "ets": f"{day}T23:59Z",
                     "format": "csv"}, timeout=45)
            txt = r.text
            ok = r.status_code == 200 and "," in txt[:300]
            df = pd.read_csv(io.StringIO(txt)) if ok else None
            out["iem_mos"][f"{model} {day}"] = {"http": r.status_code, "rows": None if df is None else len(df),
                                                "columns": None if df is None else list(df.columns),
                                                "sample": txt[:500]}
        except Exception as err:  # noqa: BLE001
            out["iem_mos"][f"{model} {day}"] = f"error {err}"[:200]
        print("MOS", model, day, str(out["iem_mos"][f"{model} {day}"])[:160], flush=True)
        save()
save()
print("saved")
