"""History backfill, run by its own nightly job so it never delays the 8 a.m. picks.

For every city it:
  1. lists settled markets back to MARKET_HISTORY_START and stores the ones older than the
     daily run's file in data/markets_hist/ (with their 8 a.m. price),
  2. downloads each market's price at every entry time in ENTRY_TIMES -> data/quotes/,
  3. works out the NWS station forecast (NBM) as it stood at each entry time -> data/nbm_slots/.
It writes only those folders, so it can't collide with the daily run's files, and it stops at
the time budget and continues on the next run.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import config
from budget import out_of_time
from data_store import _dir, close_price, load_markets, local_today, market_row
from kalshi_client import to_unix


def _path(kind, series):
    return os.path.join(_dir(kind), f"{series}.csv")


def _load(kind, series, **kw):
    p = _path(kind, series)
    return pd.read_csv(p, **kw) if os.path.exists(p) else pd.DataFrame()


def _save(kind, series, df):
    os.makedirs(_dir(kind), exist_ok=True)
    df.to_csv(_path(kind, series), index=False)


def _meta():
    p = os.path.join(config.DATA_DIR, "backfill_meta.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _save_meta(m):
    json.dump(m, open(os.path.join(config.DATA_DIR, "backfill_meta.json"), "w"), indent=2)


def entry_ts(date, city, hours):
    """Unix time of an entry time: `hours` from local midnight of the market's day."""
    t = (pd.Timestamp(date) + pd.Timedelta(hours=hours)).tz_localize(city["tz"], nonexistent="shift_forward",
                                                                     ambiguous=False)
    return int(t.timestamp())


# --- 1. older markets ------------------------------------------------------------------

def backfill_markets(client, series, city, meta):
    """Store settled markets older than the daily run's file. Lists the full history once."""
    if meta.get(series, {}).get("history_listed"):
        return
    recent = load_markets(series)
    first_recent = recent["date"].min() if not recent.empty else local_today(city)
    start = pd.Timestamp(config.MARKET_HISTORY_START)
    raw = client.settled_markets(series, include_historical=True)
    rows = [r for r in (market_row(m, series, city["fee_multiplier"]) for m in raw) if r]
    rows = [r for r in rows if start <= r["date"] < first_recent and r["result"] is not None]
    old = _load("markets_hist", series, parse_dates=["date"])
    known = set(old["ticker"]) if not old.empty else set()
    new = pd.DataFrame([r for r in rows if r["ticker"] not in known])
    if len(new):
        new = new.assign(yes_bid=None, yes_ask=None)
        out = pd.concat([old, new], ignore_index=True) if not old.empty else new
        _save("markets_hist", series, out.sort_values(["date", "ticker"]))
    earliest = min((r["date"] for r in rows), default=None)
    meta.setdefault(series, {}).update(history_listed=True, older_markets=len(rows),
                                       earliest=f"{earliest:%Y-%m-%d}" if earliest is not None else None)
    print(f"  {series}: {len(rows)} older settled markets (from {meta[series]['earliest']})")


# --- 2. prices at every entry time --------------------------------------------------

def _pick(candles, ts):
    usable = [c for c in candles if c.get("end_period_ts", 0) <= ts]
    if not usable:
        return None, None
    last = max(usable, key=lambda c: c["end_period_ts"])
    return close_price(last.get("yes_bid")), close_price(last.get("yes_ask"))


def slot_quotes(client, series, city, cutoff_ts, chunk=600):
    """Price at each entry time for markets that don't have them yet. Saves every `chunk`."""
    recent = load_markets(series)
    hist = _load("markets_hist", series, parse_dates=["date"])
    mk = pd.concat([x for x in (recent, hist) if not x.empty], ignore_index=True)
    if mk.empty:
        return 0
    mk = mk.drop_duplicates("ticker")
    have = _load("quotes", series)
    done = set(have["ticker"]) if not have.empty else set()
    todo = mk[~mk["ticker"].isin(done)].sort_values("date", ascending=False)
    if todo.empty:
        return 0
    slots = {k: v[0] for k, v in config.ENTRY_TIMES.items()}
    lo_h, hi_h = min(slots.values()) - 3, max(slots.values())
    added = 0
    for i in range(0, len(todo), chunk):
        if out_of_time(reserve_min=20):
            print(f"    {series}: time budget reached, {len(todo) - i} markets left for the next run")
            break
        part = todo.iloc[i:i + chunk]
        rows = []
        archived = part["settled_ts"].fillna(0) < (cutoff_ts or 0)

        # recent markets: 100 per request
        for date, day in part[~archived].groupby("date"):
            start, end = entry_ts(date, city, lo_h), entry_ts(date, city, hi_h)
            for j in range(0, len(day), 100):
                tick = list(day["ticker"].iloc[j:j + 100])
                try:
                    cs = client.batch_candlesticks(tick, start, end)
                except Exception as err:  # noqa: BLE001
                    print(f"    batch prices failed for {date:%Y-%m-%d}: {err}")
                    continue
                for t in tick:
                    for slot, h in slots.items():
                        bid, ask = _pick(cs.get(t, []), entry_ts(date, city, h))
                        rows.append({"ticker": t, "slot": slot, "yes_bid": bid, "yes_ask": ask})

        # archived markets: one request each, several in parallel under the shared rate limit
        def one(r):
            start, end = entry_ts(r.date, city, lo_h), entry_ts(r.date, city, hi_h)
            try:
                cs = client.historical_candlesticks(r.ticker, start, end)
            except Exception as err:  # noqa: BLE001
                return [{"ticker": r.ticker, "slot": "error", "yes_bid": None, "yes_ask": None, "err": str(err)[:80]}]
            return [{"ticker": r.ticker, "slot": slot, "yes_bid": b, "yes_ask": a}
                    for slot, h in slots.items() for b, a in [_pick(cs, entry_ts(r.date, city, h))]]

        with ThreadPoolExecutor(max_workers=6) as ex:
            for res in ex.map(one, part[archived].itertuples()):
                rows += [x for x in res if x["slot"] != "error"]
        if rows:
            new = pd.DataFrame(rows)[["ticker", "slot", "yes_bid", "yes_ask"]]
            have = pd.concat([have, new], ignore_index=True) if not have.empty else new
            _save("quotes", series, have)
            added += new["ticker"].nunique()
    # fill the 8 a.m. price into the backfilled market file, for the regular backtest
    hist = _load("markets_hist", series, parse_dates=["date"])
    if not hist.empty and not have.empty:
        q8 = have[have["slot"] == "d0_08"].drop_duplicates("ticker", keep="last").set_index("ticker")
        hist["yes_bid"] = hist["ticker"].map(q8["yes_bid"])
        hist["yes_ask"] = hist["ticker"].map(q8["yes_ask"])
        _save("markets_hist", series, hist)
    return added


# --- 3. NWS forecast as of each entry time ------------------------------------------

def nbm_slots(city):
    from nbm import fetch_runs, daily_guidance
    series = city["series"]
    have = _load("nbm_slots", series, parse_dates=["date"])
    start = pd.Timestamp(config.WEATHER_START) if have.empty else have["date"].max() - pd.Timedelta(days=5)
    today = local_today(city)
    if not have.empty and have["date"].max() >= today:
        return
    runs = fetch_runs(city["station"], start - pd.Timedelta(days=3), today)
    out = pd.DataFrame(index=pd.DatetimeIndex(pd.date_range(start, today), name="date"))
    for slot, (h, _) in config.ENTRY_TIMES.items():
        g = daily_guidance(runs, city, out.index, decision_hour=h)
        out[f"nbm_txn_{slot}"] = g["nbm_txn"].reindex(out.index)
        out[f"nbm_xnd_{slot}"] = g["nbm_xnd"].reindex(out.index)
    out = out.reset_index()
    if not have.empty:
        out = pd.concat([have[have["date"] < start], out], ignore_index=True)
    _save("nbm_slots", series, out)


# --- run ---------------------------------------------------------------------------------

def run(client, cities):
    meta = _meta()
    cutoff = client.historical_cutoff_ts()
    print("Backfill: older markets ...")
    for series, city in cities.items():
        if out_of_time(reserve_min=60):
            break
        try:
            backfill_markets(client, series, city, meta)
        except Exception as err:  # noqa: BLE001
            print(f"  {series}: listing failed ({err})")
        _save_meta(meta)
    print("Backfill: NWS forecast at each entry time ...")
    for series, city in cities.items():
        if out_of_time(reserve_min=60):
            break
        try:
            nbm_slots(city)
        except Exception as err:  # noqa: BLE001
            print(f"  {series}: NWS forecast failed ({err})")
    print("Backfill: prices at each entry time ...")
    left = 0
    for series, city in cities.items():
        if out_of_time(reserve_min=20):
            left += 1
            continue
        try:
            n = slot_quotes(client, series, city, cutoff)
            print(f"  {series}: prices for {n} markets")
        except Exception as err:  # noqa: BLE001
            print(f"  {series}: prices failed ({err})")
    return complete(cities)


def complete(cities):
    """True when every city has entry-time prices for all its markets and NBM slots."""
    for s in cities:
        mk = pd.concat([x for x in (load_markets(s), _load("markets_hist", s)) if not x.empty], ignore_index=True)
        q = _load("quotes", s)
        if mk.empty or q.empty or not set(mk["ticker"]) <= set(q["ticker"]) or _load("nbm_slots", s).empty:
            return False
    return True
