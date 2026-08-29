#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式版 V8.1
============================================================

核心目標
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. STOCK + ETF 全部進價格管線
3. 不修改 Universe
4. 不用成交行情建立 Universe
5. 不使用 CMoney
6. 不逐檔逐月抓歷史價格
7. 使用「單一交易日 / 全市場」批次抓取
8. 首次初始化只抓最近 90 個交易日
9. 後續只抓既有資料之後的新交易日
10. TWSE / TPEx 官方資料優先
11. 官方資料失敗時才允許 Yahoo fallback
12. Yahoo fallback 必須通過完整資料驗證
13. Yahoo fallback 不得偽裝成官方資料
14. 每筆 OHLCV 必須通過資料完整性驗證
15. 官方 / Yahoo 同日資料若可取得，進行交叉驗證
16. shard 驗證
17. manifest 驗證
18. atomic replace
19. 不會因為部分來源失敗而偷偷寫入錯誤資料
20. Universe 數量與實際價格資料必須可追蹤

============================================================
V8.1 與 V8.0 主要差異
============================================================

V8.0：

    2023-01-01
        ↓
    每個交易日
        ↓
    逐日批次

但初始化可能掃描約 955 個交易日。

V8.1：

    第一次初始化
        ↓
    找最新交易日
        ↓
    往前 90 個交易日
        ↓
    全市場批次抓取

後續：

    Data/prices
        ↓
    找每檔最後日期
        ↓
    只抓缺少的日期
        ↓
    官方優先
        ↓
    Yahoo 僅作 fallback
        ↓
    驗證
        ↓
    merge
        ↓
    atomic replace

============================================================
重要
============================================================

本程式的 prices 結構：

Data/prices/
    prices_001.json
    prices_002.json
    ...
    manifest.json

每個 shard：

{
    "stocks": {
        "2330.TW": [
            {
                "date": "2026-08-28",
                "open": 123,
                "high": 125,
                "low": 122,
                "close": 124,
                "volume": 12345678,
                "source": "TWSE official"
            }
        ]
    }
}

注意：

為了讓 build_ui_data.py 可以統一處理 STOCK / ETF，
ETF 也會進入 prices。

============================================================
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V8.1"
SCHEMA_VERSION = "prices-v8.1"


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

INITIAL_TRADING_DAYS = 90

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0
MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# SAFETY
# ============================================================

MIN_INITIAL_SUCCESS_RATE = 0.95
MIN_INCREMENTAL_SUCCESS_RATE = 0.95

ABSOLUTE_MIN_HISTORY_ROWS = 20

MAX_RETRIES = 3
REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.05
RETRY_DELAY = 1.5

MAX_WORKERS = 8


# ============================================================
# DATE
# ============================================================

TODAY = datetime.now(
    timezone.utc
).date()


# ============================================================
# OFFICIAL URL
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/MI_INDEX"
)

TPEX_DAILY_URL = (
    "https://www.tpex.org.tw/"
    "www/zh-tw/afterTrading/"
    "dailyQuotes"
)


# ============================================================
# YAHOO
# ============================================================

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


# ============================================================
# SESSION
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
        .replace("=", "")
        .replace("--", "")
        .replace("X", "")
        .strip()
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

    parts = text.split("/")

    if len(parts) == 3:

        try:

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])

            if year < 1911:
                year += 1911

            result = date(
                year,
                month,
                day,
            )

            return result.isoformat()

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


def date_to_yyyymmdd(
    value: date,
) -> str:

    return value.strftime(
        "%Y%m%d"
    )


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
# UNIVERSE NORMALIZATION
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
# UNIVERSE CONTAINER
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

        symbol = normalized["symbol"]

        if normalized["type"] == "STOCK":

            if symbol not in parsed_stocks:

                parsed_stocks[
                    symbol
                ] = normalized

        elif normalized["type"] == "ETF":

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

    if actual_stock_count != declared_stock_count:

        raise RuntimeError(
            "Universe STOCK 數量不一致："
            f"metadata={declared_stock_count}, "
            f"actual={actual_stock_count}"
        )

    if actual_etf_count != declared_etf_count:

        raise RuntimeError(
            "Universe ETF 數量不一致："
            f"metadata={declared_etf_count}, "
            f"actual={actual_etf_count}"
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

    all_items = []

    all_items.extend(
        parsed_stocks.values()
    )

    all_items.extend(
        parsed_etfs.values()
    )

    all_items.sort(
        key=lambda item: item["symbol"]
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

    return all_items


# ============================================================
# PRICE VALIDATION
# ============================================================

def validate_price_row(
    row: Dict[str, Any],
    expected_date: Optional[str] = None,
) -> Tuple[
    bool,
    str,
]:

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

        return (
            False,
            f"缺少欄位：{sorted(missing)}",
        )

    date_value = parse_date(
        row.get("date")
    )

    if date_value is None:

        return (
            False,
            "日期格式錯誤",
        )

    if expected_date:

        if date_value != expected_date:

            return (
                False,
                (
                    f"日期不一致："
                    f"{date_value} "
                    f"!= "
                    f"{expected_date}"
                ),
            )

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
        open_value is None
        or high is None
        or low is None
        or close is None
    ):

        return (
            False,
            "OHLC 有非數值",
        )

    if (
        open_value <= 0
        or high <= 0
        or low <= 0
        or close <= 0
    ):

        return (
            False,
            "OHLC <= 0",
        )

    if volume < 0:

        return (
            False,
            "成交量 < 0",
        )

    if high < max(
        open_value,
        close,
    ):

        return (
            False,
            "high < open/close",
        )

    if low > min(
        open_value,
        close,
    ):

        return (
            False,
            "low > open/close",
        )

    if high < low:

        return (
            False,
            "high < low",
        )

    return (
        True,
        "",
    )


# ============================================================
# HISTORY VALIDATION
# ============================================================

def validate_history(
    rows: List[Dict[str, Any]],
) -> Tuple[
    bool,
    str,
]:

    if not isinstance(
        rows,
        list,
    ):

        return (
            False,
            "prices 不是 list",
        )

    if len(rows) < ABSOLUTE_MIN_HISTORY_ROWS:

        return (
            False,
            (
                f"歷史資料不足："
                f"{len(rows)}"
            ),
        )

    seen = set()
    previous = ""

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):

            return (
                False,
                "price row 不是 object",
            )

        ok, reason = validate_price_row(
            row
        )

        if not ok:
            return False, reason

        date_value = row["date"]

        if date_value in seen:

            return (
                False,
                f"日期重複：{date_value}",
            )

        seen.add(date_value)

        if (
            previous
            and date_value <= previous
        ):

            return (
                False,
                "日期未嚴格遞增",
            )

        previous = date_value

    return (
        True,
        "",
    )


# ============================================================
# NETWORK JSON
# ============================================================

def request_json(
    url: str,
    params: Dict[str, Any],
    label: str,
) -> Any:

    last_error = ""

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

            text = response.text.strip()

            if not text:

                raise RuntimeError(
                    "HTTP 200 但 response body 為空"
                )

            try:

                return response.json()

            except Exception as exc:

                raise RuntimeError(
                    f"HTTP JSON failed: "
                    f"{exc}"
                )

        except Exception as exc:

            last_error = str(exc)

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"{label} failed: {last_error}"
    )


# ============================================================
# TWSE FIELD MAPPING
# ============================================================

def find_twse_price_table(
    payload: Dict[str, Any],
) -> Optional[
    Tuple[
        List[str],
        List[List[Any]]
    ]
]:

    tables = payload.get(
        "tables"
    )

    if isinstance(
        tables,
        list,
    ):

        for table in tables:

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
                clean_text(value)
                for value in fields
            ]

            if (
                "證券代號"
                in field_names
                and
                "開盤價"
                in field_names
                and
                "最高價"
                in field_names
                and
                "最低價"
                in field_names
                and
                "收盤價"
                in field_names
                and
                "成交股數"
                in field_names
            ):

                return (
                    field_names,
                    data,
                )

    fields = payload.get(
        "fields9"
    )

    data = payload.get(
        "data9"
    )

    if (
        isinstance(fields, list)
        and isinstance(data, list)
    ):

        field_names = [
            clean_text(value)
            for value in fields
        ]

        if (
            "證券代號"
            in field_names
            and
            "開盤價"
            in field_names
            and
            "最高價"
            in field_names
            and
            "最低價"
            in field_names
            and
            "收盤價"
            in field_names
            and
            "成交股數"
            in field_names
        ):

            return (
                field_names,
                data,
            )

    return None


# ============================================================
# TWSE DAY
# ============================================================

def fetch_twse_day(
    date_value: str,
) -> Dict[str, Dict[str, Any]]:

    params = {
        "response": "json",
        "date": date_value.replace(
            "-",
            "",
        ),
        "type": "ALL",
        "_": str(
            int(
                time.time()
                * 1000
            )
        ),
    }

    payload = request_json(
        TWSE_MI_INDEX_URL,
        params,
        f"TWSE {date_value}",
    )

    stat = clean_text(
        payload.get("stat")
    ).upper()

    if stat and stat not in {
        "OK",
        "NORMAL",
    }:

        return {}

    table = find_twse_price_table(
        payload
    )

    if table is None:

        return {}

    fields, data = table

    indexes = {
        field: index
        for index, field
        in enumerate(fields)
    }

    result = {}

    for raw_row in data:

        if not isinstance(
            raw_row,
            list,
        ):
            continue

        try:

            code = extract_code(
                raw_row[
                    indexes["證券代號"]
                ]
            )

            if code is None:
                continue

            open_value = safe_float(
                raw_row[
                    indexes["開盤價"]
                ]
            )

            high = safe_float(
                raw_row[
                    indexes["最高價"]
                ]
            )

            low = safe_float(
                raw_row[
                    indexes["最低價"]
                ]
            )

            close = safe_float(
                raw_row[
                    indexes["收盤價"]
                ]
            )

            volume = safe_int(
                raw_row[
                    indexes["成交股數"]
                ]
            )

        except Exception:

            continue

        row = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

        ok, reason = validate_price_row(
            row,
            expected_date=date_value,
        )

        if not ok:

            continue

        result[
            code
        ] = row

    return result


# ============================================================
# TPEX TABLE EXTRACTION
# ============================================================

def recursive_find_rows(
    obj: Any,
) -> Iterable[
    Tuple[
        Optional[List[str]],
        List[List[Any]]
    ]
]:

    if isinstance(
        obj,
        dict,
    ):

        fields = obj.get(
            "fields"
        )

        data = obj.get(
            "data"
        )

        if (
            isinstance(fields, list)
            and isinstance(data, list)
            and data
        ):

            clean_fields = [
                clean_text(value)
                for value in fields
            ]

            if (
                "證券代號"
                in clean_fields
                and
                "開盤"
                in clean_fields
            ):

                yield (
                    clean_fields,
                    data,
                )

        for value in obj.values():

            yield from recursive_find_rows(
                value
            )

    elif isinstance(
        obj,
        list,
    ):

        for value in obj:

            yield from recursive_find_rows(
                value
            )


# ============================================================
# TPEX DAY
# ============================================================

def fetch_tpex_day(
    date_value: str,
) -> Dict[str, Dict[str, Any]]:

    yyyymmdd = date_value.replace(
        "-",
        "",
    )

    params = {
        "l": "zh-tw",
        "d": yyyymmdd,
        "o": "json",
    }

    try:

        payload = request_json(
            TPEX_DAILY_URL,
            params,
            f"TPEx {date_value}",
        )

    except Exception:

        return {}

    result = {}

    # --------------------------------------------------------
    # 新版 JSON 結構
    # --------------------------------------------------------

    candidates = list(
        recursive_find_rows(
            payload
        )
    )

    for fields, data in candidates:

        if fields is None:
            continue

        field_map = {
            field: index
            for index, field
            in enumerate(fields)
        }

        code_field = None
        open_field = None
        high_field = None
        low_field = None
        close_field = None
        volume_field = None

        for field in field_map:

            if field == "證券代號":
                code_field = field

            if field in {
                "開盤",
                "開盤價",
            }:
                open_field = field

            if field in {
                "最高",
                "最高價",
            }:
                high_field = field

            if field in {
                "最低",
                "最低價",
            }:
                low_field = field

            if field in {
                "收盤",
                "收盤價",
            }:
                close_field = field

            if field in {
                "成交股數",
                "成交量",
            }:
                volume_field = field

        if not all(
            (
                code_field,
                open_field,
                high_field,
                low_field,
                close_field,
                volume_field,
            )
        ):
            continue

        for raw_row in data:

            if not isinstance(
                raw_row,
                list,
            ):
                continue

            try:

                code = extract_code(
                    raw_row[
                        field_map[
                            code_field
                        ]
                    ]
                )

                if code is None:
                    continue

                row = {
                    "date": date_value,
                    "open": safe_float(
                        raw_row[
                            field_map[
                                open_field
                            ]
                        ]
                    ),
                    "high": safe_float(
                        raw_row[
                            field_map[
                                high_field
                            ]
                        ]
                    ),
                    "low": safe_float(
                        raw_row[
                            field_map[
                                low_field
                            ]
                        ]
                    ),
                    "close": safe_float(
                        raw_row[
                            field_map[
                                close_field
                            ]
                        ]
                    ),
                    "volume": safe_int(
                        raw_row[
                            field_map[
                                volume_field
                            ]
                        ]
                    ),
                }

            except Exception:

                continue

            ok, _ = validate_price_row(
                row,
                expected_date=date_value,
            )

            if ok:

                result[
                    code
                ] = row

    return result


# ============================================================
# MARKET DAY FETCH
# ============================================================

def fetch_market_day(
    date_value: str,
) -> Dict[str, Dict[str, Dict[str, Any]]]:

    result = {
        "TW": {},
        "TWO": {},
    }

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    try:

        result["TW"] = fetch_twse_day(
            date_value
        )

    except Exception as exc:

        log(
            f"      ⚠️ TWSE "
            f"{date_value}："
            f"{exc}"
        )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    try:

        result["TWO"] = fetch_tpex_day(
            date_value
        )

    except Exception as exc:

        log(
            f"      ⚠️ TPEx "
            f"{date_value}："
            f"{exc}"
        )

    return result


# ============================================================
# TRADING DAY GENERATION
# ============================================================

def candidate_dates(
    start_date: date,
    end_date: date,
) -> List[str]:

    result = []

    current = start_date

    while current <= end_date:

        # ----------------------------------------------------
        # 台股週六週日不交易
        # ----------------------------------------------------

        if current.weekday() < 5:

            result.append(
                current.isoformat()
            )

        current += timedelta(
            days=1
        )

    return result


# ============================================================
# FIND LATEST AVAILABLE OFFICIAL DAY
# ============================================================

def find_latest_market_days(
    max_days: int = 15,
) -> List[str]:

    result = []

    current = TODAY

    checked = 0

    while (
        checked < max_days
        and len(result) < 1
    ):

        if current.weekday() < 5:

            date_value = current.isoformat()

            log(
                f"檢查最新交易日："
                f"{date_value}"
            )

            market = fetch_market_day(
                date_value
            )

            if (
                market["TW"]
                or market["TWO"]
            ):

                result.append(
                    date_value
                )

                return result

        current -= timedelta(
            days=1
        )

        checked += 1

    return result


# ============================================================
# LOAD EXISTING PRICES
# ============================================================

def load_existing_prices(
    universe: List[
        Dict[str, str]
    ],
) -> Dict[
    str,
    Dict[str, Any]
]:

    section(
        "檢查既有 Data/prices"
    )

    if not OUTPUT_DIR.exists():

        log(
            "Data/prices 不存在"
        )

        return {}

    files = sorted(
        OUTPUT_DIR.glob(
            "prices_*.json"
        )
    )

    if not files:

        log(
            "沒有既有 shard"
        )

        return {}

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    results = {}

    for path in files:

        try:

            data = load_json(
                path
            )

        except Exception as exc:

            raise RuntimeError(
                f"讀取 {path.name} 失敗："
                f"{exc}"
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

        for symbol, rows in stocks.items():

            if symbol not in expected_symbols:
                continue

            ok, reason = validate_history(
                rows
            )

            if not ok:

                raise RuntimeError(
                    f"{symbol} "
                    f"既有歷史資料驗證失敗："
                    f"{reason}"
                )

            results[symbol] = {
                "symbol": symbol,
                "code": extract_code(
                    symbol
                ),
                "market": (
                    "TWO"
                    if symbol.endswith(".TWO")
                    else "TW"
                ),
                "name": "",
                "type": "UNKNOWN",
                "source": "existing",
                "history_rows": len(rows),
                "history_status": (
                    "complete"
                    if len(rows) >= 60
                    else "short_history"
                ),
                "latest_date": rows[-1]["date"],
                "prices": rows,
            }

    log(
        f"既有股票歷史："
        f"{len(results)} 檔"
    )

    return results


# ============================================================
# APPLY UNIVERSE METADATA
# ============================================================

def attach_metadata(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> None:

    metadata = {
        item["symbol"]: item
        for item in universe
    }

    for symbol, result in results.items():

        item = metadata.get(
            symbol
        )

        if item is None:
            continue

        result["code"] = item["code"]
        result["market"] = item["market"]
        result["type"] = item["type"]
        result["name"] = item["name"]


# ============================================================
# MERGE DAY
# ============================================================

def merge_day_data(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    day_data: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ],
    universe_by_symbol: Dict[
        str,
        Dict[str, str]
    ],
    date_value: str,
) -> Tuple[
    int,
    int,
]:

    success = 0
    missing = 0

    for symbol, item in universe_by_symbol.items():

        market = item["market"]
        code = item["code"]

        row = day_data.get(
            market,
            {}
        ).get(
            code
        )

        if row is None:

            missing += 1

            continue

        ok, reason = validate_price_row(
            row,
            expected_date=date_value,
        )

        if not ok:

            log(
                f"      ⚠️ {symbol} "
                f"資料驗證失敗："
                f"{reason}"
            )

            missing += 1

            continue

        if symbol not in results:

            results[symbol] = {
                "symbol": symbol,
                "code": item["code"],
                "market": item["market"],
                "name": item["name"],
                "type": item["type"],
                "source": (
                    "TWSE official"
                    if market == "TW"
                    else "TPEx official"
                ),
                "history_rows": 0,
                "history_status": "short_history",
                "latest_date": date_value,
                "prices": [],
            }

        rows = results[
            symbol
        ]["prices"]

        replaced = False

        for index, existing in enumerate(
            rows
        ):

            if existing.get(
                "date"
            ) == date_value:

                rows[index] = {
                    **row,
                    "source": (
                        "TWSE official"
                        if market == "TW"
                        else "TPEx official"
                    ),
                }

                replaced = True

                break

        if not replaced:

            rows.append(
                {
                    **row,
                    "source": (
                        "TWSE official"
                        if market == "TW"
                        else "TPEx official"
                    ),
                }
            )

        rows.sort(
            key=lambda x: x["date"]
        )

        results[
            symbol
        ]["latest_date"] = rows[-1][
            "date"
        ]

        results[
            symbol
        ]["history_rows"] = len(rows)

        results[
            symbol
        ]["history_status"] = (
            "complete"
            if len(rows) >= 60
            else "short_history"
        )

        results[
            symbol
        ]["source"] = (
            "TWSE official"
            if market == "TW"
            else "TPEx official"
        )

        success += 1

    return success, missing


# ============================================================
# YAHOO SINGLE DAY
# ============================================================

def fetch_yahoo_day(
    symbol: str,
    date_value: str,
) -> Optional[
    Dict[str, Any]
]:

    start = date.fromisoformat(
        date_value
    )

    end = start + timedelta(
        days=2
    )

    params = {
        "period1": date_to_timestamp(
            start.isoformat()
        ),
        "period2": date_to_timestamp(
            end.isoformat()
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
            params,
            f"Yahoo {symbol} {date_value}",
        )

    except Exception:

        return None

    chart = payload.get(
        "chart",
        {}
    )

    if not isinstance(
        chart,
        dict,
    ):
        return None

    result = chart.get(
        "result"
    )

    if not isinstance(
        result,
        list,
    ) or not result:

        return None

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

    if (
        not timestamps
        or not quote_list
    ):
        return None

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

    for index, timestamp in enumerate(
        timestamps
    ):

        try:

            dt = datetime.fromtimestamp(
                int(timestamp),
                tz=timezone.utc,
            )

            current = dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:

            continue

        if current != date_value:
            continue

        row = {
            "date": current,
            "open": (
                safe_float(
                    opens[index]
                )
                if index < len(opens)
                else None
            ),
            "high": (
                safe_float(
                    highs[index]
                )
                if index < len(highs)
                else None
            ),
            "low": (
                safe_float(
                    lows[index]
                )
                if index < len(lows)
                else None
            ),
            "close": (
                safe_float(
                    closes[index]
                )
                if index < len(closes)
                else None
            ),
            "volume": (
                safe_int(
                    volumes[index]
                )
                if index < len(volumes)
                else 0
            ),
        }

        ok, _ = validate_price_row(
            row,
            expected_date=date_value,
        )

        if ok:

            return row

    return None


# ============================================================
# YAHOO FALLBACK VALIDATION
# ============================================================

def validate_fallback_against_official(
    yahoo_row: Dict[str, Any],
    official_row: Optional[
        Dict[str, Any]
    ],
) -> Tuple[
    bool,
    str,
]:

    if official_row is None:

        return (
            True,
            "official_missing",
        )

    # --------------------------------------------------------
    # 如果官方資料存在，Yahoo 不應該在正常流程覆蓋官方
    # --------------------------------------------------------

    for field in (
        "open",
        "high",
        "low",
        "close",
    ):

        official = safe_float(
            official_row.get(field)
        )

        yahoo = safe_float(
            yahoo_row.get(field)
        )

        if (
            official is None
            or yahoo is None
        ):

            return (
                False,
                f"{field} 無法比較",
            )

        # ----------------------------------------------------
        # 允許極小四捨五入差異
        # ----------------------------------------------------

        tolerance = max(
            0.01,
            abs(official) * 0.0025,
        )

        if abs(
            official - yahoo
        ) > tolerance:

            return (
                False,
                (
                    f"{field} 差異過大："
                    f"official={official}, "
                    f"yahoo={yahoo}"
                ),
            )

    return (
        True,
        "cross_validated",
    )


# ============================================================
# YAHOO FALLBACK FOR MISSING
# ============================================================

def fill_missing_with_yahoo(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_by_symbol: Dict[
        str,
        Dict[str, str]
    ],
    failed_dates: List[str],
) -> int:

    if not failed_dates:

        return 0

    section(
        "Yahoo fallback"
    )

    fallback_count = 0

    for date_value in failed_dates:

        log(
            f"Fallback 日期："
            f"{date_value}"
        )

        missing_symbols = []

        for symbol, item in universe_by_symbol.items():

            rows = (
                results.get(
                    symbol,
                    {}
                ).get(
                    "prices",
                    []
                )
            )

            existing = next(
                (
                    row
                    for row in rows
                    if row.get(
                        "date"
                    ) == date_value
                ),
                None,
            )

            if existing is None:

                missing_symbols.append(
                    symbol
                )

        if not missing_symbols:

            continue

        # ----------------------------------------------------
        # Yahoo fallback 本身可以並行
        # ----------------------------------------------------

        def worker(
            symbol: str,
        ) -> Tuple[
            str,
            Optional[Dict[str, Any]]
        ]:

            return (
                symbol,
                fetch_yahoo_day(
                    symbol,
                    date_value,
                ),
            )

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            futures = [
                executor.submit(
                    worker,
                    symbol,
                )
                for symbol
                in missing_symbols
            ]

            for future in as_completed(
                futures
            ):

                symbol, row = (
                    future.result()
                )

                if row is None:
                    continue

                item = universe_by_symbol[
                    symbol
                ]

                if symbol not in results:

                    results[symbol] = {
                        "symbol": symbol,
                        "code": item["code"],
                        "market": item["market"],
                        "name": item["name"],
                        "type": item["type"],
                        "source": "Yahoo fallback",
                        "history_rows": 0,
                        "history_status": "short_history",
                        "latest_date": date_value,
                        "prices": [],
                    }

                rows = results[
                    symbol
                ]["prices"]

                rows.append(
                    {
                        **row,
                        "source": "Yahoo fallback",
                    }
                )

                rows.sort(
                    key=lambda x: x["date"]
                )

                results[
                    symbol
                ]["source"] = "Yahoo fallback"

                results[
                    symbol
                ]["latest_date"] = rows[-1][
                    "date"
                ]

                results[
                    symbol
                ]["history_rows"] = len(rows)

                results[
                    symbol
                ]["history_status"] = (
                    "complete"
                    if len(rows) >= 60
                    else "short_history"
                )

                fallback_count += 1

    return fallback_count


# ============================================================
# TRIM HISTORY
# ============================================================

def trim_to_recent_days(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    max_days: int,
) -> None:

    for result in results.values():

        rows = result.get(
            "prices",
            []
        )

        rows.sort(
            key=lambda x: x["date"]
        )

        if len(rows) > max_days:

            result["prices"] = rows[
                -max_days:
            ]

        result["history_rows"] = len(
            result["prices"]
        )

        if result[
            "prices"
        ]:

            result["latest_date"] = (
                result["prices"][-1]["date"]
            )

        result["history_status"] = (
            "complete"
            if len(
                result["prices"]
            ) >= 60
            else "short_history"
        )


# ============================================================
# DETERMINE INITIALIZATION
# ============================================================

def is_initialized(
    existing: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> bool:

    if not existing:
        return False

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set(
        existing.keys()
    )

    if expected != actual:

        return False

    for symbol in expected:

        rows = existing[
            symbol
        ].get(
            "prices",
            []
        )

        if len(rows) < INITIAL_TRADING_DAYS:

            return False

    return True


# ============================================================
# INITIALIZATION
# ============================================================

def initialize_history(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    int,
]:

    section(
        "V8.1 全市場 90 交易日初始化"
    )

    latest_days = find_latest_market_days()

    if not latest_days:

        raise RuntimeError(
            "找不到最新官方交易日"
        )

    latest_date = date.fromisoformat(
        latest_days[0]
    )

    # --------------------------------------------------------
    # 往前找足夠多的工作日。
    #
    # 不是所有平日都是交易日，因此先取約 150 個工作日，
    # 再以實際有市場資料的日期篩選。
    # --------------------------------------------------------

    candidate_start = (
        latest_date
        - timedelta(
            days=150
        )
    )

    candidates = candidate_dates(
        candidate_start,
        latest_date,
    )

    log(
        f"最新交易日："
        f"{latest_date.isoformat()}"
    )

    log(
        f"開始尋找最近 "
        f"{INITIAL_TRADING_DAYS} "
        f"個實際交易日"
    )

    universe_by_symbol = {
        item["symbol"]: item
        for item in universe
    }

    valid_dates = []

    market_cache = {}

    # --------------------------------------------------------
    # 從最新往前找。
    # --------------------------------------------------------

    for date_value in reversed(
        candidates
    ):

        if len(valid_dates) >= INITIAL_TRADING_DAYS:
            break

        market = fetch_market_day(
            date_value
        )

        has_market_data = (
            bool(
                market["TW"]
            )
            or bool(
                market["TWO"]
            )
        )

        if not has_market_data:
            continue

        valid_dates.append(
            date_value
        )

        market_cache[
            date_value
        ] = market

        log(
            f"  ✓ {date_value}"
            f"  "
            f"TW={len(market['TW'])}"
            f" "
            f"TWO={len(market['TWO'])}"
            f" "
            f""
            f"{len(valid_dates)}/"
            f"{INITIAL_TRADING_DAYS}"
        )

    if len(valid_dates) < INITIAL_TRADING_DAYS:

        raise RuntimeError(
            "官方市場資料不足 90 個交易日："
            f"{len(valid_dates)}"
        )

    # --------------------------------------------------------
    # 重新按照日期由舊到新 merge
    # --------------------------------------------------------

    valid_dates.sort()

    for index, date_value in enumerate(
        valid_dates,
        start=1,
    ):

        market = market_cache[
            date_value
        ]

        success, missing = merge_day_data(
            results,
            market,
            universe_by_symbol,
            date_value,
        )

        log(
            f"[{index}/{len(valid_dates)}] "
            f"{date_value} "
            f"成功={success} "
            f"缺少={missing}"
        )

    # --------------------------------------------------------
    # 初始化不能只要求日期存在，
    # 每檔商品至少要有 20 筆才允許進正式資料。
    # --------------------------------------------------------

    invalid = []

    for symbol, result in results.items():

        rows = result.get(
            "prices",
            []
        )

        ok, reason = validate_history(
            rows
        )

        if not ok:

            invalid.append(
                (
                    symbol,
                    reason,
                )
            )

    if invalid:

        log(
            "⚠️ 初始化有商品官方資料不足："
            f"{len(invalid)}"
        )

        # ----------------------------------------------------
        # 僅對缺少的商品做 Yahoo fallback。
        # ----------------------------------------------------

        missing_dates = []

        for symbol, _ in invalid:

            rows = results.get(
                symbol,
                {}
            ).get(
                "prices",
                []
            )

            have = {
                row["date"]
                for row in rows
            }

            for d in valid_dates:

                if d not in have:

                    missing_dates.append(
                        d
                    )

        missing_dates = sorted(
            set(missing_dates)
        )

        fill_missing_with_yahoo(
            results,
            universe_by_symbol,
            missing_dates,
        )

    trim_to_recent_days(
        results,
        INITIAL_TRADING_DAYS,
    )

    return (
        results,
        len(valid_dates),
    )


# ============================================================
# INCREMENTAL UPDATE
# ============================================================

def incremental_update(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    int,
    int,
]:

    section(
        "V8.1 增量價格更新"
    )

    universe_by_symbol = {
        item["symbol"]: item
        for item in universe
    }

    latest_dates = []

    for symbol in universe_by_symbol:

        rows = (
            results.get(
                symbol,
                {}
            ).get(
                "prices",
                []
            )
        )

        if rows:

            latest_dates.append(
                rows[-1]["date"]
            )

    if not latest_dates:

        return (
            results,
            0,
            0,
        )

    global_latest = max(
        latest_dates
    )

    today_string = TODAY.isoformat()

    if global_latest >= today_string:

        log(
            "✓ 已經是最新日期，無需更新"
        )

        return (
            results,
            0,
            0,
        )

    start = (
        date.fromisoformat(
            global_latest
        )
        + timedelta(days=1)
    )

    end = TODAY

    dates = candidate_dates(
        start,
        end,
    )

    log(
        f"既有最新日期："
        f"{global_latest}"
    )

    log(
        f"增量日期範圍："
        f"{start.isoformat()} "
        f"→ "
        f"{end.isoformat()}"
    )

    if not dates:

        return (
            results,
            0,
            0,
        )

    missing_dates = []

    successful_days = 0

    failed_days = 0

    for index, date_value in enumerate(
        dates,
        start=1,
    ):

        log(
            f"[UPDATE {index}/{len(dates)}] "
            f"{date_value}"
        )

        market = fetch_market_day(
            date_value
        )

        tw_count = len(
            market["TW"]
        )

        two_count = len(
            market["TWO"]
        )

        if (
            tw_count == 0
            and two_count == 0
        ):

            log(
                "      ↳ 無官方市場資料"
            )

            continue

        success, missing = merge_day_data(
            results,
            market,
            universe_by_symbol,
            date_value,
        )

        expected_count = len(
            universe
        )

        rate = (
            success / expected_count
            if expected_count
            else 0
        )

        log(
            f"      TW={tw_count} "
            f"TWO={two_count} "
            f""
            f"成功={success}/"
            f"{expected_count} "
            f""
            f"({rate:.2%})"
        )

        if success == 0:

            failed_days += 1

            missing_dates.append(
                date_value
            )

        else:

            successful_days += 1

            if rate < MIN_INCREMENTAL_SUCCESS_RATE:

                log(
                    "      ⚠️ 官方資料不完整，"
                    "啟用 Yahoo fallback"
                )

                missing_dates.append(
                    date_value
                )

    # --------------------------------------------------------
    # Yahoo 只補真正缺少的商品日期
    # --------------------------------------------------------

    fallback_count = fill_missing_with_yahoo(
        results,
        universe_by_symbol,
        sorted(
            set(
                missing_dates
            )
        ),
    )

    trim_to_recent_days(
        results,
        INITIAL_TRADING_DAYS,
    )

    return (
        results,
        successful_days,
        fallback_count,
    )


# ============================================================
# FINAL RESULT VALIDATION
# ============================================================

def validate_final_results(
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
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

        log(
            f"❌ 最終缺少商品："
            f"{len(missing)}"
        )

        for symbol in sorted(
            missing
        )[:100]:

            log(
                f"  {symbol}"
            )

        raise RuntimeError(
            "價格結果缺少 Universe 商品"
        )

    extra = actual - expected

    if extra:

        raise RuntimeError(
            "價格結果存在 Universe 以外商品："
            f"{len(extra)}"
        )

    invalid = []

    for symbol in sorted(
        expected
    ):

        rows = results[
            symbol
        ].get(
            "prices",
            []
        )

        ok, reason = validate_history(
            rows
        )

        if not ok:

            invalid.append(
                (
                    symbol,
                    reason,
                )
            )

    if invalid:

        log(
            "❌ 最終歷史資料驗證失敗："
            f"{len(invalid)}"
        )

        for symbol, reason in invalid[:100]:

            log(
                f"  {symbol}: "
                f"{reason}"
            )

        raise RuntimeError(
            "最終價格資料驗證失敗"
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

            stocks[symbol] = (
                results[
                    symbol
                ]["prices"]
            )

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

    if path.stat().st_size > MAX_FILE_SIZE_BYTES:

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

    if set(
        stocks.keys()
    ) != set(
        expected_symbols
    ):

        raise RuntimeError(
            f"{path.name} "
            "商品集合不一致"
        )

    for symbol, rows in stocks.items():

        ok, reason = validate_history(
            rows
        )

        if not ok:

            raise RuntimeError(
                f"{symbol}："
                f"{reason}"
            )


# ============================================================
# SOURCE STATISTICS
# ============================================================

def source_statistics(
    results: Dict[
        str,
        Dict[str, Any]
    ],
) -> Dict[str, int]:

    result = {}

    for record in results.values():

        rows = record.get(
            "prices",
            []
        )

        for row in rows:

            source = row.get(
                "source",
                "unknown",
            )

            result[source] = (
                result.get(
                    source,
                    0,
                )
                + 1
            )

    return result


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe: List[
        Dict[str, str]
    ],
) -> Dict[str, Any]:

    complete_count = 0
    short_count = 0

    latest_dates = []

    stock_count = 0
    etf_count = 0

    for item in universe:

        if item["type"] == "STOCK":
            stock_count += 1

        elif item["type"] == "ETF":
            etf_count += 1

    for result in results.values():

        rows = result.get(
            "prices",
            []
        )

        if len(rows) >= 60:

            complete_count += 1

        else:

            short_count += 1

        if rows:

            latest_dates.append(
                rows[-1]["date"]
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "universe_total_count": len(
            universe
        ),

        "universe_stock_count": stock_count,

        "universe_etf_count": etf_count,

        "price_total_count": len(
            results
        ),

        "price_stock_count": sum(
            1
            for item in universe
            if item["type"] == "STOCK"
            and item["symbol"] in results
        ),

        "price_etf_count": sum(
            1
            for item in universe
            if item["type"] == "ETF"
            and item["symbol"] in results
        ),

        "complete_history_count": (
            complete_count
        ),

        "short_history_count": (
            short_count
        ),

        "failed_count": (
            len(universe)
            - len(results)
        ),

        "initial_trading_days": (
            INITIAL_TRADING_DAYS
        ),

        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),

        "sources": source_statistics(
            results
        ),

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
    universe: List[
        Dict[str, str]
    ],
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
        "universe_total_count"
    ) != len(universe):

        raise RuntimeError(
            "manifest universe_total_count 錯誤"
        )

    if manifest.get(
        "universe_stock_count"
    ) != sum(
        1
        for item in universe
        if item["type"] == "STOCK"
    ):

        raise RuntimeError(
            "manifest universe_stock_count 錯誤"
        )

    if manifest.get(
        "universe_etf_count"
    ) != sum(
        1
        for item in universe
        if item["type"] == "ETF"
    ):

        raise RuntimeError(
            "manifest universe_etf_count 錯誤"
        )

    if manifest.get(
        "price_total_count"
    ) != len(universe):

        raise RuntimeError(
            "manifest price_total_count 錯誤"
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
    universe: List[
        Dict[str, str]
    ],
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
        universe,
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
        universe,
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
# MAIN
# ============================================================

def main() -> int:

    started = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    # ========================================================
    # Universe
    # ========================================================

    universe = load_universe()

    universe_count = len(
        universe
    )

    universe_by_symbol = {
        item["symbol"]: item
        for item in universe
    }

    stock_count = sum(
        1
        for item in universe
        if item["type"] == "STOCK"
    )

    etf_count = sum(
        1
        for item in universe
        if item["type"] == "ETF"
    )

    log(
        f"Universe STOCK："
        f"{stock_count}"
    )

    log(
        f"Universe ETF："
        f"{etf_count}"
    )

    log(
        f"Universe TOTAL："
        f"{universe_count}"
    )

    # ========================================================
    # Existing
    # ========================================================

    existing = load_existing_prices(
        universe
    )

    initialized = is_initialized(
        existing,
        universe,
    )

    if initialized:

        log(
            "✓ 既有價格資料已具備初始化歷史"
        )

        results = existing

        attach_metadata(
            results,
            universe,
        )

        # ====================================================
        # Incremental
        # ====================================================

        (
            results,
            updated_days,
            fallback_count,
        ) = incremental_update(
            results,
            universe,
        )

        log(
            f"增量更新交易日："
            f"{updated_days}"
        )

        log(
            f"Yahoo fallback 筆數："
            f"{fallback_count}"
        )

    else:

        log(
            "⚠️ 尚未初始化"
        )

        log(
            f"第一次初始化只抓最近 "
            f"{INITIAL_TRADING_DAYS} "
            f"個交易日"
        )

        results = {}

        (
            results,
            initialized_days,
        ) = initialize_history(
            results,
            universe,
        )

        log(
            f"初始化交易日："
            f"{initialized_days}"
        )

    # ========================================================
    # Metadata
    # ========================================================

    attach_metadata(
        results,
        universe,
    )

    # ========================================================
    # Final
    # ========================================================

    section(
        "最終價格資料驗證"
    )

    validate_final_results(
        results,
        universe,
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

    if success_rate < (
        MIN_INITIAL_SUCCESS_RATE
    ):

        log(
            "❌ 價格成功率低於安全門檻："
            f"{MIN_INITIAL_SUCCESS_RATE:.0%}"
        )

        return 1

    # ========================================================
    # 7794
    # ========================================================

    if "7794.TWO" in results:

        record = results[
            "7794.TWO"
        ]

        log("")
        log(
            "================================================"
        )

        log(
            "✓ 7794.TWO 最終驗證"
        )

        log(
            f"資料筆數："
            f"{len(record['prices'])}"
        )

        log(
            f"最新日期："
            f"{record['latest_date']}"
        )

        log(
            f"商品類型："
            f"{record['type']}"
        )

        log(
            f"市場："
            f"{record['market']}"
        )

        log(
            "================================================"
        )

    # ========================================================
    # Temporary
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
            universe,
        )

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
        f"{stock_count}"
    )

    log(
        f"Universe ETF："
        f"{etf_count}"
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

    source_counts = source_statistics(
        results
    )

    log("")

    log(
        "資料來源統計："
    )

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"  {source}："
            f"{count}"
        )

    if "7794.TWO" in results:

        log(
            "✓ 7794.TWO："
            "已進入價格資料鏈"
        )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ fetch_prices.py V8.1 完成"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
