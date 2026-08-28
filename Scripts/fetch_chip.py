#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_chip.py
============================================================

核心契約
------------------------------------------------------------
1. Data/universe.json 是唯一 Universe 來源
2. 只接受 status == "active"
3. TWSE / TPEx 分開使用各自官方資料來源
4. 三大法人：
   - TWSE：TWSE 官方 T86
   - TPEx：TPEx 官方 3insti
5. 資券當沖率：
   - TWSE：TWSE 官方 MI_MARGN「資券互抵」
   - TPEx：TPEx 官方 S23 / STKDMARGIN.TXT「資券相抵」
6. 成交量：
   - TWSE：TWSE 官方 STOCK_DAY_ALL
   - TPEx：TPEx 官方 daily_close_quotes
7. 公式：

       資券相抵量(股)
       ───────────── × 100
          成交量(股)

8. 完全禁止：
   - MoneyLink
   - CMoney
   - Yahoo Finance
   - 任何第三方網站 fallback
   - 現股當沖成交股數
   - 可現股當沖標的數量
   - TWTB4U
   - tpex_intraday_trading_statistics
   - 融資餘額
   - 融券餘額
   - 融券賣出量

9. TPEx STKDMARGIN.TXT：
   官方 S23 格式每筆 165 bytes。
   最後欄位：
       147:155 = 資券相抵
   單位：
       千股
   因此：
       資券相抵股數 = 欄位值 × 1000

10. 官方資券相抵資料不足時：
    不准 fallback。
    該股票 day_trading_rate = None。
    Gate 會阻止錯誤資料寫入。

11. Universe / Chip 必須 1:1。

12. 寫檔採 atomic write。
============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys
import time

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "OFFICIAL-SOURCE-ONLY-2026.08.29"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# OFFICIAL TWSE
# ============================================================

TWSE_WEB = "https://www.twse.com.tw/rwd/zh"

TWSE_T86_URL = (
    TWSE_WEB
    + "/fund/T86"
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
# OFFICIAL TPEx
# ============================================================

TPEX_OPENAPI = (
    "https://www.tpex.org.tw/openapi/v1"
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


# ============================================================
# TPEx OFFICIAL S23
# ============================================================

TPEX_S23_FILENAME = "STKDMARGIN.TXT"

TPEX_S23_RECORD_LENGTH = 165

# 官方 S23 固定長度欄位
S23_CODE_START = 0
S23_CODE_END = 6

S23_MARGIN_OFFSET_START = 147
S23_MARGIN_OFFSET_END = 155

SHARES_PER_TRADING_UNIT = 1000


# ============================================================
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 40
RETRIES = 4
REQUEST_SLEEP = 0.6

HISTORY_DAYS = 20
MAX_LOOKBACK_DAYS = 70


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
    "Referer": "https://www.tpex.org.tw/",
}


session = requests.Session()
session.headers.update(HEADERS)


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
        .replace("\u3000", "")
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
        value_float = float(text)

        if not math.isfinite(value_float):
            return None

        return value_float

    except Exception:
        return None


# ============================================================
# FIELD FIND
# ============================================================

def find_field(
    row: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized: Dict[str, Any] = {}

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
# RESPONSE NORMALIZATION
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

        for index, key in enumerate(fields):

            if index >= len(row):
                break

            record[str(key)] = row[index]

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

            result.extend(
                rows_from_fields_data(
                    table.get("fields"),
                    table.get("data"),
                )
            )

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
) -> Optional[Any]:

    last_error = ""

    for attempt in range(1, RETRIES + 1):

        try:

            response = session.get(
                url,
                params=params,
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

        if attempt < RETRIES:
            time.sleep(attempt)

    log(
        f"      ❌ {last_error}"
    )

    return None


def request_bytes(
    url: str,
) -> Optional[bytes]:

    last_error = ""

    for attempt in range(1, RETRIES + 1):

        try:

            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            if (
                response.status_code == 200
                and response.content
            ):
                return response.content

            last_error = (
                f"HTTP {response.status_code}"
            )

        except Exception as exc:

            last_error = str(exc)

        if attempt < RETRIES:
            time.sleep(attempt)

    log(
        f"      ❌ {last_error}"
    )

    return None


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> List[Dict[str, str]]:

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

    if not isinstance(raw, dict):
        raise RuntimeError(
            "universe.json 的 stocks 結構無效"
        )

    securities: List[Dict[str, str]] = []
    seen = set()

    for key, item in raw.items():

        if not isinstance(item, dict):
            continue

        if clean_text(
            item.get("status", "")
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
        f"✓ Universe：{len(securities)} 檔"
    )

    log(
        "  TWSE："
        + str(
            sum(
                x["market"] == "TWSE"
                for x in securities
            )
        )
    )

    log(
        "  TPEx："
        + str(
            sum(
                x["market"] == "TPEX"
                for x in securities
            )
        )
    )

    return securities


# ============================================================
# TWSE INSTITUTIONAL
# ============================================================

def fetch_twse_institutional(
    data_date: str,
) -> Dict[str, float]:

    payload = request_json(
        TWSE_T86_URL,
        {
            "response": "json",
            "date": data_date,
            "selectType": "ALL",
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

        values: List[float] = []

        for aliases in (
            [
                "外陸資買賣超股數",
                "外資及陸資買賣超股數",
            ],
            [
                "投信買賣超股數",
            ],
            [
                "自營商買賣超股數",
            ],
        ):

            value = safe_number(
                find_field(
                    row,
                    aliases,
                )
            )

            if value is not None:
                values.append(value)

        if values:
            result[symbol] = sum(values)

    return result


# ============================================================
# TPEx INSTITUTIONAL
# ============================================================

def fetch_tpex_institutional(
    data_date: str,
) -> Dict[str, float]:

    compact = data_date.replace(
        "-",
        "",
    )

    params_list = [
        {
            "l": "zh-tw",
            "d": compact,
        },
        {
            "l": "zh-tw",
            "date": compact,
        },
        {
            "date": compact,
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

            values: List[float] = []

            for aliases in (
                [
                    "外資及陸資買賣超股數",
                    "外資買賣超",
                ],
                [
                    "投信買賣超股數",
                    "投信買賣超",
                ],
                [
                    "自營商買賣超股數",
                    "自營商買賣超",
                ],
            ):

                value = safe_number(
                    find_field(
                        row,
                        aliases,
                    )
                )

                if value is not None:
                    values.append(value)

            if values:
                result[symbol] = sum(values)

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
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_institutional(
        yyyymmdd(dt)
    )

    result = dict(twse)
    result.update(tpex)

    return result


# ============================================================
# INSTITUTIONAL HISTORY
# ============================================================

def fetch_institutional_history(
    days: int,
) -> Tuple[
    Optional[str],
    List[str],
    Dict[str, Dict[str, Optional[float]]],
]:

    section(
        f"2. 最近 {days} 個交易日三大法人"
    )

    history: Dict[
        str,
        Dict[str, Optional[float]],
    ] = {}

    trading_dates: List[str] = []

    current = (
        now_tw()
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    )

    attempts = 0

    while (
        len(trading_dates) < days
        and attempts < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            data_date = iso_date(
                current
            )

            log(
                f"[{len(trading_dates)+1}/"
                f"{days}] {data_date}"
            )

            data = fetch_daily_institutional(
                current
            )

            if data:

                trading_dates.append(
                    data_date
                )

                for symbol in data:

                    history.setdefault(
                        symbol,
                        {},
                    )

                for symbol in history:

                    history[symbol][
                        data_date
                    ] = data.get(symbol)

                log(
                    f"      ✓ 法人資料："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ 本日無有效法人資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(days=1)
        attempts += 1

    if len(trading_dates) < days:

        log(
            f"❌ 實際交易日不足："
            f"{len(trading_dates)} / {days}"
        )

        return None, [], {}

    for symbol in history:

        for date in trading_dates:

            if date not in history[symbol]:
                history[symbol][date] = None

    latest_date = trading_dates[0]

    log(
        f"✓ 有效交易日："
        f"{len(trading_dates)}"
    )

    log(
        f"✓ 最新法人資料日："
        f"{latest_date}"
    )

    return (
        latest_date,
        trading_dates,
        history,
    )


# ============================================================
# PERIOD SUM
# ============================================================

def period_sum(
    values: List[Optional[float]],
    days: int,
) -> Optional[float]:

    if len(values) < days:
        return None

    window = values[:days]

    if any(
        value is None
        for value in window
    ):
        return None

    return round(
        sum(
            float(value)
            for value in window
        ),
        2,
    )


# ============================================================
# TWSE VOLUME
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
# TPEx VOLUME
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

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    tables = payload.get("tables")

    if not isinstance(tables, list):
        return {}

    for table in tables:

        if not isinstance(table, dict):
            continue

        fields = table.get("fields")
        data = table.get("data")

        if not isinstance(fields, list):
            continue

        if not isinstance(data, list):
            continue

        normalized_fields = [
            normalize_key(x)
            for x in fields
        ]

        code_index = None
        offset_index = None

        for index, field in enumerate(
            normalized_fields
        ):

            if field in {
                normalize_key("證券代號"),
                normalize_key("股票代號"),
                normalize_key("代號"),
            }:
                code_index = index

            if (
                "資券互抵" in field
                or "資券相抵" in field
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

            if (
                code_index >= len(row)
                or offset_index >= len(row)
            ):
                continue

            symbol = clean_code(
                row[code_index]
            )

            if not symbol:
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
                "source": "official",
                "source_name": "TWSE_MI_MARGN",
                "source_field": "資券互抵",
                "source_unit": "trading_unit",
                "source_date": data_date,
                "margin_offset_volume_raw":
                    raw_offset,
                "margin_offset_volume":
                    offset_shares,
            }

    log(
        f"  ✓ {len(result)} 檔"
    )

    return result


# ============================================================
# TPEx S23 URL DISCOVERY
# ============================================================

def tpex_s23_urls(
    trade_date: str,
) -> List[str]:

    dt = datetime.strptime(
        trade_date,
        "%Y-%m-%d",
    )

    y = dt.strftime("%Y")
    m = dt.strftime("%m")
    compact = dt.strftime("%Y%m%d")
    ym = dt.strftime("%Y%m")

    # TPEx EDIS 官方檔案路徑。
    # 不加入任何第三方來源。
    return [
        (
            "https://www.tpex.org.tw/"
            f"storage/edis/{y}/{m}/"
            f"{TPEX_S23_FILENAME}"
        ),
        (
            "https://www.tpex.org.tw/"
            f"storage/edis/{compact}/"
            f"{TPEX_S23_FILENAME}"
        ),
        (
            "https://www.tpex.org.tw/"
            f"storage/edis/{ym}/"
            f"{TPEX_S23_FILENAME}"
        ),
    ]


# ============================================================
# TPEx S23 PARSER
# ============================================================

def parse_tpex_s23(
    raw: bytes,
    trade_date: str,
) -> Dict[str, Dict[str, Any]]:

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    # --------------------------------------------------------
    # 官方 S23：
    # 每筆 165 bytes
    # --------------------------------------------------------

    records: List[bytes] = []

    if len(raw) >= TPEX_S23_RECORD_LENGTH:

        for start in range(
            0,
            len(raw),
            TPEX_S23_RECORD_LENGTH,
        ):

            record = raw[
                start:
                start + TPEX_S23_RECORD_LENGTH
            ]

            if (
                len(record)
                == TPEX_S23_RECORD_LENGTH
            ):
                records.append(record)

    # --------------------------------------------------------
    # 若檔案有 CR/LF：
    # 再嘗試逐行解析。
    # --------------------------------------------------------

    if not records:

        records = [
            line
            for line in raw.splitlines()
            if len(line) >= 155
        ]

    for record in records:

        try:

            code = record[
                S23_CODE_START:
                S23_CODE_END
            ].decode(
                "ascii",
                errors="ignore",
            ).strip()

            if not code:
                continue

            if code.upper() in {
                "TOTSHR",
                "TOTAMT",
            }:
                continue

            if not code.isdigit():
                continue

            if not (
                4 <= len(code) <= 6
            ):
                continue

            raw_offset_text = record[
                S23_MARGIN_OFFSET_START:
                S23_MARGIN_OFFSET_END
            ].decode(
                "ascii",
                errors="ignore",
            ).strip()

            if not raw_offset_text:
                continue

            raw_offset = safe_number(
                raw_offset_text
            )

            if raw_offset is None:
                continue

            if raw_offset < 0:
                continue

            # 官方單位：千股
            offset_shares = (
                raw_offset
                * SHARES_PER_TRADING_UNIT
            )

            result[code] = {
                "symbol": code,
                "source": "official",
                "source_name":
                    "TPEx_S23_STKDMARGIN.TXT",
                "source_field":
                    "資券相抵",
                "source_unit":
                    "thousand_shares",
                "source_date":
                    trade_date,
                "margin_offset_volume_raw":
                    raw_offset,
                "margin_offset_volume":
                    offset_shares,
            }

        except (
            ValueError,
            IndexError,
            UnicodeDecodeError,
        ):
            continue

    return result


# ============================================================
# TPEx OFFICIAL S23
# ============================================================

def fetch_tpex_margin_offset(
    trade_date: str,
) -> Dict[str, Dict[str, Any]]:

    log(
        "TPEx 官方資券相抵："
    )

    raw: Optional[bytes] = None
    used_url = ""

    for url in tpex_s23_urls(
        trade_date
    ):

        try:

            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            if (
                response.status_code == 200
                and response.content
            ):

                raw = response.content
                used_url = url
                break

        except Exception as exc:

            log(
                f"  ⚠️ 官方 S23 "
                f"請求失敗：{exc}"
            )

    if raw is None:

        log(
            "  ❌ TPEx 官方 "
            "STKDMARGIN.TXT 無法取得"
        )

        return {}

    result = parse_tpex_s23(
        raw,
        trade_date,
    )

    if not result:

        log(
            "  ❌ TPEx 官方 "
            "STKDMARGIN.TXT "
            "解析後沒有任何有效資券相抵資料"
        )

        log(
            f"  官方 URL：{used_url}"
        )

        return {}

    log(
        f"  ✓ TPEx 官方資券相抵："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# DAY TRADE DATA
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

    twse_offset = (
        fetch_twse_margin_offset(
            data_date
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_offset = (
        fetch_tpex_margin_offset(
            data_date
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    # --------------------------------------------------------
    # 官方成交量
    # --------------------------------------------------------

    twse_volume = (
        fetch_twse_total_volume()
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_volume = (
        fetch_tpex_total_volume()
    )

    result: Dict[
        str,
        Dict[str, Optional[float]],
    ] = {}

    valid_rates = 0
    invalid = 0
    official_valid = 0

    twse_valid = 0
    tpex_valid = 0

    for item in securities:

        symbol = item["symbol"]
        market = item["market"]

        if market == "TWSE":

            source = twse_offset.get(
                symbol
            )

            total_volume = (
                twse_volume.get(symbol)
            )

        else:

            source = tpex_offset.get(
                symbol
            )

            total_volume = (
                tpex_volume.get(symbol)
            )

        offset = None

        if source is not None:

            offset = safe_number(
                source.get(
                    "margin_offset_volume"
                )
            )

        source_name = None
        source_field = None
        source_unit = None
        source_date = None

        if source is not None:

            source_name = source.get(
                "source_name"
            )

            source_field = source.get(
                "source_field"
            )

            source_unit = source.get(
                "source_unit"
            )

            source_date = source.get(
                "source_date"
            )

        rate: Optional[float] = None

        if (
            offset is not None
            and total_volume is not None
            and total_volume > 0
            and offset >= 0
            and offset <= total_volume
        ):

            rate = round(
                offset
                / total_volume
                * 100.0,
                4,
            )

            valid_rates += 1
            official_valid += 1

            if market == "TWSE":
                twse_valid += 1
            else:
                tpex_valid += 1

        else:

            invalid += 1

        result[symbol] = {
            "margin_offset_volume":
                offset,
            "day_trading_volume":
                offset,
            "total_volume":
                total_volume,
            "day_trading_rate":
                rate,
            "day_trading_source":
                (
                    "official"
                    if rate is not None
                    else None
                ),
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
        "valid_rates":
            valid_rates,
        "invalid":
            invalid,
        "twse_valid":
            twse_valid,
        "tpex_valid":
            tpex_valid,

        # 永遠為 0。
        # 正式版完全取消 fallback。
        "fallback_valid": 0,
        "twse_fallback": 0,
        "tpex_fallback": 0,
    }

    log("")
    log(
        f"✓ 有效資券當沖率："
        f"{valid_rates}"
    )

    log(
        f"  TWSE 官方："
        f"{twse_valid}"
    )

    log(
        f"  TPEx 官方："
        f"{tpex_valid}"
    )

    log(
        f"  無效/缺資料："
        f"{invalid}"
    )

    log(
        "  MoneyLink fallback："
        "已永久取消"
    )

    return result, statistics


# ============================================================
# BUILD CHIP
# ============================================================

def build_chip(
    securities: List[Dict[str, str]],
    trading_dates: List[str],
    history: Dict[
        str,
        Dict[str, Optional[float]],
    ],
    daytrade: Dict[
        str,
        Dict[str, Optional[float]],
    ],
    data_date: str,
) -> Dict[str, Dict[str, Any]]:

    section(
        "4. 建立 Chip"
    )

    stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for item in securities:

        symbol = item["symbol"]

        symbol_history = history.get(
            symbol,
            {},
        )

        values = [
            symbol_history.get(date)
            for date in trading_dates
        ]

        dt = daytrade.get(
            symbol,
            {},
        )

        stocks[symbol] = {

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

            "margin_offset_volume":
                dt.get(
                    "margin_offset_volume"
                ),

            # Dashboard 舊欄位相容
            "day_trading_volume":
                dt.get(
                    "day_trading_volume"
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

    for symbol, item in stocks.items():

        missing = (
            REQUIRED_FIELDS
            - set(item.keys())
        )

        if missing:

            log(
                f"❌ {symbol}: "
                f"缺少欄位："
                f"{sorted(missing)}"
            )

            errors += 1

        if item.get("symbol") != symbol:

            log(
                f"❌ {symbol}: "
                "symbol mismatch"
            )

            errors += 1

        source = item.get(
            "day_trading_source"
        )

        if source not in {
            None,
            "official",
        }:

            log(
                f"❌ {symbol}: "
                f"非法資料來源："
                f"{source}"
            )

            errors += 1

        # 正式版禁止任何 fallback
        if (
            "fallback"
            in str(source).lower()
        ):

            log(
                f"❌ {symbol}: "
                "偵測到 fallback source"
            )

            errors += 1

    if errors:

        log(
            f"❌ Structure Gate FAIL："
            f"{errors}"
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
    trading_dates: List[str],
) -> bool:

    section(
        "6. Data Quality Gate"
    )

    errors = 0

    universe_count = len(
        securities
    )

    chip_count = len(
        stocks
    )

    # --------------------------------------------------------
    # Universe / Chip 1:1
    # --------------------------------------------------------

    if universe_count != chip_count:

        log(
            f"❌ Universe / Chip："
            f"{universe_count} / "
            f"{chip_count}"
        )

        errors += 1

    universe_symbols = {
        item["symbol"]
        for item in securities
    }

    chip_symbols = set(
        stocks.keys()
    )

    missing = (
        universe_symbols
        - chip_symbols
    )

    extra = (
        chip_symbols
        - universe_symbols
    )

    if missing:

        log(
            "❌ Chip 缺少 Universe："
            f"{sorted(missing)}"
        )

        errors += len(missing)

    if extra:

        log(
            "❌ Chip 多出 Universe："
            f"{sorted(extra)}"
        )

        errors += len(extra)

    # --------------------------------------------------------
    # 20D
    # --------------------------------------------------------

    if len(trading_dates) != HISTORY_DAYS:

        log(
            f"❌ 實際交易日數量："
            f"{len(trading_dates)} / "
            f"{HISTORY_DAYS}"
        )

        errors += 1

    # --------------------------------------------------------
    # 官方來源
    # --------------------------------------------------------

    if statistics.get(
        "twse_fallback",
        0,
    ) != 0:

        log(
            "❌ TWSE fallback 非 0"
        )

        errors += 1

    if statistics.get(
        "tpex_fallback",
        0,
    ) != 0:

        log(
            "❌ TPEx fallback 非 0"
        )

        errors += 1

    if statistics.get(
        "fallback_valid",
        0,
    ) != 0:

        log(
            "❌ fallback_valid 非 0"
        )

        errors += 1

    # --------------------------------------------------------
    # 每檔資料
    # --------------------------------------------------------

    valid = 0

    for symbol, item in stocks.items():

        offset = item.get(
            "margin_offset_volume"
        )

        alias = item.get(
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

        source_date = item.get(
            "day_trading_source_date"
        )

        updated_at = item.get(
            "updated_at"
        )

        # 日期
        if updated_at != data_date:

            log(
                f"❌ {symbol}: "
                f"updated_at={updated_at}, "
                f"expected={data_date}"
            )

            errors += 1

        # fallback 完全禁止
        if source == "validated_fallback":

            log(
                f"❌ {symbol}: "
                "偵測到舊 fallback source"
            )

            errors += 1

        # 沒有官方資料時允許 None，
        # 但不可製造 rate。
        if rate is None:

            if (
                offset is not None
                or total is not None
            ):

                if (
                    offset is not None
                    and total is not None
                    and total > 0
                ):

                    log(
                        f"❌ {symbol}: "
                        "原始資料存在但 rate 缺失"
                    )

                    errors += 1

            continue

        # rate 有值必須完整
        if (
            offset is None
            or total is None
            or total <= 0
        ):

            log(
                f"❌ {symbol}: "
                "rate 有值但原始資料不完整"
            )

            errors += 1
            continue

        if offset < 0:

            log(
                f"❌ {symbol}: "
                "offset < 0"
            )

            errors += 1
            continue

        if offset > total:

            log(
                f"❌ {symbol}: "
                "offset > total_volume"
            )

            errors += 1
            continue

        if alias != offset:

            log(
                f"❌ {symbol}: "
                "day_trading_volume "
                "與 margin_offset_volume 不一致"
            )

            errors += 1

        expected = round(
            offset
            / total
            * 100.0,
            4,
        )

        try:
            stored = float(rate)

        except (
            TypeError,
            ValueError,
        ):

            log(
                f"❌ {symbol}: "
                "rate 無法轉換"
            )

            errors += 1
            continue

        if abs(
            expected - stored
        ) > 0.0001:

            log(
                f"❌ {symbol}: "
                f"rate={stored}, "
                f"expected={expected}"
            )

            errors += 1

        if source != "official":

            log(
                f"❌ {symbol}: "
                f"有效 rate 卻非官方 source："
                f"{source}"
            )

            errors += 1

        if not source_date:

            log(
                f"❌ {symbol}: "
                "official 缺 source_date"
            )

            errors += 1

        elif source_date != data_date:

            log(
                f"❌ {symbol}: "
                "source_date 不一致"
            )

            errors += 1

        valid += 1

    log("")
    log(
        f"Universe / Chip："
        f"{universe_count} / "
        f"{chip_count}"
    )

    log(
        f"有效資券當沖率："
        f"{valid}"
    )

    log(
        "TWSE 官方資券相抵："
        f"{statistics.get('twse_official_offset_source', 0)}"
    )

    log(
        "TPEx 官方資券相抵："
        f"{statistics.get('tpex_official_offset_source', 0)}"
    )

    log(
        "TWSE 官方成交量："
        f"{statistics.get('twse_volume_source', 0)}"
    )

    log(
        "TPEx 官方成交量："
        f"{statistics.get('tpex_volume_source', 0)}"
    )

    log(
        "MoneyLink fallback：0"
    )

    # 至少要有官方資料
    if valid == 0:

        log(
            "❌ 沒有任何可驗證的"
            "官方資券當沖率"
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
        "✓ 20D 使用固定交易日序列"
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
        "✓ 全部資料來源為官方"
    )

    log(
        "✓ MoneyLink fallback = 0"
    )

    log(
        "✓ Data Quality Gate PASS"
    )

    return True


# ============================================================
# PAYLOAD CONTRACT
# ============================================================

def validate_payload_contract(
    payload: Dict[str, Any],
    securities: List[Dict[str, str]],
) -> bool:

    if not isinstance(
        payload,
        dict,
    ):
        return False

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return False

    expected_symbols = {
        item["symbol"]
        for item in securities
    }

    actual_symbols = set(
        stocks.keys()
    )

    if (
        expected_symbols
        != actual_symbols
    ):
        return False

    if (
        payload.get(
            "universe_count"
        )
        != len(securities)
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

    if (
        definition.get(
            "numerator"
        )
        != "margin_offset_volume"
    ):
        return False

    if (
        definition.get(
            "denominator"
        )
        != "total_volume"
    ):
        return False

    if (
        definition.get(
            "formula"
        )
        != "資券相抵量 / 成交量 * 100"
    ):
        return False

    # 正式版來源契約
    forbidden = (
        "MoneyLink",
        "MONEYLINK",
        "validated_fallback",
        "CMoney",
    )

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
    )

    for word in forbidden:

        if word in serialized:
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

    temp_file = (
        DATA_DIR
        / "chip.json.tmp"
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
            f"❌ chip.json JSON "
            f"解析失敗：{exc}"
        )

        return False

    if not validate_payload_contract(
        payload,
        securities,
    ):

        log(
            "❌ Payload Contract FAIL"
        )

        return False

    stocks = payload.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        return False

    for symbol, item in stocks.items():

        if item.get(
            "symbol"
        ) != symbol:

            return False

        if item.get(
            "updated_at"
        ) != data_date:

            return False

        source = item.get(
            "day_trading_source"
        )

        if source not in {
            None,
            "official",
        }:

            return False

        if source == "validated_fallback":

            return False

        offset = item.get(
            "margin_offset_volume"
        )

        alias = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        if offset != alias:

            return False

        if rate is None:
            continue

        if (
            offset is None
            or total is None
            or total <= 0
        ):

            return False

        expected = round(
            offset
            / total
            * 100.0,
            4,
        )

        try:
            stored = float(rate)
        except Exception:
            return False

        if abs(
            expected - stored
        ) > 0.0001:

            return False

        if source != "official":

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
        "✓ 所有來源皆為 official"
    )

    log(
        "✓ fallback = 0"
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
        "台股 AI 選股系統 "
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

        securities = load_universe()

        # ----------------------------------------------------
        # 2. Institutional 20D
        # ----------------------------------------------------

        (
            data_date,
            trading_dates,
            history,
        ) = fetch_institutional_history(
            HISTORY_DAYS
        )

        if (
            not data_date
            or len(trading_dates)
            != HISTORY_DAYS
            or not history
        ):

            log(
                "❌ 三大法人 20D "
                "取得失敗"
            )

            return 1

        # ----------------------------------------------------
        # 3. Official Margin Offset
        # ----------------------------------------------------

        (
            daytrade,
            statistics,
        ) = build_daytrade_data(
            securities,
            data_date,
        )

        # ----------------------------------------------------
        # 4. Build Chip
        # ----------------------------------------------------

        stocks = build_chip(
            securities,
            trading_dates,
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
            trading_dates,
        ):
            return 1

        # ----------------------------------------------------
        # 7. Payload
        # ----------------------------------------------------

        payload: Dict[str, Any] = {

            "version":
                VERSION,

            "generated_at":
                now_tw().isoformat(),

            "data_date":
                data_date,

            "institutional_trading_dates":
                trading_dates,

            "institutional_history_days":
                HISTORY_DAYS,

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
                    "資券相抵量占當日成交量之比例",

                "official_sources": {

                    "TWSE":
                        "TWSE MI_MARGN",

                    "TPEX":
                        "TPEx S23 STKDMARGIN.TXT",

                    "volume_TWSE":
                        "TWSE STOCK_DAY_ALL",

                    "volume_TPEX":
                        "TPEx daily close quotes",
                },

                "forbidden_sources": [
                    "MoneyLink",
                    "CMoney",
                    "Yahoo Finance",
                    "TWTB4U",
                    "tpex_intraday_trading_statistics",
                    "可現股當沖標的",
                    "現股當沖成交股數",
                    "融資餘額",
                    "融券餘額",
                    "融券賣出量",
                ],
            },
        }

        # ----------------------------------------------------
        # 8. Payload Contract
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 9. Atomic Write
        # ----------------------------------------------------

        if not atomic_write(
            payload
        ):

            return 1

        # ----------------------------------------------------
        # 10. Post Write Verify
        # ----------------------------------------------------

        if not verify_written_chip(
            securities,
            data_date,
        ):

            log(
                "❌ Post Write Verify FAIL"
            )

            return 1

        # ----------------------------------------------------
        # 11. Result
        # ----------------------------------------------------

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
            f"✓ Chip："
            f"{len(stocks)}"
        )

        log(
            "✓ 法人實際交易日："
            f"{len(trading_dates)}"
        )

        log(
            "✓ 官方有效資券當沖率："
            f"{statistics.get('valid_rates', 0)}"
        )

        log(
            "✓ TWSE 官方："
            f"{statistics.get('twse_valid', 0)}"
        )

        log(
            "✓ TPEx 官方："
            f"{statistics.get('tpex_valid', 0)}"
        )

        log(
            "✓ MoneyLink fallback：0"
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
    sys.exit(main())