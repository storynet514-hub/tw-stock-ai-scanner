#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

PRICE PIPELINE V12.0
============================================================

本檔案唯一責任：
    Data/universe.json
        ↓
    官方 TWSE / TPEx 歷史價格批次資料
        ↓
    Existing Price 歷史資料
        ↓
    Yahoo 僅補官方缺口
        ↓
    Price Results
        ↓
    Shards
        ↓
    Manifest
        ↓
    完整 Validation
        ↓
    Atomic Replace
        ↓
    FINAL DISK VALIDATION

核心契約
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. 只接受 active STOCK / ETF
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. TWSE / TPEx 官方資料優先
7. 官方資料以市場 / 日期批次抓取
8. 不逐股票逐日期呼叫官方 API
9. Yahoo 不負責建立 Universe
10. Yahoo 只負責補官方資料缺口
11. 官方資料存在時，官方資料優先於 Yahoo
12. Existing Price 只作歷史連續性補充
13. >= 1 筆有效 OHLCV 就必須保留
14. < 20 筆 = short_history
15. 20~89 筆 = partial_history
16. >= 90 筆 = complete
17. 最多保存 90 筆
18. 官方不足 90 筆時，必須嘗試 Yahoo 補缺口
19. 官方 API 失敗必須留下 diagnostics
20. Yahoo fallback 成功 / 失敗必須留下 diagnostics
21. 0 筆才是真正 missing
22. Universe / Price 集合必須完整一致
23. 不允許 Price 出現 Universe 外商品
24. 不允許跨 shard 重複
25. shard 必須與 results 完整一致
26. manifest 必須與實際 shard 完整一致
27. 任一 validation FAIL，不破壞舊 Data/prices
28. 所有 validation PASS 後才 atomic replace
29. Atomic replace 後再次從磁碟重新讀取驗證
30. 舊 schema / 壞 shard 自動忽略
31. 不因歷史不足 silently drop 商品
32. 不對 2300+ 股票逐檔呼叫官方歷史 API
33. Yahoo 只會對官方資料不足的商品啟動
34. 官方資料優先、Yahoo 補洞
35. 所有資料來源狀態可觀測

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

# 90 個交易日約需 125~140 個 calendar days。
# 留足市場假日 / 長假 / API 缺資料空間。
LOOKBACK_CALENDAR_DAYS = 240

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

REQUEST_DELAY = 0.08


# ============================================================
# FALLBACK
# ============================================================

# 官方資料少於這個數量就必須檢查 Yahoo 補洞。
YAHOO_FILL_TRIGGER = TARGET_HISTORY_ROWS

# Yahoo 單一商品最多補幾次。
# 1 次 request 可取得整段歷史，因此不是逐日期抓取。
YAHOO_MAX_RETRIES = 3


# ============================================================
# OFFICIAL ENDPOINTS
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/afterTrading/MI_INDEX"
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
        "None",
        "null",
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
                : -len(suffix)
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
    retries: int = MAX_RETRIES,
) -> Tuple[Any, Optional[str]]:

    last_error = None

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json(), None

        except Exception as exc:

            last_error = (
                f"{type(exc).__name__}: {exc}"
            )

            if attempt < retries:

                time.sleep(
                    RETRY_DELAY * attempt
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

        if item.get("status") != "active":

            raise RuntimeError(
                f"Universe {key} "
                "status 不是 active"
            )

        symbol = normalize_symbol(
            item.get("symbol")
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
# TWSE DAILY BATCH
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
    )

    if error:

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
# TPEX DAILY BATCH
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
        "se": "EW",
    }

    data, error = http_get_json(
        TPEX_DAILY_URL,
        params,
    )

    if error:

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
        
    if not aa_data:
        
         return {}, "aaData_empty"

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
# OFFICIAL COLLECTION
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
        "trading_days_attempted": 0,
        "twse_success_days": 0,
        "twse_failed_days": 0,
        "tpex_success_days": 0,
        "tpex_failed_days": 0,
        "twse_errors": {},
        "tpex_errors": {},
    }

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

            diagnostics[
                "trading_days_attempted"
            ] += 1

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
                    "twse_failed_days"
                ] += 1

                diagnostics[
                    "twse_errors"
                ][date_text] = twse_error

            else:

                diagnostics[
                    "twse_success_days"
                ] += 1

            if tpex_error:

                diagnostics[
                    "tpex_failed_days"
                ] += 1

                diagnostics[
                    "tpex_errors"
                ][date_text] = tpex_error

            else:

                diagnostics[
                    "tpex_success_days"
                ] += 1

            target_twse = (
                universe_by_market[
                    "TWSE"
                ]
            )

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

            target_tpex = (
                universe_by_market[
                    "TPEX"
                ]
            )

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

    return collected, diagnostics


# ============================================================
# YAHOO SYMBOL
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


# ============================================================
# YAHOO HISTORY
# ============================================================

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
        "includeAdjustedClose":
            "true",
    }

    data, error = http_get_json(
        url,
        params,
        retries=YAHOO_MAX_RETRIES,
    )

    if error:

        return [], error

    try:

        chart = data["chart"]

        if chart.get("error"):

            return [], (
                f"yahoo_chart_error:"
                f"{chart['error']}"
            )

        result = chart[
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
            f"yahoo_invalid_response:"
            f"{type(exc).__name__}"
        )

    if not isinstance(
        timestamps,
        list,
    ):

        return [], "yahoo_timestamp_missing"

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

        open_value = (
            opens[index]
            if index < len(opens)
            else None
        )

        high_value = (
            highs[index]
            if index < len(highs)
            else None
        )

        low_value = (
            lows[index]
            if index < len(lows)
            else None
        )

        close_value = (
            closes[index]
            if index < len(closes)
            else None
        )

        volume_value = (
            volumes[index]
            if index < len(volumes)
            else None
        )

        normalized = normalize_price_row(
            item["symbol"],
            date_text,
            open_value,
            high_value,
            low_value,
            close_value,
            volume_value,
        )

        if normalized:

            rows.append(
                normalized
            )

    rows.sort(
        key=lambda x: x["date"]
    )

    return rows, None


# ============================================================
# MERGE PRICE ROWS
# ============================================================

def merge_rows(
    base_rows: List[Dict[str, Any]],
    supplement_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    merged = {}

    for row in base_rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        date_text = normalize_date(
            row.get("date")
        )

        if not date_text:
            continue

        normalized = normalize_price_row(
            "",
            date_text,
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("volume"),
        )

        if normalized:

            merged[
                date_text
            ] = normalized

    for row in supplement_rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        date_text = normalize_date(
            row.get("date")
        )

        if not date_text:
            continue

        normalized = normalize_price_row(
            "",
            date_text,
            row.get("open"),
            row.get("high"),
            row.get("low"),
            row.get("close"),
            row.get("volume"),
        )

        if normalized:

            # supplement 只補不存在日期。
            # 已有官方 / existing 日期不覆蓋。
            if date_text not in merged:

                merged[
                    date_text
                ] = normalized

    return sorted(
        merged.values(),
        key=lambda x: x["date"],
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

    existing = load_existing_prices()

    official, official_diagnostics = (
        collect_official_market_data(
            universe,
            start_date,
            end_date,
        )
    )

    results = {}
    diagnostics = {}

    source_counts = {}

    fallback_attempted = 0
    fallback_success = 0
    fallback_failed = 0

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

        # ----------------------------------------------------
        # 1. Existing
        # ----------------------------------------------------

        existing_rows = existing.get(
            symbol,
            []
        )

        # ----------------------------------------------------
        # 2. Official
        # ----------------------------------------------------

        official_rows = list(
            official[
                item["market"]
            ].get(
                symbol,
                {}
            ).values()
        )

        official_rows.sort(
            key=lambda x: x["date"]
        )

        # ----------------------------------------------------
        # 官方優先。
        #
        # Existing 只補官方不存在的日期。
        # ----------------------------------------------------

        merged = merge_rows(
            official_rows,
            existing_rows,
        )

        source = "official"

        official_count = len(
            official_rows
        )

        existing_count = len(
            existing_rows
        )

        # ----------------------------------------------------
        # 3. Yahoo 補洞
        #
        # 只要最終資料 < 90 就啟動。
        #
        # Yahoo 不覆蓋官方 / existing。
        # ----------------------------------------------------

        yahoo_rows = []

        if len(merged) < (
            YAHOO_FILL_TRIGGER
        ):

            fallback_attempted += 1

            log(
                f"  → 官方/既有資料 "
                f"{len(merged)} 筆，"
                f"啟動 Yahoo 補缺口"
            )

            yahoo_rows, yahoo_error = (
                fetch_yahoo_history(
                    item,
                    start_date,
                    end_date,
                )
            )

            if yahoo_rows:

                before_count = len(
                    merged
                )

                merged = merge_rows(
                    merged,
                    yahoo_rows,
                )

                added_count = (
                    len(merged)
                    - before_count
                )

                if added_count > 0:

                    fallback_success += 1

                    if official_count > 0:

                        source = (
                            "official+Yahoo supplement"
                        )

                    elif existing_count > 0:

                        source = (
                            "existing+Yahoo supplement"
                        )

                    else:

                        source = (
                            "Yahoo fallback"
                        )

                    log(
                        f"  → Yahoo取得 "
                        f"{len(yahoo_rows)} 筆，"
                        f"補入 {added_count} 筆"
                    )

                else:

                    fallback_failed += 1

                    diagnostics[
                        symbol
                    ] = (
                        "yahoo_no_new_dates"
                    )

                    log(
                        "  ⚠ Yahoo 有資料，"
                        "但沒有新增日期"
                    )

            else:

                fallback_failed += 1

                diagnostics[
                    symbol
                ] = (
                    "yahoo_failed:"
                    f"{yahoo_error or 'unknown'}"
                )

                log(
                    "  ❌ Yahoo fallback 失敗："
                    f"{yahoo_error or 'unknown'}"
                )

        # ----------------------------------------------------
        # 4. 最終只保存最近 90 筆
        # ----------------------------------------------------

        merged = sorted(
            merged,
            key=lambda x: x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        # ----------------------------------------------------
        # 5. 0 rows = 真 missing
        # ----------------------------------------------------

        if not merged:

            diagnostics[
                symbol
            ] = (
                diagnostics.get(
                    symbol,
                    "no_valid_price_data"
                )
            )

            log(
                "  ❌ 0 筆：真正 missing"
            )

            continue

        history_count = len(
            merged
        )

        # ----------------------------------------------------
        # 6. History status
        # ----------------------------------------------------

        if history_count < (
            SHORT_HISTORY_THRESHOLD
        ):

            history_status = (
                "short_history"
            )

            diagnostics[
                symbol
            ] = (
                f"short_history:"
                f"{history_count}"
            )

        elif history_count < (
            TARGET_HISTORY_ROWS
        ):

            history_status = (
                "partial_history"
            )

            diagnostics[
                symbol
            ] = (
                f"partial_history:"
                f"{history_count}"
            )

        else:

            history_status = "complete"

            # complete 仍可能有 fallback，
            # 所以只有沒有異常時才清掉 diagnostics。
            if symbol not in diagnostics:

                diagnostics.pop(
                    symbol,
                    None,
                )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            )
            + 1
        )

        results[symbol] = {
            "symbol": symbol,
            "code": item["code"],
            "market": item["market"],
            "type": item["type"],
            "name": item["name"],
            "source": source,
            "history_rows": history_count,
            "history_status":
                history_status,
            "latest_date":
                merged[-1]["date"],
            "prices": merged,
        }

        log(
            f"  ✓ {history_count} 筆"
            f" / {history_status}"
            f" / {source}"
        )

    runtime_diagnostics = {
        "official": official_diagnostics,
        "source_counts": source_counts,
        "yahoo_fallback_attempted":
            fallback_attempted,
        "yahoo_fallback_success":
            fallback_success,
        "yahoo_fallback_failed":
            fallback_failed,
    }

    return (
        results,
        diagnostics,
        runtime_diagnostics,
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
    diagnostics: Dict[str, str],
    runtime_diagnostics: Dict[str, Any],
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

    official = runtime_diagnostics.get(
        "official",
        {}
    )

    log(
        f"官方交易日嘗試："
        f"{official.get('trading_days_attempted', 0)}"
    )

    log(
        f"TWSE 成功："
        f"{official.get('twse_success_days', 0)}"
    )

    log(
        f"TWSE 失敗："
        f"{official.get('twse_failed_days', 0)}"
    )

    log(
        f"TPEx 成功："
        f"{official.get('tpex_success_days', 0)}"
    )

    log(
        f"TPEx 失敗："
        f"{official.get('tpex_failed_days', 0)}"
    )

    log(
        f"Yahoo fallback 嘗試："
        f"{runtime_diagnostics.get('yahoo_fallback_attempted', 0)}"
    )

    log(
        f"Yahoo fallback 成功："
        f"{runtime_diagnostics.get('yahoo_fallback_success', 0)}"
    )

    log(
        f"Yahoo fallback 失敗："
        f"{runtime_diagnostics.get('yahoo_fallback_failed', 0)}"
    )

    if validation["missing"]:

        log("")
        log(
            "❌ Universe 缺少價格資料："
        )

        for symbol in validation[
            "missing"
        ]:

            log(
                f"  {symbol}"
                f" → "
                f"{diagnostics.get(symbol, 'missing')}"
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
                f"  {symbol}"
                f" → {reason}"
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
    diagnostics: Dict[str, str],
    runtime_diagnostics: Dict[str, Any],
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

        "diagnostics":
            diagnostics,

        "runtime_diagnostics":
            runtime_diagnostics,

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

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest root 無效"
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

    for filename in files:

        safe_filename = Path(
            str(filename)
        ).name

        if safe_filename != str(filename):

            raise RuntimeError(
                f"manifest 出現非法檔名："
                f"{filename}"
            )

        path = (
            output_dir
            / safe_filename
        )

        if not path.exists():

            raise RuntimeError(
                f"manifest 指向不存在 shard："
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
                f"{filename} root 無效"
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

    stock_count = 0
    etf_count = 0

    for item in universe:

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
    diagnostics: Dict[str, str],
    runtime_diagnostics: Dict[str, Any],
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
    # shard 自我驗證
    # --------------------------------------------------------

    for filename in shard_files:

        path = (
            temp_dir
            / filename
        )

        data = load_json(
            path
        )

        stocks = data[
            "stocks"
        ]

        validate_shard(
            path,
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
        runtime_diagnostics,
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
        "官方資料：TWSE / TPEx 日期批次優先"
    )

    log(
        "Existing：僅補歷史連續性"
    )

    log(
        "Yahoo：官方 / Existing 不足時補缺口"
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

    results, diagnostics, runtime_diagnostics = (
        build_results(
            universe
        )
    )

    # ========================================================
    # RESULT VALIDATION
    # ========================================================

    validation = validate_results(
        results,
        universe,
    )

    print_diagnostics(
        validation,
        diagnostics,
        runtime_diagnostics,
    )

    # --------------------------------------------------------
    # malformed 不允許寫入
    # --------------------------------------------------------

    if validation[
        "malformed"
    ]:

        raise RuntimeError(
            "Price results 存在 malformed data"
        )

    # --------------------------------------------------------
    # extra 不允許寫入
    # --------------------------------------------------------

    if validation[
        "extra"
    ]:

        raise RuntimeError(
            "Price results 出現 Universe 外商品"
        )

    # --------------------------------------------------------
    # missing 不允許寫入
    #
    # 這裡故意 fail closed。
    # 舊資料不會被破壞。
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
            diagnostics,
            runtime_diagnostics,
        )

        # ----------------------------------------------------
        # 寫入後重新讀檔驗證
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
    # FINAL DISK VALIDATION
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

    log(
        "✓ Universe 是唯一商品來源"
    )

    log(
        "✓ TWSE / TPEx 官方資料優先"
    )

    log(
        "✓ 官方採日期批次抓取"
    )

    log(
        "✓ 不逐股票逐日期呼叫官方 API"
    )

    log(
        "✓ Existing 可補歷史連續性"
    )

    log(
        "✓ 官方不足時會啟動 Yahoo 補缺口"
    )

    log(
        "✓ Yahoo 不覆蓋官方資料"
    )

    log(
        "✓ 官方抓取失敗具備 diagnostics"
    )

    log(
        "✓ Yahoo fallback 具備 diagnostics"
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
            f"❌ {type(exc).__name__}: {exc}"
        )

        raise SystemExit(
            1
        )
