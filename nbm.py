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
NBM_VERSION = 2                      # bump to re-download history when the parsing changes


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
        for attempt in range(6):
            try:
                resp = requests.get(IEM_MOS, params=params, headers=UA, timeout=180)
            except (requests.Timeout, requests.ConnectionError):
                time.sleep(20 * (attempt + 1))
                continue
            # Iowa State answers "Too many requests from your IP address, slow down." when busy
            if resp.status_code in (429, 502, 503, 504) or resp.text.startswith("Too many requests"):
                time.sleep(30 * (attempt + 1))
                continue
            break
        if resp is None or resp.status_code != 200 or not resp.text.startswith("runtime"):
            raise RuntimeError(f"NBM archive unavailable for {station} {ms:%Y-%m}")
        df = pd.read_csv(io.StringIO(resp.text), usecols=lambda c: c in ("runtime", "ftime", "txn", "xnd"))
        if len(df):
            frames.append(df)
        time.sleep(2.0)   # stay well under the archive's rate limit
    if not frames:
        return pd.DataFrame(columns=["runtime", "ftime", "txn", "xnd"])
    df = pd.concat(frames, ignore_index=True)
    df["runtime"] = pd.to_datetime(df["runtime"])
    df["ftime"] = pd.to_datetime(df["ftime"])
    for c in ("txn", "xnd"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    return df.dropna(subset=["txn"]).drop_duplicates(["runtime", "ftime"])


def daily_guidance(runs, city, dates, now=None, decision_hour=None):
    """For each local date D: the NBM daytime high and its uncertainty from the latest run
    public before D's decision time, plus how old that run was. `decision_hour` is hours from
    local midnight of D (negative = the day before); default DECISION_HOUR_LOCAL."""
    decision_hour = config.DECISION_HOUR_LOCAL if decision_hour is None else decision_hour
    out = []
    if runs.empty:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="date"), columns=["nbm_txn", "nbm_xnd", "nbm_age_h"])
    runtimes = np.sort(runs["runtime"].unique())
    by_run = dict(tuple(runs.groupby("runtime")))
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    for d in dates:
        d = pd.Timestamp(d)
        decision = (d + pd.Timedelta(hours=decision_hour)).tz_localize(city["tz"], nonexistent="shift_forward",
                                                                       ambiguous=False).tz_convert("UTC").tz_localize(None)
        cutoff = min(decision, now) - LATENCY
        # NBM lists the daytime high for US stations at 00 UTC the next day (the 12-hour
        # max ending then); the low is listed at 12 UTC. Runs issued after 12 UTC no longer
        # include the current day's high, so walk back to the latest run that does.
        target = d + pd.Timedelta(days=1)
        i = np.searchsorted(runtimes, np.datetime64(cutoff), side="right") - 1
        while i >= 0:
            rt = pd.Timestamp(runtimes[i])
            if decision - rt > pd.Timedelta(hours=30):
                break
            row = by_run[rt][by_run[rt]["ftime"] == target]
            if len(row):
                r = row.iloc[0]
                out.append({"date": d, "nbm_txn": float(r["txn"]),
                            "nbm_xnd": float(r["xnd"]) if pd.notna(r["xnd"]) else np.nan,
                            "nbm_age_h": (decision - rt).total_seconds() / 3600})
                break
            i -= 1
    return pd.DataFrame(out).set_index("date") if out else \
        pd.DataFrame(index=pd.DatetimeIndex([], name="date"), columns=["nbm_txn", "nbm_xnd", "nbm_age_h"])
