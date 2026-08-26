#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_chip.py

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只接受 status == "active"
3. universe.json 的 stocks 必須是 dict
4. 不探測 CMoney
5. 不寫死 Universe 數量
6. chip.json 必須與 Universe 1:1
7. 保留最近 20 個交易日三大法人資料
8. 資券當沖率：

       資券相抵量 ÷ 成交量 × 100

9. 禁止：
   - TWTB4U
   - tpex_intraday_trading_statistics
   - 現股當沖資格
   - 現股當沖成交股數

   作為資券當沖率分子。

10. TWSE / TPEx 優先使用官方來源
11. 官方來源失敗才允許 validated fallback
12. Structure Gate
13. Data Quality Gate
14. Gate 全部 PASS 才 Atomic Write
15. Atomic Write 後再次讀取 chip.json 驗證
"""

from __future__ import annotations

import json
import math
import re
import sys
import time

from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "MARGIN-OFFSET-CONTRACT-V1"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 40
RETRIES = 4
RETRY_SLEEP = 1.0
REQUEST_SLEEP = 0.6

HISTORY_DAYS = 20
MAX_LOOKBACK_DAYS = 70

SHARES_PER_TRADING_UNIT = 1000


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "text/html,"
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
}


session = requests.Session()


# ============================================================
# TWSE
# ============================================================

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
)

TWSE_MARGIN_URL = (
    "https://www.twse.com.tw/"
    "exchangeReport/MI_MARGN"
)

TWSE_VOLUME_URL = (
    "https://openapi.twse.com.tw/v1/"
    "exchangeReport/STOCK_DAY_ALL"
)


# ============================================================
# TPEx
# ============================================================

TPEX_OPENAPI = (
    "https://www.tpex.org.tw/openapi/v1"
)

TPEX_MARGIN_URL = (
    TPEX_OPENAPI
    + "/tpex_mainboard_margin_balance"
)

TPEX_VOLUME_URL = (
    TPEX_OPENAPI
    + "/tpex_mainboard_daily_close_quotes"
)

TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)

TPEX_MARGIN_LEGACY_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/margin_trading/"
    "margin_balance/"
    "margin_bal_result.php"
)


# ============================================================
# OPTIONAL FALLBACK
# ============================================================

MONEYLINK_URL = (
    "https://www.money-link.com.tw/"
    "TWStock/StockChips.aspx"
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
# TIME
# ============================================================

def now_tw() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    )


def iso_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


# ============================================================
# BASIC NORMALIZATION
# ============================================================

def clean_code(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().upper()

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    return text


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[\s_\-\/\(\)（）]+",
        "",
        text,
    )


def safe_number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    text = str(value).strip()

    if not text:
        return None

    if text.lower() in {
        "-",
        "--",
        "---",
        "－",
        "none",
        "null",
        "n/a",
        "na",
    }:
        return None

    text = (
        text
        .replace(",", "")
        .replace("，", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("%", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    try:
        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except (TypeError, ValueError):
        return None


# ============================================================
# FIELD HELPERS
# ============================================================

def find_field(
    row: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized: Dict[str, Any] = {}

    for key, value in row.items():
        normalized[normalize_key(key)] = value

    for alias in aliases:
        normalized_alias = normalize_key(alias)

        if normalized_alias in normalized:
            return normalized[normalized_alias]

    return None


def find_code(
    row: Dict[str, Any],
) -> str:

    return clean_code(
        find_field(
            row,
            [
                "代號",
                "證券代號",
                "股票代號",
                "Code",
                "SecurityCode",
                "SecuritiesCompanyCode",
                "StockCode",
                "ticker",
                "symbol",
            ],
        )
    )


def normalize_date_value(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = clean_text(value)

    if not text:
        return None

    match = re.search(
        r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})",
        text,
    )

    if match:
        return (
            f"{int(match.group(1)):04d}-"
            f"{int(match.group(2)):02d}-"
            f"{int(match.group(3)):02d}"
        )

    match = re.search(
        r"(20\d{2})(\d{2})(\d{2})",
        text,
    )

    if match:
        return (
            f"{match.group(1)}-"
            f"{match.group(2)}-"
            f"{match.group(3)}"
        )

    return None


def find_date(
    row: Dict[str, Any],
) -> Optional[str]:

    value = find_field(
        row,
        [
            "日期",
            "資料日期",
            "交易日期",
            "Date",
            "date",
            "TradeDate",
            "trade_date",
            "as_of_date",
        ],
    )

    return normalize_date_value(value)


# ============================================================
# PAYLOAD NORMALIZATION
# ============================================================

def rows_from_fields_data(
    fields: Any,
    data: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(fields, list):
        return []

    if not isinstance(data, list):
        return []

    result: List[Dict[str, Any]] = []

    for row in data:

        if isinstance(row, dict):
            result.append(row)
            continue

        if not isinstance(row, list):
            continue

        record: Dict[str, Any] = {}

        for index, field_name in enumerate(fields):

            if index >= len(row):
                break

            record[str(field_name)] = row[index]

        if record:
            result.append(record)

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(payload, list):

        return [
            row
            for row in payload
            if isinstance(row, dict)
        ]

    if not isinstance(payload, dict):
        return []

    rows = rows_from_fields_data(
        payload.get("fields"),
        payload.get("data"),
    )

    if rows:
        return rows

    tables = payload.get("tables")

    if isinstance(tables, list):

        result: List[Dict[str, Any]] = []

        for table in tables:

            if not isinstance(table, dict):
                continue

            table_rows = rows_from_fields_data(
                table.get("fields"),
                table.get("data"),
            )

            result.extend(table_rows)

        if result:
            return result

    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
    ):

        value = payload.get(key)

        if not isinstance(value, list):
            continue

        rows = [
            row
            for row in value
            if isinstance(row, dict)
        ]

        if rows:
            return rows

    return []


# ============================================================
# HTTP
# ============================================================

def request_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    retries: int = RETRIES,
) -> Optional[Any]:

    last_error = "unknown error"

    for attempt in range(1, retries + 1):

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:

                last_error = (
                    f"HTTP {response.status_code}"
                )

            else:

                text = response.text.strip()

                if not text:

                    last_error = "EMPTY RESPONSE"

                else:

                    try:
                        return response.json()

                    except Exception as exc:
                        last_error = (
                            f"JSON ERROR: {exc}"
                        )

        except Exception as exc:

            last_error = (
                f"HTTP ERROR: {exc}"
            )

        if attempt < retries:
            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"      ❌ {last_error}"
    )

    return None


def request_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    retries: int = RETRIES,
) -> Optional[str]:

    last_error = "unknown error"

    for attempt in range(1, retries + 1):

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:

                text = response.text.strip()

                if text:
                    return text

                last_error = "EMPTY RESPONSE"

            else:

                last_error = (
                    f"HTTP {response.status_code}"
                )

        except Exception as exc:

            last_error = (
                f"HTTP ERROR: {exc}"
            )

        if attempt < retries:
            time.sleep(
                RETRY_SLEEP * attempt
            )

    log(
        f"      ❌ {last_error}"
    )

    return None


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> List[Dict[str, str]]:

    section(
        "1. Universe 載入與契約驗證"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    try:

        payload = json.loads(
            UNIVERSE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        raise RuntimeError(
            f"universe.json JSON 解析失敗：{exc}"
        ) from exc

    if not isinstance(payload, dict):

        raise RuntimeError(
            "universe.json 根節點必須是 dict"
        )

    raw_stocks = payload.get("stocks")

    if not isinstance(raw_stocks, dict):

        raise RuntimeError(
            "universe.json 的 stocks 必須是 dict"
        )

    securities: List[Dict[str, str]] = []

    seen: set[str] = set()

    for key, item in raw_stocks.items():

        if not isinstance(item, dict):
            continue

        status = clean_text(
            item.get("status")
        ).lower()

        if status != "active":
            continue

        symbol = clean_code(
            item.get(
                "symbol",
                key,
            )
        )

        if not symbol:
            continue

        if symbol in seen:
            raise RuntimeError(
                f"Universe 出現重複代號：{symbol}"
            )

        market = clean_text(
            item.get("market")
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:
            raise RuntimeError(
                f"Universe {symbol} market 無效：{market}"
            )

        name = clean_text(
            item.get("name")
        )

        if not name:
            raise RuntimeError(
                f"Universe {symbol} 缺少 name"
            )

        full_symbol = clean_text(
            item.get("full_symbol")
        )

        if not full_symbol:

            suffix = (
                ".TW"
                if market == "TWSE"
                else ".TWO"
            )

            full_symbol = (
                symbol + suffix
            )

        instrument_type = clean_text(
            item.get(
                "type",
                "STOCK",
            )
        ).upper()

        if not instrument_type:
            instrument_type = "STOCK"

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": name,
                "market": market,
                "type": instrument_type,
            }
        )

        seen.add(symbol)

    if not securities:

        raise RuntimeError(
            "Universe 沒有任何 status == active 的標的"
        )

    twse_count = sum(
        1
        for item in securities
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in securities
        if item["market"] == "TPEX"
    )

    log(
        f"✓ Active Universe：{len(securities)} 檔"
    )
    log(
        f"  TWSE：{twse_count} 檔"
    )
    log(
        f"  TPEx：{tpex_count} 檔"
    )

    return securities


# ============================================================
# INSTITUTIONAL - TWSE
# ============================================================

def fetch_twse_institutional(
    data_date: str,
) -> Dict[str, float]:

    payload = request_json(
        TWSE_T86_URL,
        {
            "response": "json",
            "date": data_date,
        },
    )

    if not isinstance(payload, dict):
        return {}

    rows = normalize_records(payload)

    result: Dict[str, float] = {}

    for row in rows:

        symbol = find_code(row)

        if not symbol:
            continue

        foreign = safe_number(
            find_field(
                row,
                [
                    "外陸資買賣超股數",
                    "外資及陸資買賣超股數",
                    "ForeignInvestorNetBuySell",
                    "ForeignInvestorNet",
                ],
            )
        )

        trust = safe_number(
            find_field(
                row,
                [
                    "投信買賣超股數",
                    "InvestmentTrustNetBuySell",
                    "InvestmentTrustNet",
                ],
            )
        )

        dealer = safe_number(
            find_field(
                row,
                [
                    "自營商買賣超股數",
                    "DealerNetBuySell",
                    "DealerNet",
                ],
            )
        )

        values = [
            value
            for value in (
                foreign,
                trust,
                dealer,
            )
            if value is not None
        ]

        if values:

            result[symbol] = round(
                sum(values),
                2,
            )

    return result


# ============================================================
# INSTITUTIONAL - TPEX
# ============================================================

def fetch_tpex_institutional(
    data_date: str,
) -> Dict[str, float]:

    compact_date = data_date.replace(
        "-",
        "",
    )

    params_list = [
        {
            "l": "zh-tw",
            "d": compact_date,
        },
        {
            "l": "zh-tw",
            "date": compact_date,
        },
        {
            "date": compact_date,
        },
    ]

    for params in params_list:

        payload = request_json(
            TPEX_INSTITUTIONAL_URL,
            params,
        )

        if not payload:
            continue

        rows = normalize_records(payload)

        result: Dict[str, float] = {}

        for row in rows:

            symbol = find_code(row)

            if not symbol:
                continue

            foreign = safe_number(
                find_field(
                    row,
                    [
                        "外資及陸資買賣超股數",
                        "外資買賣超",
                        "ForeignInvestorNetBuySell",
                        "ForeignNetBuySell",
                    ],
                )
            )

            trust = safe_number(
                find_field(
                    row,
                    [
                        "投信買賣超股數",
                        "投信買賣超",
                        "InvestmentTrustNetBuySell",
                        "InvestmentTrustNet",
                    ],
                )
            )

            dealer = safe_number(
                find_field(
                    row,
                    [
                        "自營商買賣超股數",
                        "自營商買賣超",
                        "DealerNetBuySell",
                        "DealerNet",
                    ],
                )
            )

            values = [
                value
                for value in (
                    foreign,
                    trust,
                    dealer,
                )
                if value is not None
            ]

            if values:

                result[symbol] = round(
                    sum(values),
                    2,
                )

        if result:
            return result

    return {}


# ============================================================
# DAILY INSTITUTIONAL
# ============================================================

def fetch_daily_institutional(
    dt: datetime,
) -> Dict[str, float]:

    data_date = iso_date(dt)

    twse = fetch_twse_institutional(
        data_date
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_institutional(
        data_date
    )

    merged = dict(twse)
    merged.update(tpex)

    return merged


# ============================================================
# INSTITUTIONAL HISTORY
# ============================================================

def fetch_institutional_history(
    days: int,
) -> Tuple[
    Optional[str],
    Dict[str, List[float]],
]:

    section(
        f"2. 最近 {days} 個交易日三大法人"
    )

    history: Dict[str, List[float]] = {}

    successful_days = 0
    attempts = 0
    latest_date: Optional[str] = None

    current = now_tw().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    while (
        successful_days < days
        and attempts < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            data_date = iso_date(current)

            log(
                f"[{successful_days + 1}/{days}] "
                f"{data_date}"
            )

            data = fetch_daily_institutional(
                current
            )

            if data:

                successful_days += 1

                if latest_date is None:
                    latest_date = data_date

                for symbol, value in data.items():

                    history.setdefault(
                        symbol,
                        [],
                    ).append(value)

                log(
                    f"      ✓ 三大法人："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ 本日無法人資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(days=1)
        attempts += 1

    log(
        f"✓ 有效交易日：{successful_days}"
    )

    log(
        f"✓ 最新法人資料日：{latest_date}"
    )

    if successful_days < days:

        log(
            "❌ 法人 20D 資料不足"
        )

        return None, {}

    return latest_date, history


# ============================================================
# PERIOD SUM
# ============================================================

def period_sum(
    values: List[float],
    days: int,
) -> Optional[float]:

    if len(values) < days:
        return None

    return round(
        sum(values[:days]),
        2,
    )


# ============================================================
# TWSE DAILY VOLUME
# ============================================================

def fetch_twse_total_volume() -> Dict[str, float]:

    log(
        "TWSE 官方成交量："
    )

    payload = request_json(
        TWSE_VOLUME_URL
    )

    rows = normalize_records(payload)

    result: Dict[str, float] = {}

    for row in rows:

        symbol = find_code(row)

        if not symbol:
            continue

        volume = safe_number(
            find_field(
                row,
                [
                    "成交股數",
                    "成交量",
                    "TradeVolume",
                    "TradingVolume",
                    "TradingShares",
                ],
            )
        )

        if volume is not None and volume > 0:
            result[symbol] = volume

    log(
        f"  ✓ {len(result)} 檔"
    )

    return result


# ============================================================
# TPEX DAILY VOLUME
# ============================================================

def fetch_tpex_total_volume() -> Dict[str, float]:

    log(
        "TPEx 官方成交量："
    )

    payload = request_json(
        TPEX_VOLUME_URL
    )

    rows = normalize_records(payload)

    result: Dict[str, float] = {}

    for row in rows:

        symbol = find_code(row)

        if not symbol:
            continue

        volume = safe_number(
            find_field(
                row,
                [
                    "成交股數",
                    "成交量",
                    "TradingShares",
                    "TradeShares",
                    "TradingVolume",
                ],
            )
        )

        if volume is not None and volume > 0:
            result[symbol] = volume

    log(
        f"  ✓ {len(result)} 檔"
    )

    return result


# ============================================================
# TWSE MARGIN OFFSET
# ============================================================

def fetch_twse_margin_offset(
    data_date: str,
) -> Dict[str, Dict[str, Any]]:

    """
    TWSE 官方 MI_MARGN。

    分子：
        資券互抵 / 資券相抵

    單位：
        張

    轉換：
        張 × 1000 = 股
    """

    log(
        "TWSE 官方資券相抵："
    )

    payload = request_json(
        TWSE_MARGIN_URL,
        {
            "response": "json",
            "date": data_date.replace("-", ""),
            "selectType": "ALL",
        },
    )

    if not isinstance(payload, dict):

        log(
            "  ❌ MI_MARGN 無有效 JSON"
        )

        return {}

    tables = payload.get("tables")

    if not isinstance(tables, list):

        log(
            "  ❌ MI_MARGN 缺少 tables"
        )

        return {}

    result: Dict[str, Dict[str, Any]] = {}

    for table in tables:

        if not isinstance(table, dict):
            continue

        fields = table.get("fields")
        data = table.get("data")

        if not isinstance(fields, list):
            continue

        if not isinstance(data, list):
            continue

        field_names = [
            clean_text(field)
            for field in fields
        ]

        code_index: Optional[int] = None
        offset_index: Optional[int] = None

        for index, field_name in enumerate(
            field_names
        ):

            normalized = normalize_key(
                field_name
            )

            if normalized in {
                normalize_key("代號"),
                normalize_key("證券代號"),
            }:
                code_index = index

            if (
                "資券互抵" in field_name
                or "資券相抵" in field_name
            ):
                offset_index = index

        if (
            code_index is None
            or offset_index is None
        ):
            continue

        for row in data:

            if not isinstance(row, list):
                continue

            if code_index >= len(row):
                continue

            if offset_index >= len(row):
                continue

            symbol = clean_code(
                row[code_index]
            )

            if not symbol:
                continue

            if symbol in {
                "合計",
                "TOTAL",
            }:
                continue

            raw_offset = safe_number(
                row[offset_index]
            )

            if raw_offset is None:
                continue

            if raw_offset < 0:
                continue

            offset_shares = (
                raw_offset
                * SHARES_PER_TRADING_UNIT
            )

            result[symbol] = {
                "symbol": symbol,
                "source_date": data_date,
                "source": "official",
                "source_name": "TWSE_MI_MARGN",
                "source_field": field_names[
                    offset_index
                ],
                "source_unit": "trading_unit",
                "margin_offset_volume_raw": raw_offset,
                "margin_offset_volume": offset_shares,
            }

    log(
        f"  ✓ 官方資券相抵："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEX MARGIN PARSER
# ============================================================

def parse_tpex_margin_rows(
    rows: List[Dict[str, Any]],
    source_name: str,
    expected_date: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[str, Dict[str, Any]] = {}

    for row in rows:

        symbol = find_code(row)

        if not symbol:
            continue

        raw_offset_value = find_field(
            row,
            [
                "資券相抵",
                "資券互抵",
                "MarginOffset",
                "MarginOffsetVolume",
                "OffsettingVolume",
                "OffsetVolume",
                "MarginOffsetting",
                "CreditOffset",
            ],
        )

        raw_offset = safe_number(
            raw_offset_value
        )

        if raw_offset is None:
            continue

        if raw_offset < 0:
            continue

        source_date = find_date(row)

        if (
            expected_date is not None
            and source_date is not None
            and source_date != expected_date
        ):
            continue

        offset_shares = (
            raw_offset
            * SHARES_PER_TRADING_UNIT
        )

        result[symbol] = {
            "symbol": symbol,
            "source_date": source_date,
            "source": "official",
            "source_name": source_name,
            "source_field": "資券相抵",
            "source_unit": "thousand_shares",
            "margin_offset_volume_raw": raw_offset,
            "margin_offset_volume": offset_shares,
        }

    return result


# ============================================================
# TPEX MARGIN OFFSET
# ============================================================

def fetch_tpex_margin_offset(
    data_date: str,
) -> Dict[str, Dict[str, Any]]:

    log(
        "TPEx 官方資券相抵："
    )

    payload = request_json(
        TPEX_MARGIN_URL
    )

    rows = normalize_records(payload)

    result = parse_tpex_margin_rows(
        rows,
        "TPEX_MAINBOARD_MARGIN_BALANCE",
        data_date,
    )

    if result:

        log(
            f"  ✓ 官方資券相抵："
            f"{len(result)} 檔"
        )

        return result

    log(
        "  ⚠️ TPEx OpenAPI 無有效資券相抵，"
        "嘗試官方 legacy"
    )

    compact_date = data_date.replace(
        "-",
        "",
    )

    params_list = [
        {
            "l": "zh-tw",
            "o": "json",
            "d": compact_date,
            "s": "0,asc",
        },
        {
            "l": "zh-tw",
            "o": "json",
            "d": compact_date,
        },
        {
            "d": compact_date,
        },
    ]

    for params in params_list:

        text = request_text(
            TPEX_MARGIN_LEGACY_URL,
            params,
        )

        if not text:
            continue

        try:

            payload2 = json.loads(text)

        except Exception:
            continue

        rows2 = normalize_records(
            payload2
        )

        result = parse_tpex_margin_rows(
            rows2,
            "TPEX_MARGIN_LEGACY",
            data_date,
        )

        if result:

            log(
                f"  ✓ TPEx 官方 legacy："
                f"{len(result)} 檔"
            )

            return result

    log(
        "  ❌ TPEx 官方資券相抵來源皆無有效資料"
    )

    return {}


# ============================================================
# FALLBACK HTML PARSER
# ============================================================

class TextTableParser(HTMLParser):

    def __init__(self) -> None:

        super().__init__()

        self.in_cell = False
        self.cell: List[str] = []

        self.row: List[str] = []
        self.rows: List[List[str]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:

        if tag.lower() in {
            "td",
            "th",
        }:

            self.in_cell = True
            self.cell = []

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        tag = tag.lower()

        if (
            tag in {"td", "th"}
            and self.in_cell
        ):

            self.row.append(
                "".join(self.cell).strip()
            )

            self.in_cell = False

        elif tag == "tr":

            if self.row:
                self.rows.append(
                    self.row
                )

            self.row = []

    def handle_data(
        self,
        data: str,
    ) -> None:

        if self.in_cell:
            self.cell.append(data)


# ============================================================
# MONEYLINK FALLBACK
# ============================================================

def fallback_moneylink(
    symbol: str,
    target_date: str,
) -> Optional[Dict[str, Any]]:

    """
    僅作為官方市場來源整體失敗時的 fallback。

    必須同時驗證：
    1. 股票代號
    2. 日期
    3. 資券相抵量
    4. 成交量

    禁止接受只有：
    - 當沖資格
    - 當沖率
    - 現股當沖成交股數
    """

    try:

        response = session.get(
            MONEYLINK_URL,
            params={
                "SymId": symbol,
                "TWMId": "Chips_STK01",
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return None

        html = response.text

        if not html:
            return None

        plain = re.sub(
            r"<[^>]+>",
            " ",
            html,
        )

        plain = re.sub(
            r"\s+",
            " ",
            plain,
        ).strip()

        date_variants = [
            target_date,
            target_date.replace("-", "/"),
            target_date.replace("-", ""),
        ]

        if not any(
            date_value in plain
            for date_value in date_variants
        ):
            return None

        if "資券相抵" not in plain:
            return None

        parser = TextTableParser()
        parser.feed(html)

        offset: Optional[float] = None

        for row in parser.rows:

            if not row:
                continue

            joined = " ".join(row)

            if "資券相抵" not in joined:
                continue

            for value in reversed(row):

                numeric = safe_number(value)

                if numeric is not None:
                    offset = numeric
                    break

            if offset is not None:
                break

        if offset is None:

            match = re.search(
                r"資券相抵"
                r"(?:\s*\(張\))?"
                r"[^0-9]{0,100}"
                r"(\d[\d,]*)",
                plain,
            )

            if match:
                offset = safe_number(
                    match.group(1)
                )

        if offset is None:
            return None

        volume_match = re.search(
            r"(?:總量|成交量)"
            r"[：:\s]*"
            r"([\d,]+)",
            plain,
        )

        if not volume_match:
            return None

        total_volume = safe_number(
            volume_match.group(1)
        )

        if total_volume is None:
            return None

        if total_volume <= 0:
            return None

        if offset < 0:
            return None

        offset_shares = (
            offset
            * SHARES_PER_TRADING_UNIT
        )

        if offset_shares > total_volume:
            return None

        return {
            "symbol": symbol,
            "source_date": target_date,
            "source": "validated_fallback",
            "source_name": "MONEYLINK",
            "source_field": "資券相抵(張)",
            "source_unit": "lot",
            "margin_offset_volume_raw": offset,
            "margin_offset_volume": offset_shares,
            "fallback_total_volume": total_volume,
        }

    except Exception:
        return None


# ============================================================
# FALLBACK CONTROLLER
# ============================================================

def use_fallback_if_needed(
    securities: List[Dict[str, str]],
    data_date: str,
    twse_offset: Dict[str, Dict[str, Any]],
    tpex_offset: Dict[str, Dict[str, Any]],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int],
]:

    stats = {
        "twse_fallback": 0,
        "tpex_fallback": 0,
        "fallback_valid": 0,
    }

    twse_failed = not bool(twse_offset)
    tpex_failed = not bool(tpex_offset)

    merged: Dict[str, Dict[str, Any]] = {}

    merged.update(twse_offset)
    merged.update(tpex_offset)

    if not twse_failed and not tpex_failed:
        return merged, stats

    log("")
    log(
        "⚠️ 至少一個市場官方資券相抵來源"
        "整體失敗，啟用 validated fallback"
    )

    for item in securities:

        market = item["market"]
        symbol = item["symbol"]

        if market == "TWSE" and not twse_failed:
            continue

        if market == "TPEX" and not tpex_failed:
            continue

        if symbol in merged:
            continue

        fallback = fallback_moneylink(
            symbol,
            data_date,
        )

        if fallback is None:
            continue

        merged[symbol] = fallback

        stats["fallback_valid"] += 1

        if market == "TWSE":
            stats["twse_fallback"] += 1
        else:
            stats["tpex_fallback"] += 1

        time.sleep(0.15)

    return merged, stats


# ============================================================
# DAYTRADE / MARGIN OFFSET DATA
# ============================================================

def build_daytrade_data(
    securities: List[Dict[str, str]],
    data_date: str,
) -> Tuple[
    Dict[str, Dict[str, Optional[float]]],
    Dict[str, int],
]:

    section(
        "3. 資券當沖率資料"
    )

    # --------------------------------------------------------
    # 官方資券相抵
    # --------------------------------------------------------

    twse_offset = fetch_twse_margin_offset(
        data_date
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_offset = fetch_tpex_margin_offset(
        data_date
    )

    time.sleep(
        REQUEST_SLEEP
    )

    # --------------------------------------------------------
    # 官方成交量
    # --------------------------------------------------------

    twse_volume = fetch_twse_total_volume()

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_volume = fetch_tpex_total_volume()

    # --------------------------------------------------------
    # 官方來源失敗才 fallback
    # --------------------------------------------------------

    offset_all, fallback_stats = (
        use_fallback_if_needed(
            securities,
            data_date,
            twse_offset,
            tpex_offset,
        )
    )

    result: Dict[
        str,
        Dict[str, Optional[float]],
    ] = {}

    valid_rates = 0
    invalid = 0

    official_valid = 0
    fallback_valid = 0

    twse_valid = 0
    tpex_valid = 0

    for item in securities:

        symbol = item["symbol"]
        market = item["market"]

        source = offset_all.get(
            symbol
        )

        total_volume: Optional[float]

        if market == "TWSE":

            total_volume = twse_volume.get(
                symbol
            )

        else:

            total_volume = tpex_volume.get(
                symbol
            )

        source_type: Optional[str] = None
        source_name: Optional[str] = None
        source_field: Optional[str] = None
        source_unit: Optional[str] = None
        source_date: Optional[str] = None
        offset: Optional[float] = None

        if source is not None:

            source_type = source.get(
                "source"
            )

            source_name = source.get(
                "source_name"
            )

            source_field = source.get(
                "source_field"
            )

            source_unit = source.get(
                "source_unit"
            )

            source_date = (
                clean_text(
                    source.get(
                        "source_date"
                    )
                )
                or None
            )

            offset = safe_number(
                source.get(
                    "margin_offset_volume"
                )
            )

        # fallback 必須同時提供成交量
        if (
            total_volume is None
            and source_type
            == "validated_fallback"
            and source is not None
        ):

            total_volume = safe_number(
                source.get(
                    "fallback_total_volume"
                )
            )

        # ----------------------------------------------------
        # 日期驗證
        # ----------------------------------------------------

        if source_type == "validated_fallback":

            date_ok = (
                source_date == data_date
            )

        else:

            date_ok = (
                source_date is None
                or source_date == data_date
            )

        # ----------------------------------------------------
        # 公式
        # ----------------------------------------------------

        rate: Optional[float] = None

        if (
            offset is not None
            and total_volume is not None
            and offset >= 0
            and total_volume > 0
            and offset <= total_volume
            and date_ok
        ):

            rate = round(
                offset
                / total_volume
                * 100.0,
                4,
            )

            valid_rates += 1

            if source_type == "official":
                official_valid += 1

            elif source_type == "validated_fallback":
                fallback_valid += 1

            if market == "TWSE":
                twse_valid += 1
            else:
                tpex_valid += 1

        else:

            invalid += 1

        result[symbol] = {
            "margin_offset_volume": offset,
            "day_trading_volume": offset,
            "total_volume": total_volume,
            "day_trading_rate": rate,
            "day_trading_source": source_type,
            "day_trading_source_name": source_name,
            "day_trading_source_field": source_field,
            "day_trading_source_unit": source_unit,
            "day_trading_source_date": source_date,
        }

    statistics: Dict[str, int] = {
        "twse_official_offset_source": len(
            twse_offset
        ),
        "tpex_official_offset_source": len(
            tpex_offset
        ),
        "twse_volume_source": len(
            twse_volume
        ),
        "tpex_volume_source": len(
            tpex_volume
        ),
        "official_valid": official_valid,
        "fallback_valid": fallback_valid,
        "valid_rates": valid_rates,
        "invalid": invalid,
        "twse_valid": twse_valid,
        "tpex_valid": tpex_valid,
        **fallback_stats,
    }

    log("")
    log(
        f"✓ 有效資券當沖率：{valid_rates}"
    )
    log(
        f"  官方：{official_valid}"
    )
    log(
        f"  備援：{fallback_valid}"
    )
    log(
        f"  無效/缺資料：{invalid}"
    )
    log(
        f"  TWSE：{twse_valid}"
    )
    log(
        f"  TPEx：{tpex_valid}"
    )

    return result, statistics


# ============================================================
# BUILD CHIP
# ============================================================

def build_chip(
    securities: List[Dict[str, str]],
    history: Dict[str, List[float]],
    daytrade: Dict[
        str,
        Dict[str, Optional[float]],
    ],
    data_date: str,
) -> Dict[str, Dict[str, Any]]:

    section(
        "4. 建立 Chip"
    )

    stocks: Dict[str, Dict[str, Any]] = {}

    for item in securities:

        symbol = item["symbol"]

        values = history.get(
            symbol,
            [],
        )

        dt = daytrade.get(
            symbol,
            {},
        )

        stocks[symbol] = {
            "symbol": symbol,
            "full_symbol": item["full_symbol"],
            "name": item["name"],
            "market": item["market"],
            "type": item["type"],

            "institutional_1d": (
                values[0]
                if len(values) >= 1
                else None
            ),

            "institutional_5d": period_sum(
                values,
                5,
            ),

            "institutional_10d": period_sum(
                values,
                10,
            ),

            "institutional_20d": period_sum(
                values,
                20,
            ),

            "margin_offset_volume": dt.get(
                "margin_offset_volume"
            ),

            # Dashboard 舊欄位相容。
            # 語義已固定為「資券相抵量」。
            "day_trading_volume": dt.get(
                "margin_offset_volume"
            ),

            "total_volume": dt.get(
                "total_volume"
            ),

            "day_trading_rate": dt.get(
                "day_trading_rate"
            ),

            "day_trading_source": dt.get(
                "day_trading_source"
            ),

            "day_trading_source_name": dt.get(
                "day_trading_source_name"
            ),

            "day_trading_source_field": dt.get(
                "day_trading_source_field"
            ),

            "day_trading_source_unit": dt.get(
                "day_trading_source_unit"
            ),

            "day_trading_source_date": dt.get(
                "day_trading_source_date"
            ),

            "updated_at": data_date,
        }

    return stocks


# ============================================================
# STRUCTURE GATE
# ============================================================

FORBIDDEN_FIELDS = {
    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",
}


REQUIRED_FIELDS = {
    "symbol",
    "full_symbol",
    "name",
    "market",
    "type",

    "institutional_1d",
    "institutional_5d",
    "institutional_10d",
    "institutional_20d",

    "margin_offset_volume",
    "day_trading_volume",
    "total_volume",
    "day_trading_rate",

    "day_trading_source",
    "day_trading_source_name",
    "day_trading_source_field",
    "day_trading_source_unit",
    "day_trading_source_date",

    "updated_at",
}


def validate_structure(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "5. Structure Gate"
    )

    errors = 0

    if not isinstance(stocks, dict):

        log(
            "❌ stocks 不是 dict"
        )

        return False

    for symbol, item in stocks.items():

        if not isinstance(item, dict):

            log(
                f"❌ {symbol}: item 不是 dict"
            )

            errors += 1
            continue

        missing = (
            REQUIRED_FIELDS
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {symbol} 缺欄位："
                f"{sorted(missing)}"
            )

            errors += len(missing)

        if clean_code(
            item.get("symbol")
        ) != symbol:

            log(
                f"❌ {symbol}: symbol 不一致"
            )

            errors += 1

        if item.get("market") not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol}: market 無效"
            )

            errors += 1

        # ----------------------------------------------------
        # forbidden fields
        # ----------------------------------------------------

        for forbidden in FORBIDDEN_FIELDS:

            if forbidden in item:

                log(
                    f"❌ {symbol}: "
                    f"禁止欄位 {forbidden}"
                )

                errors += 1

        # ----------------------------------------------------
        # offset
        # ----------------------------------------------------

        offset = item.get(
            "margin_offset_volume"
        )

        if offset is not None:

            if not isinstance(
                offset,
                (int, float),
            ):

                log(
                    f"❌ {symbol}: "
                    "margin_offset_volume 非數值"
                )

                errors += 1

            elif offset < 0:

                log(
                    f"❌ {symbol}: "
                    "margin_offset_volume < 0"
                )

                errors += 1

        # ----------------------------------------------------
        # total volume
        # ----------------------------------------------------

        total = item.get(
            "total_volume"
        )

        if total is not None:

            if not isinstance(
                total,
                (int, float),
            ):

                log(
                    f"❌ {symbol}: "
                    "total_volume 非數值"
                )

                errors += 1

            elif total <= 0:

                log(
                    f"❌ {symbol}: "
                    "total_volume <= 0"
                )

                errors += 1

        # ----------------------------------------------------
        # offset <= volume
        # ----------------------------------------------------

        if (
            isinstance(offset, (int, float))
            and isinstance(total, (int, float))
            and offset > total
        ):

            log(
                f"❌ {symbol}: "
                "資券相抵量 > 成交量"
            )

            errors += 1

        # ----------------------------------------------------
        # legacy alias
        # ----------------------------------------------------

        alias_volume = item.get(
            "day_trading_volume"
        )

        if (
            offset is not None
            and alias_volume != offset
        ):

            log(
                f"❌ {symbol}: "
                "day_trading_volume "
                "必須等於 margin_offset_volume"
            )

            errors += 1

        # ----------------------------------------------------
        # rate
        # ----------------------------------------------------

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            if not isinstance(
                rate,
                (int, float),
            ):

                log(
                    f"❌ {symbol}: "
                    "day_trading_rate 非數值"
                )

                errors += 1

            elif (
                rate < 0
                or rate > 100
            ):

                log(
                    f"❌ {symbol}: "
                    f"day_trading_rate={rate}"
                )

                errors += 1

        # ----------------------------------------------------
        # source
        # ----------------------------------------------------

        source = item.get(
            "day_trading_source"
        )

        if source not in {
            None,
            "official",
            "validated_fallback",
        }:

            log(
                f"❌ {symbol}: "
                f"非法 source={source}"
            )

            errors += 1

        if source == "official":

            if not item.get(
                "day_trading_source_name"
            ):

                log(
                    f"❌ {symbol}: "
                    "official 缺 source_name"
                )

                errors += 1

            if not item.get(
                "day_trading_source_field"
            ):

                log(
                    f"❌ {symbol}: "
                    "official 缺 source_field"
                )

                errors += 1

        if source == "validated_fallback":

            source_date = item.get(
                "day_trading_source_date"
            )

            if not source_date:

                log(
                    f"❌ {symbol}: "
                    "fallback 缺 source_date"
                )

                errors += 1

    if errors:

        log(
            f"❌ Structure Gate FAIL：{errors}"
        )

        return False

    log(
        f"✓ Structure Gate PASS："
        f"{len(stocks)} 檔"
    )

    return True


# ============================================================
# DATA QUALITY GATE
# ============================================================

def data_quality_gate(
    securities: List[Dict[str, str]],
    stocks: Dict[str, Dict[str, Any]],
    statistics: Dict[str, int],
    data_date: str,
) -> bool:

    section(
        "6. Data Quality Gate"
    )

    errors = 0

    universe_count = len(securities)
    chip_count = len(stocks)

    # --------------------------------------------------------
    # 1:1 count
    # --------------------------------------------------------

    if universe_count != chip_count:

        log(
            f"❌ Universe / Chip："
            f"{universe_count} / {chip_count}"
        )

        errors += 1

    # --------------------------------------------------------
    # exact symbol set
    # --------------------------------------------------------

    universe_symbols = {
        item["symbol"]
        for item in securities
    }

    chip_symbols = set(stocks.keys())

    missing_symbols = (
        universe_symbols
        - chip_symbols
    )

    extra_symbols = (
        chip_symbols
        - universe_symbols
    )

    if missing_symbols:

        log(
            "❌ Chip 缺少 Universe："
            f"{sorted(missing_symbols)}"
        )

        errors += len(missing_symbols)

    if extra_symbols:

        log(
            "❌ Chip 多出 Universe："
            f"{sorted(extra_symbols)}"
        )

        errors += len(extra_symbols)

    # --------------------------------------------------------
    # formula validation
    # --------------------------------------------------------

    valid = 0
    formula_fail = 0
    source_fail = 0

    for symbol, item in stocks.items():

        offset = item.get(
            "margin_offset_volume"
        )

        total = item.get(
            "total_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        source = item.get(
            "day_trading_source"
        )

        if rate is None:
            continue

        valid += 1

        if (
            offset is None
            or total is None
            or total <= 0
        ):

            log(
                f"❌ {symbol}: "
                "rate 有值但原始資料不完整"
            )

            formula_fail += 1
            continue

        if offset < 0:

            log(
                f"❌ {symbol}: "
                "offset < 0"
            )

            formula_fail += 1
            continue

        if offset > total:

            log(
                f"❌ {symbol}: "
                "offset > total_volume"
            )

            formula_fail += 1
            continue

        expected = round(
            offset
            / total
            * 100.0,
            4,
        )

        try:
            stored_rate = float(rate)
        except (TypeError, ValueError):

            log(
                f"❌ {symbol}: "
                "rate 無法轉成 float"
            )

            formula_fail += 1
            continue

        if abs(
            expected - stored_rate
        ) > 0.0001:

            log(
                f"❌ {symbol}: "
                f"stored={stored_rate}, "
                f"expected={expected}"
            )

            formula_fail += 1

        if source not in {
            "official",
            "validated_fallback",
        }:

            log(
                f"❌ {symbol}: "
                f"非法資料來源 {source}"
            )

            source_fail += 1

    # --------------------------------------------------------
    # statistics
    # --------------------------------------------------------

    log(
        f"Universe / Chip："
        f"{universe_count} / {chip_count}"
    )

    log(
        f"有效資券當沖率：{valid}"
    )

    log(
        f"公式驗證失敗：{formula_fail}"
    )

    log(
        f"來源驗證失敗：{source_fail}"
    )

    log(
        "TWSE 官方資券相抵筆數："
        f"{statistics.get('twse_official_offset_source', 0)}"
    )

    log(
        "TPEx 官方資券相抵筆數："
        f"{statistics.get('tpex_official_offset_source', 0)}"
    )

    log(
        "TWSE 官方成交量筆數："
        f"{statistics.get('twse_volume_source', 0)}"
    )

    log(
        "TPEx 官方成交量筆數："
        f"{statistics.get('tpex_volume_source', 0)}"
    )

    log(
        "官方有效筆數："
        f"{statistics.get('official_valid', 0)}"
    )

    log(
        "fallback 有效筆數："
        f"{statistics.get('fallback_valid', 0)}"
    )

    # --------------------------------------------------------
    # Market source sanity
    # --------------------------------------------------------

    twse_universe = sum(
        1
        for item in securities
        if item["market"] == "TWSE"
    )

    tpex_universe = sum(
        1
        for item in securities
        if item["market"] == "TPEX"
    )

    twse_official = statistics.get(
        "twse_official_offset_source",
        0,
    )

    tpex_official = statistics.get(
        "tpex_official_offset_source",
        0,
    )

    twse_fallback = statistics.get(
        "twse_fallback",
        0,
    )

    tpex_fallback = statistics.get(
        "tpex_fallback",
        0,
    )

    if (
        twse_universe > 0
        and twse_official == 0
        and twse_fallback == 0
    ):

        log(
            "❌ TWSE 沒有官方資券相抵，"
            "也沒有有效 fallback"
        )

        errors += 1

    if (
        tpex_universe > 0
        and tpex_official == 0
        and tpex_fallback == 0
    ):

        log(
            "❌ TPEx 沒有官方資券相抵，"
            "也沒有有效 fallback"
        )

        errors += 1

    # --------------------------------------------------------
    # Need at least one valid rate
    # --------------------------------------------------------

    if valid == 0:

        log(
            "❌ 沒有任何可驗證的資券當沖率"
        )

        errors += 1

    # --------------------------------------------------------
    # Fail counts
    # --------------------------------------------------------

    errors += formula_fail
    errors += source_fail

    # --------------------------------------------------------
    # Date validation
    # --------------------------------------------------------

    for symbol, item in stocks.items():

        updated_at = item.get(
            "updated_at"
        )

        if updated_at != data_date:

            log(
                f"❌ {symbol}: "
                f"updated_at={updated_at}, "
                f"expected={data_date}"
            )

            errors += 1

        source = item.get(
            "day_trading_source"
        )

        source_date = item.get(
            "day_trading_source_date"
        )

        if source == "validated_fallback":

            if source_date != data_date:

                log(
                    f"❌ {symbol}: "
                    "fallback 日期不一致"
                )

                errors += 1

    if errors:

        log("")
        log(
            f"❌ Data Quality Gate FAIL："
            f"{errors}"
        )

        log(
            "❌ 禁止寫入 chip.json"
        )

        return False

    log("")
    log(
        "✓ Universe / Chip 1:1"
    )

    log(
        "✓ 資券相抵量存在且可驗證"
    )

    log(
        "✓ 成交量存在且可驗證"
    )

    log(
        "✓ 資券相抵量 <= 成交量"
    )

    log(
        "✓ 資券當沖率公式一致"
    )

    log(
        "✓ 資料來源可追溯"
    )

    log(
        "✓ Data Quality Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD CONTRACT VALIDATION
# ============================================================

def validate_payload_contract(
    payload: Dict[str, Any],
    securities: List[Dict[str, str]],
) -> bool:

    if not isinstance(payload, dict):
        return False

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        return False

    expected_symbols = {
        item["symbol"]
        for item in securities
    }

    actual_symbols = set(stocks.keys())

    if expected_symbols != actual_symbols:
        return False

    if payload.get("universe_count") != len(
        securities
    ):
        return False

    definition = payload.get(
        "day_trade_definition"
    )

    if not isinstance(
        definition,
        dict,
    ):
        return False

    if definition.get(
        "numerator"
    ) != "margin_offset_volume":
        return False

    if definition.get(
        "denominator"
    ) != "total_volume":
        return False

    if definition.get(
        "formula"
    ) != "資券相抵量 / 成交量 * 100":
        return False

    return True


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write(
    payload: Dict[str, Any],
) -> bool:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = DATA_DIR / (
        "chip.json.tmp"
    )

    try:

        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )

        temp_file.write_text(
            serialized,
            encoding="utf-8",
        )

        # 先確認 temp JSON 可以重新 parse
        json.loads(
            temp_file.read_text(
                encoding="utf-8"
            )
        )

        temp_file.replace(
            CHIP_FILE
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗：{exc}"
        )

        try:
            temp_file.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        return False


# ============================================================
# POST WRITE VERIFY
# ============================================================

def verify_written_chip(
    securities: List[Dict[str, str]],
    data_date: str,
) -> bool:

    section(
        "7. Atomic Write 後再次驗證"
    )

    if not CHIP_FILE.exists():

        log(
            "❌ chip.json 不存在"
        )

        return False

    try:

        payload = json.loads(
            CHIP_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        log(
            f"❌ chip.json JSON 解析失敗：{exc}"
        )

        return False

    if not validate_payload_contract(
        payload,
        securities,
    ):

        log(
            "❌ chip.json Payload Contract FAIL"
        )

        return False

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(stocks, dict):
        return False

    for symbol, item in stocks.items():

        if not isinstance(item, dict):

            log(
                f"❌ {symbol}: item 非 dict"
            )

            return False

        if item.get(
            "symbol"
        ) != symbol:

            log(
                f"❌ {symbol}: symbol mismatch"
            )

            return False

        offset = item.get(
            "margin_offset_volume"
        )

        alias_volume = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        source = item.get(
            "day_trading_source"
        )

        # ----------------------------------------------------
        # alias
        # ----------------------------------------------------

        if (
            offset is not None
            and alias_volume != offset
        ):

            log(
                f"❌ {symbol}: "
                "day_trading_volume mismatch"
            )

            return False

        # ----------------------------------------------------
        # date
        # ----------------------------------------------------

        if item.get(
            "updated_at"
        ) != data_date:

            log(
                f"❌ {symbol}: "
                "updated_at mismatch"
            )

            return False

        # ----------------------------------------------------
        # source
        # ----------------------------------------------------

        if source not in {
            None,
            "official",
            "validated_fallback",
        }:

            log(
                f"❌ {symbol}: "
                f"非法 source={source}"
            )

            return False

        # ----------------------------------------------------
        # rate
        # ----------------------------------------------------

        if rate is None:
            continue

        if (
            offset is None
            or total is None
            or total <= 0
        ):

            log(
                f"❌ {symbol}: "
                "rate 有值但原始資料缺失"
            )

            return False

        if offset < 0:
            return False

        if offset > total:
            return False

        expected = round(
            offset
            / total
            * 100.0,
            4,
        )

        try:
            stored_rate = float(rate)
        except (TypeError, ValueError):
            return False

        if abs(
            expected - stored_rate
        ) > 0.0001:

            log(
                f"❌ {symbol}: "
                f"寫入後公式錯誤 "
                f"{stored_rate} != {expected}"
            )

            return False

        if source == "validated_fallback":

            if item.get(
                "day_trading_source_date"
            ) != data_date:

                log(
                    f"❌ {symbol}: "
                    "fallback 日期錯誤"
                )

                return False

    log(
        "✓ chip.json 重新讀取成功"
    )

    log(
        "✓ Universe / Chip 1:1"
    )

    log(
        "✓ 所有 rate 重新計算一致"
    )

    log(
        "✓ 所有 updated_at 正確"
    )

    log(
        "✓ Post Write Verify PASS"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"fetch_chip.py "
        f"{VERSION}"
    )

    log(
        f"開始時間：{now_tw().isoformat()}"
    )

    try:

        # ====================================================
        # 1. Universe
        # ====================================================

        securities = load_universe()

        # ====================================================
        # 2. Institutional 20D
        # ====================================================

        data_date, history = (
            fetch_institutional_history(
                HISTORY_DAYS
            )
        )

        if (
            not data_date
            or not history
        ):

            log(
                "❌ 三大法人 20D 取得失敗"
            )

            log(
                "❌ 停止 BUILD"
            )

            return 1

        # ====================================================
        # 3. Margin Offset
        # ====================================================

        daytrade, statistics = (
            build_daytrade_data(
                securities,
                data_date,
            )
        )

        # ====================================================
        # 4. Build Chip
        # ====================================================

        stocks = build_chip(
            securities,
            history,
            daytrade,
            data_date,
        )

        # ====================================================
        # 5. Structure Gate
        # ====================================================

        if not validate_structure(
            stocks
        ):

            return 1

        # ====================================================
        # 6. Data Quality Gate
        # ====================================================

        if not data_quality_gate(
            securities,
            stocks,
            statistics,
            data_date,
        ):

            return 1

        # ====================================================
        # 7. Payload
        # ====================================================

        payload: Dict[str, Any] = {
            "version": VERSION,

            "generated_at":
                now_tw().isoformat(),

            "data_date":
                data_date,

            "universe_count":
                len(securities),

            "stocks":
                stocks,

            "statistics":
                statistics,

            "day_trade_definition": {
                "name":
                    "資券當沖率",

                "formula":
                    "資券相抵量 / 成交量 * 100",

                "numerator":
                    "margin_offset_volume",

                "denominator":
                    "total_volume",

                "unit":
                    "shares",

                "description":
                    (
                        "資券相抵量占當日成交量"
                        "之比例"
                    ),

                "forbidden_numerator_sources": [
                    "TWTB4U",
                    "tpex_intraday_trading_statistics",
                    "可現股當沖標的",
                    "現股當沖成交股數",
                ],
            },
        }

        # ====================================================
        # 8. Payload Contract
        # ====================================================

        if not validate_payload_contract(
            payload,
            securities,
        ):

            log(
                "❌ Payload Contract FAIL"
            )

            return 1

        log(
            "✓ Payload Contract PASS"
        )

        # ====================================================
        # 9. Atomic Write
        # ====================================================

        if not atomic_write(
            payload
        ):

            return 1

        # ====================================================
        # 10. Post Write Verify
        # ====================================================

        if not verify_written_chip(
            securities,
            data_date,
        ):

            log(
                "❌ Post Write Verify FAIL"
            )

            return 1

        # ====================================================
        # 11. Result
        # ====================================================

        elapsed = (
            time.time()
            - start_time
        )

        section(
            "BUILD RESULT"
        )

        log(
            "✓ fetch_chip.py PASS"
        )

        log(
            f"✓ data_date：{data_date}"
        )

        log(
            f"✓ Universe：{len(securities)}"
        )

        log(
            "✓ Chip："
            f"{len(stocks)}"
        )

        log(
            "✓ 有效資券當沖率："
            f"{statistics.get('valid_rates', 0)}"
        )

        log(
            "✓ 官方有效："
            f"{statistics.get('official_valid', 0)}"
        )

        log(
            "✓ fallback 有效："
            f"{statistics.get('fallback_valid', 0)}"
        )

        log(
            f"✓ elapsed：{elapsed:.1f}s"
        )

        return 0

    except KeyboardInterrupt:

        log(
            "❌ 使用者中斷"
        )

        return 130

    except Exception as exc:

        log(
            f"❌ BUILD EXCEPTION：{exc}"
        )

        return 1


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    sys.exit(main())
