#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V11.0.0

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

============================================================
V11.0.0
============================================================

本版本只修正 A：當沖資料鏈。

維持不變：
    Universe
    Universe 股票池
    Universe 分類
    三大法人 1D
    三大法人 5D
    三大法人 10D
    三大法人 20D
    main_force_* 禁止政策

============================================================
A-1 TWSE
============================================================

優先：

    TWSE OpenAPI
    /v1/exchangeReport/TWTB4U

Fallback：

    TWSE Web API
    /exchangeReport/TWTB4U
    response=html

不再依賴：

    /rwd/zh/afterTrading/TWTB4U
    response=json

============================================================
A-2 TPEx
============================================================

優先：

    TPEx OpenAPI
    /openapi/v1/tpex_intraday_trading_statistics

Fallback：

    TPEx 官方現股當沖統計 HTML

不再使用：

    3itrade_hedge_result.php
    作為當沖資料來源

============================================================
資料政策
============================================================

1. Universe 是唯一股票池
2. 全市場處理
3. 單一股票缺資料 = None
4. 不用 0 冒充缺資料
5. 不使用固定 index 猜欄位
6. 不取第一個數字
7. 不取最後一個數字
8. 不使用其他欄位冒充當沖成交股數
9. 當沖率 = 當沖成交股數 / 總成交股數 * 100
10. 當沖率必須 0~100
11. 當沖成交股數不得大於總成交股數
12. Universe / Chip 數量必須一致
13. 寫入前驗證
14. Atomic Write
15. 寫入後驗證
16. 官方資料整批完全失敗才停止

============================================================
重要定義
============================================================

institutional_*：

    三大法人買賣超

不是：

    主力買賣超

禁止：

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

VERSION = "V11.0.0"


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

    print(
        message,
        flush=True,
    )


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


def yyyymmdd(
    date_obj: datetime,
) -> str:

    return date_obj.strftime(
        "%Y%m%d"
    )


def roc_date_slash(
    date_obj: datetime,
) -> str:

    roc_year = (
        date_obj.year - 1911
    )

    return (
        f"{roc_year:03d}/"
        f"{date_obj.month:02d}/"
        f"{date_obj.day:02d}"
    )


def roc_date_compact(
    date_obj: datetime,
) -> str:

    roc_year = (
        date_obj.year - 1911
    )

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

    text = str(
        value
    ).strip()

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

    # --------------------------------------------------------
    # YYYYMMDD
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{8}",
        text,
    ):

        try:

            return datetime.strptime(
                text,
                "%Y%m%d",
            ).strftime(
                "%Y-%m-%d"
            )

        except Exception:

            return None

    # --------------------------------------------------------
    # ROC compact YYYMMDD
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{7}",
        text,
    ):

        try:

            year = (
                int(text[:3])
                + 1911
            )

            month = int(
                text[3:5]
            )

            day = int(
                text[5:7]
            )

            return datetime(
                year,
                month,
                day,
            ).strftime(
                "%Y-%m-%d"
            )

        except Exception:

            return None

    # --------------------------------------------------------
    # YYYY/MM/DD or YYY/MM/DD
    # --------------------------------------------------------

    parts = text.split(
        "/"
    )

    if len(parts) != 3:

        return None

    try:

        year = int(
            parts[0]
        )

        month = int(
            parts[1]
        )

        day = int(
            parts[2]
        )

        if year < 1911:

            year += 1911

        return datetime(
            year,
            month,
            day,
        ).strftime(
            "%Y-%m-%d"
        )

    except Exception:

        return None


# ============================================================
# Basic helpers
# ============================================================

def clean_code(
    value: Any,
) -> str:

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


def clean_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    return str(
        value
    ).strip()


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

    text = str(
        value
    ).strip()

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

        number = float(
            text
        )

        if not math.isfinite(
            number
        ):

            return None

        return number

    except Exception:

        return None


def normalize_field_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    text = str(
        value
    )

    text = re.sub(
        r"[\s　\r\n\t]+",
        "",
        text,
    )

    return text.strip()


def is_valid_symbol(
    code: str,
) -> bool:

    code = clean_code(
        code
    )

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
# JSON table helpers
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

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if (
        isinstance(
            fields,
            list,
        )
        and isinstance(
            rows,
            list,
        )
    ):

        tables.append(
            (
                fields,
                rows,
            )
        )

    raw_tables = data.get(
        "tables"
    )

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
        normalize_field_name(
            name
        )
        for name in names
    }

    for index, field in enumerate(
        fields
    ):

        normalized = (
            normalize_field_name(
                field
            )
        )

        if normalized in wanted:

            return index

    return None


def find_column_contains(
    fields: List[Any],
    keywords: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(
            field
        )
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


def find_code_column(
    fields: List[Any],
) -> Optional[int]:

    return find_column_exact(
        fields,
        [
            "證券代號",
            "股票代號",
            "代號",
            "SecuritiesCompanyCode",
            "Code",
        ],
    )


# ============================================================
# Dict field helpers
# ============================================================

def dict_field_name(
    row: Dict[str, Any],
    exact: List[str],
    contains: Optional[List[str]] = None,
) -> Optional[str]:

    normalized = {
        normalize_field_name(
            key
        ): key
        for key in row.keys()
    }

    for name in exact:

        key = normalize_field_name(
            name
        )

        if key in normalized:

            return normalized[key]

    if contains:

        for keyword in contains:

            key_word = normalize_field_name(
                keyword
            )

            for normalized_key, original_key in normalized.items():

                if key_word in normalized_key:

                    return original_key

    return None


def get_dict_code(
    row: Dict[str, Any],
) -> str:

    key = dict_field_name(
        row,
        [
            "Code",
            "SecuritiesCompanyCode",
            "StockCode",
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

class TableParser(
    HTMLParser
):

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
            tag in {
                "td",
                "th",
            }
            and self.current_row
            is not None
        ):

            self.current_cell = []

    def handle_data(
        self,
        data,
    ):

        if (
            self.current_cell
            is not None
        ):

            self.current_cell.append(
                data
            )

    def handle_endtag(
        self,
        tag,
    ):

        tag = tag.lower()

        if (
            tag in {
                "td",
                "th",
            }
            and self.current_row
            is not None
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
    Dict[str, Any]
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

            data = json.load(
                f
            )

    except Exception as exc:

        log(
            f"❌ Universe JSON "
            f"解析失敗：{exc}"
        )

        return []

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ Universe 根節點不是 object"
        )

        return []

    declared_count = None

    if data.get(
        "universe_count"
    ) is not None:

        try:

            declared_count = int(
                data.get(
                    "universe_count"
                )
            )

        except Exception:

            log(
                "❌ universe_count "
                "無法轉成整數"
            )

            return []

    stocks = data.get(
        "stocks"
    )

    items: List[
        Dict[str, Any]
    ] = []

    if isinstance(
        stocks,
        dict,
    ):

        log(
            f"✓ 偵測 stocks object："
            f"{len(stocks)} 檔"
        )

        if (
            declared_count is not None
            and declared_count
            != len(stocks)
        ):

            log(
                "❌ Universe 數量矛盾"
            )

            return []

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

            item = dict(
                value
            )

            item["symbol"] = (
                clean_code(key)
            )

            items.append(
                item
            )

    else:

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
                if isinstance(
                    x,
                    dict,
                )
            ]

    if not items:

        log(
            "❌ Universe 沒有可用資料"
        )

        return []

    securities = []

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

        seen.add(
            symbol
        )

        name = clean_name(
            item.get(
                "name",
                "",
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

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            if (
                original_symbol.endswith(
                    ".TWO"
                )
                or original_symbol.endswith(
                    "TWO"
                )
            ):

                market = "TPEX"

            elif (
                original_symbol.endswith(
                    ".TW"
                )
                or original_symbol.endswith(
                    "TW"
                )
            ):

                market = "TWSE"

            elif symbol.startswith(
                "3"
            ):

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

            if (
                re.fullmatch(
                    r"\d{4,6}[A-Z0-9]{1,2}",
                    symbol,
                )
                and not re.fullmatch(
                    r"\d{4,6}",
                    symbol,
                )
            ):

                sec_type = "ETF"

            else:

                sec_type = "Stock"

        if not full_symbol:

            if market == "TPEX":

                full_symbol = (
                    f"{symbol}.TWO"
                )

            else:

                full_symbol = (
                    f"{symbol}.TW"
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
        f"  原始標的："
        f"{len(items)}"
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

    if (
        len(securities)
        != len(items)
    ):

        log(
            "❌ Universe "
            "解析後數量不一致"
        )

        return []

    if (
        declared_count is not None
        and len(securities)
        != declared_count
    ):

        log(
            "❌ universe_count "
            "與實際載入數量不一致"
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

    log("")

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log(
        f"✓ Stock："
        f"{stock_count}"
    )

    log(
        f"✓ ETF："
        f"{etf_count}"
    )

    log(
        f"✓ TWSE："
        f"{twse_count}"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count}"
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

    result = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    if data.get(
        "stat"
    ) != "OK":

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

        symbol = clean_code(
            row[0]
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        net = safe_number(
            row[18]
        )

        if net is None:

            continue

        result[symbol] = round(
            net / 1000.0,
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

        numeric_values = []

        for value in row[1:]:

            number = safe_number(
                value
            )

            if number is not None:

                numeric_values.append(
                    number
                )

        if not numeric_values:

            continue

        net = numeric_values[-1]

        result[code] = round(
            net / 1000.0,
            2,
        )

    return result


# ============================================================
# Daily institutional
# ============================================================

def fetch_daily_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    twse = fetch_twse_institutional(
        yyyymmdd(
            date_obj
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_institutional(
        date_obj
    )

    result = dict(
        twse
    )

    for symbol, value in tpex.items():

        result[symbol] = value

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
        f"同步最近 {days} 個交易日三大法人資料"
    )

    history = {}

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
                f"["
                f"{successful_days + 1}/"
                f"{days}"
                f"] "
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

                    history[
                        symbol
                    ].append(
                        value
                    )

                log(
                    f"      ✓ "
                    f"TWSE/TPEx："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ "
                    "本日無可用法人資料"
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
        f"{successful_days} 個交易日"
    )

    log(
        f"✓ 最新資料日："
        f"{latest_date}"
    )

    log(
        f"✓ 有歷史籌碼資料："
        f"{len(history)} 檔"
    )

    return (
        latest_date,
        history,
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
# TWSE day-trading OpenAPI
# ============================================================

def fetch_twse_daytrade_openapi()
    -> Tuple[
        Dict[str, float],
        Optional[str],
        bool,
    ]:

    """
    TWSE 官方 OpenAPI：

        /v1/exchangeReport/TWTB4U

    官方定義：
        上市股票每日當日沖銷交易標的及統計

    回傳：

        data
        data_date
        source_success

    注意：
        不依賴固定欄位 index。
    """

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

    result = {}

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
                "DayTradingShares",
                "IntradayTradingShares",
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

    data_date = None

    if len(dates) == 1:

        data_date = next(
            iter(dates)
        )

    elif len(dates) > 1:

        # 官方資料不應該混合多日期。
        # 保守使用最新日期。
        data_date = max(
            dates
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

    """
    TWSE 官方 HTML fallback。

    使用：
        /exchangeReport/TWTB4U

    response=html

    不使用：
        /rwd/... response=json
    """

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

    target_date = (
        date_obj.strftime(
            "%Y-%m-%d"
        )
    )

    page_date = None

    for row in rows[:20]:

        for value in row:

            parsed = normalize_date_text(
                value
            )

            if parsed:

                page_date = parsed

                break

        if page_date:

            break

    header_index = None

    code_index = None

    volume_index = None

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        has_code = any(
            (
                "證券代號" in value
                or "股票代號" in value
            )
            for value in normalized
        )

        has_volume = any(
            (
                "當日沖銷交易成交股數"
                in value
                or "當日沖銷成交股數"
                in value
            )
            for value in normalized
        )

        if (
            has_code
            and has_volume
        ):

            header_index = index

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

            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}, page_date, False

    if (
        page_date is not None
        and page_date != target_date
    ):

        return {}, page_date, False

    result = {}

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
        page_date,
        True,
    )


# ============================================================
# TWSE day-trading wrapper
# ============================================================

def fetch_twse_daytrade(
    date_obj: datetime,
) -> Tuple[
    Dict[str, float],
    Optional[str],
    str,
]:

    section(
        "TWSE 當沖資料"
    )

    data, data_date, success = (
        fetch_twse_daytrade_openapi()
    )

    if success:

        log(
            f"  ✓ TWSE OpenAPI："
            f"{len(data)} 檔"
        )

        log(
            f"  ✓ 資料日期："
            f"{data_date or 'API 未提供'}"
        )

        if data:

            return (
                data,
                data_date,
                "TWSE OpenAPI",
            )

    log(
        "  ⚠️ TWSE OpenAPI "
        "沒有取得有效當沖資料"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    data, data_date, success = (
        fetch_twse_daytrade_html(
            date_obj
        )
    )

    if success:

        log(
            f"  ✓ TWSE HTML fallback："
            f"{len(data)} 檔"
        )

        log(
            f"  ✓ 資料日期："
            f"{data_date or 'HTML 未提供'}"
        )

        if data:

            return (
                data,
                data_date,
                "TWSE HTML fallback",
            )

    log(
        "  ❌ TWSE 當沖資料取得失敗"
    )

    return (
        {},
        None,
        "TWSE FAILED",
    )


# ============================================================
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_str: str,
) -> Dict[str, float]:

    url = (
        f"{TWSE_WEB_BASE}/"
        "rwd/zh/afterTrading/MI_INDEX"
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

    result = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    tables = extract_json_tables(
        data
    )

    for fields, rows in tables:

        code_index = find_code_column(
            fields
        )

        volume_index = (
            find_column_contains(
                fields,
                [
                    "成交股數",
                    "成交量",
                ],
            )
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

    return result


# ============================================================
# TPEx day-trading OpenAPI
# ============================================================

def fetch_tpex_daytrade_openapi()
    -> Tuple[
        Dict[str, float],
        Optional[str],
        bool,
    ]:

    """
    TPEx 官方 OpenAPI：

        /openapi/v1/tpex_intraday_trading_statistics

    官方名稱：

        上櫃股票現股當沖交易統計資訊

    動態尋找：
        股票代號
        資料日期
        當日沖銷成交股數
    """

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

    result = {}

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
                "IntradayTradingShares",
                "DayTradingShares",
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

    data_date = None

    if len(dates) == 1:

        data_date = next(
            iter(dates)
        )

    elif len(dates) > 1:

        data_date = max(
            dates
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

    """
    TPEx 官方現股當沖統計頁面 fallback。

    目前頁面：

        /zh-tw/mainboard/trading/
        day-trading/statistics/day.html

    注意：

        TPEx 官方頁面的資料可能由前端載入，
        因此 HTML fallback 只在 server-rendered
        table 存在時使用。
    """

    url = (
        f"{TPEX_BASE}/"
        "zh-tw/mainboard/trading/"
        "day-trading/statistics/day.html"
    )

    params = {
        "date": (
            f"{date_obj.year}/"
            f"{date_obj.month:02d}/"
            f"{date_obj.day:02d}"
        )
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

    target_date = (
        date_obj.strftime(
            "%Y-%m-%d"
        )
    )

    page_date = None

    for row in rows[:20]:

        for value in row:

            parsed = normalize_date_text(
                value
            )

            if parsed:

                page_date = parsed

                break

        if page_date:

            break

    header_index = None

    code_index = None

    volume_index = None

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        has_code = any(
            (
                "證券代號" in value
                or "股票代號" in value
            )
            for value in normalized
        )

        has_volume = any(
            (
                "當日沖銷交易成交股數"
                in value
                or "當日沖銷成交股數"
                in value
            )
            for value in normalized
        )

        if (
            has_code
            and has_volume
        ):

            header_index = index

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

            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}, page_date, False

    if (
        page_date is not None
        and page_date != target_date
    ):

        return {}, page_date, False

    result = {}

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
        page_date,
        True,
    )


# ============================================================
# TPEx day-trading wrapper
# ============================================================

def fetch_tpex_daytrade(
    date_obj: datetime,
) -> Tuple[
    Dict[str, float],
    Optional[str],
    str,
]:

    section(
        "TPEx 當沖資料"
    )

    data, data_date, success = (
        fetch_tpex_daytrade_openapi()
    )

    if success:

        log(
            f"  ✓ TPEx OpenAPI："
            f"{len(data)} 檔"
        )

        log(
            f"  ✓ 資料日期："
            f"{data_date or 'API 未提供'}"
        )

        if data:

            return (
                data,
                data_date,
                "TPEx OpenAPI",
            )

    log(
        "  ⚠️ TPEx OpenAPI "
        "沒有取得有效當沖資料"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    data, data_date, success = (
        fetch_tpex_daytrade_html(
            date_obj
        )
    )

    if success:

        log(
            f"  ✓ TPEx HTML fallback："
            f"{len(data)} 檔"
        )

        log(
            f"  ✓ 資料日期："
            f"{data_date or 'HTML 未提供'}"
        )

        if data:

            return (
                data,
                data_date,
                "TPEx HTML fallback",
            )

    log(
        "  ❌ TPEx 當沖資料取得失敗"
    )

    return (
        {},
        None,
        "TPEx FAILED",
    )


# ============================================================
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume() -> Dict[str, float]:

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_mainboard_daily_close_quotes"
    )

    data = get_json(
        url
    )

    result = {}

    if not isinstance(
        data,
        list,
    ):

        return result

    for row in data:

        if not isinstance(
            row,
            dict,
        ):

            continue

        code = clean_code(
            row.get(
                "SecuritiesCompanyCode"
            )
        )

        if not is_valid_symbol(
            code
        ):

            continue

        volume = safe_number(
            row.get(
                "TradingShares"
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
# A: Fetch complete day-trading chain
# ============================================================

def fetch_daytrade_chain(
    data_date: str,
) -> Tuple[
    Dict[str, float],
    Dict[str, float],
    Dict[str, float],
    Dict[str, float],
    Dict[str, Any],
]:

    section(
        "A：同步當沖資料鏈"
    )

    try:

        date_obj = datetime.strptime(
            data_date,
            "%Y-%m-%d",
        )

    except Exception:

        log(
            f"❌ 無效資料日期："
            f"{data_date}"
        )

        return (
            {},
            {},
            {},
            {},
            {},
        )

    twse_daytrade, twse_daytrade_date, twse_source = (
        fetch_twse_daytrade(
            date_obj
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    log("")
    log(
        "TWSE 總成交量："
    )

    twse_volume = (
        fetch_twse_total_volume(
            yyyymmdd(
                date_obj
            )
        )
    )

    log(
        f"  ✓ TWSE 總成交量："
        f"{len(twse_volume)} 檔"
    )

    tpex_daytrade, tpex_daytrade_date, tpex_source = (
        fetch_tpex_daytrade(
            date_obj
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    log("")
    log(
        "TPEx 總成交量："
    )

    tpex_volume = (
        fetch_tpex_total_volume()
    )

    log(
        f"  ✓ TPEx 總成交量："
        f"{len(tpex_volume)} 檔"
    )

    diagnostics = {

        "target_date":
            data_date,

        "twse_daytrade_date":
            twse_daytrade_date,

        "tpex_daytrade_date":
            tpex_daytrade_date,

        "twse_daytrade_source":
            twse_source,

        "tpex_daytrade_source":
            tpex_source,

        "twse_daytrade_count":
            len(twse_daytrade),

        "twse_total_volume_count":
            len(twse_volume),

        "tpex_daytrade_count":
            len(tpex_daytrade),

        "tpex_total_volume_count":
            len(tpex_volume),
    }

    return (
        twse_daytrade,
        twse_volume,
        tpex_daytrade,
        tpex_volume,
        diagnostics,
    )


# ============================================================
# Day-trading rate
# ============================================================

def calculate_daytrade_rate(
    daytrade_volume: Optional[float],
    total_volume: Optional[float],
) -> Optional[float]:

    if (
        daytrade_volume is None
        or total_volume is None
    ):

        return None

    if (
        daytrade_volume < 0
        or total_volume <= 0
    ):

        return None

    if daytrade_volume > total_volume:

        return None

    rate = (
        daytrade_volume
        /
        total_volume
        *
        100.0
    )

    if not math.isfinite(
        rate
    ):

        return None

    if (
        rate < 0
        or rate > 100
    ):

        return None

    return round(
        rate,
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
        Dict[str, Any],
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
        Dict[str, Any]
    ],
    history: Dict[
        str,
        List[float],
    ],
    data_date: str,
    twse_daytrade: Dict[str, float],
    twse_volume: Dict[str, float],
    tpex_daytrade: Dict[str, float],
    tpex_volume: Dict[str, float],
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int],
]:

    stocks = {}

    complete_1d = 0
    complete_5d = 0
    complete_10d = 0
    complete_20d = 0

    daytrade_available = 0
    total_volume_available = 0
    rate_available = 0
    invalid_rate = 0
    insufficient = 0

    twse_daytrade_available = 0
    tpex_daytrade_available = 0

    twse_rate_available = 0
    tpex_rate_available = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

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

        if item[
            "market"
        ] == "TWSE":

            daytrade_volume = (
                twse_daytrade.get(
                    symbol
                )
            )

            total_volume = (
                twse_volume.get(
                    symbol
                )
            )

        else:

            daytrade_volume = (
                tpex_daytrade.get(
                    symbol
                )
            )

            total_volume = (
                tpex_volume.get(
                    symbol
                )
            )

        if daytrade_volume is not None:

            daytrade_available += 1

            if item[
                "market"
            ] == "TWSE":

                twse_daytrade_available += 1

            else:

                tpex_daytrade_available += 1

        if total_volume is not None:

            total_volume_available += 1

        rate = calculate_daytrade_rate(
            daytrade_volume,
            total_volume,
        )

        if rate is not None:

            rate_available += 1

            if item[
                "market"
            ] == "TWSE":

                twse_rate_available += 1

            else:

                tpex_rate_available += 1

        elif (
            daytrade_volume is not None
            and total_volume is not None
        ):

            invalid_rate += 1

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
                daytrade_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,

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

        "daytrade_available":
            daytrade_available,

        "total_volume_available":
            total_volume_available,

        "daytrade_rate_available":
            rate_available,

        "invalid_daytrade_rate":
            invalid_rate,

        "twse_daytrade_available":
            twse_daytrade_available,

        "tpex_daytrade_available":
            tpex_daytrade_available,

        "twse_rate_available":
            twse_rate_available,

        "tpex_rate_available":
            tpex_rate_available,
    }

    return (
        stocks,
        statistics,
    )


# ============================================================
# Universe verification
# ============================================================

def verify_universe_count(
    securities: List[
        Dict[str, Any]
    ],
) -> bool:

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(
                f
            )

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

        return False

    expected = None

    if data.get(
        "universe_count"
    ) is not None:

        try:

            expected = int(
                data.get(
                    "universe_count"
                )
            )

        except Exception:

            return False

    stocks = data.get(
        "stocks"
    )

    if isinstance(
        stocks,
        dict,
    ):

        actual = len(
            stocks
        )

        if (
            expected is not None
            and expected != actual
        ):

            log(
                "❌ Universe "
                "header 數量錯誤"
            )

            return False

        if len(
            securities
        ) != actual:

            log(
                "❌ fetch_chip "
                "載入數量錯誤"
            )

            log(
                f"   Universe："
                f"{actual}"
            )

            log(
                f"   fetch_chip："
                f"{len(securities)}"
            )

            return False

    if (
        expected is not None
        and len(securities)
        != expected
    ):

        log(
            "❌ Universe / "
            "fetch_chip 數量不一致"
        )

        return False

    return True


# ============================================================
# Structural validation
# ============================================================

def validate_structure(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
) -> bool:

    section(
        "全市場 Chip 結構驗證"
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
                "不是 object"
            )

            continue

        missing = (
            required_fields
            -
            set(
                item.keys()
            )
        )

        if missing:

            errors += len(
                missing
            )

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
                "symbol 錯誤"
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
                "name 為空"
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
                "market 無效"
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
                "type 無效"
            )

        daytrade = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        if daytrade is not None:

            if (
                not isinstance(
                    daytrade,
                    (
                        int,
                        float,
                    ),
                )
                or daytrade < 0
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    "當沖成交量非法"
                )

        if total is not None:

            if (
                not isinstance(
                    total,
                    (
                        int,
                        float,
                    ),
                )
                or total < 0
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    "總成交量非法"
                )

        if rate is not None:

            if (
                not isinstance(
                    rate,
                    (
                        int,
                        float,
                    ),
                )
                or rate < 0
                or rate > 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    "當沖率超出 0~100"
                )

        if (
            daytrade is not None
            and total is not None
            and daytrade > total
        ):

            errors += 1

            log(
                f"❌ {symbol} "
                "當沖量大於總成交量"
            )

    if not scan_forbidden_fields(
        stocks
    ):

        errors += 1

    if errors:

        log("")

        log(
            f"❌ 結構驗證失敗："
            f"{errors} 個錯誤"
        )

        return False

    log(
        f"✓ 全市場 "
        f"{len(stocks)} 檔"
        "結構驗證通過"
    )

    return True


# ============================================================
# A quality validation
# ============================================================

def validate_daytrade_quality(
    stocks: Dict[
        str,
        Dict[str, Any],
    ],
) -> bool:

    section(
        "A：當沖資料品質驗證"
    )

    total = len(
        stocks
    )

    daytrade_count = 0
    total_volume_count = 0
    rate_count = 0
    invalid_count = 0

    twse_daytrade_count = 0
    tpex_daytrade_count = 0

    twse_rate_count = 0
    tpex_rate_count = 0

    for symbol, item in stocks.items():

        daytrade = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        if daytrade is not None:

            daytrade_count += 1

            if item.get(
                "market"
            ) == "TWSE":

                twse_daytrade_count += 1

            elif item.get(
                "market"
            ) == "TPEX":

                tpex_daytrade_count += 1

        if total_volume is not None:

            total_volume_count += 1

        if rate is not None:

            rate_count += 1

            if item.get(
                "market"
            ) == "TWSE":

                twse_rate_count += 1

            elif item.get(
                "market"
            ) == "TPEX":

                tpex_rate_count += 1

        if (
            daytrade is not None
            and total_volume is not None
        ):

            if (
                daytrade < 0
                or total_volume <= 0
                or daytrade > total_volume
            ):

                invalid_count += 1

        if (
            rate is not None
            and (
                rate < 0
                or rate > 100
            )
        ):

            invalid_count += 1

    log(
        f"Universe："
        f"{total}"
    )

    log(
        f"當沖成交量："
        f"{daytrade_count}"
    )

    log(
        f"總成交量："
        f"{total_volume_count}"
    )

    log(
        f"有效當沖率："
        f"{rate_count}"
    )

    log(
        f"TWSE 當沖："
        f"{twse_daytrade_count}"
    )

    log(
        f"TPEx 當沖："
        f"{tpex_daytrade_count}"
    )

    log(
        f"TWSE 有效當沖率："
        f"{twse_rate_count}"
    )

    log(
        f"TPEx 有效當沖率："
        f"{tpex_rate_count}"
    )

    log(
        f"非法當沖率："
        f"{invalid_count}"
    )

    if daytrade_count == 0:

        log(
            "❌ A-1 "
            "當沖成交量為 0"
        )

        return False

    if rate_count == 0:

        log(
            "❌ A-2 "
            "有效當沖率為 0"
        )

        return False

    if invalid_count > 0:

        log(
            f"❌ A-3 "
            f"存在 {invalid_count} "
            "筆非法資料"
        )

        return False

    # 至少 TWSE / TPEx 其中一側有資料。
    # 全市場不能兩邊都完全為 0。
    if (
        twse_daytrade_count == 0
        and tpex_daytrade_count == 0
    ):

        log(
            "❌ A-4 "
            "TWSE / TPEx "
            "兩市場當沖皆為 0"
        )

        return False

    log(
        "✓ A-1 "
        "當沖成交量正常"
    )

    log(
        "✓ A-2 "
        "有效當沖率正常"
    )

    log(
        "✓ A-3 "
        "0~100 / 當沖量<=總成交量"
        "驗證通過"
    )

    log(
        "✓ A："
        "當沖資料鏈驗證通過"
    )

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

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
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

            data = json.load(
                f
            )

    except Exception as exc:

        log(
            f"❌ chip.json JSON "
            f"錯誤：{exc}"
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
            "❌ chip.json 數量錯誤"
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
                "寫入後 symbol 錯誤"
            )

            return False

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            if (
                rate < 0
                or rate > 100
            ):

                log(
                    f"❌ {symbol} "
                    "寫入後當沖率非法"
                )

                return False

        daytrade = item.get(
            "day_trading_volume"
        )

        total = item.get(
            "total_volume"
        )

        if (
            daytrade is not None
            and total is not None
            and daytrade > total
        ):

            log(
                f"❌ {symbol} "
                "寫入後當沖量大於總量"
            )

            return False

    log(
        f"✓ chip.json 寫入後："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 禁止欄位掃描通過"
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
        "正式入口："
        "Scripts/fetch_chip.py"
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
        "TWSE OpenAPI + HTML fallback"
    )

    log(
        "  當沖："
        "TPEx OpenAPI + HTML fallback"
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
    # 2. History
    # ========================================================

    data_date, history = fetch_history(
        HISTORY_DAYS
    )

    if not data_date:

        log(
            "❌ 全部交易日 API "
            "都沒有取得有效資料"
        )

        log(
            "❌ 不覆蓋既有 chip.json"
        )

        return 1

    if not history:

        log(
            "❌ history 為空"
        )

        return 1

    # ========================================================
    # 3. A day-trading chain
    # ========================================================

    (
        twse_daytrade,
        twse_volume,
        tpex_daytrade,
        tpex_volume,
        daytrade_diagnostics,
    ) = fetch_daytrade_chain(
        data_date
    )

    # ========================================================
    # 4. Build
    # ========================================================

    section(
        "建立全市場 Chip"
    )

    (
        stocks,
        statistics,
    ) = build_chip(
        securities,
        history,
        data_date,
        twse_daytrade,
        twse_volume,
        tpex_daytrade,
        tpex_volume,
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
    # 7. A quality
    # ========================================================

    if not validate_daytrade_quality(
        stocks
    ):

        log(
            "❌ A 當沖資料鏈驗證失敗"
        )

        log(
            "❌ 不覆蓋既有 chip.json"
        )

        return 1

    # ========================================================
    # 8. Output
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
    # 9. Final pre-write count
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
    # 10. Write
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
    # 11. Post verification
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 12. Final report
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
        f"{stock_count}"
    )

    log(
        f"✓ ETF："
        f"{etf_count}"
    )

    log(
        f"✓ TWSE："
        f"{twse_count}"
    )

    log(
        f"✓ TPEX："
        f"{tpex_count}"
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

    log("")

    log(
        "A：當沖資料："
    )

    log(
        f"  TWSE 當沖："
        f"{statistics['twse_daytrade_available']}"
    )

    log(
        f"  TPEx 當沖："
        f"{statistics['tpex_daytrade_available']}"
    )

    log(
        f"  當沖成交量："
        f"{statistics['daytrade_available']}"
    )

    log(
        f"  總成交量："
        f"{statistics['total_volume_available']}"
    )

    log(
        f"  有效當沖率："
        f"{statistics['daytrade_rate_available']}"
    )

    log(
        f"  非法當沖率："
        f"{statistics['invalid_daytrade_rate']}"
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
        "============================================================"
    )

    log(
        "CHIP BUILD PASS"
    )

    log(
        "============================================================"
    )

    log(
        f"✓ fetch_chip.py {VERSION}"
    )

    log(
        f"✓ 全市場 {len(stocks)} 檔"
    )

    log(
        f"✓ A 當沖資料鏈通過"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
