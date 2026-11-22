"""Plain-language insights from the entry-time backtest, plus how live paper picks compare.

write() is called by timing.run with every entry time's walk-forward predictions and trades and
saves results/insights.json. compare_live() runs at site build time against the picks logs.
Money figures assume a flat $100 per bet (profit per $1 staked x 100), after Kalshi's fees.
"""
import json
import os

import numpy as np
import pandas as pd

import config

STAKE = 100
RECENT_DAYS = 150
TARGET_PICKS = 300
PATH = os.path.join(config.RESULTS_DIR, "insights.json")


def _ret(trades):
    """Profit per $1 staked on each bet (pnl is per $1 contract bought at `price`)."""
    return trades["pnl"] / trades["price"]


def _per_year(trades, days):
    return float(_ret(trades).sum() * STAKE / max(days, 1) * 365) if len(trades) else 0.0


def _year_range(trades, start, end, n=3000, seed=0):
    """90% range for a year's profit at $STAKE a bet, resampling calendar days (with no-bet days)."""
    days = pd.date_range(start, end)
    daily = pd.Series(0.0, index=days)
    if len(trades):
        s = (_ret(trades) * STAKE).groupby(trades["date"]).sum()
        daily.loc[s.index.intersection(days)] = s.loc[s.index.intersection(days)]
    sims = np.random.default_rng(seed).choice(daily.values, size=(n, 365)).sum(axis=1)
    return [float(np.percentile(sims, 5)), float(np.percentile(sims, 95))]


def slot_insights(preds, trades):
    t = trades.copy()
    t["date"] = pd.to_datetime(t["date"])
    start, end = preds["date"].min(), preds["date"].max()
    span = (end - start).days + 1
    if t.empty:
        return {"bets": 0, "span_days": span}
    t["ret"] = _ret(t)
    per_day = t.groupby("date").size()
    month = t.groupby(t["date"].dt.to_period("M"))["ret"].sum() * STAKE
    recent = t[t["date"] > end - pd.Timedelta(days=RECENT_DAYS)]
    best = t.sort_values("ev", ascending=False).groupby("date").head(1) if "ev" in t else t.iloc[:0]
    by_city = (t.groupby("series")["ret"].sum() * STAKE).sort_values(ascending=False)
    city_name = t.groupby("series")["city"].first() if "city" in t else pd.Series(dtype=str)
    half = t["date"].dt.year.astype(str) + np.where(t["date"].dt.month <= 6, " Jan–Jun", " Jul–Dec")
    halves = {h: {"bets": int(len(g)), "ret": float(g["ret"].mean())} for h, g in t.groupby(half)}

    def without(drop):
        rest = t[~t["series"].isin(drop)]
        return {"cities": [str(city_name.get(s, s)) for s in drop], "ret": float(rest["ret"].mean()) if len(rest) else 0.0,
                "per_year": _per_year(rest, span), "range": _year_range(rest, start, end)}

    return {
        "span_days": span, "bets": int(len(t)), "bets_per_day": len(t) / span,
        "no_bet_days": float(1 - len(per_day) / span), "median_bets_on_bet_days": float(per_day.median()),
        "max_bets_day": int(per_day.max()),
        "ret": float(t["ret"].mean()), "ret_sd": float(t["ret"].std()), "avg_price": float(t["price"].mean()),
        "per_bet": float(t["ret"].mean() * STAKE), "per_day": float(t["ret"].sum() * STAKE / span),
        "per_year": _per_year(t, span), "range": _year_range(t, start, end),
        "recent_per_year": _per_year(recent, RECENT_DAYS), "recent_ret": float(recent["ret"].mean()) if len(recent) else 0.0,
        "worst_month": float(month.min()), "best_month": float(month.max()),
        "losing_months": int((month < 0).sum()), "months": int(len(month)),
        "best_only": {"per_year": _per_year(best, span), "ret": float(best["ret"].mean()) if len(best) else 0.0,
                      "bets_per_year": len(best) / span * 365},
        "without_top1": without(list(by_city.index[:1])), "without_top3": without(list(by_city.index[:3])),
        "top_cities": [{"city": str(city_name.get(s, s)), "per_year": float(v / span * 365)} for s, v in by_city.head(3).items()],
        "bottom_cities": [{"city": str(city_name.get(s, s)), "per_year": float(v / span * 365)}
                          for s, v in by_city.tail(3)[::-1].items()],
        "halves": halves,
    }


def write(results):
    """results: {slot: (label, preds, trades)}"""
    out = {"generated": pd.Timestamp.now().isoformat(timespec="minutes"), "stake": STAKE,
           "recent_days": RECENT_DAYS, "entry_times": {}}
    for slot, (label, preds, trades) in results.items():
        out["entry_times"][slot] = {"label": label, **slot_insights(preds, trades)}
    os.makedirs(config.RESULTS_DIR, exist_ok=True)
    json.dump(out, open(PATH, "w"), indent=2, default=float)
    return out


def load():
    return json.load(open(PATH)) if os.path.exists(PATH) else None


def compare_live(log, expected):
    """How settled paper picks compare with the backtest's profit per $1 staked."""
    settled = log[log["won"].notna() & log["price"].notna()] if log is not None and not log.empty else pd.DataFrame()
    n = len(settled)
    res = {"n": n, "target": TARGET_PICKS, "expected": expected.get("ret"), "actual": None, "band": None,
           "status": "waiting"}
    if n == 0 or expected.get("ret") is None:
        return res
    actual = float(_ret(settled).mean())
    sd = expected.get("ret_sd") or float(_ret(settled).std() or 1.0)
    half = 1.645 * sd / np.sqrt(n)
    res.update(actual=actual, band=[expected["ret"] - half, expected["ret"] + half],
               profit_100=float(_ret(settled).sum() * STAKE))
    if n < 30:
        res["status"] = "too early"
    elif actual < expected["ret"] - half:
        res["status"] = "behind"
    elif actual < 0:
        res["status"] = "losing, within luck"
    else:
        res["status"] = "on track"
    return res
