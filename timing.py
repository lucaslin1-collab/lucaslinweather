"""Timing backtest: would entering at a different time than 8 a.m. have an edge?

For each entry time in ENTRY_TIMES, the bot is rebuilt with only what was public then:
  - market prices at that time (data/quotes/),
  - the NWS station forecast from the latest run before then (data/nbm_slots/),
  - global-model forecasts issued 1 day ahead for same-day entries, 2 days ahead for
    entries the day before (so nothing from after the entry time leaks in).
Each entry time gets its own walk-forward backtest, market blend and trades, scored like
the main backtest. Writes results/timing.json.
"""
import json
import os

import numpy as np
import pandas as pd

import config
from backtest import simulate, summarize, walk_forward
from data_store import _dir, load_all_markets, load_weather


def _load(kind, series, **kw):
    p = os.path.join(_dir(kind), f"{series}.csv")
    return pd.read_csv(p, **kw) if os.path.exists(p) else pd.DataFrame()


def weather_at(series, slot):
    w = load_weather(series)
    if w.empty:
        return w
    lead = 2 if slot.startswith("d1") else 1
    fc = {c: "fc1_" + c.split("_", 1)[1] for c in w.columns if c.startswith(f"fc{lead}_")}
    out = w[list(fc)].rename(columns=fc)
    out["observed_high"] = w["observed_high"]
    nb = _load("nbm_slots", series, parse_dates=["date"])
    if not nb.empty and f"nbm_txn_{slot}" in nb:
        nb = nb.set_index("date")
        out["nbm_txn"] = nb[f"nbm_txn_{slot}"].reindex(out.index)
        out["nbm_xnd"] = nb[f"nbm_xnd_{slot}"].reindex(out.index)
    return out


def markets_at(series, slot):
    mk = load_all_markets(series)
    q = _load("quotes", series)
    if mk.empty or q.empty:
        return pd.DataFrame()
    q = q[q["slot"] == slot].drop_duplicates("ticker", keep="last")[["ticker", "yes_bid", "yes_ask"]]
    return mk.drop(columns=["yes_bid", "yes_ask"], errors="ignore").merge(q, on="ticker", how="inner")


def save_blend(slot, preds):
    """Market blend fit on every out-of-sample prediction for this entry time, for live use."""
    import pickle
    from model import MarketBlend
    from strategy import has_quote
    q = preds[has_quote(preds) & preds["p_model"].notna()]
    if len(q) < 200:
        return
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    blend = MarketBlend().fit(q["p_model"], q["mid"], q["result"])
    pickle.dump(blend, open(os.path.join(config.RESULTS_DIR, f"blend_{slot}.pkl"), "wb"))


def run(cities):
    results = {"generated": pd.Timestamp.now().isoformat(timespec="minutes"), "entry_times": {}}
    for slot, (hours, label) in config.ENTRY_TIMES.items():
        weather = {s: weather_at(s, slot) for s in cities}
        mk = [markets_at(s, slot) for s in cities]
        mk = [m for m in mk if not m.empty]
        if not mk:
            continue
        markets = pd.concat(mk, ignore_index=True)
        markets["mid"] = (markets["yes_bid"] + markets["yes_ask"]) / 2
        try:
            preds = walk_forward(cities, weather=weather, markets=markets)
        except SystemExit as err:
            print(f"  {label}: skipped ({err})")
            continue
        trades = simulate(preds, "p_blend")
        save_blend(slot, preds)
        entry = {"label": label, "hours": hours,
                 "period": [f"{preds['date'].min():%Y-%m-%d}", f"{preds['date'].max():%Y-%m-%d}"],
                 "overall": summarize(preds, trades), "cities": {}}
        span = (preds["date"].max() - preds["date"].min()).days + 1
        entry["overall"]["bets_per_day"] = round(len(trades) / max(span, 1), 2)
        for s, rows in preds.groupby("series"):
            c = summarize(rows, trades[trades["series"] == s] if len(trades) else trades)
            c["city"] = cities[s]["city"]
            entry["cities"][s] = c
        results["entry_times"][slot] = entry
        o = entry["overall"]
        print(f"  {label:<24} skill model {o.get('bss_model', np.nan):+.3f} blend {o.get('bss_blend', np.nan):+.3f} "
              f"| {o['trades']} bets ({entry['overall']['bets_per_day']}/day) P&L ${o.get('pnl', 0):.2f} "
              f"range {[round(x, 1) for x in o.get('ci90', [0, 0])]} | {o['verdict']}")
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    json.dump(results, open(os.path.join(config.RESULTS_DIR, "timing.json"), "w"), indent=2, default=float)
    return results
