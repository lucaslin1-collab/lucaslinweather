"""The National Weather Service's own station forecast: the National Blend of Models (NBM)
text guidance ("NBS"), archived by Iowa State's Environmental Mesonet.

For each day it gives the forecast daytime high for the exact station Kalshi settles on
(`txn`) and NBM's own uncertainty for it (`xnd`, a standard deviation in °F). NBM runs
through the night, so the bot can use a run issued shortly before its 8 a.m. decision
instead of a forecast from the day before.
"""
import io
import time

import numpy as np
import pandas as pd
import requests

import config

IEM_MOS = "https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py"
UA = {"User-Agent": "lucaslinweather (research bot)"}
LATENCY = pd.Timedelta(minutes=75)   # NBM text is usually public about an hour after its run time


def fetch_runs(station, start, end):
    """All NBS runs with run time in [start, end], one request per month. Columns:
    runtime, ftime (UTC), txn, xnd."""
    frames = []
    for ms in pd.date_range(pd.Timestamp(start).replace(day=1), end, freq="MS"):
        s = max(pd.Timestamp(start), ms)
        e = min(pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59), ms + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59))
        params = {"station": station, "model": "NBS", "sts": f"{s:%Y-%m-%dT%H:%M}Z",
                  "ets": f"{e:%Y-%m-%dT%H:%M}Z", "format": "csv"}
        resp = None
        for attempt in range(5):
            try:
                resp = requests.get(IEM_MOS, params=params, headers=UA, timeout=180)
            except (requests.Timeout, requests.ConnectionError):
                time.sleep(15 * (attempt + 1))
                continue
            if resp.status_code in (429, 502, 503, 504):
                time.sleep(15 * (attempt + 1))
                continue
            break
        if resp is None or resp.status_code != 200 or "," not in resp.text[:200]:
            continue
        df = pd.read_csv(io.StringIO(resp.text), usecols=lambda c: c in ("runtime", "ftime", "txn", "xnd"))
        if len(df):
            frames.append(df)
        time.sleep(0.5)
    if not frames:
        return pd.DataFrame(columns=["runtime", "ftime", "txn", "xnd"])
    df = pd.concat(frames, ignore_index=True)
    df["runtime"] = pd.to_datetime(df["runtime"])
    df["ftime"] = pd.to_datetime(df["ftime"])
    for c in ("txn", "xnd"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    return df.dropna(subset=["txn"]).drop_duplicates(["runtime", "ftime"])


def daily_guidance(runs, city, dates, now=None):
    """For each local date D: the NBM daytime high and its uncertainty from the latest run
    public before D's decision time (DECISION_HOUR_LOCAL), plus how old that run was."""
    out = []
    if runs.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="date"), columns=["nbm_txn", "nbm_xnd", "nbm_age_h"])
    runtimes = np.sort(runs["runtime"].unique())
    by_run = dict(tuple(runs.groupby("runtime")))
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    for d in dates:
        d = pd.Timestamp(d)
        decision = (d + pd.Timedelta(hours=config.DECISION_HOUR_LOCAL)).tz_localize(city["tz"]).tz_convert("UTC").tz_localize(None)
        cutoff = min(decision, now) - LATENCY
        i = np.searchsorted(runtimes, np.datetime64(cutoff), side="right") - 1
        if i < 0:
            continue
        rt = pd.Timestamp(runtimes[i])
        if decision - rt > pd.Timedelta(hours=30):
            continue
        # daytime max for local day D: the larger txn value whose valid time falls between
        # mid-afternoon on D and early morning on D+1 (local standard time)
        day0 = d - pd.Timedelta(hours=city["std_offset"])
        run = by_run[rt]
        win = run[(run["ftime"] >= day0 + pd.Timedelta(hours=14)) & (run["ftime"] <= day0 + pd.Timedelta(hours=30))]
        if win.empty:
            continue
        best = win.loc[win["txn"].idxmax()]
        out.append({"date": d, "nbm_txn": float(best["txn"]), "nbm_xnd": float(best["xnd"]) if pd.notna(best["xnd"]) else np.nan,
                    "nbm_age_h": (decision - rt).total_seconds() / 3600})
    return pd.DataFrame(out).set_index("date") if out else \
        pd.DataFrame(index=pd.DatetimeIndex([], name="date"), columns=["nbm_txn", "nbm_xnd", "nbm_age_h"])
