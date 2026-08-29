#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py
正式修正版 V6.0

核心原則
============================================================

1. Data/universe.json 是唯一 Universe 來源
2. stocks 裡面的 type 決定 STOCK / ETF
3. ETF 絕對不能進 STOCK Price Gate
4. STOCK Universe 必須正確解析
5. TWSE STOCK → TWSE 官方優先
6. TPEX STOCK → TPEx 官方優先
7. Yahoo 只能最後 fallback
8. 不使用舊 prices 冒充新資料
9. 不使用 CMoney
10. 不因單一股票失敗而靜默遺漏
11. temporary directory
12. 完整驗證後 atomic replace
13. 每 100 檔一個 shard
14. 不產生 Data/prices.json

重要修正
============================================================

V5.0 問題：

A. forced_type="Stock" 導致 ETF 被誤算成 STOCK
B. Universe metadata 1944，但實際解析成 2102
C. ETF 0050 / 0051 等被錯誤抓價格
D. TWSE API 出現 redirect loop
E. 官方 HTTP / JSON / stat 錯誤沒有清楚分類
F. TPEx fallback 架構過度依賴單一路徑
G. Yahoo 被迫承擔過多價格資料
H. 7794.TWO 沒有被做成獨立官方驗證

V6.0：

✓ STOCK / ETF 嚴格依 type 分類
✓ metadata 與實際 STOCK 數量交叉驗證
✓ ETF 完全排除
✓ TWSE 官方 HTTPS endpoint
✓ 禁止 redirect loop
✓ HTTP 狀態獨立記錄
✓ JSON 解析獨立記錄
✓ TWSE stat 狀態獨立記錄
✓ TPEx 官方優先
✓ Yahoo 最後 fallback
✓ 7794.TWO 強制驗證
✓ 1944 → 1944 才視為完整成功
✓ 所有失敗股票明確列出
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

VERSION = "V6.0"
SCHEMA_VERSION = "prices-v6.0"


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


# ============================================================
# OFFICIAL API
# ============================================================

TWSE_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/STOCK_DAY"
)

TPEX_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "daily_trading_info/"
    "st43_result.php"
)

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)


# ============================================================
# REQUEST
# ============================================================

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.10

RETRY_DELAY = 1.5

TWSE_FETCH_MONTHS = 24

TPEX_FETCH_MONTHS = 24

STOCKS_PER_FILE = 100

MIN_SUCCESS_RATE = 0.80

MAX_FILE_SIZE_MB = 80.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
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
            "application/json,text/plain,*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Referer": "https://www.twse.com.tw/",
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
    ) as file:
        return json.load(file)


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
    ) as file:

        json.dump(
            data,
            file,
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

    try:

        text = (
            str(value)
            .replace(",", "")
            .replace(" ", "")
            .strip()
        )

        if not text:
            return None

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

def parse_iso_date(
    value: Any,
) -> Optional[str]:

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

            continue

    return None


def parse_roc_date(
    value: Any,
) -> Optional[str]:

    text = clean_text(value)

    if not text:
        return None

    parts = text.split("/")

    if len(parts) != 3:
        return parse_iso_date(text)

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

        return None


# ============================================================
# MONTH
# ============================================================

def month_sequence(
    months: int,
) -> List[Tuple[int, int]]:

    now = datetime.now(
        timezone.utc
    )

    year = now.year
    month = now.month

    result = []

    for _ in range(months):

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

    return list(
        reversed(result)
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

    if not text.isdigit():
        return None

    if not 4 <= len(text) <= 6:
        return None

    return text


# ============================================================
# SYMBOL
# ============================================================

def extract_symbol(
    item: Dict[str, Any],
) -> Optional[str]:

    for key in (
        "full_symbol",
        "fullSymbol",
        "yahoo_symbol",
        "yahooSymbol",
        "symbol",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if text.endswith(".TW"):

            code = extract_code(text)

            if code:
                return code + ".TW"

        if text.endswith(".TWO"):

            code = extract_code(text)

            if code:
                return code + ".TWO"

    return None


# ============================================================
# TYPE
# ============================================================

def detect_type(
    item: Dict[str, Any],
) -> str:

    value = item.get("type")

    if value is not None:

        text = clean_text(
            value
        ).upper()

        if text == "ETF":
            return "ETF"

        if text == "STOCK":
            return "Stock"

        if "ETF" in text:
            return "ETF"

        if "STOCK" in text:
            return "Stock"

        if "股票" in text:
            return "Stock"

    for key in (
        "security_type",
        "securityType",
        "instrument_type",
        "instrumentType",
        "category",
        "類型",
        "商品類型",
        "證券類型",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if "ETF" in text:
            return "ETF"

        if (
            "STOCK" in text
            or "股票" in text
        ):
            return "Stock"

    return "Unknown"


# ============================================================
# MARKET
# ============================================================

def detect_market(
    item: Dict[str, Any],
    symbol: Optional[str],
) -> Optional[str]:

    if symbol:

        if symbol.endswith(".TWO"):
            return "TWO"

        if symbol.endswith(".TW"):
            return "TW"

    for key in (
        "market",
        "exchange",
        "market_type",
        "marketType",
        "board",
        "市場",
        "市場別",
        "交易所",
        "掛牌市場",
        "上市櫃",
        "上市櫃別",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if (
            text in {
                "TPEX",
                "TWO",
                "OTC",
                "O",
            }
            or "TPEX" in text
            or "OTC" in text
            or "上櫃" in text
            or "上柜" in text
            or "櫃買" in text
            or "柜买" in text
        ):
            return "TWO"

        if (
            text in {
                "TWSE",
                "TW",
                "TSE",
            }
            or "TWSE" in text
            or "上市" in text
        ):
            return "TW"

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
        "company_name",
        "security_name",
        "名稱",
        "證券名稱",
        "公司名稱",
    ):

        value = clean_text(
            item.get(key)
        )

        if value:
            return value

    return ""


# ============================================================
# NORMALIZE UNIVERSE ITEM
# ============================================================

def normalize_item(
    key: str,
    item: Any,
) -> Optional[Dict[str, str]]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    symbol = extract_symbol(
        item
    )

    if symbol is None:

        symbol = clean_text(
            key
        ).upper()

        code = extract_code(
            symbol
        )

        if code is None:
            return None

        symbol = (
            code
            + (
                ".TWO"
                if clean_text(
                    item.get("market")
                ).upper()
                in {
                    "TWO",
                    "TPEX",
                    "OTC",
                }
                else ".TW"
            )
        )

    code = extract_code(
        symbol
    )

    if code is None:
        return None

    security_type = detect_type(
        item
    )

    market = detect_market(
        item,
        symbol,
    )

    if market is None:

        return None

    return {
        "symbol": symbol,
        "code": code,
        "market": market,
        "name": extract_name(item),
        "type": security_type,
    }


# ============================================================
# LOAD UNIVERSE
# ============================================================

def load_universe() -> List[Dict[str, str]]:

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

    stocks_container = universe.get(
        "stocks"
    )

    if not isinstance(
        stocks_container,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks 必須是 object"
        )

    declared_stock_count = universe.get(
        "stock_count"
    )

    declared_etf_count = universe.get(
        "etf_count"
    )

    stock_records = {}
    etf_records = {}

    parse_failures = []

    for key, raw_item in stocks_container.items():

        normalized = normalize_item(
            key,
            raw_item,
        )

        if normalized is None:

            parse_failures.append(
                str(key)
            )

            continue

        symbol = normalized["symbol"]

        if normalized["type"] == "Stock":

            stock_records[symbol] = normalized

        elif normalized["type"] == "ETF":

            etf_records[symbol] = normalized

        else:

            parse_failures.append(
                symbol
            )

    actual_stock_count = len(
        stock_records
    )

    actual_etf_count = len(
        etf_records
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
        f"實際 STOCK："
        f"{actual_stock_count}"
    )

    log(
        f"實際 ETF："
        f"{actual_etf_count}"
    )

    if parse_failures:

        log(
            f"⚠️ 無法解析："
            f"{len(parse_failures)} 檔"
        )

        for symbol in parse_failures[:30]:

            log(
                f"  {symbol}"
            )

    # --------------------------------------------------------
    # 核心 Gate
    # --------------------------------------------------------

    if (
        isinstance(
            declared_stock_count,
            int,
        )
        and declared_stock_count
        != actual_stock_count
    ):

        raise RuntimeError(
            "Universe STOCK 數量不一致："
            f"metadata={declared_stock_count}, "
            f"actual={actual_stock_count}"
        )

    if (
        isinstance(
            declared_etf_count,
            int,
        )
        and declared_etf_count
        != actual_etf_count
    ):

        raise RuntimeError(
            "Universe ETF 數量不一致："
            f"metadata={declared_etf_count}, "
            f"actual={actual_etf_count}"
        )

    if actual_stock_count == 0:

        raise RuntimeError(
            "Universe STOCK 為 0"
        )

    # --------------------------------------------------------
    # 7794
    # --------------------------------------------------------

    if "7794.TWO" in stock_records:

        target = stock_records[
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

    else:

        log(
            "⚠️ Universe 沒有 7794.TWO"
        )

    return list(
        stock_records.values()
    )


# ============================================================
# REQUEST JSON
# ============================================================

def request_json(
    url: str,
    params: Dict[str, Any],
    source: str,
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

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
                allow_redirects=False,
            )

            # ------------------------------------------------
            # Redirect
            # ------------------------------------------------

            if 300 <= response.status_code < 400:

                location = (
                    response.headers.get(
                        "Location",
                        "",
                    )
                )

                last_error = (
                    f"{source} HTTP "
                    f"{response.status_code} "
                    f"redirect → "
                    f"{location}"
                )

                # 直接重新組成 HTTPS URL，
                # 不讓 requests 陷入 redirect loop。

                if location.startswith(
                    "http://"
                ):

                    location = (
                        "https://"
                        + location[7:]
                    )

                if location:

                    try:

                        response = SESSION.get(
                            location,
                            timeout=REQUEST_TIMEOUT,
                            allow_redirects=False,
                        )

                    except Exception as exc:

                        last_error = (
                            f"{source} redirect "
                            f"request failed: {exc}"
                        )

            response.raise_for_status()

            try:

                payload = response.json()

            except Exception as exc:

                last_error = (
                    f"{source} JSON parse failed: "
                    f"{exc}"
                )

                raise

            if not isinstance(
                payload,
                dict,
            ):

                last_error = (
                    f"{source} JSON root "
                    f"不是 object"
                )

                raise RuntimeError(
                    last_error
                )

            return (
                payload,
                "",
            )

        except Exception as exc:

            if not last_error:
                last_error = str(exc)

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    return (
        None,
        last_error,
    )


# ============================================================
# TWSE PARSER
# ============================================================

def parse_twse_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    stat = clean_text(
        payload.get("stat")
    ).upper()

    if stat not in {
        "OK",
        "NORMAL",
    }:

        return []

    data = payload.get(
        "data",
        []
    )

    if not isinstance(
        data,
        list,
    ):

        return []

    rows = {}

    for row in data:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 7:
            continue

        date_value = parse_roc_date(
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
            high is None
            or low is None
            or close is None
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
        key=lambda x: x["date"],
    )


# ============================================================
# TWSE OFFICIAL MONTH
# ============================================================

def fetch_twse_month(
    code: str,
    year: int,
    month: int,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    params = {
        "response": "json",
        "date": f"{year}{month:02d}01",
        "stockNo": code,
    }

    payload, error = request_json(
        TWSE_URL,
        params,
        f"TWSE {code}",
    )

    if payload is None:

        return (
            [],
            error,
        )

    rows = parse_twse_payload(
        payload
    )

    if not rows:

        stat = clean_text(
            payload.get("stat")
        )

        if stat:

            return (
                [],
                f"TWSE stat={stat}",
            )

        return (
            [],
            "TWSE data=0",
        )

    return (
        rows,
        "",
    )


# ============================================================
# TWSE HISTORY
# ============================================================

def fetch_twse_history(
    code: str,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    section(
        f"TWSE 官方資料：{code}.TW"
    )

    all_rows = {}

    last_error = ""

    for year, month in month_sequence(
        TWSE_FETCH_MONTHS
    ):

        rows, error = fetch_twse_month(
            code,
            year,
            month,
        )

        if error:
            last_error = error

        for row in rows:

            all_rows[
                row["date"]
            ] = row

        if len(all_rows) >= MIN_HISTORY_ROWS:

            break

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda x: x["date"],
    )

    if result:

        return (
            result,
            "",
        )

    return (
        [],
        last_error
        or "TWSE 無資料",
    )


# ============================================================
# TPEX PARSER
# ============================================================

def parse_tpex_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    data = payload.get(
        "aaData",
        []
    )

    if not isinstance(
        data,
        list,
    ):

        return []

    rows = {}

    for row in data:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 7:
            continue

        date_value = parse_roc_date(
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
            high is None
            or low is None
            or close is None
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
        key=lambda x: x["date"],
    )


# ============================================================
# TPEX OFFICIAL MONTH
# ============================================================

def fetch_tpex_month(
    code: str,
    year: int,
    month: int,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    roc_year = year - 1911

    params = {
        "l": "zh-tw",
        "d": f"{roc_year:03d}/{month:02d}",
        "stkno": code,
    }

    payload, error = request_json(
        TPEX_URL,
        params,
        f"TPEx {code}",
    )

    if payload is None:

        return (
            [],
            error,
        )

    rows = parse_tpex_payload(
        payload
    )

    if not rows:

        return (
            [],
            "TPEx aaData=0",
        )

    return (
        rows,
        "",
    )


# ============================================================
# TPEX HISTORY
# ============================================================

def fetch_tpex_history(
    code: str,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    section(
        f"TPEx 官方資料：{code}.TWO"
    )

    all_rows = {}

    last_error = ""

    for year, month in month_sequence(
        TPEX_FETCH_MONTHS
    ):

        rows, error = fetch_tpex_month(
            code,
            year,
            month,
        )

        if error:
            last_error = error

        for row in rows:

            all_rows[
                row["date"]
            ] = row

        if len(all_rows) >= MIN_HISTORY_ROWS:

            break

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda x: x["date"],
    )

    if result:

        return (
            result,
            "",
        )

    return (
        [],
        last_error
        or "TPEx 無資料",
    )


# ============================================================
# YAHOO
# ============================================================

def parse_yahoo_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    chart = payload.get(
        "chart",
        {}
    )

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
        "timestamp",
        []
    )

    quote_list = (
        first
        .get("indicators", {})
        .get("quote", [])
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
        key=lambda x: x["date"],
    )


def fetch_yahoo(
    symbol: str,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    start = int(
        datetime.strptime(
            START_DATE,
            "%Y-%m-%d",
        )
        .replace(
            tzinfo=timezone.utc
        )
        .timestamp()
    )

    end = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    params = {
        "period1": start,
        "period2": end,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    last_error = ""

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

            payload = response.json()

            rows = parse_yahoo_payload(
                payload
            )

            if rows:

                return (
                    rows,
                    "Yahoo fallback",
                )

            last_error = (
                "Yahoo data=0"
            )

        except Exception as exc:

            last_error = str(exc)

        if attempt < MAX_RETRIES:

            time.sleep(
                RETRY_DELAY * attempt
            )

    return (
        [],
        f"Yahoo failed: {last_error}",
    )


# ============================================================
# FETCH STOCK
# ============================================================

def fetch_stock_history(
    item: Dict[str, str],
) -> Tuple[
    List[Dict[str, Any]],
    str,
    str,
]:

    code = item["code"]
    market = item["market"]
    symbol = item["symbol"]

    # ========================================================
    # TPEX → 官方優先
    # ========================================================

    if market == "TWO":

        official_rows, official_error = (
            fetch_tpex_history(code)
        )

        if len(official_rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            source = (
                "TPEx official"
                if len(official_rows)
                >= MIN_HISTORY_ROWS
                else
                "TPEx official (short history)"
            )

            return (
                official_rows,
                source,
                "",
            )

        log(
            f"⚠️ {symbol} TPEx 官方不足"
        )

        if official_error:

            log(
                f"   原因："
                f"{official_error}"
            )

        log(
            f"→ {symbol} 啟動 Yahoo 最後備援"
        )

        yahoo_rows, yahoo_source = (
            fetch_yahoo(symbol)
        )

        if yahoo_rows:

            return (
                yahoo_rows,
                yahoo_source,
                official_error
                or "TPEx official insufficient",
            )

        return (
            [],
            "",
            official_error
            or yahoo_source,
        )

    # ========================================================
    # TWSE → 官方優先
    # ========================================================

    official_rows, official_error = (
        fetch_twse_history(code)
    )

    if len(official_rows) >= (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        source = (
            "TWSE official"
            if len(official_rows)
            >= MIN_HISTORY_ROWS
            else
            "TWSE official (short history)"
        )

        return (
            official_rows,
            source,
            "",
        )

    log(
        f"⚠️ {symbol} TWSE 官方不足"
    )

    if official_error:

        log(
            f"   原因："
            f"{official_error}"
        )

    log(
        f"→ {symbol} 啟動 Yahoo 最後備援"
    )

    yahoo_rows, yahoo_source = (
        fetch_yahoo(symbol)
    )

    if yahoo_rows:

        return (
            yahoo_rows,
            yahoo_source,
            official_error
            or "TWSE official insufficient",
        )

    return (
        [],
        "",
        official_error
        or yahoo_source,
    )


# ============================================================
# NORMALIZE PRICE ROWS
# ============================================================

def normalize_price_rows(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    result = {}

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        date_value = parse_iso_date(
            row.get("date")
        )

        if not date_value:
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

        result[date_value] = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        result.values(),
        key=lambda x: x["date"],
    )


# ============================================================
# FETCH ONE
# ============================================================

def fetch_one(
    item: Dict[str, str],
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    rows, source, reason = (
        fetch_stock_history(item)
    )

    rows = normalize_price_rows(
        rows
    )

    symbol = item["symbol"]

    if len(rows) < (
        ABSOLUTE_MIN_HISTORY_ROWS
    ):

        return (
            None,
            reason
            or f"history_rows={len(rows)}",
        )

    status = (
        "complete"
        if len(rows) >= MIN_HISTORY_ROWS
        else "short_history"
    )

    result = {
        "symbol": symbol,
        "code": item["code"],
        "market": item["market"],
        "name": item["name"],
        "source": source,
        "history_rows": len(rows),
        "history_status": status,
        "latest_date": rows[-1]["date"],
        "prices": rows,
    }

    if reason:

        result["fallback_reason"] = reason

    log(
        f"✓ {symbol} "
        f"{item['name']} "
        f"→ {len(rows)} 筆 "
        f"→ {source}"
    )

    return (
        result,
        "",
    )


# ============================================================
# SHARDS
# ============================================================

def build_shards(
    results: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:

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
# VALIDATE SHARD
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
            f"{path.name} symbol 不一致"
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
                f"{symbol} 歷史不足："
                f"{len(rows)}"
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
                    f"{symbol} price row 欄位不足"
                )

            current = str(
                row["date"]
            )

            if previous and current < previous:

                raise RuntimeError(
                    f"{symbol} 日期未排序"
                )

            previous = current


# ============================================================
# MANIFEST
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[str, Dict[str, Any]],
    universe_count: int,
) -> Dict[str, Any]:

    complete = 0
    short = 0

    sources = {}
    latest_dates = []

    for result in results.values():

        if result["history_status"] == "complete":
            complete += 1
        else:
            short += 1

        source = result["source"]

        sources[source] = (
            sources.get(source, 0) + 1
        )

        latest_dates.append(
            result["latest_date"]
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_stock_count": universe_count,
        "price_stock_count": len(results),
        "complete_history_count": complete,
        "short_history_count": short,
        "failed_count": (
            universe_count - len(results)
        ),
        "min_history_rows": MIN_HISTORY_ROWS,
        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),
        "sources": sources,
        "latest_date": (
            max(latest_dates)
            if latest_dates
            else None
        ),
        "files": shard_files,
    }


# ============================================================
# WRITE OUTPUT
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[str, Dict[str, Any]],
    universe_count: int,
) -> None:

    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    shards = build_shards(
        results
    )

    shard_files = []

    symbols = sorted(
        results.keys()
    )

    for index, shard in enumerate(
        shards,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = (
            temp_dir / filename
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
        universe_count,
    )

    manifest_path = (
        temp_dir / "manifest.json"
    )

    save_json(
        manifest_path,
        manifest,
    )

    # --------------------------------------------------------
    # 最終 Manifest Gate
    # --------------------------------------------------------

    if manifest[
        "universe_stock_count"
    ] != universe_count:

        raise RuntimeError(
            "manifest universe_stock_count 錯誤"
        )

    if manifest[
        "price_stock_count"
    ] != universe_count:

        raise RuntimeError(
            "價格資料不是完整 Universe："
            f"{manifest['price_stock_count']}/"
            f"{universe_count}"
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

    backup = (
        DATA_DIR
        / ".prices_backup"
    )

    if backup.exists():

        shutil.rmtree(
            backup
        )

    if OUTPUT_DIR.exists():

        OUTPUT_DIR.rename(
            backup
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

        if backup.exists():

            backup.rename(
                OUTPUT_DIR
            )

        raise

    if backup.exists():

        shutil.rmtree(
            backup
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

    log("")
    log(
        f"開始官方資料抓取："
        f"{universe_count} 檔 STOCK"
    )

    # --------------------------------------------------------
    # Results
    # --------------------------------------------------------

    results = {}
    failures = {}

    source_counts = {}

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

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

                failures[
                    symbol
                ] = reason

                log(
                    f"❌ {symbol} "
                    f"→ {reason}"
                )

            else:

                results[
                    symbol
                ] = result

                source = result[
                    "source"
                ]

                source_counts[
                    source
                ] = (
                    source_counts.get(
                        source,
                        0,
                    ) + 1
                )

        except Exception as exc:

            failures[
                symbol
            ] = str(exc)

            log(
                f"❌ {symbol} "
                f"未預期錯誤："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # Summary
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
    # Missing
    # --------------------------------------------------------

    expected = {
        item["symbol"]
        for item in universe
    }

    actual = set(
        results.keys()
    )

    missing = expected - actual

    if missing:

        log("")
        log(
            f"⚠️ 缺少價格資料："
            f"{len(missing)} 檔"
        )

        for symbol in sorted(
            missing
        ):

            log(
                f"  {symbol}: "
                f"{failures.get(symbol, '')}"
            )

    # --------------------------------------------------------
    # 1944 → 1944 Gate
    # --------------------------------------------------------

    if success_rate < MIN_SUCCESS_RATE:

        log(
            "❌ 成功率低於安全門檻"
        )

        return 1

    # --------------------------------------------------------
    # Temporary
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
        # 7794 FINAL CHECK
        # ----------------------------------------------------

        if "7794.TWO" in expected:

            log("")
            log(
                "=" * 72
            )
            log(
                "7794.TWO 最終驗證"
            )
            log(
                "=" * 72
            )

            if "7794.TWO" not in results:

                log(
                    "❌ 7794.TWO 無價格資料"
                )

                raise RuntimeError(
                    "7794.TWO 官方/備援資料失敗"
                )

            target = results[
                "7794.TWO"
            ]

            log(
                f"source       = "
                f"{target['source']}"
            )

            log(
                f"history_rows = "
                f"{target['history_rows']}"
            )

            log(
                f"latest_date  = "
                f"{target['latest_date']}"
            )

            log(
                f"status       = "
                f"{target['history_status']}"
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
            f"❌ 價格資料建置失敗："
            f"{exc}"
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
        time.time() - started
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

    if "7794.TWO" in results:

        log(
            "✓ 7794.TWO："
            "已成功進入價格資料鏈"
        )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"✓ fetch_prices.py {VERSION} 完成"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())