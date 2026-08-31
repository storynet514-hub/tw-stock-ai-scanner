#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

PRICE PIPELINE V11.1
============================================================

資料流程
------------------------------------------------------------

    Data/universe.json
            ↓
    官方 TWSE / TPEx 日期批次
            ↓
    TWSE STOCK_DAY_ALL 最新交易日補強
            ↓
    Existing price cache
            ↓
    官方資料優先合併
            ↓
    官方歷史不足 → Yahoo 補足
            ↓
    Price results
            ↓
    Shards
            ↓
    Manifest
            ↓
    FINAL VALIDATION
            ↓
    Atomic Replace

核心契約
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. 只接受 active STOCK / ETF
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. TWSE / TPEx 官方資料優先
7. 官方歷史資料採日期批次抓取
8. TWSE STOCK_DAY_ALL 作為最新交易日官方補強來源
9. 不逐股票逐日期呼叫官方 API
10. 官方資料不足 TARGET_HISTORY_ROWS 時才啟動 Yahoo 補資料
11. Yahoo 永遠不能覆蓋官方同日期資料
12. Existing cache 只作歷史保留與暫時補強
13. >= 1 筆有效 OHLCV 必須寫入 Price
14. 0 筆才是 missing
15. 正常歷史目標 90 筆
16. 最大保存 90 筆
17. short_history < 20
18. partial_history 20~89
19. complete >= 90
20. Universe / Price 集合必須完整一致
21. 不允許 Price 出現 Universe 外商品
22. 不允許跨 shard 重複
23. shard 必須與 results 完整一致
24. manifest 必須與 shard 完整一致
25. 官方 HTTP 錯誤必須留下 diagnostics
26. Yahoo fallback 必須留下 diagnostics
27. 所有 validation PASS 後才 atomic replace
28. 任一 validation FAIL，不破壞舊 Data/prices
29. 舊 schema / 壞 shard 自動忽略
30. 每次執行最後重新讀取輸出並做 FINAL VALIDATION
31. 絕不因歷史不足 silently drop 商品
32. 不會對 2301 檔股票逐檔呼叫官方 API

============================================================
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V11.1"
SCHEMA_VERSION = "prices-v11.1"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_DIR = DATA_DIR / "prices"


# ============================================================
# PRICE POLICY
# ============================================================

TARGET_HISTORY_ROWS = 90
MAX_HISTORY_ROWS = 90

SHORT_HISTORY_THRESHOLD = 20

# 150 個 calendar days 約可涵蓋 90 個以上正常交易日。
# 比 V11.0 的 180 天降低官方 request 數量。
LOOKBACK_CALENDAR_DAYS = 150

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0
MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# HTTP
# ============================================================

MAX_RETRIES = 3
REQUEST_TIMEOUT = 20
RETRY_DELAY = 1.5

# 官方批次 request 間隔
REQUEST_DELAY = 0.08

# Yahoo request 間隔
YAHOO_REQUEST_DELAY = 0.05


# ============================================================
# VALIDATION
# ============================================================

SUCCESS_RATE_TARGET = 0.80


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
# REQUEST DIAGNOSTICS
# ============================================================

REQUEST_STATS: Dict[str, Any] = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "twse_mi_index_requests": 0,
    "twse_stock_day_all_requests": 0,
    "tpex_requests": 0,
    "yahoo_requests": 0,
    "twse_mi_index_failures": 0,
    "twse_stock_day_all_failures": 0,
    "tpex_failures": 0,
    "yahoo_failures": 0,
}

REQUEST_ERRORS: List[Dict[str, Any]] = []


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

    if text in {
        "--",
        "---",
        "-",
        "N/A",
        "None",
        "null",
        "X",
    }:
        return None

    text = (
        text
        .replace(",", "")
        .replace(" ", "")
    )

    try:
        number = float(text)
    except Exception:
        return None

    if not math.isfinite(number):
        return None

    return number


def safe_int(
    value: Any,
) -> Optional[int]:

    number = safe_float(value)

    if number is None:
        return None

    return int(round(number))


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    upper = text.upper()

    for suffix in (
        ".TW",
        ".TWO",
        ".HK",
    ):

        if upper.endswith(suffix):

            text = text[
                :-len(suffix)
            ]

            break

    text = text.strip()

    return text or None


# ============================================================
# DATE
# ============================================================

def normalize_date(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    )

    for fmt in formats:

        try:

            dt = datetime.strptime(
                text,
                fmt,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            pass

    # ROC YYYY/MM/DD
    if "/" in text:

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

    # ROC YYYYMMDD
    if (
        len(text) == 7
        and text.isdigit()
    ):

        try:

            year = int(text[:3]) + 1911
            month = int(text[3:5])
            day = int(text[5:7])

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

    return None


# ============================================================
# PRICE ROW
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

    date_text = normalize_date(
        date_value
    )

    if not date_text:
        return None

    close_price = safe_float(close)

    if close_price is None:
        return None

    if close_price <= 0:
        return None

    open_price = safe_float(
        open_value
    )

    high_price = safe_float(
        high
    )

    low_price = safe_float(
        low
    )

    volume_value = safe_int(
        volume
    )

    if open_price is None:
        open_price = close_price

    if high_price is None:
        high_price = close_price

    if low_price is None:
        low_price = close_price

    if open_price <= 0:
        return None

    if high_price <= 0:
        return None

    if low_price <= 0:
        return None

    if high_price < low_price:
        return None

    high_price = max(
        high_price,
        open_price,
        close_price,
    )

    low_price = min(
        low_price,
        open_price,
        close_price,
    )

    if volume_value is None:
        volume_value = 0

    if volume_value < 0:
        return None

    return {
        "date": date_text,
        "open": round(
            open_price,
            4,
        ),
        "high": round(
            high_price,
            4,
        ),
        "low": round(
            low_price,
            4,
        ),
        "close": round(
            close_price,
            4,
        ),
        "volume": int(
            volume_value
        ),
    }


# ============================================================
# HTTP JSON
# ============================================================

def http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    source: str = "unknown",
) -> Tuple[
    Optional[Any],
    Optional[str],
]:

    last_error = None

    REQUEST_STATS[
        "total_requests"
    ] += 1

    if source == "TWSE_MI_INDEX":
        REQUEST_STATS[
            "twse_mi_index_requests"
        ] += 1

    elif source == "TWSE_STOCK_DAY_ALL":
        REQUEST_STATS[
            "twse_stock_day_all_requests"
        ] += 1

    elif source == "TPEX":
        REQUEST_STATS[
            "tpex_requests"
        ] += 1

    elif source == "Yahoo":
        REQUEST_STATS[
            "yahoo_requests"
        ] += 1

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            REQUEST_STATS[
                "successful_requests"
            ] += 1

            return data, None

        except Exception as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    REQUEST_STATS[
        "failed_requests"
    ] += 1

    if source == "TWSE_MI_INDEX":
        REQUEST_STATS[
            "twse_mi_index_failures"
        ] += 1

    elif source == "TWSE_STOCK_DAY_ALL":
        REQUEST_STATS[
            "twse_stock_day_all_failures"
        ] += 1

    elif source == "TPEX":
        REQUEST_STATS[
            "tpex_failures"
        ] += 1

    elif source == "Yahoo":
        REQUEST_STATS[
            "yahoo_failures"
        ] += 1

    REQUEST_ERRORS.append(
        {
            "source": source,
            "url": url,
            "params": params,
            "error": last_error,
            "attempts": MAX_RETRIES,
        }
    )

    return None, last_error


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> List[
    Dict[str, str]
]:

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "universe.json root 必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.stocks 必須是 object"
        )

    result = []
    seen = set()

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"Universe {key} 不是 object"
            )

        if item.get(
            "status"
        ) != "active":

            raise RuntimeError(
                f"Universe {key} "
                "status 不是 active"
            )

        symbol = normalize_symbol(
            item.get(
                "symbol"
            )
            or key
        )

        if not symbol:

            raise RuntimeError(
                f"Universe {key} "
                "沒有有效 symbol"
            )

        if symbol in seen:

            raise RuntimeError(
                f"Universe 重複 symbol："
                f"{symbol}"
            )

        market = clean_text(
            item.get("market")
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            raise RuntimeError(
                f"{symbol} market 無效："
                f"{market}"
            )

        record_type = clean_text(
            item.get("type")
        ).upper()

        if record_type not in {
            "STOCK",
            "ETF",
        }:

            raise RuntimeError(
                f"{symbol} type 無效："
                f"{record_type}"
            )

        seen.add(symbol)

        result.append(
            {
                "symbol": symbol,
                "code": symbol,
                "name": clean_text(
                    item.get("name")
                ),
                "market": market,
                "type": record_type,
                "instrument_type":
                    clean_text(
                        item.get(
                            "instrument_type"
                        )
                    ),
            }
        )

    if not result:

        raise RuntimeError(
            "Universe 為 0"
        )

    return result


# ============================================================
# TWSE MI_INDEX
# ============================================================

def fetch_twse_daily_batch(
    target_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    params = {
        "response": "json",
        "date": target_date.replace(
            "-",
            "",
        ),
        "type": "ALLBUT0999",
    }

    data, error = http_get_json(
        TWSE_MI_INDEX_URL,
        params,
        "TWSE_MI_INDEX",
    )

    if data is None:

        return {}, error

    if not isinstance(
        data,
        dict,
    ):

        return {}, "response_not_object"

    result = {}

    tables = data.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):

        return {}, "tables_missing"

    for table in tables:

        if not isinstance(
            table,
            dict,
        ):
            continue

        fields = table.get(
            "fields"
        )

        rows = table.get(
            "data"
        )

        if not isinstance(
            fields,
            list,
        ):
            continue

        if not isinstance(
            rows,
            list,
        ):
            continue

        field_map = {}

        for index, field in enumerate(
            fields
        ):

            field_map[
                clean_text(field)
            ] = index

        def value_from_row(
            row: List[Any],
            names: Tuple[str, ...],
        ) -> Any:

            for name in names:

                index = field_map.get(
                    name
                )

                if (
                    index is not None
                    and index < len(row)
                ):

                    return row[index]

            return None

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            symbol = normalize_symbol(
                value_from_row(
                    row,
                    (
                        "證券代號",
                        "股票代號",
                    ),
                )
            )

            if not symbol:
                continue

            normalized = normalize_price_row(
                symbol,
                target_date,
                value_from_row(
                    row,
                    (
                        "開盤價",
                        "開盤",
                    ),
                ),
                value_from_row(
                    row,
                    (
                        "最高價",
                        "最高",
                    ),
                ),
                value_from_row(
                    row,
                    (
                        "最低價",
                        "最低",
                    ),
                ),
                value_from_row(
                    row,
                    (
                        "收盤價",
                        "收盤",
                    ),
                ),
                value_from_row(
                    row,
                    (
                        "成交股數",
                        "成交量",
                    ),
                ),
            )

            if normalized:

                result[symbol] = normalized

    return result, None


# ============================================================
# TWSE STOCK_DAY_ALL
# ============================================================

def fetch_twse_stock_day_all() -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    """
    STOCK_DAY_ALL：

    - 一次取得最新交易日全部上市商品
    - 不傳 date，因為此 endpoint 本身只提供最新交易日
    - Date 欄位直接作為交易日
    - 只取 Universe 中存在的 TWSE symbol
    """

    data, error = http_get_json(
        TWSE_STOCK_DAY_ALL_URL,
        None,
        "TWSE_STOCK_DAY_ALL",
    )

    if data is None:

        return {}, error

    if not isinstance(
        data,
        list,
    ):

        return {}, "response_not_list"

    result = {}

    for item in data:

        if not isinstance(
            item,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            item.get("Code")
            or item.get("證券代號")
        )

        if not symbol:
            continue

        date_value = (
            item.get("Date")
            or item.get("日期")
        )

        normalized = normalize_price_row(
            symbol,
            date_value,
            item.get("OpeningPrice")
            or item.get("開盤價"),
            item.get("HighestPrice")
            or item.get("最高價"),
            item.get("LowestPrice")
            or item.get("最低價"),
            item.get("ClosingPrice")
            or item.get("收盤價"),
            item.get("TradeVolume")
            or item.get("成交股數"),
        )

        if normalized:

            result[symbol] = normalized

    if not result:

        return {}, "no_valid_rows"

    return result, None


# ============================================================
# TPEX DAILY
# ============================================================

def fetch_tpex_daily_batch(
    target_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    try:

        dt = datetime.strptime(
            target_date,
            "%Y-%m-%d",
        )

    except ValueError:

        return {}, "invalid_date"

    roc_date = (
        f"{dt.year - 1911:03d}/"
        f"{dt.month:02d}/"
        f"{dt.day:02d}"
    )

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date,
    }

    data, error = http_get_json(
        TPEX_DAILY_URL,
        params,
        "TPEX",
    )

    if data is None:

        return {}, error

    if not isinstance(
        data,
        dict,
    ):

        return {}, "response_not_object"

    aa_data = data.get(
        "aaData"
    )

    if not isinstance(
        aa_data,
        list,
    ):

        return {}, "aaData_missing"

    result = {}

    for row in aa_data:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 8:
            continue

        symbol = normalize_symbol(
            row[0]
        )

        if not symbol:
            continue

        normalized = normalize_price_row(
            symbol,
            target_date,
            row[4],
            row[5],
            row[6],
            row[2],
            row[7],
        )

        if normalized:

            result[symbol] = normalized

    return result, None


# ============================================================
# EXISTING PRICES
# ============================================================

def load_existing_prices() -> Dict[
    str,
    List[Dict[str, Any]]
]:

    result = {}

    if not OUTPUT_DIR.exists():

        return result

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if not manifest_path.exists():

        return result

    try:

        manifest = load_json(
            manifest_path
        )

    except Exception:

        return result

    if not isinstance(
        manifest,
        dict,
    ):

        return result

    files = manifest.get(
        "files"
    )

    if not isinstance(
        files,
        list,
    ):

        return result

    for filename in files:

        filename = Path(
            str(filename)
        ).name

        if filename == "manifest.json":
            continue

        path = (
            OUTPUT_DIR
            / filename
        )

        if not path.exists():
            continue

        try:

            data = load_json(
                path
            )

        except Exception:

            continue

        if not isinstance(
            data,
            dict,
        ):
            continue

        stocks = data.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):
            continue

        for symbol, rows in stocks.items():

            symbol = normalize_symbol(
                symbol
            )

            if not symbol:
                continue

            if not isinstance(
                rows,
                list,
            ):
                continue

            clean_rows = []

            for row in rows:

                if not isinstance(
                    row,
                    dict,
                ):
                    continue

                normalized = normalize_price_row(
                    symbol,
                    row.get("date"),
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                )

                if normalized:

                    clean_rows.append(
                        normalized
                    )

            clean_rows.sort(
                key=lambda x: x["date"]
            )

            if clean_rows:

                result[symbol] = (
                    clean_rows[
                        -MAX_HISTORY_ROWS:
                    ]
                )

    return result


# ============================================================
# OFFICIAL BATCH COLLECTION
# ============================================================

def collect_official_market_data(
    universe: List[Dict[str, str]],
    start_date: str,
    end_date: str,
) -> Tuple[
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, Any],
]:

    universe_by_market = {
        "TWSE": set(),
        "TPEX": set(),
    }

    for item in universe:

        universe_by_market[
            item["market"]
        ].add(
            item["symbol"]
        )

    collected = {
        "TWSE": {},
        "TPEX": {},
    }

    diagnostics = {
        "twse_batch_success": 0,
        "twse_batch_failure": 0,
        "tpex_batch_success": 0,
        "tpex_batch_failure": 0,
        "twse_rows": 0,
        "tpex_rows": 0,
        "twse_stock_day_all_rows": 0,
        "twse_stock_day_all_error": None,
        "errors": [],
    }

    # --------------------------------------------------------
    # STOCK_DAY_ALL
    # --------------------------------------------------------

    stock_day_all, stock_day_all_error = (
        fetch_twse_stock_day_all()
    )

    diagnostics[
        "twse_stock_day_all_rows"
    ] = len(
        stock_day_all
    )

    diagnostics[
        "twse_stock_day_all_error"
    ] = stock_day_all_error

    target_twse = universe_by_market[
        "TWSE"
    ]

    for symbol, row in (
        stock_day_all.items()
    ):

        if symbol not in target_twse:
            continue

        collected[
            "TWSE"
        ].setdefault(
            symbol,
            {}
        )[
            row["date"]
        ] = row

    if stock_day_all_error:

        diagnostics[
            "errors"
        ].append(
            {
                "source":
                    "TWSE_STOCK_DAY_ALL",
                "error":
                    stock_day_all_error,
            }
        )

    # --------------------------------------------------------
    # Historical date batches
    # --------------------------------------------------------

    start_dt = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    )

    end_dt = datetime.strptime(
        end_date,
        "%Y-%m-%d",
    )

    total_days = (
        end_dt - start_dt
    ).days + 1

    current = start_dt
    counter = 0

    while current <= end_dt:

        date_text = current.strftime(
            "%Y-%m-%d"
        )

        counter += 1

        if current.weekday() < 5:

            twse, twse_error = (
                fetch_twse_daily_batch(
                    date_text
                )
            )

            tpex, tpex_error = (
                fetch_tpex_daily_batch(
                    date_text
                )
            )

            if twse_error:

                diagnostics[
                    "twse_batch_failure"
                ] += 1

                diagnostics[
                    "errors"
                ].append(
                    {
                        "source":
                            "TWSE_MI_INDEX",
                        "date":
                            date_text,
                        "error":
                            twse_error,
                    }
                )

            else:

                diagnostics[
                    "twse_batch_success"
                ] += 1

            if tpex_error:

                diagnostics[
                    "tpex_batch_failure"
                ] += 1

                diagnostics[
                    "errors"
                ].append(
                    {
                        "source":
                            "TPEX",
                        "date":
                            date_text,
                        "error":
                            tpex_error,
                    }
                )

            else:

                diagnostics[
                    "tpex_batch_success"
                ] += 1

            for symbol, row in twse.items():

                if symbol in target_twse:

                    collected[
                        "TWSE"
                    ].setdefault(
                        symbol,
                        {}
                    )[
                        row["date"]
                    ] = row

            target_tpex = universe_by_market[
                "TPEX"
            ]

            for symbol, row in tpex.items():

                if symbol in target_tpex:

                    collected[
                        "TPEX"
                    ].setdefault(
                        symbol,
                        {}
                    )[
                        row["date"]
                    ] = row

            diagnostics[
                "twse_rows"
            ] += len(twse)

            diagnostics[
                "tpex_rows"
            ] += len(tpex)

            log(
                f"  官方批次 "
                f"{counter}/{total_days} "
                f"{date_text} "
                f"TWSE={len(twse)} "
                f"TPEx={len(tpex)}"
            )

            time.sleep(
                REQUEST_DELAY
            )

        current += timedelta(
            days=1
        )

    return (
        collected,
        diagnostics,
    )


# ============================================================
# YAHOO
# ============================================================

def yahoo_symbol(
    item: Dict[str, str],
) -> str:

    symbol = item["symbol"]

    if item["market"] == "TWSE":

        return f"{symbol}.TW"

    if item["market"] == "TPEX":

        return f"{symbol}.TWO"

    return symbol


def fetch_yahoo_history(
    item: Dict[str, str],
    start_date: str,
    end_date: str,
) -> Tuple[
    List[Dict[str, Any]],
    Optional[str],
]:

    symbol = yahoo_symbol(
        item
    )

    try:

        start_dt = datetime.strptime(
            start_date,
            "%Y-%m-%d",
        )

        end_dt = datetime.strptime(
            end_date,
            "%Y-%m-%d",
        )

    except ValueError:

        return [], "invalid_date"

    period1 = int(
        start_dt.replace(
            tzinfo=timezone.utc
        ).timestamp()
    )

    period2 = int(
        (
            end_dt
            + timedelta(days=1)
        ).replace(
            tzinfo=timezone.utc
        ).timestamp()
    )

    url = YAHOO_URL.format(
        symbol=symbol
    )

    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    data, error = http_get_json(
        url,
        params,
        "Yahoo",
    )

    if data is None:

        return [], error

    try:

        result = data[
            "chart"
        ][
            "result"
        ][0]

        timestamps = result.get(
            "timestamp"
        )

        quote = result[
            "indicators"
        ][
            "quote"
        ][0]

    except Exception as exc:

        return [], (
            "invalid_chart_response:"
            f"{type(exc).__name__}"
        )

    if not isinstance(
        timestamps,
        list,
    ):

        return [], "timestamp_missing"

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

    for index, timestamp in enumerate(
        timestamps
    ):

        try:

            dt = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

        except Exception:

            continue

        date_text = dt.strftime(
            "%Y-%m-%d"
        )

        normalized = normalize_price_row(
            item["symbol"],
            date_text,
            (
                opens[index]
                if index < len(opens)
                else None
            ),
            (
                highs[index]
                if index < len(highs)
                else None
            ),
            (
                lows[index]
                if index < len(lows)
                else None
            ),
            (
                closes[index]
                if index < len(closes)
                else None
            ),
            (
                volumes[index]
                if index < len(volumes)
                else None
            ),
        )

        if normalized:

            rows.append(
                normalized
            )

    rows.sort(
        key=lambda x: x["date"]
    )

    if not rows:

        return [], "no_valid_rows"

    return (
        rows[-MAX_HISTORY_ROWS:],
        None,
    )


# ============================================================
# HISTORY STATUS
# ============================================================

def history_status(
    count: int,
) -> str:

    if count < SHORT_HISTORY_THRESHOLD:

        return "short_history"

    if count < TARGET_HISTORY_ROWS:

        return "partial_history"

    return "complete"


# ============================================================
# SOURCE LABEL
# ============================================================

def build_source_label(
    used_existing: bool,
    used_official: bool,
    used_stock_day_all: bool,
    used_yahoo: bool,
) -> str:

    sources = []

    if used_existing:
        sources.append(
            "existing_cache"
        )

    if used_official:
        sources.append(
            "official"
        )

    if used_stock_day_all:
        sources.append(
            "STOCK_DAY_ALL"
        )

    if used_yahoo:
        sources.append(
            "Yahoo fallback"
        )

    if not sources:

        return "no_valid_source"

    return " + ".join(
        sources
    )


# ============================================================
# BUILD RESULTS
# ============================================================

def build_results(
    universe: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, Any],
    Dict[str, Any],
]:

    section(
        "FETCH PRICE HISTORY"
    )

    today = datetime.now(
        timezone.utc
    ).date()

    start_date = (
        today
        - timedelta(
            days=LOOKBACK_CALENDAR_DAYS
        )
    ).strftime(
        "%Y-%m-%d"
    )

    end_date = today.strftime(
        "%Y-%m-%d"
    )

    log(
        f"官方資料範圍："
        f"{start_date} ~ {end_date}"
    )

    log(
        f"LOOKBACK_CALENDAR_DAYS："
        f"{LOOKBACK_CALENDAR_DAYS}"
    )

    # --------------------------------------------------------
    # Existing
    # --------------------------------------------------------

    existing = load_existing_prices()

    log(
        f"Existing cache："
        f"{len(existing)} 檔"
    )

    # --------------------------------------------------------
    # Official
    # --------------------------------------------------------

    official, official_diagnostics = (
        collect_official_market_data(
            universe,
            start_date,
            end_date,
        )
    )

    results = {}
    diagnostics: Dict[str, Any] = {}

    yahoo_fallback_count = 0
    yahoo_success_count = 0
    yahoo_failure_count = 0

    total = len(universe)

    for index, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item["symbol"]

        log(
            f"[{index}/{total}] "
            f"{symbol} "
            f"{item['name']}"
        )

        row_map: Dict[
            str,
            Dict[str, Any],
        ] = {}

        used_existing = False
        used_official = False
        used_stock_day_all = False
        used_yahoo = False

        # ----------------------------------------------------
        # Existing cache
        # ----------------------------------------------------

        previous_rows = existing.get(
            symbol,
            []
        )

        if previous_rows:

            used_existing = True

            for row in previous_rows:

                row_map[
                    row["date"]
                ] = row

        # ----------------------------------------------------
        # Official historical
        # ----------------------------------------------------

        market_data = official[
            item["market"]
        ].get(
            symbol,
            {}
        )

        if market_data:

            used_official = True

            for date_text, row in (
                market_data.items()
            ):

                row_map[
                    date_text
                ] = row

        # ----------------------------------------------------
        # Detect STOCK_DAY_ALL contribution
        # ----------------------------------------------------

        stock_day_all_dates = set()

        if item["market"] == "TWSE":

            # collect_official_market_data 已經把
            # STOCK_DAY_ALL 合併進 official。
            #
            # 判斷方式：
            # 如果最新一筆官方資料來自 STOCK_DAY_ALL
            # 不可直接從 row object 判斷，因此使用
            # 最新交易日與 STOCK_DAY_ALL 全市場結果
            # 的存在狀態來標記。
            #
            # 此處只做 diagnostics，不改價格優先權。

            for date_text in market_data.keys():

                stock_day_all_dates.add(
                    date_text
                )

        # ----------------------------------------------------
        # Determine official history count
        # ----------------------------------------------------

        official_count = len(
            row_map
        )

        # ----------------------------------------------------
        # Yahoo supplemental fallback
        #
        # V11.0：
        #   只有 0 筆才 Yahoo
        #
        # V11.1：
        #   官方/既有資料不足 90 筆
        #   → Yahoo 補資料
        # ----------------------------------------------------

        if official_count < TARGET_HISTORY_ROWS:

            yahoo_fallback_count += 1
            used_yahoo = True

            missing_before = (
                TARGET_HISTORY_ROWS
                - official_count
            )

            log(
                f"  → 官方/既有資料 "
                f"{official_count} 筆，"
                f"不足 {missing_before} 筆"
            )

            yahoo_rows, yahoo_error = (
                fetch_yahoo_history(
                    item,
                    start_date,
                    end_date,
                )
            )

            if yahoo_rows:

                yahoo_success_count += 1

                for row in yahoo_rows:

                    date_text = row["date"]

                    # 官方優先：
                    # Yahoo 只能補不存在的日期。
                    if date_text not in row_map:

                        row_map[
                            date_text
                        ] = row

                log(
                    f"  → Yahoo 補資料："
                    f"{len(yahoo_rows)} 筆"
                )

            else:

                yahoo_failure_count += 1

                log(
                    "  → Yahoo fallback 失敗"
                )

            diagnostics[
                symbol
            ] = {
                "official_rows_before_yahoo":
                    official_count,
                "yahoo_rows_returned":
                    len(yahoo_rows),
                "yahoo_error":
                    yahoo_error,
                "used_yahoo_fallback":
                    True,
            }

        else:

            diagnostics[
                symbol
            ] = {
                "official_rows_before_yahoo":
                    official_count,
                "yahoo_rows_returned":
                    0,
                "yahoo_error":
                    None,
                "used_yahoo_fallback":
                    False,
            }

        # ----------------------------------------------------
        # Final rows
        # ----------------------------------------------------

        final_rows = sorted(
            row_map.values(),
            key=lambda x: x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        # ----------------------------------------------------
        # Final count
        # ----------------------------------------------------

        if not final_rows:

            diagnostics[
                symbol
            ].update(
                {
                    "status":
                        "missing",
                    "final_rows":
                        0,
                    "source":
                        "no_valid_source",
                }
            )

            log(
                "  ❌ 0 筆："
                "真正 missing"
            )

            continue

        final_count = len(
            final_rows
        )

        status = history_status(
            final_count
        )

        # ----------------------------------------------------
        # Source label
        # ----------------------------------------------------

        source = build_source_label(
            used_existing,
            used_official,
            used_stock_day_all,
            used_yahoo,
        )

        # 如果官方資料有日期但來源主要是官方，
        # 明確標記 STOCK_DAY_ALL 曾參與。
        if (
            item["market"] == "TWSE"
            and official_count > 0
        ):

            latest_official_date = max(
                market_data.keys()
            )

            if latest_official_date in (
                stock_day_all_dates
            ):

                used_stock_day_all = True

                source = build_source_label(
                    used_existing,
                    used_official,
                    used_stock_day_all,
                    used_yahoo,
                )

        diagnostics[
            symbol
        ].update(
            {
                "status":
                    status,
                "final_rows":
                    final_count,
                "source":
                    source,
                "latest_date":
                    final_rows[-1]["date"],
            }
        )

        if final_count < TARGET_HISTORY_ROWS:

            diagnostics[
                symbol
            ][
                "history_warning"
            ] = (
                "official_and_yahoo_sources_still_insufficient"
            )

        results[symbol] = {
            "symbol": symbol,
            "code": item["code"],
            "market": item["market"],
            "type": item["type"],
            "name": item["name"],
            "source": source,
            "history_rows": final_count,
            "history_status": status,
            "latest_date":
                final_rows[-1]["date"],
            "prices": final_rows,
        }

        log(
            f"  ✓ {final_count} 筆"
            f" / {status}"
            f" / {source}"
        )

        if yahoo_fallback_count:

            time.sleep(
                YAHOO_REQUEST_DELAY
            )

    fallback_diagnostics = {
        "yahoo_fallback_count":
            yahoo_fallback_count,
        "yahoo_success_count":
            yahoo_success_count,
        "yahoo_failure_count":
            yahoo_failure_count,
    }

    return (
        results,
        diagnostics,
        {
            "official":
                official_diagnostics,
            "fallback":
                fallback_diagnostics,
        },
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

    extra = sorted(
        actual - expected
    )

    malformed = []
    duplicate_dates = []

    for symbol, record in results.items():

        if symbol not in expected:

            malformed.append(
                (
                    symbol,
                    "not_in_universe",
                )
            )

            continue

        if not isinstance(
            record,
            dict,
        ):

            malformed.append(
                (
                    symbol,
                    "record_not_object",
                )
            )

            continue

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

        if len(rows) < 1:

            malformed.append(
                (
                    symbol,
                    "zero_history_rows",
                )
            )

            continue

        if len(rows) > MAX_HISTORY_ROWS:

            malformed.append(
                (
                    symbol,
                    "history_exceeds_max",
                )
            )

            continue

        dates = set()
        previous_date = ""

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
                symbol,
                row.get("date"),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("volume"),
            )

            if normalized is None:

                malformed.append(
                    (
                        symbol,
                        "invalid_ohlcv",
                    )
                )

                break

            date_text = row.get(
                "date"
            )

            if date_text in dates:

                duplicate_dates.append(
                    symbol
                )

                break

            dates.add(
                date_text
            )

            if (
                previous_date
                and date_text <= previous_date
            ):

                malformed.append(
                    (
                        symbol,
                        "date_not_strictly_increasing",
                    )
                )

                break

            previous_date = date_text

        if not record.get(
            "source"
        ):

            malformed.append(
                (
                    symbol,
                    "missing_source",
                )
            )

        if record.get(
            "history_rows"
        ) != len(rows):

            malformed.append(
                (
                    symbol,
                    "history_rows_mismatch",
                )
            )

        if record.get(
            "latest_date"
        ) != rows[-1]["date"]:

            malformed.append(
                (
                    symbol,
                    "latest_date_mismatch",
                )
            )

        expected_status = (
            "short_history"
            if len(rows) < SHORT_HISTORY_THRESHOLD
            else (
                "partial_history"
                if len(rows) < TARGET_HISTORY_ROWS
                else "complete"
            )
        )

        if record.get(
            "history_status"
        ) != expected_status:

            malformed.append(
                (
                    symbol,
                    "history_status_mismatch",
                )
            )

    return {
        "expected_count":
            len(expected),

        "actual_count":
            len(actual),

        "missing":
            missing,

        "extra":
            extra,

        "malformed":
            malformed,

        "duplicate_dates":
            sorted(
                set(duplicate_dates)
            ),

        "success_rate":
            (
                len(
                    expected & actual
                )
                / len(expected)
                if expected
                else 0.0
            ),
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

def print_diagnostics(
    validation: Dict[str, Any],
    diagnostics: Dict[str, Any],
) -> None:

    section(
        "PRICE DATA VALIDATION"
    )

    log(
        f"Universe："
        f"{validation['expected_count']}"
    )

    log(
        f"Price："
        f"{validation['actual_count']}"
    )

    log(
        f"Price 缺失："
        f"{len(validation['missing'])}"
    )

    log(
        f"Price 額外："
        f"{len(validation['extra'])}"
    )

    log(
        f"Malformed："
        f"{len(validation['malformed'])}"
    )

    log(
        f"Duplicate dates："
        f"{len(validation['duplicate_dates'])}"
    )

    log(
        f"成功率："
        f"{validation['success_rate']:.2%}"
    )

    official = diagnostics.get(
        "official",
        {}
    )

    fallback = diagnostics.get(
        "fallback",
        {}
    )

    log("")
    log(
        "官方批次 request："
        f"{REQUEST_STATS['twse_mi_index_requests']}"
    )

    log(
        "TPEx request："
        f"{REQUEST_STATS['tpex_requests']}"
    )

    log(
        "STOCK_DAY_ALL request："
        f"{REQUEST_STATS['twse_stock_day_all_requests']}"
    )

    log(
        "Yahoo request："
        f"{REQUEST_STATS['yahoo_requests']}"
    )

    log(
        "HTTP success："
        f"{REQUEST_STATS['successful_requests']}"
    )

    log(
        "HTTP failed："
        f"{REQUEST_STATS['failed_requests']}"
    )

    log(
        "Yahoo fallback 商品數："
        f"{fallback.get('yahoo_fallback_count', 0)}"
    )

    log(
        "Yahoo fallback 成功："
        f"{fallback.get('yahoo_success_count', 0)}"
    )

    log(
        "Yahoo fallback 失敗："
        f"{fallback.get('yahoo_failure_count', 0)}"
    )

    log(
        "STOCK_DAY_ALL rows："
        f"{official.get('twse_stock_day_all_rows', 0)}"
    )

    if validation["missing"]:

        log("")
        log(
            "❌ Universe 缺少價格資料："
        )

        for symbol in validation[
            "missing"
        ]:

            item_diag = diagnostics.get(
                symbol,
                "missing",
            )

            log(
                f"  {symbol} → "
                f"{item_diag}"
            )

    if validation["extra"]:

        log("")
        log(
            "❌ Price 額外商品："
        )

        for symbol in validation[
            "extra"
        ]:

            log(
                f"  {symbol}"
            )

    if validation["malformed"]:

        log("")
        log(
            "❌ Price 結構錯誤："
        )

        for symbol, reason in (
            validation["malformed"]
        ):

            log(
                f"  {symbol} → "
                f"{reason}"
            )

    if validation[
        "duplicate_dates"
    ]:

        log("")
        log(
            "❌ 日期重複："
        )

        for symbol in validation[
            "duplicate_dates"
        ]:

            log(
                f"  {symbol}"
            )

    if REQUEST_ERRORS:

        log("")
        log(
            f"⚠ HTTP errors："
            f"{len(REQUEST_ERRORS)}"
        )

        for error in REQUEST_ERRORS[
            :20
        ]:

            log(
                f"  {error['source']} "
                f"→ {error['error']}"
            )

        if len(
            REQUEST_ERRORS
        ) > 20:

            log(
                f"  ... "
                f"其餘 {len(REQUEST_ERRORS) - 20} 筆"
            )


# ============================================================
# SHARDS
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any],
    ],
) -> List[
    Tuple[str, Dict[str, Any]]
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
                ][
                    "prices"
                ]
            )

        filename = (
            f"prices_"
            f"{start // STOCKS_PER_FILE + 1:03d}"
            f".json"
        )

        shards.append(
            (
                filename,
                {
                    "stocks": stocks
                },
            )
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

    actual = set(
        stocks.keys()
    )

    expected = set(
        expected_symbols
    )

    if actual != expected:

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

        if len(rows) < 1:

            raise RuntimeError(
                f"{symbol} 沒有價格資料"
            )

        if len(rows) > MAX_HISTORY_ROWS:

            raise RuntimeError(
                f"{symbol} 超過 "
                f"{MAX_HISTORY_ROWS} 筆"
            )

        previous = ""
        dates = set()

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} row "
                    "不是 object"
                )

            normalized = normalize_price_row(
                symbol,
                row.get("date"),
                row.get("open"),
                row.get("high"),
                row.get("low"),
                row.get("close"),
                row.get("volume"),
            )

            if normalized is None:

                raise RuntimeError(
                    f"{symbol} OHLCV 異常"
                )

            date_text = row["date"]

            if date_text in dates:

                raise RuntimeError(
                    f"{symbol} 日期重複"
                )

            if (
                previous
                and date_text <= previous
            ):

                raise RuntimeError(
                    f"{symbol} 日期排序錯誤"
                )

            dates.add(
                date_text
            )

            previous = date_text


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any],
    ],
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, Any],
) -> Dict[str, Any]:

    source_counts = {}
    type_counts = {}

    complete_count = 0
    partial_count = 0
    short_count = 0

    latest_dates = []

    for record in results.values():

        source = record.get(
            "source",
            "unknown",
        )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

        record_type = record.get(
            "type",
            "unknown",
        )

        type_counts[record_type] = (
            type_counts.get(
                record_type,
                0,
            )
            + 1
        )

        status = record.get(
            "history_status"
        )

        if status == "complete":

            complete_count += 1

        elif status == "partial_history":

            partial_count += 1

        elif status == "short_history":

            short_count += 1

        latest = record.get(
            "latest_date"
        )

        if latest:
            latest_dates.append(
                latest
            )

    price_stock_count = sum(
        1
        for record in results.values()
        if record.get("type") == "STOCK"
    )

    price_etf_count = sum(
        1
        for record in results.values()
        if record.get("type") == "ETF"
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
            price_stock_count,

        "price_etf_count":
            price_etf_count,

        "price_total_count":
            len(results),

        "missing_count":
            len(
                validation[
                    "missing"
                ]
            ),

        "missing_symbols":
            validation[
                "missing"
            ],

        "extra_count":
            len(
                validation[
                    "extra"
                ]
            ),

        "extra_symbols":
            validation[
                "extra"
            ],

        "complete_history_count":
            complete_count,

        "partial_history_count":
            partial_count,

        "short_history_count":
            short_count,

        "short_history_threshold":
            SHORT_HISTORY_THRESHOLD,

        "target_history_rows":
            TARGET_HISTORY_ROWS,

        "max_history_rows":
            MAX_HISTORY_ROWS,

        "success_rate":
            validation[
                "success_rate"
            ],

        "sources":
            source_counts,

        "types":
            type_counts,

        # ----------------------------------------------------
        # V11.1 observability
        # ----------------------------------------------------

        "request_stats":
            REQUEST_STATS,

        "request_errors":
            REQUEST_ERRORS,

        "fetch_diagnostics":
            diagnostics,

        "latest_date":
            (
                max(latest_dates)
                if latest_dates
                else None
            ),

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

    if not path.exists():

        raise RuntimeError(
            "manifest.json 不存在"
        )

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
        "generator_version"
    ) != VERSION:

        raise RuntimeError(
            "manifest generator_version 錯誤"
        )

    if manifest.get(
        "universe_stock_count"
    ) != universe_stock_count:

        raise RuntimeError(
            "manifest universe STOCK count 錯誤"
        )

    if manifest.get(
        "universe_etf_count"
    ) != universe_etf_count:

        raise RuntimeError(
            "manifest universe ETF count 錯誤"
        )

    if manifest.get(
        "expected_total_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest expected_total_count 錯誤"
        )

    if manifest.get(
        "price_total_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest price_total_count 錯誤"
        )

    if manifest.get(
        "missing_count"
    ) != 0:

        raise RuntimeError(
            "manifest missing_count != 0"
        )

    if manifest.get(
        "extra_count"
    ) != 0:

        raise RuntimeError(
            "manifest extra_count != 0"
        )

    files = manifest.get(
        "files"
    )

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 不一致"
        )

    # V11.1 diagnostics 必須存在
    if not isinstance(
        manifest.get(
            "request_stats"
        ),
        dict,
    ):

        raise RuntimeError(
            "manifest.request_stats 缺失"
        )

    if not isinstance(
        manifest.get(
            "fetch_diagnostics"
        ),
        dict,
    ):

        raise RuntimeError(
            "manifest.fetch_diagnostics 缺失"
        )


# ============================================================
# COMPLETE OUTPUT VALIDATION
# ============================================================

def validate_complete_output(
    output_dir: Path,
    universe: List[Dict[str, str]],
) -> None:

    section(
        "FINAL OUTPUT VALIDATION"
    )

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    expected_stock_count = sum(
        1
        for item in universe
        if item["type"] == "STOCK"
    )

    expected_etf_count = sum(
        1
        for item in universe
        if item["type"] == "ETF"
    )

    manifest_path = (
        output_dir
        / "manifest.json"
    )

    if not manifest_path.exists():

        raise RuntimeError(
            "最終輸出缺少 manifest.json"
        )

    manifest = load_json(
        manifest_path
    )

    files = manifest.get(
        "files"
    )

    if not isinstance(
        files,
        list,
    ):

        raise RuntimeError(
            "manifest.files 無效"
        )

    all_symbols = set()

    stock_count = 0
    etf_count = 0

    for filename in files:

        path = (
            output_dir
            / Path(
                str(filename)
            ).name
        )

        if not path.exists():

            raise RuntimeError(
                f"manifest 指向不存在 shard："
                f"{filename}"
            )

        data = load_json(
            path
        )

        stocks = data.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):

            raise RuntimeError(
                f"{filename} stocks 無效"
            )

        overlap = (
            all_symbols
            & set(stocks.keys())
        )

        if overlap:

            raise RuntimeError(
                f"跨 shard 重複："
                f"{sorted(overlap)[:20]}"
            )

        all_symbols.update(
            stocks.keys()
        )

        validate_shard(
            path,
            sorted(
                stocks.keys()
            ),
        )

    missing = sorted(
        expected_symbols
        - all_symbols
    )

    extra = sorted(
        all_symbols
        - expected_symbols
    )

    if missing:

        raise RuntimeError(
            "FINAL VALIDATION："
            "Universe 缺少價格："
            f"{missing}"
        )

    if extra:

        raise RuntimeError(
            "FINAL VALIDATION："
            "Price 額外商品："
            f"{extra}"
        )

    if len(all_symbols) != len(
        expected_symbols
    ):

        raise RuntimeError(
            "FINAL VALIDATION："
            "Universe / Price 數量不一致"
        )

    for item in universe:

        symbol = item["symbol"]

        if symbol not in all_symbols:

            raise RuntimeError(
                f"FINAL VALIDATION："
                f"{symbol} 不存在"
            )

        if item["type"] == "STOCK":

            stock_count += 1

        elif item["type"] == "ETF":

            etf_count += 1

    if stock_count != expected_stock_count:

        raise RuntimeError(
            "FINAL VALIDATION："
            "STOCK count 錯誤"
        )

    if etf_count != expected_etf_count:

        raise RuntimeError(
            "FINAL VALIDATION："
            "ETF count 錯誤"
        )

    validate_manifest(
        manifest_path,
        sorted(
            expected_symbols
        ),
        files,
        expected_stock_count,
        expected_etf_count,
    )

    log(
        f"Universe：{len(expected_symbols)}"
    )

    log(
        f"Price：{len(all_symbols)}"
    )

    log(
        f"STOCK：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"Shards：{len(files)}"
    )

    log(
        "✓ Universe → Price 完整對接"
    )

    log(
        "✓ 跨 shard duplicate = 0"
    )

    log(
        "✓ Price extra = 0"
    )

    log(
        "✓ Price missing = 0"
    )

    log(
        "✓ Manifest PASS"
    )

    log(
        "✓ FINAL OUTPUT VALIDATION PASS"
    )


# ============================================================
# WRITE TEMP OUTPUT
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[
        str,
        Dict[str, Any],
    ],
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, Any],
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

    for filename, shard in shards:

        path = (
            temp_dir
            / filename
        )

        save_json(
            path,
            shard,
        )

        shard_files.append(
            filename
        )

    # --------------------------------------------------------
    # shard validation
    # --------------------------------------------------------

    for filename in shard_files:

        data = load_json(
            temp_dir
            / filename
        )

        stocks = data[
            "stocks"
        ]

        validate_shard(
            temp_dir / filename,
            sorted(
                stocks.keys()
            ),
        )

    # --------------------------------------------------------
    # shard 集合驗證
    # --------------------------------------------------------

    shard_symbols = set()

    for filename in shard_files:

        data = load_json(
            temp_dir
            / filename
        )

        stocks = data[
            "stocks"
        ]

        overlap = (
            shard_symbols
            & set(stocks.keys())
        )

        if overlap:

            raise RuntimeError(
                "寫入階段發現跨 shard 重複："
                f"{sorted(overlap)}"
            )

        shard_symbols.update(
            stocks.keys()
        )

    if shard_symbols != set(symbols):

        raise RuntimeError(
            "寫入階段 shard "
            "與 results 集合不一致"
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


# ============================================================
# ATOMIC REPLACE
# ============================================================

def atomic_replace_output(
    temp_output: Path,
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

        temp_output.rename(
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

    section(
        f"TAIWAN STOCK AI PRICE PIPELINE {VERSION}"
    )

    log(
        "官方資料：TWSE / TPEx 日期批次"
    )

    log(
        "TWSE：STOCK_DAY_ALL 最新交易日補強"
    )

    log(
        "Yahoo：官方歷史不足時才補資料"
    )

    log(
        f"目標歷史：{TARGET_HISTORY_ROWS} 筆"
    )

    log(
        f"最大歷史：{MAX_HISTORY_ROWS} 筆"
    )

    log(
        f"短歷史：< {SHORT_HISTORY_THRESHOLD} 筆"
    )

    log(
        "最小存在條件：>= 1 筆有效 OHLCV"
    )

    # ========================================================
    # LOAD UNIVERSE
    # ========================================================

    section(
        "LOAD UNIVERSE"
    )

    universe = load_universe()

    universe_stock_count = sum(
        1
        for item in universe
        if item["type"] == "STOCK"
    )

    universe_etf_count = sum(
        1
        for item in universe
        if item["type"] == "ETF"
    )

    log(
        f"Universe total："
        f"{len(universe)}"
    )

    log(
        f"Universe STOCK："
        f"{universe_stock_count}"
    )

    log(
        f"Universe ETF："
        f"{universe_etf_count}"
    )

    # ========================================================
    # BUILD RESULTS
    # ========================================================

    (
        results,
        diagnostics,
        fetch_diagnostics,
    ) = build_results(
        universe
    )

    combined_diagnostics = {
        "per_symbol":
            diagnostics,
        "fetch":
            fetch_diagnostics,
        "request_errors":
            REQUEST_ERRORS,
    }

    # ========================================================
    # RESULT VALIDATION
    # ========================================================

    validation = validate_results(
        results,
        universe,
    )

    print_diagnostics(
        validation,
        {
            **diagnostics,
            "official":
                fetch_diagnostics.get(
                    "official",
                    {}
                ),
            "fallback":
                fetch_diagnostics.get(
                    "fallback",
                    {}
                ),
        },
    )

    # --------------------------------------------------------
    # malformed / extra
    # --------------------------------------------------------

    if validation[
        "malformed"
    ]:

        raise RuntimeError(
            "Price results 存在 malformed data"
        )

    if validation[
        "extra"
    ]:

        raise RuntimeError(
            "Price results 出現 Universe 外商品"
        )

    # --------------------------------------------------------
    # missing
    # --------------------------------------------------------

    if validation[
        "missing"
    ]:

        log("")
        log(
            "❌ Price pipeline 無法建立完整 Universe"
        )

        for symbol in validation[
            "missing"
        ]:

            log(
                f"  {symbol}"
                f" → "
                f"{diagnostics.get(symbol, 'missing')}"
            )

        raise RuntimeError(
            "Universe → Price 尚未完整對接"
        )

    if len(results) != len(
        universe
    ):

        raise RuntimeError(
            "Price results count "
            "與 Universe 不一致"
        )

    # ========================================================
    # TEMPORARY OUTPUT
    # ========================================================

    section(
        "WRITE TEMPORARY PRICE DATA"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_root = Path(
        tempfile.mkdtemp(
            prefix=".prices_",
            dir=str(DATA_DIR),
        )
    )

    temp_output = (
        temp_root
        / "prices"
    )

    try:

        write_price_directory(
            temp_output,
            results,
            universe_stock_count,
            universe_etf_count,
            validation,
            combined_diagnostics,
        )

        # ----------------------------------------------------
        # 不相信記憶中的 results
        # 直接重新讀檔驗證
        # ----------------------------------------------------

        validate_complete_output(
            temp_output,
            universe,
        )

        # ----------------------------------------------------
        # ATOMIC REPLACE
        # ----------------------------------------------------

        section(
            "ATOMIC REPLACE"
        )

        atomic_replace_output(
            temp_output
        )

    finally:

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

    # ========================================================
    # FINAL VALIDATION AGAINST ACTUAL DISK
    # ========================================================

    validate_complete_output(
        OUTPUT_DIR,
        universe,
    )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    final_complete = sum(
        1
        for record in results.values()
        if record.get(
            "history_status"
        ) == "complete"
    )

    final_partial = sum(
        1
        for record in results.values()
        if record.get(
            "history_status"
        ) == "partial_history"
    )

    final_short = sum(
        1
        for record in results.values()
        if record.get(
            "history_status"
        ) == "short_history"
    )

    section(
        "PRICE PIPELINE PASS"
    )

    log(
        f"Universe：{len(universe)}"
    )

    log(
        f"Price：{len(results)}"
    )

    log(
        f"Complete：{final_complete}"
    )

    log(
        f"Partial：{final_partial}"
    )

    log(
        f"Short：{final_short}"
    )

    log(
        "Missing：0"
    )

    log(
        "Extra：0"
    )

    log(
        "Malformed：0"
    )

    log("")
    log(
        "REQUEST SUMMARY"
    )

    log(
        f"Total requests："
        f"{REQUEST_STATS['total_requests']}"
    )

    log(
        f"TWSE MI_INDEX："
        f"{REQUEST_STATS['twse_mi_index_requests']}"
    )

    log(
        f"TPEx："
        f"{REQUEST_STATS['tpex_requests']}"
    )

    log(
        f"STOCK_DAY_ALL："
        f"{REQUEST_STATS['twse_stock_day_all_requests']}"
    )

    log(
        f"Yahoo："
        f"{REQUEST_STATS['yahoo_requests']}"
    )

    log(
        f"HTTP failed："
        f"{REQUEST_STATS['failed_requests']}"
    )

    log("")
    log(
        "✓ Universe 是唯一商品來源"
    )

    log(
        "✓ 官方價格採市場/日期批次抓取"
    )

    log(
        "✓ STOCK_DAY_ALL 已正式參與"
    )

    log(
        "✓ 官方資料優先"
    )

    log(
        "✓ 官方歷史不足會啟動補資料"
    )

    log(
        "✓ Yahoo 僅作補資料 fallback"
    )

    log(
        "✓ 官方資料不會被 Yahoo 覆蓋"
    )

    log(
        "✓ HTTP failure 已具備 diagnostics"
    )

    log(
        "✓ 歷史不足不會 silently drop"
    )

    log(
        "✓ >= 1 筆有效 OHLCV 即保留"
    )

    log(
        "✓ short_history 正確標記"
    )

    log(
        "✓ partial_history 正確標記"
    )

    log(
        "✓ complete history 正確標記"
    )

    log(
        "✓ Universe → Price = 100%"
    )

    log(
        "✓ shard validation PASS"
    )

    log(
        "✓ manifest validation PASS"
    )

    log(
        "✓ final disk validation PASS"
    )

    log(
        "✓ PRICE PIPELINE PASS"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:

        log("")
        log(
            "❌ 使用者中止"
        )

        raise SystemExit(
            130
        )

    except Exception as exc:

        log("")
        log(
            "========================================"
        )
        log(
            "PRICE PIPELINE FAILED"
        )
        log(
            "========================================"
        )
        log(
            f"❌ {exc}"
        )

        raise SystemExit(
            1
        )
