#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

PRICE PIPELINE V13.0
============================================================

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只接受 active STOCK / ETF
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. TWSE / TPEx 官方資料優先
7. 官方資料採「日期批次」抓取
8. 禁止「商品 × 日期」HTTP
9. Yahoo 僅作真正無官方資料商品的最後 fallback
10. >= 1 筆有效 OHLCV 即必須保留
11. < 20 筆只能標記 short_history，不得排除
12. 0 筆才是真正 missing
13. 既有 Price history 優先保留
14. 新 Universe 商品自動加入
15. Price 不得出現 Universe 外商品
16. shard / manifest 必須完全一致
17. temporary directory
18. atomic replace
19. 每個 HTTP request 都有 timeout
20. 不允許單一 request 無限卡住
21. 最終 Universe == Price 才 PASS

============================================================
V13 架構
============================================================

Universe
   │
   ├── TWSE symbols
   │
   └── TPEx symbols
          │
          ▼
   判斷需要補歷史的日期
          │
          ▼
   TWSE：每個日期一次
   TPEx：每個日期一次
          │
          ▼
   官方批次資料
          │
          ▼
   Universe symbol 對接
          │
          ▼
   existing + official
          │
          ▼
   >= 1 筆 → Price
   0 筆    → missing
          │
          ▼
   shard validation
          │
          ▼
   manifest validation
          │
          ▼
   atomic replace
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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V13.0"
SCHEMA_VERSION = "prices-v13.0"


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

# 新商品 / 歷史不足商品需要補歷史時，
# 往前抓這麼多日曆日。
INITIAL_LOOKBACK_CALENDAR_DAYS = 180

# 已有完整歷史時，只補近期交易日。
RECENT_REFRESH_CALENDAR_DAYS = 10

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

# 絕對禁止無限等待。
REQUEST_TIMEOUT = 20

RETRY_DELAY = 1.5

REQUEST_DELAY = 0.08


# ============================================================
# FALLBACK
# ============================================================

YAHOO_ENABLED = True

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


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

        except ValueError:
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

            except ValueError:
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

    close_price = safe_float(
        close
    )

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

    # 官方資料部分商品可能缺少 OHLC，
    # 允許使用 close 補齊。
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
) -> Tuple[
    Optional[Any],
    Optional[str],
]:

    last_error = "unknown_error"

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

            last_error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY
                )

    return (
        None,
        last_error,
    )


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
    seen: Set[str] = set()

    for key, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                f"Universe {key} "
                "不是 object"
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
                "symbol 無效"
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

        instrument_type = clean_text(
            item.get(
                "instrument_type"
            )
        )

        record_type = clean_text(
            item.get(
                "type"
            )
        )

        result.append(
            {
                "symbol": symbol,
                "code": symbol,
                "name": clean_text(
                    item.get(
                        "name"
                    )
                ),
                "market": market,
                "type": record_type,
                "instrument_type":
                    instrument_type,
            }
        )

    if not result:

        raise RuntimeError(
            "Universe 為 0"
        )

    return result


# ============================================================
# EXISTING PRICE DATA
# ============================================================

def load_existing_prices() -> Dict[
    str,
    List[Dict[str, Any]],
]:

    result: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

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

        path = (
            OUTPUT_DIR
            / Path(
                str(filename)
            ).name
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

            normalized_symbol = (
                normalize_symbol(
                    symbol
                )
            )

            if not normalized_symbol:
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
                        normalized_symbol,
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

            deduped = {}

            for row in clean_rows:

                deduped[
                    row["date"]
                ] = row

            final_rows = sorted(
                deduped.values(),
                key=lambda x: x["date"],
            )

            if final_rows:

                result[
                    normalized_symbol
                ] = final_rows[
                    -MAX_HISTORY_ROWS:
                ]

    return result


# ============================================================
# DATE RANGE
# ============================================================

def weekday_dates(
    start_date: str,
    end_date: str,
) -> List[str]:

    start = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    ).date()

    end = datetime.strptime(
        end_date,
        "%Y-%m-%d",
    ).date()

    result = []

    current = start

    while current <= end:

        # 0 = Monday
        # 6 = Sunday
        if current.weekday() < 5:

            result.append(
                current.strftime(
                    "%Y-%m-%d"
                )
            )

        current += timedelta(
            days=1
        )

    return result


def determine_fetch_start(
    universe: List[Dict[str, str]],
    existing: Dict[
        str,
        List[Dict[str, Any]],
    ],
    today: datetime,
) -> str:

    # --------------------------------------------------------
    # 只要有任何 Universe 商品：
    #
    # - 完全沒有歷史
    # - 歷史不足 90 筆
    #
    # 就進入 bootstrap mode。
    #
    # 這時整個市場只按日期批次抓一次。
    # 不是逐商品抓。
    # --------------------------------------------------------

    need_bootstrap = False

    for item in universe:

        symbol = item["symbol"]

        rows = existing.get(
            symbol,
            [],
        )

        if len(rows) < TARGET_HISTORY_ROWS:

            need_bootstrap = True
            break

    if need_bootstrap:

        return (
            today
            - timedelta(
                days=INITIAL_LOOKBACK_CALENDAR_DAYS
            )
        ).strftime(
            "%Y-%m-%d"
        )

    return (
        today
        - timedelta(
            days=RECENT_REFRESH_CALENDAR_DAYS
        )
    ).strftime(
        "%Y-%m-%d"
    )


# ============================================================
# TWSE BATCH
# ============================================================

def fetch_twse_batch(
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

    if data is None:

        return (
            {},
            error,
        )

    result = {}

    if not isinstance(
        data,
        dict,
    ):

        return (
            {},
            "response_not_object",
        )

    tables = data.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):

        return (
            {},
            "tables_missing",
        )

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
            clean_text(field): index
            for index, field
            in enumerate(fields)
        }

        def value(
            row: List[Any],
            names: Iterable[str],
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
                value(
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
                    value(
                        row,
                        (
                            "開盤價",
                            "開盤",
                        ),
                    ),
                    value(
                        row,
                        (
                            "最高價",
                            "最高",
                        ),
                    ),
                    value(
                        row,
                        (
                            "最低價",
                            "最低",
                        ),
                    ),
                    value(
                        row,
                        (
                            "收盤價",
                            "收盤",
                        ),
                    ),
                    value(
                        row,
                        (
                            "成交股數",
                            "成交量",
                        ),
                    ),
                )
            )

            if normalized:

                result[
                    symbol
                ] = normalized

    return (
        result,
        None,
    )


# ============================================================
# TPEx BATCH
# ============================================================

def fetch_tpex_batch(
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
            error,
        )

    if not isinstance(
        data,
        dict,
    ):

        return (
            {},
            "response_not_object",
        )

    aa_data = data.get(
        "aaData"
    )

    if not isinstance(
        aa_data,
        list,
    ):

        return (
            {},
            "aaData_missing",
        )

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

            result[
                symbol
            ] = normalized

    return (
        result,
        None,
    )


# ============================================================
# OFFICIAL BATCH HISTORY
# ============================================================

def fetch_official_history(
    universe: List[Dict[str, str]],
    existing: Dict[
        str,
        List[Dict[str, Any]],
    ],
    start_date: str,
    end_date: str,
) -> Tuple[
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, str],
]:

    section(
        "OFFICIAL BATCH FETCH"
    )

    dates = weekday_dates(
        start_date,
        end_date,
    )

    twse_symbols = {
        item["symbol"]
        for item in universe
        if item["market"] == "TWSE"
    }

    tpex_symbols = {
        item["symbol"]
        for item in universe
        if item["market"] == "TPEX"
    }

    log(
        f"日期範圍："
        f"{start_date} ~ {end_date}"
    )

    log(
        f"工作日批次："
        f"{len(dates)}"
    )

    log(
        f"TWSE Universe："
        f"{len(twse_symbols)}"
    )

    log(
        f"TPEx Universe："
        f"{len(tpex_symbols)}"
    )

    log(
        "HTTP 模式：日期批次"
    )

    official: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ] = {
        "TWSE": {},
        "TPEX": {},
    }

    diagnostics: Dict[str, str] = {}

    total_requests = 0
    successful_requests = 0

    for index, date_text in enumerate(
        dates,
        start=1,
    ):

        # ----------------------------------------------------
        # TWSE
        # ----------------------------------------------------

        if twse_symbols:

            log(
                f"[{index}/{len(dates)}] "
                f"TWSE {date_text}"
            )

            twse_data, twse_error = (
                fetch_twse_batch(
                    date_text
                )
            )

            total_requests += 1

            if twse_data:

                successful_requests += 1

                for symbol in (
                    twse_symbols
                    & set(twse_data.keys())
                ):

                    official[
                        "TWSE"
                    ].setdefault(
                        symbol,
                        {}
                    )[
                        date_text
                    ] = twse_data[
                        symbol
                    ]

            elif twse_error:

                diagnostics[
                    f"TWSE:{date_text}"
                ] = twse_error

            time.sleep(
                REQUEST_DELAY
            )

        # ----------------------------------------------------
        # TPEx
        # ----------------------------------------------------

        if tpex_symbols:

            log(
                f"[{index}/{len(dates)}] "
                f"TPEx {date_text}"
            )

            tpex_data, tpex_error = (
                fetch_tpex_batch(
                    date_text
                )
            )

            total_requests += 1

            if tpex_data:

                successful_requests += 1

                for symbol in (
                    tpex_symbols
                    & set(tpex_data.keys())
                ):

                    official[
                        "TPEX"
                    ].setdefault(
                        symbol,
                        {}
                    )[
                        date_text
                    ] = tpex_data[
                        symbol
                    ]

            elif tpex_error:

                diagnostics[
                    f"TPEx:{date_text}"
                ] = tpex_error

            time.sleep(
                REQUEST_DELAY
            )

    log("")
    log(
        f"官方 HTTP requests："
        f"{total_requests}"
    )

    log(
        f"成功 request："
        f"{successful_requests}"
    )

    if total_requests:

        log(
            "官方 request 成功率："
            f"{successful_requests / total_requests:.2%}"
        )

    return (
        official,
        diagnostics,
    )


# ============================================================
# YAHOO
# ============================================================

def yahoo_symbol(
    item: Dict[str, str],
) -> str:

    if item["market"] == "TWSE":

        return (
            f'{item["symbol"]}.TW'
        )

    if item["market"] == "TPEX":

        return (
            f'{item["symbol"]}.TWO'
        )

    return item["symbol"]


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
        "includeAdjustedClose": "true",
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
# BUILD RESULTS
# ============================================================

def build_results(
    universe: List[Dict[str, str]],
    existing: Dict[
        str,
        List[Dict[str, Any]],
    ],
    official: Dict[
        str,
        Dict[str, Dict[str, Any]]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, str],
]:

    section(
        "BUILD PRICE RESULTS"
    )

    universe_by_symbol = {
        item["symbol"]: item
        for item in universe
    }

    results: Dict[
        str,
        Dict[str, Any]
    ] = {}

    diagnostics: Dict[str, str] = {}

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
            Dict[str, Any]
        ] = {}

        # ----------------------------------------------------
        # 1. existing
        # ----------------------------------------------------

        for row in existing.get(
            symbol,
            [],
        ):

            row_map[
                row["date"]
            ] = row

        # ----------------------------------------------------
        # 2. official
        # ----------------------------------------------------

        market_data = official.get(
            item["market"],
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

        # ----------------------------------------------------
        # 3. final rows
        # ----------------------------------------------------

        final_rows = sorted(
            row_map.values(),
            key=lambda x: x["date"],
        )

        final_rows = final_rows[
            -MAX_HISTORY_ROWS:
        ]

        source = (
            "TWSE official"
            if item["market"] == "TWSE"
            else "TPEx official"
        )

        official_count = len(
            symbol_data
        )

        # ----------------------------------------------------
        # 4. Yahoo fallback
        #
        # 只有：
        #
        # existing == 0
        # AND
        # official == 0
        #
        # 才允許 Yahoo。
        #
        # 絕不因歷史不足 20 筆就丟 Yahoo。
        # ----------------------------------------------------

        if (
            len(final_rows) == 0
            and official_count == 0
            and YAHOO_ENABLED
        ):

            log(
                f"  → {symbol} "
                "官方 0 筆，Yahoo fallback"
            )

            yahoo_rows = (
                fetch_yahoo_history(
                    item,
                    (
                        datetime.now(
                            timezone.utc
                        )
                        - timedelta(
                            days=
                            INITIAL_LOOKBACK_CALENDAR_DAYS
                        )
                    ).strftime(
                        "%Y-%m-%d"
                    ),
                    datetime.now(
                        timezone.utc
                    ).strftime(
                        "%Y-%m-%d"
                    ),
                )
            )

            if yahoo_rows:

                for row in yahoo_rows:

                    row_map[
                        row["date"]
                    ] = row

                final_rows = sorted(
                    row_map.values(),
                    key=lambda x: x["date"],
                )[
                    -MAX_HISTORY_ROWS:
                ]

                source = (
                    "Yahoo fallback"
                )

        # ----------------------------------------------------
        # 5. 真正 missing
        # ----------------------------------------------------

        if not final_rows:

            diagnostics[
                symbol
            ] = (
                "no_valid_price_data"
            )

            log(
                "  ❌ 無有效價格資料"
            )

            continue

        # ----------------------------------------------------
        # 6. history status
        # ----------------------------------------------------

        row_count = len(
            final_rows
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
                f"short_history:{row_count}"
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
                f"partial_history:{row_count}"
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
            "name": item["name"],
            "source": source,
            "history_rows": row_count,
            "history_status": history_status,
            "latest_date": (
                final_rows[-1]["date"]
            ),
            "prices": final_rows,
        }

        log(
            f"  ✓ {row_count} 筆"
            f" / {history_status}"
            f" / {source}"
        )

    return (
        results,
        diagnostics,
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

            date_text = row.get(
                "date"
            )

            if (
                previous_date
                and date_text
                <= previous_date
            ):

                malformed.append(
                    (
                        symbol,
                        "date_not_increasing",
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

    success_rate = (
        len(actual & expected)
        / len(expected)
        if expected
        else 0.0
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
        "success_rate":
            success_rate,
    }


# ============================================================
# SHARDS
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any]
    ],
) -> List[
    Tuple[str, Dict[str, Any]]
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

            stocks[symbol] = (
                results[symbol]["prices"]
            )

        filename = (
            f"prices_"
            f"{start // STOCKS_PER_FILE + 1:03d}"
            f".json"
        )

        output.append(
            (
                filename,
                {
                    "stocks": stocks
                },
            )
        )

    return output


# ============================================================
# SHARD VALIDATION
# ============================================================

def validate_shard(
    path: Path,
    expected_symbols: Set[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            f"找不到 shard："
            f"{path.name}"
        )

    if (
        path.stat().st_size
        > MAX_FILE_SIZE_BYTES
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

    actual_symbols = set(
        stocks.keys()
    )

    if actual_symbols != expected_symbols:

        missing = sorted(
            expected_symbols
            - actual_symbols
        )

        extra = sorted(
            actual_symbols
            - expected_symbols
        )

        raise RuntimeError(
            f"{path.name} 集合錯誤 "
            f"missing={missing[:10]} "
            f"extra={extra[:10]}"
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
                f"{symbol} 0 筆價格"
            )

        previous_date = ""

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
                previous_date
                and row["date"]
                <= previous_date
            ):

                raise RuntimeError(
                    f"{symbol} 日期排序錯誤"
                )

            previous_date = row["date"]


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

    source_counts: Dict[
        str,
        int
    ] = {}

    type_counts: Dict[
        str,
        int
    ] = {}

    complete_count = 0
    partial_count = 0
    short_count = 0

    latest_dates = []

    for record in results.values():

        source = record.get(
            "source",
            "unknown",
        )

        source_counts[
            source
        ] = (
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

        type_counts[
            record_type
        ] = (
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

        else:

            short_count += 1

        latest_date = record.get(
            "latest_date"
        )

        if latest_date:
            latest_dates.append(
                latest_date
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
                for record
                in results.values()
                if record.get("type")
                == "STOCK"
            ),

        "price_etf_count":
            sum(
                1
                for record
                in results.values()
                if record.get("type")
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
            validation[
                "success_rate"
            ],

        "sources":
            source_counts,

        "types":
            type_counts,

        "diagnostics":
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
    expected_symbols: Set[str],
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

    missing_symbols = set(
        manifest.get(
            "missing_symbols",
            []
        )
    )

    if missing_symbols:

        raise RuntimeError(
            "manifest 仍存在 missing："
            f"{sorted(missing_symbols)}"
        )


# ============================================================
# WRITE DIRECTORY
# ============================================================

def write_price_directory(
    temp_output: Path,
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, str],
) -> None:

    temp_output.mkdir(
        parents=True,
        exist_ok=True,
    )

    shard_pairs = build_shards(
        results
    )

    shard_files = [
        filename
        for filename, _
        in shard_pairs
    ]

    symbols = set(
        results.keys()
    )

    for filename, shard in (
        shard_pairs
    ):

        path = (
            temp_output
            / filename
        )

        save_json(
            path,
            shard,
        )

    # --------------------------------------------------------
    # Validate every shard
    # --------------------------------------------------------

    for filename, shard in (
        shard_pairs
    ):

        expected = set(
            shard["stocks"].keys()
        )

        validate_shard(
            temp_output / filename,
            expected,
        )

    # --------------------------------------------------------
    # Manifest
    # --------------------------------------------------------

    manifest = build_manifest(
        shard_files,
        results,
        universe_stock_count,
        universe_etf_count,
        validation,
        diagnostics,
    )

    manifest_path = (
        temp_output
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
# FINAL DIRECTORY VALIDATION
# ============================================================

def validate_written_directory(
    output_dir: Path,
    universe: List[Dict[str, str]],
) -> None:

    manifest_path = (
        output_dir
        / "manifest.json"
    )

    if not manifest_path.exists():

        raise RuntimeError(
            "寫入後找不到 manifest.json"
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

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set()

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

        actual.update(
            stocks.keys()
        )

    missing = expected - actual
    extra = actual - expected

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

    if len(actual) != len(expected):

        raise RuntimeError(
            "寫入後 Universe / Price "
            "數量不一致"
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
        "TAIWAN STOCK AI PRICE PIPELINE V13.0"
    )

    log(
        "架構：官方日期批次 → Universe 對接"
    )

    log(
        "禁止：商品 × 日期 HTTP"
    )

    log(
        f"目標歷史："
        f"{TARGET_HISTORY_ROWS} 筆"
    )

    log(
        f"短歷史門檻："
        f"{SHORT_HISTORY_THRESHOLD} 筆"
    )

    log(
        "最小存在條件："
        "1 筆有效 OHLCV"
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
    # EXISTING
    # ========================================================

    section(
        "LOAD EXISTING PRICE DATA"
    )

    existing = load_existing_prices()

    log(
        f"Existing Price symbols："
        f"{len(existing)}"
    )

    # ========================================================
    # DETERMINE RANGE
    # ========================================================

    today = datetime.now(
        timezone.utc
    )

    start_date = determine_fetch_start(
        universe,
        existing,
        today,
    )

    end_date = today.strftime(
        "%Y-%m-%d"
    )

    bootstrap = any(
        len(
            existing.get(
                item["symbol"],
                []
            )
        ) < TARGET_HISTORY_ROWS
        for item in universe
    )

    if bootstrap:

        log(
            "模式：BOOTSTRAP / HISTORY REPAIR"
        )

    else:

        log(
            "模式：RECENT REFRESH"
        )

    log(
        f"官方抓取範圍："
        f"{start_date} ~ {end_date}"
    )

    # ========================================================
    # OFFICIAL BATCH
    # ========================================================

    official, fetch_diagnostics = (
        fetch_official_history(
            universe,
            existing,
            start_date,
            end_date,
        )
    )

    # ========================================================
    # BUILD
    # ========================================================

    results, diagnostics = (
        build_results(
            universe,
            existing,
            official,
        )
    )

    for key, value in (
        fetch_diagnostics.items()
    ):

        diagnostics[
            f"official:{key}"
        ] = value

    # ========================================================
    # VALIDATE RESULTS
    # ========================================================

    validation = validate_results(
        results,
        universe,
    )

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
        f"Universe → Price："
        f"{validation['actual_count']}"
        f"/"
        f"{validation['expected_count']}"
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

    if validation["missing"]:

        log("")
        log(
            "❌ Missing："
        )

        for symbol in (
            validation["missing"]
        ):

            log(
                f"  {symbol}"
                f" → "
                f"{diagnostics.get("
                    symbol,
                    "no_valid_price_data"
                )}"
            )

    if validation["extra"]:

        log("")
        log(
            "❌ Extra："
        )

        for symbol in (
            validation["extra"]
        ):

            log(
                f"  {symbol}"
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
                f"  {symbol}"
                f" → {reason}"
            )

        raise RuntimeError(
            "Price results 存在 "
            "無效資料結構"
        )

    if validation["extra"]:

        raise RuntimeError(
            "Price results 出現 "
            "Universe 外商品"
        )

    # ========================================================
    # IMPORTANT:
    #
    # missing 不允許寫入假資料。
    #
    # 因此：
    #
    # 0 筆商品 → 不會被偽造
    #             → workflow 明確 FAIL
    #
    # 這樣才能真正找出官方資料問題。
    # ========================================================

    if validation["missing"]:

        raise RuntimeError(
            "Universe 與 Price 尚未完整對接；"
            "請查看上述 Missing 清單"
        )

    # ========================================================
    # WRITE
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
            prefix=".prices_v13_",
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
        )

        validate_written_directory(
            temp_output,
            universe,
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
    # FINAL
    # ========================================================

    complete_count = sum(
        1
        for record in results.values()
        if record.get(
            "history_status"
        ) == "complete"
    )

    partial_count = sum(
        1
        for record in results.values()
        if record.get(
            "history_status"
        ) == "partial_history"
    )

    short_count = sum(
        1
        for record in results.values()
        if record.get(
            "history_status"
        ) == "short_history"
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
        f"{len(results)}"
    )

    log(
        f"Complete："
        f"{complete_count}"
    )

    log(
        f"Partial："
        f"{partial_count}"
    )

    log(
        f"Short："
        f"{short_count}"
    )

    log(
        f"Missing："
        f"{len(validation['missing'])}"
    )

    if (
        len(results)
        != len(universe)
    ):

        raise RuntimeError(
            "Universe 與 Price count 不一致"
        )

    section(
        "PRICE PIPELINE PASS"
    )

    log(
        "✓ Universe 是唯一商品來源"
    )

    log(
        "✓ active STOCK / ETF 全部進入 pipeline"
    )

    log(
        "✓ 官方資料採日期批次"
    )

    log(
        "✓ 不使用商品 × 日期 HTTP"
    )

    log(
        "✓ 歷史不足 20 日不排除"
    )

    log(
        "✓ >= 1 筆有效 OHLCV 即保留"
    )

    log(
        "✓ 真正 0 筆才是 missing"
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