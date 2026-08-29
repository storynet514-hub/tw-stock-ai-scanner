#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式版 V7.0
============================================================

核心原則
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 直接依 Universe 的 type 判斷 STOCK / ETF
3. 絕不自行猜測 ETF / STOCK
4. STOCK 必須嚴格等於 Universe metadata stock_count
5. ETF 完全不進價格抓取
6. TWSE / TPEx 官方資料優先
7. 官方失敗才使用 Yahoo Finance fallback
8. 不因 ETF 混入造成股票數量膨脹
9. 不使用舊價格資料冒充新資料
10. 所有輸出先寫 temporary directory
11. 完整驗證後 atomic replace
12. 不產生 Data/prices.json

============================================================
Universe 驗證
------------------------------------------------------------

預期：

    stock_count = 1944
    etf_count   = 357
    total       = 2301

重要：

目前 universe.json 的 stocks object
可能同時包含 STOCK + ETF。

因此：

    不可把 len(stocks) 當 STOCK 數量。

必須依：

    item["type"]

嚴格分流。

============================================================
價格來源
------------------------------------------------------------

TWSE：

    官方 STOCK_DAY

TPEx：

    官方 st43_result.php

Yahoo：

    最後 fallback

============================================================
歷史資料
------------------------------------------------------------

完整：

    >= 60 筆

短歷史：

    >= 20 筆

失敗：

    < 20 筆

============================================================
安全機制
------------------------------------------------------------

Universe mismatch
    → FAIL，不開始抓價格

STOCK = 0
    → FAIL

success rate < 80%
    → FAIL

單檔 < 20 筆
    → failed

temporary validation failure
    → FAIL

manifest validation failure
    → FAIL
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import time

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V7.0"
SCHEMA_VERSION = "prices-v7.0"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"


# ============================================================
# Data settings
# ============================================================

START_DATE = "2023-01-01"

MIN_HISTORY_ROWS = 60

ABSOLUTE_MIN_HISTORY_ROWS = 20

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# Safety
# ============================================================

MIN_SUCCESS_RATE = 0.80

MAX_RETRIES = 3

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.12

RETRY_DELAY = 2.0


# ============================================================
# Official URLs
# ============================================================

TWSE_STOCK_DAY_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY"
)

TPEX_ST43_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_trading_info/st43_result.php"
)


# ============================================================
# Yahoo
# ============================================================

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


# ============================================================
# Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Connection": "keep-alive",
    }
)


# ============================================================
# Logging
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# JSON
# ============================================================

def load_json(path: Path) -> Any:

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as f:

        return json.load(f)


def save_json(
    path: Path,
    data: Any,
) -> None:

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
# Text
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


# ============================================================
# Number
# ============================================================

def safe_float(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("--", "")
        .replace("X", "")
    )

    try:

        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def safe_int(value: Any) -> int:

    number = safe_float(value)

    if number is None:
        return 0

    return int(number)


# ============================================================
# Date
# ============================================================

def date_to_timestamp(
    date_string: str,
) -> int:

    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d",
    )

    dt = dt.replace(
        tzinfo=timezone.utc
    )

    return int(
        dt.timestamp()
    )


def parse_date(value: Any) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

    # ROC date
    parts = text.split("/")

    if len(parts) == 3:

        try:

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])

            if year < 1911:
                year += 1911

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except Exception:
            pass

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ):

        try:

            dt = datetime.strptime(
                text,
                fmt,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            continue

    return None


# ============================================================
# Code
# ============================================================

def extract_code(value: Any) -> Optional[str]:

    text = clean_text(value).upper()

    if not text:
        return None

    if text.endswith(".TWO"):
        text = text[:-4]

    elif text.endswith(".TW"):
        text = text[:-3]

    if not text.isdigit():
        return None

    if not 4 <= len(text) <= 6:
        return None

    return text


# ============================================================
# Type
# ============================================================

def normalize_type(
    value: Any,
) -> Optional[str]:

    text = clean_text(value).upper()

    if not text:
        return None

    if text == "STOCK":
        return "STOCK"

    if text == "ETF":
        return "ETF"

    return None


# ============================================================
# Market
# ============================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = clean_text(value).upper()

    if not text:
        return None

    if text in {
        "TW",
        "TWSE",
        "TSE",
        "上市",
    }:
        return "TW"

    if text in {
        "TWO",
        "TPEX",
        "OTC",
        "上櫃",
        "上柜",
    }:
        return "TWO"

    return None


# ============================================================
# Name
# ============================================================

def extract_name(
    item: Dict[str, Any],
) -> str:

    for key in (
        "name",
        "stock_name",
        "security_name",
        "company_name",
        "證券名稱",
        "名稱",
    ):

        value = clean_text(
            item.get(key)
        )

        if value:
            return value

    return ""


# ============================================================
# Full symbol
# ============================================================

def extract_symbol(
    item: Dict[str, Any],
    fallback_key: Optional[str] = None,
) -> Optional[str]:

    for key in (
        "full_symbol",
        "fullSymbol",
        "yahoo_symbol",
        "yahooSymbol",
        "symbol",
    ):

        value = clean_text(
            item.get(key)
        ).upper()

        if value.endswith(".TW"):

            code = extract_code(value)

            if code:
                return code + ".TW"

        if value.endswith(".TWO"):

            code = extract_code(value)

            if code:
                return code + ".TWO"

    if fallback_key:

        value = clean_text(
            fallback_key
        ).upper()

        if value.endswith(".TW"):

            code = extract_code(value)

            if code:
                return code + ".TW"

        if value.endswith(".TWO"):

            code = extract_code(value)

            if code:
                return code + ".TWO"

    return None


# ============================================================
# Extract code from object
# ============================================================

def extract_item_code(
    item: Dict[str, Any],
    symbol: Optional[str],
) -> Optional[str]:

    for key in (
        "code",
        "stock_code",
        "stock_id",
        "ticker",
        "security_code",
        "證券代號",
        "有價證券代號",
        "代號",
    ):

        code = extract_code(
            item.get(key)
        )

        if code:
            return code

    if symbol:

        return extract_code(symbol)

    return None


# ============================================================
# Normalize universe record
# ============================================================

def normalize_record(
    item: Any,
    fallback_key: Optional[str] = None,
) -> Optional[Dict[str, str]]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # type 是唯一 STOCK / ETF 判斷來源。
    # 不再根據名稱、category、instrument_type 猜測。
    # --------------------------------------------------------

    record_type = normalize_type(
        item.get("type")
    )

    if record_type is None:

        return None

    symbol = extract_symbol(
        item,
        fallback_key,
    )

    code = extract_item_code(
        item,
        symbol,
    )

    if not code:
        return None

    market = normalize_market(
        item.get("market")
    )

    if market is None and symbol:

        if symbol.endswith(".TWO"):
            market = "TWO"

        elif symbol.endswith(".TW"):
            market = "TW"

    if market is None:

        return None

    if symbol is None:

        symbol = (
            code
            + (
                ".TWO"
                if market == "TWO"
                else ".TW"
            )
        )

    expected_suffix = (
        ".TWO"
        if market == "TWO"
        else ".TW"
    )

    # --------------------------------------------------------
    # Universe market / symbol 不一致
    # --------------------------------------------------------

    if not symbol.endswith(
        expected_suffix
    ):

        symbol = (
            code
            + expected_suffix
        )

    return {
        "symbol": symbol,
        "code": code,
        "market": market,
        "type": record_type,
        "name": extract_name(item),
    }


# ============================================================
# Universe container
# ============================================================

def extract_container(
    universe: Dict[str, Any],
    key: str,
) -> List[Tuple[Optional[str], Any]]:

    value = universe.get(key)

    result = []

    if isinstance(
        value,
        list,
    ):

        for item in value:
            result.append(
                (None, item)
            )

        return result

    if isinstance(
        value,
        dict,
    ):

        for key_value, item in value.items():

            result.append(
                (
                    str(key_value),
                    item,
                )
            )

    return result


# ============================================================
# Load Universe
# ============================================================

def load_universe() -> Tuple[
    List[Dict[str, str]],
    int,
    int,
]:

    section(
        "讀取 Data/universe.json"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    universe = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        universe,
        dict,
    ):

        raise RuntimeError(
            "universe.json 必須是 object"
        )

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    declared_stock_count = (
        universe.get("stock_count")
    )

    declared_etf_count = (
        universe.get("etf_count")
    )

    if not isinstance(
        declared_stock_count,
        int,
    ):

        raise RuntimeError(
            "universe.json 缺少有效 stock_count"
        )

    if not isinstance(
        declared_etf_count,
        int,
    ):

        raise RuntimeError(
            "universe.json 缺少有效 etf_count"
        )

    log(
        f"Universe metadata stock_count："
        f"{declared_stock_count}"
    )

    log(
        f"Universe metadata etf_count："
        f"{declared_etf_count}"
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # stocks object 可能包含 STOCK + ETF。
    # 必須直接看 type。
    # --------------------------------------------------------

    raw_items = extract_container(
        universe,
        "stocks",
    )

    if not raw_items:

        raw_items = extract_container(
            universe,
            "items",
        )

    if not raw_items:

        raise RuntimeError(
            "Universe 找不到 stocks/items"
        )

    stocks: Dict[
        str,
        Dict[str, str],
    ] = {}

    etfs: Dict[
        str,
        Dict[str, str],
    ] = {}

    invalid = []

    duplicate_stocks = []

    duplicate_etfs = []

    for fallback_key, raw_item in raw_items:

        record = normalize_record(
            raw_item,
            fallback_key,
        )

        if record is None:

            invalid.append(
                fallback_key
            )

            continue

        symbol = record["symbol"]

        if record["type"] == "STOCK":

            if symbol in stocks:

                duplicate_stocks.append(
                    symbol
                )

                continue

            stocks[symbol] = record

        elif record["type"] == "ETF":

            if symbol in etfs:

                duplicate_etfs.append(
                    symbol
                )

                continue

            etfs[symbol] = record

    # --------------------------------------------------------
    # Strict validation
    # --------------------------------------------------------

    actual_stock_count = len(stocks)

    actual_etf_count = len(etfs)

    log(
        f"實際解析 STOCK："
        f"{actual_stock_count}"
    )

    log(
        f"實際解析 ETF："
        f"{actual_etf_count}"
    )

    log(
        f"無法解析："
        f"{len(invalid)}"
    )

    # --------------------------------------------------------
    # NEVER silently continue on Universe mismatch.
    # --------------------------------------------------------

    if actual_stock_count != (
        declared_stock_count
    ):

        sample = [
            value
            for value in invalid
            if value
        ][:20]

        raise RuntimeError(
            "Universe STOCK 數量錯誤："
            f"metadata={declared_stock_count}, "
            f"actual={actual_stock_count}, "
            f"invalid_sample={sample}"
        )

    if actual_etf_count != (
        declared_etf_count
    ):

        raise RuntimeError(
            "Universe ETF 數量錯誤："
            f"metadata={declared_etf_count}, "
            f"actual={actual_etf_count}"
        )

    # --------------------------------------------------------
    # Total consistency
    # --------------------------------------------------------

    total_expected = (
        declared_stock_count
        + declared_etf_count
    )

    total_actual = (
        actual_stock_count
        + actual_etf_count
    )

    if total_actual != total_expected:

        raise RuntimeError(
            "Universe 總數錯誤："
            f"metadata={total_expected}, "
            f"actual={total_actual}"
        )

    # --------------------------------------------------------
    # 7794 diagnostic
    # --------------------------------------------------------

    target = stocks.get(
        "7794.TWO"
    )

    if target:

        log("")
        log(
            "✓ 7794 Universe："
        )
        log(
            f"  code   = {target['code']}"
        )
        log(
            f"  market = {target['market']}"
        )
        log(
            f"  symbol = {target['symbol']}"
        )
        log(
            f"  type   = {target['type']}"
        )

    # --------------------------------------------------------
    # ETF diagnostic
    # --------------------------------------------------------

    if "0050.TW" in stocks:

        raise RuntimeError(
            "Universe 錯誤：0050.TW "
            "被判定為 STOCK。"
            "這代表 type parser 仍然錯誤。"
        )

    if "0050.TW" in etfs:

        log(
            "✓ 0050.TW 正確判定為 ETF"
        )

    log("")
    log(
        "✓ Universe 驗證完全通過"
    )
    log(
        f"✓ STOCK = {actual_stock_count}"
    )
    log(
        f"✓ ETF   = {actual_etf_count}"
    )
    log(
        f"✓ TOTAL = {total_actual}"
    )

    return (
        list(stocks.values()),
        declared_stock_count,
        declared_etf_count,
    )


# ============================================================
# Normalize price rows
# ============================================================

def normalize_price_rows(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    data: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        date_value = parse_date(
            row.get("date")
        )

        if not date_value:
            continue

        close = safe_float(
            row.get("close")
        )

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        open_value = safe_float(
            row.get("open")
        )

        volume = safe_int(
            row.get("volume")
        )

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        if close <= 0:
            continue

        if open_value is None:
            open_value = close

        data[date_value] = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        data.values(),
        key=lambda x: x["date"],
    )


# ============================================================
# TWSE parser
# ============================================================

def parse_twse_rows(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    if not isinstance(
        payload,
        dict,
    ):
        return []

    data = payload.get(
        "data"
    )

    if not isinstance(
        data,
        list,
    ):
        return []

    result = []

    for row in data:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 7:
            continue

        date_value = parse_date(
            row[0]
        )

        if not date_value:
            continue

        volume = safe_int(
            row[1]
        )

        open_value = safe_float(
            row[3]
        )

        high = safe_float(
            row[4]
        )

        low = safe_float(
            row[5]
        )

        close = safe_float(
            row[6]
        )

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        if close <= 0:
            continue

        if open_value is None:
            open_value = close

        result.append(
            {
                "date": date_value,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    return normalize_price_rows(
        result
    )


# ============================================================
# TWSE month
# ============================================================

def fetch_twse_month(
    code: str,
    year: int,
    month: int,
) -> List[Dict[str, Any]]:

    date_value = (
        f"{year}{month:02d}01"
    )

    params = {
        "response": "json",
        "date": date_value,
        "stockNo": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TWSE_STOCK_DAY_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            payload = response.json()

            rows = parse_twse_rows(
                payload
            )

            return rows

        except Exception as exc:

            if attempt >= MAX_RETRIES:

                log(
                    f"      ⚠️ TWSE "
                    f"{code} "
                    f"{year}-{month:02d}: "
                    f"{exc}"
                )

            else:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    return []


# ============================================================
# TWSE history
# ============================================================

def fetch_twse_history(
    code: str,
) -> List[Dict[str, Any]]:

    section(
        f"TWSE 官方資料：{code}.TW"
    )

    all_rows: Dict[
        str,
        Dict[str, Any],
    ] = {}

    start = datetime.strptime(
        START_DATE,
        "%Y-%m-%d",
    )

    now = datetime.now(
        timezone.utc
    )

    year = start.year
    month = start.month

    while (
        year < now.year
        or (
            year == now.year
            and month <= now.month
        )
    ):

        rows = fetch_twse_month(
            code,
            year,
            month,
        )

        for row in rows:

            all_rows[
                row["date"]
            ] = row

        if len(all_rows) >= (
            MIN_HISTORY_ROWS
        ):

            # 不需要繼續抓更舊資料。
            break

        month += 1

        if month == 13:

            month = 1
            year += 1

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda x: x["date"],
    )

    log(
        f"TWSE 官方取得："
        f"{code}.TW "
        f"{len(result)} 筆"
    )

    return result


# ============================================================
# TPEx parser
# ============================================================

def parse_tpex_rows(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    if not isinstance(
        payload,
        dict,
    ):
        return []

    data = payload.get(
        "aaData"
    )

    if not isinstance(
        data,
        list,
    ):
        return []

    result = []

    for row in data:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 7:
            continue

        date_value = parse_date(
            row[0]
        )

        if not date_value:
            continue

        volume = safe_int(
            row[1]
        )

        open_value = safe_float(
            row[3]
        )

        high = safe_float(
            row[4]
        )

        low = safe_float(
            row[5]
        )

        close = safe_float(
            row[6]
        )

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        if close <= 0:
            continue

        if open_value is None:
            open_value = close

        result.append(
            {
                "date": date_value,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    return normalize_price_rows(
        result
    )


# ============================================================
# TPEx month
# ============================================================

def fetch_tpex_month(
    code: str,
    year: int,
    month: int,
) -> List[Dict[str, Any]]:

    roc_year = year - 1911

    params = {
        "l": "zh-tw",
        "d": (
            f"{roc_year:03d}/"
            f"{month:02d}"
        ),
        "stkno": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TPEX_ST43_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            payload = response.json()

            return parse_tpex_rows(
                payload
            )

        except Exception as exc:

            if attempt >= MAX_RETRIES:

                log(
                    f"      ⚠️ TPEx "
                    f"{code} "
                    f"{year}-{month:02d}: "
                    f"{exc}"
                )

            else:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    return []


# ============================================================
# TPEx history
# ============================================================

def fetch_tpex_history(
    code: str,
) -> List[Dict[str, Any]]:

    section(
        f"TPEx 官方資料：{code}.TWO"
    )

    all_rows: Dict[
        str,
        Dict[str, Any],
    ] = {}

    start = datetime.strptime(
        START_DATE,
        "%Y-%m-%d",
    )

    now = datetime.now(
        timezone.utc
    )

    year = start.year
    month = start.month

    while (
        year < now.year
        or (
            year == now.year
            and month <= now.month
        )
    ):

        rows = fetch_tpex_month(
            code,
            year,
            month,
        )

        for row in rows:

            all_rows[
                row["date"]
            ] = row

        if len(all_rows) >= (
            MIN_HISTORY_ROWS
        ):

            break

        month += 1

        if month == 13:

            month = 1
            year += 1

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda x: x["date"],
    )

    log(
        f"TPEx 官方取得："
        f"{code}.TWO "
        f"{len(result)} 筆"
    )

    return result


# ============================================================
# Yahoo parser
# ============================================================

def parse_yahoo_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    chart = payload.get(
        "chart",
        {},
    )

    if not isinstance(
        chart,
        dict,
    ):
        return []

    results = chart.get(
        "result"
    )

    if not isinstance(
        results,
        list,
    ) or not results:

        return []

    first = results[0]

    if not isinstance(
        first,
        dict,
    ):
        return []

    timestamps = first.get(
        "timestamp"
    )

    indicators = first.get(
        "indicators",
        {},
    )

    quote_list = indicators.get(
        "quote",
        [],
    )

    if not timestamps or not quote_list:
        return []

    quote = quote_list[0]

    opens = quote.get(
        "open",
        [],
    )

    highs = quote.get(
        "high",
        [],
    )

    lows = quote.get(
        "low",
        [],
    )

    closes = quote.get(
        "close",
        [],
    )

    volumes = quote.get(
        "volume",
        [],
    )

    result = []

    for index, timestamp in enumerate(
        timestamps
    ):

        try:

            dt = datetime.fromtimestamp(
                int(timestamp),
                tz=timezone.utc,
            )

            date_value = dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:

            continue

        close = (
            safe_float(
                closes[index]
            )
            if index < len(closes)
            else None
        )

        high = (
            safe_float(
                highs[index]
            )
            if index < len(highs)
            else None
        )

        low = (
            safe_float(
                lows[index]
            )
            if index < len(lows)
            else None
        )

        open_value = (
            safe_float(
                opens[index]
            )
            if index < len(opens)
            else None
        )

        volume = (
            safe_int(
                volumes[index]
            )
            if index < len(volumes)
            else 0
        )

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        if close <= 0:
            continue

        if open_value is None:
            open_value = close

        result.append(
            {
                "date": date_value,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    return normalize_price_rows(
        result
    )


# ============================================================
# Yahoo
# ============================================================

def fetch_yahoo(
    symbol: str,
) -> List[Dict[str, Any]]:

    start_ts = date_to_timestamp(
        START_DATE
    )

    end_ts = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    params = {
        "period1": start_ts,
        "period2": end_ts,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                YAHOO_URL.format(
                    symbol=symbol
                ),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            return parse_yahoo_payload(
                payload
            )

        except Exception as exc:

            if attempt >= MAX_RETRIES:

                log(
                    f"      ⚠️ Yahoo "
                    f"{symbol}: "
                    f"{exc}"
                )

            else:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    return []


# ============================================================
# Stock history
# ============================================================

def fetch_stock_history(
    item: Dict[str, str],
) -> Tuple[
    List[Dict[str, Any]],
    str,
    str,
]:

    symbol = item["symbol"]

    code = item["code"]

    market = item["market"]

    # --------------------------------------------------------
    # 官方優先
    # --------------------------------------------------------

    if market == "TW":

        log(
            f"→ 官方來源優先："
            f"{symbol} "
            f"{item['name']}"
        )

        rows = fetch_twse_history(
            code
        )

        if len(rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            return (
                rows,
                (
                    "TWSE official"
                    if len(rows) >= MIN_HISTORY_ROWS
                    else "TWSE official (short history)"
                ),
                "",
            )

    elif market == "TWO":

        log(
            f"→ 官方來源優先："
            f"{symbol} "
            f"{item['name']}"
        )

        rows = fetch_tpex_history(
            code
        )

        if len(rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            return (
                rows,
                (
                    "TPEx official"
                    if len(rows) >= MIN_HISTORY_ROWS
                    else "TPEx official (short history)"
                ),
                "",
            )

    # --------------------------------------------------------
    # Yahoo fallback
    # --------------------------------------------------------

    log(
        f"→ {symbol} "
        f"啟動 Yahoo 最後備援"
    )

    yahoo_rows = fetch_yahoo(
        symbol
    )

    if len(yahoo_rows) >= (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        return (
            yahoo_rows,
            (
                "Yahoo fallback"
                if len(yahoo_rows) >= MIN_HISTORY_ROWS
                else "Yahoo fallback (short history)"
            ),
            "official source insufficient",
        )

    return (
        [],
        "",
        (
            "official source insufficient; "
            f"Yahoo only {len(yahoo_rows)} rows"
        ),
    )


# ============================================================
# Single stock
# ============================================================

def fetch_one(
    item: Dict[str, str],
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    rows, source, reason = (
        fetch_stock_history(
            item
        )
    )

    rows = normalize_price_rows(
        rows
    )

    if len(rows) < (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        return (
            None,
            reason
            or (
                f"insufficient history: "
                f"{len(rows)}"
            ),
        )

    status = (
        "complete"
        if len(rows) >= MIN_HISTORY_ROWS
        else "short_history"
    )

    result = {
        "symbol": item["symbol"],
        "code": item["code"],
        "market": item["market"],
        "name": item["name"],
        "source": source,
        "history_rows": len(rows),
        "history_status": status,
        "latest_date": rows[-1]["date"],
        "prices": rows,
    }

    if reason:
        result["fallback_reason"] = reason

    log(
        f"✓ {item['symbol']} "
        f"{item['name']} "
        f"→ {len(rows)} 筆 "
        f"→ {source}"
    )

    return (
        result,
        "",
    )


# ============================================================
# Build shards
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any],
    ],
) -> List[
    Dict[str, Any]
]:

    symbols = sorted(
        results.keys()
    )

    shards = []

    for start in range(
        0,
        len(symbols),
        STOCKS_PER_FILE,
    ):

        chunk = symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        stocks = {}

        for symbol in chunk:

            stocks[symbol] = (
                results[symbol]["prices"]
            )

        shards.append(
            {
                "stocks": stocks
            }
        )

    return shards


# ============================================================
# Validate shard
# ============================================================

def validate_shard(
    path: Path,
    expected_symbols: List[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            f"shard 不存在："
            f"{path.name}"
        )

    if path.stat().st_size > (
        MAX_FILE_SIZE_BYTES
    ):

        raise RuntimeError(
            f"shard 超過 "
            f"{MAX_FILE_SIZE_MB} MB："
            f"{path.name}"
        )

    data = load_json(
        path
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} 根節點錯誤"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} 缺少 stocks"
        )

    if set(stocks.keys()) != set(
        expected_symbols
    ):

        missing = sorted(
            set(expected_symbols)
            - set(stocks.keys())
        )

        extra = sorted(
            set(stocks.keys())
            - set(expected_symbols)
        )

        raise RuntimeError(
            f"{path.name} 股票不一致："
            f"missing={missing[:20]} "
            f"extra={extra[:20]}"
        )

    for symbol, rows in stocks.items():

        if not isinstance(
            rows,
            list,
        ):

            raise RuntimeError(
                f"{symbol} prices 必須為 list"
            )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{symbol} "
                f"歷史資料不足："
                f"{len(rows)}"
            )

        previous = ""

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} "
                    f"存在錯誤 price row"
                )

            required = {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }

            missing = (
                required
                - set(row.keys())
            )

            if missing:

                raise RuntimeError(
                    f"{symbol} "
                    f"缺少欄位："
                    f"{sorted(missing)}"
                )

            date_value = (
                str(row["date"])
            )

            if (
                previous
                and date_value < previous
            ):

                raise RuntimeError(
                    f"{symbol} 日期未排序"
                )

            previous = date_value


# ============================================================
# Manifest
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any],
    ],
    universe_count: int,
) -> Dict[str, Any]:

    complete = 0
    short = 0

    sources: Dict[
        str,
        int,
    ] = {}

    latest_dates = []

    for result in results.values():

        if result.get(
            "history_status"
        ) == "complete":

            complete += 1

        else:

            short += 1

        source = result.get(
            "source",
            "",
        )

        sources[source] = (
            sources.get(
                source,
                0,
            )
            + 1
        )

        latest = result.get(
            "latest_date"
        )

        if latest:
            latest_dates.append(
                latest
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_stock_count": universe_count,
        "price_stock_count": len(results),
        "complete_history_count": complete,
        "short_history_count": short,
        "failed_count": (
            universe_count
            - len(results)
        ),
        "min_history_rows": MIN_HISTORY_ROWS,
        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),
        "sources": sources,
        "latest_date": (
            max(latest_dates)
            if latest_dates
            else None
        ),
        "files": shard_files,
    }


# ============================================================
# Validate manifest
# ============================================================

def validate_manifest(
    path: Path,
    expected_symbols: List[str],
    expected_shards: List[str],
    universe_count: int,
) -> None:

    manifest = load_json(
        path
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest 格式錯誤"
        )

    if manifest.get(
        "universe_stock_count"
    ) != universe_count:

        raise RuntimeError(
            "manifest universe_stock_count 錯誤："
            f"{manifest.get('universe_stock_count')} "
            f"!= {universe_count}"
        )

    if manifest.get(
        "price_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest price_stock_count 錯誤："
            f"{manifest.get('price_stock_count')} "
            f"!= {len(expected_symbols)}"
        )

    files = manifest.get(
        "files"
    )

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 錯誤"
        )


# ============================================================
# Write temporary output
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[
        str,
        Dict[str, Any],
    ],
    universe_count: int,
) -> None:

    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    shards = build_shards(
        results
    )

    shard_files = []

    symbols = sorted(
        results.keys()
    )

    for index, shard in enumerate(
        shards,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = (
            temp_dir
            / filename
        )

        save_json(
            path,
            shard,
        )

        start = (
            (index - 1)
            * STOCKS_PER_FILE
        )

        expected = symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        validate_shard(
            path,
            expected,
        )

        shard_files.append(
            filename
        )

    manifest = build_manifest(
        shard_files,
        results,
        universe_count,
    )

    manifest_path = (
        temp_dir
        / "manifest.json"
    )

    save_json(
        manifest_path,
        manifest,
    )

    validate_manifest(
        manifest_path,
        symbols,
        shard_files,
        universe_count,
    )


# ============================================================
# Atomic replace
# ============================================================

def replace_output(
    temp_dir: Path,
) -> None:

    backup_dir = (
        DATA_DIR
        / ".prices_backup"
    )

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )

    if OUTPUT_DIR.exists():

        OUTPUT_DIR.rename(
            backup_dir
        )

    try:

        temp_dir.rename(
            OUTPUT_DIR
        )

    except Exception:

        if OUTPUT_DIR.exists():

            shutil.rmtree(
                OUTPUT_DIR
            )

        if backup_dir.exists():

            backup_dir.rename(
                OUTPUT_DIR
            )

        raise

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )


# ============================================================
# Main
# ============================================================

def main() -> int:

    started = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    try:

        (
            universe,
            universe_stock_count,
            universe_etf_count,
        ) = load_universe()

    except Exception as exc:

        log("")
        log(
            f"❌ Universe 驗證失敗："
            f"{exc}"
        )

        return 1

    universe_count = len(
        universe
    )

    # --------------------------------------------------------
    # Final hard check
    # --------------------------------------------------------

    if universe_count != (
        universe_stock_count
    ):

        log(
            "❌ Universe STOCK 數量不一致"
        )

        return 1

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

    section(
        f"開始官方資料抓取："
        f"{universe_count} 檔 STOCK"
    )

    results: Dict[
        str,
        Dict[str, Any],
    ] = {}

    failures: Dict[
        str,
        str,
    ] = {}

    source_counts: Dict[
        str,
        int,
    ] = {}

    for index, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item["symbol"]

        log(
            f"[{index}/{universe_count}] "
            f"{symbol} "
            f"{item['name']}"
        )

        try:

            result, reason = fetch_one(
                item
            )

            if result is None:

                failures[symbol] = reason

                log(
                    f"❌ {symbol} "
                    f"→ {reason}"
                )

            else:

                results[symbol] = result

                source = result[
                    "source"
                ]

                source_counts[
                    source
                ] = (
                    source_counts.get(
                        source,
                        0,
                    )
                    + 1
                )

        except Exception as exc:

            failures[symbol] = str(exc)

            log(
                f"❌ {symbol} "
                f"未預期錯誤："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    success_count = len(
        results
    )

    failed_count = len(
        failures
    )

    success_rate = (
        success_count
        / universe_count
        if universe_count
        else 0
    )

    section(
        "價格資料結果"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    log(
        f"成功："
        f"{success_count}"
    )

    log(
        f"失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # --------------------------------------------------------
    # Missing
    # --------------------------------------------------------

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    missing_symbols = (
        expected_symbols
        - set(results.keys())
    )

    if missing_symbols:

        log("")
        log(
            "⚠️ 尚有缺少價格資料："
            f"{len(missing_symbols)} 檔"
        )

        for symbol in sorted(
            missing_symbols
        ):

            log(
                f"  {symbol}: "
                f"{failures.get(symbol, '')}"
            )

    # --------------------------------------------------------
    # Success gate
    # --------------------------------------------------------

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

        log("")
        log(
            "❌ 成功率低於安全門檻"
        )

        return 1

    # --------------------------------------------------------
    # Build temporary
    # --------------------------------------------------------

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="prices_build_",
            dir=str(DATA_DIR),
        )
    )

    temp_dir = (
        temp_root
        / "prices"
    )

    try:

        section(
            "建立 temporary Data/prices"
        )

        write_price_directory(
            temp_dir,
            results,
            universe_count,
        )

        # ----------------------------------------------------
        # 7794
        # ----------------------------------------------------

        if "7794.TWO" in expected_symbols:

            log("")
            log(
                "================================================"
            )

            if "7794.TWO" in results:

                target = results[
                    "7794.TWO"
                ]

                log(
                    "✓ 7794.TWO 最終驗證"
                )

                log(
                    f"資料筆數："
                    f"{target['history_rows']}"
                )

                log(
                    f"來源："
                    f"{target['source']}"
                )

                log(
                    f"最新日期："
                    f"{target['latest_date']}"
                )

                log(
                    f"狀態："
                    f"{target['history_status']}"
                )

            else:

                log(
                    "❌ 7794.TWO "
                    "仍然缺少價格資料"
                )

            log(
                "================================================"
            )

        # ----------------------------------------------------
        # Replace
        # ----------------------------------------------------

        section(
            "替換正式 Data/prices"
        )

        replace_output(
            temp_dir
        )

        log(
            "✓ Data/prices/ 已成功更新"
        )

    except Exception as exc:

        log("")
        log(
            f"❌ 價格資料建置失敗："
            f"{exc}"
        )

        return 1

    finally:

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - started
    )

    section(
        "FINAL PRICE RESULT"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    log(
        f"Price 成功："
        f"{success_count}"
    )

    log(
        f"Price 失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    log(
        f"Universe ETF："
        f"{universe_etf_count}"
    )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ fetch_prices.py 完成"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )