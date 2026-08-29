#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py
正式版 V5.0

============================================================
資料責任
============================================================

1. Data/universe.json 是唯一 Universe 來源
2. 只處理 STOCK
3. ETF 完全排除於 STOCK Price Gate
4. TWSE STOCK 優先使用 TWSE 官方資料
5. TPEX STOCK 優先使用 TPEx 官方資料
6. Yahoo Finance 只能作為最後備援
7. 不使用 CMoney
8. 不使用舊 prices 冒充新資料
9. 不因單一股票失敗而靜默遺漏
10. 所有結果先寫入 temporary directory
11. 完整驗證後才 atomic replace
12. 每 100 檔股票一個 shard
13. 不產生 Data/prices.json

============================================================
V5.0 核心架構
============================================================

Universe
    │
    ├── TWSE STOCK
    │       │
    │       └── TWSE 官方 STOCK_DAY
    │
    └── TPEX STOCK
            │
            └── TPEx 官方 st43_result.php
                    │
                    └── 逐月累積歷史資料

官方資料不足
    │
    └── Yahoo Finance 最後備援

============================================================
資料來源優先順序
============================================================

TWSE:
    1. TWSE official
    2. Yahoo fallback

TPEX:
    1. TPEx official
    2. Yahoo fallback

注意：
    Yahoo 絕對不是主要來源。

============================================================
TPEx 官方資料
============================================================

官方頁面：

https://www.tpex.org.tw/web/stock/
aftertrading/daily_trading_info/st43.php

官方查詢 endpoint：

https://www.tpex.org.tw/web/stock/
aftertrading/daily_trading_info/st43_result.php

參數：

    d     = 民國年月，例如 115/08
    stkno = 股票代碼

官方回傳：

    aaData

欄位：

    0 日期
    1 成交股數
    2 成交金額
    3 開盤
    4 最高
    5 最低
    6 收盤
    7 漲跌
    8 成交筆數

============================================================
TWSE 官方資料
============================================================

官方 endpoint：

https://www.twse.com.tw/exchangeReport/STOCK_DAY

參數：

    response=json
    date=YYYYMMDD
    stockNo=股票代碼

============================================================
歷史資料規則
============================================================

正常：
    >= 60 筆

新掛牌股票：
    20 ~ 59 筆允許
    history_status = short_history

少於：
    < 20 筆 → FAIL

============================================================
安全 Gate
============================================================

成功率 < 80%
    → FAIL

官方資料不足時可以使用 Yahoo
但會明確標記：

    Yahoo fallback

不允許把 Yahoo 資料標記成官方。

============================================================
7794 特別驗證
============================================================

7794.TWO：

    market = TPEX
    → TPEx official
    → 不先查 Yahoo

最後 log 明確顯示：

    7794.TWO
    source = TPEx official
    history_rows = XXX

============================================================
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
# 基本設定
# ============================================================

VERSION = "V5.0"

SCHEMA_VERSION = "prices-v5.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"


# ============================================================
# 歷史資料設定
# ============================================================

START_DATE = "2023-01-01"

MIN_HISTORY_ROWS = 60

ABSOLUTE_MIN_HISTORY_ROWS = 20


# ============================================================
# 官方 API
# ============================================================

TWSE_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY"
)

TPEX_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_trading_info/"
    "st43_result.php"
)

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


# ============================================================
# Request
# ============================================================

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

TPEX_FALLBACK_MONTHS = 24

TWSE_FETCH_MONTHS = 48

STOCKS_PER_FILE = 100

MIN_SUCCESS_RATE = 0.80

MAX_FILE_SIZE_MB = 80.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
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
# Log
# ============================================================

def log(message: str = "") -> None:

    print(
        message,
        flush=True
    )


def section(title: str) -> None:

    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# JSON
# ============================================================

def load_json(
    path: Path,
) -> Any:

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:

        return json.load(file)


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
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# Numeric
# ============================================================

def safe_float(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    try:

        text = (
            str(value)
            .replace(",", "")
            .replace(" ", "")
            .strip()
        )

        if not text:
            return None

        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def safe_int(
    value: Any,
) -> int:

    number = safe_float(value)

    if number is None:
        return 0

    return int(number)


# ============================================================
# Text
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


# ============================================================
# Date
# ============================================================

def parse_iso_date(
    value: Any,
) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

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


def parse_tpex_date(
    value: Any,
) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

    parts = text.split("/")

    if len(parts) == 3:

        try:

            year = int(parts[0])

            month = int(parts[1])

            day = int(parts[2])

            if year < 1911:
                year += 1911

            dt = datetime(
                year,
                month,
                day,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            pass

    return parse_iso_date(
        text
    )


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


# ============================================================
# Month generator
# ============================================================

def month_sequence(
    months: int,
) -> List[Tuple[int, int]]:

    now = datetime.now(
        timezone.utc
    )

    year = now.year

    month = now.month

    result = []

    for _ in range(months):

        result.append(
            (
                year,
                month,
            )
        )

        month -= 1

        if month == 0:

            month = 12
            year -= 1

    return list(
        reversed(result)
    )


# ============================================================
# Code
# ============================================================

def extract_code(
    value: Any,
) -> Optional[str]:

    text = clean_text(
        value
    ).upper()

    if not text:
        return None

    if text.endswith(".TWO"):
        text = text[:-4]

    elif text.endswith(".TW"):
        text = text[:-3]

    if not text.isdigit():
        return None

    if not (
        4 <= len(text) <= 6
    ):
        return None

    return text


# ============================================================
# Full Symbol
# ============================================================

def extract_full_symbol(
    item: Any,
) -> Optional[str]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    for key in (
        "full_symbol",
        "fullSymbol",
        "yahoo_symbol",
        "yahooSymbol",
        "symbol",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if text.endswith(".TW"):

            code = extract_code(text)

            if code:
                return (
                    code + ".TW"
                )

        if text.endswith(".TWO"):

            code = extract_code(text)

            if code:
                return (
                    code + ".TWO"
                )

    return None


# ============================================================
# Market
# ============================================================

def detect_market(
    item: Any,
) -> Optional[str]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    full_symbol = extract_full_symbol(
        item
    )

    if full_symbol:

        if full_symbol.endswith(".TWO"):
            return "TWO"

        if full_symbol.endswith(".TW"):
            return "TW"

    for key in (
        "market",
        "exchange",
        "market_type",
        "marketType",
        "board",
        "市場",
        "市場別",
        "交易所",
        "掛牌市場",
        "上市櫃",
        "上市櫃別",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if not text:
            continue

        if (
            text in {
                "TPEX",
                "TWO",
                "OTC",
                "O",
            }
            or "TPEX" in text
            or "OTC" in text
            or "上櫃" in text
            or "上柜" in text
            or "櫃買" in text
            or "柜买" in text
        ):
            return "TWO"

        if (
            text in {
                "TWSE",
                "TW",
                "TSE",
            }
            or "TWSE" in text
            or "上市" in text
        ):
            return "TW"

    return None


# ============================================================
# Type
# ============================================================

def detect_type(
    item: Any,
) -> str:

    if not isinstance(
        item,
        dict,
    ):
        return "Stock"

    for key in (
        "type",
        "security_type",
        "securityType",
        "category",
        "instrument_type",
        "instrumentType",
        "類型",
        "商品類型",
        "證券類型",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if (
            text == "ETF"
            or "ETF" in text
        ):
            return "ETF"

        if (
            text == "STOCK"
            or "STOCK" in text
            or "股票" in text
        ):
            return "Stock"

    return "Stock"


# ============================================================
# Name
# ============================================================

def extract_name(
    item: Any,
) -> str:

    if not isinstance(
        item,
        dict,
    ):
        return ""

    for key in (
        "name",
        "stock_name",
        "company_name",
        "security_name",
        "名稱",
        "證券名稱",
        "公司名稱",
    ):

        value = clean_text(
            item.get(key)
        )

        if value:
            return value

    return ""


# ============================================================
# Yahoo Symbol
# ============================================================

def build_yahoo_symbol(
    code: str,
    market: str,
    full_symbol: Optional[str] = None,
) -> str:

    if full_symbol:

        full = clean_text(
            full_symbol
        ).upper()

        if (
            full.endswith(".TW")
            or full.endswith(".TWO")
        ):
            return full

    if market == "TWO":

        return (
            code + ".TWO"
        )

    return (
        code + ".TW"
    )


# ============================================================
# Normalize Universe item
# ============================================================

def normalize_item(
    item: Any,
    forced_type: Optional[str] = None,
) -> Optional[Dict[str, str]]:

    if isinstance(
        item,
        str,
    ):

        text = clean_text(
            item
        ).upper()

        code = extract_code(
            text
        )

        if not code:
            return None

        market = (
            "TWO"
            if text.endswith(".TWO")
            else "TW"
        )

        return {
            "symbol": build_yahoo_symbol(
                code,
                market,
                text,
            ),
            "code": code,
            "market": market,
            "name": "",
            "type": (
                forced_type
                or "Stock"
            ),
        }

    if not isinstance(
        item,
        dict,
    ):
        return None

    full_symbol = extract_full_symbol(
        item
    )

    code = None

    for key in (
        "symbol",
        "code",
        "stock_id",
        "stock_code",
        "ticker",
        "證券代號",
        "有價證券代號",
        "代號",
    ):

        code = extract_code(
            item.get(key)
        )

        if code:
            break

    if code is None and full_symbol:

        code = extract_code(
            full_symbol
        )

    if code is None:
        return None

    market = detect_market(
        item
    )

    if market is None and full_symbol:

        if full_symbol.endswith(".TWO"):
            market = "TWO"

        elif full_symbol.endswith(".TW"):
            market = "TW"

    if market is None:

        market = "TW"

    security_type = (
        forced_type
        or detect_type(item)
    )

    return {
        "symbol": build_yahoo_symbol(
            code,
            market,
            full_symbol,
        ),
        "code": code,
        "market": market,
        "name": extract_name(item),
        "type": security_type,
    }


# ============================================================
# Universe container
# ============================================================

def extract_container(
    universe: Dict[str, Any],
    key: str,
) -> List[Any]:

    value = universe.get(
        key
    )

    if isinstance(
        value,
        list,
    ):
        return value

    if isinstance(
        value,
        dict,
    ):

        result = []

        for symbol, item in value.items():

            if isinstance(
                item,
                dict,
            ):

                record = dict(
                    item
                )

                if not (
                    record.get("symbol")
                    or record.get("code")
                    or record.get("stock_code")
                ):

                    record["symbol"] = symbol

                result.append(
                    record
                )

            else:

                result.append(
                    {
                        "symbol": symbol,
                        "value": item,
                    }
                )

        return result

    return []


# ============================================================
# Load Universe
# ============================================================

def load_universe() -> List[Dict[str, str]]:

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
            "universe.json 根節點必須是 object"
        )

    stocks_raw = extract_container(
        universe,
        "stocks",
    )

    if not stocks_raw:

        stocks_raw = extract_container(
            universe,
            "items",
        )

    etfs_raw = extract_container(
        universe,
        "etfs",
    )

    if not stocks_raw:

        raise RuntimeError(
            "Universe 找不到 stocks/items"
        )

    stocks = {}

    skipped = 0

    for item in stocks_raw:

        normalized = normalize_item(
            item,
            forced_type="Stock",
        )

        if normalized is None:

            skipped += 1

            continue

        if normalized["type"] != "Stock":

            continue

        symbol = normalized[
            "symbol"
        ]

        if symbol in stocks:

            continue

        stocks[
            symbol
        ] = normalized

    if not stocks:

        raise RuntimeError(
            "Universe STOCK 為空"
        )

    declared_count = universe.get(
        "stock_count"
    )

    if isinstance(
        declared_count,
        int,
    ):

        log(
            "Universe metadata "
            f"stock_count：{declared_count}"
        )

        log(
            "實際 STOCK："
            f"{len(stocks)}"
        )

    log(
        f"Universe STOCK："
        f"{len(stocks)} 檔"
    )

    log(
        f"Universe ETF："
        f"{len(etfs_raw)} 檔"
    )

    if skipped:

        log(
            "⚠️ 無法解析 STOCK："
            f"{skipped} 檔"
        )

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

    return list(
        stocks.values()
    )


# ============================================================
# Normalize OHLCV row
# ============================================================

def normalize_price_rows(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    normalized = {}

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        date_value = parse_iso_date(
            row.get("date")
        )

        if not date_value:
            continue

        open_value = safe_float(
            row.get("open")
        )

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        close = safe_float(
            row.get("close")
        )

        volume = safe_int(
            row.get("volume")
        )

        if (
            high is None
            or low is None
            or close is None
        ):
            continue

        if close <= 0:
            continue

        if open_value is None:

            open_value = close

        normalized[
            date_value
        ] = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        normalized.values(),
        key=lambda x: x["date"]
    )


# ============================================================
# TWSE 官方 parser
# ============================================================

def parse_twse_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    if not isinstance(
        payload,
        dict,
    ):
        return []

    stat = clean_text(
        payload.get("stat")
    )

    if stat and stat != "OK":

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

        date_value = parse_iso_date(
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
            high is None
            or low is None
            or close is None
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

    return result


# ============================================================
# TWSE official monthly fetch
# ============================================================

def fetch_twse_month(
    code: str,
    year: int,
    month: int,
) -> List[Dict[str, Any]]:

    date_param = (
        f"{year:04d}"
        f"{month:02d}"
        "01"
    )

    params = {
        "response": "json",
        "date": date_param,
        "stockNo": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TWSE_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            rows = parse_twse_payload(
                payload
            )

            return rows

        except Exception as exc:

            if attempt == MAX_RETRIES:

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
# TWSE official history
# ============================================================

def fetch_twse_history(
    code: str,
) -> List[Dict[str, Any]]:

    section(
        f"TWSE 官方資料：{code}.TW"
    )

    all_rows = {}

    months = month_sequence(
        TWSE_FETCH_MONTHS
    )

    start_date = (
        START_DATE
    )

    for year, month in months:

        rows = fetch_twse_month(
            code,
            year,
            month,
        )

        for row in rows:

            if row["date"] >= start_date:

                all_rows[
                    row["date"]
                ] = row

        if len(all_rows) >= (
            MIN_HISTORY_ROWS
        ):

            # 這裡不能停。
            #
            # 我們需要最新日期的完整資料。
            #
            # 所以繼續抓到當月。
            pass

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda x: x["date"]
    )

    log(
        f"TWSE 官方取得："
        f"{code}.TW "
        f"{len(result)} 筆"
    )

    return result


# ============================================================
# TPEx official parser
# ============================================================

def parse_tpex_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    if not isinstance(
        payload,
        dict,
    ):
        return []

    rows = payload.get(
        "aaData"
    )

    if not isinstance(
        rows,
        list,
    ):
        return []

    result = []

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 7:
            continue

        date_value = parse_tpex_date(
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
            high is None
            or low is None
            or close is None
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

    return result


# ============================================================
# TPEx official month
# ============================================================

def fetch_tpex_month(
    code: str,
    year: int,
    month: int,
) -> List[Dict[str, Any]]:

    roc_year = year - 1911

    date_param = (
        f"{roc_year:03d}/"
        f"{month:02d}"
    )

    params = {
        "l": "zh-tw",
        "d": date_param,
        "stkno": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TPEX_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            rows = parse_tpex_payload(
                payload
            )

            return rows

        except Exception as exc:

            if attempt == MAX_RETRIES:

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
# TPEx official history
# ============================================================

def fetch_tpex_history(
    code: str,
) -> List[Dict[str, Any]]:

    section(
        f"TPEx 官方資料：{code}.TWO"
    )

    all_rows = {}

    months = month_sequence(
        TPEX_FALLBACK_MONTHS
    )

    for year, month in months:

        rows = fetch_tpex_month(
            code,
            year,
            month,
        )

        for row in rows:

            if row["date"] >= START_DATE:

                all_rows[
                    row["date"]
                ] = row

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda x: x["date"]
    )

    if result:

        log(
            f"✓ TPEx official："
            f"{code}.TWO "
            f"{len(result)} 筆"
        )

    else:

        log(
            f"❌ TPEx official："
            f"{code}.TWO "
            "沒有有效資料"
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
        {}
    )

    if not isinstance(
        chart,
        dict,
    ):
        return []

    result = chart.get(
        "result"
    )

    if not isinstance(
        result,
        list,
    ):
        return []

    if not result:
        return []

    first = result[0]

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
        {}
    )

    quotes = indicators.get(
        "quote",
        []
    )

    if not timestamps or not quotes:

        return []

    quote = quotes[0]

    if not isinstance(
        quote,
        dict,
    ):
        return []

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

    rows = {}

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

        rows[
            date_value
        ] = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        rows.values(),
        key=lambda x: x["date"]
    )


# ============================================================
# Yahoo fallback
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

    last_error = ""

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

            rows = parse_yahoo_payload(
                payload
            )

            if rows:

                return rows

            last_error = (
                f"資料不足："
                f"{len(rows)} 筆"
            )

        except Exception as exc:

            last_error = str(exc)

        if attempt < MAX_RETRIES:

            time.sleep(
                RETRY_DELAY * attempt
            )

    log(
        f"      ⚠️ Yahoo fallback "
        f"{symbol}: "
        f"{last_error}"
    )

    return []


# ============================================================
# 官方資料選擇
# ============================================================

def fetch_official_history(
    item: Dict[str, str],
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    code = item["code"]

    market = item["market"]

    # --------------------------------------------------------
    # TPEX
    # --------------------------------------------------------

    if market == "TWO":

        rows = fetch_tpex_history(
            code
        )

        rows = normalize_price_rows(
            rows
        )

        if len(rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            return (
                rows,
                "TPEx official",
            )

        return (
            rows,
            "",
        )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    rows = fetch_twse_history(
        code
    )

    rows = normalize_price_rows(
        rows
    )

    if len(rows) >= (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        return (
            rows,
            "TWSE official",
        )

    return (
        rows,
        "",
    )


# ============================================================
# 單檔股票
# ============================================================

def fetch_one(
    item: Dict[str, str],
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    symbol = item["symbol"]

    code = item["code"]

    market = item["market"]

    name = item["name"]

    log(
        f"→ 官方來源優先："
        f"{symbol} "
        f"{name}"
    )

    # --------------------------------------------------------
    # 1. 官方
    # --------------------------------------------------------

    official_rows, official_source = (
        fetch_official_history(
            item
        )
    )

    official_rows = normalize_price_rows(
        official_rows
    )

    # --------------------------------------------------------
    # 官方 >= 60
    # --------------------------------------------------------

    if len(official_rows) >= (
        MIN_HISTORY_ROWS
    ):

        result = {
            "symbol": symbol,
            "code": code,
            "market": market,
            "name": name,
            "source": official_source,
            "history_rows": len(
                official_rows
            ),
            "history_status": "complete",
            "latest_date": (
                official_rows[-1]["date"]
            ),
            "prices": official_rows,
        }

        log(
            f"✓ {symbol} "
            f"→ {len(official_rows)} 筆 "
            f"→ {official_source}"
        )

        return (
            result,
            "",
        )

    # --------------------------------------------------------
    # 官方 20~59
    # --------------------------------------------------------

    if len(official_rows) >= (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        result = {
            "symbol": symbol,
            "code": code,
            "market": market,
            "name": name,
            "source": (
                official_source
                + " (short history)"
            ),
            "history_rows": len(
                official_rows
            ),
            "history_status": "short_history",
            "latest_date": (
                official_rows[-1]["date"]
            ),
            "prices": official_rows,
            "fallback_reason": (
                "official_history_short"
            ),
        }

        # ----------------------------------------------------
        # 即使官方有 20~59 筆，
        # 仍然嘗試 Yahoo 補充。
        #
        # 但 Yahoo 只能補資料，
        # 不取代官方資料。
        # ----------------------------------------------------

        yahoo_rows = fetch_yahoo(
            symbol
        )

        yahoo_rows = normalize_price_rows(
            yahoo_rows
        )

        if len(yahoo_rows) > len(
            official_rows
        ):

            merged = {}

            for row in official_rows:
                merged[
                    row["date"]
                ] = row

            for row in yahoo_rows:

                if row["date"] not in merged:

                    merged[
                        row["date"]
                    ] = row

            merged_rows = sorted(
                merged.values(),
                key=lambda x: x["date"]
            )

            if len(merged_rows) >= (
                MIN_HISTORY_ROWS
            ):

                result[
                    "prices"
                ] = merged_rows

                result[
                    "history_rows"
                ] = len(
                    merged_rows
                )

                result[
                    "history_status"
                ] = "complete"

                result[
                    "latest_date"
                ] = merged_rows[-1][
                    "date"
                ]

                result[
                    "source"
                ] = (
                    official_source
                    + " + Yahoo supplement"
                )

                log(
                    f"✓ {symbol} "
                    f"→ {len(merged_rows)} 筆 "
                    f"→ {official_source} "
                    "＋ Yahoo supplement"
                )

        return (
            result,
            "",
        )

    # --------------------------------------------------------
    # 官方 < 20
    # --------------------------------------------------------

    log(
        f"⚠️ {symbol} "
        f"官方資料只有 "
        f"{len(official_rows)} 筆"
    )

    # --------------------------------------------------------
    # 最後才 Yahoo
    # --------------------------------------------------------

    log(
        f"→ {symbol} "
        "啟動 Yahoo 最後備援"
    )

    yahoo_rows = fetch_yahoo(
        symbol
    )

    yahoo_rows = normalize_price_rows(
        yahoo_rows
    )

    if len(yahoo_rows) >= (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        status = (
            "complete"
            if len(yahoo_rows)
            >= MIN_HISTORY_ROWS
            else "short_history"
        )

        result = {
            "symbol": symbol,
            "code": code,
            "market": market,
            "name": name,
            "source": "Yahoo fallback",
            "history_rows": len(
                yahoo_rows
            ),
            "history_status": status,
            "latest_date": (
                yahoo_rows[-1]["date"]
            ),
            "prices": yahoo_rows,
            "fallback_reason": (
                "official_history_insufficient"
            ),
        }

        log(
            f"✓ {symbol} "
            f"→ {len(yahoo_rows)} 筆 "
            "→ Yahoo fallback"
        )

        return (
            result,
            "",
        )

    # --------------------------------------------------------
    # 完全失敗
    # --------------------------------------------------------

    reason = (
        f"official={len(official_rows)} "
        f"yahoo={len(yahoo_rows)}"
    )

    log(
        f"❌ {symbol} "
        f"{name} "
        f"→ {reason}"
    )

    return (
        None,
        reason,
    )


# ============================================================
# Build shards
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any]
    ],
) -> List[
    Tuple[
        str,
        Dict[str, Any]
    ]
]:

    symbols = sorted(
        results.keys()
    )

    output = []

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

            stocks[
                symbol
            ] = results[
                symbol
            ]["prices"]

        index = (
            start // STOCKS_PER_FILE
        ) + 1

        filename = (
            f"prices_{index:03d}.json"
        )

        output.append(
            (
                filename,
                {
                    "stocks": stocks
                }
            )
        )

    return output


# ============================================================
# Validate shard
# ============================================================

def validate_shard(
    path: Path,
    expected_symbols: List[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            f"找不到 shard："
            f"{path}"
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
            f"{path.name} "
            "根節點錯誤"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} "
            "缺少 stocks"
        )

    actual = set(
        stocks.keys()
    )

    expected = set(
        expected_symbols
    )

    if actual != expected:

        missing = sorted(
            expected - actual
        )

        extra = sorted(
            actual - expected
        )

        raise RuntimeError(
            f"{path.name} "
            f"missing={missing[:20]} "
            f"extra={extra[:20]}"
        )

    for symbol, rows in stocks.items():

        if not isinstance(
            rows,
            list,
        ):

            raise RuntimeError(
                f"{symbol} "
                "prices 必須是 list"
            )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{symbol} "
                f"歷史資料不足："
                f"{len(rows)}"
            )

        previous_date = ""

        for row in rows:

            required = {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} "
                    "存在非 object row"
                )

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

            date_value = str(
                row["date"]
            )

            if (
                previous_date
                and date_value
                < previous_date
            ):

                raise RuntimeError(
                    f"{symbol} "
                    "日期未排序"
                )

            previous_date = date_value


# ============================================================
# Manifest
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> Dict[str, Any]:

    complete = 0

    short_history = 0

    official_count = 0

    yahoo_count = 0

    source_counts = {}

    latest_dates = []

    for result in results.values():

        status = result.get(
            "history_status"
        )

        if status == "complete":

            complete += 1

        elif status == "short_history":

            short_history += 1

        source = result.get(
            "source",
            ""
        )

        source_counts[
            source
        ] = (
            source_counts.get(
                source,
                0
            )
            + 1
        )

        if (
            "official"
            in source.lower()
        ):

            official_count += 1

        if (
            "yahoo"
            in source.lower()
        ):

            yahoo_count += 1

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
        "generated_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "universe_stock_count": (
            universe_count
        ),
        "price_stock_count": len(
            results
        ),
        "complete_history_count": (
            complete
        ),
        "short_history_count": (
            short_history
        ),
        "failed_count": (
            universe_count
            - len(results)
        ),
        "official_source_count": (
            official_count
        ),
        "yahoo_fallback_count": (
            yahoo_count
        ),
        "min_history_rows": (
            MIN_HISTORY_ROWS
        ),
        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),
        "sources": source_counts,
        "latest_date": (
            max(latest_dates)
            if latest_dates
            else None
        ),
        "files": shard_files,
    }


# ============================================================
# Manifest validation
# ============================================================

def validate_manifest(
    path: Path,
    expected_symbols: List[str],
    expected_shards: List[str],
) -> None:

    manifest = load_json(
        path
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest 根節點錯誤"
        )

    files = manifest.get(
        "files"
    )

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 不一致"
        )

    if manifest.get(
        "universe_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest "
            "universe_stock_count 錯誤"
        )

    if manifest.get(
        "price_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest "
            "price_stock_count 錯誤："
            f"{manifest.get('price_stock_count')} "
            f"!="
            f"{len(expected_symbols)}"
        )


# ============================================================
# Write temporary directory
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> None:

    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    shard_data = build_shards(
        results
    )

    shard_files = []

    for filename, payload in shard_data:

        path = (
            temp_dir
            / filename
        )

        save_json(
            path,
            payload
        )

        expected = sorted(
            payload[
                "stocks"
            ].keys()
        )

        validate_shard(
            path,
            expected
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
        manifest
    )

    all_symbols = sorted(
        results.keys()
    )

    validate_manifest(
        manifest_path,
        all_symbols,
        shard_files,
    )

    log(
        f"✓ shard 驗證完成："
        f"{len(shard_files)} 個"
    )

    log(
        "✓ manifest 驗證完成"
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

    start_time = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    universe = load_universe()

    universe_count = len(
        universe
    )

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

    section(
        f"開始官方資料抓取："
        f"{universe_count} 檔 STOCK"
    )

    results = {}

    failures = {}

    source_counts = {}

    for index, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item[
            "symbol"
        ]

        log(
            ""
        )

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

                failures[
                    symbol
                ] = reason

            else:

                results[
                    symbol
                ] = result

                source = result.get(
                    "source",
                    ""
                )

                source_counts[
                    source
                ] = (
                    source_counts.get(
                        source,
                        0
                    )
                    + 1
                )

        except Exception as exc:

            failures[
                symbol
            ] = str(exc)

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

    official_count = sum(
        count
        for source, count
        in source_counts.items()
        if "official"
        in source.lower()
    )

    yahoo_count = sum(
        count
        for source, count
        in source_counts.items()
        if "yahoo"
        in source.lower()
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

    log(
        f"官方來源："
        f"{official_count}"
    )

    log(
        f"Yahoo 最後備援："
        f"{yahoo_count}"
    )

    log("")

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # --------------------------------------------------------
    # Failure gate
    # --------------------------------------------------------

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

        log(
            ""
        )

        log(
            "❌ 成功率低於安全門檻："
            f"{MIN_SUCCESS_RATE:.0%}"
        )

        for symbol, reason in list(
            failures.items()
        )[:50]:

            log(
                f"  {symbol}: "
                f"{reason}"
            )

        return 1

    # --------------------------------------------------------
    # Missing
    # --------------------------------------------------------

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
        )[:50]:

            log(
                f"  {symbol}: "
                f"{failures.get(symbol, '')}"
            )

    # --------------------------------------------------------
    # 7794 validation
    # --------------------------------------------------------

    if "7794.TWO" in expected_symbols:

        log("")
        log(
            "=" * 72
        )
        log(
            "7794.TWO 官方來源驗證"
        )
        log(
            "=" * 72
        )

        if "7794.TWO" in results:

            target = results[
                "7794.TWO"
            ]

            log(
                f"資料來源："
                f"{target['source']}"
            )

            log(
                f"資料筆數："
                f"{target['history_rows']}"
            )

            log(
                f"最新日期："
                f"{target['latest_date']}"
            )

            log(
                f"歷史狀態："
                f"{target['history_status']}"
            )

            if (
                "TPEx official"
                in target["source"]
            ):

                log(
                    "✓ 7794.TWO "
                    "確實由 TPEx 官方資料鏈取得"
                )

            else:

                log(
                    "⚠️ 7794.TWO "
                    "沒有使用 TPEx official "
                    "作為最終來源"
                )

        else:

            log(
                "❌ 7794.TWO "
                "仍然沒有價格資料"
            )

    # --------------------------------------------------------
    # Temporary directory
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
        # 最終 Universe / Price Gate
        # ----------------------------------------------------

        final_manifest = load_json(
            temp_dir
            / "manifest.json"
        )

        if final_manifest.get(
            "universe_stock_count"
        ) != universe_count:

            raise RuntimeError(
                "manifest "
                "universe_stock_count 錯誤"
            )

        if final_manifest.get(
            "price_stock_count"
        ) != success_count:

            raise RuntimeError(
                "manifest "
                "price_stock_count 錯誤"
            )

        # ----------------------------------------------------
        # 這裡要求完整 Universe。
        #
        # 不再接受：
        # 1943 / 1944
        #
        # 因為既然 Gate 已經完成，
        # 最終 prices 就必須與 Universe 完全一致。
        # ----------------------------------------------------

        if success_count != universe_count:

            raise RuntimeError(
                "Price Universe 不完整："
                f"{success_count}/"
                f"{universe_count}"
            )

        # ----------------------------------------------------
        # Atomic replace
        # ----------------------------------------------------

        section(
            "替換正式 Data/prices"
        )

        replace_output(
            temp_dir
        )

        log(
            "✓ Data/prices/ "
            "已成功更新"
        )

    except Exception as exc:

        log("")
        log(
            "❌ 價格資料建置失敗："
            f"{exc}"
        )

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
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
        - start_time
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
        f"官方來源："
        f"{official_count}"
    )

    log(
        f"Yahoo fallback："
        f"{yahoo_count}"
    )

    if (
        success_count
        == universe_count
    ):

        log(
            "✓ Universe → Price "
            "100% 對齊"
        )

    else:

        log(
            "❌ Universe → Price "
            "未完全對齊"
        )

    if "7794.TWO" in expected_symbols:

        if "7794.TWO" in results:

            log(
                "✓ 7794.TWO："
                "已進入價格資料鏈"
            )

            log(
                "✓ 7794.TWO source："
                f"{results['7794.TWO']['source']}"
            )

        else:

            log(
                "❌ 7794.TWO："
                "缺少價格資料"
            )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ fetch_prices.py V5.0 完成"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
