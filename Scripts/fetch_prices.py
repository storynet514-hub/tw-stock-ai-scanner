#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式批次版 V8.0
============================================================

核心架構
------------------------------------------------------------
Data/universe.json
        ↓
1944 檔 STOCK
        ↓
TWSE / TPEx 官方「全市場批次日行情」
        ↓
本地依股票代號分流
        ↓
Data/prices/
        ↓
analysis.json
        ↓
ui_data.json
        ↓
index.html

V8.0 核心原則
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. 只接受 status == active（若 Universe 有 status）
3. STOCK / ETF 完全分流
4. ETF 不進價格抓取
5. 不修改 Universe
6. 不使用成交行情建立 Universe
7. 不使用 CMoney
8. TWSE / TPEx 官方資料優先
9. 不再使用「每檔股票 × 每月份」的抓法
10. 官方全市場批次資料一次抓取後，本地分流
11. 已存在歷史資料時採增量更新
12. 20 筆為絕對最低歷史資料
13. 60 筆以上為 complete
14. Yahoo 僅針對個別缺失股票作最後 fallback
15. 所有 1944 檔必須成功才能正式替換 Data/prices
16. temporary directory
17. shard 驗證
18. manifest 驗證
19. atomic replace
20. 不允許半套資料進入正式 Data/prices

============================================================
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import sys
import tempfile
import time

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V8.0"
SCHEMA_VERSION = "prices-v8.0"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_DIR = DATA_DIR / "prices"


# ============================================================
# HISTORY
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
# SAFETY
# ============================================================

# V8.0 正式輸出要求全 Universe 成功。
MIN_SUCCESS_RATE = 1.00

MAX_RETRIES = 3
REQUEST_TIMEOUT = 30

BATCH_DELAY = 0.20
RETRY_DELAY = 2.0


# ============================================================
# OFFICIAL ENDPOINTS
# ============================================================

# TWSE 全市場日成交資訊
TWSE_DAILY_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY_ALL"
)

TWSE_DAILY_RWD_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/STOCK_DAY_ALL"
)


# TPEx 全市場上櫃股票行情
TPEX_DAILY_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_close_quotes/"
    "stk_quote_result.php"
)


# ============================================================
# YAHOO LAST FALLBACK
# ============================================================

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


# ============================================================
# HTTP
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
            "text/csv,"
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
# LOG
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
# TEXT
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
# NUMBER
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
        .replace("元", "")
        .replace("％", "")
        .replace("%", "")
    )

    if text in {
        "",
        "-",
        "－",
        "—",
        "None",
        "null",
    }:
        return None

    try:

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
# DATE
# ============================================================

def parse_date(
    value: Any,
) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

    # --------------------------------------------------------
    # YYYY-MM-DD
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # ROC date
    # --------------------------------------------------------

    parts = re.split(
        r"[/\-]",
        text,
    )

    if len(parts) == 3:

        try:

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])

            if year < 1911:
                year += 1911

            dt = date(
                year,
                month,
                day,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            return None

    return None


def parse_date_from_header(
    value: Any,
) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

    # 2026/08/28
    match = re.search(
        r"20\d{2}[/-]\d{1,2}[/-]\d{1,2}",
        text,
    )

    if match:

        return parse_date(
            match.group(0)
        )

    # 115/08/28
    match = re.search(
        r"\b\d{2,3}[/-]\d{1,2}[/-]\d{1,2}\b",
        text,
    )

    if match:

        return parse_date(
            match.group(0)
        )

    return None


# ============================================================
# CODE
# ============================================================

def extract_code(
    value: Any,
) -> Optional[str]:
    """
    合法 Universe 商品代號：

        4~6 碼
        第一碼必須為數字
        後續允許英數字

    例如：

        2330
        3081
        7794
        0050
        00400A
        00980A
    """

    text = clean_text(value).upper()

    if not text:
        return None

    if text.endswith(".TWO"):
        text = text[:-4]

    elif text.endswith(".TW"):
        text = text[:-3]

    if not 4 <= len(text) <= 6:
        return None

    if not text[0].isdigit():
        return None

    if not all(
        char.isalnum()
        for char in text
    ):
        return None

    return text


# ============================================================
# TYPE
# ============================================================

def normalize_type(
    value: Any,
) -> Optional[str]:

    text = clean_text(value).upper()

    if text == "STOCK":
        return "STOCK"

    if text == "ETF":
        return "ETF"

    return None


# ============================================================
# MARKET
# ============================================================

def normalize_market(
    value: Any,
) -> Optional[str]:

    text = clean_text(value).upper()

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
# NAME
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
# SYMBOL
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

        code = extract_code(value)

        if code:

            if value.endswith(".TWO"):
                return code + ".TWO"

            if value.endswith(".TW"):
                return code + ".TW"

            # 沒有 suffix 的 symbol，
            # 先只回傳 code，market 後面決定。
            return code

    if fallback_key:

        value = clean_text(
            fallback_key
        ).upper()

        code = extract_code(value)

        if code:

            if value.endswith(".TWO"):
                return code + ".TWO"

            if value.endswith(".TW"):
                return code + ".TW"

            return code

    return None


# ============================================================
# ITEM CODE
# ============================================================

def extract_item_code(
    item: Dict[str, Any],
    symbol: Optional[str],
    fallback_key: Optional[str] = None,
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

        code = extract_code(symbol)

        if code:
            return code

    if fallback_key:

        return extract_code(
            fallback_key
        )

    return None


# ============================================================
# STATUS
# ============================================================

def is_active_item(
    item: Dict[str, Any],
) -> bool:

    if "status" not in item:
        return True

    status = clean_text(
        item.get("status")
    ).lower()

    return status in {
        "active",
        "enabled",
        "listed",
        "verify",
        "verified",
    }


# ============================================================
# NORMALIZE RECORD
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

    if not is_active_item(item):
        return None

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
        fallback_key,
    )

    if code is None:
        return None

    market = normalize_market(
        item.get("market")
    )

    if market is None and symbol:

        symbol_text = clean_text(
            symbol
        ).upper()

        if symbol_text.endswith(".TWO"):
            market = "TWO"

        elif symbol_text.endswith(".TW"):
            market = "TW"

    if market is None:
        return None

    suffix = (
        ".TWO"
        if market == "TWO"
        else ".TW"
    )

    return {
        "symbol": code + suffix,
        "code": code,
        "market": market,
        "type": record_type,
        "name": extract_name(item),
    }


# ============================================================
# CONTAINER
# ============================================================

def extract_container(
    universe: Dict[str, Any],
    key: str,
) -> List[
    Tuple[Optional[str], Any]
]:

    value = universe.get(key)

    result = []

    if isinstance(
        value,
        list,
    ):

        for item in value:

            result.append(
                (
                    None,
                    item,
                )
            )

        return result

    if isinstance(
        value,
        dict,
    ):

        for symbol, item in value.items():

            result.append(
                (
                    str(symbol),
                    item,
                )
            )

    return result


# ============================================================
# LOAD UNIVERSE
# ============================================================

def load_universe() -> List[
    Dict[str, str]
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
            "universe.json 根節點必須是 object"
        )

    declared_stock_count = universe.get(
        "stock_count"
    )

    declared_etf_count = universe.get(
        "etf_count"
    )

    if not isinstance(
        declared_stock_count,
        int,
    ):

        raise RuntimeError(
            "Universe stock_count 不存在或不是整數"
        )

    if not isinstance(
        declared_etf_count,
        int,
    ):

        raise RuntimeError(
            "Universe etf_count 不存在或不是整數"
        )

    raw_items = extract_container(
        universe,
        "stocks",
    )

    if not raw_items:

        raise RuntimeError(
            "Universe stocks 為空"
        )

    parsed_stocks: Dict[
        str,
        Dict[str, str]
    ] = {}

    parsed_etfs: Dict[
        str,
        Dict[str, str]
    ] = {}

    unparsed: List[str] = []

    for fallback_key, item in raw_items:

        normalized = normalize_record(
            item,
            fallback_key,
        )

        if normalized is None:

            unparsed.append(
                fallback_key
                or "<unknown>"
            )

            continue

        symbol = normalized["symbol"]

        if normalized["type"] == "STOCK":

            parsed_stocks.setdefault(
                symbol,
                normalized,
            )

        elif normalized["type"] == "ETF":

            parsed_etfs.setdefault(
                symbol,
                normalized,
            )

    actual_stock_count = len(
        parsed_stocks
    )

    actual_etf_count = len(
        parsed_etfs
    )

    log(
        f"Universe metadata stock_count："
        f"{declared_stock_count}"
    )

    log(
        f"Universe metadata etf_count："
        f"{declared_etf_count}"
    )

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
        f"{len(unparsed)}"
    )

    if actual_stock_count != declared_stock_count:

        if unparsed:

            log(
                "前 50 個無法解析項目："
            )

            for value in unparsed[:50]:
                log(
                    f"  {value}"
                )

        raise RuntimeError(
            "Universe STOCK 數量不一致"
        )

    if actual_etf_count != declared_etf_count:

        if unparsed:

            log(
                "前 50 個無法解析項目："
            )

            for value in unparsed[:50]:
                log(
                    f"  {value}"
                )

        raise RuntimeError(
            "Universe ETF 數量不一致"
        )

    if unparsed:

        log(
            "前 50 個無法解析項目："
        )

        for value in unparsed[:50]:
            log(
                f"  {value}"
            )

        raise RuntimeError(
            "Universe 存在未解析商品"
        )

    target = parsed_stocks.get(
        "7794.TWO"
    )

    if target:

        log("")
        log("✓ 7794 Universe：")
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
        parsed_stocks.values()
    )


# ============================================================
# DATE UTILITIES
# ============================================================

def parse_iso_date(
    value: str,
) -> date:

    return datetime.strptime(
        value,
        "%Y-%m-%d",
    ).date()


def iso_date(
    value: date,
) -> str:

    return value.strftime(
        "%Y-%m-%d"
    )


def today_taiwan() -> date:

    # UTC+8，不依賴 pytz
    return (
        datetime.now(
            timezone.utc
        )
        + timedelta(hours=8)
    ).date()


def daterange_days(
    start: date,
    end: date,
) -> Iterable[date]:

    current = start

    while current <= end:

        yield current

        current += timedelta(
            days=1
        )


def trading_date_candidates(
    start: date,
    end: date,
) -> List[date]:

    """
    批次歷史建立時使用日曆日。

    非交易日官方 API 會回空資料，因此不需要
    先自行建立台灣交易日曆。

    這裡只排除週六、週日，
    國定假日仍由官方 API 判定。
    """

    result = []

    for current in daterange_days(
        start,
        end,
    ):

        if current.weekday() >= 5:
            continue

        result.append(current)

    return result


# ============================================================
# HTTP JSON
# ============================================================

def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"HTTP JSON failed: "
        f"{url} "
        f"{last_error}"
    )


# ============================================================
# TWSE BATCH
# ============================================================

def normalize_twse_row(
    row: Any,
    trade_date: str,
) -> Optional[
    Tuple[str, Dict[str, Any]]
]:

    if not isinstance(
        row,
        list,
    ):
        return None

    if len(row) < 8:
        return None

    code = extract_code(
        row[0]
    )

    if code is None:
        return None

    open_value = safe_float(
        row[4]
    )

    high = safe_float(
        row[5]
    )

    low = safe_float(
        row[6]
    )

    close = safe_float(
        row[7]
    )

    volume = safe_int(
        row[2]
    )

    if (
        close is None
        or high is None
        or low is None
    ):
        return None

    if close <= 0:
        return None

    if open_value is None:
        open_value = close

    return (
        code,
        {
            "date": trade_date,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
    )


def fetch_twse_batch(
    query_date: date,
) -> Dict[
    str,
    Dict[str, Any]
]:

    """
    一次取得 TWSE 全市場資料。

    優先使用 STOCK_DAY_ALL JSON/RWD。

    回傳：
        code -> OHLCV
    """

    date_text = query_date.strftime(
        "%Y%m%d"
    )

    params = {
        "response": "json",
        "date": date_text,
    }

    payload = None

    # --------------------------------------------------------
    # 官方主端點
    # --------------------------------------------------------

    try:

        payload = get_json(
            TWSE_DAILY_URL,
            params,
        )

    except Exception:

        # ----------------------------------------------------
        # RWD 官方端點
        # ----------------------------------------------------

        payload = get_json(
            TWSE_DAILY_RWD_URL,
            params,
        )

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    data = payload.get(
        "data",
        []
    )

    if not isinstance(
        data,
        list,
    ):
        return {}

    actual_date = (
        parse_date(
            payload.get("date")
        )
        or query_date.strftime(
            "%Y-%m-%d"
        )
    )

    result = {}

    for row in data:

        normalized = normalize_twse_row(
            row,
            actual_date,
        )

        if normalized is None:
            continue

        code, record = normalized

        result[code] = record

    return result


# ============================================================
# TPEX BATCH
# ============================================================

def normalize_tpex_row(
    row: Any,
    trade_date: str,
) -> Optional[
    Tuple[str, Dict[str, Any]]
]:

    if not isinstance(
        row,
        list,
    ):
        return None

    if len(row) < 9:
        return None

    code = extract_code(
        row[0]
    )

    if code is None:
        return None

    close = safe_float(
        row[2]
    )

    open_value = safe_float(
        row[4]
    )

    high = safe_float(
        row[5]
    )

    low = safe_float(
        row[6]
    )

    volume = safe_int(
        row[8]
    )

    if (
        close is None
        or high is None
        or low is None
    ):
        return None

    if close <= 0:
        return None

    if open_value is None:
        open_value = close

    return (
        code,
        {
            "date": trade_date,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
    )


def fetch_tpex_batch(
    query_date: date,
) -> Dict[
    str,
    Dict[str, Any]
]:

    """
    一次取得 TPEx 全市場上櫃股票行情。

    TPEx 官方 endpoint：
        daily_close_quotes/stk_quote_result.php

    JSON 欄位 aaData。
    """

    roc_year = (
        query_date.year
        - 1911
    )

    date_text = (
        f"{roc_year:03d}/"
        f"{query_date.month:02d}/"
        f"{query_date.day:02d}"
    )

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": date_text,
        "s": "0,asc,0",
    }

    payload = get_json(
        TPEX_DAILY_URL,
        params,
    )

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    data = payload.get(
        "aaData",
        []
    )

    if not isinstance(
        data,
        list,
    ):
        return {}

    result = {}

    actual_date = (
        query_date.strftime(
            "%Y-%m-%d"
        )
    )

    for row in data:

        normalized = normalize_tpex_row(
            row,
            actual_date,
        )

        if normalized is None:
            continue

        code, record = normalized

        result[code] = record

    return result


# ============================================================
# BATCH MARKET FETCH
# ============================================================

def fetch_batch_date(
    query_date: date,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Dict[str, Any]],
]:

    """
    同一交易日：

        TWSE 全市場一次
        TPEx 全市場一次
    """

    twse = {}
    tpex = {}

    try:

        twse = fetch_twse_batch(
            query_date
        )

    except Exception as exc:

        log(
            f"      ⚠️ TWSE "
            f"{iso_date(query_date)} "
            f"失敗：{exc}"
        )

    time.sleep(
        BATCH_DELAY
    )

    try:

        tpex = fetch_tpex_batch(
            query_date
        )

    except Exception as exc:

        log(
            f"      ⚠️ TPEx "
            f"{iso_date(query_date)} "
            f"失敗：{exc}"
        )

    return twse, tpex


# ============================================================
# EXISTING DATA
# ============================================================

def load_existing_prices(
    universe_symbols: Set[str],
) -> Dict[
    str,
    List[Dict[str, Any]]
]:

    """
    從既有 Data/prices/*.json 載入歷史。

    V8.0 不需要重新下載已經存在的歷史。
    """

    section(
        "檢查既有 Data/prices"
    )

    if not OUTPUT_DIR.exists():

        log(
            "目前沒有既有 Data/prices，"
            "執行完整歷史初始化。"
        )

        return {}

    files = sorted(
        OUTPUT_DIR.glob(
            "prices_*.json"
        )
    )

    if not files:

        log(
            "找不到既有 shard，"
            "執行完整歷史初始化。"
        )

        return {}

    result = {}

    for path in files:

        try:

            data = load_json(
                path
            )

            stocks = data.get(
                "stocks",
                {}
            )

            if not isinstance(
                stocks,
                dict,
            ):
                continue

            for symbol, rows in stocks.items():

                if symbol not in universe_symbols:
                    continue

                if not isinstance(
                    rows,
                    list,
                ):
                    continue

                result[symbol] = rows

        except Exception as exc:

            log(
                f"⚠️ 無法讀取 "
                f"{path.name}: "
                f"{exc}"
            )

    log(
        f"既有股票歷史："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# NORMALIZE EXISTING
# ============================================================

def normalize_existing_rows(
    rows: List[Any],
) -> List[Dict[str, Any]]:

    result = {}

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        trade_date = parse_date(
            row.get("date")
        )

        if not trade_date:
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

        result[trade_date] = {
            "date": trade_date,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return [
        result[key]
        for key in sorted(
            result.keys()
        )
    ]


# ============================================================
# MERGE
# ============================================================

def merge_rows(
    existing: List[Dict[str, Any]],
    incoming: Iterable[
        Dict[str, Any]
    ],
) -> List[Dict[str, Any]]:

    result = {}

    for row in existing:

        trade_date = parse_date(
            row.get("date")
        )

        if not trade_date:
            continue

        result[trade_date] = row

    for row in incoming:

        trade_date = parse_date(
            row.get("date")
        )

        if not trade_date:
            continue

        result[trade_date] = row

    return [
        result[key]
        for key in sorted(
            result.keys()
        )
    ]


# ============================================================
# LAST DATE
# ============================================================

def latest_row_date(
    rows: List[Dict[str, Any]],
) -> Optional[date]:

    if not rows:
        return None

    values = []

    for row in rows:

        parsed = parse_date(
            row.get("date")
        )

        if parsed:
            values.append(
                parse_iso_date(
                    parsed
                )
            )

    if not values:
        return None

    return max(values)


# ============================================================
# YAHOO
# ============================================================

def date_to_timestamp(
    date_value: date,
) -> int:

    dt = datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        tzinfo=timezone.utc,
    )

    return int(
        dt.timestamp()
    )


def parse_yahoo(
    payload: Dict[str, Any],
) -> List[
    Dict[str, Any]
]:

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
    ) or not result:

        return []

    first = result[0]

    timestamps = first.get(
        "timestamp"
    )

    indicators = first.get(
        "indicators",
        {}
    )

    quote_list = indicators.get(
        "quote",
        []
    )

    if not timestamps or not quote_list:
        return []

    quote = quote_list[0]

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

            trade_date = dt.strftime(
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

        rows[trade_date] = {
            "date": trade_date,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return [
        rows[key]
        for key in sorted(
            rows.keys()
        )
    ]


def fetch_yahoo(
    symbol: str,
    start_date: date,
) -> List[
    Dict[str, Any]
]:

    period1 = date_to_timestamp(
        start_date
    )

    period2 = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    params = {
        "period1": period1,
        "period2": period2,
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

            rows = parse_yahoo(
                response.json()
            )

            if rows:
                return rows

        except Exception:

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    return []


# ============================================================
# BUILD BATCH HISTORY
# ============================================================

def build_batch_history(
    universe: List[Dict[str, str]],
    existing: Dict[
        str,
        List[Dict[str, Any]]
    ],
) -> Tuple[
    Dict[str, List[Dict[str, Any]]],
    Dict[str, str],
]:

    section(
        "V8.0 全市場批次價格抓取"
    )

    universe_by_code = {
        item["code"]: item
        for item in universe
    }

    symbols = {
        item["symbol"]
        for item in universe
    }

    history: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    for symbol, rows in existing.items():

        history[symbol] = (
            normalize_existing_rows(
                rows
            )
        )

    # --------------------------------------------------------
    # 決定初始化 / 增量
    # --------------------------------------------------------

    today = today_taiwan()

    global_latest = None

    for rows in history.values():

        latest = latest_row_date(
            rows
        )

        if latest:

            if (
                global_latest is None
                or latest > global_latest
            ):
                global_latest = latest

    if global_latest:

        fetch_start = (
            global_latest
            + timedelta(days=1)
        )

        log(
            f"既有資料最新日期："
            f"{iso_date(global_latest)}"
        )

        log(
            f"V8.0 增量開始："
            f"{iso_date(fetch_start)}"
        )

    else:

        fetch_start = parse_iso_date(
            START_DATE
        )

        log(
            "沒有可用歷史資料，"
            "執行完整批次初始化："
            f"{START_DATE}"
        )

    # --------------------------------------------------------
    # 沒有需要更新
    # --------------------------------------------------------

    if fetch_start > today:

        log(
            "✓ 現有價格資料已經是最新"
        )

        return history, {}

    candidates = trading_date_candidates(
        fetch_start,
        today,
    )

    log(
        f"預計批次日期："
        f"{len(candidates)} 天"
    )

    failures: Dict[
        str,
        str
    ] = {}

    # --------------------------------------------------------
    # 每個交易日只呼叫 TWSE + TPEx 各一次
    # --------------------------------------------------------

    for index, query_date in enumerate(
        candidates,
        start=1,
    ):

        date_text = iso_date(
            query_date
        )

        log(
            f"[BATCH {index}/"
            f"{len(candidates)}] "
            f"{date_text}"
        )

        twse_rows, tpex_rows = (
            fetch_batch_date(
                query_date
            )
        )

        if not twse_rows and not tpex_rows:

            # 非交易日/官方暫無資料。
            # 不直接視為股票失敗。
            log(
                "      ↳ 無市場資料，"
                "略過"
            )

            continue

        log(
            f"      TWSE："
            f"{len(twse_rows)}"
        )

        log(
            f"      TPEx："
            f"{len(tpex_rows)}"
        )

        # ----------------------------------------------------
        # TWSE 本地分流
        # ----------------------------------------------------

        for code, record in twse_rows.items():

            item = universe_by_code.get(
                code
            )

            if item is None:
                continue

            if item["market"] != "TW":
                continue

            symbol = item["symbol"]

            history.setdefault(
                symbol,
                [],
            )

            history[symbol] = merge_rows(
                history[symbol],
                [record],
            )

        # ----------------------------------------------------
        # TPEx 本地分流
        # ----------------------------------------------------

        for code, record in tpex_rows.items():

            item = universe_by_code.get(
                code
            )

            if item is None:
                continue

            if item["market"] != "TWO":
                continue

            symbol = item["symbol"]

            history.setdefault(
                symbol,
                [],
            )

            history[symbol] = merge_rows(
                history[symbol],
                [record],
            )

        time.sleep(
            BATCH_DELAY
        )

    # --------------------------------------------------------
    # 初步驗證
    # --------------------------------------------------------

    for item in universe:

        symbol = item["symbol"]

        rows = history.get(
            symbol,
            []
        )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            failures[symbol] = (
                "官方批次歷史不足："
                f"{len(rows)} 筆"
            )

    return history, failures


# ============================================================
# INDIVIDUAL FALLBACK
# ============================================================

def apply_individual_fallback(
    universe: List[Dict[str, str]],
    history: Dict[
        str,
        List[Dict[str, Any]]
    ],
    failures: Dict[str, str],
) -> Dict[str, str]:

    """
    只有官方批次資料無法滿足最低 20 筆時，
    才對「缺失股票」啟動 Yahoo。

    正常 1944 全成功時：
        不會進入這個流程。
    """

    if not failures:
        return failures

    section(
        "個別缺失股票最後 fallback"
    )

    remaining = {}

    by_symbol = {
        item["symbol"]: item
        for item in universe
    }

    for index, symbol in enumerate(
        sorted(failures.keys()),
        start=1,
    ):

        item = by_symbol.get(
            symbol
        )

        if item is None:
            continue

        log(
            f"[FALLBACK {index}/"
            f"{len(failures)}] "
            f"{symbol}"
        )

        current_rows = history.get(
            symbol,
            [],
        )

        current_latest = (
            latest_row_date(
                current_rows
            )
        )

        if current_latest:

            yahoo_start = (
                current_latest
                + timedelta(days=1)
            )

            yahoo_rows = fetch_yahoo(
                item["symbol"],
                yahoo_start,
            )

            merged = merge_rows(
                current_rows,
                yahoo_rows,
            )

        else:

            yahoo_rows = fetch_yahoo(
                item["symbol"],
                parse_iso_date(
                    START_DATE
                ),
            )

            merged = merge_rows(
                current_rows,
                yahoo_rows,
            )

        history[symbol] = merged

        if len(merged) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            log(
                f"      ✓ Yahoo fallback："
                f"{len(merged)} 筆"
            )

            del failures[symbol]

        else:

            remaining[symbol] = (
                "官方批次不足 + "
                "Yahoo fallback 不足："
                f"{len(merged)} 筆"
            )

        time.sleep(
            BATCH_DELAY
        )

    return remaining


# ============================================================
# BUILD RESULTS
# ============================================================

def build_results(
    universe: List[Dict[str, str]],
    history: Dict[
        str,
        List[Dict[str, Any]]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, str],
]:

    results = {}
    failures = {}

    for item in universe:

        symbol = item["symbol"]

        rows = normalize_existing_rows(
            history.get(
                symbol,
                [],
            )
        )

        history[symbol] = rows

        count = len(rows)

        if count < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            failures[symbol] = (
                f"歷史資料不足："
                f"{count}"
            )

            continue

        status = (
            "complete"
            if count >= MIN_HISTORY_ROWS
            else "short_history"
        )

        source = (
            "TWSE official batch"
            if item["market"] == "TW"
            else "TPEx official batch"
        )

        results[symbol] = {
            "symbol": symbol,
            "code": item["code"],
            "market": item["market"],
            "name": item["name"],
            "source": source,
            "history_rows": count,
            "history_status": status,
            "latest_date": rows[-1]["date"],
            "prices": rows,
        }

    return results, failures


# ============================================================
# SHARDS
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any]
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

            stocks[symbol] = results[
                symbol
            ]["prices"]

        shards.append(
            {
                "stocks": stocks
            }
        )

    return shards


# ============================================================
# SHARD VALIDATION
# ============================================================

def validate_shard(
    path: Path,
    expected_symbols: List[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            f"找不到 shard："
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
            f"{path.name} root 錯誤"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} stocks 錯誤"
        )

    if set(stocks.keys()) != set(
        expected_symbols
    ):

        raise RuntimeError(
            f"{path.name} 股票集合不一致"
        )

    for symbol, rows in stocks.items():

        if not isinstance(
            rows,
            list,
        ):

            raise RuntimeError(
                f"{symbol} prices 不是 list"
            )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{symbol} 歷史資料不足："
                f"{len(rows)}"
            )

        previous_date = ""

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} price row 錯誤"
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
                    f"{symbol} 缺少欄位："
                    f"{sorted(missing)}"
                )

            trade_date = parse_date(
                row["date"]
            )

            if trade_date is None:

                raise RuntimeError(
                    f"{symbol} 日期格式錯誤："
                    f"{row['date']}"
                )

            if (
                previous_date
                and trade_date
                < previous_date
            ):

                raise RuntimeError(
                    f"{symbol} 日期未排序"
                )

            previous_date = trade_date

            close = safe_float(
                row["close"]
            )

            high = safe_float(
                row["high"]
            )

            low = safe_float(
                row["low"]
            )

            if (
                close is None
                or high is None
                or low is None
                or close <= 0
            ):

                raise RuntimeError(
                    f"{symbol} OHLC 異常"
                )


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> Dict[str, Any]:

    source_counts = {}

    complete_count = 0
    short_count = 0

    latest_dates = []

    for result in results.values():

        source = result.get(
            "source",
            ""
        )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

        if (
            result.get(
                "history_status"
            )
            == "complete"
        ):

            complete_count += 1

        else:

            short_count += 1

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
        "complete_history_count": complete_count,
        "short_history_count": short_count,
        "failed_count": (
            universe_count
            - len(results)
        ),
        "min_history_rows": MIN_HISTORY_ROWS,
        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),
        "batch_mode": True,
        "sources": source_counts,
        "latest_date": (
            max(latest_dates)
            if latest_dates
            else None
        ),
        "files": shard_files,
    }


# ============================================================
# MANIFEST VALIDATION
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
            "manifest root 錯誤"
        )

    if manifest.get(
        "universe_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest universe_stock_count 錯誤"
        )

    if manifest.get(
        "price_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest price_stock_count 錯誤："
            f"{manifest.get('price_stock_count')} "
            f"!= "
            f"{len(expected_symbols)}"
        )

    if manifest.get(
        "failed_count"
    ) != 0:

        raise RuntimeError(
            "manifest failed_count 必須為 0"
        )

    files = manifest.get(
        "files"
    )

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 不一致"
        )


# ============================================================
# WRITE TEMP
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

    shards = build_shards(
        results
    )

    symbols = sorted(
        results.keys()
    )

    shard_files = []

    for index, shard in enumerate(
        shards,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = temp_dir / filename

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
        temp_dir / "manifest.json"
    )

    save_json(
        manifest_path,
        manifest,
    )

    validate_manifest(
        manifest_path,
        symbols,
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
# ATOMIC REPLACE
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
# FINAL VALIDATION
# ============================================================

def validate_final_universe(
    universe: List[Dict[str, str]],
    results: Dict[
        str,
        Dict[str, Any]
    ],
) -> None:

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set(
        results.keys()
    )

    if expected != actual:

        missing = sorted(
            expected - actual
        )

        extra = sorted(
            actual - expected
        )

        raise RuntimeError(
            "FINAL Universe / Price "
            "集合不一致；"
            f"missing={missing[:20]}, "
            f"extra={extra[:20]}"
        )

    for symbol, result in results.items():

        rows = result["prices"]

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{symbol} 最終歷史資料不足"
            )

    log(
        f"✓ FINAL Universe 驗證："
        f"{len(expected)} / "
        f"{len(actual)}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

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

    log("")
    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    # --------------------------------------------------------
    # Existing
    # --------------------------------------------------------

    existing = load_existing_prices(
        expected_symbols
    )

    # --------------------------------------------------------
    # Batch fetch
    # --------------------------------------------------------

    history, batch_failures = (
        build_batch_history(
            universe,
            existing,
        )
    )

    # --------------------------------------------------------
    # Individual fallback
    # --------------------------------------------------------

    fallback_failures = (
        apply_individual_fallback(
            universe,
            history,
            batch_failures,
        )
    )

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    results, validation_failures = (
        build_results(
            universe,
            history,
        )
    )

    failures = {}

    failures.update(
        fallback_failures
    )

    failures.update(
        validation_failures
    )

    success_count = len(
        results
    )

    failed_count = (
        universe_count
        - success_count
    )

    success_rate = (
        success_count
        / universe_count
        if universe_count
        else 0
    )

    # --------------------------------------------------------
    # Result
    # --------------------------------------------------------

    section(
        "價格資料結果"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    log(
        f"官方成功："
        f"{success_count}"
    )

    log(
        f"官方失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    # --------------------------------------------------------
    # 1944 HARD GATE
    # --------------------------------------------------------

    if success_count != universe_count:

        log("")
        log(
            "❌ 價格資料未達完整 Universe"
        )

        log(
            f"需要："
            f"{universe_count}"
        )

        log(
            f"實際："
            f"{success_count}"
        )

        log(
            f"缺少："
            f"{failed_count}"
        )

        for symbol, reason in sorted(
            failures.items()
        )[:100]:

            log(
                f"  {symbol}: "
                f"{reason}"
            )

        log("")
        log(
            "❌ 不寫入正式 Data/prices"
        )

        return 1

    # --------------------------------------------------------
    # Success rate safety
    # --------------------------------------------------------

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

        log(
            "❌ 成功率低於安全門檻"
        )

        return 1

    # --------------------------------------------------------
    # FINAL Universe validation
    # --------------------------------------------------------

    try:

        validate_final_universe(
            universe,
            results,
        )

    except Exception as exc:

        log(
            f"❌ FINAL 驗證失敗："
            f"{exc}"
        )

        return 1

    # --------------------------------------------------------
    # 7794
    # --------------------------------------------------------

    if "7794.TWO" in expected_symbols:

        record = results.get(
            "7794.TWO"
        )

        if record:

            log("")
            log(
                "================================================"
            )
            log(
                "✓ 7794.TWO 最終驗證"
            )
            log(
                f"資料筆數："
                f"{record['history_rows']}"
            )
            log(
                f"資料來源："
                f"{record['source']}"
            )
            log(
                f"最新日期："
                f"{record['latest_date']}"
            )
            log(
                f"狀態："
                f"{record['history_status']}"
            )
            log(
                "================================================"
            )

    # --------------------------------------------------------
    # TEMP
    # --------------------------------------------------------

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="prices_build_",
            dir=str(DATA_DIR),
        )
    )

    temp_dir = (
        temp_root / "prices"
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
        # ATOMIC
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
    # FINAL
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
        f"官方成功："
        f"{success_count}"
    )

    log(
        f"官方失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    log(
        "✓ 1944 檔全部進入價格資料鏈"
    )

    if "7794.TWO" in expected_symbols:

        if "7794.TWO" in results:

            log(
                "✓ 7794.TWO："
                "已成功進入價格資料鏈"
            )

        else:

            log(
                "❌ 7794.TWO："
                "仍缺少價格資料"
            )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ fetch_prices.py V8.0 完成"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
