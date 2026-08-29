#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式價格管線 V9.0
============================================================

核心架構
------------------------------------------------------------

Data/universe.json
        │
        ├── STOCK
        │
        └── ETF
                │
                ▼
        官方市場每日批次價格
                │
                ├── TWSE
                └── TPEx
                │
                ▼
          Data/prices/
                │
                ├── prices_001.json
                ├── prices_002.json
                └── manifest.json


V9.0 核心原則
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. STOCK / ETF 都進價格管線
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. 不逐檔抓官方歷史價格
7. 初始化使用整市場 / 每交易日批次
8. TWSE 官方優先
9. TPEx 官方優先
10. Yahoo 僅作最後 fallback
11. fallback 永遠標記來源
12. 不把 fallback 假裝成官方資料
13. OHLC / 日期 / volume 做完整驗證
14. 正常目標 90 筆
15. 絕對最低 20 筆
16. 缺少商品不再阻塞整條價格管線
17. 缺少商品必須明確列入 diagnostics
18. 真正資料格式錯誤仍然 FAIL
19. temporary directory
20. shard 驗證
21. manifest 驗證
22. atomic replace
23. 舊版 prices schema 不符合 V9 時安全重建
24. 每日增量只處理最新交易日
25. Universe 新增商品會重新初始化
26. TPEx 不再猜 OHLC 欄位
27. TPEx 使用官方 aaData 欄位名稱解析
28. 缺少資料 ≠ 程式執行失敗
29. 資料異常 ≠ 靜默忽略
30. 所有缺失商品都留下可追蹤診斷


官方來源
------------------------------------------------------------

TWSE:
https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX

TWSE 全市場最新:
https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL

TPEx:
https://www.tpex.org.tw/web/stock/aftertrading/
otc_quotes_no1430/stk_wn1430_result.php

Yahoo fallback:
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}

============================================================
"""

from __future__ import annotations

import json
import math
import re
import shutil
import sys
import tempfile
import time

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V9.0"
SCHEMA_VERSION = "prices-v9.0"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_DIR = DATA_DIR / "prices"


# ============================================================
# PRICE SETTINGS
# ============================================================

INITIAL_HISTORY_DAYS = 90

ABSOLUTE_MIN_HISTORY_ROWS = 20

INITIAL_LOOKBACK_CALENDAR_DAYS = 180

MAX_HISTORY_ROWS = 90

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0
MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# SAFETY
# ============================================================

MAX_RETRIES = 3

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

# ------------------------------------------------------------
# 注意：
#
# 這個值現在只做 diagnostics。
# 不再作為「整個價格程式是否 exit 1」的條件。
# ------------------------------------------------------------

DIAGNOSTIC_SUCCESS_RATE_TARGET = 0.80


# ============================================================
# OFFICIAL ENDPOINTS
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/MI_INDEX"
)

TWSE_STOCK_DAY_ALL_URL = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/STOCK_DAY_ALL"
)

TPEX_DAILY_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "otc_quotes_no1430/"
    "stk_wn1430_result.php"
)


# ============================================================
# YAHOO FALLBACK
# ============================================================

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


# ============================================================
# HTTP SESSION
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
            indent=2,
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
        .replace("+", "")
        .replace(" ", "")
    )

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

    compact = re.sub(
        r"[^0-9]",
        "",
        text,
    )

    # --------------------------------------------------------
    # YYYYMMDD
    # --------------------------------------------------------

    if len(compact) == 8:

        try:

            year = int(compact[:4])
            month = int(compact[4:6])
            day = int(compact[6:8])

            if 1911 <= year <= 2100:

                dt = date(
                    year,
                    month,
                    day,
                )

                return dt.isoformat()

        except Exception:
            pass

    # --------------------------------------------------------
    # ROC YYYY/MM/DD
    # --------------------------------------------------------

    parts = text.split("/")

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

            return dt.isoformat()

        except Exception:
            pass

    # --------------------------------------------------------
    # ISO
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

    return None


# ============================================================
# CODE
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

    # --------------------------------------------------------
    # 股票 / ETF code：
    # 只接受 4~6 位英數，
    # 且第一位必須為數字。
    # --------------------------------------------------------

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

    text = clean_text(
        value
    ).upper()

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

    text = clean_text(
        value
    ).upper()

    if text in {
        "TW",
        "TWSE",
        "TSE",
        "上市",
        "上市股票",
    }:

        return "TW"

    if text in {
        "TWO",
        "TPEX",
        "OTC",
        "上櫃",
        "上柜",
        "上櫃股票",
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

        code = extract_code(
            value
        )

        if code:

            if value.endswith(".TWO"):
                return code + ".TWO"

            if value.endswith(".TW"):
                return code + ".TW"

    if fallback_key:

        value = clean_text(
            fallback_key
        ).upper()

        code = extract_code(
            value
        )

        if code:

            if value.endswith(".TWO"):
                return code + ".TWO"

            if value.endswith(".TW"):
                return code + ".TW"

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

        code = extract_code(
            symbol
        )

        if code:
            return code

    if fallback_key:

        return extract_code(
            fallback_key
        )

    return None


# ============================================================
# NORMALIZE UNIVERSE RECORD
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

        if symbol.endswith(".TWO"):
            market = "TWO"

        elif symbol.endswith(".TW"):
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
                (None, item)
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

def load_universe() -> Tuple[
    List[Dict[str, str]],
    List[Dict[str, str]],
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
            "Universe stock_count "
            "不存在或不是整數"
        )

    if not isinstance(
        declared_etf_count,
        int,
    ):

        raise RuntimeError(
            "Universe etf_count "
            "不存在或不是整數"
        )

    raw_items = extract_container(
        universe,
        "stocks",
    )

    if not raw_items:

        raise RuntimeError(
            "Universe stocks 為空"
        )

    parsed_stocks = {}
    parsed_etfs = {}

    unparsed = []

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

        symbol = normalized[
            "symbol"
        ]

        if normalized[
            "type"
        ] == "STOCK":

            parsed_stocks.setdefault(
                symbol,
                normalized,
            )

        else:

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
        f"Universe metadata STOCK："
        f"{declared_stock_count}"
    )

    log(
        f"Universe metadata ETF："
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

        raise RuntimeError(
            "Universe STOCK 數量不一致"
        )

    if actual_etf_count != declared_etf_count:

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

    if "7794.TWO" in parsed_stocks:

        target = parsed_stocks[
            "7794.TWO"
        ]

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

    return (
        list(parsed_stocks.values()),
        list(parsed_etfs.values()),
    )


# ============================================================
# HTTP JSON
# ============================================================

def request_json(
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

            text = response.text.lstrip()

            if not text:

                raise RuntimeError(
                    "empty response"
                )

            content_type = (
                response.headers.get(
                    "content-type",
                    ""
                ).lower()
            )

            # ------------------------------------------------
            # TPEx 有時 content-type 不一定嚴格標 JSON，
            # 因此不能只靠 content-type 判斷。
            # ------------------------------------------------

            if (
                "json" not in content_type
                and not text.startswith("{")
                and not text.startswith("[")
            ):

                raise RuntimeError(
                    "response is not JSON"
                )

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
# PRICE ROW VALIDATION
# ============================================================

def normalize_price_row(
    code: str,
    date_value: Any,
    open_value: Any,
    high: Any,
    low: Any,
    close: Any,
    volume: Any,
) -> Optional[Dict[str, Any]]:

    parsed_date = parse_date(
        date_value
    )

    if not parsed_date:
        return None

    o = safe_float(open_value)
    h = safe_float(high)
    l = safe_float(low)
    c = safe_float(close)
    v = safe_int(volume)

    if (
        o is None
        or h is None
        or l is None
        or c is None
    ):
        return None

    if (
        o <= 0
        or h <= 0
        or l <= 0
        or c <= 0
    ):
        return None

    if h < max(o, c):
        return None

    if l > min(o, c):
        return None

    if h < l:
        return None

    if v < 0:
        return None

    return {
        "date": parsed_date,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


# ============================================================
# TWSE RESPONSE RECURSION
# ============================================================

def recursively_find_tables(
    value: Any,
) -> Iterable[Any]:

    if isinstance(
        value,
        dict,
    ):

        yield value

        for child in value.values():

            yield from recursively_find_tables(
                child
            )

    elif isinstance(
        value,
        list,
    ):

        for child in value:

            yield from recursively_find_tables(
                child
            )


# ============================================================
# TWSE TABLE PARSER
# ============================================================

def parse_twse_payload(
    payload: Any,
    requested_date: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for table in recursively_find_tables(
        payload
    ):

        if not isinstance(
            table,
            dict,
        ):
            continue

        fields = table.get(
            "fields"
        )

        data = table.get(
            "data"
        )

        if not isinstance(
            fields,
            list,
        ):
            continue

        if not isinstance(
            data,
            list,
        ):
            continue

        field_names = [
            clean_text(x)
            for x in fields
        ]

        if (
            "證券代號" not in field_names
            or "收盤價" not in field_names
        ):
            continue

        def index_of(
            names: List[str],
        ) -> Optional[int]:

            for name in names:

                if name in field_names:

                    return field_names.index(
                        name
                    )

            return None

        code_index = index_of(
            ["證券代號"]
        )

        date_index = index_of(
            [
                "日期",
                "資料日期",
            ]
        )

        open_index = index_of(
            ["開盤價"]
        )

        high_index = index_of(
            ["最高價"]
        )

        low_index = index_of(
            ["最低價"]
        )

        close_index = index_of(
            ["收盤價"]
        )

        volume_index = index_of(
            [
                "成交股數",
                "成交量",
            ]
        )

        if (
            code_index is None
            or open_index is None
            or high_index is None
            or low_index is None
            or close_index is None
        ):
            continue

        for row in data:

            if not isinstance(
                row,
                list,
            ):
                continue

            def value_at(
                index: Optional[int],
            ) -> Any:

                if (
                    index is None
                    or index >= len(row)
                ):
                    return None

                return row[index]

            code = extract_code(
                value_at(code_index)
            )

            if not code:
                continue

            row_date = (
                value_at(date_index)
                or requested_date
            )

            normalized = normalize_price_row(
                code=code,
                date_value=row_date,
                open_value=value_at(
                    open_index
                ),
                high=value_at(
                    high_index
                ),
                low=value_at(
                    low_index
                ),
                close=value_at(
                    close_index
                ),
                volume=value_at(
                    volume_index
                ),
            )

            if normalized:

                result[code] = normalized

        if result:
            return result

    return result


# ============================================================
# TWSE DAILY
# ============================================================

def fetch_twse_daily(
    target_date: str,
) -> Dict[str, Dict[str, Any]]:

    params = {
        "response": "json",
        "date": target_date.replace(
            "-",
            "",
        ),
        "type": "ALLBUT0999",
    }

    payload = request_json(
        TWSE_MI_INDEX_URL,
        params=params,
    )

    rows = parse_twse_payload(
        payload,
        target_date,
    )

    if rows:

        actual_dates = {
            row["date"]
            for row in rows.values()
        }

        if (
            len(actual_dates) != 1
            or target_date not in actual_dates
        ):

            return {}

    return rows


# ============================================================
# TWSE CURRENT
# ============================================================

def fetch_twse_current() -> Dict[
    str,
    Dict[str, Any]
]:

    payload = request_json(
        TWSE_STOCK_DAY_ALL_URL
    )

    if not isinstance(
        payload,
        list,
    ):

        raise RuntimeError(
            "TWSE STOCK_DAY_ALL "
            "不是 list"
        )

    result = {}

    for row in payload:

        if not isinstance(
            row,
            dict,
        ):
            continue

        code = extract_code(
            row.get("Code")
        )

        if not code:
            continue

        normalized = normalize_price_row(
            code=code,
            date_value=row.get("Date"),
            open_value=row.get(
                "OpeningPrice"
            ),
            high=row.get(
                "HighestPrice"
            ),
            low=row.get(
                "LowestPrice"
            ),
            close=row.get(
                "ClosingPrice"
            ),
            volume=row.get(
                "TradeVolume"
            ),
        )

        if normalized:
            result[code] = normalized

    return result


# ============================================================
# TPEX FIELD NORMALIZATION
# ============================================================

def normalize_tpex_field(
    value: Any,
) -> str:

    text = clean_text(
        value
    )

    text = (
        text
        .replace(" ", "")
        .replace("\t", "")
        .replace("\n", "")
    )

    return text


# ============================================================
# TPEX COLUMN MAP
# ============================================================

TPEX_FIELD_ALIASES = {

    "code": {
        "公司代號",
        "證券代號",
        "有價證券代號",
        "代號",
    },

    "name": {
        "公司名稱",
        "證券名稱",
        "名稱",
    },

    "close": {
        "收盤價",
        "收盤",
    },

    "open": {
        "開盤價",
        "開盤",
    },

    "high": {
        "最高價",
        "最高",
    },

    "low": {
        "最低價",
        "最低",
    },

    "volume": {
        "成交股數",
        "成交量",
    },

    "date": {
        "日期",
        "資料日期",
    },
}


def find_tpex_column(
    fields: List[Any],
    logical_name: str,
) -> Optional[int]:

    aliases = TPEX_FIELD_ALIASES[
        logical_name
    ]

    normalized_fields = [
        normalize_tpex_field(x)
        for x in fields
    ]

    for index, field in enumerate(
        normalized_fields
    ):

        if field in aliases:
            return index

    return None


# ============================================================
# TPEX aaData ROW NORMALIZER
# ============================================================

def parse_tpex_payload(
    payload: Any,
    requested_date: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        payload,
        dict,
    ):
        return result

    aa_data = payload.get(
        "aaData"
    )

    if not isinstance(
        aa_data,
        list,
    ):
        return result

    # --------------------------------------------------------
    # TPEx API 通常直接提供 columns / fields。
    # 如果有欄位名稱，完全依欄位名稱解析。
    # --------------------------------------------------------

    fields = payload.get(
        "fields"
    )

    if not isinstance(
        fields,
        list,
    ):

        fields = payload.get(
            "columns"
        )

    if isinstance(
        fields,
        list,
    ):

        code_index = find_tpex_column(
            fields,
            "code",
        )

        close_index = find_tpex_column(
            fields,
            "close",
        )

        open_index = find_tpex_column(
            fields,
            "open",
        )

        high_index = find_tpex_column(
            fields,
            "high",
        )

        low_index = find_tpex_column(
            fields,
            "low",
        )

        volume_index = find_tpex_column(
            fields,
            "volume",
        )

        date_index = find_tpex_column(
            fields,
            "date",
        )

        if (
            code_index is not None
            and close_index is not None
            and open_index is not None
            and high_index is not None
            and low_index is not None
        ):

            for row in aa_data:

                if not isinstance(
                    row,
                    list,
                ):
                    continue

                def value_at(
                    index: Optional[int],
                ) -> Any:

                    if (
                        index is None
                        or index >= len(row)
                    ):
                        return None

                    return row[index]

                code = extract_code(
                    value_at(
                        code_index
                    )
                )

                if not code:
                    continue

                row_date = (
                    value_at(
                        date_index
                    )
                    or requested_date
                )

                normalized = normalize_price_row(
                    code=code,
                    date_value=row_date,
                    open_value=value_at(
                        open_index
                    ),
                    high=value_at(
                        high_index
                    ),
                    low=value_at(
                        low_index
                    ),
                    close=value_at(
                        close_index
                    ),
                    volume=value_at(
                        volume_index
                    ),
                )

                if normalized:
                    result[code] = normalized

    # --------------------------------------------------------
    # 相容舊 TPEx payload：
    #
    # 官方頁面目前常見 aaData 直接搭配固定欄位順序。
    #
    # 這裡只在沒有 fields / columns 時使用。
    #
    # 絕對不再用「連續四個數字猜 OHLC」。
    # --------------------------------------------------------

    if not result:

        for row in aa_data:

            if not isinstance(
                row,
                list,
            ):
                continue

            if len(row) < 7:
                continue

            code = extract_code(
                row[0]
            )

            if not code:
                continue

            # ------------------------------------------------
            # TPEx STK_WN1430 常見排列：
            #
            # 0 公司代號
            # 1 公司名稱
            # 2 收盤價
            # 3 漲跌
            # 4 開盤價
            # 5 最高價
            # 6 最低價
            # 7 成交股數
            #
            # 但不同版本可能增加欄位。
            #
            # 只有在結構明確符合這個模式時才接受。
            # ------------------------------------------------

            close_value = row[2]
            open_value = row[4]
            high_value = row[5]
            low_value = row[6]

            volume_value = (
                row[7]
                if len(row) > 7
                else 0
            )

            normalized = normalize_price_row(
                code=code,
                date_value=requested_date,
                open_value=open_value,
                high=high_value,
                low=low_value,
                close=close_value,
                volume=volume_value,
            )

            if normalized:

                result[code] = normalized

    # --------------------------------------------------------
    # 日期驗證
    # --------------------------------------------------------

    if result:

        actual_dates = {
            row["date"]
            for row in result.values()
        }

        if (
            len(actual_dates) != 1
            or requested_date not in actual_dates
        ):

            return {}

    return result


# ============================================================
# TPEX DAILY
# ============================================================

def fetch_tpex_daily(
    target_date: str,
) -> Dict[str, Dict[str, Any]]:

    dt = datetime.strptime(
        target_date,
        "%Y-%m-%d",
    )

    roc_year = (
        dt.year - 1911
    )

    date_value = (
        f"{roc_year:03d}/"
        f"{dt.month:02d}/"
        f"{dt.day:02d}"
    )

    params = {
        "l": "zh-tw",
        "d": date_value,
        "se": "EW",
        "_": str(
            int(
                time.time() * 1000
            )
        ),
    }

    payload = request_json(
        TPEX_DAILY_URL,
        params=params,
    )

    return parse_tpex_payload(
        payload,
        target_date,
    )


# ============================================================
# YAHOO PARSER
# ============================================================

def parse_yahoo_payload(
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

    quotes = indicators.get(
        "quote",
        []
    )

    if not timestamps or not quotes:
        return []

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

    rows = []

    for i, timestamp in enumerate(
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

        row = normalize_price_row(
            code="",
            date_value=date_value,
            open_value=(
                opens[i]
                if i < len(opens)
                else None
            ),
            high=(
                highs[i]
                if i < len(highs)
                else None
            ),
            low=(
                lows[i]
                if i < len(lows)
                else None
            ),
            close=(
                closes[i]
                if i < len(closes)
                else None
            ),
            volume=(
                volumes[i]
                if i < len(volumes)
                else 0
            ),
        )

        if row:
            rows.append(row)

    return sorted(
        rows,
        key=lambda x: x["date"],
    )


# ============================================================
# YAHOO
# ============================================================

def fetch_yahoo_symbol(
    symbol: str,
) -> List[
    Dict[str, Any]
]:

    params = {
        "period1": int(
            (
                datetime.now(
                    timezone.utc
                )
                - timedelta(
                    days=INITIAL_HISTORY_DAYS
                    + 30
                )
            ).timestamp()
        ),
        "period2": int(
            datetime.now(
                timezone.utc
            ).timestamp()
        ),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    try:

        payload = request_json(
            YAHOO_URL.format(
                symbol=symbol
            ),
            params=params,
        )

        return parse_yahoo_payload(
            payload
        )

    except Exception as exc:

        log(
            f"      ⚠️ Yahoo fallback "
            f"{symbol}：{exc}"
        )

        return []


# ============================================================
# DATE RANGE
# ============================================================

def candidate_dates(
    days: int,
) -> List[str]:

    today = date.today()

    start = (
        today
        - timedelta(
            days=days
        )
    )

    dates = []

    current = start

    while current <= today:

        if current.weekday() < 5:

            dates.append(
                current.isoformat()
            )

        current += timedelta(
            days=1
        )

    return dates


# ============================================================
# MARKET HISTORY STRUCTURE
# ============================================================

def build_empty_market_history(
    universe: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for item in universe:

        result[item["symbol"]] = {
            "symbol": item["symbol"],
            "code": item["code"],
            "market": item["market"],
            "type": item["type"],
            "name": item["name"],
            "source": "official",
            "prices": {},
        }

    return result


# ============================================================
# APPEND MARKET DATA
# ============================================================

def append_market_snapshot(
    records: Dict[str, Dict[str, Any]],
    market_rows: Dict[str, Dict[str, Any]],
    universe_by_code: Dict[
        Tuple[str, str],
        Dict[str, str],
    ],
    source: str,
) -> int:

    added = 0

    for code, row in market_rows.items():

        # ----------------------------------------------------
        # 直接用市場 + code 找 Universe。
        # 不用跨市場猜測。
        # ----------------------------------------------------

        # 此函式一次只處理一個市場。
        # source 決定 market。
        market = (
            "TW"
            if source == "official_twse"
            else "TWO"
        )

        item = universe_by_code.get(
            (
                market,
                code,
            )
        )

        if item is None:
            continue

        symbol = item["symbol"]

        if symbol not in records:
            continue

        records[
            symbol
        ]["prices"][
            row["date"]
        ] = row

        records[
            symbol
        ]["source"] = source

        added += 1

    return added


# ============================================================
# INITIAL HISTORY
# ============================================================

def fetch_initial_history(
    universe: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, str],
]:

    section(
        "V9.0 全市場批次初始化"
    )

    log(
        f"目標歷史：最近 "
        f"{INITIAL_HISTORY_DAYS} 個交易日"
    )

    log(
        "官方來源：TWSE + TPEx"
    )

    log(
        "fallback：Yahoo"
    )

    records = build_empty_market_history(
        universe
    )

    universe_by_code = {
        (
            item["market"],
            item["code"],
        ): item
        for item in universe
    }

    dates = candidate_dates(
        INITIAL_LOOKBACK_CALENDAR_DAYS
    )

    total_dates = len(
        dates
    )

    twse_success_dates = 0
    tpex_success_dates = 0

    for index, target_date in enumerate(
        dates,
        start=1,
    ):

        complete = all(
            len(
                record["prices"]
            ) >= INITIAL_HISTORY_DAYS
            for record in records.values()
        )

        if complete:

            log("")
            log(
                f"✓ 全市場已取得 "
                f"{INITIAL_HISTORY_DAYS} "
                "個交易日"
            )

            break

        log(
            f"[BATCH {index}/{total_dates}] "
            f"{target_date}"
        )

        # ====================================================
        # TWSE
        # ====================================================

        try:

            twse_rows = fetch_twse_daily(
                target_date
            )

            if twse_rows:

                twse_success_dates += 1

                added = append_market_snapshot(
                    records,
                    twse_rows,
                    universe_by_code,
                    "official_twse",
                )

                log(
                    f"      ✓ TWSE："
                    f"{len(twse_rows)} 檔，"
                    f"匹配 {added}"
                )

            else:

                log(
                    "      ↳ TWSE："
                    "無有效資料"
                )

        except Exception as exc:

            log(
                f"      ⚠️ TWSE："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

        # ====================================================
        # TPEx
        # ====================================================

        try:

            tpex_rows = fetch_tpex_daily(
                target_date
            )

            if tpex_rows:

                tpex_success_dates += 1

                added = append_market_snapshot(
                    records,
                    tpex_rows,
                    universe_by_code,
                    "official_tpex",
                )

                log(
                    f"      ✓ TPEx："
                    f"{len(tpex_rows)} 檔，"
                    f"匹配 {added}"
                )

            else:

                log(
                    "      ↳ TPEx："
                    "無有效資料"
                )

        except Exception as exc:

            log(
                f"      ⚠️ TPEx："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # ========================================================
    # 官方結果
    # ========================================================

    results = {}
    diagnostics = {}

    for symbol, record in records.items():

        rows = sorted(
            record["prices"].values(),
            key=lambda x: x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        if len(rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            results[symbol] = {
                "symbol": symbol,
                "code": record["code"],
                "market": record["market"],
                "type": record["type"],
                "name": record["name"],
                "source": record["source"],
                "history_rows": len(rows),
                "history_status": (
                    "complete"
                    if len(rows)
                    >= INITIAL_HISTORY_DAYS
                    else "short_history"
                ),
                "latest_date": rows[-1]["date"],
                "prices": rows,
            }

        else:

            diagnostics[symbol] = (
                "official_history_insufficient:"
                f"{len(rows)}"
            )

    # ========================================================
    # YAHOO FALLBACK
    # ========================================================

    fallback_candidates = [
        symbol
        for symbol in records
        if symbol not in results
    ]

    if fallback_candidates:

        section(
            "官方批次不足 → Yahoo 最後 fallback"
        )

        log(
            f"需要 fallback："
            f"{len(fallback_candidates)} 檔"
        )

        universe_map = {
            item["symbol"]: item
            for item in universe
        }

        for index, symbol in enumerate(
            fallback_candidates,
            start=1,
        ):

            item = universe_map.get(
                symbol
            )

            if item is None:
                continue

            log(
                f"[FALLBACK "
                f"{index}/"
                f"{len(fallback_candidates)}] "
                f"{symbol}"
            )

            yahoo_rows = fetch_yahoo_symbol(
                symbol
            )

            yahoo_rows = yahoo_rows[
                -MAX_HISTORY_ROWS:
            ]

            if len(yahoo_rows) >= (
                ABSOLUTE_MIN_HISTORY_ROWS
            ):

                results[symbol] = {
                    "symbol": symbol,
                    "code": item["code"],
                    "market": item["market"],
                    "type": item["type"],
                    "name": item["name"],
                    "source": "Yahoo fallback",
                    "history_rows": len(
                        yahoo_rows
                    ),
                    "history_status": (
                        "complete"
                        if len(yahoo_rows)
                        >= INITIAL_HISTORY_DAYS
                        else "short_history"
                    ),
                    "latest_date":
                        yahoo_rows[-1]["date"],
                    "prices": yahoo_rows,
                }

                diagnostics.pop(
                    symbol,
                    None,
                )

                log(
                    f"      ✓ Yahoo："
                    f"{len(yahoo_rows)} 筆"
                )

            else:

                diagnostics[symbol] = (
                    "official_and_yahoo_insufficient:"
                    f"{len(yahoo_rows)}"
                )

            time.sleep(
                REQUEST_DELAY
            )

    return results, diagnostics


# ============================================================
# LOAD EXISTING PRICES
# ============================================================

def load_existing_prices(
    universe: List[Dict[str, str]],
) -> Optional[
    Dict[str, Dict[str, Any]]
]:

    section(
        "檢查既有 Data/prices"
    )

    if not OUTPUT_DIR.exists():

        log(
            "既有 Data/prices 不存在"
        )

        return None

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if not manifest_path.exists():

        log(
            "⚠️ 沒有 manifest.json，"
            "視為舊版資料"
        )

        return None

    try:

        manifest = load_json(
            manifest_path
        )

        if not isinstance(
            manifest,
            dict,
        ):
            return None

        if manifest.get(
            "schema_version"
        ) != SCHEMA_VERSION:

            log(
                "⚠️ prices schema 不是 "
                f"{SCHEMA_VERSION}"
            )

            return None

        files = manifest.get(
            "files"
        )

        if not isinstance(
            files,
            list,
        ):
            return None

        results = {}

        for filename in files:

            path = (
                OUTPUT_DIR
                / filename
            )

            if not path.exists():

                raise RuntimeError(
                    f"缺少 shard："
                    f"{filename}"
                )

            data = load_json(
                path
            )

            if not isinstance(
                data,
                dict,
            ):
                raise RuntimeError(
                    f"{filename} root 錯誤"
                )

            stocks = data.get(
                "stocks"
            )

            if not isinstance(
                stocks,
                dict,
            ):
                return None

            for symbol, rows in stocks.items():

                if not isinstance(
                    rows,
                    list,
                ):
                    return None

                normalized_rows = []

                for row in rows:

                    if not isinstance(
                        row,
                        dict,
                    ):
                        continue

                    normalized = normalize_price_row(
                        code="",
                        date_value=row.get(
                            "date"
                        ),
                        open_value=row.get(
                            "open"
                        ),
                        high=row.get(
                            "high"
                        ),
                        low=row.get(
                            "low"
                        ),
                        close=row.get(
                            "close"
                        ),
                        volume=row.get(
                            "volume"
                        ),
                    )

                    if normalized:

                        normalized_rows.append(
                            normalized
                        )

                if normalized_rows:

                    results[symbol] = {
                        "symbol": symbol,
                        "prices": sorted(
                            normalized_rows,
                            key=lambda x:
                                x["date"],
                        )[
                            -MAX_HISTORY_ROWS:
                        ],
                    }

        if not results:

            return None

        log(
            f"既有價格資料："
            f"{len(results)} 檔"
        )

        return results

    except Exception as exc:

        log(
            f"⚠️ 舊版 prices 無法讀取："
            f"{exc}"
        )

        log(
            "↳ 安全重建 V9.0"
        )

        return None


# ============================================================
# UPDATE EXISTING
# ============================================================

def update_existing_with_latest(
    existing: Dict[str, Dict[str, Any]],
    universe: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, str],
    bool,
]:

    section(
        "V9.0 每日增量更新"
    )

    universe_map = {
        item["symbol"]: item
        for item in universe
    }

    # ========================================================
    # TWSE
    # ========================================================

    twse_rows = {}

    try:

        twse_rows = fetch_twse_current()

        log(
            f"✓ TWSE 最新快照："
            f"{len(twse_rows)} 檔"
        )

    except Exception as exc:

        log(
            f"⚠️ TWSE 最新快照失敗："
            f"{exc}"
        )

    time.sleep(
        REQUEST_DELAY
    )

    # ========================================================
    # TPEx
    # ========================================================

    today = date.today()
    current_date = today
    tpex_rows = {}

    for _ in range(7):

        if current_date.weekday() >= 5:

            current_date -= timedelta(
                days=1
            )

            continue

        target = current_date.isoformat()

        try:

            tpex_rows = fetch_tpex_daily(
                target
            )

            if tpex_rows:

                log(
                    f"✓ TPEx 最新快照："
                    f"{len(tpex_rows)} 檔 "
                    f"{target}"
                )

                break

        except Exception as exc:

            log(
                f"      ⚠️ TPEx "
                f"{target}："
                f"{exc}"
            )

        current_date -= timedelta(
            days=1
        )

    latest_dates = []

    latest_dates.extend(
        row["date"]
        for row in twse_rows.values()
    )

    latest_dates.extend(
        row["date"]
        for row in tpex_rows.values()
    )

    if not latest_dates:

        log(
            "⚠️ 官方最新資料完全不可用"
        )

        return {}, {
            item["symbol"]:
                "official_latest_unavailable"
            for item in universe
        }, False

    latest_date = max(
        latest_dates
    )

    log(
        f"本次價格更新日："
        f"{latest_date}"
    )

    result = {}
    diagnostics = {}
    updated = False

    for item in universe:

        symbol = item["symbol"]

        previous = existing.get(
            symbol
        )

        if previous is None:

            diagnostics[symbol] = (
                "new_universe_symbol"
            )

            continue

        rows = previous.get(
            "prices",
            []
        )

        row_map = {
            row["date"]: row
            for row in rows
            if isinstance(row, dict)
            and row.get("date")
        }

        source = "existing"

        market_rows = (
            twse_rows
            if item["market"] == "TW"
            else tpex_rows
        )

        official_row = market_rows.get(
            item["code"]
        )

        if official_row:

            row_map[
                official_row["date"]
            ] = official_row

            source = (
                "official_twse"
                if item["market"] == "TW"
                else "official_tpex"
            )

            updated = True

        else:

            # ------------------------------------------------
            # 只有官方沒有該商品時才 fallback。
            # ------------------------------------------------

            yahoo_rows = fetch_yahoo_symbol(
                symbol
            )

            candidates = [
                row
                for row in yahoo_rows
                if row["date"] == latest_date
            ]

            if candidates:

                row_map[
                    latest_date
                ] = candidates[0]

                source = "Yahoo fallback"

                updated = True

            else:

                diagnostics[symbol] = (
                    "latest_price_missing"
                )

        final_rows = sorted(
            row_map.values(),
            key=lambda x: x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        if len(final_rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            diagnostics[symbol] = (
                "history_below_minimum:"
                f"{len(final_rows)}"
            )

            continue

        result[symbol] = {
            "symbol": symbol,
            "code": item["code"],
            "market": item["market"],
            "type": item["type"],
            "name": item["name"],
            "source": source,
            "history_rows": len(
                final_rows
            ),
            "history_status": (
                "complete"
                if len(final_rows)
                >= INITIAL_HISTORY_DAYS
                else "short_history"
            ),
            "latest_date":
                final_rows[-1]["date"],
            "prices": final_rows,
        }

    return (
        result,
        diagnostics,
        updated,
    )


# ============================================================
# RESULT VALIDATION
# ============================================================

def validate_results(
    results: Dict[str, Dict[str, Any]],
    universe: List[Dict[str, str]],
) -> Dict[str, Any]:

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set(
        results.keys()
    )

    missing = sorted(
        expected - actual
    )

    malformed = []

    for symbol, record in results.items():

        rows = record.get(
            "prices"
        )

        if not isinstance(
            rows,
            list,
        ):

            malformed.append(
                (
                    symbol,
                    "prices_not_list",
                )
            )

            continue

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            malformed.append(
                (
                    symbol,
                    f"history_below_{ABSOLUTE_MIN_HISTORY_ROWS}",
                )
            )

            continue

        previous = ""

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                malformed.append(
                    (
                        symbol,
                        "row_not_object",
                    )
                )

                break

            normalized = normalize_price_row(
                code="",
                date_value=row.get(
                    "date"
                ),
                open_value=row.get(
                    "open"
                ),
                high=row.get(
                    "high"
                ),
                low=row.get(
                    "low"
                ),
                close=row.get(
                    "close"
                ),
                volume=row.get(
                    "volume"
                ),
            )

            if normalized is None:

                malformed.append(
                    (
                        symbol,
                        "invalid_ohlcv",
                    )
                )

                break

            if (
                previous
                and row["date"]
                <= previous
            ):

                malformed.append(
                    (
                        symbol,
                        "date_not_strictly_increasing",
                    )
                )

                break

            previous = row["date"]

        source = record.get(
            "source"
        )

        if not source:

            malformed.append(
                (
                    symbol,
                    "missing_source",
                )
            )

    return {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing": missing,
        "malformed": malformed,
        "success_rate": (
            len(actual) / len(expected)
            if expected
            else 0
        ),
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

def print_diagnostics(
    validation: Dict[str, Any],
    diagnostics: Dict[str, str],
) -> None:

    missing = validation[
        "missing"
    ]

    malformed = validation[
        "malformed"
    ]

    success_rate = validation[
        "success_rate"
    ]

    section(
        "PRICE DATA DIAGNOSTICS"
    )

    log(
        f"Universe："
        f"{validation['expected_count']}"
    )

    log(
        f"已有價格："
        f"{validation['actual_count']}"
    )

    log(
        f"缺少："
        f"{len(missing)}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    if missing:

        log("")
        log(
            "⚠️ 缺少價格資料："
        )

        for symbol in missing:

            reason = diagnostics.get(
                symbol,
                "missing_price_data",
            )

            log(
                f"  - {symbol}"
                f" → {reason}"
            )

    if malformed:

        log("")
        log(
            "❌ 真正資料結構錯誤："
        )

        for symbol, reason in malformed:

            log(
                f"  - {symbol}"
                f" → {reason}"
            )

        # ----------------------------------------------------
        # malformed 才是真正阻塞性錯誤。
        # ----------------------------------------------------

        raise RuntimeError(
            "存在無效價格資料"
        )

    if (
        success_rate
        < DIAGNOSTIC_SUCCESS_RATE_TARGET
    ):

        log("")
        log(
            "⚠️ 價格完整率低於 "
            f"{DIAGNOSTIC_SUCCESS_RATE_TARGET:.0%}"
        )

        log(
            "⚠️ 這是資料完整性警告，"
            "不是程式執行錯誤"
        )

    elif missing:

        log("")
        log(
            "⚠️ 少數商品尚未取得價格，"
            "但價格管線本身正常完成"
        )

    else:

        log("")
        log(
            "✓ 全部 Universe 商品"
            "均取得有效價格資料"
        )


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
            f"{path.name} 超過 "
            f"{MAX_FILE_SIZE_MB} MB"
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
                f"{symbol} prices "
                "不是 list"
            )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{symbol} 歷史不足"
            )

        previous = ""

        for row in rows:

            normalized = normalize_price_row(
                code="",
                date_value=row.get(
                    "date"
                ),
                open_value=row.get(
                    "open"
                ),
                high=row.get(
                    "high"
                ),
                low=row.get(
                    "low"
                ),
                close=row.get(
                    "close"
                ),
                volume=row.get(
                    "volume"
                ),
            )

            if normalized is None:

                raise RuntimeError(
                    f"{symbol} OHLCV 異常"
                )

            if (
                previous
                and row["date"]
                <= previous
            ):

                raise RuntimeError(
                    f"{symbol} 日期排序錯誤"
                )

            previous = row["date"]


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, str],
) -> Dict[str, Any]:

    source_counts = {}
    type_counts = {}

    complete_count = 0
    short_count = 0

    latest_dates = []

    for result in results.values():

        source = result.get(
            "source",
            "unknown"
        )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

        record_type = result.get(
            "type",
            "unknown"
        )

        type_counts[record_type] = (
            type_counts.get(
                record_type,
                0,
            )
            + 1
        )

        if result.get(
            "history_status"
        ) == "complete":

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
        "schema_version":
            SCHEMA_VERSION,

        "generator_version":
            VERSION,

        "generated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "universe_stock_count":
            universe_stock_count,

        "universe_etf_count":
            universe_etf_count,

        "expected_total_count":
            validation[
                "expected_count"
            ],

        "price_stock_count":
            sum(
                1
                for x in results.values()
                if x.get("type")
                == "STOCK"
            ),

        "price_etf_count":
            sum(
                1
                for x in results.values()
                if x.get("type")
                == "ETF"
            ),

        "price_total_count":
            len(results),

        "missing_count":
            len(
                validation["missing"]
            ),

        "missing_symbols":
            validation["missing"],

        "complete_history_count":
            complete_count,

        "short_history_count":
            short_count,

        "absolute_min_history_rows":
            ABSOLUTE_MIN_HISTORY_ROWS,

        "target_history_rows":
            INITIAL_HISTORY_DAYS,

        "max_history_rows":
            MAX_HISTORY_ROWS,

        "success_rate":
            validation["success_rate"],

        "diagnostic_success_rate_target":
            DIAGNOSTIC_SUCCESS_RATE_TARGET,

        "sources":
            source_counts,

        "types":
            type_counts,

        "diagnostics":
            diagnostics,

        "latest_date":
            max(latest_dates)
            if latest_dates
            else None,

        "files":
            shard_files,
    }


# ============================================================
# MANIFEST VALIDATION
# ============================================================

def validate_manifest(
    path: Path,
    expected_symbols: List[str],
    expected_shards: List[str],
    universe_stock_count: int,
    universe_etf_count: int,
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
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            "manifest schema_version 錯誤"
        )

    if manifest.get(
        "universe_stock_count"
    ) != universe_stock_count:

        raise RuntimeError(
            "manifest stock count 錯誤"
        )

    if manifest.get(
        "universe_etf_count"
    ) != universe_etf_count:

        raise RuntimeError(
            "manifest ETF count 錯誤"
        )

    if manifest.get(
        "price_total_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest price_total_count "
            "錯誤"
        )

    files = manifest.get(
        "files"
    )

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 不一致"
        )

    missing_count = manifest.get(
        "missing_count"
    )

    if not isinstance(
        missing_count,
        int,
    ):

        raise RuntimeError(
            "manifest missing_count 錯誤"
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
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, str],
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
        universe_stock_count,
        universe_etf_count,
        validation,
        diagnostics,
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
        universe_stock_count,
        universe_etf_count,
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
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    # ========================================================
    # UNIVERSE
    # ========================================================

    stocks, etfs = load_universe()

    universe = (
        stocks
        + etfs
    )

    universe_stock_count = len(
        stocks
    )

    universe_etf_count = len(
        etfs
    )

    universe_count = len(
        universe
    )

    log(
        f"Universe STOCK："
        f"{universe_stock_count}"
    )

    log(
        f"Universe ETF："
        f"{universe_etf_count}"
    )

    log(
        f"Universe TOTAL："
        f"{universe_count}"
    )

    # ========================================================
    # EXISTING
    # ========================================================

    existing = load_existing_prices(
        universe
    )

    diagnostics = {}

    # ========================================================
    # INITIAL / INCREMENTAL
    # ========================================================

    if existing is None:

        results, diagnostics = (
            fetch_initial_history(
                universe
            )
        )

    else:

        (
            results,
            diagnostics,
            updated,
        ) = update_existing_with_latest(
            existing,
            universe,
        )

        # ----------------------------------------------------
        # 如果有新 Universe 商品，
        # 不用不完整資料混入。
        # 重新做全市場初始化。
        # ----------------------------------------------------

        expected_symbols = {
            item["symbol"]
            for item in universe
        }

        existing_symbols = set(
            existing.keys()
        )

        new_symbols = (
            expected_symbols
            - existing_symbols
        )

        if new_symbols:

            log("")
            log(
                f"⚠️ Universe 新增 "
                f"{len(new_symbols)} 檔"
            )

            log(
                "↳ 重新執行全市場初始化"
            )

            results, diagnostics = (
                fetch_initial_history(
                    universe
                )
            )

        elif not results:

            log("")
            log(
                "⚠️ 增量資料無法建立有效結果"
            )

            log(
                "↳ 重新執行全市場初始化"
            )

            results, diagnostics = (
                fetch_initial_history(
                    universe
                )
            )

    # ========================================================
    # VALIDATION
    # ========================================================

    validation = validate_results(
        results,
        universe,
    )

    print_diagnostics(
        validation,
        diagnostics,
    )

    # ========================================================
    # RESULT SUMMARY
    # ========================================================

    success_count = validation[
        "actual_count"
    ]

    failed_count = validation[
        "expected_count"
    ] - success_count

    success_rate = validation[
        "success_rate"
    ]

    section(
        "價格資料結果"
    )

    log(
        f"Universe TOTAL："
        f"{universe_count}"
    )

    log(
        f"有效價格："
        f"{success_count}"
    )

    log(
        f"缺少："
        f"{failed_count}"
    )

    log(
        f"完整率："
        f"{success_rate:.2%}"
    )

    # ========================================================
    # SOURCE COUNT
    # ========================================================

    source_counts = {}

    for record in results.values():

        source = record.get(
            "source",
            "unknown"
        )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # ========================================================
    # IMPORTANT:
    #
    # 缺少商品不再 exit 1。
    #
    # 真正資料結構錯誤已經由
    # print_diagnostics() raise。
    #
    # 所以這裡不再存在：
    #
    # if success_rate < 80:
    #     return 1
    #
    # ========================================================

    if validation["missing"]:

        log("")
        log(
            "⚠️ 本次存在未取得價格的 Universe 商品"
        )

        log(
            "⚠️ 不阻塞價格管線"
        )

    # ========================================================
    # 7794
    # ========================================================

    universe_symbols = {
        x["symbol"]
        for x in universe
    }

    if "7794.TWO" in universe_symbols:

        if "7794.TWO" in results:

            record = results[
                "7794.TWO"
            ]

            log("")
            log(
                "================================================"
            )
            log(
                "✓ 7794.TWO 最終價格驗證"
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

            latest = record[
                "prices"
            ][-1]

            log(
                f"最新收盤："
                f"{latest['close']}"
            )

            log(
                "================================================"
            )

        else:

            log("")
            log(
                "⚠️ 7794.TWO 尚未取得價格"
            )

    # ========================================================
    # TEMP DIRECTORY
    # ========================================================

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
            universe_stock_count,
            universe_etf_count,
            validation,
            diagnostics,
        )

        # ====================================================
        # ATOMIC
        # ====================================================

        section(
            "Atomic replace Data/prices"
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

        return 1

    finally:

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

    # ========================================================
    # FINAL
    # ========================================================

    elapsed = (
        time.time()
        - started
    )

    section(
        "FINAL PRICE RESULT"
    )

    log(
        f"Universe STOCK："
        f"{universe_stock_count}"
    )

    log(
        f"Universe ETF："
        f"{universe_etf_count}"
    )

    log(
        f"Universe TOTAL："
        f"{universe_count}"
    )

    log(
        f"Price 有效："
        f"{success_count}"
    )

    log(
        f"Price 缺少："
        f"{failed_count}"
    )

    log(
        f"完整率："
        f"{success_rate:.2%}"
    )

    if validation["missing"]:

        log(
            "⚠️ Pipeline：SUCCESS"
        )

        log(
            "⚠️ Data completeness："
            "WARNING"
        )

        log(
            f"⚠️ Missing symbols："
            f"{len(validation['missing'])}"
        )

    else:

        log(
            "✓ Pipeline：SUCCESS"
        )

        log(
            "✓ Data completeness："
            "100%"
        )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log("")
    log(
        "✓ fetch_prices.py V9.0 完成"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
