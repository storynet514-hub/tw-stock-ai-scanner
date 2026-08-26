#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_chip.py

本版重點：
1. Universe 唯一來源：Data/universe.json
2. 三大法人維持既有 TWSE / TPEx 官方資料邏輯
3. 資券當沖率不再使用現股當沖 TWTB4U / tpex_intraday_trading_statistics
4. TWSE：官方 MI_MARGN 的「資券互抵」
5. TPEx：官方 tpex_mainboard_margin_balance / legacy margin balance 的「資券相抵」
6. 成交量：TWSE STOCK_DAY_ALL / TPEx daily close quotes
7. 公式：資券相抵量(股) / 成交量(股) * 100
8. 官方來源失敗才使用 Money-Link 個股頁作備援；
   備援必須同時驗證代號、日期、資券相抵量、成交量
9. Gate 驗證實際數值與公式，不只驗 JSON 結構
10. 禁止使用「可現股當沖標的」或現股當日沖銷成交股數
    當作資券當沖率
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

VERSION = "MARGIN-OFFSET-REWRITE"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# TWSE
# ============================================================

TWSE_WEB = (
    "https://www.twse.com.tw/rwd/zh"
)

TWSE_T86_URL = (
    TWSE_WEB
    + "/fund/T86"
)

# 正確的信用交易資料來源
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

# 正式 OpenAPI
TPEX_MARGIN_URL = (
    TPEX_OPENAPI
    + "/tpex_mainboard_margin_balance"
)

TPEX_VOLUME_URL = (
    TPEX_OPENAPI
    + "/tpex_mainboard_daily_close_quotes"
)

# 既有法人歷史來源
TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)

# TPEx 官方 legacy 備援
TPEX_MARGIN_LEGACY_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/margin_trading/"
    "margin_balance/"
    "margin_bal_result.php"
)


# ============================================================
# EXTERNAL FALLBACK
# ============================================================

# 只在官方市場來源整體失敗時使用
MONEYLINK_URL = (
    "https://www.money-link.com.tw/"
    "TWStock/StockChips.aspx"
)


# ============================================================
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 40
RETRIES = 4
SLEEP = 0.6

HISTORY_DAYS = 20
MAX_LOOKBACK_DAYS = 70

# TWSE / TPEx 信用資料統一換算成股
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
    "Referer": (
        "https://www.twse.com.tw/"
    ),
}


session = requests.Session()


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
# BASIC CLEAN
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
    )

    return text


def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def normalize_key(value: Any) -> str:

    text = str(value).strip().lower()

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

    if text in {
        "-",
        "--",
        "---",
        "－",
        "None",
        "none",
        "NULL",
        "null",
        "N/A",
        "NA",
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

        number_value = float(text)

        if not math.isfinite(
            number_value
        ):
            return None

        return number_value

    except Exception:

        return None


# ============================================================
# FIELD FIND
# ============================================================

def find_field(
    row: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized = {}

    for key, value in row.items():

        normalized[
            normalize_key(key)
        ] = value

    for alias in aliases:

        key = normalize_key(alias)

        if key in normalized:

            return normalized[key]

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

    if value is None:
        return None

    text = clean_text(value)

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


# ============================================================
# NORMALIZE API RECORDS
# ============================================================

def rows_from_fields_data(
    fields: Any,
    data: Any,
) -> List[Dict[str, Any]]:

    if not isinstance(
        fields,
        list,
    ):
        return []

    if not isinstance(
        data,
        list,
    ):
        return []

    result = []

    for row in data:

        if isinstance(
            row,
            dict,
        ):

            result.append(row)

            continue

        if not isinstance(
            row,
            list,
        ):
            continue

        record = {}

        for index, key in enumerate(
            fields
        ):

            if index >= len(row):
                break

            record[
                str(key)
            ] = row[index]

        if record:
            result.append(record)

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:

    if isinstance(
        payload,
        list,
    ):

        return [
            row
            for row in payload
            if isinstance(
                row,
                dict,
            )
        ]

    if not isinstance(
        payload,
        dict,
    ):
        return []

    # fields + data
    rows = rows_from_fields_data(
        payload.get("fields"),
        payload.get("data"),
    )

    if rows:
        return rows

    # tables
    tables = payload.get(
        "tables"
    )

    if isinstance(
        tables,
        list,
    ):

        result = []

        for table in tables:

            if not isinstance(
                table,
                dict,
            ):
                continue

            result.extend(
                rows_from_fields_data(
                    table.get("fields"),
                    table.get("data"),
                )
            )

        if result:
            return result

    # generic data
    for key in (
        "data",
        "Data",
        "result",
        "results",
        "records",
        "Records",
    ):

        value = payload.get(
            key
        )

        if not isinstance(
            value,
            list,
        ):
            continue

        rows = [
            row
            for row in value
            if isinstance(
                row,
                dict,
            )
        ]

        if rows:
            return rows

    return []


# ============================================================
# HTTP
# ============================================================

def request_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    retries: int = RETRIES,
) -> Optional[Any]:

    last_error = ""

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:

                last_error = (
                    f"HTTP "
                    f"{response.status_code}"
                )

            else:

                text = response.text.strip()

                if not text:

                    last_error = (
                        "EMPTY RESPONSE"
                    )

                else:

                    try:

                        return response.json()

                    except Exception as exc:

                        last_error = (
                            f"JSON ERROR: "
                            f"{exc}"
                        )

        except Exception as exc:

            last_error = (
                f"HTTP ERROR: "
                f"{exc}"
            )

        if attempt < retries:

            time.sleep(
                attempt
            )

    log(
        f"      ❌ {last_error}"
    )

    return None


def request_text(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    retries: int = RETRIES,
) -> Optional[str]:

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if (
                response.status_code == 200
                and response.text.strip()
            ):

                return response.text

        except Exception:
            pass

        if attempt < retries:

            time.sleep(
                attempt
            )

    return None


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> List[
    Dict[str, str]
]:

    section(
        "1. Universe 載入與分類驗證"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到 {UNIVERSE_FILE}"
        )

    payload = json.loads(
        UNIVERSE_FILE.read_text(
            encoding="utf-8"
        )
    )

    raw = payload.get(
        "stocks",
        payload,
    )

    if not isinstance(
        raw,
        dict,
    ):

        raise RuntimeError(
            "universe.json 的 stocks 結構無效"
        )

    securities = []
    seen = set()

    for key, item in raw.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        # Universe 架構的 status 必須正確
        if clean_text(
            item.get(
                "status",
                "",
            )
        ) != "active":

            continue

        symbol = clean_code(
            item.get(
                "symbol",
                key,
            )
        )

        market = clean_text(
            item.get(
                "market",
                "",
            )
        ).upper()

        name = clean_text(
            item.get(
                "name",
                "",
            )
        )

        full_symbol = clean_text(
            item.get(
                "full_symbol",
                "",
            )
        )

        instrument_type = clean_text(
            item.get(
                "type",
                "STOCK",
            )
        ).upper()

        if not symbol:
            continue

        if symbol in seen:
            continue

        if market not in {
            "TWSE",
            "TPEX",
        }:
            continue

        if not name:
            continue

        if not full_symbol:

            full_symbol = (
                f"{symbol}.TW"
                if market == "TWSE"
                else f"{symbol}.TWO"
            )

        if instrument_type not in {
            "STOCK",
            "ETF",
            "BOND",
        }:

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
            "Universe 沒有任何 active 標的"
        )

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log(
        "  TWSE："
        f"{sum("
            "x['market'] == 'TWSE'"
            " for x in securities"
        )}"
    )

    log(
        "  TPEx："
        f"{sum("
            "x['market'] == 'TPEX'"
            " for x in securities"
        )}"
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

    if not isinstance(
        payload,
        dict,
    ):
        return {}

    rows = normalize_records(
        payload
    )

    result = {}

    for row in rows:

        symbol = find_code(
            row
        )

        if not symbol:
            continue

        values = []

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

        for value in (
            foreign,
            trust,
            dealer,
        ):

            if value is not None:
                values.append(value)

        if values:

            result[
                symbol
            ] = sum(values)

    return result


# ============================================================
# INSTITUTIONAL - TPEx
# ============================================================

def fetch_tpex_institutional(
    data_date: str,
) -> Dict[str, float]:

    params_list = [
        {
            "l": "zh-tw",
            "d": data_date.replace(
                "-",
                "",
            ),
        },
        {
            "l": "zh-tw",
            "date": data_date.replace(
                "-",
                "",
            ),
        },
        {
            "date": data_date.replace(
                "-",
                "",
            ),
        },
    ]

    for params in params_list:

        payload = request_json(
            TPEX_INSTITUTIONAL_URL,
            params,
        )

        if not payload:
            continue

        rows = normalize_records(
            payload
        )

        result = {}

        for row in rows:

            symbol = find_code(
                row
            )

            if not symbol:
                continue

            values = []

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

            for value in (
                foreign,
                trust,
                dealer,
            ):

                if value is not None:
                    values.append(value)

            if values:

                result[
                    symbol
                ] = sum(values)

        if result:
            return result

    return {}


# ============================================================
# DAILY INSTITUTIONAL
# ============================================================

def fetch_daily_institutional(
    dt: datetime,
) -> Dict[str, float]:

    twse = fetch_twse_institutional(
        yyyymmdd(dt)
    )

    time.sleep(
        SLEEP
    )

    tpex = fetch_tpex_institutional(
        yyyymmdd(dt)
    )

    result = dict(twse)

    result.update(
        tpex
    )

    return result


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

    history = {}

    successful_days = 0
    attempts = 0
    latest_date = None

    current = (
        now_tw()
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    )

    while (
        successful_days < days
        and attempts < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            data_date = iso_date(
                current
            )

            log(
                f"[{successful_days + 1}/"
                f"{days}] "
                f"{data_date}"
            )

            data = (
                fetch_daily_institutional(
                    current
                )
            )

            if data:

                successful_days += 1

                if latest_date is None:

                    latest_date = (
                        data_date
                    )

                for symbol, value in (
                    data.items()
                ):

                    history.setdefault(
                        symbol,
                        [],
                    ).append(
                        value
                    )

                log(
                    f"      ✓ 法人："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ 本日無法人資料"
                )

            time.sleep(
                SLEEP
            )

        current -= timedelta(
            days=1
        )

        attempts += 1

    log(
        f"✓ 有效交易日："
        f"{successful_days}"
    )

    log(
        f"✓ 最新資料日："
        f"{latest_date}"
    )

    if successful_days < days:

        log(
            "❌ 法人資料不足"
        )

        return None, {}

    return (
        latest_date,
        history,
    )


# ============================================================
# PERIOD
# ============================================================

def period_sum(
    values: List[float],
    days: int,
) -> Optional[float]:

    if len(values) < days:
        return None

    return round(
        sum(
            values[:days]
        ),
        2,
    )


# ============================================================
# TOTAL VOLUME - TWSE
# ============================================================

def fetch_twse_total_volume()
    -> Dict[str, float]:

    log(
        "TWSE 官方成交量："
    )

    payload = request_json(
        TWSE_VOLUME_URL
    )

    rows = normalize_records(
        payload
    )

    result = {}

    for row in rows:

        symbol = find_code(
            row
        )

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

        if (
            volume is not None
            and volume > 0
        ):

            result[
                symbol
            ] = volume

    log(
        f"  ✓ "
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TOTAL VOLUME - TPEx
# ============================================================

def fetch_tpex_total_volume()
    -> Dict[str, float]:

    log(
        "TPEx 官方成交量："
    )

    payload = request_json(
        TPEX_VOLUME_URL
    )

    rows = normalize_records(
        payload
    )

    result = {}

    for row in rows:

        symbol = find_code(
            row
        )

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

        if (
            volume is not None
            and volume > 0
        ):

            result[
                symbol
            ] = volume

    log(
        f"  ✓ "
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE OFFICIAL MARGIN OFFSET
# ============================================================

def fetch_twse_margin_offset(
    data_date: str,
) -> Dict[str, Dict[str, Any]]:

    """
    TWSE 官方 MI_MARGN。

    正確分子：
        資券互抵

    單位：
        交易單位 / 張

    最終：
        轉成股數

    禁止：
        TWTB4U
        當日沖銷成交股數
        可現股當沖標的
    """

    log(
        "TWSE 官方資券相抵："
    )

    params = {
        "response": "json",
        "date": data_date.replace(
            "-",
            "",
        ),
        "selectType": "ALL",
    }

    payload = request_json(
        TWSE_MARGIN_URL,
        params,
    )

    if not isinstance(
        payload,
        dict,
    ):

        log(
            "  ❌ MI_MARGN JSON 失敗"
        )

        return {}

    source_date = (
        find_date(
            payload
        )
        or data_date
    )

    tables = payload.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):

        log(
            "  ❌ MI_MARGN 沒有 tables"
        )

        return {}

    result = {}

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
            clean_text(x)
            for x in fields
        ]

        offset_index = None
        code_index = None

        for index, name in enumerate(
            field_names
        ):

            if name in {
                "代號",
                "證券代號",
            }:

                code_index = index

            if (
                "資券互抵"
                in name
                or
                "資券相抵"
                in name
            ):

                offset_index = index

        if (
            code_index is None
            or offset_index is None
        ):

            continue

        for row in data:

            if not isinstance(
                row,
                list,
            ):
                continue

            if (
                code_index
                >= len(row)
            ):
                continue

            if (
                offset_index
                >= len(row)
            ):
                continue

            symbol = clean_code(
                row[
                    code_index
                ]
            )

            if not symbol:
                continue

            if symbol in {
                "合計",
                "TOTAL",
            }:
                continue

            raw_offset = safe_number(
                row[
                    offset_index
                ]
            )

            if (
                raw_offset is None
                or raw_offset < 0
            ):
                continue

            offset_shares = (
                raw_offset
                * SHARES_PER_TRADING_UNIT
            )

            result[
                symbol
            ] = {
                "symbol": symbol,
                "source_date": source_date,
                "source": "official",
                "source_name": (
                    "TWSE_MI_MARGN"
                ),
                "source_field": (
                    field_names[
                        offset_index
                    ]
                ),
                "source_unit": (
                    "trading_unit"
                ),
                "margin_offset_volume_raw":
                    raw_offset,
                "margin_offset_volume":
                    offset_shares,
            }

    log(
        f"  ✓ 官方資券相抵："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx OFFICIAL MARGIN OFFSET
# ============================================================

def parse_tpex_margin_rows(
    rows: List[Dict[str, Any]],
    source_name: str,
) -> Dict[str, Dict[str, Any]]:

    result = {}

    for row in rows:

        symbol = find_code(
            row
        )

        if not symbol:
            continue

        raw_offset = find_field(
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

        offset = safe_number(
            raw_offset
        )

        if (
            offset is None
            or offset < 0
        ):
            continue

        source_date = find_date(
            row
        )

        # TPEx STKDMARGIN 規格：
        # 資券相抵 = 千股
        offset_shares = (
            offset
            * SHARES_PER_TRADING_UNIT
        )

        result[
            symbol
        ] = {
            "symbol": symbol,
            "source_date": source_date,
            "source": "official",
            "source_name": source_name,
            "source_field": "資券相抵",
            "source_unit": (
                "thousand_shares"
            ),
            "margin_offset_volume_raw":
                offset,
            "margin_offset_volume":
                offset_shares,
        }

    return result


def fetch_tpex_margin_offset()
    -> Dict[str, Dict[str, Any]]:

    log(
        "TPEx 官方資券相抵："
    )

    payload = request_json(
        TPEX_MARGIN_URL
    )

    rows = normalize_records(
        payload
    )

    result = parse_tpex_margin_rows(
        rows,
        "TPEX_MAINBOARD_MARGIN_BALANCE",
    )

    if result:

        log(
            f"  ✓ 官方資券相抵："
            f"{len(result)} 檔"
        )

        return result

    # --------------------------------------------------------
    # 官方 legacy fallback
    # --------------------------------------------------------

    today = now_tw().strftime(
        "%Y%m%d"
    )

    text = request_text(
        TPEX_MARGIN_LEGACY_URL,
        {
            "l": "zh-tw",
            "o": "json",
            "d": today,
            "s": "0,asc",
        },
    )

    if text:

        try:

            payload2 = json.loads(
                text
            )

            rows2 = normalize_records(
                payload2
            )

            result = (
                parse_tpex_margin_rows(
                    rows2,
                    "TPEX_MARGIN_LEGACY",
                )
            )

            if result:

                log(
                    f"  ✓ TPEx 官方 legacy "
                    f"資券相抵："
                    f"{len(result)} 檔"
                )

                return result

        except Exception:
            pass

    log(
        "  ❌ TPEx 官方資券相抵來源皆失敗"
    )

    return {}


# ============================================================
# EXTERNAL FALLBACK HTML PARSER
# ============================================================

class TextTableParser(
    HTMLParser
):

    def __init__(self) -> None:

        super().__init__()

        self.in_cell = False
        self.cell = []

        self.row = []

        self.rows = []

    def handle_starttag(
        self,
        tag: str,
        attrs: List[
            Tuple[
                str,
                Optional[str],
            ]
        ],
    ) -> None:

        if tag in {
            "td",
            "th",
        }:

            self.in_cell = True
            self.cell = []

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

        if (
            tag in {
                "td",
                "th",
            }
            and self.in_cell
        ):

            self.row.append(
                "".join(
                    self.cell
                ).strip()
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

            self.cell.append(
                data
            )


# ============================================================
# MONEY-LINK FALLBACK
# ============================================================

def fallback_moneylink(
    symbol: str,
    target_date: str,
) -> Optional[
    Dict[str, Any]
]:

    """
    外部備援。

    必須同時取得：
    1. 股票代號
    2. 日期
    3. 資券相抵量
    4. 成交量

    不接受：
    - 只有當沖資格
    - 只有當沖率
    - 只有融資融券餘額
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

        plain = re.sub(
            r"<[^>]+>",
            " ",
            html,
        )

        plain = re.sub(
            r"\s+",
            " ",
            plain,
        )

        # 日期必須存在
        date_variants = [
            target_date,
            target_date.replace(
                "-",
                "/",
            ),
            target_date.replace(
                "-",
                "",
            ),
        ]

        if not any(
            x in plain
            for x in date_variants
        ):

            return None

        # 必須存在資券相抵欄位
        if (
            "資券相抵"
            not in plain
        ):

            return None

        parser = TextTableParser()

        parser.feed(
            html
        )

        offset = None
        total_volume = None

        # 先從 HTML table 尋找
        for row in parser.rows:

            if len(row) < 2:
                continue

            joined = " ".join(row)

            if (
                "資券相抵"
                in joined
            ):

                for value in reversed(
                    row
                ):

                    numeric = safe_number(
                        value
                    )

                    if numeric is not None:

                        offset = numeric
                        break

        # 頁面行情區成交量
        match = re.search(
            r"總量[：:]\s*([\d,]+)",
            plain,
        )

        if match:

            total_volume = safe_number(
                match.group(1)
            )

        # 第二層資券相抵解析
        if offset is None:

            match = re.search(
                r"資券相抵"
                r"(?:\(張\))?"
                r"[^0-9]{0,100}"
                r"(\d[\d,]*)",
                plain,
            )

            if match:

                offset = safe_number(
                    match.group(1)
                )

        if (
            offset is None
            or total_volume is None
            or total_volume <= 0
        ):

            return None

        # Money-Link 單位為張
        offset_shares = (
            offset
            * SHARES_PER_TRADING_UNIT
        )

        return {
            "symbol": symbol,
            "source_date": target_date,
            "source": (
                "validated_fallback"
            ),
            "source_name": "MONEYLINK",
            "source_field": (
                "資券相抵(張)"
            ),
            "source_unit": "lot",
            "margin_offset_volume_raw":
                offset,
            "margin_offset_volume":
                offset_shares,
            "fallback_total_volume":
                total_volume,
        }

    except Exception:

        return None


# ============================================================
# FALLBACK CONTROLLER
# ============================================================

def use_fallback_if_needed(
    securities: List[
        Dict[str, str]
    ],
    data_date: str,
    twse_offset: Dict[
        str,
        Dict[str, Any]
    ],
    tpex_offset: Dict[
        str,
        Dict[str, Any]
    ],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int],
]:

    """
    外部備援只在「市場官方來源整體失敗」時啟用。

    不因為單一股票沒有信用交易資料，
    就把整個市場切換到第三方。
    """

    stats = {
        "twse_fallback": 0,
        "tpex_fallback": 0,
        "fallback_valid": 0,
    }

    twse_failed = (
        not twse_offset
    )

    tpex_failed = (
        not tpex_offset
    )

    merged = {}

    merged.update(
        twse_offset
    )

    merged.update(
        tpex_offset
    )

    if (
        not twse_failed
        and not tpex_failed
    ):

        return (
            merged,
            stats,
        )

    log("")
    log(
        "⚠️ 官方來源整體失敗，"
        "啟用外部備援"
    )

    for item in securities:

        market = item[
            "market"
        ]

        if (
            market == "TWSE"
            and not twse_failed
        ):
            continue

        if (
            market == "TPEX"
            and not tpex_failed
        ):
            continue

        symbol = item[
            "symbol"
        ]

        if symbol in merged:
            continue

        fallback = (
            fallback_moneylink(
                symbol,
                data_date,
            )
        )

        if fallback is None:
            continue

        merged[
            symbol
        ] = fallback

        stats[
            "fallback_valid"
        ] += 1

        if market == "TWSE":

            stats[
                "twse_fallback"
            ] += 1

        else:

            stats[
                "tpex_fallback"
            ] += 1

        time.sleep(
            0.15
        )

    return (
        merged,
        stats,
    )


# ============================================================
# DAYTRADE DATA
# ============================================================

def build_daytrade_data(
    securities: List[
        Dict[str, str]
    ],
    data_date: str,
) -> Tuple[
    Dict[
        str,
        Dict[str, Optional[float]]
    ],
    Dict[str, int],
]:

    section(
        "3. 資券當沖率資料"
    )

    # --------------------------------------------------------
    # 官方資券相抵
    # --------------------------------------------------------

    twse_offset = (
        fetch_twse_margin_offset(
            data_date
        )
    )

    time.sleep(
        SLEEP
    )

    tpex_offset = (
        fetch_tpex_margin_offset()
    )

    time.sleep(
        SLEEP
    )

    # --------------------------------------------------------
    # 官方成交量
    # --------------------------------------------------------

    twse_volume = (
        fetch_twse_total_volume()
    )

    time.sleep(
        SLEEP
    )

    tpex_volume = (
        fetch_tpex_total_volume()
    )

    # --------------------------------------------------------
    # fallback
    # --------------------------------------------------------

    offset_all, fallback_stats = (
        use_fallback_if_needed(
            securities,
            data_date,
            twse_offset,
            tpex_offset,
        )
    )

    result = {}

    valid_rates = 0
    invalid = 0

    official_valid = 0
    fallback_valid = 0

    twse_valid = 0
    tpex_valid = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

        market = item[
            "market"
        ]

        source = offset_all.get(
            symbol,
            {},
        )

        if market == "TWSE":

            total_volume = (
                twse_volume.get(
                    symbol
                )
            )

        else:

            total_volume = (
                tpex_volume.get(
                    symbol
                )
            )

        # 外部備援若含成交量，才允許作為成交量備援
        if (
            total_volume is None
            and source
        ):

            total_volume = (
                safe_number(
                    source.get(
                        "fallback_total_volume"
                    )
                )
            )

        offset = None

        source_date = None
        source_type = None
        source_name = None
        source_field = None
        source_unit = None

        if source:

            offset = safe_number(
                source.get(
                    "margin_offset_volume"
                )
            )

            source_date = (
                clean_text(
                    source.get(
                        "source_date",
                        "",
                    )
                )
                or None
            )

            source_type = (
                source.get(
                    "source"
                )
            )

            source_name = (
                source.get(
                    "source_name"
                )
            )

            source_field = (
                source.get(
                    "source_field"
                )
            )

            source_unit = (
                source.get(
                    "source_unit"
                )
            )

        # 日期驗證
        #
        # 官方 TPEx snapshot 有些版本沒有逐列日期，
        # 因此不偽造 API 原始日期。
        #
        # 外部 fallback 則必須有日期。
        if (
            source_type
            == "validated_fallback"
        ):

            date_ok = (
                source_date
                == data_date
            )

        else:

            date_ok = (
                source_date is None
                or
                source_date
                == data_date
            )

        rate = None

        if (
            offset is not None
            and total_volume is not None
            and offset >= 0
            and total_volume > 0
            and offset <= total_volume
            and date_ok
        ):

            rate = round(
                (
                    offset
                    / total_volume
                )
                * 100.0,
                4,
            )

            valid_rates += 1

            if source_type == "official":

                official_valid += 1

            elif (
                source_type
                == "validated_fallback"
            ):

                fallback_valid += 1

            if market == "TWSE":

                twse_valid += 1

            else:

                tpex_valid += 1

        else:

            invalid += 1

        result[
            symbol
        ] = {

            # 新的正確欄位
            "margin_offset_volume":
                offset,

            # 舊 Dashboard 相容欄位
            #
            # 現在不再代表現股當沖成交股數。
            # 明確代表：
            # 「資券相抵量（股）」
            "day_trading_volume":
                offset,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,

            "day_trading_source":
                source_type,

            "day_trading_source_name":
                source_name,

            "day_trading_source_field":
                source_field,

            "day_trading_source_unit":
                source_unit,

            "day_trading_source_date":
                source_date,
        }

    statistics = {

        "twse_official_offset_source":
            len(twse_offset),

        "tpex_official_offset_source":
            len(tpex_offset),

        "twse_volume_source":
            len(twse_volume),

        "tpex_volume_source":
            len(tpex_volume),

        "official_valid":
            official_valid,

        "fallback_valid":
            fallback_valid,

        "valid_rates":
            valid_rates,

        "invalid":
            invalid,

        "twse_valid":
            twse_valid,

        "tpex_valid":
            tpex_valid,

        **fallback_stats,
    }

    log("")
    log(
        f"✓ 有效資券當沖率："
        f"{valid_rates}"
    )

    log(
        f"  官方："
        f"{official_valid}"
    )

    log(
        f"  備援："
        f"{fallback_valid}"
    )

    log(
        f"  無效/缺資料："
        f"{invalid}"
    )

    log(
        f"  TWSE 有效："
        f"{twse_valid}"
    )

    log(
        f"  TPEx 有效："
        f"{tpex_valid}"
    )

    return (
        result,
        statistics,
    )


# ============================================================
# BUILD CHIP
# ============================================================

def build_chip(
    securities: List[
        Dict[str, str]
    ],
    history: Dict[
        str,
        List[float]
    ],
    daytrade: Dict[
        str,
        Dict[str, Optional[float]]
    ],
    data_date: str,
) -> Dict[
    str,
    Dict[str, Any]
]:

    section(
        "4. 建立 Chip"
    )

    stocks = {}

    for item in securities:

        symbol = item[
            "symbol"
        ]

        values = history.get(
            symbol,
            [],
        )

        dt = daytrade.get(
            symbol,
            {},
        )

        stocks[
            symbol
        ] = {

            "symbol":
                symbol,

            "full_symbol":
                item["full_symbol"],

            "name":
                item["name"],

            "market":
                item["market"],

            "type":
                item["type"],

            "institutional_1d":
                (
                    values[0]
                    if len(values) >= 1
                    else None
                ),

            "institutional_5d":
                period_sum(
                    values,
                    5,
                ),

            "institutional_10d":
                period_sum(
                    values,
                    10,
                ),

            "institutional_20d":
                period_sum(
                    values,
                    20,
                ),

            # 正確原始欄位
            "margin_offset_volume":
                dt.get(
                    "margin_offset_volume"
                ),

            # Dashboard 舊欄位相容
            #
            # 語義已改成：
            # 資券相抵量（股）
            #
            # 不再是現股當沖成交股數。
            "day_trading_volume":
                dt.get(
                    "margin_offset_volume"
                ),

            "total_volume":
                dt.get(
                    "total_volume"
                ),

            "day_trading_rate":
                dt.get(
                    "day_trading_rate"
                ),

            "day_trading_source":
                dt.get(
                    "day_trading_source"
                ),

            "day_trading_source_name":
                dt.get(
                    "day_trading_source_name"
                ),

            "day_trading_source_field":
                dt.get(
                    "day_trading_source_field"
                ),

            "day_trading_source_unit":
                dt.get(
                    "day_trading_source_unit"
                ),

            "day_trading_source_date":
                dt.get(
                    "day_trading_source_date"
                ),

            "updated_at":
                data_date,
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


def validate_structure(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    section(
        "5. Chip 結構驗證"
    )

    required = {
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

    errors = 0

    for symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            errors += 1
            continue

        missing = (
            required
            - set(
                item.keys()
            )
        )

        if missing:

            log(
                f"❌ {symbol} "
                f"缺欄位："
                f"{sorted(missing)}"
            )

            errors += len(
                missing
            )

            continue

        if clean_code(
            item.get(
                "symbol",
                "",
            )
        ) != symbol:

            errors += 1

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            errors += 1

        rate = item.get(
            "day_trading_rate"
        )

        offset = item.get(
            "margin_offset_volume"
        )

        total = item.get(
            "total_volume"
        )

        alias_volume = item.get(
            "day_trading_volume"
        )

        # ----------------------------------------------------
        # rate
        # ----------------------------------------------------

        if rate is not None:

            if (
                not isinstance(
                    rate,
                    (int, float),
                )
                or rate < 0
                or rate > 100
            ):

                log(
                    f"❌ {symbol} "
                    f"當沖率異常："
                    f"{rate}"
                )

                errors += 1

        # ----------------------------------------------------
        # offset
        # ----------------------------------------------------

        if (
            offset is not None
            and offset < 0
        ):

            errors += 1

        # ----------------------------------------------------
        # total
        # ----------------------------------------------------

        if (
            total is not None
            and total <= 0
        ):

            errors += 1

        # ----------------------------------------------------
        # offset <= volume
        # ----------------------------------------------------

        if (
            offset is not None
            and total is not None
            and offset > total
        ):

            log(
                f"❌ {symbol} "
                f"資券相抵量 > 成交量"
            )

            errors += 1

        # ----------------------------------------------------
        # legacy alias must equal raw offset
        # ----------------------------------------------------

        if (
            offset is not None
            and alias_volume != offset
        ):

            log(
                f"❌ {symbol} "
                f"day_trading_volume "
                f"不是 margin_offset_volume "
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
                f"❌ {symbol} "
                f"非法來源："
                f"{source}"
            )

            errors += 1

        if source == "official":

            if not item.get(
                "day_trading_source_name"
            ):

                errors += 1

            if not item.get(
                "day_trading_source_field"
            ):

                errors += 1

        if (
            source
            == "validated_fallback"
        ):

            if not item.get(
                "day_trading_source_date"
            ):

                log(
                    f"❌ {symbol} "
                    f"備援資料沒有日期"
                )

                errors += 1

    # --------------------------------------------------------
    # forbidden
    # --------------------------------------------------------

    for symbol, item in (
        stocks.items()
    ):

        for forbidden in (
            FORBIDDEN_FIELDS
        ):

            if forbidden in item:

                log(
                    f"❌ {symbol}."
                    f"{forbidden} "
                    f"禁止存在"
                )

                errors += 1

    if errors:

        log(
            f"❌ 結構 Gate FAIL："
            f"{errors}"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔 "
        f"結構驗證 PASS"
    )

    return True


# ============================================================
# DATA QUALITY GATE
# ============================================================

def data_quality_gate(
    securities: List[
        Dict[str, str]
    ],
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
    statistics: Dict[
        str,
        int
    ],
    data_date: str,
) -> bool:

    section(
        "6. 資料品質 Gate"
        "（實際數值驗證）"
    )

    errors = 0

    universe_count = len(
        securities
    )

    chip_count = len(
        stocks
    )

    if (
        universe_count
        != chip_count
    ):

        log(
            f"❌ Universe / Chip："
            f"{universe_count} / "
            f"{chip_count}"
        )

        errors += 1

    valid = 0
    formula_fail = 0
    source_fail = 0

    for symbol, item in (
        stocks.items()
    ):

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

        # ----------------------------------------------------
        # raw data must exist
        # ----------------------------------------------------

        if (
            offset is None
            or total is None
            or total <= 0
        ):

            log(
                f"❌ {symbol} "
                f"rate 有值但原始欄位不完整"
            )

            formula_fail += 1

            continue

        # ----------------------------------------------------
        # formula re-calculation
        # ----------------------------------------------------

        expected = round(
            (
                offset
                / total
            )
            * 100.0,
            4,
        )

        if (
            abs(
                expected
                - float(rate)
            )
            > 0.0001
        ):

            log(
                f"❌ {symbol} "
                f"公式驗證失敗："
                f"stored={rate}, "
                f"expected={expected}"
            )

            formula_fail += 1

        # ----------------------------------------------------
        # source validation
        # ----------------------------------------------------

        if source not in {
            "official",
            "validated_fallback",
        }:

            source_fail += 1

        # ----------------------------------------------------
        # numeric sanity
        # ----------------------------------------------------

        if (
            offset < 0
            or total <= 0
            or offset > total
        ):

            formula_fail += 1

    log(
        f"Universe / Chip："
        f"{universe_count} / "
        f"{chip_count}"
    )

    log(
        f"有效實際當沖率："
        f"{valid}"
    )

    log(
        f"公式驗證失敗："
        f"{formula_fail}"
    )

    log(
        f"來源驗證失敗："
        f"{source_fail}"
    )

    log(
        "TWSE 官方資券相抵原始筆數："
        f"{statistics.get("
            "twse_official_offset_source",
            0
        )}"
    )

    log(
        "TPEx 官方資券相抵原始筆數："
        f"{statistics.get("
            "tpex_official_offset_source",
            0
        )}"
    )

    log(
        "TWSE 官方成交量筆數："
        f"{statistics.get("
            "twse_volume_source",
            0
        )}"
    )

    log(
        "TPEx 官方成交量筆數："
        f"{statistics.get("
            "tpex_volume_source",
            0
        )}"
    )

    log(
        "官方有效率："
        f"{statistics.get("
            "official_valid",
            0
        )}"
    )

    log(
        "備援有效率："
        f"{statistics.get("
            "fallback_valid",
            0
        )}"
    )

    # --------------------------------------------------------
    # 市場官方來源 Gate
    # --------------------------------------------------------

    twse_universe = sum(
        1
        for item in securities
        if item["market"]
        == "TWSE"
    )

    tpex_universe = sum(
        1
        for item in securities
        if item["market"]
        == "TPEX"
    )

    twse_official = (
        statistics.get(
            "twse_official_offset_source",
            0,
        )
    )

    tpex_official = (
        statistics.get(
            "tpex_official_offset_source",
            0,
        )
    )

    twse_fallback = (
        statistics.get(
            "twse_fallback",
            0,
        )
    )

    tpex_fallback = (
        statistics.get(
            "tpex_fallback",
            0,
        )
    )

    if (
        twse_universe > 0
        and twse_official == 0
        and twse_fallback == 0
    ):

        log(
            "❌ TWSE 官方來源失敗，"
            "且備援沒有取得有效原始資料"
        )

        errors += 1

    if (
        tpex_universe > 0
        and tpex_official == 0
        and tpex_fallback == 0
    ):

        log(
            "❌ TPEx 官方來源失敗，"
            "且備援沒有取得有效原始資料"
        )

        errors += 1

    # --------------------------------------------------------
    # 至少要有真正可驗證的數值
    # --------------------------------------------------------

    if valid == 0:

        log(
            "❌ 全市場沒有任何 "
            "可驗證的資券當沖率"
        )

        errors += 1

    # --------------------------------------------------------
    # 實際公式失敗
    # --------------------------------------------------------

    if formula_fail:

        errors += formula_fail

    if source_fail:

        errors += source_fail

    if errors:

        log("")
        log(
            f"❌ 資料品質 Gate FAIL："
            f"{errors}"
        )

        log(
            "❌ 本次 BUILD 不寫入 chip.json"
        )

        return False

    log("")
    log(
        "✓ 原始資券相抵量存在"
    )

    log(
        "✓ 成交量存在"
    )

    log(
        "✓ 當沖率公式重新計算一致"
    )

    log(
        "✓ 來源欄位可追溯"
    )

    log(
        "✓ 資料品質 Gate PASS"
    )

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

    temp_file = (
        CHIP_FILE.with_suffix(
            ".json.tmp"
        )
    )

    try:

        temp_file.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temp_file.replace(
            CHIP_FILE
        )

        return True

    except Exception as exc:

        log(
            f"❌ Atomic Write 失敗："
            f"{exc}"
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
    securities: List[
        Dict[str, str]
    ],
    data_date: str,
) -> bool:

    section(
        "7. 寫入後再次驗證"
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

        stocks = payload.get(
            "stocks",
            {},
        )

        if not isinstance(
            stocks,
            dict,
        ):

            log(
                "❌ stocks 結構錯誤"
            )

            return False

        if (
            len(stocks)
            != len(securities)
        ):

            log(
                "❌ 寫入後 "
                "Universe / Chip "
                "數量不一致"
            )

            return False

        for symbol, item in (
            stocks.items()
        ):

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

            # alias 必須一致
            if (
                offset is not None
                and alias_volume
                != offset
            ):

                log(
                    f"❌ {symbol} "
                    f"相容欄位不一致"
                )

                return False

            if rate is None:
                continue

            if (
                offset is None
                or total is None
                or total <= 0
            ):

                log(
                    f"❌ {symbol} "
                    f"原始欄位不完整"
                )

                return False

            expected = round(
                (
                    offset
                    / total
                )
                * 100.0,
                4,
            )

            if (
                abs(
                    expected
                    - float(rate)
                )
                > 0.0001
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後公式不一致"
                )

                return False

            if item.get(
                "updated_at"
            ) != data_date:

                log(
                    f"❌ {symbol} "
                    f"日期不一致"
                )

                return False

        log(
            "✓ 寫入後實際數值驗證 PASS"
        )

        return True

    except Exception as exc:

        log(
            f"❌ 寫入後驗證失敗："
            f"{exc}"
        )

        return False


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
        f"開始時間："
        f"{now_tw().isoformat()}"
    )

    try:

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        securities = (
            load_universe()
        )

        # ----------------------------------------------------
        # 2. Institutional
        # ----------------------------------------------------

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
                "❌ 法人 20D 取得失敗"
            )

            log(
                "❌ 停止 BUILD"
            )

            return 1

        # ----------------------------------------------------
        # 3. 正確的資券當沖率
        # ----------------------------------------------------

        daytrade, statistics = (
            build_daytrade_data(
                securities,
                data_date,
            )
        )

        # ----------------------------------------------------
        # 4. Chip
        # ----------------------------------------------------

        stocks = build_chip(
            securities,
            history,
            daytrade,
            data_date,
        )

        # ----------------------------------------------------
        # 5. Structure Gate
        # ----------------------------------------------------

        if not validate_structure(
            stocks
        ):

            return 1

        # ----------------------------------------------------
        # 6. Data Quality Gate
        # ----------------------------------------------------

        if not data_quality_gate(
            securities,
            stocks,
            statistics,
            data_date,
        ):

            return 1

        # ----------------------------------------------------
        # 7. Payload
        # ----------------------------------------------------

        payload = {

            "version":
                VERSION,

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

                "forbidden_numerator_sources": [

                    "TWTB4U",

                    "tpex_intraday_trading_statistics",

                    "可現股當沖標的",

                    "現股當沖成交股數",
                ],
            },
        }

        # ----------------------------------------------------
        # 8. Atomic Write
        # ----------------------------------------------------

        if not atomic_write(
            payload
        ):

            return 1

        # ----------------------------------------------------
        # 9. Post Write Verify
        # ----------------------------------------------------

        if not verify_written_chip(
            securities,
            data_date,
        ):

            return 1

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
            f"✓ data_date："
            f"{data_date}"
        )

        log(
            f"✓ Universe："
            f"{len(securities)}"
        )

        log(
            f"✓ 有效資券當沖率："
            f"{statistics.get("
                "valid_rates",
                0
            )}"
        )

        log(
            f"✓ elapsed："
            f"{elapsed:.1f}s"
        )

        return 0

    except KeyboardInterrupt:

        log(
            "❌ 使用者中斷"
        )

        return 130

    except Exception as exc:

        log(
            f"❌ BUILD EXCEPTION："
            f"{exc}"
        )

        return 1


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
