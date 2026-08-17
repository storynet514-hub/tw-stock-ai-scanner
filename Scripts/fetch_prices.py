#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V1.0

============================================================
本程式責任
============================================================

只負責：

1. 讀取 Data/universe.json
2. 依 Universe 中的股票代號取得歷史價格
3. 取得最新可用交易日價格
4. 將價格資料寫入 Data/prices.json

============================================================
本程式不負責
============================================================

❌ 不建立 Universe
❌ 不計算 MACD
❌ 不計算 KD
❌ 不計算 RSI
❌ 不計算 MA
❌ 不判斷買進條件
❌ 不建立 UI
❌ 不建立 ui_data.json

資料流程：

Data/universe.json
        ↓
fetch_prices.py
        ↓
Data/prices.json
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_FILE = DATA_DIR / "prices.json"

# 歷史資料至少抓 3 年
# 後續 MACD / KD / RSI / MA 都會使用這些資料
START_DATE = "2023-01-01"

# Yahoo Finance chart API
YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

REQUEST_TIMEOUT = 30

# 每支股票之間稍微停頓
REQUEST_DELAY = 0.15


# ============================================================
# 輸出工具
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# 讀取 Universe
# ============================================================

def load_universe():
    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            f"找不到 Universe 檔案：{UNIVERSE_FILE}"
        )

    try:
        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"universe.json JSON 格式錯誤：{exc}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "universe.json 頂層格式不是 object"
        )

    log(
        f"Universe JSON 載入成功："
        f"{UNIVERSE_FILE}"
    )

    return data


# ============================================================
# 從 Universe 萃取股票
# ============================================================

def extract_symbols(universe):
    """
    支援目前重建架構可能使用的幾種 Universe 結構。

    例如：

    {
        "stocks": [
            {
                "symbol": "2330.TW",
                "name": "台積電"
            }
        ]
    }

    或：

    {
        "listed": [...],
        "otc": [...]
    }

    或直接：

    {
        "2330.TW": {...},
        "1303.TW": {...}
    }
    """

    symbols = {}

    def add_item(item):
        if isinstance(item, str):
            symbol = item.strip()

            if symbol:
                symbols[symbol] = {
                    "symbol": symbol
                }

            return

        if not isinstance(item, dict):
            return

        possible_symbol_keys = [
            "symbol",
            "ticker",
            "code",
            "stock_id",
            "stock_code"
        ]

        symbol = None

        for key in possible_symbol_keys:
            value = item.get(key)

            if value is not None:
                value = str(value).strip()

                if value:
                    symbol = value
                    break

        if not symbol:
            return

        record = dict(item)

        record["symbol"] = symbol

        symbols[symbol] = record

    # --------------------------------------------------------
    # 常見 list 欄位
    # --------------------------------------------------------

    list_keys = [
        "stocks",
        "listed",
        "otc",
        "tpex",
        "twse",
        "securities",
        "universe",
        "items"
    ]

    for key in list_keys:
        value = universe.get(key)

        if isinstance(value, list):
            for item in value:
                add_item(item)

        elif isinstance(value, dict):
            for key2, value2 in value.items():

                if isinstance(value2, list):
                    for item in value2:
                        add_item(item)

                elif isinstance(value2, dict):
                    record = dict(value2)

                    if not any(
                        record.get(k)
                        for k in [
                            "symbol",
                            "ticker",
                            "code",
                            "stock_id",
                            "stock_code"
                        ]
                    ):
                        record["symbol"] = key2

                    add_item(record)

    # --------------------------------------------------------
    # 如果上面沒有抓到，嘗試頂層 dictionary
    # --------------------------------------------------------

    if not symbols:
        for key, value in universe.items():

            if isinstance(value, dict):
                record = dict(value)

                if not any(
                    record.get(k)
                    for k in [
                        "symbol",
                        "ticker",
                        "code",
                        "stock_id",
                        "stock_code"
                    ]
                ):
                    record["symbol"] = key

                add_item(record)

            elif isinstance(value, str):
                # 只有看起來像股票代號才加入
                candidate = value.strip()

                if candidate:
                    add_item(candidate)

    # --------------------------------------------------------
    # 正規化 Yahoo symbol
    # --------------------------------------------------------

    normalized = {}

    for symbol, record in symbols.items():

        symbol = str(symbol).strip().upper()

        if not symbol:
            continue

        # 如果 Universe 已經帶 Yahoo suffix，直接使用
        if symbol.endswith(".TW"):
            yahoo_symbol = symbol

        elif symbol.endswith(".TWO"):
            yahoo_symbol = symbol

        else:
            # 純數字股票代號
            if symbol.isdigit():
                yahoo_symbol = symbol + ".TW"
            else:
                # 不擅自改造非台股代號
                yahoo_symbol = symbol

        record = dict(record)
        record["symbol"] = symbol
        record["yahoo_symbol"] = yahoo_symbol

        normalized[yahoo_symbol] = record

    return normalized


# ============================================================
# Yahoo Finance 取得單一股票歷史價格
# ============================================================

def fetch_history(symbol):
    url = YAHOO_CHART_URL.format(symbol=symbol)

    params = {
        "period1": int(
            datetime.strptime(
                START_DATE,
                "%Y-%m-%d"
            ).timestamp()
        ),
        "period2": int(time.time()),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true"
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    payload = response.json()

    chart = payload.get("chart", {})

    error = chart.get("error")

    if error:
        raise RuntimeError(
            str(error)
        )

    results = chart.get("result")

    if not results:
        raise RuntimeError(
            "Yahoo Finance 沒有回傳 result"
        )

    result = results[0]

    timestamps = result.get("timestamp")

    indicators = result.get("indicators", {})

    quote_list = indicators.get("quote", [])

    if not timestamps or not quote_list:
        raise RuntimeError(
            "Yahoo Finance 沒有價格資料"
        )

    quote = quote_list[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    adjclose_list = indicators.get(
        "adjclose",
        []
    )

    adjusted_closes = []

    if adjclose_list:
        adjusted_closes = adjclose_list[0].get(
            "adjclose",
            []
        )

    rows = []

    length = len(timestamps)

    for i in range(length):

        timestamp = timestamps[i]

        close = (
            closes[i]
            if i < len(closes)
            else None
        )

        if close is None:
            continue

        open_price = (
            opens[i]
            if i < len(opens)
            else None
        )

        high = (
            highs[i]
            if i < len(highs)
            else None
        )

        low = (
            lows[i]
            if i < len(lows)
            else None
        )

        volume = (
            volumes[i]
            if i < len(volumes)
            else None
        )

        adjusted_close = (
            adjusted_closes[i]
            if i < len(adjusted_closes)
            else None
        )

        date = datetime.fromtimestamp(
            timestamp
        ).strftime("%Y-%m-%d")

        rows.append({
            "date": date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "adj_close": (
                adjusted_close
                if adjusted_close is not None
                else close
            ),
            "volume": volume
        })

    if not rows:
        raise RuntimeError(
            "沒有有效交易日資料"
        )

    return rows


# ============================================================
# 清理數值
# ============================================================

def clean_number(value):
    if value is None:
        return None

    try:
        value = float(value)

        if pd.isna(value):
            return None

        return round(value, 6)

    except Exception:
        return None


def clean_rows(rows):
    cleaned = []

    for row in rows:

        cleaned.append({
            "date": row["date"],
            "open": clean_number(row.get("open")),
            "high": clean_number(row.get("high")),
            "low": clean_number(row.get("low")),
            "close": clean_number(row.get("close")),
            "adj_close": clean_number(
                row.get("adj_close")
            ),
            "volume": (
                int(row["volume"])
                if row.get("volume") is not None
                else None
            )
        })

    return cleaned


# ============================================================
# 建立 prices.json
# ============================================================

def build_prices(universe_records):

    section("開始取得歷史價格")

    total = len(universe_records)

    log(f"待處理標的：{total}")
    log(f"歷史資料起始日：{START_DATE}")
    log("")

    prices = {}

    success = 0
    failed = 0

    failed_symbols = []

    for index, (yahoo_symbol, meta) in enumerate(
        universe_records.items(),
        start=1
    ):

        log(
            f"[{index}/{total}] "
            f"取得 {yahoo_symbol}"
        )

        try:

            rows = fetch_history(
                yahoo_symbol
            )

            rows = clean_rows(rows)

            if not rows:
                raise RuntimeError(
                    "沒有有效價格資料"
                )

            latest = rows[-1]

            prices[yahoo_symbol] = {
                "symbol": meta.get(
                    "symbol",
                    yahoo_symbol
                ),
                "name": meta.get(
                    "name",
                    ""
                ),
                "market": meta.get(
                    "market",
                    meta.get(
                        "type",
                        ""
                    )
                ),
                "latest_date": latest["date"],
                "latest_close": latest["close"],
                "latest_volume": latest["volume"],
                "data": rows
            }

            success += 1

            log(
                f"    ✓ {len(rows)} 筆"
                f" | 最新日期：{latest['date']}"
                f" | 收盤：{latest['close']}"
            )

        except Exception as exc:

            failed += 1

            failed_symbols.append({
                "symbol": yahoo_symbol,
                "error": str(exc)
            })

            log(
                f"    ✗ 取得失敗：{exc}"
            )

        time.sleep(REQUEST_DELAY)

    return prices, success, failed, failed_symbols


# ============================================================
# 驗證價格資料
# ============================================================

def validate_prices(
    prices,
    total_symbols,
    success,
    failed
):
    section("價格資料驗證")

    log(f"Universe 標的數量：{total_symbols}")
    log(f"成功取得：{success}")
    log(f"取得失敗：{failed}")
    log(f"prices.json 標的數量：{len(prices)}")

    if total_symbols == 0:
        raise RuntimeError(
            "Universe 沒有任何標的，停止建立 prices.json"
        )

    if success == 0:
        raise RuntimeError(
            "所有標的都無法取得價格，"
            "停止建立 prices.json"
        )

    # 至少需要取得一部分資料
    success_ratio = success / total_symbols

    log(
        f"成功率：{success_ratio * 100:.2f}%"
    )

    # 防止 API 大規模失敗後覆蓋正常資料
    if success_ratio < 0.50:
        raise RuntimeError(
            "價格資料成功率低於 50%，"
            "為避免產生錯誤 prices.json，"
            "本次執行停止。"
        )

    # 檢查每支股票至少有一筆
    invalid = []

    for symbol, record in prices.items():

        data = record.get("data", [])

        if not data:
            invalid.append(symbol)
            continue

        latest_date = record.get(
            "latest_date"
        )

        latest_close = record.get(
            "latest_close"
        )

        if not latest_date:
            invalid.append(symbol)

        if latest_close is None:
            invalid.append(symbol)

    if invalid:
        raise RuntimeError(
            "發現價格資料結構異常："
            + ", ".join(invalid[:20])
        )

    log("✓ 價格資料結構驗證通過")


# ============================================================
# 寫入 JSON
# ============================================================

def save_prices(
    prices,
    failed_symbols
):

    section("寫入 Data/prices.json")

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {
        "version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "source": "Yahoo Finance",
        "history_start": START_DATE,
        "count": len(prices),
        "failed_count": len(failed_symbols),
        "failed_symbols": failed_symbols,
        "prices": prices
    }

    temp_file = PRICES_FILE.with_suffix(
        ".json.tmp"
    )

    # 先寫暫存檔
    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False
        )

    # 寫入成功後才正式替換
    temp_file.replace(
        PRICES_FILE
    )

    file_size = PRICES_FILE.stat().st_size

    log(
        f"✓ prices.json 建立成功"
    )

    log(
        f"檔案：{PRICES_FILE}"
    )

    log(
        f"大小：{file_size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# 主程式
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 64)
    log(
        f"台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )
    log("=" * 64)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        # ----------------------------------------------------
        # 1. 讀取 Universe
        # ----------------------------------------------------

        universe = load_universe()

        # ----------------------------------------------------
        # 2. 萃取股票
        # ----------------------------------------------------

        section("解析 Universe")

        universe_records = extract_symbols(
            universe
        )

        log(
            f"解析完成："
            f"{len(universe_records)} 個標的"
        )

        if not universe_records:
            raise RuntimeError(
                "無法從 universe.json 解析出任何股票代號"
            )

        # 顯示前 10 個
        log("")
        log("前 10 個標的：")

        for symbol, record in list(
            universe_records.items()
        )[:10]:

            name = record.get(
                "name",
                ""
            )

            log(
                f"  {symbol}"
                + (
                    f" | {name}"
                    if name
                    else ""
                )
            )

        # ----------------------------------------------------
        # 3. 抓價格
        # ----------------------------------------------------

        (
            prices,
            success,
            failed,
            failed_symbols
        ) = build_prices(
            universe_records
        )

        # ----------------------------------------------------
        # 4. 驗證
        # ----------------------------------------------------

        validate_prices(
            prices=prices,
            total_symbols=len(
                universe_records
            ),
            success=success,
            failed=failed
        )

        # ----------------------------------------------------
        # 5. 寫檔
        # ----------------------------------------------------

        save_prices(
            prices,
            failed_symbols
        )

        # ----------------------------------------------------
        # 完成
        # ----------------------------------------------------

        elapsed = time.time() - start_time

        log("")
        log("=" * 64)
        log(
            "✓ fetch_prices.py 執行完成"
        )
        log("=" * 64)

        log(
            f"成功：{success}"
        )

        log(
            f"失敗：{failed}"
        )

        log(
            f"總耗時：{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{PRICES_FILE}"
        )

        if failed_symbols:

            log("")
            log(
                "⚠️ 以下標的本次取得失敗："
            )

            for item in failed_symbols[:30]:

                log(
                    f"  "
                    f"{item['symbol']} "
                    f"→ "
                    f"{item['error']}"
                )

            if len(failed_symbols) > 30:

                log(
                    f"  ... "
                    f"另外 {len(failed_symbols) - 30} 個"
                )

        return 0

    except Exception as exc:

        log("")
        log("=" * 64)
        log(
            "❌ fetch_prices.py 執行失敗"
        )
        log("=" * 64)

        log(
            f"❌ 原因：{exc}"
        )

        # 不產生半成品
        if PRICES_FILE.exists():
            log(
                "⚠️ 保留原有 prices.json，"
                "不以不完整資料覆蓋。"
            )

        return 1


if __name__ == "__main__":
    sys.exit(main())
