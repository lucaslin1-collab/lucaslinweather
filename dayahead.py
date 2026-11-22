"""Day-before picks: price TOMORROW's markets around midday today.

The entry-time backtest (timing.py) found entering around noon the day before beat the 8 a.m.
entry. This runs that same setup live, as a paper test kept apart from the 8 a.m. picks:
  - the model is trained exactly as in the noon-day-before backtest: global-model forecasts
    issued two days ahead, and the NWS station forecast as it stood at noon the day before,
  - the market blend and the city verdicts come from that backtest (results/blend_d1_12.pkl,
    results/timing.json),
  - picks follow the same rules as the 8 a.m. picks and go to data/picks_log_dayahead.csv.
"""
import json
import os
import pickle

import numpy as np
import pandas as pd

import config
from daily import city_today, fetch_open, load_log, log_picks, print_picks, settle_log
from data_store import local_today
from strategy import select

SLOT = "d1_12"


def log_path():
    return os.path.join(config.DATA_DIR, "picks_log_dayahead.csv")


def state_path():
    return os.path.join(config.DATA_DIR, "dayahead.json")


def load_setup():
    """Verdicts and market blend from the noon-day-before backtest."""
    verdicts, blend = {}, None
    p = os.path.join(config.RESULTS_DIR, "timing.json")
    if os.path.exists(p):
        e = json.load(open(p)).get("entry_times", {}).get(SLOT, {})
        verdicts = {s: c["verdict"] for s, c in e.get("cities", {}).items()}
    b = os.path.join(config.RESULTS_DIR, f"blend_{SLOT}.pkl")
    if os.path.exists(b):
        blend = pickle.load(open(b, "rb"))
    return verdicts, blend


def tomorrow_row(city, day):
    """Tomorrow's inputs as of now: 2-day-ahead model forecasts and the latest NWS run."""
    from nbm import daily_guidance, fetch_runs
    from weather import fetch_forecasts
    fc = fetch_forecasts(city, day, day)
    row = pd.DataFrame(index=pd.DatetimeIndex([day], name="date"))
    for c in fc.columns:
        if c.startswith("fc2_"):
            row["fc1_" + c.split("_", 1)[1]] = fc[c].reindex(row.index)
    try:
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        runs = fetch_runs(city["station"], now - pd.Timedelta(days=2), now)
        g = daily_guidance(runs, city, [day], decision_hour=config.ENTRY_TIMES[SLOT][0])
        row["nbm_txn"] = g["nbm_txn"].reindex(row.index)
        row["nbm_xnd"] = g["nbm_xnd"].reindex(row.index)
    except Exception as err:  # noqa: BLE001
        print(f"  {city['series']}: NWS forecast unavailable ({err})")
    return row


def run(client, cities):
    import timing
    verdicts, blend = load_setup()
    if blend is None:
        print("No day-before blend yet (results/blend_d1_12.pkl): run the backfill first.")
    open_mk = fetch_open(client, cities)
    panels, candidates = [], []
    for series, city in cities.items():
        day = local_today(city) + pd.Timedelta(days=1)
        hist = timing.weather_at(series, SLOT)
        hist = hist[hist.index < day]
        try:
            w = pd.concat([hist, tomorrow_row(city, day)])
        except Exception as err:  # noqa: BLE001
            print(f"  {series}: forecast for tomorrow unavailable ({err})")
            w = hist
        rows = open_mk.get(series, pd.DataFrame())
        if not rows.empty:
            rows = rows[rows["date"] == day]
        panel, priced = city_today(city, w, rows, blend, verdicts.get(series, "unproven"), day)
        panels.append(panel)
        if not priced.empty:
            candidates.append(priced.assign(city=city["city"], verdict=panel["verdict"]))
    picks = select(pd.concat(candidates, ignore_index=True), "p_final") if candidates else pd.DataFrame()
    settle_log(client, path=log_path())
    settle_log(client)   # also score the 8 a.m. picks, in case they settled after the morning run
    log = log_picks(picks, path=log_path())
    print_picks(picks)
    save_state(panels, picks)
    return panels, picks, log


def save_state(panels, picks):
    cols = ["date", "series", "city", "ticker", "label", "side", "price", "prob", "ev", "stake_pct", "verdict"]
    rows = [] if picks.empty else picks[cols].assign(date=picks["date"].dt.strftime("%Y-%m-%d")).to_dict("records")
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (np.floating, float)):
                r[k] = round(float(v), 4)
    target = panels[0]["date"] if panels else None
    os.makedirs(config.DATA_DIR, exist_ok=True)
    json.dump({"generated": pd.Timestamp.now(tz="America/New_York").isoformat(timespec="minutes"),
               "target_date": target, "picks": rows,
               "panels": [{k: p.get(k) for k in ("series", "city", "date", "mu", "sigma", "note", "verdict")}
                          for p in panels]},
              open(state_path(), "w"), indent=2)


def load_state():
    """Latest day-before picks, if they are for today or later (Eastern)."""
    if not os.path.exists(state_path()):
        return None
    s = json.load(open(state_path()))
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    if not s.get("target_date") or pd.Timestamp(s["target_date"]) < today:
        return None
    s["picks"] = pd.DataFrame(s["picks"])
    if not s["picks"].empty:
        s["picks"]["date"] = pd.to_datetime(s["picks"]["date"])
    return s


def load_dayahead_log():
    return load_log(path=log_path())
