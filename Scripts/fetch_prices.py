#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式價格管線 V12.0
============================================================

核心目標
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. 所有 active STOCK / ETF 都必須進入 Price pipeline
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. TWSE / TPEx 官方資料優先
7. Yahoo 僅作「單一商品官方完全無資料」時的最後 fallback
8. 歷史不足不是商品不存在
9. >= 1 筆有效 OHLCV 即必須進入 Price results
10. 0 筆才列為 missing
11. < 20 筆只標記 short_history
12. 正常歷史目標 90 筆
13. Price shard 必須只包含 Universe 商品
14. manifest 必須與實際 shard 一致
15. 使用 temporary directory
16. atomic replace
17. 舊版 prices schema 可以安全重建
18. Universe 新增商品自動初始化
19. 不因歷史不足 silently drop 商品
20. 不允許每商品 × 每日 HTTP

============================================================

V12.0 重要架構
------------------------------------------------------------

舊版 V10：

    2301 商品
        ×
    180 天
        ×
    官方 HTTP

    => 約 40 萬次 request

V12：

    TWSE
        每個日期只抓一次

    TPEx
        每個日期只抓一次

    然後：

        official_data
             ↓
        Universe 對接
             ↓
        Price shards

============================================================

V12.0 進一步修正
------------------------------------------------------------

A. 不再每次執行都重新抓完整 180 日

    已有足夠歷史：
        只補最新日期

    歷史不足：
        才補需要的日期

    新商品：
        才使用完整 lookback

B. 官方批次資料與商品解耦

    不再：

        商品 -> 日期 -> HTTP

    改成：

        日期 -> 官方市場資料
              -> 所有 Universe 商品

C. 官方 API 單次失敗有明確 diagnostics

D. 不允許 HTTP 無限等待

E. Yahoo fallback 僅處理真正官方 0 筆的商品

F. 最終：

    Universe
        ==
    Price shards
        ==
    manifest

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

VERSION = "V12.0"
SCHEMA_VERSION = "prices-v12.0"


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

# 新商品第一次建立歷史時使用。
INITIAL_LOOKBACK_CALENDAR_DAYS = 180

# 每 shard 100 檔。
STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0
MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# HTTP POLICY
# ============================================================

MAX_RETRIES = 3

# 不允許單一 HTTP request 無限卡住。
REQUEST_TIMEOUT = 20

# 官方 API 日期與日期之間的小間隔。
REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5


# ============================================================
# FALLBACK
# ============================================================

YAHOO_ENABLED = True

# Yahoo 只在官方對該商品完全沒有任何有效資料時使用。
YAHOO_FALLBACK_ONLY_WHEN_OFFICIAL_EMPTY = True


# ============================================================
# DIAGNOSTIC
# ============================================================

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
        "null",
        "None",
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
            pass

    # ROC date
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

    open_price = safe_float(
        open_value
    )

    high_price = safe_float(
        high
    )

    low_price = safe_float(
        low
    )

    close_price = safe_float(
        close
    )

    volume_value = safe_int(
        volume
    )

    if close_price is None:
        return None

    if close_price <= 0:
        return None

    # 缺 OHLC 時允許用 close 補齊。
    if open_price is None:
        open_price = close_price

    if high_price is None:
        high_price = close_price

    if low_price is None:
        low_price = close_price

    if high_price < low_price:
        return None

    if high_price < close_price:
        high_price = close_price

    if low_price > close_price:
        low_price = close_price

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
# HTTP
# ============================================================

def http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:

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
            )

            response.raise_for_status()

            return (
                response.json(),
                None,
            )

        except Exception as exc:

            last_error = str(exc)

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY
                )

    return (
        None,
        last_error or "unknown_error",
    )


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> List[Dict[str, str]]:

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

    universe = []
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
                "缺少有效 symbol"
            )

        if symbol in seen:

            raise RuntimeError(
                f"Universe 重複 symbol："
                f"{symbol}"
            )

        seen.add(symbol)

        market = clean_text(
            item.get(
                "market"
            )
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            raise RuntimeError(
                f"{symbol} market 無效："
                f"{market}"
            )

        universe.append(
            {
                "symbol": symbol,
                "code": symbol,
                "name": clean_text(
                    item.get(
                        "name"
                    )
                ),
                "market": market,
                "type": clean_text(
                    item.get(
                        "type"
                    )
                ),
                "instrument_type":
                    clean_text(
                        item.get(
                            "instrument_type"
                        )
                    ),
            }
        )

    if not universe:

        raise RuntimeError(
            "Universe 為 0"
        )

    return universe


# ============================================================
# EXISTING PRICE DATA
# ============================================================

def load_existing_prices() -> Dict[
    str,
    Dict[str, Any],
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

        for symbol, rows in (
            stocks.items()
        ):

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

                normalized = (
                    normalize_price_row(
                        symbol,
                        row.get("date"),
                        row.get("open"),
                        row.get("high"),
                        row.get("low"),
                        row.get("close"),
                        row.get("volume"),
                    )
                )

                if normalized:
                    clean_rows.append(
                        normalized
                    )

            clean_rows.sort(
                key=lambda x: x["date"]
            )

            if clean_rows:

                result[symbol] = {
                    "rows": clean_rows[
                        -MAX_HISTORY_ROWS:
                    ],
                }

    return result


# ============================================================
# TWSE DAILY
# ============================================================

def parse_twse_mi_index(
    data: Any,
    target_date: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    tables = data.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):
        return result

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

        field_map = {
            clean_text(
                name
            ): index
            for index, name
            in enumerate(fields)
        }

        def get_value(
            row,
            names,
        ):

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
                get_value(
                    row,
                    (
                        "證券代號",
                        "股票代號",
                    ),
                )
            )

            if not symbol:
                continue

            normalized = (
                normalize_price_row(
                    symbol,
                    target_date,
                    get_value(
                        row,
                        (
                            "開盤價",
                            "開盤",
                        ),
                    ),
                    get_value(
                        row,
                        (
                            "最高價",
                            "最高",
                        ),
                    ),
                    get_value(
                        row,
                        (
                            "最低價",
                            "最低",
                        ),
                    ),
                    get_value(
                        row,
                        (
                            "收盤價",
                            "收盤",
                        ),
                    ),
                    get_value(
                        row,
                        (
                            "成交股數",
                            "成交量",
                        ),
                    ),
                )
            )

            if normalized:
                result[symbol] = normalized

    return result


def parse_twse_stock_day_all(
    data: Any,
    target_date: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        data,
        list,
    ):
        return result

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

        normalized = (
            normalize_price_row(
                symbol,
                target_date,
                item.get(
                    "OpeningPrice"
                )
                or item.get(
                    "開盤價"
                ),
                item.get(
                    "HighestPrice"
                )
                or item.get(
                    "最高價"
                ),
                item.get(
                    "LowestPrice"
                )
                or item.get(
                    "最低價"
                ),
                item.get(
                    "ClosingPrice"
                )
                or item.get(
                    "收盤價"
                ),
                item.get(
                    "TradeVolume"
                )
                or item.get(
                    "成交股數"
                ),
            )
        )

        if normalized:
            result[symbol] = normalized

    return result


def fetch_twse_day(
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
    )

    if data is not None:

        parsed = parse_twse_mi_index(
            data,
            target_date,
        )

        if parsed:
            return (
                parsed,
                None,
            )

    # MI_INDEX 沒有有效資料時，
    # 再嘗試官方 STOCK_DAY_ALL。
    data2, error2 = http_get_json(
        TWSE_STOCK_DAY_ALL_URL
    )

    if data2 is not None:

        parsed2 = (
            parse_twse_stock_day_all(
                data2,
                target_date,
            )
        )

        if parsed2:
            return (
                parsed2,
                None,
            )

    return (
        {},
        (
            error
            or error2
            or "TWSE no valid data"
        ),
    )


# ============================================================
# TPEX
# ============================================================

def parse_tpex_daily(
    data: Any,
    target_date: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    aa_data = data.get(
        "aaData"
    )

    if not isinstance(
        aa_data,
        list,
    ):
        return result

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

        normalized = (
            normalize_price_row(
                symbol,
                target_date,
                row[4],
                row[5],
                row[6],
                row[2],
                row[7],
            )
        )

        if normalized:
            result[symbol] = normalized

    return result


def fetch_tpex_day(
    target_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Optional[str],
]:

    dt = datetime.strptime(
        target_date,
        "%Y-%m-%d",
    )

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
    )

    if data is None:

        return (
            {},
            error or "TPEx request failed",
        )

    parsed = parse_tpex_daily(
        data,
        target_date,
    )

    if not parsed:

        return (
            {},
            "TPEx no valid data",
        )

    return (
        parsed,
        None,
    )


# ============================================================
# TRADING DAY GENERATOR
# ============================================================

def iter_calendar_dates(
    start_date: str,
    end_date: str,
):

    start = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    ).date()

    end = datetime.strptime(
        end_date,
        "%Y-%m-%d",
    ).date()

    current = start

    while current <= end:

        # 星期六、星期日不打官方 request。
        if current.weekday() < 5:

            yield current.strftime(
                "%Y-%m-%d"
            )

        current += timedelta(
            days=1
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
) -> List[Dict[str, Any]]:

    symbol = yahoo_symbol(
        item
    )

    start_dt = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    )

    end_dt = datetime.strptime(
        end_date,
        "%Y-%m-%d",
    )

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
        "includeAdjustedClose":
            "true",
    }

    data, error = http_get_json(
        url,
        params,
    )

    if data is None:
        return []

    try:

        chart = data[
            "chart"
        ][
            "result"
        ][0]

        timestamps = chart.get(
            "timestamp"
        )

        quote = chart[
            "indicators"
        ][
            "quote"
        ][0]

    except Exception:

        return []

    if not isinstance(
        timestamps,
        list,
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

    rows = []

    for index, timestamp in enumerate(
        timestamps
    ):

        try:

            dt = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

            date_text = dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            continue

        normalized = (
            normalize_price_row(
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
        )

        if normalized:
            rows.append(
                normalized
            )

    rows.sort(
        key=lambda x: x["date"]
    )

    return rows[
        -MAX_HISTORY_ROWS:
    ]


# ============================================================
# DETERMINE FETCH WINDOW
# ============================================================

def determine_fetch_window(
    universe: List[Dict[str, str]],
    existing: Dict[str, Dict[str, Any]],
    today: str,
) -> Tuple[str, str, bool]:

    """
    決定官方批次資料最小需要抓到哪一天。

    規則：

    1. 完全沒有歷史的商品：
       使用 180 天 lookback。

    2. 已有歷史但不足 90：
       往前補足。

    3. 已有 90 筆：
       只需要從最新日期之後開始。

    因為官方 API 是「市場 × 日期」，
    所以只取所有商品所需日期的最早日期。

    回傳：

        start_date
        end_date
        full_rebuild
    """

    today_dt = datetime.strptime(
        today,
        "%Y-%m-%d",
    ).date()

    default_start = (
        today_dt
        - timedelta(
            days=INITIAL_LOOKBACK_CALENDAR_DAYS
        )
    )

    earliest_needed = None
    full_rebuild = False

    for item in universe:

        symbol = item["symbol"]

        previous = existing.get(
            symbol
        )

        if not previous:

            needed = default_start
            full_rebuild = True

        else:

            rows = previous.get(
                "rows",
                []
            )

            if not rows:

                needed = default_start
                full_rebuild = True

            elif len(rows) < TARGET_HISTORY_ROWS:

                oldest = datetime.strptime(
                    rows[0]["date"],
                    "%Y-%m-%d",
                ).date()

                missing_rows = (
                    TARGET_HISTORY_ROWS
                    - len(rows)
                )

                # 每筆交易日約 1.4 個日曆日。
                extra_days = max(
                    30,
                    int(
                        missing_rows * 1.6
                    ),
                )

                needed = (
                    oldest
                    - timedelta(
                        days=extra_days
                    )
                )

            else:

                latest = datetime.strptime(
                    rows[-1]["date"],
                    "%Y-%m-%d",
                ).date()

                # 已經有完整歷史，
                # 從最新資料之後開始補。
                needed = latest

        if (
            earliest_needed is None
            or needed < earliest_needed
        ):

            earliest_needed = needed

    if earliest_needed is None:

        earliest_needed = default_start

    return (
        earliest_needed.strftime(
            "%Y-%m-%d"
        ),
        today,
        full_rebuild,
    )


# ============================================================
# FETCH OFFICIAL BATCH DATA
# ============================================================

def fetch_official_batch(
    universe: List[Dict[str, str]],
    start_date: str,
    end_date: str,
) -> Tuple[
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, str],
    Dict[str, int],
]:

    """
    回傳：

        official_data = {
            "TWSE": {
                "2330": {
                    "2026-08-29": row
                }
            },
            "TPEX": {
                ...
            }
        }

        errors = {
            "TWSE:2026-08-01":
                "..."
        }

        request_stats = {
            "TWSE": n,
            "TPEX": n
        }
    """

    official_data = {
        "TWSE": {},
        "TPEX": {},
    }

    errors = {}

    request_stats = {
        "TWSE": 0,
        "TPEX": 0,
    }

    markets = {
        "TWSE",
        "TPEX",
    }

    universe_markets = {
        item["market"]
        for item in universe
    }

    dates = list(
        iter_calendar_dates(
            start_date,
            end_date,
        )
    )

    section(
        "FETCH OFFICIAL BATCH DATA"
    )

    log(
        f"日期範圍："
        f"{start_date} ~ {end_date}"
    )

    log(
        f"工作日 request 數："
        f"{len(dates)}"
    )

    log(
        "模式：每市場 × 每日期一次 request"
    )

    for date_index, date_text in enumerate(
        dates,
        start=1,
    ):

        log(
            f"[{date_index}/{len(dates)}] "
            f"{date_text}"
        )

        for market in (
            sorted(markets)
        ):

            if market not in universe_markets:
                continue

            request_stats[
                market
            ] += 1

            if market == "TWSE":

                rows, error = (
                    fetch_twse_day(
                        date_text
                    )
                )

            else:

                rows, error = (
                    fetch_tpex_day(
                        date_text
                    )
                )

            if rows:

                for symbol, row in (
                    rows.items()
                ):

                    if symbol not in (
                        official_data[
                            market
                        ]
                    ):

                        official_data[
                            market
                        ][symbol] = {}

                    official_data[
                        market
                    ][symbol][
                        row["date"]
                    ] = row

                log(
                    f"  ✓ {market}："
                    f"{len(rows)} 檔"
                )

            else:

                key = (
                    f"{market}:"
                    f"{date_text}"
                )

                errors[key] = (
                    error
                    or "empty_official_data"
                )

                log(
                    f"  ⚠ {market}："
                    "無有效資料"
                )

            time.sleep(
                REQUEST_DELAY
            )

    return (
        official_data,
        errors,
        request_stats,
    )


# ============================================================
# MERGE ONE SYMBOL
# ============================================================

def merge_symbol_rows(
    item: Dict[str, str],
    existing: Dict[str, Dict[str, Any]],
    official_data: Dict[
        str,
        Dict[str, Dict[str, Any]],
    ],
    start_date: str,
    end_date: str,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    symbol = item["symbol"]
    market = item["market"]

    row_map = {}

    # --------------------------------------------------------
    # Existing
    # --------------------------------------------------------

    previous = existing.get(
        symbol
    )

    if previous:

        for row in previous.get(
            "rows",
            [],
        ):

            row_map[
                row["date"]
            ] = row

    existing_count = len(
        row_map
    )

    # --------------------------------------------------------
    # Official
    # --------------------------------------------------------

    market_data = official_data.get(
        market,
        {}
    )

    symbol_data = market_data.get(
        symbol,
        {}
    )

    for date_text, row in (
        symbol_data.items()
    ):

        row_map[
            date_text
        ] = row

    official_count = (
        len(row_map)
        - existing_count
    )

    rows = sorted(
        row_map.values(),
        key=lambda x: x["date"],
    )

    rows = rows[
        -MAX_HISTORY_ROWS:
    ]

    if rows:

        return (
            rows,
            (
                "TWSE official"
                if market == "TWSE"
                else "TPEx official"
            ),
        )

    return (
        [],
        "no_official_data",
    )


# ============================================================
# BUILD RESULTS
# ============================================================

def build_results(
    universe: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, str],
    Dict[str, Any],
]:

    section(
        "FETCH PRICE HISTORY"
    )

    today = datetime.now(
        timezone.utc
    ).date().strftime(
        "%Y-%m-%d"
    )

    existing = load_existing_prices()

    start_date, end_date, full_rebuild = (
        determine_fetch_window(
            universe,
            existing,
            today,
        )
    )

    log(
        f"Price fetch window："
        f"{start_date} ~ {end_date}"
    )

    if full_rebuild:
        log(
            "模式："
            "包含新商品 / 歷史不足商品"
        )
    else:
        log(
            "模式："
            "增量更新"
        )

    # --------------------------------------------------------
    # 官方批次資料
    # --------------------------------------------------------

    (
        official_data,
        official_errors,
        request_stats,
    ) = fetch_official_batch(
        universe,
        start_date,
        end_date,
    )

    results = {}
    diagnostics = {}

    yahoo_count = 0
    official_count = 0

    # --------------------------------------------------------
    # 對接 Universe
    # --------------------------------------------------------

    section(
        "MAP OFFICIAL DATA TO UNIVERSE"
    )

    total = len(
        universe
    )

    for index, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item["symbol"]

        rows, source = (
            merge_symbol_rows(
                item,
                existing,
                official_data,
                start_date,
                end_date,
            )
        )

        # ----------------------------------------------------
        # Official 有資料
        # ----------------------------------------------------

        if rows:

            official_count += 1

        # ----------------------------------------------------
        # Official 完全 0 筆
        #
        # 只有這裡允許 Yahoo fallback。
        # ----------------------------------------------------

        if (
            not rows
            and YAHOO_ENABLED
            and YAHOO_FALLBACK_ONLY_WHEN_OFFICIAL_EMPTY
        ):

            log(
                f"[{index}/{total}] "
                f"{symbol} "
                f"{item['name']} "
                "→ Yahoo fallback"
            )

            yahoo_rows = (
                fetch_yahoo_history(
                    item,
                    start_date,
                    end_date,
                )
            )

            if yahoo_rows:

                rows = yahoo_rows

                source = (
                    "Yahoo fallback"
                )

                yahoo_count += 1

        else:

            log(
                f"[{index}/{total}] "
                f"{symbol} "
                f"{item['name']}"
            )

        # ----------------------------------------------------
        # 真正 0 筆
        # ----------------------------------------------------

        if not rows:

            diagnostics[
                symbol
            ] = (
                "no_valid_price_data"
            )

            log(
                "  ❌ 0 筆有效價格"
            )

            continue

        # ----------------------------------------------------
        # History status
        # ----------------------------------------------------

        row_count = len(
            rows
        )

        if row_count < (
            SHORT_HISTORY_THRESHOLD
        ):

            history_status = (
                "short_history"
            )

            diagnostics[
                symbol
            ] = (
                "short_history:"
                f"{row_count}"
            )

        elif row_count < (
            TARGET_HISTORY_ROWS
        ):

            history_status = (
                "partial_history"
            )

            diagnostics[
                symbol
            ] = (
                "partial_history:"
                f"{row_count}"
            )

        else:

            history_status = (
                "complete"
            )

            diagnostics.pop(
                symbol,
                None,
            )

        results[symbol] = {
            "symbol": symbol,
            "code": item["code"],
            "market": item["market"],
            "type": item["type"],
            "instrument_type":
                item["instrument_type"],
            "name": item["name"],
            "source": source,
            "history_rows": row_count,
            "history_status":
                history_status,
            "latest_date":
                rows[-1]["date"],
            "prices": rows,
        }

        log(
            f"  ✓ {row_count} 筆"
            f" / {history_status}"
            f" / {source}"
        )

    stats = {
        "start_date": start_date,
        "end_date": end_date,
        "full_rebuild": full_rebuild,
        "official_request_count":
            request_stats,
        "official_request_errors":
            official_errors,
        "official_symbol_count":
            official_count,
        "yahoo_fallback_count":
            yahoo_count,
    }

    return (
        results,
        diagnostics,
        stats,
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

    for symbol, record in (
        results.items()
    ):

        if symbol not in expected:

            malformed.append(
                (
                    symbol,
                    "not_in_universe",
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

            normalized = (
                normalize_price_row(
                    symbol,
                    row.get("date"),
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                )
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
                and row["date"] <= previous
            ):

                malformed.append(
                    (
                        symbol,
                        "date_not_increasing",
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

        "success_rate": (
            len(
                actual & expected
            )
            / len(expected)
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
    stats: Dict[str, Any],
) -> None:

    section(
        "PRICE DATA DIAGNOSTICS"
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
        f"Missing："
        f"{len(validation['missing'])}"
    )

    log(
        f"Extra："
        f"{len(validation['extra'])}"
    )

    log(
        f"Malformed："
        f"{len(validation['malformed'])}"
    )

    log(
        f"Success rate："
        f"{validation['success_rate']:.2%}"
    )

    log(
        f"Official TWSE requests："
        f"{stats['official_request_count'].get('TWSE', 0)}"
    )

    log(
        f"Official TPEx requests："
        f"{stats['official_request_count'].get('TPEX', 0)}"
    )

    log(
        f"Yahoo fallback："
        f"{stats['yahoo_fallback_count']}"
    )

    official_errors = stats.get(
        "official_request_errors",
        {}
    )

    log(
        f"Official request errors："
        f"{len(official_errors)}"
    )

    if validation["missing"]:

        log("")
        log(
            "❌ Missing 商品："
        )

        for symbol in validation[
            "missing"
        ]:

            log(
                f"  - {symbol}"
                f" → "
                f"{diagnostics.get("
                    symbol,
                    "missing_price_data"
                )}"
            )

    if validation["extra"]:

        log("")
        log(
            "❌ Extra 商品："
        )

        for symbol in validation[
            "extra"
        ]:

            log(
                f"  - {symbol}"
            )

    if validation["malformed"]:

        log("")
        log(
            "❌ Malformed："
        )

        for symbol, reason in (
            validation["malformed"]
        ):

            log(
                f"  - {symbol}"
                f" → {reason}"
            )

    if official_errors:

        log("")
        log(
            "⚠️ 官方日期 request 異常："
        )

        # 最多顯示 20 筆，避免 log 爆量。
        for key, reason in list(
            official_errors.items()
        )[:20]:

            log(
                f"  - {key}"
                f" → {reason}"
            )

        if len(official_errors) > 20:

            log(
                f"  ... "
                f"其餘 {len(official_errors) - 20} 筆省略"
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

    if set(
        stocks.keys()
    ) != set(
        expected_symbols
    ):

        raise RuntimeError(
            f"{path.name} 股票集合不一致"
        )

    for symbol, rows in (
        stocks.items()
    ):

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

        previous = ""

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol} row "
                    "不是 object"
                )

            normalized = (
                normalize_price_row(
                    symbol,
                    row.get("date"),
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    row.get("volume"),
                )
            )

            if normalized is None:

                raise RuntimeError(
                    f"{symbol} OHLCV 異常"
                )

            if (
                previous
                and row["date"] <= previous
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
        Dict[str, Any],
    ],
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, str],
    stats: Dict[str, Any],
) -> Dict[str, Any]:

    source_counts = {}
    type_counts = {}

    complete_count = 0
    partial_count = 0
    short_count = 0

    latest_dates = []

    for result in (
        results.values()
    ):

        source = result.get(
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

        record_type = result.get(
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

        status = result.get(
            "history_status"
        )

        if status == "complete":

            complete_count += 1

        elif status == "partial_history":

            partial_count += 1

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
                for x
                in results.values()
                if x.get("type")
                == "STOCK"
            ),

        "price_etf_count":
            sum(
                1
                for x
                in results.values()
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
            validation["success_rate"],

        "sources":
            source_counts,

        "types":
            type_counts,

        "diagnostics":
            diagnostics,

        "fetch_window": {
            "start":
                stats["start_date"],
            "end":
                stats["end_date"],
            "full_rebuild":
                stats["full_rebuild"],
        },

        "official_request_count":
            stats[
                "official_request_count"
            ],

        "official_request_errors":
            len(
                stats[
                    "official_request_errors"
                ]
            ),

        "yahoo_fallback_count":
            stats[
                "yahoo_fallback_count"
            ],

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
# WRITE PRICE DIRECTORY
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
    diagnostics: Dict[str, str],
    stats: Dict[str, Any],
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

        shard_files.append(
            filename
        )

    # --------------------------------------------------------
    # Validate every shard
    # --------------------------------------------------------

    for index, filename in enumerate(
        shard_files
    ):

        start = (
            index
            * STOCKS_PER_FILE
        )

        expected = symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        validate_shard(
            temp_dir / filename,
            expected,
        )

    manifest = build_manifest(
        shard_files,
        results,
        universe_stock_count,
        universe_etf_count,
        validation,
        diagnostics,
        stats,
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
# FINAL OUTPUT VALIDATION
# ============================================================

def validate_written_output(
    universe: List[Dict[str, str]],
) -> None:

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if not manifest_path.exists():

        raise RuntimeError(
            "Price manifest 不存在"
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

    universe_symbols = {
        item["symbol"]
        for item in universe
    }

    written_symbols = set()

    seen_symbols = set()

    for filename in files:

        path = (
            OUTPUT_DIR
            / Path(
                str(filename)
            ).name
        )

        if not path.exists():

            raise RuntimeError(
                f"manifest shard 不存在："
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

        duplicates = (
            seen_symbols
            & set(stocks.keys())
        )

        if duplicates:

            raise RuntimeError(
                "跨 shard 重複："
                f"{sorted(duplicates)}"
            )

        seen_symbols.update(
            stocks.keys()
        )

        written_symbols.update(
            stocks.keys()
        )

    missing = (
        universe_symbols
        - written_symbols
    )

    extra = (
        written_symbols
        - universe_symbols
    )

    if missing:

        raise RuntimeError(
            "寫入後仍缺少 Universe 商品："
            f"{sorted(missing)}"
        )

    if extra:

        raise RuntimeError(
            "寫入後出現 Universe 外商品："
            f"{sorted(extra)}"
        )

    if len(
        written_symbols
    ) != len(
        universe_symbols
    ):

        raise RuntimeError(
            "寫入後 Universe / Price "
            "數量不一致"
        )


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    section(
        "TAIWAN STOCK AI PRICE PIPELINE V12.0"
    )

    log(
        "核心："
        "官方市場批次資料 + 增量更新"
    )

    log(
        f"正常歷史目標："
        f"{TARGET_HISTORY_ROWS} 筆"
    )

    log(
        f"短歷史標記："
        f"< {SHORT_HISTORY_THRESHOLD} 筆"
    )

    log(
        "最小存在條件："
        ">= 1 筆有效 OHLCV"
    )

    log(
        "真正 missing："
        "0 筆"
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
    # FETCH
    # ========================================================

    (
        results,
        diagnostics,
        stats,
    ) = build_results(
        universe
    )

    # ========================================================
    # VALIDATE RESULTS
    # ========================================================

    validation = validate_results(
        results,
        universe,
    )

    print_diagnostics(
        validation,
        diagnostics,
        stats,
    )

    # --------------------------------------------------------
    # 絕不允許：
    #
    # malformed
    # extra
    #
    # missing 暫時保留到 diagnostics，
    # 但最後 Price pipeline 必須 FAIL。
    # --------------------------------------------------------

    if validation["malformed"]:

        raise RuntimeError(
            "Price results 包含無效資料結構"
        )

    if validation["extra"]:

        raise RuntimeError(
            "Price results 包含 Universe 外商品"
        )

    # ========================================================
    # WRITE TEMPORARY
    # ========================================================

    section(
        "WRITE PRICE DATA"
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
            diagnostics,
            stats,
        )

        # ----------------------------------------------------
        # Temporary output validation
        # ----------------------------------------------------

        expected_symbols = {
            item["symbol"]
            for item in universe
        }

        written_symbols = set()

        manifest = load_json(
            temp_output
            / "manifest.json"
        )

        for filename in manifest[
            "files"
        ]:

            shard = load_json(
                temp_output
                / filename
            )

            stocks = shard[
                "stocks"
            ]

            duplicates = (
                written_symbols
                & set(stocks.keys())
            )

            if duplicates:

                raise RuntimeError(
                    "temporary shard "
                    "出現跨 shard 重複："
                    f"{sorted(duplicates)}"
                )

            written_symbols.update(
                stocks.keys()
            )

        missing_after_write = (
            expected_symbols
            - written_symbols
        )

        extra_after_write = (
            written_symbols
            - expected_symbols
        )

        if extra_after_write:

            raise RuntimeError(
                "temporary output 出現 "
                "Universe 外商品："
                f"{sorted(extra_after_write)}"
            )

        if missing_after_write:

            raise RuntimeError(
                "temporary output 仍缺少商品："
                f"{sorted(missing_after_write)}"
            )

        # ----------------------------------------------------
        # Atomic replacement
        # ----------------------------------------------------

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
    # FINAL OUTPUT VALIDATION
    # ========================================================

    section(
        "FINAL PRICE DATA VALIDATION"
    )

    validate_written_output(
        universe
    )

    # ========================================================
    # FINAL CONTRACT
    # ========================================================

    final_count = len(
        results
    )

    short_count = sum(
        1
        for record
        in results.values()
        if record.get(
            "history_status"
        ) == "short_history"
    )

    partial_count = sum(
        1
        for record
        in results.values()
        if record.get(
            "history_status"
        ) == "partial_history"
    )

    complete_count = sum(
        1
        for record
        in results.values()
        if record.get(
            "history_status"
        ) == "complete"
    )

    section(
        "PRICE PIPELINE RESULT"
    )

    log(
        f"Universe："
        f"{len(universe)}"
    )

    log(
        f"Price："
        f"{final_count}"
    )

    log(
        f"Complete history："
        f"{complete_count}"
    )

    log(
        f"Partial history："
        f"{partial_count}"
    )

    log(
        f"Short history："
        f"{short_count}"
    )

    log(
        f"Missing："
        f"{len(validation['missing'])}"
    )

    log(
        f"Official request："
        f"TWSE="
        f"{stats['official_request_count'].get('TWSE', 0)} "
        f"/ TPEx="
        f"{stats['official_request_count'].get('TPEX', 0)}"
    )

    log(
        f"Yahoo fallback："
        f"{stats['yahoo_fallback_count']}"
    )

    # --------------------------------------------------------
    # 最終硬契約
    # --------------------------------------------------------

    if validation["missing"]:

        log("")
        log(
            "❌ 最終仍缺少："
        )

        for symbol in validation[
            "missing"
        ]:

            log(
                f"  {symbol}"
                f" → "
                f"{diagnostics.get("
                    symbol,
                    "missing_price_data"
                )}"
            )

        raise RuntimeError(
            "Universe 與 Price "
            "仍未完整對接"
        )

    if final_count != len(
        universe
    ):

        raise RuntimeError(
            "Price count 與 Universe "
            "不一致"
        )

    # ========================================================
    # PASS
    # ========================================================

    section(
        "PRICE PIPELINE PASS"
    )

    log(
        "✓ Universe 是唯一商品來源"
    )

    log(
        "✓ 官方資料採市場批次抓取"
    )

    log(
        "✓ 不再每商品 × 每日 HTTP"
    )

    log(
        "✓ 已有歷史商品採增量更新"
    )

    log(
        "✓ 新商品自動初始化"
    )

    log(
        "✓ 歷史不足不會 silently drop"
    )

    log(
        "✓ >= 1 筆有效價格即可存在"
    )

    log(
        "✓ short_history 只代表歷史不足"
    )

    log(
        "✓ Yahoo 僅作最後 fallback"
    )

    log(
        "✓ OHLCV validation PASS"
    )

    log(
        "✓ shard validation PASS"
    )

    log(
        "✓ manifest validation PASS"
    )

    log(
        "✓ final Universe / Price validation PASS"
    )

    log(
        "✓ Universe → Price 完整對接"
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
