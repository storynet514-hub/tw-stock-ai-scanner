#!/usr/bin/env python3

-- coding: utf-8 --

“””
00838B TPEx Official ETF Historical Price Diagnostic V4

目的：

1. 使用 TPEx 官方 ETF 歷史行情資料源
2. 不使用一般上櫃股票行情 endpoint
3. 不使用 stk_wn1430_result.php
4. 只讀取 tables[].data
5. 全域搜尋 00838B
6. 驗證收盤價、成交量、成交金額
7. 不修改正式 fetch_prices.py

注意：

* 本檔案只是診斷測試。
* 成功前不修改正式價格管線。
    “””

from future import annotations

import json
import sys
from datetime import date, timedelta
from typing import Any

import requests

SYMBOL = “00838B”

TPEx 官方 ETF 歷史行情頁

PAGE_URL = (
“https://www.tpex.org.tw/web/etf/historical/”
“etf_statistics.php”
)

HEADERS = {
“User-Agent”: (
“Mozilla/5.0 (Windows NT 10.0; Win64; x64) “
“AppleWebKit/537.36 “
“(KHTML, like Gecko) “
“Chrome/131.0.0.0 Safari/537.36”
),
“Accept”: (
“application/json,text/javascript,text/plain,”
“/;q=0.01”
),
“Accept-Language”: “zh-TW,zh;q=0.9,en;q=0.8”,
“Referer”: “https://www.tpex.org.tw/”,
}

TIMEOUT = 30

def roc_date(d: date) -> str:
“”“西元日期轉民國日期，例如 2026-08-28 -> 115/08/28。”””
return f”{d.year - 1911:03d}/{d.month:02d}/{d.day:02d}”

def normalize_symbol(value: Any) -> str:
“”“統一證券代號格式。”””
if value is None:
return “”

return (
    str(value)
    .replace("\ufeff", "")
    .replace("\u3000", "")
    .strip()
    .upper()
)

def is_valid_data_row(row: Any) -> bool:
“”“判斷是否為有效 tables[].data row。”””
return isinstance(row, list) and len(row) > 0

def extract_tables_data(payload: dict[str, Any]):
“””
嚴格按照 TPEx JSON 結構讀取：

    tables[].data
不讀：
    aaData
    data
    rows
    result
"""
tables = payload.get("tables")
print()
print("TABLES")
print(f"type：{type(tables).__name__}")
if not isinstance(tables, list):
    print("❌ JSON root.tables 不是 list")
    return []
print(f"tables 數量：{len(tables)}")
all_rows = []
for table_index, table in enumerate(tables):
    print()
    print(f"TABLE [{table_index}]")
    print("-" * 80)
    if not isinstance(table, dict):
        print(f"type：{type(table).__name__}")
        print("❌ table 不是 dict")
        continue
    print("keys：")
    for key in table.keys():
        print(f"  {key}")
    data = table.get("data")
    print()
    print("tables[].data")
    print(f"type：{type(data).__name__}")
    if not isinstance(data, list):
        print("❌ data 不是 list")
        continue
    valid_rows = [
        row for row in data
        if is_valid_data_row(row)
    ]
    print(f"raw rows：{len(data)}")
    print(f"valid rows：{len(valid_rows)}")
    if valid_rows:
        print("✅ 找到有效 tables[].data")
        for row in valid_rows:
            all_rows.append(
                {
                    "table_index": table_index,
                    "row": row,
                }
            )
return all_rows

def find_symbol(rows):
“”“在所有 tables[].data 中搜尋指定商品。”””

print()
print("=" * 80)
print("GLOBAL SYMBOL SEARCH")
print("=" * 80)
target = normalize_symbol(SYMBOL)
found = []
for item in rows:
    table_index = item["table_index"]
    row = item["row"]
    if not row:
        continue
    symbol = normalize_symbol(row[0])
    if symbol == target:
        found.append(
            {
                "table_index": table_index,
                "row": row,
            }
        )
return found

def print_row(row: list[Any], table_index: int):
“”“輸出 ETF 歷史行情欄位。”””

print()
print("=" * 80)
print(f"FOUND TABLE INDEX：{table_index}")
print("=" * 80)
print("RAW ROW")
print(json.dumps(
    row,
    ensure_ascii=False,
    indent=2,
))
labels = [
    "日期",
    "證券代號",
    "證券名稱",
    "成交張數",
    "成交仟元",
    "開盤",
    "最高",
    "最低",
    "收盤",
    "漲跌",
    "筆數",
]
print()
print("FIELDS")
for index, value in enumerate(row):
    label = (
        labels[index]
        if index < len(labels)
        else f"FIELD_{index}"
    )
    print(
        f"  [{index:02d}] "
        f"{label}：{value}"
    )
return labels

def validate_row(row: list[Any]) -> bool:
“””
驗證 ETF 歷史行情資料。

預期欄位：
0 日期
1 證券代號
2 證券名稱
3 成交張數
4 成交仟元
5 開盤
6 最高
7 最低
8 收盤
9 漲跌
10 筆數
"""
print()
print("=" * 80)
print("VALIDATION")
print("=" * 80)
if len(row) < 9:
    print(
        f"❌ row 欄位不足："
        f"{len(row)} < 9"
    )
    return False
symbol = normalize_symbol(row[1])
if symbol != SYMBOL:
    print(
        f"❌ symbol 不一致："
        f"{symbol!r}"
    )
    return False
print(f"  ✅ symbol = {symbol}")
close_price = str(row[8]).strip()
volume = str(row[3]).strip()
amount = str(row[4]).strip()
print(f"  close_price = {close_price}")
print(f"  volume = {volume}")
print(f"  amount = {amount}")
if not close_price or close_price in (
    "-",
    "--",
    "－",
    "－－",
):
    print("  ❌ close_price 無有效資料")
    return False
print("  ✅ close_price 有效")
if not volume or volume in (
    "-",
    "--",
    "－",
    "－－",
):
    print("  ❌ volume 無有效資料")
    return False
print("  ✅ volume 有效")
if not amount or amount in (
    "-",
    "--",
    "－",
    "－－",
):
    print("  ⚠️ amount 無資料")
else:
    print("  ✅ amount 有效")
return True

def fetch(d: date):
“””
查詢指定日期的 TPEx ETF 歷史行情。

重要：
此處不使用一般股票行情 endpoint。
"""
tpex_date = roc_date(d)
print()
print("=" * 80)
print(f"TEST DATE：{d.isoformat()}")
print(f"TPEx DATE：{tpex_date}")
print("=" * 80)
print(f"ETF HISTORY PAGE：{PAGE_URL}")
# TPEx 舊 ETF 歷史行情頁使用日期查詢。
# 同時帶入語系及日期。
params = {
    "l": "zh-tw",
    "d": tpex_date,
}
try:
    response = requests.get(
        PAGE_URL,
        params=params,
        headers=HEADERS,
        timeout=TIMEOUT,
    )
except requests.RequestException as exc:
    print(f"❌ REQUEST ERROR：{exc}")
    return False
print()
print("HTTP")
print(f"status_code：{response.status_code}")
print(
    "content_type："
    f"{response.headers.get('Content-Type', '')}"
)
print(f"content_length：{len(response.content)}")
if response.status_code != 200:
    print("❌ HTTP STATUS 非 200")
    return False
try:
    payload = response.json()
except Exception as exc:
    print(f"❌ JSON decode failed：{exc}")
    print()
    print("RESPONSE PREVIEW")
    print(response.text[:3000])
    return False
print()
print("JSON ROOT")
print(f"type：{type(payload).__name__}")
if not isinstance(payload, dict):
    print("❌ JSON root 不是 dict")
    return False
print()
print("ROOT KEYS")
for key in payload.keys():
    print(f"  {key}")
# ------------------------------------------------------------
# 嚴格只走 tables[].data
# ------------------------------------------------------------
rows = extract_tables_data(payload)
print()
print("=" * 80)
print("DATA SUMMARY")
print("=" * 80)
print(
    "來源結構："
    "tables[].data"
)
print(
    f"有效資料 rows：{len(rows)}"
)
if not rows:
    print(
        "❌ 所有 tables[].data 均沒有有效 row"
    )
    return False
found = find_symbol(rows)
if not found:
    print()
    print(
        f"❌ {SYMBOL} 不在任何 "
        "tables[].data"
    )
    print()
    print("前 30 筆資料：")
    shown = 0
    for item in rows:
        row = item["row"]
        if not row:
            continue
        preview = [
            str(value)
            for value in row[:4]
        ]
        print(
            f"  table={item['table_index']} "
            f"{preview}"
        )
        shown += 1
        if shown >= 30:
            break
    return False
print()
print(
    f"✅ {SYMBOL} 在 "
    f"tables[].data 找到 "
    f"{len(found)} 筆"
)
overall_ok = True
for item in found:
    table_index = item["table_index"]
    row = item["row"]
    print_row(
        row,
        table_index,
    )
    if not validate_row(row):
        overall_ok = False
return overall_ok

def main():
print(”=” * 80)
print(“00838B TPEx OFFICIAL ETF HISTORICAL PRICE DIAGNOSTIC V4”)
print(”=” * 80)

print(f"測試商品：{SYMBOL}")
print("資料來源：TPEx 官方")
print("資料類型：ETF 歷史行情")
print(f"Endpoint：{PAGE_URL}")
print()
print("Yahoo：NO")
print("Universe：NO")
print("正式價格管線：NO")
print()
print("資料解析契約：")
print("  JSON root")
print("    └── tables[]")
print("          └── data[]")
print()
print("❌ 不讀 aaData")
print("❌ 不讀一般股票行情")
print("❌ 不讀 stk_wn1430_result.php")
print("❌ 不修改 fetch_prices.py")
# 2026-08-28 為最後一個交易日。
today = date(2026, 8, 28)
success_dates = []
# 測試最近 10 個日曆日。
for offset in range(10):
    d = today - timedelta(days=offset)
    try:
        ok = fetch(d)
    except Exception as exc:
        print()
        print(f"❌ TEST ERROR：{exc}")
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
    "HTTP / JSON 測試日期：10"
)
print(
    f"找到 {SYMBOL} 的日期："
    f"{len(success_dates)}"
)
if success_dates:
    print()
    print("✅ SUCCESS")
    for d in success_dates:
        print(f"  {d}")
    print()
    print(
        f"✅ 已在 TPEx 官方 "
        f"ETF tables[].data 找到 {SYMBOL}"
    )
    print()
    print(
        "下一步才檢查正式 "
        "fetch_prices.py 的 ETF 分流。"
    )
    return 0
print()
print(
    f"❌ 最近測試期間仍沒有找到 "
    f"{SYMBOL}"
)
print()
print("目前可確認：")
print("1. 測試的是 TPEx ETF 歷史行情")
print("2. 沒有使用一般上櫃股票行情 endpoint")
print("3. 沒有使用 stk_wn1430_result.php")
print("4. 解析只接受 tables[].data")
print("5. 正式 fetch_prices.py 尚未修改")
print()
print(
    "下一步只需根據這個測試的實際 HTTP/JSON "
    "結構確認 ETF API endpoint。"
)
return 1

if name == “main”:
sys.exit(main())
