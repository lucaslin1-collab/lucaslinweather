"""Save Kalshi's own description of every daily-temperature series: series fields, plus one
market's rules, so settlement sources and stations can be checked by hand."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kalshi_client import KalshiPublic  # noqa: E402

c = KalshiPublic()
out = {}
for s in c.weather_series():
    t = s.get("ticker", "")
    if not re.match(r"^KX(HIGH|LOW|TEMP)", t):
        continue
    entry = {"series": s}
    for status in ("open", "settled"):
        try:
            ms = c.get("/markets", series_ticker=t, status=status, limit=2).get("markets", [])
        except Exception as err:  # noqa: BLE001
            ms, entry[f"{status}_error"] = [], str(err)
        if ms:
            entry[f"sample_{status}"] = ms
            break
    try:  # archived (older) markets, e.g. seasonal international series
        hist = c.get("/historical/markets", series_ticker=t, limit=200).get("markets", [])
    except Exception as err:  # noqa: BLE001
        hist, entry["historical_error"] = [], str(err)
    if hist:
        dates = sorted(m.get("close_time", "") for m in hist)
        entry["historical"] = {"count_first_page": len(hist), "first_close": dates[0], "last_close": dates[-1],
                               "sample": hist[:2]}
    try:
        recent = c.get("/markets", series_ticker=t, limit=200).get("markets", [])
        if recent:
            ds = sorted(m.get("close_time", "") for m in recent)
            entry["any_status"] = {"count_first_page": len(recent), "first_close": ds[0], "last_close": ds[-1],
                                   "statuses": sorted({m.get("status") for m in recent})}
            entry.setdefault("sample_any", recent[:2])
    except Exception:  # noqa: BLE001
        pass
    out[t] = entry
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/series_info.json", "w"), indent=2, sort_keys=True)
print(len(out), "temperature series saved")
