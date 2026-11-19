"""Weather inputs per city: point-in-time model forecasts and official observed highs."""
import re
import time

import numpy as np
import pandas as pd
import requests

from config import FORECAST_MODELS, LEADS

PREV_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
IEM_CLI_URL = "https://mesonet.agron.iastate.edu/json/cli.py"
_KEY = re.compile(r"^temperature_2m_previous_day(\d+)(?:_(.+))?$")


def _get(url, params, tries=5):
    for attempt in range(tries):
        resp = requests.get(url, params=params, timeout=90)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


def fetch_forecasts(city, start, end, chunk_days=120):
    """Daily max of each model's hourly 2 m temperature (°F), as forecast 1 and 2 days earlier.

    Days are local STANDARD time, matching the NWS climate day Kalshi settles on.
    Hourly maxima run slightly below the true daily max; the model learns that bias.
    Returns one row per date with columns fc{N}_{model}.
    """
    frames = []
    last = pd.Timestamp(end) + pd.Timedelta(days=1)      # local day ends after midnight UTC
    for chunk_start in pd.date_range(start, last, freq=f"{chunk_days}D"):
        chunk_end = min(chunk_start + pd.Timedelta(days=chunk_days - 1), last)
        data = _get(PREV_RUNS_URL, {
            "latitude": city["lat"], "longitude": city["lon"],
            "hourly": ",".join(f"temperature_2m_previous_day{n}" for n in LEADS),
            "models": ",".join(FORECAST_MODELS),
            "temperature_unit": "fahrenheit", "timezone": "GMT",
            "start_date": chunk_start.strftime("%Y-%m-%d"),
            "end_date": chunk_end.strftime("%Y-%m-%d"),
        })
        hourly = data["hourly"]
        df = pd.DataFrame({"time": pd.to_datetime(hourly["time"])})
        for key, values in hourly.items():
            match = _KEY.match(key)
            if match:
                model = match.group(2) or FORECAST_MODELS[0]
                df[f"fc{match.group(1)}_{model}"] = pd.to_numeric(values, errors="coerce")
        frames.append(df)
        time.sleep(0.3)  # be polite to the free API

    hourly = pd.concat(frames, ignore_index=True)
    hourly["date"] = (hourly["time"] + pd.Timedelta(hours=city["std_offset"])).dt.normalize()
    cols = [c for c in hourly.columns if c.startswith("fc")]
    daily = hourly.groupby("date")[cols].max()
    daily = daily.where(hourly.groupby("date")[cols].count() >= 20)   # skip days with gaps
    daily = daily.loc[pd.Timestamp(start):pd.Timestamp(end)]
    return daily.dropna(axis=1, how="all")


def fetch_observed(station, years):
    """Official daily highs from the NWS Daily Climate Report (CLI), via the Iowa
    Environmental Mesonet archive. This is the number Kalshi settles on."""
    rows = []
    for year in years:
        payload = _get(IEM_CLI_URL, {"station": station, "year": int(year)})
        records = payload.get("results", payload) if isinstance(payload, dict) else payload
        rows += [{"date": r.get("valid"), "observed_high": r.get("high")} for r in records]
    obs = pd.DataFrame(rows, columns=["date", "observed_high"])
    obs["date"] = pd.to_datetime(obs["date"], errors="coerce")
    obs["observed_high"] = pd.to_numeric(obs["observed_high"], errors="coerce")  # "M" -> NaN
    obs = obs.dropna().drop_duplicates("date", keep="last").set_index("date").sort_index()
    return obs["observed_high"]


def add_daily_features(daily):
    """Ensemble statistics and seasonality from the fc* columns."""
    daily = daily.copy()
    for lead in LEADS:
        cols = [c for c in daily.columns if c.startswith(f"fc{lead}_")]
        if cols:
            daily[f"ens{lead}_mean"] = daily[cols].mean(axis=1)
            daily[f"ens{lead}_spread"] = daily[cols].std(axis=1).fillna(0.0)
    doy = pd.to_datetime(daily.index).dayofyear.values
    daily["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    daily["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return daily
