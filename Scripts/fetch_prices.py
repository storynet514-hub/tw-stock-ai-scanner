#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式價格管線 V10.0
============================================================

核心契約
------------------------------------------------------------

1. Data/universe.json 是唯一 Universe 來源
2. Universe 中所有 active STOCK / ETF 都必須進入 Price pipeline
3. 不修改 Universe
4. 不使用成交行情建立 Universe
5. 不使用 CMoney
6. 官方市場批次資料優先
7. TWSE / TPEx 官方資料優先
8. Yahoo 僅作最後 fallback
9. fallback 必須明確標記來源
10. 歷史不足不是「商品不存在」
11. 新上市商品只要有 >= 1 筆有效價格，就必須寫入 Price shard
12. 真正 0 筆資料才列為 missing
13. OHLCV / 日期必須驗證
14. 正常歷史目標 90 筆
15. 20 筆不再是商品存在與否的門檻
16. 短歷史商品標記 short_history
17. missing 商品必須寫入 diagnostics
18. Price shard 必須完整對應實際 results
19. manifest 與 shard 必須一致
20. 使用 temporary directory
21. atomic replace
22. 舊版 schema 自動安全重建
23. Universe 新增商品自動初始化
24. 每次執行都以 Universe 為最終權威集合
25. 絕不因歷史不足而 silently drop 商品

============================================================
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "V10.0"
SCHEMA_VERSION = "prices-v10.0"


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

# ------------------------------------------------------------
# 重要：
#
# 這個值不再決定商品是否進入 results。
#
# 舊版：
#     < 20 筆 -> continue -> 商品消失
#
# V10：
#     >= 1 筆 -> 寫入 Price shard
#     0 筆     -> missing / diagnostics
#
# 這是本版本最重要的修正。
# ------------------------------------------------------------

SHORT_HISTORY_THRESHOLD = 20

INITIAL_LOOKBACK_CALENDAR_DAYS = 180

STOCKS_PER_FILE = 100

MAX_FILE_SIZE_MB = 80.0
MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# HTTP
# ============================================================

MAX_RETRIES = 3
REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.08
RETRY_DELAY = 1.5


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
# YAHOO FALLBACK
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

    for suffix in (
        ".TW",
        ".TWO",
        ".HK",
    ):

        if text.upper().endswith(
            suffix
        ):

            text = text[
                :-len(suffix)
            ]

            break

    return text.strip() or None


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

    # YYYY-MM-DD
    try:

        dt = datetime.strptime(
            text,
            "%Y-%m-%d",
        )

        return dt.strftime(
            "%Y-%m-%d"
        )

    except Exception:
        pass

    # YYYY/MM/DD
    try:

        dt = datetime.strptime(
            text,
            "%Y/%m/%d",
        )

        return dt.strftime(
            "%Y-%m-%d"
        )

    except Exception:
        pass

    # YYYYMMDD
    try:

        dt = datetime.strptime(
            text,
            "%Y%m%d",
        )

        return dt.strftime(
            "%Y-%m-%d"
        )

    except Exception:
        pass

    # Taiwan ROC date
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

    if (
        open_price is None
        or high_price is None
        or low_price is None
    ):

        # ETF / newly listed instruments
        # 若官方只提供成交價，無法形成完整 OHLC，
        # 使用 close 補齊缺少欄位。
        #
        # 但 high / low 必須仍然合理。
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
# HTTP GET
# ============================================================

def http_get_json(
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
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY
                )

    raise RuntimeError(
        f"HTTP JSON 取得失敗："
        f"{url}：{last_error}"
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
                f"status 不是 active"
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
                f"Universe 出現重複 symbol："
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
                "type": record_type,
                "instrument_type":
                    instrument_type,
            }
        )

    if not universe:

        raise RuntimeError(
            "Universe 為 0"
        )

    return universe


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

    try:

        data = http_get_json(
            TWSE_MI_INDEX_URL,
            params,
        )

    except Exception:
        return {}

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

            close = get_value(
                row,
                (
                    "收盤價",
                    "收盤",
                ),
            )

            volume = get_value(
                row,
                (
                    "成交股數",
                    "成交量",
                ),
            )

            open_price = get_value(
                row,
                (
                    "開盤價",
                    "開盤",
                ),
            )

            high = get_value(
                row,
                (
                    "最高價",
                    "最高",
                ),
            )

            low = get_value(
                row,
                (
                    "最低價",
                    "最低",
                ),
            )

            normalized = normalize_price_row(
                symbol,
                target_date,
                open_price,
                high,
                low,
                close,
                volume,
            )

            if normalized:

                result[symbol] = normalized

    return result


# ============================================================
# TWSE STOCK DAY ALL
# ============================================================

def fetch_twse_stock_day_all(
    target_date: str,
) -> Dict[str, Dict[str, Any]]:

    try:

        data = http_get_json(
            TWSE_STOCK_DAY_ALL_URL
        )

    except Exception:
        return {}

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
            item.get(
                "Code"
            )
            or item.get(
                "證券代號"
            )
        )

        if not symbol:
            continue

        close = (
            item.get("ClosingPrice")
            or item.get("收盤價")
        )

        volume = (
            item.get("TradeVolume")
            or item.get("成交股數")
        )

        normalized = normalize_price_row(
            symbol,
            target_date,
            item.get(
                "OpeningPrice"
            ),
            item.get(
                "HighestPrice"
            ),
            item.get(
                "LowestPrice"
            ),
            close,
            volume,
        )

        if normalized:

            result[symbol] = normalized

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

    roc_date = (
        f"{dt.year - 1911:03d}"
        f"/{dt.month:02d}"
        f"/{dt.day:02d}"
    )

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date,
    }

    try:

        data = http_get_json(
            TPEX_DAILY_URL,
            params,
        )

    except Exception:
        return {}

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

    # --------------------------------------------------------
    # TPEx 官方 stk_wn1430_result.php
    #
    # 欄位順序以官方 aaData 為準。
    #
    # 常見：
    #
    # 0 代號
    # 1 名稱
    # 2 收盤
    # 3 漲跌
    # 4 開盤
    # 5 最高
    # 6 最低
    # 7 成交股數
    #
    # 不猜欄名，直接按照官方 aaData 結構解析。
    # --------------------------------------------------------

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

        close = row[2]
        open_price = row[4]
        high = row[5]
        low = row[6]
        volume = row[7]

        normalized = normalize_price_row(
            symbol,
            target_date,
            open_price,
            high,
            low,
            close,
            volume,
        )

        if normalized:

            result[symbol] = normalized

    return result


# ============================================================
# YAHOO SYMBOL
# ============================================================

def yahoo_symbol(
    item: Dict[str, str],
) -> str:

    symbol = item["symbol"]
    market = item["market"]

    if market == "TWSE":
        return f"{symbol}.TW"

    if market == "TPEX":
        return f"{symbol}.TWO"

    return symbol


# ============================================================
# YAHOO HISTORY
# ============================================================

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

    try:

        data = http_get_json(
            url,
            params,
        )

    except Exception:

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

    rows = []

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
                timestamp,
                tz=timezone.utc,
            )

            date_text = dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:

            continue

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

    return rows[
        -MAX_HISTORY_ROWS:
    ]


# ============================================================
# FETCH OFFICIAL HISTORY
# ============================================================

def fetch_official_history(
    item: Dict[str, str],
    start_date: str,
    end_date: str,
) -> Dict[str, Dict[str, Any]]:

    symbol = item["symbol"]

    market = item["market"]

    rows = {}

    start_dt = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    )

    end_dt = datetime.strptime(
        end_date,
        "%Y-%m-%d",
    )

    current = start_dt

    while current <= end_dt:

        date_text = current.strftime(
            "%Y-%m-%d"
        )

        daily = {}

        if market == "TWSE":

            daily = fetch_twse_daily(
                date_text
            )

            # ------------------------------------------------
            # 若 MI_INDEX 沒資料，
            # 再使用 STOCK_DAY_ALL。
            # ------------------------------------------------

            if symbol not in daily:

                all_market = (
                    fetch_twse_stock_day_all(
                        date_text
                    )
                )

                if symbol in all_market:
                    daily = all_market

        elif market == "TPEX":

            daily = fetch_tpex_daily(
                date_text
            )

        if symbol in daily:

            row = daily[
                symbol
            ]

            rows[
                row["date"]
            ] = row

        current += timedelta(
            days=1
        )

        time.sleep(
            REQUEST_DELAY
        )

    return rows


# ============================================================
# EXISTING PRICES
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

                result[symbol] = {
                    "rows": clean_rows[
                        -MAX_HISTORY_ROWS:
                    ],
                    "source":
                        "existing_price",
                }

    return result


# ============================================================
# BUILD PRICE RESULTS
# ============================================================

def build_results(
    universe: List[Dict[str, str]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, str],
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
            days=INITIAL_LOOKBACK_CALENDAR_DAYS
        )
    ).strftime(
        "%Y-%m-%d"
    )

    end_date = today.strftime(
        "%Y-%m-%d"
    )

    existing = load_existing_prices()

    results = {}

    diagnostics = {}

    universe_by_symbol = {
        item["symbol"]: item
        for item in universe
    }

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

        row_map = {}

        # ----------------------------------------------------
        # 1. existing
        # ----------------------------------------------------

        previous = existing.get(
            symbol
        )

        if previous:

            for row in previous[
                "rows"
            ]:

                row_map[
                    row["date"]
                ] = row

        # ----------------------------------------------------
        # 2. official batch history
        # ----------------------------------------------------

        official = (
            fetch_official_history(
                item,
                start_date,
                end_date,
            )
        )

        for date_text, row in (
            official.items()
        ):

            row_map[
                date_text
            ] = row

        source = (
            "TWSE official"
            if item["market"] == "TWSE"
            else "TPEx official"
        )

        # ----------------------------------------------------
        # 3. Yahoo fallback
        #
        # 只有官方資料完全沒有，或最新日期沒有，
        # 才使用 Yahoo。
        # ----------------------------------------------------

        final_rows = sorted(
            row_map.values(),
            key=lambda x: x["date"],
        )

        needs_yahoo = (
            len(final_rows) == 0
        )

        if not needs_yahoo:

            latest_date = (
                final_rows[-1]["date"]
            )

            if latest_date < end_date:

                # 非交易日不應要求一定等於今天。
                # 只要已有最近交易日即可。
                pass

        if needs_yahoo:

            yahoo_rows = (
                fetch_yahoo_history(
                    item,
                    start_date,
                    end_date,
                )
            )

            if yahoo_rows:

                for row in yahoo_rows:

                    row_map[
                        row["date"]
                    ] = row

                source = (
                    "Yahoo fallback"
                )

        # ----------------------------------------------------
        # 4. final rows
        # ----------------------------------------------------

        final_rows = sorted(
            row_map.values(),
            key=lambda x: x["date"],
        )[
            -MAX_HISTORY_ROWS:
        ]

        # ----------------------------------------------------
        # 重要修正：
        #
        # 舊版：
        #
        # if len(final_rows) < 20:
        #     diagnostics[...] = ...
        #     continue
        #
        # 這會直接把新上市 ETF 從 Price Universe 移除。
        #
        # V10：
        #
        # >= 1 筆：一定進 results
        # 0 筆：才是真正 missing
        # ----------------------------------------------------

        if len(final_rows) == 0:

            diagnostics[
                symbol
            ] = (
                "no_valid_price_data"
            )

            log(
                f"  ⚠️ {symbol} "
                "目前沒有任何有效價格資料"
            )

            continue

        if len(final_rows) < (
            SHORT_HISTORY_THRESHOLD
        ):

            diagnostics[
                symbol
            ] = (
                "short_history:"
                f"{len(final_rows)}"
            )

            history_status = (
                "short_history"
            )

        elif len(final_rows) < (
            TARGET_HISTORY_ROWS
        ):

            diagnostics[
                symbol
            ] = (
                "partial_history:"
                f"{len(final_rows)}"
            )

            history_status = (
                "partial_history"
            )

        else:

            diagnostics.pop(
                symbol,
                None,
            )

            history_status = (
                "complete"
            )

        results[symbol] = {
            "symbol": symbol,
            "code": item["code"],
            "market": item["market"],
            "type": item["type"],
            "name": item["name"],
            "source": source,
            "history_rows": len(
                final_rows
            ),
            "history_status":
                history_status,
            "latest_date":
                final_rows[-1]["date"],
            "prices": final_rows,
        }

        log(
            f"  ✓ {len(final_rows)} 筆"
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

        # ----------------------------------------------------
        # V10：
        # >=1 筆即可存在於 Price Universe
        # ----------------------------------------------------

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

            normalized = normalize_price_row(
                code=symbol,
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
) -> None:

    missing = validation[
        "missing"
    ]

    extra = validation[
        "extra"
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
        f"Price："
        f"{validation['actual_count']}"
    )

    log(
        f"缺少："
        f"{len(missing)}"
    )

    log(
        f"額外："
        f"{len(extra)}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    if missing:

        log("")
        log(
            "❌ 缺少價格資料："
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

    if extra:

        log("")
        log(
            "❌ Price 出現 Universe "
            "以外股票："
        )

        for symbol in extra:
            log(
                f"  - {symbol}"
            )

    if malformed:

        log("")
        log(
            "❌ 真正資料結構錯誤："
        )

        for symbol, reason in (
            malformed
        ):

            log(
                f"  - {symbol}"
                f" → {reason}"
            )

    # --------------------------------------------------------
    # 真正阻塞條件
    #
    # 1. malformed
    # 2. extra
    #
    # missing 不再由 fetch_prices 自己 raise，
    # 但 Validate Price Data 會在 workflow 中檢查。
    # --------------------------------------------------------

    if malformed:

        raise RuntimeError(
            "存在無效價格資料"
        )

    if extra:

        raise RuntimeError(
            "Price 出現 Universe 外股票"
        )

    if (
        success_rate
        < DIAGNOSTIC_SUCCESS_RATE_TARGET
    ):

        log("")
        log(
            "⚠️ Price 完整率低於 "
            f"{DIAGNOSTIC_SUCCESS_RATE_TARGET:.0%}"
        )

    elif missing:

        log("")
        log(
            "⚠️ 少數 Universe 商品 "
            "目前沒有有效價格資料"
        )

    else:

        log("")
        log(
            "✓ 所有 Universe 商品 "
            "均有至少一筆有效價格資料"
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

        # ----------------------------------------------------
        # V10：
        # 新上市商品可以只有 1~19 筆。
        # ----------------------------------------------------

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

            normalized = normalize_price_row(
                code=symbol,
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
        Dict[str, Any],
    ],
    universe_stock_count: int,
    universe_etf_count: int,
    validation: Dict[str, Any],
    diagnostics: Dict[str, str],
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

        "diagnostic_success_rate_target":
            DIAGNOSTIC_SUCCESS_RATE_TARGET,

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
# WRITE TEMP DIRECTORY
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
    # shard validation
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
# MAIN
# ============================================================

def main() -> int:

    section(
        "TAIWAN STOCK AI PRICE PIPELINE V10.0"
    )

    log(
        "核心修正："
        "歷史不足 20 日不再排除商品"
    )

    log(
        "最小存在條件："
        "至少 1 筆有效 OHLCV"
    )

    log(
        f"目標歷史："
        f"{TARGET_HISTORY_ROWS} 筆"
    )

    log(
        f"短歷史門檻："
        f"{SHORT_HISTORY_THRESHOLD} 筆"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

    results, diagnostics = (
        build_results(
            universe
        )
    )

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    validation = validate_results(
        results,
        universe,
    )

    print_diagnostics(
        validation,
        diagnostics,
    )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # fetch_prices.py 不因少數商品沒有資料而把
    # 已取得的有效資料全部丟掉。
    #
    # 但是如果 Universe 有商品完全沒有價格，
    # diagnostics 必須保留。
    #
    # update.yml 的 Validate Price Data
    # 會最終要求：
    #
    # Universe == Price
    #
    # 因此如果仍然 missing，
    # workflow 會明確 FAIL，而不是產生假完整資料。
    # --------------------------------------------------------

    if validation["malformed"]:

        raise RuntimeError(
            "Price results 包含 "
            "無效資料結構"
        )

    if validation["extra"]:

        raise RuntimeError(
            "Price results 包含 "
            "Universe 外商品"
        )

    # --------------------------------------------------------
    # Temporary output
    # --------------------------------------------------------

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
        )

        # ----------------------------------------------------
        # Final directory validation
        # ----------------------------------------------------

        manifest_path = (
            temp_output
            / "manifest.json"
        )

        manifest = load_json(
            manifest_path
        )

        shard_files = manifest[
            "files"
        ]

        written_symbols = set()

        for filename in shard_files:

            shard = load_json(
                temp_output
                / filename
            )

            stocks = shard[
                "stocks"
            ]

            written_symbols.update(
                stocks.keys()
            )

        expected_symbols = {
            item["symbol"]
            for item in universe
        }

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
                "寫入後出現 Universe 外股票："
                f"{sorted(extra_after_write)[:20]}"
            )

        if missing_after_write:

            log("")
            log(
                "⚠️ 寫入後仍缺少商品："
            )

            for symbol in sorted(
                missing_after_write
            ):

                log(
                    f"  - {symbol}"
                )

            raise RuntimeError(
                "Price shard 寫入後 "
                "仍未完整對應 Universe"
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

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

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

    section(
        "PRICE PIPELINE PASS"
    )

    log(
        "✓ Universe 是唯一商品來源"
    )

    log(
        "✓ 所有 active 商品均進入 Price pipeline"
    )

    log(
        "✓ 新上市商品不因歷史不足而消失"
    )

    log(
        "✓ 歷史不足商品標記 short_history"
    )

    log(
        "✓ 真正無資料商品才列 missing"
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