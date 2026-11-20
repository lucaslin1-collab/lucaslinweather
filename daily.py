"""Today's picks across every city, a paper-trading log, and the data for the dashboard."""
import json
import os
import pickle

import numpy as np
import pandas as pd

import config
from data_store import load_weather, local_today, market_row
from model import HighTempModel, bracket_label, bracket_mid
from strategy import has_quote, select, settle_pnl


def picks_log_path():
    return os.path.join(config.DATA_DIR, "picks_log.csv")


def load_backtest():
    path = os.path.join(config.RESULTS_DIR, "backtest.json")
    blend_path = os.path.join(config.RESULTS_DIR, "blend.pkl")
    summary = json.load(open(path)) if os.path.exists(path) else None
    blend = pickle.load(open(blend_path, "rb")) if os.path.exists(blend_path) else None
    return summary, blend


def city_today(city, weather, rows, blend, verdict, today):
    """Forecast and bracket probabilities for one city's market today."""
    panel = {"series": city["series"], "city": city["city"], "station": city["station"],
             "date": f"{today:%Y-%m-%d}", "verdict": verdict, "brackets": [], "note": ""}
    train = weather.dropna(subset=["observed_high"]) if "observed_high" in weather else weather.iloc[:0]
    if len(train) < config.MIN_TRAIN_DAYS:
        panel["note"] = "Not enough weather history to train yet."
        return panel, rows.iloc[:0]
    model = HighTempModel().fit(train)
    if today not in weather.index:
        panel["note"] = "No forecast for today yet. Run again after the morning model runs."
        return panel, rows.iloc[:0]
    mu, sigma = model.predict_dist(weather.loc[[today]])
    if np.isnan(mu.iloc[0]):
        panel["note"] = "Forecast data incomplete for today."
        return panel, rows.iloc[:0]
    panel["mu"], panel["sigma"] = round(float(mu.iloc[0]), 1), round(float(sigma.iloc[0]), 1)
    if rows.empty:
        panel["note"] = "No open market for today."
        return panel, rows

    rows = rows.copy()
    rows["p_model"] = model.bracket_probs(rows, weather.loc[[today]])
    rows["mid"] = (rows["yes_bid"] + rows["yes_ask"]) / 2
    rows["p_final"] = rows["p_model"]
    ok = has_quote(rows)
    if blend is not None and ok.any():
        rows.loc[ok, "p_final"] = blend.predict(rows.loc[ok, "p_model"], rows.loc[ok, "mid"])
    rows["label"] = [bracket_label(t, f, c) for t, f, c in
                     zip(rows["strike_type"], rows["floor_strike"], rows["cap_strike"])]
    rows["center"] = [bracket_mid(t, f, c) for t, f, c in
                      zip(rows["strike_type"], rows["floor_strike"], rows["cap_strike"])]
    rows = rows.sort_values("center")
    if rows["mid"].notna().all() and rows["mid"].sum() > 0:
        weights = rows["mid"] / rows["mid"].sum()
        panel["market_mean"] = round(float((weights * rows["center"]).sum()), 1)
    panel["brackets"] = [
        {"label": r.label, "model": round(float(r.p_final), 3),
         "market": None if np.isnan(r.mid) else round(float(r.mid), 3)}
        for r in rows.itertuples()]
    return panel, rows


def compute_today(cities, weather_by_series, open_by_series, blend, summary):
    verdicts = {s: c["verdict"] for s, c in (summary or {}).get("cities", {}).items()}
    panels, candidates = [], []
    for series, city in cities.items():
        today = local_today(city)
        rows = open_by_series.get(series, pd.DataFrame())
        if not rows.empty:
            rows = rows[rows["date"] == today]
        panel, priced = city_today(city, weather_by_series.get(series, pd.DataFrame()), rows,
                                   blend, verdicts.get(series, "unproven"), today)
        panels.append(panel)
        if not priced.empty:
            candidates.append(priced.assign(city=city["city"], verdict=panel["verdict"]))
    picks = pd.DataFrame()
    if candidates:
        picks = select(pd.concat(candidates, ignore_index=True), "p_final")
    return panels, picks


def fetch_open(client, cities):
    out = {}
    for series, city in cities.items():
        try:
            rows = [r for r in (market_row(m, series, city["fee_multiplier"])
                                for m in client.open_markets(series)) if r]
        except Exception as err:  # noqa: BLE001
            print(f"  {series}: couldn't load open markets ({err})")
            rows = []
        out[series] = pd.DataFrame(rows)
    return out


# --- paper-trading log ------------------------------------------------------------

LOG_COLS = ["date", "series", "city", "ticker", "label", "side", "price", "prob", "ev",
            "stake_pct", "verdict", "fee_mult", "logged_at", "won", "pnl"]


def load_log():
    path = picks_log_path()
    if not os.path.exists(path):
        return pd.DataFrame(columns=LOG_COLS)
    return pd.read_csv(path, parse_dates=["date"])


def log_picks(picks):
    """Record each pick once, at the price first seen (no hindsight re-pricing)."""
    log = load_log()
    if not picks.empty:
        new = picks.assign(logged_at=pd.Timestamp.now().isoformat(timespec="minutes"),
                           won=np.nan, pnl=np.nan)[LOG_COLS]
        log = pd.concat([log, new], ignore_index=True).drop_duplicates(["date", "ticker"], keep="first")
    os.makedirs(config.DATA_DIR, exist_ok=True)
    log.to_csv(picks_log_path(), index=False)
    return log


def settle_log(client):
    """Fill in results for past picks whose markets have resolved."""
    log = load_log()
    pending = log[log["won"].isna() & (log["date"] < pd.Timestamp.now().normalize())]
    for idx, row in pending.iterrows():
        try:
            result = client.market(row["ticker"]).get("result")
        except Exception:  # noqa: BLE001
            continue
        if result in ("yes", "no"):
            won, pnl = settle_pnl(row["side"], row["price"], int(result == "yes"), row["fee_mult"])
            log.loc[idx, ["won", "pnl"]] = [won, pnl]
    log.to_csv(picks_log_path(), index=False)
    return log


def print_picks(picks):
    if picks.empty:
        print("\nNo picks today: nothing cleared the edge, price and spread filters.")
        return
    print(f"\n{'city':<24}{'bracket':<10}{'side':<5}{'price':>7}{'prob':>7}{'EV':>7}{'stake':>7}  verdict")
    for r in picks.itertuples():
        print(f"{r.city[:23]:<24}{r.label:<10}{r.side:<5}{r.price:>7.2f}{r.prob:>7.2f}"
              f"{r.ev:>+7.2f}{r.stake_pct:>7.1%}  {r.verdict}")
    print("\nEV = expected profit per $1 contract after fees. Stake = suggested share of bankroll "
          f"({config.KELLY_FRACTION:g}x Kelly, capped at {config.MAX_STAKE_PCT:.0%}).")


def run(client, cities, update=True):
    from data_store import update_markets, update_weather
    if update:
        print("Updating weather ...")
        from budget import out_of_time
        for series, city in cities.items():
            if out_of_time(reserve_min=60):
                print("  time budget reached; remaining cities continue next run")
                break
            try:
                update_weather(city)
            except Exception as err:  # noqa: BLE001
                print(f"  {series}: weather update failed ({err})")
        print("Updating settled markets ...")
        try:
            update_markets(client, cities)
        except Exception as err:  # noqa: BLE001
            print(f"  market update failed ({err})")
        try:
            import intl_watch
            intl_watch.check(client)
        except Exception as err:  # noqa: BLE001
            print(f"  international check failed ({err})")
    summary, blend = load_backtest()
    if summary is None:
        print("No backtest yet: every city shows as 'unproven'. Run `python hightemp.py backtest`.")
    weather = {s: load_weather(s) for s in cities}
    print("Pricing today's markets ...")
    panels, picks = compute_today(cities, weather, fetch_open(client, cities), blend, summary)
    log = settle_log(client)
    log = log_picks(picks)
    print_picks(picks)
    return panels, picks, log, summary
