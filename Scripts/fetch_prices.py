#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V4.0

============================================================
用途
============================================================

讀取：

    Data/universe.json

取得台股全市場歷史 OHLCV：

    TWSE
    TPEx

輸出：

    Data/prices/

不再輸出：

    Data/prices.json

============================================================
V4.0 核心架構
============================================================

Data/universe.json
        ↓
fetch_prices.py
        ↓
Yahoo Finance
        ↓
Data/prices/
    ├── prices_001.json
    ├── prices_002.json
    ├── prices_003.json
    ├── ...
    └── manifest.json

============================================================
重要
============================================================

本程式：

✓ 不修改 universe.json
✓ 不修改 chip.json
✓ 不負責籌碼資料
✓ 不負責回測
✓ 不負責 UI
✓ 不負責 Git commit

只負責：

Universe
    ↓
歷史價格
    ↓
Data/prices/

============================================================
V4.0 特性
============================================================

1. 分檔輸出
2. 每檔固定最多 100 支股票
3. 避免 GitHub 100 MB 單檔限制
4. 自動 retry
5. Yahoo Finance chart API
6. 成功率驗證
7. TWSE / TPEx 分別統計
8. 暫存目錄寫入
9. 驗證成功才替換正式資料
10. 產生 manifest.json
11. 保留 OHLCV 歷史資料
12. 支援後續 backtest_winrate.py V2.0
"""

import json
import math
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V4.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"

# 每個 JSON 最多放幾支股票
# 100 支約數 MB，遠低於 GitHub 100 MB 限制
STOCKS_PER_FILE = 100

# 最少成功率
MIN_SUCCESS_RATE = 80.0

# Yahoo Finance 歷史資料期間
#
# 2 年資料足以支援：
# MA20
# RSI14
# MACD
# KD
# 30 / 60 / 90 日回測
#
# 同時避免資料量無限制膨脹。
PERIOD = "2y"

INTERVAL = "1d"

# HTTP timeout
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 45

TIMEOUT = (
    CONNECT_TIMEOUT,
    READ_TIMEOUT,
)

# retry
MAX_RETRIES = 3

# retry delay
RETRY_DELAY = 2

# 個股請求間隔
REQUEST_DELAY = 0.08

# 每個分檔的安全大小警戒
MAX_FILE_SIZE_MB = 80.0


# ============================================================
# Yahoo Finance
# ============================================================

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)


# ============================================================
# Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
)


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# JSON helper
# ============================================================

def load_json(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# Numeric
# ============================================================

def to_float(value):

    if value is None:
        return None

    try:

        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def to_int(value):

    if value is None:
        return None

    try:

        number = int(float(value))

        return number

    except Exception:

        return None


# ============================================================
# Safe round
# ============================================================

def safe_round(value, digits=4):

    number = to_float(value)

    if number is None:
        return None

    return round(
        number,
        digits,
    )


# ============================================================
# Normalize symbol
# ============================================================

def normalize_code(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # 去除常見市場後綴
    if text.upper().endswith(".TW"):
        text = text[:-3]

    elif text.upper().endswith(".TWO"):
        text = text[:-4]

    text = text.strip()

    # 股票代號目前以數字為主
    if not text.isdigit():
        return None

    # 台股普通股票主要 4 碼
    if len(text) != 4:
        return None

    return text


# ============================================================
# Build Yahoo symbol
# ============================================================

def build_yahoo_symbol(code, market=None):

    code = normalize_code(code)

    if code is None:
        return None

    market_text = str(
        market or ""
    ).strip().upper()

    if market_text in {
        "TPEx",
        "TPEX",
        "OTC",
        "上櫃",
        "櫃買",
        "TWO",
    }:
        return f"{code}.TWO"

    return f"{code}.TW"


# ============================================================
# Extract universe items
# ============================================================

def extract_items(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    # 主要格式
    for key in (
        "items",
        "stocks",
        "data",
        "universe",
        "symbols",
        "records",
    ):

        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):

            result = []

            for key2, value2 in value.items():

                if isinstance(value2, dict):

                    item = dict(value2)

                    if "code" not in item:
                        item["code"] = key2

                    result.append(item)

                else:

                    result.append(
                        {
                            "code": key2,
                            "name": value2,
                        }
                    )

            if result:
                return result

    return []


# ============================================================
# Extract code
# ============================================================

def extract_code(item):

    if isinstance(item, str):

        return normalize_code(item)

    if not isinstance(item, dict):
        return None

    for key in (
        "code",
        "stock_code",
        "stockCode",
        "symbol",
        "ticker",
        "證券代號",
        "股票代號",
        "代號",
    ):

        value = item.get(key)

        code = normalize_code(value)

        if code:
            return code

    return None


# ============================================================
# Extract name
# ============================================================

def extract_name(item):

    if isinstance(item, str):
        return ""

    if not isinstance(item, dict):
        return ""

    for key in (
        "name",
        "stock_name",
        "stockName",
        "short_name",
        "名稱",
        "證券名稱",
        "股票名稱",
    ):

        value = item.get(key)

        if value is not None:

            text = str(value).strip()

            if text:
                return text

    return ""


# ============================================================
# Extract market
# ============================================================

def extract_market(item):

    if isinstance(item, str):
        return ""

    if not isinstance(item, dict):
        return ""

    for key in (
        "market",
        "market_type",
        "marketType",
        "exchange",
        "市場",
        "市場別",
        "交易所",
    ):

        value = item.get(key)

        if value is not None:

            text = str(value).strip()

            if text:
                return text

    return ""


# ============================================================
# Load universe
# ============================================================

def load_universe():

    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    log(
        f"Universe JSON：{UNIVERSE_FILE}"
    )

    data = load_json(
        UNIVERSE_FILE
    )

    items = extract_items(data)

    if not items:

        raise RuntimeError(
            "universe.json 沒有找到有效 items/stocks/data"
        )

    stocks = {}

    invalid = 0

    for item in items:

        code = extract_code(item)

        if code is None:

            invalid += 1
            continue

        name = extract_name(item)

        market = extract_market(item)

        # 如果沒有市場資訊
        # 預設依 Yahoo .TW
        yahoo_symbol = build_yahoo_symbol(
            code,
            market,
        )

        if yahoo_symbol is None:
            invalid += 1
            continue

        # 避免重複
        if code in stocks:
            continue

        stocks[code] = {
            "code": code,
            "name": name,
            "market": market,
            "yahoo_symbol": yahoo_symbol,
        }

    if not stocks:

        raise RuntimeError(
            "Universe 沒有解析出任何合法股票代號。"
        )

    section("Universe 驗證")

    log(
        f"Universe 原始項目：{len(items)}"
    )

    log(
        f"合法股票代號：{len(stocks)}"
    )

    log(
        f"無效項目：{invalid}"
    )

    return stocks


# ============================================================
# Yahoo API
# ============================================================

def fetch_yahoo(symbol):

    url = YAHOO_CHART_URL.format(
        symbol=symbol
    )

    params = {
        "range": PERIOD,
        "interval": INTERVAL,
        "events": "history",
        "includeAdjustedClose": "true",
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=TIMEOUT,
            )

            log(
                f"  HTTP attempt {attempt}/{MAX_RETRIES} "
                f"| {response.status_code}"
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {},
            )

            error = chart.get(
                "error"
            )

            if error:

                raise RuntimeError(
                    str(error)
                )

            results = chart.get(
                "result"
            )

            if not results:

                raise RuntimeError(
                    "Yahoo API 沒有 result"
                )

            result = results[0]

            timestamps = result.get(
                "timestamp"
            )

            indicators = result.get(
                "indicators",
                {},
            )

            quotes = indicators.get(
                "quote",
                []
            )

            if not timestamps:
                raise RuntimeError(
                    "Yahoo 沒有 timestamp"
                )

            if not quotes:
                raise RuntimeError(
                    "Yahoo 沒有 quote"
                )

            quote = quotes[0]

            opens = quote.get(
                "open",
                []
            )

            highs = quote.get(
                "high",
                []
            )

            lows = quote.get(
                "low",
                []
            )

            closes = quote.get(
                "close",
                []
            )

            volumes = quote.get(
                "volume",
                []
            )

            adjusted_close = None

            adj = indicators.get(
                "adjclose",
                []
            )

            if adj:
                adjusted_close = adj[0].get(
                    "adjclose",
                    []
                )

            rows = []

            for i, timestamp in enumerate(
                timestamps
            ):

                if timestamp is None:
                    continue

                close = (
                    closes[i]
                    if i < len(closes)
                    else None
                )

                if close is None:
                    continue

                try:

                    date_text = datetime.fromtimestamp(
                        int(timestamp),
                        tz=timezone.utc,
                    ).strftime(
                        "%Y-%m-%d"
                    )

                except Exception:

                    continue

                open_value = (
                    opens[i]
                    if i < len(opens)
                    else None
                )

                high_value = (
                    highs[i]
                    if i < len(highs)
                    else None
                )

                low_value = (
                    lows[i]
                    if i < len(lows)
                    else None
                )

                volume_value = (
                    volumes[i]
                    if i < len(volumes)
                    else None
                )

                adj_value = None

                if (
                    adjusted_close is not None
                    and i < len(adjusted_close)
                ):
                    adj_value = adjusted_close[i]

                row = {
                    "date": date_text,
                    "open": safe_round(
                        open_value,
                        4,
                    ),
                    "high": safe_round(
                        high_value,
                        4,
                    ),
                    "low": safe_round(
                        low_value,
                        4,
                    ),
                    "close": safe_round(
                        close,
                        4,
                    ),
                    "volume": to_int(
                        volume_value
                    ),
                    "adj_close": safe_round(
                        adj_value,
                        4,
                    ),
                }

                rows.append(row)

            if not rows:

                raise RuntimeError(
                    "Yahoo 沒有有效歷史價格"
                )

            # 日期排序
            rows.sort(
                key=lambda x: x["date"]
            )

            # 去除重複日期
            unique_rows = {}

            for row in rows:
                unique_rows[
                    row["date"]
                ] = row

            rows = list(
                unique_rows.values()
            )

            rows.sort(
                key=lambda x: x["date"]
            )

            return rows

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠ 取得失敗：{exc}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"Yahoo 取得失敗：{last_error}"
    )


# ============================================================
# Build stock record
# ============================================================

def build_stock_record(stock, rows):

    return {
        "code": stock["code"],
        "symbol": stock["yahoo_symbol"],
        "name": stock["name"],
        "market": stock["market"],
        "data_start": (
            rows[0]["date"]
            if rows
            else None
        ),
        "data_end": (
            rows[-1]["date"]
            if rows
            else None
        ),
        "count": len(rows),
        "data": rows,
    }


# ============================================================
# Fetch all
# ============================================================

def fetch_all(stocks):

    section("開始取得歷史價格")

    total = len(stocks)

    success = {}

    failed = {}

    market_success = {}
    market_failed = {}

    for index, (
        code,
        stock,
    ) in enumerate(
        stocks.items(),
        start=1,
    ):

        symbol = stock[
            "yahoo_symbol"
        ]

        market = stock.get(
            "market",
            ""
        )

        if not market:
            market = (
                "TPEx"
                if symbol.endswith(".TWO")
                else "TWSE"
            )

        log(
            f"[{index}/{total}] "
            f"{code} "
            f"{stock.get('name', '')} "
            f"| {symbol}"
        )

        try:

            rows = fetch_yahoo(
                symbol
            )

            if len(rows) < 120:

                raise RuntimeError(
                    f"歷史資料不足：{len(rows)} 筆"
                )

            success[code] = build_stock_record(
                stock,
                rows,
            )

            market_success.setdefault(
                market,
                0,
            )

            market_success[
                market
            ] += 1

            log(
                f"  ✓ 成功：{len(rows)} 筆"
            )

        except Exception as exc:

            failed[code] = {
                "name": stock.get(
                    "name",
                    "",
                ),
                "symbol": symbol,
                "market": market,
                "error": str(exc),
            }

            market_failed.setdefault(
                market,
                0,
            )

            market_failed[
                market
            ] += 1

            log(
                f"  ✗ 失敗：{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    return (
        success,
        failed,
        market_success,
        market_failed,
    )


# ============================================================
# Validate
# ============================================================

def validate(
    total,
    success,
    failed,
    market_success,
    market_failed,
):

    section("價格資料驗證")

    success_count = len(
        success
    )

    failed_count = len(
        failed
    )

    if total <= 0:

        raise RuntimeError(
            "Universe 為空"
        )

    success_rate = (
        success_count /
        total *
        100
    )

    log(
        f"Universe：{total}"
    )

    log(
        f"成功：{success_count}"
    )

    log(
        f"失敗：{failed_count}"
    )

    log(
        f"成功率：{success_rate:.2f}%"
    )

    log("")

    log("市場別價格成功率")

    markets = set()

    markets.update(
        market_success.keys()
    )

    markets.update(
        market_failed.keys()
    )

    for market in sorted(
        markets
    ):

        ok = market_success.get(
            market,
            0,
        )

        bad = market_failed.get(
            market,
            0,
        )

        total_market = ok + bad

        if total_market > 0:

            rate = (
                ok /
                total_market *
