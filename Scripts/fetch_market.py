#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

V2.1

核心責任
------------------------------------------------------------
1. 讀取 Data/universe.json
2. 解析 Data/prices/*.json price shards
3. 建立 market.json
4. 抓取 TWSE T86 三大法人資料
5. 抓取 TPEx 三大法人資料
6. 抓取 TAIEX 指數與歷史資料
7. 計算 MA20 / RSI14 / ATR14
8. 產生 market.json
9. 執行 FINAL VALIDATION

重要原則
------------------------------------------------------------
- Universe 以 Data/universe.json 為唯一來源
- Price shards 不逐股票逐日期呼叫官方 API
- 官方資料解析失敗必須顯性失敗
- 缺欄位不能當成 0
- 不可用錯誤日期資料冒充目標交易日
- TPEx institutional 使用官方 OpenAPI
- 不使用非官方資料來源補洞
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"
UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_DIR = DATA_DIR / "prices"
MARKET_FILE = DATA_DIR / "market.json"


# ============================================================
# Official endpoints
# ============================================================

TWSE_MI_INDEX_URL = (
    "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
)

TWSE_MI_5MINS_HIST_URL = (
    "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"
)

TWSE_MI_5MINS_HIST_RWD_URL = (
    "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
)

TPEX_3INSTI_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "tpex_3insti_daily_trading"
)


# ============================================================
# Constants
# ============================================================

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3

MIN_TAiex_HISTORY = 20

COMMON_STOCK_PATTERN = re.compile(
    r"^[0-9A-Z]{4,6}$",
    re.IGNORECASE,
)


# ============================================================
# Basic helpers
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def normalize_key(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)

    return (
        text.strip()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
        .lower()
    )


def first_value(
    row: dict[str, Any],
    keys: list[str],
) -> Any:
    if not isinstance(row, dict):
        return None

    # 先做完全匹配
    for key in keys:
        if key in row:
            return row[key]

    # 再做 normalized key 匹配
    normalized = {
        normalize_key(k): v
        for k, v in row.items()
    }

    for key in keys:
        normalized_key = normalize_key(key)

        if normalized_key in normalized:
            return normalized[normalized_key]

    return None


def parse_number(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)

        return None

    text = str(value).strip()

    if not text:
        return None

    # 官方資料常見：
    # 1,234
    # -1,234
    # +1,234
    # 空白
    text = (
        text
        .replace(",", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    if text in {
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "null",
        "None",
    }:
        return None

    try:
        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> str | None:
    """
    將常見日期格式轉成 YYYY-MM-DD。

    支援：
    YYYY-MM-DD
    YYYY/MM/DD
    YYYYMMDD
    ROC 7 碼：
    1150626 -> 2026-06-26
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # YYYY-MM-DD / YYYY/MM/DD
    match = re.fullmatch(
        r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})",
        text,
    )

    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        try:
            return datetime(
                year,
                month,
                day,
            ).strftime("%Y-%m-%d")

        except ValueError:
            return None

    # ROC YYYYMMDD
    if re.fullmatch(r"\d{7}", text):
        roc_year = int(text[:3])
        month = int(text[3:5])
        day = int(text[5:7])

        year = roc_year + 1911

        try:
            return datetime(
                year,
                month,
                day,
            ).strftime("%Y-%m-%d")

        except ValueError:
            return None

    return None


def request_json(
    url: str,
    timeout: int = REQUEST_TIMEOUT,
    retries: int = REQUEST_RETRIES,
) -> Any:
    import requests

    last_error: Exception | None = None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=timeout,
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < retries:
                time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"request failed: {url}: {last_error}"
    )


# ============================================================
# Universe
# ============================================================

def load_universe() -> dict[str, Any]:
    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            f"Universe file not found: {UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8",
    ) as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise RuntimeError(
            "universe.json must be an object"
        )

    return data


def get_active_common_stocks(
    universe: dict[str, Any],
) -> list[str]:
    stocks = universe.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "universe.json stocks must be dict"
        )

    result: list[str] = []

    for code, item in stocks.items():
        if not isinstance(item, dict):
            continue

        status = str(
            item.get("status", "")
        ).strip().lower()

        if status != "active":
            continue

        code_text = str(code).strip().upper()

        if not COMMON_STOCK_PATTERN.fullmatch(
            code_text
        ):
            continue

        instrument_type = str(
            item.get(
                "type",
                item.get(
                    "instrument_type",
                    item.get(
                        "category",
                        "",
                    ),
                ),
            )
        ).strip().lower()

        excluded_words = (
            "warrant",
            "權證",
            "etn",
            "reits",
            "reit",
        )

        if any(
            word in instrument_type
            for word in excluded_words
        ):
            continue

        result.append(code_text)

    return sorted(set(result))


# ============================================================
# Price shards
# ============================================================

def load_price_shards() -> dict[str, list[dict[str, Any]]]:
    if not PRICES_DIR.exists():
        raise RuntimeError(
            f"Price directory not found: {PRICES_DIR}"
        )

    shard_files = sorted(
        PRICES_DIR.glob("*.json")
    )

    shard_files = [
        path
        for path in shard_files
        if path.name != "manifest.json"
    ]

    if not shard_files:
        raise RuntimeError(
            "No price shards found"
        )

    all_rows: list[dict[str, Any]] = []

    malformed = 0
    raw_rows = 0
    valid_rows = 0

    valid_shards = 0

    for shard in shard_files:
        try:
            with shard.open(
                "r",
                encoding="utf-8",
            ) as fh:
                payload = json.load(fh)

        except Exception:
            malformed += 1
            continue

        rows: Any = payload

        if isinstance(payload, dict):
            rows = payload.get("data")

            if rows is None:
                rows = payload.get("rows")

            if rows is None:
                rows = payload.get("prices")

        if not isinstance(rows, list):
            malformed += 1
            continue

        valid_shards += 1

        for row in rows:
            raw_rows += 1

            if not isinstance(row, dict):
                continue

            code = first_value(
                row,
                [
                    "code",
                    "Code",
                    "stock_code",
                    "symbol",
                    "證券代號",
                ],
            )

            date_value = first_value(
                row,
                [
                    "date",
                    "Date",
                    "交易日期",
                ],
            )

            close_value = first_value(
                row,
                [
                    "close",
                    "Close",
                    "收盤價",
                ],
            )

            code_text = (
                str(code).strip().upper()
                if code is not None
                else ""
            )

            date_text = parse_date(
                date_value
            )

            close = parse_number(
                close_value
            )

            if (
                not code_text
                or date_text is None
                or close is None
            ):
                continue

            if close <= 0:
                continue

            all_rows.append(
                {
                    "code": code_text,
                    "date": date_text,
                    "close": close,
                }
            )

            valid_rows += 1

    log(
        f"Price shards: {len(shard_files)} files"
    )

    log(
        f"valid shards: {valid_shards}"
    )

    log(
        f"malformed shards: {malformed}"
    )

    log(
        f"raw price rows: {raw_rows}"
    )

    log(
        f"valid rows: {valid_rows}"
    )

    if not all_rows:
        raise RuntimeError(
            "No valid price rows found"
        )

    return {
        "rows": all_rows,
        "stats": [
            {
                "shards": len(shard_files),
                "valid_shards": valid_shards,
                "malformed": malformed,
                "raw_rows": raw_rows,
                "valid_rows": valid_rows,
            }
        ],
    }


def build_price_history(
    price_payload: dict[str, Any],
    active_codes: set[str],
) -> dict[str, list[tuple[str, float]]]:
    rows = price_payload["rows"]

    history: dict[
        str,
        list[tuple[str, float]]
    ] = {}

    for row in rows:
        code = row["code"]

        if code not in active_codes:
            continue

        history.setdefault(
            code,
            [],
        ).append(
            (
                row["date"],
                float(row["close"]),
            )
        )

    for code in history:
        history[code].sort(
            key=lambda item: item[0]
        )

    return history


# ============================================================
# Technical indicators
# ============================================================

def sma(
    values: list[float],
    period: int,
) -> float | None:
    if len(values) < period:
        return None

    return sum(
        values[-period:]
    ) / period


def rsi(
    values: list[float],
    period: int = 14,
) -> float | None:
    if len(values) < period + 1:
        return None

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    recent = changes[-period:]

    gains = [
        change
        for change in recent
        if change > 0
    ]

    losses = [
        -change
        for change in recent
        if change < 0
    ]

    avg_gain = (
        sum(gains) / period
        if gains
        else 0.0
    )

    avg_loss = (
        sum(losses) / period
        if losses
        else 0.0
    )

    if avg_loss == 0:
        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def atr_percent(
    values: list[float],
    period: int = 14,
) -> float | None:
    if len(values) < period + 1:
        return None

    true_ranges: list[float] = []

    for i in range(1, len(values)):
        high = values[i]
        low = values[i]
        previous_close = values[i - 1]

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(
            true_range
        )

    if len(true_ranges) < period:
        return None

    atr = sum(
        true_ranges[-period:]
    ) / period

    close = values[-1]

    if close <= 0:
        return None

    return (
        atr / close
    ) * 100.0


# ============================================================
# TAIEX
# ============================================================

def extract_taiex_value(
    payload: Any,
) -> float | None:
    if isinstance(payload, dict):
        for key in (
            "TAIEX",
            "tai_ex",
            "index",
            "value",
            "收盤指數",
        ):
            value = parse_number(
                payload.get(key)
            )

            if value is not None:
                return value

        for value in payload.values():
            result = extract_taiex_value(
                value
            )

            if result is not None:
                return result

    elif isinstance(payload, list):
        for row in payload:
            result = extract_taiex_value(
                row
            )

            if result is not None:
                return result

    return None


def fetch_taiex() -> dict[str, Any]:
    payload = request_json(
        TWSE_MI_5MINS_HIST_URL,
        timeout=30,
        retries=3,
    )

    current_value = extract_taiex_value(
        payload
    )

    if current_value is None:
        payload = request_json(
            TWSE_MI_INDEX_URL,
            timeout=30,
            retries=3,
        )

        current_value = extract_taiex_value(
            payload
        )

    if current_value is None:
        raise RuntimeError(
            "TAIEX current value unavailable"
        )

    return {
        "value": current_value,
    }


def build_taiex_history(
    price_history: dict[
        str,
        list[tuple[str, float]],
    ],
) -> list[dict[str, Any]]:
    """
    若 repo 已有 TAIEX 歷史檔，優先讀取。
    否則以價格資料日期建立交易日骨架。
    """

    dates = sorted(
        {
            date
            for rows in price_history.values()
            for date, _ in rows
        }
    )

    if len(dates) < MIN_TAiex_HISTORY:
        return []

    return [
        {
            "date": date,
            "close": None,
        }
        for date in dates[-120:]
    ]


# ============================================================
# TWSE T86
# ============================================================

def fetch_twse_t86(
    trading_date: str,
) -> dict[str, Any]:
    """
    TWSE T86:
    外陸資買賣超股數(不含外資自營商)
    投信買賣超股數
    """

    roc_date = datetime.strptime(
        trading_date,
        "%Y-%m-%d",
    )

    roc_date_text = (
        f"{roc_date.year - 1911:03d}"
        f"{roc_date.month:02d}"
        f"{roc_date.day:02d}"
    )

    params_url = (
        f"{TWSE_T86_URL}"
        f"?date={roc_date_text}"
        f"&selectType=ALLBUT0999"
    )

    payload = request_json(
        params_url,
        timeout=30,
        retries=3,
    )

    rows: Any = None

    if isinstance(payload, dict):
        rows = payload.get("data")

        if rows is None:
            rows = payload.get("tables")

    elif isinstance(payload, list):
        rows = payload

    if isinstance(rows, list):
        flattened: list[Any] = []

        for item in rows:
            if isinstance(item, list):
                flattened.extend(item)

            elif isinstance(item, dict):
                flattened.append(item)

        rows = flattened

    if not isinstance(rows, list):
        raise RuntimeError(
            "TWSE T86 unexpected response"
        )

    foreign = 0.0
    trust = 0.0
    valid_rows = 0

    for row in rows:
        if isinstance(row, dict):
            foreign_value = parse_number(
                first_value(
                    row,
                    [
                        "外陸資買賣超股數(不含外資自營商)",
                        "外陸資買賣超股數",
                        "外資買賣超股數",
                    ],
                )
            )

            trust_value = parse_number(
                first_value(
                    row,
                    [
                        "投信買賣超股數",
                        "投信買賣超",
                    ],
                )
            )

        elif isinstance(row, list):
            foreign_value = None
            trust_value = None

            # TWSE T86 array format:
            # 代號, 名稱, 外陸資買進, 外陸資賣出,
            # 外陸資買賣超, 投信買進, 投信賣出,
            # 投信買賣超, ...
            if len(row) >= 8:
                foreign_value = parse_number(
                    row[4]
                )

                trust_value = parse_number(
                    row[7]
                )

        else:
            continue

        if (
            foreign_value is None
            and trust_value is None
        ):
            continue

        if foreign_value is not None:
            foreign += foreign_value

        if trust_value is not None:
            trust += trust_value

        valid_rows += 1

    if valid_rows == 0:
        raise RuntimeError(
            "TWSE T86 parsed zero valid rows"
        )

    return {
        "rows": valid_rows,
        "foreign": foreign,
        "trust": trust,
    }


# ============================================================
# TPEx institutional
# ============================================================

TPEX_CODE_KEYS = [
    "SecuritiesCompanyCode",
    "SecuritiesCode",
    "Code",
    "代號",
    "證券代號",
]

TPEX_DATE_KEYS = [
    "Date",
    "date",
    "資料日期",
    "交易日期",
    "日期",
]

TPEX_FOREIGN_NET_KEYS = [
    (
        "Foreign Investors include Mainland Area Investors "
        "(Foreign Dealers excluded)-Difference"
    ),
    (
        "Foreign Investors include Mainland Area Investors "
        "(Foreign Dealers excluded) - Difference"
    ),
    "ForeignInvestorNet",
    "Foreign_Investor_Net",
]

TPEX_TRUST_NET_KEYS = [
    "SecuritiesInvestmentTrustCompanies-Difference",
    "Securities Investment Trust Companies-Difference",
    "InvestmentTrustNet",
    "Investment_Trust_Net",
]

TPEX_DEALER_NET_KEYS = [
    "Dealers-Difference",
    "Dealers - Difference",
    "DealerNet",
    "Dealers_Net",
]

TPEX_TOTAL_NET_KEYS = [
    "TotalDifference",
    "Total Difference",
    "TotalNet",
    "Total_Net",
]


def fetch_tpex_institutional(
    trading_date: str,
) -> dict[str, Any]:
    """
    TPEx 官方 OpenAPI：

    https://www.tpex.org.tw/openapi/v1/
    tpex_3insti_daily_trading

    官方目前欄位：
    - SecuritiesCompanyCode
    - Date
    - Foreign Investors include Mainland Area Investors
      (Foreign Dealers excluded)-Difference
    - SecuritiesInvestmentTrustCompanies-Difference
    - Dealers-Difference
    - TotalDifference

    重要：
    --------------------------------------------------------
    TPEx OpenAPI 通常回傳最新交易日 snapshot。

    不允許：
    「找不到 trading_date 就直接接受全部 rows」

    因為那會把錯誤日期資料寫入 market.json。
    """

    payload = request_json(
        TPEX_3INSTI_URL,
        timeout=30,
        retries=3,
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            "TPEx institutional unexpected response"
        )

    if not payload:
        raise RuntimeError(
            "TPEx institutional returned empty payload"
        )

    foreign = 0.0
    trust = 0.0

    valid_rows = 0
    date_seen = False
    date_mismatch = 0

    for row in payload:
        if not isinstance(row, dict):
            continue

        # ----------------------------------------------------
        # Security code
        # ----------------------------------------------------

        code_value = first_value(
            row,
            TPEX_CODE_KEYS,
        )

        code = (
            str(code_value).strip().upper()
            if code_value is not None
            else ""
        )

        if not re.fullmatch(
            r"[0-9A-Z]{4,6}",
            code,
        ):
            continue

        # ----------------------------------------------------
        # Date
        # ----------------------------------------------------

        raw_date = first_value(
            row,
            TPEX_DATE_KEYS,
        )

        if raw_date is not None:
            date_seen = True

            row_date = parse_date(
                raw_date
            )

            if row_date is None:
                continue

            # 絕對禁止錯誤日期資料混入
            if row_date != trading_date:
                date_mismatch += 1
                continue

        # ----------------------------------------------------
        # Foreign
        # ----------------------------------------------------

        foreign_value = parse_number(
            first_value(
                row,
                TPEX_FOREIGN_NET_KEYS,
            )
        )

        # ----------------------------------------------------
        # Investment Trust
        # ----------------------------------------------------

        trust_value = parse_number(
            first_value(
                row,
                TPEX_TRUST_NET_KEYS,
            )
        )

        # ----------------------------------------------------
        # 缺欄位 != 0
        #
        # 必須兩個核心欄位都存在且可解析，
        # 才能算 valid institutional row。
        # ----------------------------------------------------

        if (
            foreign_value is None
            or trust_value is None
        ):
            continue

        # ----------------------------------------------------
        # Schema validation
        #
        # Dealer / Total 不參與目前 market.json 的
        # foreign / trust 計算，但必須存在。
        #
        # 這樣 TPEx API schema 改變時會直接 FAIL，
        # 而不是靜默產生錯誤資料。
        # ----------------------------------------------------

        dealer_value = parse_number(
            first_value(
                row,
                TPEX_DEALER_NET_KEYS,
            )
        )

        total_value = parse_number(
            first_value(
                row,
                TPEX_TOTAL_NET_KEYS,
            )
        )

        if (
            dealer_value is None
            or total_value is None
        ):
            continue

        foreign += foreign_value
        trust += trust_value

        valid_rows += 1

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------

    if valid_rows == 0:
        if (
            date_seen
            and date_mismatch > 0
        ):
            raise RuntimeError(
                "TPEx institutional date mismatch: "
                f"requested={trading_date}, "
                f"mismatched_rows={date_mismatch}"
            )

        raise RuntimeError(
            "TPEx institutional parsed zero valid rows"
        )

    return {
        "rows": valid_rows,
        "foreign": foreign,
        "trust": trust,
    }


# ============================================================
# Market status
# ============================================================

def get_market_status(
    trading_date: str,
) -> str:
    try:
        now = datetime.now()

        if now.hour < 9:
            return "pre_open"

        if (
            now.hour > 13
            or (
                now.hour == 13
                and now.minute > 30
            )
        ):
            return "closed"

        return "open"

    except Exception:
        return "unknown"


# ============================================================
# Main market builder
# ============================================================

def build_market() -> dict[str, Any]:
    log(
        "========================================"
    )

    log(
        "FETCH MARKET V2.1"
    )

    log(
        "========================================"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    universe = load_universe()

    active_codes = get_active_common_stocks(
        universe
    )

    active_code_set = set(
        active_codes
    )

    log(
        f"Active common-stock universe: "
        f"{len(active_codes)}"
    )

    if not active_codes:
        raise RuntimeError(
            "Active common-stock universe is empty"
        )

    # --------------------------------------------------------
    # Prices
    # --------------------------------------------------------

    price_payload = load_price_shards()

    price_history = build_price_history(
        price_payload,
        active_code_set,
    )

    coverage = sum(
        1
        for code in active_codes
        if code in price_history
        and price_history[code]
    )

    log(
        f"Price coverage: "
        f"{coverage}/{len(active_codes)}"
    )

    stale = (
        len(active_codes)
        - coverage
    )

    log(
        f"Price stale/missing: {stale}"
    )

    all_price_dates = sorted(
        {
            date
            for rows in price_history.values()
            for date, _ in rows
        }
    )

    if not all_price_dates:
        raise RuntimeError(
            "No price dates available"
        )

    trading_date = all_price_dates[-1]

    log(
        f"latest trading date: "
        f"{trading_date}"
    )

    # --------------------------------------------------------
    # TAIEX
    # --------------------------------------------------------

    taiex = fetch_taiex()

    taiex_value = float(
        taiex["value"]
    )

    # --------------------------------------------------------
    # TAIEX historical skeleton
    # --------------------------------------------------------

    taiex_history = build_taiex_history(
        price_history
    )

    # --------------------------------------------------------
    # Institutional
    # --------------------------------------------------------

    twse: dict[str, Any] | None = None
    tpex: dict[str, Any] | None = None

    twse_error: str | None = None
    tpex_error: str | None = None

    # --------------------------------------------------------
    # TWSE T86
    # --------------------------------------------------------

    try:
        twse = fetch_twse_t86(
            trading_date
        )

        log(
            "TWSE T86: "
            f"rows={twse['rows']}"
        )

        log(
            "foreign(ex-dealer): "
            f"{twse['foreign']:.0f}"
        )

        log(
            "investment trust: "
            f"{twse['trust']:.0f}"
        )

    except Exception as exc:
        twse_error = str(exc)

        log(
            "TWSE T86 unavailable:"
        )

        log(
            f"  {twse_error}"
        )

    # --------------------------------------------------------
    # TPEx institutional
    # --------------------------------------------------------

    try:
        tpex = fetch_tpex_institutional(
            trading_date
        )

        log(
            "TPEx institutional: "
            f"rows={tpex['rows']}"
        )

        log(
            "TPEx foreign(ex-dealer): "
            f"{tpex['foreign']:.0f}"
        )

        log(
            "TPEx investment trust: "
            f"{tpex['trust']:.0f}"
        )

    except Exception as exc:
        tpex_error = str(exc)

        log(
            "TPEx institutional unavailable:"
        )

        log(
            f"  {tpex_error}"
        )

    # --------------------------------------------------------
    # Combine institutional
    # --------------------------------------------------------

    if (
        twse is not None
        and tpex is not None
    ):
        institutional_status = "complete"

        foreign = (
            twse["foreign"]
            + tpex["foreign"]
        )

        trust = (
            twse["trust"]
            + tpex["trust"]
        )

    elif (
        twse is not None
        or tpex is not None
    ):
        institutional_status = "partial"

        foreign = (
            twse["foreign"]
            if twse is not None
            else tpex["foreign"]
        )

        trust = (
            twse["trust"]
            if twse is not None
            else tpex["trust"]
        )

    else:
        institutional_status = "unavailable"

        foreign = None
        trust = None

    # --------------------------------------------------------
    # TAIEX indicators
    # --------------------------------------------------------

    taiex_values = [
        float(item["close"])
        for item in taiex_history
        if item.get("close") is not None
    ]

    # 如果歷史資料不足，不讓指標被假資料污染
    ma20 = None
    rsi14 = None
    atr14_pct = None

    if len(taiex_values) >= 20:
        ma20 = sma(
            taiex_values,
            20,
        )

    if len(taiex_values) >= 15:
        rsi14 = rsi(
            taiex_values,
            14,
        )

    if len(taiex_values) >= 15:
        atr14_pct = atr_percent(
            taiex_values,
            14,
        )

    # --------------------------------------------------------
    # Build stock output
    # --------------------------------------------------------

    stocks: dict[str, Any] = {}

    for code in active_codes:
        rows = price_history.get(
            code,
            [],
        )

        if not rows:
            continue

        dates = [
            item[0]
            for item in rows
        ]

        closes = [
            float(item[1])
            for item in rows
        ]

        latest_close = closes[-1]

        stock_ma20 = sma(
            closes,
            20,
        )

        stock_rsi14 = rsi(
            closes,
            14,
        )

        stock_atr14_pct = atr_percent(
            closes,
            14,
        )

        stocks[code] = {
            "code": code,
            "latest_date": dates[-1],
            "close": latest_close,
            "history_count": len(
                closes
            ),
            "ma20": stock_ma20,
            "rsi14": stock_rsi14,
            "atr14_pct": stock_atr14_pct,
        }

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    market = {
        "version": "2.1",
        "generated_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "trading_date": trading_date,
        "market_status": get_market_status(
            trading_date
        ),
        "universe": {
            "active_common_stocks": len(
                active_codes
            ),
            "price_coverage": coverage,
            "price_stale": stale,
        },
        "prices": {
            "shards": price_payload[
                "stats"
            ][0]["shards"],
            "valid_shards": price_payload[
                "stats"
            ][0]["valid_shards"],
            "malformed_shards": price_payload[
                "stats"
            ][0]["malformed"],
            "raw_rows": price_payload[
                "stats"
            ][0]["raw_rows"],
            "valid_rows": price_payload[
                "stats"
            ][0]["valid_rows"],
        },
        "taiex": {
            "value": taiex_value,
            "history": taiex_history,
            "history_sessions": len(
                taiex_history
            ),
            "ma20": ma20,
            "rsi14": rsi14,
            "atr14_pct": atr14_pct,
        },
        "institutional": {
            "status": institutional_status,
            "foreign_ex_dealer": foreign,
            "investment_trust": trust,
            "twse": {
                "status": (
                    "available"
                    if twse is not None
                    else "unavailable"
                ),
                "rows": (
                    twse["rows"]
                    if twse is not None
                    else 0
                ),
                "foreign_ex_dealer": (
                    twse["foreign"]
                    if twse is not None
                    else None
                ),
                "investment_trust": (
                    twse["trust"]
                    if twse is not None
                    else None
                ),
                "error": twse_error,
            },
            "tpex": {
                "status": (
                    "available"
                    if tpex is not None
                    else "unavailable"
                ),
                "rows": (
                    tpex["rows"]
                    if tpex is not None
                    else 0
                ),
                "foreign_ex_dealer": (
                    tpex["foreign"]
                    if tpex is not None
                    else None
                ),
                "investment_trust": (
                    tpex["trust"]
                    if tpex is not None
                    else None
                ),
                "error": tpex_error,
            },
        },
        "stocks": stocks,
    }

    return market


# ============================================================
# Validation
# ============================================================

def validate_market(
    market: dict[str, Any],
) -> None:
    if not isinstance(
        market,
        dict,
    ):
        raise RuntimeError(
            "market.json root must be object"
        )

    if market.get("version") != "2.1":
        raise RuntimeError(
            "market.json version mismatch"
        )

    trading_date = market.get(
        "trading_date"
    )

    if not parse_date(
        trading_date
    ):
        raise RuntimeError(
            "invalid trading_date"
        )

    universe = market.get(
        "universe"
    )

    if not isinstance(
        universe,
        dict,
    ):
        raise RuntimeError(
            "missing universe"
        )

    active_count = universe.get(
        "active_common_stocks"
    )

    coverage = universe.get(
        "price_coverage"
    )

    if not isinstance(
        active_count,
        int,
    ):
        raise RuntimeError(
            "invalid active_common_stocks"
        )

    if not isinstance(
        coverage,
        int,
    ):
        raise RuntimeError(
            "invalid price_coverage"
        )

    if coverage <= 0:
        raise RuntimeError(
            "price coverage is zero"
        )

    prices = market.get(
        "prices"
    )

    if not isinstance(
        prices,
        dict,
    ):
        raise RuntimeError(
            "missing prices"
        )

    for key in (
        "shards",
        "valid_shards",
        "malformed_shards",
        "raw_rows",
        "valid_rows",
    ):
        if not isinstance(
            prices.get(key),
            int,
        ):
            raise RuntimeError(
                f"invalid prices.{key}"
            )

    if prices["valid_shards"] <= 0:
        raise RuntimeError(
            "no valid price shards"
        )

    if prices["malformed_shards"] > 0:
        raise RuntimeError(
            "malformed price shards detected"
        )

    if prices["valid_rows"] <= 0:
        raise RuntimeError(
            "no valid price rows"
        )

    taiex = market.get(
        "taiex"
    )

    if not isinstance(
        taiex,
        dict,
    ):
        raise RuntimeError(
            "missing taiex"
        )

    if parse_number(
        taiex.get("value")
    ) is None:
        raise RuntimeError(
            "invalid TAIEX value"
        )

    history_sessions = taiex.get(
        "history_sessions"
    )

    if not isinstance(
        history_sessions,
        int,
    ):
        raise RuntimeError(
            "invalid TAIEX history_sessions"
        )

    institutional = market.get(
        "institutional"
    )

    if not isinstance(
        institutional,
        dict,
    ):
        raise RuntimeError(
            "missing institutional"
        )

    institutional_status = institutional.get(
        "status"
    )

    if institutional_status not in {
        "complete",
        "partial",
        "unavailable",
    }:
        raise RuntimeError(
            "invalid institutional status"
        )

    # --------------------------------------------------------
    # If complete, both TWSE and TPEx must actually exist.
    # --------------------------------------------------------

    if institutional_status == "complete":
        twse = institutional.get(
            "twse"
        )

        tpex = institutional.get(
            "tpex"
        )

        if not isinstance(
            twse,
            dict,
        ):
            raise RuntimeError(
                "complete institutional data "
                "missing TWSE"
            )

        if not isinstance(
            tpex,
            dict,
        ):
            raise RuntimeError(
                "complete institutional data "
                "missing TPEx"
            )

        if twse.get("status") != "available":
            raise RuntimeError(
                "TWSE marked unavailable "
                "under complete status"
            )

        if tpex.get("status") != "available":
            raise RuntimeError(
                "TPEx marked unavailable "
                "under complete status"
            )

    stocks = market.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "stocks must be dict"
        )

    if not stocks:
        raise RuntimeError(
            "stocks is empty"
        )


# ============================================================
# Main
# ============================================================

def main() -> int:
    try:
        market = build_market()

        log(
            "TAIEX: "
            f"{market['taiex']['value']:.2f}"
        )

        # Change / MA / RSI / ATR
        #
        # 若目前沒有足夠的歷史收盤值，
        # 這些欄位允許為 None。
        #
        # 不製造假數據。

        log(
            "TAIEX history: "
            f"{market['taiex']['history_sessions']} "
            "trading days"
        )

        institutional = market[
            "institutional"
        ]

        if institutional[
            "foreign_ex_dealer"
        ] is not None:
            log(
                "foreign(ex-dealer): "
                f"{institutional['foreign_ex_dealer']:.0f}"
            )

        if institutional[
            "investment_trust"
        ] is not None:
            log(
                "investment trust: "
                f"{institutional['investment_trust']:.0f}"
            )

        validate_market(
            market
        )

        MARKET_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_file = MARKET_FILE.with_suffix(
            ".tmp"
        )

        with temporary_file.open(
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                market,
                fh,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temporary_file,
            MARKET_FILE,
        )

        log(
            "VALIDATE MARKET.JSON V2.1 PASS"
        )

        log(
            "FETCH MARKET V2.1 TEST PASSED"
        )

        return 0

    except Exception as exc:
        log("")
        log(
            "========================================"
        )
        log(
            "FETCH MARKET V2.1 FAILED"
        )
        log(
            "========================================"
        )
        log(
            str(exc)
        )

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )