"""Simulated cities, forecasts and markets for `python hightemp.py demo`.

The numbers mean nothing. Every fake market reads the same public forecasts the AI
does. "Sharp" cities add private information on top, so the AI can't beat them;
"lazy" cities lean on stale forecasts, so it can. The demo shows both outcomes.
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

import config
from cities import builtin_cities
from data_store import local_today, markets_path, save_weather
from model import bracket_bounds, implied_result

DEMO_CITIES = {  # series -> how much the fake market relies on stale forecasts (0 = sharp)
    "KXHIGHNY": 1.0, "KXHIGHCHI": 0.6, "KXHIGHMIA": 0.0, "KXHIGHAUS": 0.9,
    "KXHIGHDEN": 0.15, "KXHIGHLAX": 0.0, "KXHIGHTPHX": 0.0, "KXHIGHTSEA": 0.4,
}


def make(seed=21):
    rng = np.random.default_rng(seed)
    cities = {s: c for s, c in builtin_cities().items() if s in DEMO_CITIES}
    open_markets = {}
    for series, city in cities.items():
        today = local_today(city)
        dates = pd.date_range(config.WEATHER_START, today, freq="D")
        doy = dates.dayofyear.values
        mean = 84 - 1.4 * (city["lat"] - 25.8)
        amp = 7 + 0.7 * (city["lat"] - 25.8)
        clim = mean + amp * np.sin(2 * np.pi * (doy - 110) / 365.25)
        anom = np.zeros(len(dates))
        for i in range(1, len(dates)):
            anom[i] = 0.6 * anom[i - 1] + rng.normal(0, 5.5)
        truth = np.round(clim + anom)

        shared = rng.normal(0, 1.4, len(dates))
        w = pd.DataFrame(index=dates)
        w.index.name = "date"
        for model, bias, sd in zip(config.FORECAST_MODELS, rng.normal(0, 1.2, 4), [2.4, 2.0, 2.6, 3.0]):
            w[f"fc1_{model}"] = truth + bias + shared + rng.normal(0, sd, len(dates))
            w[f"fc2_{model}"] = w[f"fc1_{model}"] + rng.normal(0, 1.8, len(dates))
        w["observed_high"] = np.where(dates < today, truth, np.nan)
        save_weather(series, w)

        # The market's view: public forecasts (bias-corrected) combined with private
        # information, partly replaced by a stale forecast in "lazy" cities.
        ens = w[[c for c in w if c.startswith("fc1_")]].mean(axis=1).values
        ens = ens - np.mean(ens - truth)
        stale = w[[c for c in w if c.startswith("fc2_")]].mean(axis=1).values
        private = truth + rng.normal(0, 2.5, len(dates))
        v_pub, v_priv = np.var(ens - truth), 2.5 ** 2
        sharp = (ens / v_pub + private / v_priv) / (1 / v_pub + 1 / v_priv)
        lazy = DEMO_CITIES[series]
        market_view = lazy * stale + (1 - lazy) * sharp
        mkt_sd = float(np.std(market_view - truth))   # priced with its true error: calibrated

        rows, today_rows = [], []
        for i, d in enumerate(dates[-config.MARKET_HISTORY_DAYS:], start=len(dates) - config.MARKET_HISTORY_DAYS):
            mkt_mu = market_view[i]
            center = 2 * round((mkt_mu + rng.normal(0, 1)) / 2)   # brackets set from a forecast
            brackets = [("less", np.nan, center - 3)] + \
                [("between", lo, lo + 1) for lo in (center - 3, center - 1, center + 1, center + 3)] + \
                [("greater", center + 4, np.nan)]
            event = f"{series}-{d:%y%b%d}".upper()
            for strike_type, floor, cap in brackets:
                lo, hi = bracket_bounds(strike_type, floor, cap)
                p = stats.norm.cdf((hi - mkt_mu) / mkt_sd) - stats.norm.cdf((lo - mkt_mu) / mkt_sd)
                mid = np.clip(p + rng.normal(0, 0.01), 0.02, 0.98)
                half = rng.choice([0.01, 0.01, 0.02, 0.03])
                tag = f"B{floor + 0.5:g}" if strike_type == "between" else f"T{(cap if strike_type == 'less' else floor):g}"
                row = {"series": series, "ticker": f"{event}-{tag}", "event_ticker": event, "date": d,
                       "strike_type": strike_type, "floor_strike": floor, "cap_strike": cap,
                       "settled_ts": None, "fee_mult": 1.0,
                       "yes_bid": round(max(mid - half, 0.01), 2), "yes_ask": round(min(mid + half, 0.99), 2)}
                if d < today:
                    rows.append({**row, "result": implied_result(strike_type, floor, cap, truth[i])})
                else:
                    today_rows.append({**row, "result": None})
        os.makedirs(os.path.dirname(markets_path(series)), exist_ok=True)
        pd.DataFrame(rows).to_csv(markets_path(series), index=False)
        open_markets[series] = pd.DataFrame(today_rows)
    return cities, open_markets
