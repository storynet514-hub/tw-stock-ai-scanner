#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py
正式修正版：DayTrade Source / Parser Fix

核心：
1. Universe = Data/universe.json
2. 三大法人 = TWSE T86 + TPEx 官方資料
3. 1D / 5D / 10D / 20D
4. 當沖：
   - TWSE：官方 TWTB4U Web API
   - TPEx：官方 OpenAPI tpex_intraday_trading_statistics
5. 總成交量：
   - TWSE：官方 STOCK_DAY_ALL
   - TPEx：官方 tpex_mainboard_daily_close_quotes
6. 當沖率 = 當沖成交股數 / 總成交股數 × 100
7. 不估算主力
8. 不產生 main_force_*
9. 缺資料 = None
10. Universe / Chip 必須一致
11. API 失敗不得拿空資料冒充成功
12. Atomic Write
13. 寫入後重新驗證
"""

from __future__ import annotations

import csv
import io
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

VERSION = "V15.0.0"


# ============================================================
# PATH
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# OFFICIAL API
# ============================================================

# ------------------------------------------------------------
# TWSE
# ------------------------------------------------------------

TWSE_OPENAPI_BASE = (
    "https://openapi.twse.com.tw/v1"
)

TWSE_WEB_BASE = (
    "https://www.twse.com.tw/rwd/zh"
)

TWSE_T86_URL = (
    "https://www.twse.com.tw/rwd/zh/fund/T86"
)

TWSE_DAYTRADE_URL = (
    TWSE_WEB_BASE
    + "/afterTrading/TWTB4U"
)

TWSE_TOTAL_VOLUME_URL = (
    TWSE_OPENAPI_BASE
    + "/exchangeReport/STOCK_DAY_ALL"
)


# ------------------------------------------------------------
# TPEx
# ------------------------------------------------------------

TPEX_OPENAPI_BASE = (
    "https://www.tpex.org.tw/openapi/v1"
)

TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)

TPEX_DAYTRADE_URL = (
    TPEX_OPENAPI_BASE
    + "/tpex_intraday_trading_statistics"
)

TPEX_TOTAL_VOLUME_URL = (
    TPEX_OPENAPI_BASE
    + "/tpex_mainboard_daily_close_quotes"
)


# ============================================================
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 40
API_RETRIES = 4
REQUEST_SLEEP = 0.6
HISTORY_DAYS = 20
MAX_LOOKBACK_DAYS = 70


HEADERS_TWSE = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/javascript,"
        "text/plain,"
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

HEADERS_TPEX = {
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
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
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

def now_taiwan() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    )


def yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def roc_date(dt: datetime) -> str:
    return (
        f"{dt.year - 1911:03d}/"
        f"{dt.month:02d}/"
        f"{dt.day:02d}"
    )


def iso_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


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

    if text in {
        "",
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

    try:

        value_float = float(text)

        if not math.isfinite(
            value_float
        ):
            return None

        return value_float

    except Exception:

        return None


def is_valid_symbol(
    symbol: str,
) -> bool:

    symbol = clean_code(symbol)

    if not symbol:
        return False

    return bool(
        re.fullmatch(
            r"\d{4,6}[A-Z0-9]{0,2}",
            symbol,
        )
    )


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

    value = find_field(
        row,
        [
            "Code",
            "code",
            "SecuritiesCompanyCode",
            "SecurityCode",
            "StockCode",
            "證券代號",
            "股票代號",
            "證券代碼",
            "股票代碼",
        ],
    )

    return clean_code(value)


# ============================================================
# HTTP JSON
# ============================================================

def request_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    headers: Optional[
        Dict[str, str]
    ] = None,
    retries: int = API_RETRIES,
) -> Optional[Any]:

    last_error = ""

    request_headers = (
        headers
        if headers is not None
        else HEADERS_TWSE
    )

    for attempt in range(
        1,
        retries + 1,
    ):

        try:

            response = session.get(
                url,
                params=params,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
            )

            status = response.status_code

            if status != 200:

                last_error = (
                    f"HTTP {status}"
                )

                if attempt < retries:

                    time.sleep(
                        attempt
                    )

                    continue

                log(
                    f"      ❌ {last_error}"
                )

                return None

            text = response.text.strip()

            if not text:

                last_error = (
                    "EMPTY RESPONSE"
                )

                if attempt < retries:

                    time.sleep(
                        attempt
                    )

                    continue

                log(
                    "      ❌ EMPTY RESPONSE"
                )

                return None

            try:

                return response.json()

            except Exception as exc:

                last_error = (
                    f"JSON ERROR: {exc}"
                )

                if attempt < retries:

                    time.sleep(
                        attempt
                    )

                    continue

                log(
                    f"      ❌ {last_error}"
                )

                return None

        except Exception as exc:

            last_error = (
                f"HTTP ERROR: {exc}"
            )

            if attempt < retries:

                time.sleep(
                    attempt
                )

                continue

            log(
                f"      ❌ {last_error}"
            )

            return None

    return None


# ============================================================
# UNIVERSAL JSON RECORD NORMALIZER
#
# 這裡是本次最重要的修正之一。
#
# 同時支援：
#
# 1. list[dict]
# 2. {"data": [dict, ...]}
# 3. {"fields": [...], "data": [[...], ...]}
# 4. {"tables": [{"fields": [...], "data": [...]}, ...]}
# 5. data1 / fields1
# 6. data2 / fields2 ...
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

        for idx, field in enumerate(
            fields
        ):

            if idx >= len(row):
                break

            record[
                str(field)
            ] = row[idx]

        if record:

            result.append(
                record
            )

    return result


def normalize_records(
    payload: Any,
) -> List[Dict[str, Any]]:

    # --------------------------------------------------------
    # 1. list
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 2. fields + data
    # --------------------------------------------------------

    direct_fields = payload.get(
        "fields"
    )

    direct_data = payload.get(
        "data"
    )

    rows = rows_from_fields_data(
        direct_fields,
        direct_data,
    )

    if rows:

        return rows

    # --------------------------------------------------------
    # 3. data / Data / result / records
    # --------------------------------------------------------

    for key in (
        "data",
        "Data",
        "result",
        "results",
        "Result",
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

        dict_rows = [
            row
            for row in value
            if isinstance(
                row,
                dict,
            )
        ]

        if dict_rows:

            return dict_rows

    # --------------------------------------------------------
    # 4. tables
    # --------------------------------------------------------

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

            rows = rows_from_fields_data(
                table.get("fields"),
                table.get("data"),
            )

            result.extend(
                rows
            )

        if result:

            return result

    # --------------------------------------------------------
    # 5. data1 / fields1 ...
    # --------------------------------------------------------

    result = []

    for key, value in payload.items():

        if not str(key).lower().startswith(
            "data"
        ):
            continue

        suffix = str(key)[4:]

        if suffix and not suffix.isdigit():
            continue

        if not isinstance(
            value,
            list,
        ):
            continue

        field_key = (
            "fields" + suffix
        )

        fields = payload.get(
            field_key
        )

        if fields is None:

            fields = payload.get(
                "field" + suffix
            )

        rows = rows_from_fields_data(
            fields,
            value,
        )

        result.extend(
            rows
        )

    return result


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

        log(
            f"❌ 找不到："
            f"{UNIVERSE_FILE}"
        )

        return []

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:

            payload = json.load(
                file
            )

    except Exception as exc:

        log(
            f"❌ Universe JSON 錯誤："
            f"{exc}"
        )

        return []

    declared_count = None
    raw_items = []

    if isinstance(
        payload,
        dict,
    ):

        if payload.get(
            "universe_count"
        ) is not None:

            try:

                declared_count = int(
                    payload[
                        "universe_count"
                    ]
                )

            except Exception:

                return []

        stocks = payload.get(
            "stocks"
        )

        if isinstance(
            stocks,
            dict,
        ):

            for symbol, value in (
                stocks.items()
            ):

                if not isinstance(
                    value,
                    dict,
                ):
                    continue

                item = dict(value)

                item["symbol"] = symbol

                raw_items.append(
                    item
                )

        elif isinstance(
            payload.get("items"),
            list,
        ):

            raw_items = [
                dict(x)
                for x in payload["items"]
                if isinstance(x, dict)
            ]

    elif isinstance(
        payload,
        list,
    ):

        raw_items = [
            dict(x)
            for x in payload
            if isinstance(x, dict)
        ]

    securities = []
    seen = set()

    for item in raw_items:

        symbol = clean_code(
            item.get(
                "symbol",
                item.get(
                    "code",
                    "",
                ),
            )
        )

        if not symbol:
            continue

        if symbol in seen:
            continue

        if not is_valid_symbol(
            symbol
        ):
            continue

        seen.add(symbol)

        name = clean_text(
            item.get(
                "name",
                symbol,
            )
        )

        market = clean_text(
            item.get(
                "market",
                "",
            )
        ).upper()

        original_symbol = clean_text(
            item.get(
                "symbol",
                "",
            )
        ).upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            if (
                ".TWO" in original_symbol
                or original_symbol.endswith(
                    "TWO"
                )
            ):

                market = "TPEX"

            elif (
                ".TW" in original_symbol
                or original_symbol.endswith(
                    "TW"
                )
            ):

                market = "TWSE"

            else:

                # 僅作 Universe 已缺 market 時的
                # 最後 fallback。
                market = (
                    "TPEX"
                    if symbol.startswith("3")
                    else "TWSE"
                )

        sec_type = clean_text(
            item.get(
                "type",
                "STOCK",
            )
        ).upper()

        if sec_type not in {
            "STOCK",
            "ETF",
            "BOND",
        }:

            sec_type = "STOCK"

        full_symbol = clean_text(
            item.get(
                "full_symbol",
                "",
            )
        )

        if not full_symbol:

            full_symbol = (
                f"{symbol}.TWO"
                if market == "TPEX"
                else f"{symbol}.TW"
            )

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": name or symbol,
                "market": market,
                "type": sec_type,
            }
        )

    if (
        declared_count is not None
        and len(securities)
        != declared_count
    ):

        log(
            "❌ Universe 數量錯誤"
        )

        log(
            f"   header："
            f"{declared_count}"
        )

        log(
            f"   parsed："
            f"{len(securities)}"
        )

        return []

    stock_count = sum(
        1
        for x in securities
        if x["type"] == "STOCK"
    )

    etf_count = sum(
        1
        for x in securities
        if x["type"] == "ETF"
    )

    bond_count = sum(
        1
        for x in securities
        if x["type"] == "BOND"
    )

    twse_count = sum(
        1
        for x in securities
        if x["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for x in securities
        if x["market"] == "TPEX"
    )

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log("")
    log("Universe Type：")
    log(f"  BOND: {bond_count}")
    log(f"  ETF: {etf_count}")
    log(f"  STOCK: {stock_count}")

    log("")
    log("Universe Market：")
    log(f"  TPEX: {tpex_count}")
    log(f"  TWSE: {twse_count}")

    log(
        "✓ Type 完整繼承 Universe"
    )

    log(
        "✓ fetch_chip 不重新分類 ETF"
    )

    log(
        "✓ Bond 不會被轉成 ETF"
    )

    return securities


# ============================================================
# UNIVERSE COUNT VERIFY
# ============================================================

def verify_universe_count(
    securities: List[
        Dict[str, str]
    ],
) -> bool:

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as file:

            payload = json.load(
                file
            )

    except Exception:

        return False

    if not isinstance(
        payload,
        dict,
    ):

        return True

    expected = payload.get(
        "universe_count"
    )

    if expected is not None:

        try:

            expected = int(
                expected
            )

        except Exception:

            return False

        if len(securities) != expected:

            log(
                "❌ Universe header / "
                "parsed 數量不一致"
            )

            return False

    stocks = payload.get(
        "stocks"
    )

    if isinstance(
        stocks,
        dict,
    ):

        if len(stocks) != len(
            securities
        ):

            log(
                "❌ Universe stocks / "
                "parsed 數量不一致"
            )

            return False

    return True


# ============================================================
# TWSE INSTITUTIONAL
# ============================================================

def fetch_twse_institutional(
    date_str: str,
) -> Dict[str, float]:

    params = {
        "response": "json",
        "date": date_str,
        "selectType": "ALL",
    }

    payload = request_json(
        TWSE_T86_URL,
        params=params,
        headers=HEADERS_TWSE,
    )

    if not isinstance(
        payload,
        dict,
    ):

        return {}

    if payload.get(
        "stat"
    ) not in {
        "OK",
        "ok",
        None,
    }:

        return {}

    rows = normalize_records(
        payload
    )

    result = {}

    for row in rows:

        code = find_code(
            row
        )

        if not is_valid_symbol(
            code
        ):
            continue

        net = safe_number(
            find_field(
                row,
                [
                    "TotalNet",
                    "三大法人買賣超股數",
                    "三大法人買賣超",
                ],
            )
        )

        if net is None:

            # T86 的第 19 欄為三大法人
            # 買賣超股數。
            values = list(
                row.values()
            )

            if len(values) >= 19:

                net = safe_number(
                    values[18]
                )

        if net is None:
            continue

        result[code] = round(
            net / 1000.0,
            2,
        )

    return result


# ============================================================
# TPEx INSTITUTIONAL
# ============================================================

class SimpleTableParser(
    HTMLParser
):

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.rows = []
        self.current_row = None
        self.current_cell = None

    def handle_starttag(
        self,
        tag,
        attrs,
    ):

        tag = tag.lower()

        if tag == "tr":

            self.current_row = []

        elif (
            tag in {"td", "th"}
            and self.current_row is not None
        ):

            self.current_cell = []

    def handle_data(
        self,
        data,
    ):

        if self.current_cell is not None:

            self.current_cell.append(
                data
            )

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if (
            tag in {"td", "th"}
            and self.current_row is not None
        ):

            value = "".join(
                self.current_cell or []
            ).strip()

            self.current_row.append(
                value
            )

            self.current_cell = None

        elif tag == "tr":

            if self.current_row:

                self.rows.append(
                    self.current_row
                )

            self.current_row = None


def fetch_tpex_institutional(
    dt: datetime,
) -> Dict[str, float]:

    params = {
        "l": "zh-tw",
        "se": "EW",
        "t": "D",
        "d": roc_date(dt),
    }

    try:

        response = session.get(
            TPEX_INSTITUTIONAL_URL,
            params=params,
            headers=HEADERS_TPEX,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return {}

        text = response.text

    except Exception:

        return {}

    if not text.strip():
        return {}

    parser = SimpleTableParser()

    try:

        parser.feed(text)

    except Exception:

        return {}

    result = {}

    for row in parser.rows:

        if len(row) < 10:
            continue

        code = clean_code(
            row[0]
        )

        if not is_valid_symbol(
            code
        ):
            continue

        numbers = []

        for value in row[1:]:

            number = safe_number(
                value
            )

            if number is not None:

                numbers.append(
                    number
                )

        if not numbers:
            continue

        net = numbers[-1]

        result[code] = round(
            net / 1000.0,
            2,
        )

    return result


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
        dt
    )

    result = dict(twse)

    result.update(
        tpex
    )

    return result


# ============================================================
# HISTORY
# ============================================================

def fetch_history(
    days: int = HISTORY_DAYS,
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

    current = now_taiwan().replace(
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

            date_text = iso_date(
                current
            )

            log(
                f"[{successful_days + 1}/"
                f"{days}] "
                f"{date_text}"
            )

            data = fetch_daily_institutional(
                current
            )

            if data:

                successful_days += 1

                if latest_date is None:

                    latest_date = date_text

                for symbol, value in (
                    data.items()
                ):

                    history.setdefault(
                        symbol,
                        []
                    ).append(
                        value
                    )

                log(
                    f"      ✓ 法人資料："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ 本日無法人資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(
            days=1
        )

        attempts += 1

    log("")

    log(
        f"✓ 有效交易日："
        f"{successful_days}"
    )

    log(
        f"✓ 最新資料日："
        f"{latest_date}"
    )

    log(
        f"✓ 有法人資料標的："
        f"{len(history)}"
    )

    if successful_days < days:

        log(
            "❌ 無法取得完整 20D 法人資料"
        )

        return None, {}

    return latest_date, history


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
# TWSE DAYTRADE
#
# 不再使用：
# openapi.twse.com.tw/v1/exchangeReport/TWTB4U
#
# 改用官方：
# www.twse.com.tw/rwd/zh/afterTrading/TWTB4U
#
# 這個 API 是：
# {
#   stat,
#   date,
#   fields,
#   data
# }
#
# 本版 normalize_records() 已能正確轉換。
# ============================================================

def fetch_twse_daytrade(
    data_date: str,
) -> Dict[str, float]:

    log(
        "TWSE 當沖："
    )

    params = {
        "response": "json",
        "date": data_date,
    }

    payload = request_json(
        TWSE_DAYTRADE_URL,
        params=params,
        headers=HEADERS_TWSE,
    )

    if not isinstance(
        payload,
        dict,
    ):

        log(
            "  ❌ TWSE 當沖 API 無有效 JSON"
        )

        return {}

    stat = clean_text(
        payload.get(
            "stat",
            "",
        )
    )

    if (
        stat
        and stat.upper() != "OK"
    ):

        log(
            f"  ❌ TWSE 當沖 stat："
            f"{stat}"
        )

        return {}

    rows = normalize_records(
        payload
    )

    log(
        f"  API rows："
        f"{len(rows)}"
    )

    result = {}

    for row in rows:

        code = find_code(
            row
        )

        if not code:
            continue

        volume = safe_number(
            find_field(
                row,
                [
                    "TradeShares",
                    "當日沖銷交易成交股數",
                    "當日沖銷交易成交量",
                    "DayTradingShares",
                    "DayTradeShares",
                    "DayTradingVolume",
                    "DayTradeVolume",
                ],
            )
        )

        if volume is None:
            continue

        if volume < 0:
            continue

        result[code] = volume

    log(
        f"  ✓ 有效當沖資料："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE TOTAL VOLUME
# ============================================================

def fetch_twse_total_volume(
    data_date: str,
) -> Dict[str, float]:

    log(
        "TWSE 總成交量："
    )

    payload = request_json(
        TWSE_TOTAL_VOLUME_URL,
        headers=HEADERS_TWSE,
    )

    rows = normalize_records(
        payload
    )

    result = {}

    for row in rows:

        code = find_code(
            row
        )

        if not code:
            continue

        volume = safe_number(
            find_field(
                row,
                [
                    "TradeVolume",
                    "TradingVolume",
                    "TradingShares",
                    "成交股數",
                    "成交量",
                    "Volume",
                ],
            )
        )

        if volume is None:
            continue

        if volume <= 0:
            continue

        result[code] = volume

    log(
        f"  ✓ 總成交量："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx DAYTRADE
# ============================================================

def fetch_tpex_daytrade() -> Dict[
    str,
    float,
]:

    log(
        "TPEx 當沖："
    )

    payload = request_json(
        TPEX_DAYTRADE_URL,
        headers=HEADERS_TPEX,
    )

    rows = normalize_records(
        payload
    )

    log(
        f"  API rows："
        f"{len(rows)}"
    )

    result = {}

    for row in rows:

        code = find_code(
            row
        )

        if not code:
            continue

        volume = safe_number(
            find_field(
                row,
                [
                    "TradeShares",
                    "TradingShares",
                    "DayTradingShares",
                    "DayTradeShares",
                    "DayTradingVolume",
                    "DayTradeVolume",
                    "TradingVolume",
                    "當日沖銷交易成交股數",
                    "當日沖銷交易成交量",
                    "成交股數",
                    "成交量",
                ],
            )
        )

        if volume is None:
            continue

        if volume < 0:
            continue

        result[code] = volume

    log(
        f"  ✓ 有效當沖資料："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx TOTAL VOLUME
# ============================================================

def fetch_tpex_total_volume() -> Dict[
    str,
    float,
]:

    log(
        "TPEx 總成交量："
    )

    payload = request_json(
        TPEX_TOTAL_VOLUME_URL,
        headers=HEADERS_TPEX,
    )

    rows = normalize_records(
        payload
    )

    result = {}

    for row in rows:

        code = find_code(
            row
        )

        if not code:
            continue

        volume = safe_number(
            find_field(
                row,
                [
                    "TradingShares",
                    "TradeVolume",
                    "TradingVolume",
                    "Volume",
                    "成交股數",
                    "成交量",
                ],
            )
        )

        if volume is None:
            continue

        if volume <= 0:
            continue

        result[code] = volume

    log(
        f"  ✓ 總成交量："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# DAYTRADE INTEGRATION
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
        "3. 當沖資料"
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse_day = fetch_twse_daytrade(
        data_date.replace(
            "-",
            "",
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    twse_total = fetch_twse_total_volume(
        data_date
    )

    time.sleep(
        REQUEST_SLEEP
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    tpex_day = fetch_tpex_daytrade()

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_total = fetch_tpex_total_volume()

    log("")
    log(
        "當沖整合結果："
    )

    log(
        f"  TWSE 當沖來源："
        f"{len(twse_day)}"
    )

    log(
        f"  TWSE 總成交量："
        f"{len(twse_total)}"
    )

    log(
        f"  TPEx 當沖來源："
        f"{len(tpex_day)}"
    )

    log(
        f"  TPEx 總成交量："
        f"{len(tpex_total)}"
    )

    result = {}

    valid_rates = 0
    invalid = 0
    twse_valid = 0
    tpex_valid = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

        market = item[
            "market"
        ]

        if market == "TWSE":

            day_volume = (
                twse_day.get(
                    symbol
                )
            )

            total_volume = (
                twse_total.get(
                    symbol
                )
            )

        else:

            day_volume = (
                tpex_day.get(
                    symbol
                )
            )

            total_volume = (
                tpex_total.get(
                    symbol
                )
            )

        rate = None

        if (
            day_volume is not None
            and total_volume is not None
            and total_volume > 0
            and day_volume >= 0
            and day_volume <= total_volume
        ):

            rate = round(
                day_volume
                / total_volume
                * 100.0,
                4,
            )

            valid_rates += 1

            if market == "TWSE":

                twse_valid += 1

            else:

                tpex_valid += 1

        else:

            invalid += 1

        result[symbol] = {
            "day_trading_volume":
                day_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,
        }

    log("")
    log(
        f"  有效當沖率："
        f"{valid_rates}"
    )

    log(
        f"  無效資料："
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

    statistics = {
        "twse_daytrade_source":
            len(twse_day),

        "twse_total_volume_source":
            len(twse_total),

        "tpex_daytrade_source":
            len(tpex_day),

        "tpex_total_volume_source":
            len(tpex_total),

        "valid_rates":
            valid_rates,

        "invalid":
            invalid,

        "twse_valid":
            twse_valid,

        "tpex_valid":
            tpex_valid,
    }

    return (
        result,
        statistics,
    )


# ============================================================
# BUILD CHIP
# ============================================================

FORBIDDEN_FIELDS = {
    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",
}


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
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int],
]:

    section(
        "4. 建立 Chip"
    )

    stocks = {}

    complete_1d = 0
    complete_5d = 0
    complete_10d = 0
    complete_20d = 0
    insufficient = 0
    valid_daytrade = 0
    missing_daytrade = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

        values = history.get(
            symbol,
            [],
        )

        institutional_1d = (
            values[0]
            if len(values) >= 1
            else None
        )

        institutional_5d = (
            period_sum(
                values,
                5,
            )
        )

        institutional_10d = (
            period_sum(
                values,
                10,
            )
        )

        institutional_20d = (
            period_sum(
                values,
                20,
            )
        )

        if institutional_1d is not None:
            complete_1d += 1

        if institutional_5d is not None:
            complete_5d += 1

        if institutional_10d is not None:
            complete_10d += 1

        if institutional_20d is not None:
            complete_20d += 1

        if not values:
            insufficient += 1

        dt = daytrade.get(
            symbol,
            {},
        )

        day_volume = dt.get(
            "day_trading_volume"
        )

        total_volume = dt.get(
            "total_volume"
        )

        day_rate = dt.get(
            "day_trading_rate"
        )

        if day_rate is not None:

            valid_daytrade += 1

        else:

            missing_daytrade += 1

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
                institutional_1d,

            "institutional_5d":
                institutional_5d,

            "institutional_10d":
                institutional_10d,

            "institutional_20d":
                institutional_20d,

            "day_trading_volume":
                day_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                day_rate,

            "updated_at":
                data_date,
        }

    statistics = {

        "complete_1d":
            complete_1d,

        "complete_5d":
            complete_5d,

        "complete_10d":
            complete_10d,

        "complete_20d":
            complete_20d,

        "insufficient":
            insufficient,

        "valid_daytrade":
            valid_daytrade,

        "missing_daytrade":
            missing_daytrade,
    }

    return (
        stocks,
        statistics,
    )


# ============================================================
# FORBIDDEN FIELD
# ============================================================

def scan_forbidden_fields(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    errors = 0

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):
            continue

        for field in (
            FORBIDDEN_FIELDS
        ):

            if field in item:

                log(
                    f"❌ "
                    f"{symbol}.{field} "
                    f"禁止存在"
                )

                errors += 1

    return errors == 0


# ============================================================
# STRUCTURE VALIDATION
# ============================================================

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
        "day_trading_volume",
        "total_volume",
        "day_trading_rate",
        "updated_at",
    }

    errors = 0

    for symbol, item in stocks.items():

        if not isinstance(
            item,
            dict,
        ):

            errors += 1

            continue

        missing = (
            required
            - set(item.keys())
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

        if clean_code(
            item.get(
                "symbol",
                "",
            )
        ) != symbol:

            errors += 1

        if not clean_text(
            item.get(
                "name",
                "",
            )
        ):

            errors += 1

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            errors += 1

        if item.get(
            "type"
        ) not in {
            "STOCK",
            "ETF",
            "BOND",
        }:

            errors += 1

        rate = item.get(
            "day_trading_rate"
        )

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

        day_volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        if (
            day_volume is not None
            and day_volume < 0
        ):

            errors += 1

        if (
            total_volume is not None
            and total_volume <= 0
        ):

            errors += 1

        if (
            day_volume is not None
            and total_volume is not None
            and day_volume > total_volume
        ):

            log(
                f"❌ {symbol} "
                f"當沖成交股數 > "
                f"總成交股數"
            )

            errors += 1

    if not scan_forbidden_fields(
        stocks
    ):

        errors += 1

    if errors:

        log(
            f"❌ 結構驗證 FAIL："
            f"{errors}"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔"
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
    daytrade_statistics: Dict[
        str,
        int
    ],
) -> bool:

    section(
        "6. 資料品質 Gate"
    )

    errors = 0

    universe_count = len(
        securities
    )

    chip_count = len(
        stocks
    )

    valid_daytrade = sum(
        1
        for item in stocks.values()
        if item.get(
            "day_trading_rate"
        ) is not None
    )

    missing_daytrade = (
        universe_count
        - valid_daytrade
    )

    log(
        f"Universe / Chip："
        f"{universe_count} / "
        f"{chip_count}"
    )

    log(
        f"有效當沖資料："
        f"{valid_daytrade}"
    )

    log(
        f"缺當沖資料："
        f"{missing_daytrade}"
    )

    if (
        chip_count
        != universe_count
    ):

        log(
            "❌ Universe / Chip "
            "數量不一致"
        )

        errors += 1

    twse_source = (
        daytrade_statistics.get(
            "twse_daytrade_source",
            0,
        )
    )

    tpex_source = (
        daytrade_statistics.get(
            "tpex_daytrade_source",
            0,
        )
    )

    twse_valid = (
        daytrade_statistics.get(
            "twse_valid",
            0,
        )
    )

    tpex_valid = (
        daytrade_statistics.get(
            "tpex_valid",
            0,
        )
    )

    # --------------------------------------------------------
    # 核心 Gate
    # --------------------------------------------------------

    if (
        twse_source == 0
        and tpex_source == 0
    ):

        log(
            "❌ TWSE + TPEx "
            "當沖來源皆為 0"
        )

        errors += 1

    if valid_daytrade == 0:

        log(
            "❌ 全市場有效當沖資料 = 0"
        )

        errors += 1

    # --------------------------------------------------------
    # 市場級別
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

    if (
        twse_universe > 0
        and twse_source > 0
        and twse_valid == 0
    ):

        log(
            "❌ TWSE 有來源但 "
            "0 檔可計算當沖率"
        )

        errors += 1

    if (
        tpex_universe > 0
        and tpex_source > 0
        and tpex_valid == 0
    ):

        log(
            "❌ TPEx 有來源但 "
            "0 檔可計算當沖率"
        )

        errors += 1

    if errors:

        log("")
        log(
            f"❌ 資料品質 Gate FAIL："
            f"{errors}"
        )

        log("")
        log(
            "❌ 本次 BUILD 不允許 PASS"
        )

        log(
            "❌ 保留既有 chip.json"
        )

        return False

    log("")
    log(
        "✓ TWSE / TPEx 當沖來源有效"
    )

    log(
        "✓ 至少一檔有效當沖率"
    )

    log(
        "✓ Universe / Chip 數量一致"
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

    temp_file = CHIP_FILE.with_name(
        CHIP_FILE.name
        + ".tmp"
    )

    try:

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )

            file.flush()

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

            if temp_file.exists():

                temp_file.unlink()

        except Exception:

            pass

        return False


# ============================================================
# POST WRITE
# ============================================================

def verify_written_chip(
    expected_count: int,
) -> bool:

    section(
        "7. 寫入後重新驗證 chip.json"
    )

    if not CHIP_FILE.exists():

        log(
            "❌ chip.json 不存在"
        )

        return False

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:

            payload = json.load(
                file
            )

    except Exception as exc:

        log(
            f"❌ chip.json JSON 錯誤："
            f"{exc}"
        )

        return False

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

    if len(stocks) != expected_count:

        log(
            "❌ chip.json 數量錯誤"
        )

        return False

    if not scan_forbidden_fields(
        stocks
    ):

        return False

    valid_rate = 0

    for symbol, item in (
        stocks.items()
    ):

        if not isinstance(
            item,
            dict,
        ):

            return False

        if clean_code(
            item.get(
                "symbol",
                "",
            )
        ) != symbol:

            return False

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            valid_rate += 1

            if (
                not isinstance(
                    rate,
                    (int, float),
                )
                or rate < 0
                or rate > 100
            ):

                return False

    if valid_rate <= 0:

        log(
            "❌ 寫入後有效當沖率 = 0"
        )

        return False

    log(
        f"✓ chip.json："
        f"{len(stocks)} 檔"
    )

    log(
        f"✓ 有效當沖率："
        f"{valid_rate}"
    )

    log(
        "✓ 禁止欄位掃描 PASS"
    )

    return True


# ============================================================
# MAIN
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log(
        f"開始時間："
        f"{now_taiwan().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("")
    log(
        "資料架構："
    )

    log(
        "  Universe：Data/universe.json"
    )

    log(
        "  Output：Data/chip.json"
    )

    log(
        "  三大法人：TWSE + TPEx"
    )

    log(
        "  期間：1D / 5D / 10D / 20D"
    )

    log(
        "  TWSE 當沖："
        "官方 TWTB4U Web API"
    )

    log(
        "  TPEx 當沖："
        "官方 tpex_intraday_trading_statistics"
    )

    log(
        "  主力估算：禁止"
    )

    log(
        "  main_force_*：禁止"
    )

    # ========================================================
    # 1. Universe
    # ========================================================

    securities = load_universe()

    if not securities:

        log(
            "❌ Universe 載入失敗"
        )

        return 1

    if not verify_universe_count(
        securities
    ):

        return 1

    # ========================================================
    # 2. Institutional
    # ========================================================

    data_date, history = fetch_history(
        HISTORY_DAYS
    )

    if not data_date:

        log(
            "❌ 20D 法人資料取得失敗"
        )

        return 1

    if not history:

        log(
            "❌ history 為空"
        )

        return 1

    # ========================================================
    # 3. DayTrade
    # ========================================================

    daytrade, daytrade_statistics = (
        build_daytrade_data(
            securities,
            data_date,
        )
    )

    # ========================================================
    # 4. Build
    # ========================================================

    stocks, statistics = build_chip(
        securities,
        history,
        daytrade,
        data_date,
    )

    # ========================================================
    # 5. Count
    # ========================================================

    if len(stocks) != len(
        securities
    ):

        log(
            "❌ Chip / Universe "
            "數量不一致"
        )

        return 1

    # ========================================================
    # 6. Structure
    # ========================================================

    if not validate_structure(
        stocks
    ):

        return 1

    # ========================================================
    # 7. Data Quality
    # ========================================================

    if not data_quality_gate(
        securities,
        stocks,
        daytrade_statistics,
    ):

        return 1

    # ========================================================
    # 8. Counts
    # ========================================================

    stock_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "STOCK"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "ETF"
    )

    bond_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "BOND"
    )

    twse_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in stocks.values()
        if item["market"] == "TPEX"
    )

    valid_daytrade = sum(
        1
        for item in stocks.values()
        if item.get(
            "day_trading_rate"
        ) is not None
    )

    # ========================================================
    # 9. OUTPUT
    # ========================================================

    output = {

        "schema_version":
            VERSION,

        "data_date":
            data_date,

        "generated_at":
            now_taiwan().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "universe_count":
            len(stocks),

        "stock_count":
            stock_count,

        "etf_count":
            etf_count,

        "bond_count":
            bond_count,

        "twse_count":
            twse_count,

        "tpex_count":
            tpex_count,

        "statistics":
            statistics,

        "daytrade_statistics":
            daytrade_statistics,

        "stocks":
            stocks,
    }

    # ========================================================
    # 10. FINAL CHECK
    # ========================================================

    if (
        output["universe_count"]
        != len(securities)
    ):

        log(
            "❌ 最終 Universe / "
            "Chip 數量錯誤"
        )

        return 1

    if valid_daytrade <= 0:

        log(
            "❌ 最終驗證："
            "有效當沖率仍為 0"
        )

        return 1

    # ========================================================
    # 11. ATOMIC WRITE
    # ========================================================

    section(
        "Atomic Write → Data/chip.json"
    )

    if not atomic_write(
        output
    ):

        return 1

    log(
        f"✓ 已寫入："
        f"{CHIP_FILE}"
    )

    # ========================================================
    # 12. POST VERIFY
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 13. FINAL REPORT
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "全市場驗證結果"
    )

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log(
        f"✓ Chip："
        f"{len(stocks)} 檔"
    )

    log(
        f"✓ Stock："
        f"{stock_count} 檔"
    )

    log(
        f"✓ ETF："
        f"{etf_count} 檔"
    )

    log(
        f"✓ BOND："
        f"{bond_count} 檔"
    )

    log(
        f"✓ TWSE："
        f"{twse_count} 檔"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count} 檔"
    )

    log("")
    log(
        "三大法人資料完整度："
    )

    log(
        f"  1D："
        f"{statistics['complete_1d']}"
    )

    log(
        f"  5D："
        f"{statistics['complete_5d']}"
    )

    log(
        f"  10D："
        f"{statistics['complete_10d']}"
    )

    log(
        f"  20D："
        f"{statistics['complete_20d']}"
    )

    log(
        f"  無資料："
        f"{statistics['insufficient']}"
    )

    log("")
    log(
        "當沖資料："
    )

    log(
        f"  TWSE 當沖來源："
        f"{daytrade_statistics['twse_daytrade_source']}"
    )

    log(
        f"  TWSE 總成交量："
        f"{daytrade_statistics['twse_total_volume_source']}"
    )

    log(
        f"  TPEx 當沖來源："
        f"{daytrade_statistics['tpex_daytrade_source']}"
    )

    log(
        f"  TPEx 總成交量："
        f"{daytrade_statistics['tpex_total_volume_source']}"
    )

    log(
        f"  TWSE 有效當沖率："
        f"{daytrade_statistics['twse_valid']}"
    )

    log(
        f"  TPEx 有效當沖率："
        f"{daytrade_statistics['tpex_valid']}"
    )

    log(
        f"  全市場有效當沖率："
        f"{valid_daytrade}"
    )

    log("")
    log(
        "欄位政策："
    )

    log(
        "  ✓ institutional_1d"
    )

    log(
        "  ✓ institutional_5d"
    )

    log(
        "  ✓ institutional_10d"
    )

    log(
        "  ✓ institutional_20d"
    )

    log(
        "  ✓ day_trading_volume"
    )

    log(
        "  ✓ total_volume"
    )

    log(
        "  ✓ day_trading_rate"
    )

    log(
        "  ✗ main_force_1d"
    )

    log(
        "  ✗ main_force_5d"
    )

    log(
        "  ✗ main_force_10d"
    )

    log(
        "  ✗ main_force_20d"
    )

    log("")
    log(
        "=" * 60
    )

    log(
        "CHIP BUILD PASS"
    )

    log(
        "=" * 60
    )

    log(
        f"✓ fetch_chip.py {VERSION}"
    )

    log(
        f"✓ 全市場 {len(stocks)} 檔"
    )

    log(
        f"✓ 有效當沖率 {valid_daytrade} 檔"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
