#!/usr/bin/env python3

-- coding: utf-8 --

“””
00838B TPEx ETF Official Price Diagnostic

Purpose:

1. Test TPEx ETF historical price data only.
2. Do not use stk_wn1430_result.php.
3. Do not use normal OTC stock quote endpoint.
4. Read only tables[].data.
5. Search for 00838B.
6. Validate close price and volume.
7. Do not modify the production price pipeline.
    “””

from future import annotations

import json
import sys
from datetime import date, timedelta

import requests

SYMBOL = “00838B”

ENDPOINT = (
“https://www.tpex.org.tw/web/etf/historical/”
“etf_statistics.php”
)

HEADERS = {
“User-Agent”: (
“Mozilla/5.0 (Windows NT 10.0; Win64; x64) “
“AppleWebKit/537.36 “
“(KHTML, like Gecko) “
“Chrome/131.0 Safari/537.36”
),
“Accept”: “application/json,text/plain,/”,
“Accept-Language”: “zh-TW,zh;q=0.9,en;q=0.8”,
“Referer”: “https://www.tpex.org.tw/”,
}

TIMEOUT = 30

def roc_date(d: date) -> str:
return f”{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}”

def normalize(value) -> str:
if value is None:
return “”

return (
    str(value)
    .replace("\ufeff", "")
    .replace("\u3000", "")
    .strip()
    .upper()
)

def request_json(d: date):
tpex_date = roc_date(d)

params = {
    "l": "zh-tw",
    "d": tpex_date,
}
print()
print("=" * 80)
print(f"TEST DATE: {d.isoformat()}")
print(f"TPEx DATE: {tpex_date}")
print("=" * 80)
print(f"ENDPOINT: {ENDPOINT}")
try:
    response = requests.get(
        ENDPOINT,
        params=params,
        headers=HEADERS,
        timeout=TIMEOUT,
    )
except requests.RequestException as exc:
    print(f"REQUEST ERROR: {exc}")
    return None
print()
print("HTTP")
print(f"status_code: {response.status_code}")
print(
    "content_type: "
    f"{response.headers.get('Content-Type', '')}"
)
print(f"content_length: {len(response.content)}")
if response.status_code != 200:
    print("HTTP STATUS IS NOT 200")
    return None
try:
    payload = response.json()
except Exception as exc:
    print(f"JSON DECODE FAILED: {exc}")
    print()
    print("RESPONSE PREVIEW:")
    print(response.text[:2000])
    return None
return payload

def extract_rows(payload):
“””
Strict data contract:

    root
      tables[]
        data[]
No aaData.
No root data.
No rows.
"""
print()
print("JSON ROOT")
print(f"type: {type(payload).__name__}")
if not isinstance(payload, dict):
    print("JSON ROOT IS NOT DICT")
    return []
print()
print("ROOT KEYS")
for key in payload.keys():
    print(f"  {key}")
tables = payload.get("tables")
print()
print("TABLES")
print(f"type: {type(tables).__name__}")
if not isinstance(tables, list):
    print("tables IS NOT LIST")
    return []
print(f"table_count: {len(tables)}")
rows = []
for table_index, table in enumerate(tables):
    print()
    print("-" * 80)
    print(f"TABLE [{table_index}]")
    if not isinstance(table, dict):
        print(
            f"table type: "
            f"{type(table).__name__}"
        )
        continue
    print("table keys:")
    for key in table.keys():
        print(f"  {key}")
    data = table.get("data")
    print()
    print("tables[].data")
    print(f"type: {type(data).__name__}")
    if not isinstance(data, list):
        print("data IS NOT LIST")
        continue
    print(f"raw rows: {len(data)}")
    for row in data:
        if isinstance(row, list) and row:
            rows.append(
                (
                    table_index,
                    row,
                )
            )
    print(
        "valid rows: "
        f"{sum(1 for x in rows if x[0] == table_index)}"
    )
return rows

def find_symbol(rows):
print()
print(”=” * 80)
print(“GLOBAL SYMBOL SEARCH”)
print(”=” * 80)

found = []
for table_index, row in rows:
    if not row:
        continue
    # ETF historical data may use different column ordering.
    # Search every field for the exact symbol instead of
    # assuming that symbol is always column zero.
    for value in row:
        if normalize(value) == SYMBOL:
            found.append(
                (
                    table_index,
                    row,
                )
            )
            break
return found

def print_row(table_index, row):
print()
print(”=” * 80)
print(f”FOUND IN TABLE: {table_index}”)
print(”=” * 80)

print("RAW ROW:")
print(
    json.dumps(
        row,
        ensure_ascii=False,
        indent=2,
    )
)
print()
print("ROW FIELDS:")
for index, value in enumerate(row):
    print(
        f"  [{index:02d}] {value}"
    )

def validate_row(row):
“””
Validate the discovered row without assuming
a fixed ETF field order.

The diagnostic first prints every field.
Then attempts to identify numeric price fields.
"""
print()
print("=" * 80)
print("VALIDATION")
print("=" * 80)
if not row:
    print("EMPTY ROW")
    return False
symbol_found = any(
    normalize(value) == SYMBOL
    for value in row
)
if not symbol_found:
    print(
        f"SYMBOL {SYMBOL} NOT FOUND IN ROW"
    )
    return False
print(f"OK: symbol {SYMBOL} found in row")
numeric_values = []
for index, value in enumerate(row):
    text = normalize(value)
    if not text:
        continue
    if text in ("-", "--", "N/A", "NULL"):
        continue
    cleaned = (
        text.replace(",", "")
        .replace(" ", "")
    )
    try:
        number = float(cleaned)
    except ValueError:
        continue
    numeric_values.append(
        (
            index,
            value,
            number,
        )
    )
print()
print("NUMERIC FIELDS:")
for index, value, number in numeric_values:
    print(
        f"  [{index:02d}] "
        f"value={value} "
        f"numeric={number}"
    )
if not numeric_values:
    print(
        "ERROR: no numeric fields found"
    )
    return False
print()
print(
    "OK: ETF row contains numeric "
    "market data"
)
return True

def fetch_one_date(d: date) -> bool:
payload = request_json(d)

if payload is None:
    return False
rows = extract_rows(payload)
print()
print("=" * 80)
print("DATA EXTRACTION SUMMARY")
print("=" * 80)
print(
    "source: tables[].data"
)
print(
    f"valid rows: {len(rows)}"
)
if not rows:
    print(
        "ERROR: tables[].data contains "
        "zero valid rows"
    )
    return False
found = find_symbol(rows)
print(
    f"searched symbol: {SYMBOL}"
)
print(
    f"matches: {len(found)}"
)
if not found:
    print()
    print(
        f"ERROR: {SYMBOL} not found "
        "in tables[].data"
    )
    print()
    print("FIRST 30 ROWS:")
    for number, (table_index, row) in enumerate(
        rows[:30],
        start=1,
    ):
        preview = " | ".join(
            str(value)
            for value in row[:8]
        )
        print(
            f"  {number:02d}. "
            f"table={table_index} "
            f"{preview}"
        )
    return False
all_valid = True
for table_index, row in found:
    print_row(
        table_index,
        row,
    )
    if not validate_row(row):
        all_valid = False
return all_valid

def main():
print(”=” * 80)
print(“00838B TPEx ETF OFFICIAL PRICE DIAGNOSTIC”)
print(”=” * 80)

print(f"TEST SYMBOL: {SYMBOL}")
print("SOURCE: TPEx OFFICIAL")
print("DATA TYPE: ETF HISTORICAL PRICE")
print(f"ENDPOINT: {ENDPOINT}")
print()
print("Yahoo: NO")
print("Universe: NO")
print("Production pipeline: NO")
print()
print("DATA CONTRACT:")
print("  JSON root")
print("    -> tables[]")
print("       -> data[]")
print()
print("aaData: NO")
print("stk_wn1430_result.php: NO")
print("normal OTC stock quote: NO")
print("fetch_prices.py: NOT MODIFIED")
# Last trading day used for this diagnostic.
start_date = date(
    2026,
    8,
    28,
)
success_dates = []
tested_dates = 0
for offset in range(10):
    d = start_date - timedelta(
        days=offset
    )
    tested_dates += 1
    try:
        ok = fetch_one_date(d)
    except Exception as exc:
        print()
        print(
            f"TEST ERROR: {exc}"
        )
        ok = False
    if ok:
        success_dates.append(
            d.isoformat()
        )
print()
print("=" * 80)
print("FINAL RESULT")
print("=" * 80)
print(
    f"HTTP / JSON test dates: "
    f"{tested_dates}"
)
print(
    f"Found {SYMBOL}: "
    f"{len(success_dates)} dates"
)
if success_dates:
    print()
    print("SUCCESS")
    for item in success_dates:
        print(f"  {item}")
    print()
    print(
        f"Confirmed TPEx official ETF "
        f"data contains {SYMBOL}."
    )
    print()
    print(
        "Next step: inspect production "
        "fetch_prices.py ETF routing."
    )
    return 0
print()
print(
    f"FAILED: {SYMBOL} was not found "
    "during the test period."
)
print()
print("Confirmed:")
print(
    "1. Test targets TPEx ETF data."
)
print(
    "2. General OTC stock endpoint "
    "is not used."
)
print(
    "3. stk_wn1430_result.php "
    "is not used."
)
print(
    "4. Parser reads only "
    "tables[].data."
)
print(
    "5. Production fetch_prices.py "
    "was not modified."
)
return 1

if name == “main”:
sys.exit(main())
