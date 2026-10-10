#!/usr/bin/env python3
"""Builds data.json for Capex Atlas using yfinance.

Reads the company list straight from capex-atlas.html (the CO_START/CO_END block),
pulls each company's latest annual capex and free cash flow from Yahoo Finance,
converts to USD billions, and writes data.json. Companies that fail this run keep
their previous values, so one bad day on Yahoo never wipes the dashboard.
"""
import datetime as dt
import json
import os
import re
import sys
import time

import requests
import yfinance as yf

HTML = sys.argv[1] if len(sys.argv) > 1 else "capex-atlas.html"
OUT = sys.argv[2] if len(sys.argv) > 2 else "data.json"
DEFAULT_CUR = {"US": "USD", "CN": "CNY", "DE": "EUR", "JP": "JPY", "IN": "INR"}
CAPEX_ROWS = ["Capital Expenditure", "Capital Expenditure Reported", "Purchase Of PPE"]


def load_companies(path):
    html = open(path, encoding="utf-8").read()
    m = re.search(r"/\*CO_START\*/(\[.*?\])/\*CO_END\*/", html, re.S)
    if not m:
        raise SystemExit("CO_START/CO_END block not found in " + path)
    return [{"n": r[0], "t": r[1], "c": r[2]} for r in json.loads(m.group(1))]


def get_rates():
    r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("result") != "success":
        raise RuntimeError("FX API failed")
    return j["rates"]


def first_row(df, names):
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return s.sort_index(ascending=False)
    return None


def fetch_one(co, rates):
    t = yf.Ticker(co["t"])
    cf = t.cashflow
    if cf is None or cf.empty:
        return None
    capex = first_row(cf, CAPEX_ROWS)
    if capex is None:
        return None
    col = capex.index[0]
    cur = None
    try:
        cur = t.get_info().get("financialCurrency")
    except Exception:
        pass
    cur = cur or DEFAULT_CUR[co["c"]]
    rate = rates.get(cur)
    if not rate:
        return None
    fcf = first_row(cf, ["Free Cash Flow"])
    fcf_v = None
    if fcf is not None and col in fcf.index:
        fcf_v = round(float(fcf[col]) / rate / 1e9, 3)
    return {
        "cap": round(abs(float(capex.iloc[0])) / rate / 1e9, 3),
        "fcf": fcf_v,
        "fy": str(getattr(col, "year", col)),
    }


def main():
    cos = load_companies(HTML)
    rates = get_rates()
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("companies", {})
        except Exception:
            pass
    data, failed = dict(old), []
    for i, co in enumerate(cos, 1):
        res = None
        for attempt in range(2):
            try:
                res = fetch_one(co, rates)
                break
            except Exception as e:
                time.sleep(2 + attempt * 3)
        if res:
            data[co["t"]] = res
        else:
            failed.append(co["t"])
        if i % 25 == 0:
            print(f"{i}/{len(cos)} done, {len(failed)} failed", flush=True)
        time.sleep(0.4)
    ok = len(cos) - len(failed)
    print(f"fresh data for {ok}/{len(cos)}; failed: {failed}")
    if ok < len(cos) * 0.3:
        raise SystemExit("Too few companies returned data (Yahoo may be blocking); keeping the old data.json")
    out = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "fresh": ok,
        "failed": failed,
        "companies": data,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
