#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py

正式修正版 V7.1
============================================================

本版只修正已確認的 Universe 解析問題，並保留：

1. Data/universe.json 是唯一 Universe 來源
2. STOCK / ETF 完全分流
3. ETF 不進價格抓取
4. 不修改 Universe
5. 不用成交行情建立 Universe
6. 不使用 CMoney
7. TWSE / TPEx 官方資料優先
8. Yahoo 僅作官方資料失敗時的最後 fallback
9. 20 筆為絕對最低歷史資料
10. 60 筆以上為完整歷史
11. temporary directory
12. shard 驗證
13. manifest 驗證
14. atomic replace

============================================================
V7.1 核心修正
============================================================

V7.0 已確認錯誤：

Universe：
    metadata ETF = 357

V7.0：
    實際 ETF = 158
    無法解析 = 199

根本原因：

extract_code() 原本只接受：

    0-9

但官方 Universe 合法商品包含：

    00400A
    009xxA
    其他合法英數商品代號

因此：

    00400A
    ↓
    isdigit() == False
    ↓
    被判定無法解析
    ↓
    Universe 商品被錯誤丟掉

V7.1：

合法代號規則：

    4~6 碼
    第一碼必須是數字
    後續允許英數字

例如：

    2330
    3081
    7794
    0050
    00400A
    00980A

均可正常解析。

============================================================
重要設計
============================================================

Universe 的 stocks object 可能同時包含：

    STOCK
    ETF

因此：

    len(universe["stocks"])

不能直接當 STOCK 數量。

必須依：

    item["type"]

分流。

metadata：

    stock_count
    etf_count

必須與實際解析結果一致。

如果不一致：

    FAIL

而不是偷偷繼續。

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
# VERSION
# ============================================================

VERSION = "V7.1"
SCHEMA_VERSION = "prices-v7.1"


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

MIN_SUCCESS_RATE = 0.80

MAX_RETRIES = 3
REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.12
RETRY_DELAY = 2.0


# ============================================================
# OFFICIAL DATA
# ============================================================

TWSE_STOCK_DAY_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY"
)

TPEX_ST43_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_trading_info/st43_result.php"
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
    )

    try:

        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def safe_int(value: Any) -> int:

    number = safe_float(value)

    if number is None:
        return 0

    return int(number)


# ============================================================
# DATE
# ============================================================

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

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

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


# ============================================================
# CODE
# ============================================================

def extract_code(
    value: Any,
) -> Optional[str]:
    """
    V7.1 關鍵修正。

    舊版：
        isdigit()

    會錯誤排除：

        00400A
        00980A
        等合法英數商品代號。

    新規則：

        4~6 碼
        第一碼必須為數字
        後續允許 A-Z / 0-9
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

        code = extract_code(symbol)

        if code:
            return code

    if fallback_key:

        return extract_code(
            fallback_key
        )

    return None


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

    # --------------------------------------------------------
    # type 是唯一 STOCK / ETF 分流依據
    # --------------------------------------------------------

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
                parsed_stocks[symbol] = normalized

        elif normalized["type"] == "ETF":

            if symbol not in parsed_etfs:
                parsed_etfs[symbol] = normalized

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

    # --------------------------------------------------------
    # 這裡是硬 Gate
    # --------------------------------------------------------

    if actual_stock_count != declared_stock_count:

        log("")
        log(
            "❌ Universe 驗證失敗："
            f"STOCK metadata="
            f"{declared_stock_count}, "
            f"actual="
            f"{actual_stock_count}"
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
            "Universe STOCK 數量不一致"
        )

    if actual_etf_count != declared_etf_count:

        log("")
        log(
            "❌ Universe 驗證失敗："
            f"ETF metadata="
            f"{declared_etf_count}, "
            f"actual="
            f"{actual_etf_count}"
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
            "Universe ETF 數量不一致"
        )

    if unparsed:

        log("")
        log(
            "❌ Universe 存在無法解析項目："
            f"{len(unparsed)}"
        )

        for value in unparsed[:50]:
            log(
                f"  {value}"
            )

        raise RuntimeError(
            "Universe 存在未解析商品"
        )

    # --------------------------------------------------------
    # 7794 明確驗證
    # --------------------------------------------------------

    target = parsed_stocks.get(
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
        parsed_stocks.values()
    )


# ============================================================
# MONTH SEQUENCE
# ============================================================

def month_sequence(
    start_date: str,
) -> List[
    Tuple[int, int]
]:

    start = datetime.strptime(
        start_date,
        "%Y-%m-%d",
    )

    now = datetime.now(
        timezone.utc
    )

    result = []

    year = now.year
    month = now.month

    while (
        year > start.year
        or (
            year == start.year
            and month >= start.month
        )
    ):

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

    result.reverse()

    return result


# ============================================================
# TWSE MONTH
# ============================================================

def fetch_twse_month(
    code: str,
    year: int,
    month: int,
) -> List[
    Dict[str, Any]
]:

    roc_year = year - 1911

    date_value = (
        f"{roc_year:03d}"
        f"{month:02d}01"
    )

    params = {
        "response": "json",
        "date": date_value,
        "stockNo": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TWSE_STOCK_DAY_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            payload = response.json()

            data = payload.get(
                "data",
                []
            )

            if not isinstance(
                data,
                list,
            ):
                return []

            rows = []

            for row in data:

                if not isinstance(
                    row,
                    list,
                ):
                    continue

                if len(row) < 7:
                    continue

                date_value = parse_date(
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
                    close is None
                    or high is None
                    or low is None
                ):
                    continue

                if close <= 0:
                    continue

                if open_value is None:
                    open_value = close

                rows.append(
                    {
                        "date": date_value,
                        "open": open_value,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
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
# TPEX MONTH
# ============================================================

def fetch_tpex_month(
    code: str,
    year: int,
    month: int,
) -> List[
    Dict[str, Any]
]:

    roc_year = year - 1911

    params = {
        "l": "zh-tw",
        "d": (
            f"{roc_year:03d}/"
            f"{month:02d}"
        ),
        "stkno": code,
    }

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TPEX_ST43_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            response.raise_for_status()

            payload = response.json()

            data = payload.get(
                "aaData",
                []
            )

            if not isinstance(
                data,
                list,
            ):
                return []

            rows = []

            for row in data:

                if not isinstance(
                    row,
                    list,
                ):
                    continue

                if len(row) < 7:
                    continue

                date_value = parse_date(
                    row[0]
                )

                if not date_value:
                    continue

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

                volume = safe_int(
                    row[1]
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

                rows.append(
                    {
                        "date": date_value,
                        "open": open_value,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
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
# OFFICIAL HISTORY
# ============================================================

def fetch_official_history(
    item: Dict[str, str],
) -> List[
    Dict[str, Any]
]:

    code = item["code"]
    market = item["market"]

    all_rows = {}

    for year, month in month_sequence(
        START_DATE
    ):

        if market == "TW":

            rows = fetch_twse_month(
                code,
                year,
                month,
            )

        else:

            rows = fetch_tpex_month(
                code,
                year,
                month,
            )

        for row in rows:

            all_rows[
                row["date"]
            ] = row

        time.sleep(
            REQUEST_DELAY
        )

    return sorted(
        all_rows.values(),
        key=lambda row: row["date"],
    )


# ============================================================
# YAHOO
# ============================================================

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

        close = (
            safe_float(closes[i])
            if i < len(closes)
            else None
        )

        high = (
            safe_float(highs[i])
            if i < len(highs)
            else None
        )

        low = (
            safe_float(lows[i])
            if i < len(lows)
            else None
        )

        open_value = (
            safe_float(opens[i])
            if i < len(opens)
            else None
        )

        volume = (
            safe_int(volumes[i])
            if i < len(volumes)
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

        rows[date_value] = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        rows.values(),
        key=lambda row: row["date"],
    )


def fetch_yahoo(
    symbol: str,
) -> List[
    Dict[str, Any]
]:

    params = {
        "period1": date_to_timestamp(
            START_DATE
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
# ONE STOCK
# ============================================================

def fetch_one(
    item: Dict[str, str],
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    symbol = item["symbol"]

    name = item["name"]

    log(
        f"→ 官方來源優先："
        f"{symbol} {name}"
    )

    # --------------------------------------------------------
    # OFFICIAL FIRST
    # --------------------------------------------------------

    official_rows = fetch_official_history(
        item
    )

    if len(official_rows) >= (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        status = (
            "complete"
            if len(official_rows)
            >= MIN_HISTORY_ROWS
            else "short_history"
        )

        source = (
            "TWSE official"
            if item["market"] == "TW"
            else "TPEx official"
        )

        log(
            f"✓ {symbol} "
            f"→ {len(official_rows)} 筆 "
            f"→ {source}"
        )

        return (
            {
                "symbol": symbol,
                "code": item["code"],
                "market": item["market"],
                "name": name,
                "source": source,
                "history_rows": len(
                    official_rows
                ),
                "history_status": status,
                "latest_date": official_rows[-1][
                    "date"
                ],
                "prices": official_rows,
            },
            "",
        )

    log(
        f"⚠️ {symbol} "
        f"官方資料只有 "
        f"{len(official_rows)} 筆"
    )

    # --------------------------------------------------------
    # YAHOO LAST FALLBACK
    # --------------------------------------------------------

    yahoo_rows = fetch_yahoo(
        symbol
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

        log(
            f"✓ {symbol} "
            f"→ {len(yahoo_rows)} 筆 "
            f"→ Yahoo fallback"
        )

        return (
            {
                "symbol": symbol,
                "code": item["code"],
                "market": item["market"],
                "name": name,
                "source": "Yahoo fallback",
                "history_rows": len(
                    yahoo_rows
                ),
                "history_status": status,
                "latest_date": yahoo_rows[-1][
                    "date"
                ],
                "prices": yahoo_rows,
            },
            "official_history_insufficient",
        )

    return (
        None,
        (
            f"官方資料不足："
            f"{len(official_rows)}；"
            f"Yahoo資料不足："
            f"{len(yahoo_rows)}"
        ),
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
            f"找不到 shard：{path.name}"
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

            date_value = str(
                row["date"]
            )

            if (
                previous_date
                and date_value
                < previous_date
            ):

                raise RuntimeError(
                    f"{symbol} 日期未排序"
                )

            previous_date = date_value


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

    # --------------------------------------------------------
    # PRICE FETCH
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

        symbol = item["symbol"]

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

                failures[symbol] = reason

            else:

                results[symbol] = result

                source = result[
                    "source"
                ]

                source_counts[source] = (
                    source_counts.get(
                        source,
                        0,
                    )
                    + 1
                )

        except Exception as exc:

            failures[symbol] = str(exc)

            log(
                f"❌ {symbol} "
                f"未預期錯誤："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # RESULT
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

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # --------------------------------------------------------
    # 80% SAFETY GATE
    # --------------------------------------------------------

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

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
    # MISSING
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
        )[:100]:

            log(
                f"  {symbol}: "
                f"{failures.get(symbol, '')}"
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
        # 7794
        # ----------------------------------------------------

        if "7794.TWO" in expected_symbols:

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

            else:

                log(
                    "⚠️ 7794.TWO 尚無價格資料"
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
        "✓ fetch_prices.py 完成"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )