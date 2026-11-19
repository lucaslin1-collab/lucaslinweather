"""Walk-forward backtest across all cities.

For every month, each city's weather model is trained only on earlier days, and the
market blend only on earlier out-of-sample predictions. Picks follow the exact rules
the daily run uses. Writes results/backtest.json, predictions.csv, trades.csv, blend.pkl.
"""
import json
import os
import pickle

import numpy as np
import pandas as pd

import config
from data_store import load_markets, load_weather
from model import EPS, HighTempModel, MarketBlend
from strategy import has_quote, select, settle_pnl


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def log_loss(p, y):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def bootstrap_ci(trades, n=2000, seed=0):
    """90% interval for total P&L, resampling whole days (same-day brackets are correlated)."""
    if trades.empty:
        return [0.0, 0.0]
    by_day = trades.groupby("date")["pnl"].sum().values
    totals = np.random.default_rng(seed).choice(by_day, size=(n, len(by_day))).sum(axis=1)
    return [float(np.percentile(totals, 5)), float(np.percentile(totals, 95))]


def walk_forward(cities):
    weather = {s: load_weather(s) for s in cities}
    markets = pd.concat([load_markets(s) for s in cities], ignore_index=True)
    if markets.empty:
        raise SystemExit("No market data yet. Run `python hightemp.py setup` first.")
    markets["mid"] = (markets["yes_bid"] + markets["yes_ask"]) / 2
    preds = []
    for month in sorted(markets["date"].dt.to_period("M").unique()):
        month_rows = []
        for series, w in weather.items():
            rows = markets[(markets["series"] == series) & (markets["date"].dt.to_period("M") == month)]
            if rows.empty or w.empty:
                continue
            train = w[w.index < month.start_time].dropna(subset=["observed_high"])
            if len(train) < config.MIN_TRAIN_DAYS:
                continue
            model = HighTempModel().fit(train)
            test_days = w[w.index.to_period("M") == month]
            rows = rows.copy()
            rows["p_model"] = model.bracket_probs(rows, test_days)
            mu, sigma = model.predict_dist(test_days)
            rows["mu"], rows["sigma"] = rows["date"].map(mu), rows["date"].map(sigma)
            rows["observed_high"] = rows["date"].map(w["observed_high"])
            rows["raw_ens"] = rows["date"].map(w[[c for c in w if c.startswith("fc1_")]].mean(axis=1))
            month_rows.append(rows)
        if not month_rows:
            continue
        rows = pd.concat(month_rows, ignore_index=True)
        rows["p_blend"] = np.nan
        prior = pd.concat(preds) if preds else pd.DataFrame()
        if not prior.empty:
            prior = prior[has_quote(prior) & prior["p_model"].notna()]
            if prior["date"].nunique() >= config.MIN_BLEND_DAYS:
                blend = MarketBlend().fit(prior["p_model"], prior["mid"], prior["result"])
                ok = has_quote(rows) & rows["p_model"].notna()
                rows.loc[ok, "p_blend"] = blend.predict(rows.loc[ok, "p_model"], rows.loc[ok, "mid"])
        preds.append(rows)
    if not preds:
        raise SystemExit(f"Not enough history: each city needs {config.MIN_TRAIN_DAYS}+ days of weather data.")
    return pd.concat(preds, ignore_index=True)


def simulate(preds, prob_col):
    picks = select(preds, prob_col)
    if picks.empty:
        return pd.DataFrame(columns=["date", "series", "ticker", "side", "price", "won", "pnl"])
    settled = [settle_pnl(r.side, r.price, r.result, r.fee_mult) for r in picks.itertuples()]
    picks = picks.assign(won=[s[0] for s in settled], pnl=[s[1] for s in settled])
    return picks[["date", "series", "ticker", "strike_type", "floor_strike", "cap_strike",
                  "side", "price", "prob", "ev", "won", "pnl"]]


def summarize(rows, trades):
    q = rows[has_quote(rows) & rows["p_model"].notna()]
    days = rows.drop_duplicates(["series", "date"]).dropna(subset=["mu", "observed_high"])
    out = {"days": int(rows[["series", "date"]].drop_duplicates().shape[0]),
           "markets": int(len(rows)), "quoted_markets": int(len(q))}
    if not days.empty:
        out["rmse_raw"] = float(np.sqrt(np.mean((days["raw_ens"] - days["observed_high"]) ** 2)))
        out["rmse_model"] = float(np.sqrt(np.mean((days["mu"] - days["observed_high"]) ** 2)))
        z = (days["observed_high"] - days["mu"]) / days["sigma"]
        out["coverage80"] = float((np.abs(z) < 1.2816).mean())
    if not q.empty:
        out["brier_market"] = brier(q["mid"], q["result"])
        out["brier_model"] = brier(q["p_model"], q["result"])
        out["bss_model"] = 1 - out["brier_model"] / out["brier_market"]
        qb = q[q["p_blend"].notna()]
        if len(qb):
            out["bss_blend"] = 1 - brier(qb["p_blend"], qb["result"]) / brier(qb["mid"], qb["result"])
    out["trades"] = int(len(trades))
    if len(trades):
        out["win_rate"] = float(trades["won"].mean())
        out["pnl"] = float(trades["pnl"].sum())
        out["pnl_per_trade"] = float(trades["pnl"].mean())
        out["ci90"] = bootstrap_ci(trades)
    out["verdict"] = verdict(out)
    return out


def verdict(s):
    """'edge' only when the out-of-sample record is clearly positive."""
    if s.get("trades", 0) >= 30 and s.get("ci90", [0, 0])[0] > 0 and s.get("bss_blend", -1) > 0:
        return "edge"
    if s.get("trades", 0) >= 30 and (s.get("ci90", [0, 0])[1] < 0 or s.get("bss_blend", 0) < -0.02):
        return "no edge"
    return "unproven"


def run(cities):
    preds = walk_forward(cities)
    trades = simulate(preds, "p_blend")
    model_trades = simulate(preds, "p_model")

    summary = {"generated": pd.Timestamp.now().isoformat(timespec="minutes"),
               "period": [f"{preds['date'].min():%Y-%m-%d}", f"{preds['date'].max():%Y-%m-%d}"],
               "overall": summarize(preds, trades),
               "model_only": summarize(preds, model_trades),
               "cities": {}}
    for series, rows in preds.groupby("series"):
        s = summarize(rows, trades[trades["series"] == series])
        s["city"] = cities[series]["city"]
        summary["cities"][series] = s
    daily_pnl = trades.groupby("date")["pnl"].sum().sort_index().cumsum()
    summary["equity"] = [[f"{d:%Y-%m-%d}", round(float(v), 2)] for d, v in daily_pnl.items()]

    q = preds[has_quote(preds) & preds["p_model"].notna()]
    blend = MarketBlend().fit(q["p_model"], q["mid"], q["result"])
    summary["blend_weights"] = blend.weights

    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    preds.to_csv(os.path.join(config.RESULTS_DIR, "predictions.csv"), index=False)
    trades.to_csv(os.path.join(config.RESULTS_DIR, "trades.csv"), index=False)
    with open(os.path.join(config.RESULTS_DIR, "backtest.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    with open(os.path.join(config.RESULTS_DIR, "blend.pkl"), "wb") as fh:
        pickle.dump(blend, fh)
    print_summary(summary)
    return summary


def print_summary(s):
    o = s["overall"]
    print(f"\nOut-of-sample {s['period'][0]} to {s['period'][1]}: {o['days']} city-days, "
          f"{o['markets']} markets")
    print(f"{'series':<13}{'city':<24}{'RMSE°F':>7}{'BSS mdl':>9}{'BSS bld':>9}"
          f"{'trades':>8}{'P&L $':>9}{'90% CI':>18}  verdict")
    for series, c in sorted(s["cities"].items()):
        ci = c.get("ci90")
        print(f"{series:<13}{c['city'][:23]:<24}{c.get('rmse_model', np.nan):>7.2f}"
              f"{c.get('bss_model', np.nan):>+9.3f}{c.get('bss_blend', np.nan):>+9.3f}"
              f"{c['trades']:>8}{c.get('pnl', 0):>9.2f}"
              f"{(f'{ci[0]:.0f} to {ci[1]:.0f}' if ci else '-'):>18}  {c['verdict']}")
    print(f"{'ALL':<37}{o.get('rmse_model', np.nan):>7.2f}{o.get('bss_model', np.nan):>+9.3f}"
          f"{o.get('bss_blend', np.nan):>+9.3f}{o['trades']:>8}{o.get('pnl', 0):>9.2f}")
    print("\nBSS = Brier skill score vs the market price (>0 beats the market). "
          "Trades use $1 contracts, taker fees included.")
