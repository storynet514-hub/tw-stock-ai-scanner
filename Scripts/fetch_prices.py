#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式價格管線 V8.2
============================================================

核心架構
------------------------------------------------------------

Data/universe.json
        │
        ├── STOCK
        │      │
        │      └── 全市場批次價格
        │
        └── ETF
               │
               └── 全市場批次價格
                       │
                       ▼
                Data/prices/
                       │
                       ├── prices_001.json
                       ├── prices_002.json
                       ├── ...
                       └── manifest.json


V8.2 核心原則
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. STOCK / ETF 都進價格管線
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. 不逐檔抓歷史價格
7. 初始化只建立最近 90 個交易日
8. 歷史資料使用「整市場 / 每交易日」批次抓取
9. TWSE 官方優先
10. TPEx 官方優先
11. Yahoo 僅作官方市場批次失敗時的最後 fallback
12. fallback 永遠標記來源
13. 不把 fallback 假裝成官方資料
14. OHLC / 日期 / volume 做資料完整性驗證
15. 每檔正常目標至少 90 筆
16. 絕對最低 20 筆
17. 成功率低於 80% 時 FAIL
18. temporary directory
19. shard 驗證
20. manifest 驗證
21. atomic replace
22. 舊版 prices shard schema 不符合 V8.2 時安全重建
23. 每日增量只抓最新交易日，不重新建立 90 日歷史


官方來源
------------------------------------------------------------

TWSE
    https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX

TPEx
    https://www.tpex.org.tw/web/stock/aftertrading/
    otc_quotes_no1430/stk_wn1430_result.php

Yahoo
    https://query1.finance.yahoo.com/v8/finance/chart/{symbol}

Yahoo 只作最後 fallback。

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

VERSION = "V8.2"
SCHEMA_VERSION = "prices-v8.2"


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

# 為了取得 90 個實際交易日，
# 初始化最多往前搜尋約 150 個曆日。
INITIAL_LOOKBACK_CALENDAR_DAYS = 150

# 正常增量只保留最近這個數量。
MAX_HISTORY_ROWS = 90

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0
MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# SAFETY
# ============================================================

MIN_SUCCESS_RATE = 0.80

MAX_RETRIES = 3

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5


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
        .replace("+", "")
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

    # --------------------------------------------------------
    # ROC YYYYMMDD / YYMMDD
    # --------------------------------------------------------

    compact = re.sub(
        r"[^0-9]",
        "",
        text,
    )

    if len(compact) == 8:

        try:

            year = int(
                compact[:4]
            )

            month = int(
                compact[4:6]
            )

            day = int(
                compact[6:8]
            )

            if 1911 <= year <= 2100:

                return (
                    f"{year:04d}-"
                    f"{month:02d}-"
                    f"{day:02d}"
                )

        except Exception:
            pass

    if len(compact) == 7:

        try:

            year = (
                int(compact[:3])
                + 1911
            )

            month = int(
                compact[3:5]
            )

            day = int(
                compact[5:7]
            )

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # ROC / date
    # --------------------------------------------------------

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

    symbol = code + suffix

    return {
        "symbol": symbol,
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

            if symbol not in parsed_stocks:

                parsed_stocks[
                    symbol
                ] = normalized

        elif normalized[
            "type"
        ] == "ETF":

            if symbol not in parsed_etfs:

                parsed_etfs[
                    symbol
                ] = normalized

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

    if actual_stock_count != (
        declared_stock_count
    ):

        raise RuntimeError(
            "Universe STOCK 數量不一致"
        )

    if actual_etf_count != (
        declared_etf_count
    ):

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
            f"  code   = "
            f"{target['code']}"
        )
        log(
            f"  market = "
            f"{target['market']}"
        )
        log(
            f"  symbol = "
            f"{target['symbol']}"
        )
        log(
            f"  type   = "
            f"{target['type']}"
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

            content_type = (
                response.headers.get(
                    "content-type",
                    ""
                ).lower()
            )

            text = response.text.lstrip()

            if not text:

                raise RuntimeError(
                    "empty response"
                )

            if (
                "json" not in content_type
                and not text.startswith(
                    "{"
                )
                and not text.startswith(
                    "["
                )
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
# GENERIC VALUE HELPERS
# ============================================================

def first_value(
    row: Dict[str, Any],
    keys: Iterable[str],
) -> Any:

    lowered = {
        str(k).strip().lower(): v
        for k, v in row.items()
    }

    for key in keys:

        value = lowered.get(
            str(key).strip().lower()
        )

        if value is not None:

            return value

    return None


def find_key(
    row: Dict[str, Any],
    candidates: Iterable[str],
) -> Optional[str]:

    candidate_set = {
        str(x).strip().lower()
        for x in candidates
    }

    for key in row.keys():

        if (
            str(key)
            .strip()
            .lower()
            in candidate_set
        ):

            return key

    return None


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

    o = safe_float(
        open_value
    )

    h = safe_float(
        high
    )

    l = safe_float(
        low
    )

    c = safe_float(
        close
    )

    v = safe_int(
        volume
    )

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

    # --------------------------------------------------------
    # 尋找包含證券代號 / 收盤價的 table
    # --------------------------------------------------------

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

        field_text = " ".join(
            clean_text(x)
            for x in fields
        )

        if (
            "證券代號" not in field_text
            or "收盤價" not in field_text
        ):

            continue

        try:

            code_index = fields.index(
                "證券代號"
            )

        except ValueError:

            continue

        def index_of(
            names: List[str],
        ) -> Optional[int]:

            for name in names:

                if name in fields:

                    return fields.index(
                        name
                    )

            return None

        date_index = index_of(
            [
                "日期",
                "資料日期",
            ]
        )

        volume_index = index_of(
            [
                "成交股數",
                "成交量",
            ]
        )

        open_index = index_of(
            [
                "開盤價",
            ]
        )

        high_index = index_of(
            [
                "最高價",
            ]
        )

        low_index = index_of(
            [
                "最低價",
            ]
        )

        close_index = index_of(
            [
                "收盤價",
            ]
        )

        if (
            open_index is None
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

            if code_index >= len(row):

                continue

            code = extract_code(
                row[code_index]
            )

            if not code:
                continue

            row_date = (
                row[date_index]
                if (
                    date_index is not None
                    and date_index < len(row)
                )
                else requested_date
            )

            normalized = normalize_price_row(
                code=code,
                date_value=row_date,
                open_value=row[open_index],
                high=row[high_index],
                low=row[low_index],
                close=row[close_index],
                volume=(
                    row[volume_index]
                    if (
                        volume_index is not None
                        and volume_index < len(row)
                    )
                    else 0
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

    # --------------------------------------------------------
    # 官方回傳可能是前一交易日。
    # 必須驗證實際日期。
    # --------------------------------------------------------

    if rows:

        actual_dates = {
            row["date"]
            for row in rows.values()
        }

        if (
            len(actual_dates) != 1
            or target_date not in actual_dates
        ):

            # 如果不是指定日期，不接受。
            return {}

    return rows


# ============================================================
# TWSE CURRENT SNAPSHOT
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
# TPEX DAILY PARSER
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

    data = payload.get(
        "aaData"
    )

    if not isinstance(
        data,
        list,
    ):

        return result

    # --------------------------------------------------------
    # TPEx STK_WN1430:
    #
    # 日期
    # 成交股數
    # 成交金額
    # 開盤價
    # 最高價
    # 最低價
    # 收盤價
    # 漲跌
    # 成交筆數
    #
    # 不依賴固定欄位數量。
    # --------------------------------------------------------

    for row in data:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 7:
            continue

        # ----------------------------------------------------
        # 代號通常位於 row 中第一個看起來像合法 code
        # ----------------------------------------------------

        code = None
        code_index = None

        for i, value in enumerate(row):

            candidate = extract_code(
                value
            )

            if candidate:

                code = candidate
                code_index = i
                break

        if not code:
            continue

        # ----------------------------------------------------
        # 日期通常位於最前面
        # ----------------------------------------------------

        row_date = None

        for value in row[:3]:

            parsed = parse_date(
                value
            )

            if parsed:

                row_date = parsed
                break

        if row_date is None:

            row_date = requested_date

        # ----------------------------------------------------
        # 找價格區域
        #
        # 典型資料：
        # date,
        # volume,
        # value,
        # open,
        # high,
        # low,
        # close,
        # change,
        # transactions
        # ----------------------------------------------------

        numeric_values = []

        for i, value in enumerate(row):

            if i == code_index:
                continue

            number = safe_float(
                value
            )

            if number is not None:

                numeric_values.append(
                    (
                        i,
                        number,
                        value,
                    )
                )

        # ----------------------------------------------------
        # 優先按照官方 STK_WN1430 結構
        # 找 code 後的數字區域。
        # ----------------------------------------------------

        after = [
            item
            for item in numeric_values
            if item[0] > code_index
        ]

        if len(after) < 6:
            continue

        # ----------------------------------------------------
        # 尋找連續 OHLC 四個價格
        # 條件：
        #
        # high >= open / close
        # low <= open / close
        # high >= low
        # ----------------------------------------------------

        chosen = None

        for j in range(
            0,
            len(after) - 3,
        ):

            values = after[
                j:j + 4
            ]

            o = values[0][1]
            h = values[1][1]
            l = values[2][1]
            c = values[3][1]

            if (
                o > 0
                and h > 0
                and l > 0
                and c > 0
                and h >= max(o, c)
                and l <= min(o, c)
                and h >= l
            ):

                chosen = (
                    values
                )

                break

        if chosen is None:
            continue

        open_value = chosen[0][1]
        high = chosen[1][1]
        low = chosen[2][1]
        close = chosen[3][1]

        # ----------------------------------------------------
        # 成交股數：
        # 通常為 code 後第一個 numeric。
        # ----------------------------------------------------

        volume = 0

        if after:

            volume = safe_int(
                after[0][2]
            )

        normalized = normalize_price_row(
            code=code,
            date_value=row_date,
            open_value=open_value,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

        if normalized:

            result[code] = normalized

    # --------------------------------------------------------
    # 日期一致性驗證
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
# YAHOO DAILY
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

            rows.append(
                row
            )

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
                    + 20
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

        # 只送平日。
        if current.weekday() < 5:

            dates.append(
                current.isoformat()
            )

        current += timedelta(
            days=1
        )

    return dates


# ============================================================
# MARKET SNAPSHOT STRUCTURE
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
        # 同一 code 在 TW / TWO 不應該混淆。
        # ----------------------------------------------------

        for market in (
            "TW",
            "TWO",
        ):

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

            # 官方資料若存在，source 以官方為準。
            if source.startswith(
                "official"
            ):

                records[
                    symbol
                ]["source"] = source

            added += 1

            break

    return added


# ============================================================
# FETCH INITIAL HISTORY
# ============================================================

def fetch_initial_history(
    universe: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:

    section(
        "V8.2 全市場批次初始化"
    )

    log(
        f"目標歷史："
        f"最近 {INITIAL_HISTORY_DAYS} "
        f"個交易日"
    )

    log(
        "抓取方式："
        "TWSE / TPEx 每交易日全市場批次"
    )

    log(
        "不再逐檔抓取"
    )

    records = build_empty_market_history(
        universe
    )

    universe_by_code = {}

    for item in universe:

        universe_by_code[
            (
                item["market"],
                item["code"],
            )
        ] = item

    dates = candidate_dates(
        INITIAL_LOOKBACK_CALENDAR_DAYS
    )

    total_dates = len(
        dates
    )

    successful_market_dates = 0

    twse_success_dates = 0
    tpex_success_dates = 0

    for index, target_date in enumerate(
        dates,
        start=1,
    ):

        # ----------------------------------------------------
        # 如果所有商品都已取得 90 筆，
        # 可以立即停止。
        # ----------------------------------------------------

        complete = True

        for record in records.values():

            if len(
                record["prices"]
            ) < INITIAL_HISTORY_DAYS:

                complete = False
                break

        if complete:

            log("")
            log(
                "✓ 全市場已取得 "
                f"{INITIAL_HISTORY_DAYS} "
                "個交易日"
            )

            break

        log(
            f"[BATCH {index}/{total_dates}] "
            f"{target_date}"
        )

        day_added = 0

        # ====================================================
        # TWSE
        # ====================================================

        twse_rows = {}

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

                day_added += added

                log(
                    f"      ✓ TWSE："
                    f"{len(twse_rows)} "
                    f"檔市場資料，"
                    f"匹配 {added}"
                )

            else:

                log(
                    "      ↳ TWSE："
                    "指定日期無有效市場資料"
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

        tpex_rows = {}

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

                day_added += added

                log(
                    f"      ✓ TPEx："
                    f"{len(tpex_rows)} "
                    f"檔市場資料，"
                    f"匹配 {added}"
                )

            else:

                log(
                    "      ↳ TPEx："
                    "指定日期無有效市場資料"
                )

        except Exception as exc:

            log(
                f"      ⚠️ TPEx："
                f"{exc}"
            )

        if day_added > 0:

            successful_market_dates += 1

        time.sleep(
            REQUEST_DELAY
        )

    # ========================================================
    # 建立結果
    # ========================================================

    results = {}

    failures = {}

    for symbol, record in records.items():

        rows = list(
            record["prices"].values()
        )

        rows.sort(
            key=lambda x: x["date"]
        )

        # 只保留最近 90 筆。
        rows = rows[
            -MAX_HISTORY_ROWS:
        ]

        if len(rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            latest = rows[-1]

            status = (
                "complete"
                if len(rows)
                >= INITIAL_HISTORY_DAYS
                else "short_history"
            )

            results[symbol] = {
                "symbol": symbol,
                "code": record["code"],
                "market": record["market"],
                "type": record["type"],
                "name": record["name"],
                "source": record["source"],
                "history_rows": len(rows),
                "history_status": status,
                "latest_date": latest[
                    "date"
                ],
                "prices": rows,
            }

        else:

            failures[symbol] = (
                "官方市場批次資料不足："
                f"{len(rows)} 筆"
            )

    # ========================================================
    # 最後 fallback
    #
    # 只對官方市場批次資料不足的個股使用 Yahoo。
    # ========================================================

    fallback_candidates = [
        symbol
        for symbol in records.keys()
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

        for index, symbol in enumerate(
            fallback_candidates,
            start=1,
        ):

            item = next(
                (
                    x
                    for x in universe
                    if x["symbol"] == symbol
                ),
                None,
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

            # ------------------------------------------------
            # 只取最近 90 筆。
            # ------------------------------------------------

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
                    "latest_date": yahoo_rows[-1][
                        "date"
                    ],
                    "prices": yahoo_rows,
                }

                failures.pop(
                    symbol,
                    None,
                )

                log(
                    f"      ✓ Yahoo："
                    f"{len(yahoo_rows)} 筆"
                )

            else:

                failures[symbol] = (
                    "官方不足 + "
                    "Yahoo不足："
                    f"{len(yahoo_rows)}"
                )

            time.sleep(
                REQUEST_DELAY
            )

    return results


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

            log(
                "⚠️ manifest 格式錯誤，"
                "重新初始化"
            )

            return None

        files = manifest.get(
            "files"
        )

        if not isinstance(
            files,
            list,
        ):

            log(
                "⚠️ manifest.files 錯誤，"
                "重新初始化"
            )

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

            # ------------------------------------------------
            # V8.2 shard 必須是 dict。
            # 舊格式不是 dict → 安全視為舊資料。
            # ------------------------------------------------

            if not isinstance(
                stocks,
                dict,
            ):

                log(
                    f"⚠️ {filename} "
                    f"不是 V8.2 stocks dict，"
                    "重新初始化"
                )

                return None

            for symbol, rows in stocks.items():

                if not isinstance(
                    rows,
                    list,
                ):

                    log(
                        f"⚠️ {filename} "
                        f"{symbol} rows 錯誤，"
                        "重新初始化"
                    )

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
                            key=lambda x: x[
                                "date"
                            ],
                        )[
                            -MAX_HISTORY_ROWS:
                        ],
                    }

        # ----------------------------------------------------
        # 必須至少有一定數量資料，
        # 否則不做增量。
        # ----------------------------------------------------

        if not results:

            log(
                "既有價格沒有有效股票"
            )

            return None

        log(
            f"既有股票歷史："
            f"{len(results)} 檔"
        )

        return results

    except Exception as exc:

        # ----------------------------------------------------
        # 這裡特別重要：
        #
        # 舊版 Data/prices 結構錯誤不能阻塞 V8.2。
        # 直接重建。
        # ----------------------------------------------------

        log(
            f"⚠️ 舊版 prices 無法讀取："
            f"{exc}"
        )

        log(
            "↳ 不沿用舊格式，"
            "直接建立 V8.2 歷史資料"
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
    bool,
]:

    section(
        "V8.2 每日增量更新"
    )

    universe_map = {
        item["symbol"]: item
        for item in universe
    }

    # ========================================================
    # TWSE 最新快照
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
    # TPEx 最新日期
    #
    # 由最新市場資料決定實際交易日。
    # ========================================================

    today = date.today()

    current_date = today

    tpex_rows = {}

    # 最多往前找 7 個曆日。
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

    # ========================================================
    # 建立最新日期
    # ========================================================

    latest_dates = []

    for row in twse_rows.values():

        latest_dates.append(
            row["date"]
        )

    for row in tpex_rows.values():

        latest_dates.append(
            row["date"]
        )

    if not latest_dates:

        raise RuntimeError(
            "TWSE / TPEx 都無法取得最新市場資料"
        )

    latest_date = max(
        latest_dates
    )

    log(
        f"本次價格更新日："
        f"{latest_date}"
    )

    # ========================================================
    # 建立結果
    # ========================================================

    result = {}

    updated = False

    for item in universe:

        symbol = item["symbol"]

        previous = existing.get(
            symbol
        )

        if previous is None:

            # 新加入 Universe 的股票
            # 不能只塞一天，必須 fallback 到完整初始化。
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

        # ----------------------------------------------------
        # 官方資料優先
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Yahoo fallback
        # ----------------------------------------------------

        if official_row is None:

            yahoo_rows = fetch_yahoo_symbol(
                symbol
            )

            candidates = [
                row
                for row in yahoo_rows
                if row["date"]
                == latest_date
            ]

            if candidates:

                row_map[
                    latest_date
                ] = candidates[0]

                source = "Yahoo fallback"

                updated = True

        final_rows = sorted(
            row_map.values(),
            key=lambda x: x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        if len(final_rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

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
            "latest_date": final_rows[-1][
                "date"
            ],
            "prices": final_rows,
        }

        time.sleep(
            0.005
        )

    # --------------------------------------------------------
    # 如果 Universe 新增股票，
    # 不允許用不完整資料混進正式結果。
    # --------------------------------------------------------

    missing = [
        item["symbol"]
        for item in universe
        if item["symbol"]
        not in result
    ]

    if missing:

        log(
            f"⚠️ 增量更新後缺少："
            f"{len(missing)} 檔"
        )

        log(
            "↳ 重新執行全市場初始化"
        )

        return (
            {},
            False,
        )

    return (
        result,
        updated,
    )


# ============================================================
# RESULT VALIDATION
# ============================================================

def validate_results(
    results: Dict[str, Dict[str, Any]],
    universe: List[Dict[str, str]],
) -> None:

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set(
        results.keys()
    )

    missing = expected - actual

    if missing:

        raise RuntimeError(
            "正式價格資料缺少 "
            f"{len(missing)} 檔"
        )

    for symbol, record in results.items():

        rows = record.get(
            "prices"
        )

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
                f"{symbol} 歷史不足："
                f"{len(rows)}"
            )

        previous = ""

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} row 錯誤"
                )

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
                    f"{symbol} 存在異常 OHLCV"
                )

            if (
                previous
                and row["date"]
                <= previous
            ):

                raise RuntimeError(
                    f"{symbol} 日期未嚴格遞增"
                )

            previous = row["date"]

        source = record.get(
            "source",
            ""
        )

        if not source:

            raise RuntimeError(
                f"{symbol} 缺少 source"
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

            required = {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }

            if not required.issubset(
                row.keys()
            ):

                raise RuntimeError(
                    f"{symbol} 欄位不足"
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
) -> Dict[str, Any]:

    source_counts = {}

    complete_count = 0
    short_count = 0

    latest_dates = []

    type_counts = {}

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

        record_type = result.get(
            "type",
            ""
        )

        type_counts[record_type] = (
            type_counts.get(
                record_type,
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
        "universe_stock_count":
            universe_stock_count,
        "universe_etf_count":
            universe_etf_count,
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
        "sources":
            source_counts,
        "types":
            type_counts,
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

    # ========================================================
    # FIRST INITIALIZATION
    # ========================================================

    if existing is None:

        results = fetch_initial_history(
            universe
        )

    else:

        # ====================================================
        # DAILY INCREMENT
        # ====================================================

        results, updated = (
            update_existing_with_latest(
                existing,
                universe,
            )
        )

        # ----------------------------------------------------
        # 如果 Universe 有新增商品，
        # 或舊資料不足，重新執行市場批次初始化。
        # ----------------------------------------------------

        if not results:

            results = fetch_initial_history(
                universe
            )

    # ========================================================
    # RESULT
    # ========================================================

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

    section(
        "價格資料結果"
    )

    log(
        f"Universe TOTAL："
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
    # HARD SAFETY GATE
    # ========================================================

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

        log("")
        log(
            "❌ 價格資料成功率低於 "
            f"{MIN_SUCCESS_RATE:.0%}"
        )

        return 1

    # ========================================================
    # VALIDATE
    # ========================================================

    section(
        "全市場價格資料驗證"
    )

    try:

        validate_results(
            results,
            universe,
        )

        log(
            "✓ 所有 Universe 商品 "
            "均通過價格資料驗證"
        )

    except Exception as exc:

        log(
            f"❌ 價格資料驗證失敗："
            f"{exc}"
        )

        return 1

    # ========================================================
    # 7794
    # ========================================================

    if "7794.TWO" in {
        x["symbol"]
        for x in universe
    }:

        if "7794.TWO" not in results:

            log(
                "❌ 7794.TWO "
                "不存在於價格結果"
            )

            return 1

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

        if record["prices"]:

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

    # ========================================================
    # TEMP
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
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ fetch_prices.py V8.2 完成"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
