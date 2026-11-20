"""Local CSV store for weather and market history, updated incrementally."""
import os
from datetime import datetime

import numpy as np
import pandas as pd

import config
from kalshi_client import dollars, to_unix
from model import implied_result
from budget import out_of_time
from weather import fetch_forecasts, fetch_observed

SUPPORTED = {"between", "less", "less_or_equal", "greater", "greater_or_equal"}


def _dir(name):
    return os.path.join(config.DATA_DIR, name)


# --- helpers --------------------------------------------------------------------

def event_date(event_ticker):
    """KXHIGHNY-26NOV12 -> 2026-11-12"""
    return pd.Timestamp(datetime.strptime(event_ticker.rsplit("-", 1)[-1], "%y%b%d"))


def decision_ts(date, city):
    local = pd.Timestamp(date).tz_localize(city["tz"]) + pd.Timedelta(hours=config.DECISION_HOUR_LOCAL)
    return int(local.timestamp())


def local_today(city):
    return pd.Timestamp.now(tz=city["tz"]).normalize().tz_localize(None)


def market_row(m, series, fee_mult):
    if m.get("strike_type") not in SUPPORTED:
        return None
    return {
        "series": series, "ticker": m["ticker"], "event_ticker": m["event_ticker"],
        "date": event_date(m["event_ticker"]), "strike_type": m["strike_type"],
        "floor_strike": m.get("floor_strike"), "cap_strike": m.get("cap_strike"),
        "result": {"yes": 1, "no": 0}.get(m.get("result")),
        "settled_ts": to_unix(m.get("settlement_ts")) or to_unix(m.get("close_time")),
        "yes_bid": dollars(m.get("yes_bid_dollars")), "yes_ask": dollars(m.get("yes_ask_dollars")),
        "fee_mult": fee_mult,
    }


# --- weather --------------------------------------------------------------------

def weather_path(series):
    return os.path.join(_dir("weather"), f"{series}.csv")


def load_weather(series):
    path = weather_path(series)
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, index_col="date", parse_dates=["date"])


def save_weather(series, df):
    os.makedirs(_dir("weather"), exist_ok=True)
    df.sort_index().to_csv(weather_path(series), index_label="date")


def update_weather(city):
    """Fetch forecasts and observed highs from the last stored day (minus a margin) to today."""
    existing = load_weather(city["series"])
    today = local_today(city)
    if existing.empty:
        start = pd.Timestamp(config.WEATHER_START)
    else:
        start = existing.index.max() - pd.Timedelta(days=10)
    fc = fetch_forecasts(city, start, today)
    obs = fetch_observed(city["station"], range(start.year, today.year + 1))
    new = fc.join(obs.loc[start:], how="outer")
    combined = new.combine_first(existing) if not existing.empty else new
    combined = add_nbm(city, combined, existing, today)
    save_weather(city["series"], combined)
    return combined


def add_nbm(city, combined, existing, today):
    """Add the NWS station forecast (NBM) columns; backfill them in full the first time."""
    from nbm import daily_guidance, fetch_runs
    have = existing["nbm_txn"].dropna() if (not existing.empty and "nbm_txn" in existing) else pd.Series(dtype=float)
    start = pd.Timestamp(config.WEATHER_START) if have.empty else have.index.max() - pd.Timedelta(days=5)
    try:
        runs = fetch_runs(city["station"], start - pd.Timedelta(days=2), today)
        g = daily_guidance(runs, city, pd.date_range(start, today))
    except Exception as err:  # noqa: BLE001
        print(f"    {city['series']}: NWS station forecast unavailable ({err})")
        return combined
    if g.empty:
        return combined
    for col in ("nbm_txn", "nbm_xnd", "nbm_age_h"):
        if col not in combined:
            combined[col] = np.nan
        combined.loc[g.index.intersection(combined.index), col] = g[col]
        extra = g.index.difference(combined.index)
        if len(extra):
            combined = pd.concat([combined, g.loc[extra, [col]]]).groupby(level=0).first()
    return combined.sort_index()


# --- markets --------------------------------------------------------------------

def markets_path(series):
    return os.path.join(_dir("markets"), f"{series}.csv")


def load_markets(series):
    path = markets_path(series)
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["date"])


def close_price(q):
    """Closing price in dollars. Live candles use `close_dollars`; archived candles use
    `close` (also dollars). Older formats gave whole cents."""
    q = q or {}
    v = q.get("close_dollars", q.get("close"))
    if v is None or v == "":
        return None
    v = float(v)
    return v / 100 if v > 1 else v


def fetch_decision_quotes(client, rows, cities, cutoff_ts):
    """YES bid/ask from the last hourly candle at or before each market's decision time.
    Recent markets are fetched 100 at a time; archived ones need one call each."""
    rows = rows.copy()
    rows["decision_ts"] = [decision_ts(d, cities[s]) for d, s in zip(rows["date"], rows["series"])]
    archived = rows["settled_ts"].notna() & (cutoff_ts is not None) & (rows["settled_ts"] < (cutoff_ts or 0))
    quotes = {}

    def pick(candles, dts):
        usable = [c for c in candles if c.get("end_period_ts", 0) <= dts]
        if not usable:
            return (None, None)
        last = max(usable, key=lambda c: c["end_period_ts"])
        return close_price(last.get("yes_bid")), close_price(last.get("yes_ask"))

    live = rows[~archived]
    for _, day in live.groupby("date"):
        for i in range(0, len(day), 100):
            chunk = day.iloc[i:i + 100]
            start, end = chunk["decision_ts"].min() - 12 * 3600, chunk["decision_ts"].max()
            try:
                candles = client.batch_candlesticks(list(chunk["ticker"]), start, end)
            except Exception as err:  # noqa: BLE001
                print(f"    batch quotes failed for {chunk['date'].iloc[0]:%Y-%m-%d}: {err}")
                continue
            for r in chunk.itertuples():
                quotes[r.ticker] = pick(candles.get(r.ticker, []), r.decision_ts)

    old = rows[archived]
    for n, r in enumerate(old.itertuples(), 1):
        if out_of_time(reserve_min=25):
            print(f"    time budget reached: {len(old) - n + 1} archived prices left for the next run")
            break
        try:
            candles = client.historical_candlesticks(r.ticker, r.decision_ts - 12 * 3600, r.decision_ts)
            quotes[r.ticker] = pick(candles, r.decision_ts)
        except Exception as err:  # noqa: BLE001
            print(f"    {r.ticker}: {err}")
        if n % 500 == 0:
            print(f"    archived quotes {n}/{len(old)}")

    rows["yes_bid"] = [quotes.get(t, (None, None))[0] for t in rows["ticker"]]
    rows["yes_ask"] = [quotes.get(t, (None, None))[1] for t in rows["ticker"]]
    rows["quote_tried"] = [1 if t in quotes else 0 for t in rows["ticker"]]
    return rows.drop(columns="decision_ts")


def update_markets(client, cities, history_days=None):
    """Add newly settled markets (with their 8am quotes) to each city's market file.
    Each city is saved as soon as it's done, so an interrupted run picks up where it stopped."""
    history_days = history_days or config.MARKET_HISTORY_DAYS
    cutoff = client.historical_cutoff_ts()
    for series, city in cities.items():
        existing = load_markets(series)
        if existing.empty:
            since = local_today(city) - pd.Timedelta(days=history_days)
            raw = client.settled_markets(series, include_historical=True)
        else:
            since = existing["date"].max() - pd.Timedelta(days=2)
            raw = client.settled_markets(series, min_close_ts=int(since.timestamp()),
                                         include_historical=False)
        rows = [r for r in (market_row(m, series, city["fee_multiplier"]) for m in raw) if r]
        rows = [r for r in rows if r["date"] >= since and r["result"] is not None]
        known = set(existing["ticker"]) if not existing.empty else set()
        rows = [r for r in rows if r["ticker"] not in known]
        # markets stored earlier without a price (e.g. an interrupted run) get one more try
        if not existing.empty:
            tried = existing["quote_tried"] if "quote_tried" in existing else pd.Series(0, index=existing.index)
            retry = existing[existing["yes_bid"].isna() & (tried.fillna(0) == 0)]
        else:
            retry = pd.DataFrame()
        print(f"  {series}: {len(rows)} new settled markets" + (f", re-pricing {len(retry)}" if len(retry) else ""))
        if not rows and retry.empty:
            continue
        if out_of_time(reserve_min=25):
            print("    time budget reached; continuing next run")
            break
        todo = pd.concat([pd.DataFrame(rows), retry.drop(columns=["yes_bid", "yes_ask", "quote_tried"], errors="ignore")],
                         ignore_index=True)
        new = fetch_decision_quotes(client, todo, cities, cutoff)
        combined = pd.concat([existing, new], ignore_index=True) if not existing.empty else new
        combined = combined.drop_duplicates("ticker", keep="last").sort_values(["date", "ticker"])
        os.makedirs(_dir("markets"), exist_ok=True)
        combined.to_csv(markets_path(series), index=False)


def check_bracket_rules(cities):
    """Do our bracket rules reproduce Kalshi's settlements? Should be ~100% per city."""
    out = {}
    for series in cities:
        mk, w = load_markets(series), load_weather(series)
        if mk.empty or w.empty or "observed_high" not in w:
            continue
        mk["observed_high"] = mk["date"].map(w["observed_high"])
        mk = mk.dropna(subset=["observed_high", "result"])
        if mk.empty:
            continue
        agree = [implied_result(r.strike_type, r.floor_strike, r.cap_strike, r.observed_high) == r.result
                 for r in mk.itertuples()]
        out[series] = float(np.mean(agree))
    return out
