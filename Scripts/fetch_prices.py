#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V4.0

功能：
1. 讀取 Data/universe.json
2. 取得全市場股票 2 年歷史日線
3. 使用 Yahoo Finance
4. 輸出至 Data/prices/
5. 每 100 檔股票一個 JSON
6. 產生 Data/prices/manifest.json
7. 驗證成功率
8. 驗證所有輸出 JSON
9. 驗證通過後才替換既有價格資料

不再產生：
Data/prices.json

輸出：

Data/
└── prices/
    ├── prices_001.json
    ├── prices_002.json
    ├── ...
    └── manifest.json
"""

import json
import math
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

STOCKS_PER_FILE = 100

MIN_SUCCESS_RATE = 80.0

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

YAHOO_RANGE = "2y"
YAHOO_INTERVAL = "1d"

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 45

MAX_RETRIES = 3
RETRY_DELAY = 2.0
REQUEST_DELAY = 0.08

MAX_FILE_SIZE_MB = 80.0

MIN_HISTORY_ROWS = 120


# ============================================================
# HTTP Session
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
# JSON
# ============================================================

def load_json(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# 數值
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
        return int(float(value))

    except Exception:
        return None


def safe_round(value, digits=4):
    number = to_float(value)

    if number is None:
        return None

    return round(number, digits)


# ============================================================
# 股票代號
# ============================================================

def normalize_code(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    upper = text.upper()

    if upper.endswith(".TWO"):
        text = text[:-4]

    elif upper.endswith(".TW"):
        text = text[:-3]

    text = text.strip()

    if not text.isdigit():
        return None

    if len(text) != 4:
        return None

    return text


# ============================================================
# 市場
# ============================================================

def normalize_market(value):
    if value is None:
        return ""

    text = str(value).strip().upper()

    if text in {
        "TPEX",
        "TPEX",
        "OTC",
        "TWO",
        "上櫃",
        "櫃買",
    }:
        return "TPEx"

    if text in {
        "TWSE",
        "TW",
        "上市",
    }:
        return "TWSE"

    return ""


# ============================================================
# Yahoo Symbol
# ============================================================

def yahoo_symbol(code, market):
    if market == "TPEx":
        return code + ".TWO"

    return code + ".TW"


# ============================================================
# Universe 結構
# ============================================================

def get_items(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    keys = (
        "items",
        "stocks",
        "data",
        "universe",
        "symbols",
        "records",
    )

    for key in keys:

        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):

            result = []

            for code, item in value.items():

                if isinstance(item, dict):

                    copied = dict(item)

                    if "code" not in copied:
                        copied["code"] = code

                    result.append(copied)

                else:

                    result.append(
                        {
                            "code": code,
                            "name": str(item),
                        }
                    )

            if result:
                return result

    return []


# ============================================================
# Universe 欄位
# ============================================================

def get_code(item):

    if isinstance(item, str):
        return normalize_code(item)

    if not isinstance(item, dict):
        return None

    keys = (
        "code",
        "stock_code",
        "stockCode",
        "symbol",
        "ticker",
        "證券代號",
        "股票代號",
        "代號",
    )

    for key in keys:

        if key not in item:
            continue

        code = normalize_code(
            item.get(key)
        )

        if code:
            return code

    return None


def get_name(item):

    if isinstance(item, str):
        return ""

    if not isinstance(item, dict):
        return ""

    keys = (
        "name",
        "stock_name",
        "stockName",
        "short_name",
        "名稱",
        "證券名稱",
        "股票名稱",
    )

    for key in keys:

        if key not in item:
            continue

        value = item.get(key)

        if value is None:
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


def get_market(item):

    if isinstance(item, str):
        return ""

    if not isinstance(item, dict):
        return ""

    keys = (
        "market",
        "market_type",
        "marketType",
        "exchange",
        "市場",
        "市場別",
        "交易所",
    )

    for key in keys:

        if key not in item:
            continue

        market = normalize_market(
            item.get(key)
        )

        if market:
            return market

    return ""


# ============================================================
# 讀取 Universe
# ============================================================

def load_universe():

    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    data = load_json(
        UNIVERSE_FILE
    )

    items = get_items(data)

    if not items:
        raise RuntimeError(
            "universe.json 沒有有效股票資料"
        )

    stocks = {}

    invalid = 0

    for item in items:

        code = get_code(item)

        if code is None:
            invalid += 1
            continue

        name = get_name(item)

        market = get_market(item)

        if not market:
            market = "TWSE"

        symbol = yahoo_symbol(
            code,
            market,
        )

        if code in stocks:
            continue

        stocks[code] = {
            "code": code,
            "name": name,
            "market": market,
            "symbol": symbol,
        }

    log(
        f"Universe 原始項目：{len(items)}"
    )

    log(
        f"合法股票代號：{len(stocks)}"
    )

    log(
        f"無效項目：{invalid}"
    )

    if not stocks:
        raise RuntimeError(
            "Universe 沒有解析出任何合法台股代號"
        )

    log("")
    log("前 20 個合法標的：")

    for index, stock in enumerate(
        list(stocks.values())[:20],
        start=1,
    ):

        log(
            f"{index:3d}. "
            f"{stock['symbol']} | "
            f"{stock['name']}"
        )

    return stocks


# ============================================================
# Yahoo Finance
# ============================================================

def fetch_stock(symbol):

    url = YAHOO_URL.format(
        symbol=symbol
    )

    params = {
        "range": YAHOO_RANGE,
        "interval": YAHOO_INTERVAL,
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
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
            )

            log(
                f"      HTTP "
                f"{attempt}/{MAX_RETRIES}："
                f"{response.status_code}"
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {}
            )

            chart_error = chart.get(
                "error"
            )

            if chart_error:
                raise RuntimeError(
                    str(chart_error)
                )

            results = chart.get(
                "result"
            )

            if not results:
                raise RuntimeError(
                    "Yahoo result 為空"
                )

            result = results[0]

            timestamps = result.get(
                "timestamp"
            )

            indicators = result.get(
                "indicators",
                {}
            )

            quotes = indicators.get(
                "quote",
                []
            )

            if not timestamps:
                raise RuntimeError(
                    "沒有 timestamp"
                )

            if not quotes:
                raise RuntimeError(
                    "沒有 quote"
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

            adjusted = []

            adj_group = indicators.get(
                "adjclose",
                []
            )

            if adj_group:
                adjusted = adj_group[0].get(
                    "adjclose",
                    []
                )

            rows = []

            for i, timestamp in enumerate(
                timestamps
            ):

                if timestamp is None:
                    continue

                if i >= len(closes):
                    continue

                close = to_float(
                    closes[i]
                )

                if close is None:
                    continue

                try:

                    date_text = datetime.fromtimestamp(
                        int(timestamp),
                        timezone.utc,
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

                adjusted_value = (
                    adjusted[i]
                    if i < len(adjusted)
                    else None
                )

                rows.append(
                    {
                        "date": date_text,
                        "open": safe_round(
                            open_value
                        ),
                        "high": safe_round(
                            high_value
                        ),
                        "low": safe_round(
                            low_value
                        ),
                        "close": safe_round(
                            close
                        ),
                        "volume": to_int(
                            volume_value
                        ),
                        "adj_close": safe_round(
                            adjusted_value
                        ),
                    }
                )

            if not rows:
                raise RuntimeError(
                    "沒有有效歷史價格"
                )

            unique = {}

            for row in rows:
                unique[row["date"]] = row

            rows = list(
                unique.values()
            )

            rows.sort(
                key=lambda x: x["date"]
            )

            return rows

        except Exception as error:

            last_error = error

            log(
                f"      ⚠ 失敗：{error}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"Yahoo 取得失敗：{last_error}"
    )


# ============================================================
# 取得全部價格
# ============================================================

def fetch_all(stocks):

    section("開始取得歷史價格")

    total = len(stocks)

    success = {}
    failed = {}

    market_success = {}
    market_failed = {}

    for index, stock in enumerate(
        stocks.values(),
        start=1,
    ):

        code = stock["code"]
        name = stock["name"]
        market = stock["market"]
        symbol = stock["symbol"]

        log(
            f"[{index}/{total}] "
            f"{code} {name} | "
            f"{market} | {symbol}"
        )

        try:

            rows = fetch_stock(
                symbol
            )

            if len(rows) < MIN_HISTORY_ROWS:
                raise RuntimeError(
                    f"歷史資料不足：{len(rows)} 筆"
                )

            success[code] = {
                "code": code,
                "symbol": symbol,
                "name": name,
                "market": market,
                "data_start": rows[0]["date"],
                "data_end": rows[-1]["date"],
                "count": len(rows),
                "data": rows,
            }

            market_success[market] = (
                market_success.get(
                    market,
                    0
                ) + 1
            )

            log(
                f"      ✓ {len(rows)} 筆"
            )

        except Exception as error:

            failed[code] = {
                "code": code,
                "name": name,
                "market": market,
                "symbol": symbol,
                "error": str(error),
            }

            market_failed[market] = (
                market_failed.get(
                    market,
                    0
                ) + 1
            )

            log(
                f"      ✗ {error}"
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
# 驗證成功率
# ============================================================

def validate_success(
    total,
    success,
    failed,
    market_success,
    market_failed,
):

    section("價格資料驗證")

    success_count = len(success)
    failed_count = len(failed)

    if total <= 0:
        raise RuntimeError(
            "Universe 為空"
        )

    success_rate = (
        success_count
        * 100.0
        / total
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

    for market in sorted(markets):

        ok = market_success.get(
            market,
            0
        )

        bad = market_failed.get(
            market,
            0
        )

        market_total = ok + bad

        if market_total == 0:
            continue

        market_rate = (
            ok
            * 100.0
            / market_total
        )

        log(
            f"{market}："
            f"{ok}/{market_total} "
            f"({market_rate:.2f}%)"
        )

    if success_rate < MIN_SUCCESS_RATE:
        raise RuntimeError(
            f"價格成功率只有 "
            f"{success_rate:.2f}%，"
            f"低於 {MIN_SUCCESS_RATE:.0f}%，"
            f"停止更新。"
        )

    if success_count == 0:
        raise RuntimeError(
            "完全沒有取得有效價格資料"
        )

    log("")
    log("✓ 價格資料驗證通過")

    return success_rate


# ============================================================
# 分割股票
# ============================================================

def make_chunks(records):

    items = list(
        records.items()
    )

    chunks = []

    for start in range(
        0,
        len(items),
        STOCKS_PER_FILE,
    ):

        chunks.append(
            dict(
                items[
                    start:
                    start + STOCKS_PER_FILE
                ]
            )
        )

    return chunks


# ============================================================
# 建立價格分檔
# ============================================================

def write_price_files(
    records,
    output_dir,
):

    section("建立 Data/prices/ 分檔")

    if output_dir.exists():
        shutil.rmtree(
            output_dir
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    chunks = make_chunks(
        records
    )

    shard_count = len(chunks)

    files = []

    total_bytes = 0

    for number, chunk in enumerate(
        chunks,
        start=1,
    ):

        filename = (
            f"prices_{number:03d}.json"
        )

        path = (
            output_dir
            / filename
        )

        data = {
            "schema_version": "prices-v4.0",
            "version": VERSION,
            "generated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "market": "TW",
            "shard": number,
            "shards": shard_count,
            "count": len(chunk),
            "stocks": chunk,
        }

        save_json(
            path,
            data,
        )

        size_bytes = path.stat().st_size

        size_mb = (
            si
