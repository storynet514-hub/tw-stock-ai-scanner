#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V11.1.0

============================================================
全市場籌碼資料正式版
============================================================

正式入口：
    Scripts/fetch_chip.py

輸入：
    Data/universe.json

輸出：
    Data/chip.json

資料：
    1. 三大法人 1D
    2. 三大法人 5D
    3. 三大法人 10D
    4. 三大法人 20D
    5. 當沖成交股數
    6. 個股總成交股數
    7. 當沖率

本版本修正：
    A. 恢復完整可執行檔案
    B. 修正 V11.0.0 函式宣告語法
    C. 所有 typing / helper / constant 完整定義
    D. TWSE 當沖 OpenAPI -> HTML fallback
    E. TPEx 當沖 OpenAPI -> HTML fallback
    F. 動態尋找欄位，不猜固定 index
    G. 當沖率必須 0~100
    H. 當沖成交股數不得大於總成交股數
    I. 缺資料 = None，不以 0 冒充
    J. Universe 是唯一股票池
    K. Universe / Chip 數量必須一致
    L. Atomic Write
    M. 寫入前後驗證
    N. 禁止 main_force_* 欄位

重要：
    institutional_* = 三大法人買賣超

不是：
    主力買賣超

因此禁止：
    main_force_1d
    main_force_5d
    main_force_10d
    main_force_20d
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
# Version
# ============================================================

VERSION = "V11.1.0"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# Network
# ============================================================

REQUEST_TIMEOUT = 30

REQUEST_SLEEP = 0.35

MAX_LOOKBACK_DAYS = 70

HISTORY_DAYS = 20


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, "
        "text/javascript, "
        "text/plain, "
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Referer": "https://www.twse.com.tw/",
}


TWSE_WEB_BASE = (
    "https://www.twse.com.tw"
)

TWSE_OPENAPI_BASE = (
    "https://openapi.twse.com.tw/v1"
)

TPEX_BASE = (
    "https://www.tpex.org.tw"
)

TPEX_OPENAPI_BASE = (
    "https://www.tpex.org.tw/openapi/v1"
)


# ============================================================
# Session
# ============================================================

session = requests.Session()

session.headers.update(HEADERS)


# ============================================================
# Logging
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Time
# ============================================================

def now_taiwan() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    )


def today_taiwan() -> str:
    return now_taiwan().strftime("%Y-%m-%d")


def yyyymmdd(date_obj: datetime) -> str:
    return date_obj.strftime("%Y%m%d")


def roc_date_slash(date_obj: datetime) -> str:
    roc_year = date_obj.year - 1911

    return (
        f"{roc_year:03d}/"
        f"{date_obj.month:02d}/"
        f"{date_obj.day:02d}"
    )


def roc_date_compact(date_obj: datetime) -> str:
    roc_year = date_obj.year - 1911

    return (
        f"{roc_year:03d}"
        f"{date_obj.month:02d}"
        f"{date_obj.day:02d}"
    )


def normalize_date_text(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
        .replace(".", "/")
        .replace("-", "/")
        .replace("\\", "/")
    )

    text = re.sub(
        r"\s+",
        "",
        text,
    )

    if re.fullmatch(
        r"\d{8}",
        text,
    ):
        try:
            return datetime.strptime(
                text,
                "%Y%m%d",
            ).strftime("%Y-%m-%d")
        except Exception:
            return None

    if re.fullmatch(
        r"\d{7}",
        text,
    ):
        try:
            year = int(text[:3]) + 1911
            month = int(text[3:5])
            day = int(text[5:7])

            return datetime(
                year,
                month,
                day,
            ).strftime("%Y-%m-%d")

        except Exception:
            return None

    parts = text.split("/")

    if len(parts) != 3:
        return None

    try:
        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        if year < 1911:
            year += 1911

        return datetime(
            year,
            month,
            day,
        ).strftime("%Y-%m-%d")

    except Exception:
        return None


# ============================================================
# Basic helpers
# ============================================================

def clean_code(value: Any) -> str:

    if value is None:
        return ""

    text = (
        str(value)
        .strip()
        .upper()
    )

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
    )

    return text


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def safe_number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ):
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("%", "")
    )

    if text in {
        "-",
        "--",
        "---",
        "None",
        "null",
        "NULL",
        "N/A",
        "NA",
        "無",
    }:
        return None

    try:
        number = float(text)

        if not math.isfinite(number):
            return None

        return number

    except Exception:
        return None


def normalize_field_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    text = str(value)

    text = re.sub(
        r"[\s　\r\n\t]+",
        "",
        text,
    )

    return text.strip()


def is_valid_symbol(
    code: str,
) -> bool:

    code = clean_code(code)

    if not code:
        return False

    return bool(
        re.fullmatch(
            r"\d{4,6}[A-Z0-9]{0,2}",
            code,
        )
    )


# ============================================================
# HTTP
# ============================================================

def get_response(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    headers: Optional[
        Dict[str, str]
    ] = None,
) -> Optional[requests.Response]:

    try:

        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      HTTP "
                f"{response.status_code}"
            )

            return None

        return response

    except Exception as exc:

        log(
            f"      API error："
            f"{exc}"
        )

        return None


def get_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    headers: Optional[
        Dict[str, str]
    ] = None,
) -> Optional[Any]:

    response = get_response(
        url,
        params,
        headers,
    )

    if response is None:
        return None

    text = response.text.strip()

    if not text:
        return None

    try:
        return response.json()

    except Exception as exc:

        log(
            f"      JSON parse error："
            f"{exc}"
        )

        return None


# ============================================================
# Dynamic JSON helpers
# ============================================================

def extract_json_tables(
    data: Any,
) -> List[
    Tuple[
        List[Any],
        List[Any],
    ]
]:

    tables = []

    if not isinstance(
        data,
        dict,
    ):
        return tables

    fields = data.get("fields")

    rows = data.get("data")

    if (
        isinstance(fields, list)
        and isinstance(rows, list)
    ):
        tables.append(
            (
                fields,
                rows,
            )
        )

    raw_tables = data.get("tables")

    if isinstance(
        raw_tables,
        list,
    ):

        for table in raw_tables:

            if not isinstance(
                table,
                dict,
            ):
                continue

            fields2 = table.get(
                "fields"
            )

            rows2 = table.get(
                "data"
            )

            if (
                isinstance(
                    fields2,
                    list,
                )
                and isinstance(
                    rows2,
                    list,
                )
            ):

                tables.append(
                    (
                        fields2,
                        rows2,
                    )
                )

    return tables


def find_column_exact(
    fields: List[Any],
    names: List[str],
) -> Optional[int]:

    wanted = {
        normalize_field_name(name)
        for name in names
    }

    for index, field in enumerate(fields):

        normalized = normalize_field_name(
            field
        )

        if normalized in wanted:
            return index

    return None


def find_column_contains(
    fields: List[Any],
    keywords: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(field)
        for field in fields
    ]

    for keyword in keywords:

        key = normalize_field_name(
            keyword
        )

        for index, field in enumerate(
            normalized
        ):

            if key in field:
                return index

    return None


def dict_field_name(
    row: Dict[str, Any],
    exact: List[str],
    contains: Optional[
        List[str]
    ] = None,
) -> Optional[str]:

    normalized = {
        normalize_field_name(key): key
        for key in row.keys()
    }

    for name in exact:

        normalized_name = (
            normalize_field_name(name)
        )

        if normalized_name in normalized:
            return normalized[
                normalized_name
            ]

    if contains:

        for keyword in contains:

            normalized_keyword = (
                normalize_field_name(
                    keyword
                )
            )

            for (
                normalized_key,
                original_key,
            ) in normalized.items():

                if (
                    normalized_keyword
                    in normalized_key
                ):
                    return original_key

    return None


def get_dict_code(
    row: Dict[str, Any],
) -> str:

    key = dict_field_name(
        row,
        [
            "Code",
            "StockCode",
            "SecuritiesCompanyCode",
            "證券代號",
            "股票代號",
            "代號",
        ],
    )

    if key is None:
        return ""

    return clean_code(
        row.get(key)
    )


def get_dict_date(
    row: Dict[str, Any],
) -> Optional[str]:

    key = dict_field_name(
        row,
        [
            "Date",
            "date",
            "TradeDate",
            "DataDate",
            "資料日期",
            "日期",
        ],
    )

    if key is None:
        return None

    return normalize_date_text(
        row.get(key)
    )


# ============================================================
# HTML parser
# ============================================================

class TableParser(HTMLParser):

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[
            List[str]
        ] = []

        self.current_row: Optional[
            List[str]
        ] = None

        self.current_cell: Optional[
            List[str]
        ] = None

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


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[
    Dict[str, str]
]:

    section(
        "讀取 Data/universe.json"
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
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ Universe JSON "
            f"解析失敗：{exc}"
        )

        return []

    declared_count = None

    if isinstance(
        data,
        dict,
    ):

        raw_count = data.get(
            "universe_count"
        )

        if raw_count is not None:

            try:
                declared_count = int(
                    raw_count
                )

            except Exception:

                log(
                    "❌ universe_count "
                    "無法轉成整數"
                )

                return []

    items: List[
        Dict[str, Any]
    ] = []

    stocks = (
        data.get("stocks")
        if isinstance(data, dict)
        else None
    )

    if isinstance(
        stocks,
        dict,
    ):

        for key, value in stocks.items():

            if not isinstance(
                value,
                dict,
            ):

                log(
                    f"❌ stocks[{key}] "
                    f"不是 object"
                )

                return []

            item = dict(value)

            item["symbol"] = clean_code(
                key
            )

            items.append(item)

    elif isinstance(
        data,
        dict,
    ):

        legacy = data.get(
            "items",
            [],
        )

        if isinstance(
            legacy,
            list,
        ):

            items = [
                dict(x)
                for x in legacy
                if isinstance(x, dict)
            ]

    elif isinstance(
        data,
        list,
    ):

        items = [
            dict(x)
            for x in data
            if isinstance(x, dict)
        ]

    if not items:

        log(
            "❌ Universe 沒有可用股票資料"
        )

        return []

    if (
        declared_count is not None
        and declared_count != len(items)
    ):

        log(
            "❌ Universe 數量矛盾"
        )

        log(
            f"   universe_count："
            f"{declared_count}"
        )

        log(
            f"   實際 stocks："
            f"{len(items)}"
        )

        return []

    securities: List[
        Dict[str, str]
    ] = []

    seen = set()

    rejected = []

    for item in items:

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

            rejected.append(
                {
                    "symbol": "",
                    "reason": "missing_symbol",
                }
            )

            continue

        if symbol in seen:

            rejected.append(
                {
                    "symbol": symbol,
                    "reason": "duplicate",
                }
            )

            continue

        if not is_valid_symbol(
            symbol
        ):

            rejected.append(
                {
                    "symbol": symbol,
                    "reason": "invalid_symbol",
                }
            )

            continue

        seen.add(symbol)

        name = clean_name(
            item.get(
                "name",
                symbol,
            )
        )

        market = str(
            item.get(
                "market",
                "",
            )
        ).strip().upper()

        original_symbol = str(
            item.get(
                "symbol",
                "",
            )
        ).strip().upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            if (
                ".TWO" in original_symbol
                or original_symbol.endswith("TWO")
            ):

                market = "TPEX"

            elif (
                ".TW" in original_symbol
                or original_symbol.endswith("TW")
            ):

                market = "TWSE"

            elif symbol.startswith("3"):

                market = "TPEX"

            else:

                market = "TWSE"

        raw_type = str(
            item.get(
                "type",
                "",
            )
        ).strip().lower()

        if raw_type == "etf":

            sec_type = "ETF"

        elif raw_type == "stock":

            sec_type = "Stock"

        else:

            if re.fullmatch(
                r"\d{4,6}[A-Z0-9]{1,2}",
                symbol,
            ):

                sec_type = "ETF"

            else:

                sec_type = "Stock"

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

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

    log("")
    log("Universe 驗證")
    log(
        f"  原始標的：{len(items)}"
    )
    log(
        f"  成功載入："
        f"{len(securities)}"
    )
    log(
        f"  被排除："
        f"{len(rejected)}"
    )

    if rejected:

        for item in rejected[:50]:

            log(
                f"   "
                f"{item['symbol']} | "
                f"{item['reason']}"
            )

    if len(securities) != len(items):

        log(
            "❌ Universe 解析後 "
            "數量不一致"
        )

        return []

    stock_count = sum(
        1
        for item in securities
        if item["type"] == "Stock"
    )

    etf_count = sum(
        1
        for item in securities
        if item["type"] == "ETF"
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
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log(
        f"✓ Stock：{stock_count}"
    )

    log(
        f"✓ ETF：{etf_count}"
    )

    log(
        f"✓ TWSE：{twse_count}"
    )

    log(
        f"✓ TPEX：{tpex_count}"
    )

    return securities


# ============================================================
# TWSE institutional
# ============================================================

def fetch_twse_institutional(
    date_str: str,
) -> Dict[str, float]:

    url = (
        f"{TWSE_WEB_BASE}/"
        "rwd/zh/fund/T86"
    )

    params = {
        "response": "json",
        "date": date_str,
        "selectType": "ALL",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[str, float] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    if data.get("stat") != "OK":
        return result

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):
        return result

    for row in rows:

        if not isinstance(
            row,
            list,
        ):
            continue

        if len(row) < 19:
            continue

        code = clean_code(
            row[0]
        )

        if not is_valid_symbol(
            code
        ):
            continue

        value = safe_number(
            row[18]
        )

        if value is None:
            continue

        result[code] = round(
            value / 1000.0,
            2,
        )

    return result


# ============================================================
# TPEx institutional
# ============================================================

def fetch_tpex_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    roc = roc_date_slash(
        date_obj
    )

    url = (
        f"{TPEX_BASE}/"
        "web/stock/3insti/daily_trade/"
        "3itrade_hedge_result.php"
    )

    params = {
        "l": "zh-tw",
        "se": "EW",
        "t": "D",
        "d": roc,
    }

    response = get_response(
        url,
        params,
    )

    if response is None:
        return {}

    parser = TableParser()

    try:
        parser.feed(
            response.text
        )
    except Exception:
        return {}

    result: Dict[str, float] = {}

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

        candidates = []

        for value in row[1:]:

            number = safe_number(
                value
            )

            if number is not None:
                candidates.append(
                    number
                )

        if not candidates:
            continue

        value = candidates[-1]

        result[code] = round(
            value / 1000.0,
            2,
        )

    return result


# ============================================================
# Daily institutional
# ============================================================

def fetch_daily_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    date_str = yyyymmdd(
        date_obj
    )

    twse = fetch_twse_institutional(
        date_str
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_institutional(
        date_obj
    )

    result = dict(twse)

    result.update(
        tpex
    )

    return result


# ============================================================
# Institutional history
# ============================================================

def fetch_history(
    days: int = HISTORY_DAYS,
) -> Tuple[
    Optional[str],
    Dict[str, List[float]],
]:

    section(
        f"同步最近 {days} "
        f"個交易日三大法人資料"
    )

    history: Dict[
        str,
        List[float]
    ] = {}

    successful_days = 0

    attempted = 0

    latest_date = None

    current = now_taiwan().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    while (
        successful_days < days
        and attempted < MAX_LOOKBACK_DAYS
    ):

        if current.weekday() < 5:

            date_text = current.strftime(
                "%Y-%m-%d"
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

                for symbol, value in data.items():

                    history.setdefault(
                        symbol,
                        [],
                    )

                    history[symbol].append(
                        value
                    )

                log(
                    f"      ✓ "
                    f"法人資料："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ "
                    "本日無有效法人資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(
            days=1
        )

        attempted += 1

    if successful_days == 0:

        return None, {}

    log("")
    log(
        f"✓ 成功取得 "
        f"{successful_days} "
        f"個交易日"
    )

    log(
        f"✓ 最新資料日："
        f"{latest_date}"
    )

    log(
        f"✓ 有歷史資料標的："
        f"{len(history)}"
    )

    return latest_date, history


# ============================================================
# TWSE day-trading OpenAPI
# ============================================================

def fetch_twse_daytrade_openapi() -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    url = (
        f"{TWSE_OPENAPI_BASE}/"
        "exchangeReport/TWTB4U"
    )

    data = get_json(
        url
    )

    if not isinstance(
        data,
        list,
    ):

        return {}, None, False

    result: Dict[str, float] = {}

    dates = set()

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        code = get_dict_code(
            row
        )

        if not is_valid_symbol(
            code
        ):
            continue

        row_date = get_dict_date(
            row
        )

        if row_date:
            dates.add(
                row_date
            )

        volume_key = dict_field_name(
            row,
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
                "DayTradingShares",
                "DayTradeShares",
                "IntradayTradingShares",
                "TradingShares",
            ],
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
            ],
        )

        if volume_key is None:
            continue

        volume = safe_number(
            row.get(
                volume_key
            )
        )

        if (
            volume is None
            or volume < 0
        ):
            continue

        result[code] = round(
            volume,
            2,
        )

    data_date = (
        max(dates)
        if dates
        else None
    )

    return (
        result,
        data_date,
        True,
    )


# ============================================================
# TWSE day-trading HTML fallback
# ============================================================

def fetch_twse_daytrade_html(
    date_obj: datetime,
) -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    url = (
        f"{TWSE_WEB_BASE}/"
        "exchangeReport/TWTB4U"
    )

    params = {
        "date": yyyymmdd(
            date_obj
        ),
        "response": "html",
        "selectType": "All",
    }

    response = get_response(
        url,
        params,
    )

    if response is None:
        return {}, None, False

    parser = TableParser()

    try:
        parser.feed(
            response.text
        )
    except Exception:
        return {}, None, False

    rows = parser.rows

    if not rows:
        return {}, None, False

    header_index = None

    code_index = None

    volume_index = None

    for index, row in enumerate(rows):

        normalized = [
            normalize_field_name(
                value
            )
            for value in row
        ]

        for i, value in enumerate(
            normalized
        ):

            if (
                "證券代號" in value
                or "股票代號" in value
            ):

                code_index = i
                break

        for i, value in enumerate(
            normalized
        ):

            if (
                "當日沖銷交易成交股數"
                in value
                or "當日沖銷成交股數"
                in value
            ):

                volume_index = i
                break

        if (
            code_index is not None
            and volume_index is not None
        ):

            header_index = index
            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}, None, False

    result: Dict[str, float] = {}

    for row in rows[
        header_index + 1:
    ]:

        if (
            code_index >= len(row)
            or volume_index >= len(row)
        ):
            continue

        code = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            code
        ):
            continue

        volume = safe_number(
            row[volume_index]
        )

        if (
            volume is None
            or volume < 0
        ):
            continue

        result[code] = round(
            volume,
            2,
        )

    return (
        result,
        date_obj.strftime(
            "%Y-%m-%d"
        ),
        bool(result),
    )


# ============================================================
# TPEx day-trading OpenAPI
# ============================================================

def fetch_tpex_daytrade_openapi() -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_intraday_trading_statistics"
    )

    data = get_json(
        url
    )

    if not isinstance(
        data,
        list,
    ):

        return {}, None, False

    result: Dict[str, float] = {}

    dates = set()

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        code = get_dict_code(
            row
        )

        if not is_valid_symbol(
            code
        ):
            continue

        row_date = get_dict_date(
            row
        )

        if row_date:
            dates.add(
                row_date
            )

        volume_key = dict_field_name(
            row,
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
                "IntradayTradingShares",
                "DayTradingShares",
                "DayTradeShares",
                "TradingShares",
            ],
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
            ],
        )

        if volume_key is None:
            continue

        volume = safe_number(
            row.get(
                volume_key
            )
        )

        if (
            volume is None
            or volume < 0
        ):
            continue

        result[code] = round(
            volume,
            2,
        )

    data_date = (
        max(dates)
        if dates
        else None
    )

    return (
        result,
        data_date,
        True,
    )


# ============================================================
# TPEx day-trading HTML fallback
# ============================================================

def fetch_tpex_daytrade_html(
    date_obj: datetime,
) -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    roc = roc_date_slash(
        date_obj
    )

    url = (
        f"{TPEX_BASE}/"
        "web/stock/aftertrading/"
        "daily_trading_stat.php"
    )

    params = {
        "l": "zh-tw",
        "d": roc,
    }

    response = get_response(
        url,
        params,
    )

    if response is None:
        return {}, None, False

    parser = TableParser()

    try:
        parser.feed(
            response.text
        )
    except Exception:
        return {}, None, False

    rows = parser.rows

    if not rows:
        return {}, None, False

    code_index = None

    volume_index = None

    header_index = None

    for index, row in enumerate(rows):

        normalized = [
            normalize_field_name(
                value
            )
            for value in row
        ]

        current_code = None
        current_volume = None

        for i, value in enumerate(
            normalized
        ):

            if (
                "證券代號" in value
                or "股票代號" in value
            ):
                current_code = i

            if (
                "當日沖銷交易成交股數"
                in value
                or "當日沖銷成交股數"
                in value
            ):
                current_volume = i

        if (
            current_code is not None
            and current_volume is not None
        ):

            header_index = index
            code_index = current_code
            volume_index = current_volume
            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}, None, False

    result: Dict[str, float] = {}

    for row in rows[
        header_index + 1:
    ]:

        if (
            code_index >= len(row)
            or volume_index >= len(row)
        ):
            continue

        code = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            code
        ):
            continue

        volume = safe_number(
            row[volume_index]
        )

        if (
            volume is None
            or volume < 0
        ):
            continue

        result[code] = round(
            volume,
            2,
        )

    return (
        result,
        date_obj.strftime(
            "%Y-%m-%d"
        ),
        bool(result),
    )


# ============================================================
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_obj: datetime,
) -> Dict[str, float]:

    date_str = yyyymmdd(
        date_obj
    )

    url = (
        f"{TWSE_WEB_BASE}/"
        "rwd/zh/afterTrading/"
        "MI_INDEX"
    )

    params = {
        "response": "json",
        "date": date_str,
        "type": "ALLBUT0999",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[str, float] = {}

    if not isinstance(
        data,
        dict,
    ):
        return result

    tables = extract_json_tables(
        data
    )

    for fields, rows in tables:

        code_index = find_column_exact(
            fields,
            [
                "證券代號",
                "股票代號",
            ],
        )

        volume_index = find_column_contains(
            fields,
            [
                "成交股數",
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):
            continue

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            if (
                code_index >= len(row)
                or volume_index >= len(row)
            ):
                continue

            code = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                code
            ):
                continue

            volume = safe_number(
                row[volume_index]
            )

            if (
                volume is None
                or volume < 0
            ):
                continue

            result[code] = round(
                volume,
                2,
            )

        if result:
            break

    return result


# ============================================================
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume(
    date_obj: datetime,
) -> Dict[str, float]:

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_mainboard_daily_close_quotes"
    )

    data = get_json(
        url
    )

    result: Dict[str, float] = {}

    if not isinstance(
        data,
        list,
    ):
        return result

    target_date = date_obj.strftime(
        "%Y-%m-%d"
    )

    for row in data:

        if not isinstance(
            row,
            dict,
        ):
            continue

        code = get_dict_code(
            row
        )

        if not is_valid_symbol(
            code
        ):
            continue

        row_date = get_dict_date(
            row
        )

        if (
            row_date is not None
            and row_date != target_date
        ):
            continue

        volume_key = dict_field_name(
            row,
            [
                "成交股數",
                "TradingShares",
                "Volume",
                "TotalVolume",
            ],
            [
                "成交股數",
            ],
        )

        if volume_key is None:
            continue

        volume = safe_number(
            row.get(
                volume_key
            )
        )

        if (
            volume is None
            or volume < 0
        ):
            continue

        result[code] = round(
            volume,
            2,
        )

    return result


# ============================================================
# Day-trading combined fetch
# ============================================================

def fetch_daytrade_data(
    data_date: str,
    securities: List[
        Dict[str, str]
    ],
) -> Tuple[
    Dict[str, float],
    Dict[str, float],
    Dict[str, Any],
]:

    section(
        "A：當沖資料鏈"
    )

    date_obj = datetime.strptime(
        data_date,
        "%Y-%m-%d",
    )

    twse_daytrade, twse_date, twse_ok = (
        fetch_twse_daytrade_openapi()
    )

    log(
        f"TWSE OpenAPI："
        f"{len(twse_daytrade)} 檔"
    )

    if not twse_daytrade:

        twse_daytrade, twse_date, twse_ok = (
            fetch_twse_daytrade_html(
                date_obj
            )
        )

        log(
            f"TWSE HTML fallback："
            f"{len(twse_daytrade)} 檔"
        )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_daytrade, tpex_date, tpex_ok = (
        fetch_tpex_daytrade_openapi()
    )

    log(
        f"TPEx OpenAPI："
        f"{len(tpex_daytrade)} 檔"
    )

    if not tpex_daytrade:

        tpex_daytrade, tpex_date, tpex_ok = (
            fetch_tpex_daytrade_html(
                date_obj
            )
        )

        log(
            f"TPEx HTML fallback："
            f"{len(tpex_daytrade)} 檔"
        )

    time.sleep(
        REQUEST_SLEEP
    )

    twse_volume = (
        fetch_twse_total_volume(
            date_obj
        )
    )

    log(
        f"TWSE 總成交量："
        f"{len(twse_volume)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_volume = (
        fetch_tpex_total_volume(
            date_obj
        )
    )

    log(
        f"TPEx 總成交量："
        f"{len(tpex_volume)} 檔"
    )

    daytrade: Dict[
        str,
        float
    ] = {}

    total_volume: Dict[
        str,
        float
    ] = {}

    daytrade.update(
        twse_daytrade
    )

    daytrade.update(
        tpex_daytrade
    )

    total_volume.update(
        twse_volume
    )

    total_volume.update(
        tpex_volume
    )

    universe_symbols = {
        item["symbol"]
        for item in securities
    }

    daytrade = {
        code: value
        for code, value in daytrade.items()
        if code in universe_symbols
    }

    total_volume = {
        code: value
        for code, value in total_volume.items()
        if code in universe_symbols
    }

    diagnostics = {
        "twse_daytrade_count":
            len(twse_daytrade),

        "tpex_daytrade_count":
            len(tpex_daytrade),

        "twse_total_volume_count":
            len(twse_volume),

        "tpex_total_volume_count":
            len(tpex_volume),

        "daytrade_count":
            len(daytrade),

        "total_volume_count":
            len(total_volume),

        "twse_daytrade_source":
            (
                "openapi"
                if twse_date == data_date
                and twse_ok
                else "html"
                if twse_ok
                else "failed"
            ),

        "tpex_daytrade_source":
            (
                "openapi"
                if tpex_date == data_date
                and tpex_ok
                else "html"
                if tpex_ok
                else "failed"
            ),
    }

    return (
        daytrade,
        total_volume,
        diagnostics,
    )


# ============================================================
# Period calculation
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
# Forbidden fields
# ============================================================

FORBIDDEN_FIELDS = {
    "main_force_1d",
    "main_force_5d",
    "main_force_10d",
    "main_force_20d",
}


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

        for field in FORBIDDEN_FIELDS:

            if field in item:

                log(
                    f"❌ "
                    f"{symbol}.{field} "
                    f"禁止存在"
                )

                errors += 1

    return errors == 0


# ============================================================
# Build chip
# ============================================================

def build_chip(
    securities: List[
        Dict[str, str]
    ],
    history: Dict[
        str,
        List[float]
    ],
    data_date: str,
    daytrade: Dict[str, float],
    total_volume: Dict[str, float],
) -> Tuple[
    Dict[
        str,
        Dict[str, Any]
    ],
    Dict[str, int],
]:

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    complete_1d = 0
    complete_5d = 0
    complete_10d = 0
    complete_20d = 0
    daytrade_count = 0
    rate_count = 0
    invalid_rate_count = 0
    over_volume_count = 0
    insufficient = 0

    for item in securities:

        symbol = item["symbol"]

        values = history.get(
            symbol,
            [],
        )

        inst_1d = (
            values[0]
            if len(values) >= 1
            else None
        )

        inst_5d = period_sum(
            values,
            5,
        )

        inst_10d = period_sum(
            values,
            10,
        )

        inst_20d = period_sum(
            values,
            20,
        )

        if inst_1d is not None:
            complete_1d += 1

        if inst_5d is not None:
            complete_5d += 1

        if inst_10d is not None:
            complete_10d += 1

        if inst_20d is not None:
            complete_20d += 1

        if not values:
            insufficient += 1

        dt_volume = daytrade.get(
            symbol
        )

        total = total_volume.get(
            symbol
        )

        dt_rate = None

        if dt_volume is not None:

            daytrade_count += 1

            if (
                total is not None
                and total > 0
            ):

                if dt_volume <= total:

                    dt_rate = round(
                        (
                            dt_volume
                            / total
                        ) * 100.0,
                        2,
                    )

                    if (
                        0 <= dt_rate <= 100
                    ):

                        rate_count += 1

                    else:

                        dt_rate = None
                        invalid_rate_count += 1

                else:

                    over_volume_count += 1

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
                inst_1d,

            "institutional_5d":
                inst_5d,

            "institutional_10d":
                inst_10d,

            "institutional_20d":
                inst_20d,

            "day_trading_volume":
                dt_volume,

            "total_volume":
                total,

            "day_trading_rate":
                dt_rate,

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

        "daytrade_count":
            daytrade_count,

        "daytrade_rate_count":
            rate_count,

        "invalid_daytrade_rate":
            invalid_rate_count,

        "daytrade_over_total":
            over_volume_count,

        "insufficient":
            insufficient,
    }

    return (
        stocks,
        statistics,
    )


# ============================================================
# Structure validation
# ============================================================

def validate_structure(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    section(
        "Chip 結構驗證"
    )

    required_fields = {
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

            log(
                f"❌ {symbol} "
                f"不是 object"
            )

            continue

        missing = (
            required_fields
            - set(item.keys())
        )

        if missing:

            errors += len(missing)

            log(
                f"❌ {symbol} "
                f"缺欄位："
                f"{sorted(missing)}"
            )

        if clean_code(
            item.get(
                "symbol",
                "",
            )
        ) != symbol:

            errors += 1

            log(
                f"❌ {symbol} "
                f"symbol 錯誤"
            )

        if not clean_name(
            item.get(
                "name",
                "",
            )
        ):

            errors += 1

            log(
                f"❌ {symbol} "
                f"name 為空"
            )

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            errors += 1

            log(
                f"❌ {symbol} "
                f"market 無效"
            )

        if item.get(
            "type"
        ) not in {
            "Stock",
            "ETF",
        }:

            errors += 1

            log(
                f"❌ {symbol} "
                f"type 無效"
            )

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            if not (
                isinstance(
                    rate,
                    (int, float),
                )
                and math.isfinite(
                    float(rate)
                )
                and 0 <= rate <= 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率超出 0~100："
                    f"{rate}"
                )

        dt_volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        if (
            dt_volume is not None
            and total_volume is not None
        ):

            if dt_volume > total_volume:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖量大於總成交量："
                    f"{dt_volume} > "
                    f"{total_volume}"
                )

    if not scan_forbidden_fields(
        stocks
    ):

        errors += 1

    if errors:

        log(
            f"❌ 結構驗證失敗："
            f"{errors} 個錯誤"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔 "
        f"結構驗證通過"
    )

    return True


# ============================================================
# Universe count verification
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
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ Universe "
            f"重新讀取失敗："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        return True

    stocks = data.get(
        "stocks"
    )

    if isinstance(
        stocks,
        dict,
    ):

        expected = len(stocks)

        if len(securities) != expected:

            log(
                "❌ Universe / "
                "fetch_chip 數量不一致"
            )

            log(
                f"   Universe："
                f"{expected}"
            )

            log(
                f"   fetch_chip："
                f"{len(securities)}"
            )

            return False

    raw_count = data.get(
        "universe_count"
    )

    if raw_count is not None:

        try:

            expected = int(
                raw_count
            )

        except Exception:

            return False

        if len(securities) != expected:

            log(
                "❌ universe_count / "
                "fetch_chip 不一致"
            )

            return False

    return True


# ============================================================
# Atomic write
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
        ) as f:

            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.flush()

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
# Post-write verification
# ============================================================

def verify_written_chip(
    expected_count: int,
) -> bool:

    section(
        "寫入後重新驗證 chip.json"
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
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ chip.json "
            f"JSON 錯誤："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ chip.json "
            "根節點不是 object"
        )

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ chip.json "
            "stocks 不是 object"
        )

        return False

    if len(stocks) != expected_count:

        log(
            "❌ chip.json "
            "數量錯誤"
        )

        log(
            f"   預期："
            f"{expected_count}"
        )

        log(
            f"   實際："
            f"{len(stocks)}"
        )

        return False

    if not scan_forbidden_fields(
        stocks
    ):

        return False

    for symbol, item in stocks.items():

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

            log(
                f"❌ {symbol} "
                f"寫入後 symbol 錯誤"
            )

            return False

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            if not (
                isinstance(
                    rate,
                    (int, float),
                )
                and 0 <= rate <= 100
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率錯誤"
                )

                return False

    log(
        f"✓ chip.json "
        f"寫入後："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 禁止欄位掃描通過"
    )

    log(
        "✓ 當沖率範圍驗證通過"
    )

    return True


# ============================================================
# Main
# ============================================================

def main() -> int:

    start = time.time()

    section(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log(
        f"開始時間："
        f"{now_taiwan().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("")
    log("資料架構：")
    log(
        "  Universe："
        "Data/universe.json"
    )
    log(
        "  Output："
        "Data/chip.json"
    )
    log(
        "  三大法人："
        "TWSE + TPEx"
    )
    log(
        "  期間："
        "1D / 5D / 10D / 20D"
    )
    log(
        "  當沖："
        "TWSE / TPEx 官方資料"
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
    # 2. Institutional history
    # ========================================================

    data_date, history = fetch_history(
        HISTORY_DAYS
    )

    if not data_date:

        log(
            "❌ 完全無法取得 "
            "法人歷史資料"
        )

        log(
            "❌ 為避免覆蓋既有 "
            "chip.json，本次停止"
        )

        return 1

    # ========================================================
    # 3. Day trading
    # ========================================================

    (
        daytrade,
        total_volume,
        daytrade_diagnostics,
    ) = fetch_daytrade_data(
        data_date,
        securities,
    )

    # ========================================================
    # 4. Build
    # ========================================================

    section(
        "建立全市場 Chip"
    )

    stocks, statistics = build_chip(
        securities,
        history,
        data_date,
        daytrade,
        total_volume,
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
    # 7. Output metadata
    # ========================================================

    stock_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "Stock"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if item["type"] == "ETF"
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

        "twse_count":
            twse_count,

        "tpex_count":
            tpex_count,

        "statistics":
            statistics,

        "daytrade_diagnostics":
            daytrade_diagnostics,

        "stocks":
            stocks,
    }

    # ========================================================
    # 8. Final pre-write validation
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

    # ========================================================
    # 9. Write
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
    # 10. Post-write verification
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 11. Final report
    # ========================================================

    elapsed = (
        time.time()
        - start
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
        f"✓ TWSE："
        f"{twse_count} 檔"
    )

    log(
        f"✓ TPEx："
        f"{tpex_count} 檔"
    )

    log("")
    log("三大法人資料完整度：")

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

    log("")
    log("當沖資料：")

    log(
        f"  當沖成交量："
        f"{statistics['daytrade_count']}"
    )

    log(
        f"  成功計算當沖率："
        f"{statistics['daytrade_rate_count']}"
    )

    log(
        f"  當沖率異常："
        f"{statistics['invalid_daytrade_rate']}"
    )

    log(
        f"  當沖量 > 總成交量："
        f"{statistics['daytrade_over_total']}"
    )

    log(
        f"  TWSE 當沖來源："
        f"{daytrade_diagnostics['twse_daytrade_source']}"
    )

    log(
        f"  TPEx 當沖來源："
        f"{daytrade_diagnostics['tpex_daytrade_source']}"
    )

    log("")
    log("欄位政策：")

    log("  ✓ institutional_1d")
    log("  ✓ institutional_5d")
    log("  ✓ institutional_10d")
    log("  ✓ institutional_20d")
    log("  ✓ day_trading_volume")
    log("  ✓ total_volume")
    log("  ✓ day_trading_rate")

    log("  ✗ main_force_1d")
    log("  ✗ main_force_5d")
    log("  ✗ main_force_10d")
    log("  ✗ main_force_20d")

    log("")
    log("=" * 72)
    log("CHIP BUILD PASS")
    log("=" * 72)

    log(
        f"✓ fetch_chip.py {VERSION}"
    )

    log(
        f"✓ 全市場 "
        f"{len(stocks)} 檔"
    )

    log(
        f"✓ 耗時："
        f"{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )