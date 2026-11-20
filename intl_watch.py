"""Watch Kalshi's international daily-high series. The AI doesn't price them yet; when any
of them opens markets, the site says so and the data is saved for adding them properly."""
import json
import os
import re

import config

INTL = re.compile(r"^KXHIGHT[A-JL-Z][A-Z]{3}$")   # ICAO-coded series outside the US (US codes start with K)


def check(client):
    found = {}
    try:
        series = [s for s in client.weather_series() if INTL.match(s.get("ticker", ""))]
    except Exception as err:  # noqa: BLE001
        print(f"  international check skipped ({err})")
        return None
    for s in series:
        try:
            ms = client.get("/markets", series_ticker=s["ticker"], status="open", limit=50).get("markets", [])
        except Exception:  # noqa: BLE001
            continue
        if ms:
            found[s["ticker"]] = {"title": s.get("title", "").strip(), "open_markets": len(ms),
                                  "sample_rules": ms[0].get("rules_primary", "")}
    result = {"series_listed": len(series), "open": found}
    os.makedirs(config.DATA_DIR, exist_ok=True)
    json.dump(result, open(os.path.join(config.DATA_DIR, "international_watch.json"), "w"), indent=2)
    print(f"  international series: {len(series)} listed, {len(found)} with open markets")
    return result


def load():
    p = os.path.join(config.DATA_DIR, "international_watch.json")
    return json.load(open(p)) if os.path.exists(p) else None
