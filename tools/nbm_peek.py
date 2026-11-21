"""Dump raw NBM text rows (runtime, ftime, txn, xnd) for a few stations/days, to check how the
daytime high is listed. Writes data/nbm_peek.json."""
import io
import json

import pandas as pd
import requests

out = {}
for st, day in [("KPHX", "2025-07-15"), ("KLAX", "2025-07-15"), ("KDEN", "2025-01-15"), ("KPHX", "2026-11-18"), ("KNYC", "2025-07-15")]:
    r = requests.get("https://mesonet.agron.iastate.edu/cgi-bin/request/mos.py",
                     params={"station": st, "model": "NBS", "sts": f"{day}T00:00Z", "ets": f"{day}T23:59Z", "format": "csv"},
                     timeout=120)
    df = pd.read_csv(io.StringIO(r.text))
    print(st, day, "http", r.status_code, "rows", len(df), "cols", list(df.columns)[:12], flush=True)
    if "txn" not in df:
        out[f"{st} {day}"] = {"columns": list(df.columns), "head": r.text[:600]}
        continue
    keep = [c for c in ("ftime", "txn", "xnd", "tmp") if c in df]
    out[f"{st} {day}"] = {rt: g[keep].values.tolist() for rt, g in df.groupby("runtime")}
json.dump(out, open("data/nbm_peek.json", "w"), indent=1, default=str)
