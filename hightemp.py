"""High-temp AI: forecasts every Kalshi daily-high market, finds mispriced brackets,
and writes a dashboard. It only recommends; it never places orders.

  python hightemp.py demo        try it on simulated data (no internet needed for the data)
  python hightemp.py setup       first run: find cities, download history, backtest (~20-40 min)
  python hightemp.py daily       each morning around 8 local time: update, price today, dashboard
  python hightemp.py backtest    re-run the walk-forward backtest on the data you have
"""
import argparse
import os
import webbrowser

import pandas as pd

import config


def write_dashboard(panels, picks, log, summary, demo=False):
    """Local one-page dashboard plus the multi-page website in site/."""
    from dashboard import render
    import website
    path = os.path.abspath(config.DASHBOARD_PATH)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(panels, picks, log, summary, demo=demo))
    print(f"\nDashboard: {path}")
    website.build(panels, picks, log, summary, demo=demo)
    return path


def cmd_setup(args):
    from cities import discover, save_cities
    from data_store import check_bracket_rules, update_markets, update_weather
    from kalshi_client import KalshiPublic
    import backtest

    client = KalshiPublic()
    print("Finding Kalshi daily-high cities ...")
    cities = discover(client)
    save_cities(cities)
    print(f"  {len(cities)} cities: " + ", ".join(c["city"] for c in cities.values()))

    print("Downloading weather history (forecast archive + official highs) ...")
    from budget import out_of_time
    for n, (series, city) in enumerate(cities.items(), 1):
        if out_of_time(reserve_min=60):
            print("  time budget reached; remaining cities continue next run")
            break
        try:
            update_weather(city)
            print(f"  [{n}/{len(cities)}] {city['city']}")
        except Exception as err:  # noqa: BLE001
            print(f"  [{n}/{len(cities)}] {city['city']}: failed ({err})")

    print(f"Downloading settled markets and their 8am prices (last {config.MARKET_HISTORY_DAYS} days) ...")
    update_markets(client, cities)

    print("Checking bracket rules against Kalshi's settlements ...")
    for series, rate in check_bracket_rules(cities).items():
        flag = "" if rate >= 0.97 else "   <- check model.bracket_bounds() for this city"
        print(f"  {series:<13}{rate:6.1%}{flag}")

    print("Backtesting ...")
    backtest.run(cities)
    print("\nSetup done. Run `python hightemp.py daily` each morning.")


def backtest_is_stale(cities):
    """Re-run when the backtest is a week old or misses cities that now have data."""
    import json
    from data_store import load_all_markets, load_markets, load_weather
    path = os.path.join(config.RESULTS_DIR, "backtest.json")
    if not os.path.exists(path):
        return True
    s = json.load(open(path))
    if s.get("features_version") != config.FEATURES_VERSION:
        return True
    if pd.Timestamp.now() - pd.Timestamp(s.get("generated", "2000-01-01")) > pd.Timedelta(days=6):
        return True
    ready = {c for c in cities if not load_weather(c).empty and not load_markets(c).empty}
    if ready - set(s.get("cities", {})):
        return True
    # re-score when the backfill has added a lot of older markets
    n_now = sum(len(load_all_markets(c)) for c in ready)
    return n_now > 1.15 * s.get("n_markets", n_now)


def cmd_backfill(args):
    """Extend price history and entry-time data, then re-run the timing backtest when useful."""
    import json
    from cities import load_cities
    from kalshi_client import KalshiPublic
    import backfill
    import timing
    cities = load_cities()
    before = backfill._meta()
    done = backfill.run(KalshiPublic(), cities)
    path = os.path.join(config.RESULTS_DIR, "timing.json")
    age_h = (pd.Timestamp.now() - pd.Timestamp(json.load(open(path))["generated"])).total_seconds() / 3600 \
        if os.path.exists(path) else 1e9
    if age_h > 20 or backfill._meta() != before or done:
        print("Timing backtest ...")
        timing.run(cities)
    print("Backfill complete." if done else "Backfill continues on the next run.")


def cmd_backtest(args):
    from cities import load_cities
    import backtest
    cities = load_cities()
    if getattr(args, "if_stale", False) and not backtest_is_stale(cities):
        print("Backtest is up to date.")
        raise SystemExit(10)
    backtest.run(cities)


def cmd_daily(args):
    from cities import load_cities
    from kalshi_client import KalshiPublic
    import daily
    panels, picks, log, summary = daily.run(KalshiPublic(), load_cities(), update=not args.no_update)
    path = write_dashboard(panels, picks, log, summary)
    if args.open:
        webbrowser.open(f"file://{path}")


def cmd_demo(args):
    config.DATA_DIR = os.path.join("demo", "data")
    config.RESULTS_DIR = os.path.join("demo", "results")
    config.DASHBOARD_PATH = os.path.join("demo", "dashboard.html")
    config.SITE_DIR = os.path.join("demo", "site")
    import backtest
    import daily
    import synthetic
    from cities import save_cities

    print("Simulating 8 cities ...")
    cities, open_markets = synthetic.make()
    save_cities(cities)
    summary = backtest.run(cities)
    _, blend = daily.load_backtest()
    weather = {s: daily.load_weather(s) for s in cities}
    panels, picks = daily.compute_today(cities, weather, open_markets, blend, summary)

    # Pretend the last three weeks of backtest picks were logged live, so the
    # track-record section has something to show.
    trades = pd.read_csv(os.path.join(config.RESULTS_DIR, "trades.csv"), parse_dates=["date"])
    recent = trades[trades["date"] >= trades["date"].max() - pd.Timedelta(days=20)].copy()
    recent["city"] = recent["series"].map(lambda s: cities[s]["city"])
    from model import bracket_label
    recent["label"] = [bracket_label(t, f, c) for t, f, c in
                       zip(recent["strike_type"], recent["floor_strike"], recent["cap_strike"])]
    recent["verdict"] = recent["series"].map(lambda s: summary["cities"].get(s, {}).get("verdict", "unproven"))
    recent["stake_pct"], recent["fee_mult"] = 0.0, 1.0
    recent["logged_at"] = recent["date"].dt.strftime("%Y-%m-%dT08:00")
    if os.path.exists(daily.picks_log_path()):
        os.remove(daily.picks_log_path())
    os.makedirs(config.DATA_DIR, exist_ok=True)
    recent[daily.LOG_COLS].to_csv(daily.picks_log_path(), index=False)
    log = daily.log_picks(picks)

    daily.print_picks(picks)
    path = write_dashboard(panels, picks, log, summary, demo=True)
    if args.open:
        webbrowser.open(f"file://{path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    sub.add_parser("backfill").set_defaults(fn=cmd_backfill)
    bt = sub.add_parser("backtest")
    bt.add_argument("--if-stale", action="store_true", help="only run if a week old or cities were added")
    bt.set_defaults(fn=cmd_backtest)
    d = sub.add_parser("daily")
    d.add_argument("--no-update", action="store_true", help="skip downloading new data")
    d.add_argument("--open", action="store_true", help="open the dashboard in your browser")
    d.set_defaults(fn=cmd_daily)
    demo = sub.add_parser("demo")
    demo.add_argument("--open", action="store_true")
    demo.set_defaults(fn=cmd_demo)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
