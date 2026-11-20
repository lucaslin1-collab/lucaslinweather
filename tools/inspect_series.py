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
    out[t] = entry
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/series_info.json", "w"), indent=2, sort_keys=True)
print(len(out), "temperature series saved")
