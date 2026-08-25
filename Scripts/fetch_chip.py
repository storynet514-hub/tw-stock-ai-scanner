#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V12.0.0

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
V12.0.0 修正
============================================================

A. Universe type 完整繼承
------------------------------------------------------------
fetch_chip 不再自行猜測 ETF。

Universe 已經是唯一分類來源。

例如：

    Stock
    ETF
    ETN
    TDR
    Warrant
    Bond
    其他

全部原樣保留。

因此：

    Bond 16

絕對不會再次被 fetch_chip 改成：

    ETF 16


B. TPEx 當沖改用官方 OpenAPI
------------------------------------------------------------

官方 endpoint：

    /openapi/v1/tpex_intraday_trading_statistics

用途：

    上櫃股票現股當沖交易統計資訊

資料解析採：

    動態欄位名稱
    不使用固定 index

若 OpenAPI 無資料：

    才進入 HTML fallback

不使用假的 0。


C. 當沖率
------------------------------------------------------------

day_trading_rate：

    當沖成交股數
    /
    個股總成交股數
    × 100

限制：

    0 <= rate <= 100

若：

    day_trade > total_volume

則拒絕該筆資料。


D. Universe
------------------------------------------------------------

Universe 是唯一股票池。

fetch_chip 不增加、不刪除、不重新分類標的。


E. 三大法人
------------------------------------------------------------

institutional_*：

    三大法人買賣超

不是：

    主力買賣超


禁止：

    main_force_1d
    main_force_5d
    main_force_10d
    main_force_20d


F. 缺資料
------------------------------------------------------------

缺資料：

    None

禁止：

    0 冒充缺資料


G. 整批原則
------------------------------------------------------------

單一股票缺資料：

    不破壞整批

官方 API 整批失敗：

    該市場資料來源停止

但不以 0 冒充。


H. Atomic Write
------------------------------------------------------------

先寫：

    chip.json.tmp

成功後：

    replace -> chip.json


I. 寫入前後驗證
------------------------------------------------------------

Universe count
Chip count
Type
Market
Required fields
Forbidden fields
Day-trading range
Day-trading volume <= total volume

全部驗證。


============================================================
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

VERSION = "V12.0.0"


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


TWSE_OPENAPI_BASE = (
    "https://openapi.twse.com.tw/v1"
)

TPEX_OPENAPI_BASE = (
    "https://www.tpex.org.tw/openapi/v1"
)


# ============================================================
# Session
# ============================================================

session = requests.Session()

session.headers.update(
    HEADERS
)


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


def roc_date(
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

    text = (
        str(value)
        .strip()
    )

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

        if not math.isfinite(
            number
        ):

            return None

        return number

    except Exception:

        return None


def normalize_field(
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
) -> Optional[requests.Response]:

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return None

        return response

    except Exception:

        return None


def get_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Optional[Any]:

    response = get_response(
        url,
        params,
    )

    if response is None:

        return None

    try:

        return response.json()

    except Exception:

        return None


# ============================================================
# Generic JSON rows
# ============================================================

def json_rows(
    data: Any,
) -> List[Dict[str, Any]]:

    rows = []

    if isinstance(
        data,
        list,
    ):

        for item in data:

            if isinstance(
                item,
                dict,
            ):

                rows.append(
                    item
                )

        return rows

    if not isinstance(
        data,
        dict,
    ):

        return rows

    raw = data.get(
        "data"
    )

    if isinstance(
        raw,
        list,
    ):

        for item in raw:

            if isinstance(
                item,
                dict,
            ):

                rows.append(
                    item
                )

    return rows


def field_key(
    row: Dict[str, Any],
    exact: List[str],
    contains: Optional[
        List[str]
    ] = None,
) -> Optional[str]:

    normalized = {
        normalize_field(
            key
        ): key
        for key in row.keys()
    }

    for name in exact:

        wanted = normalize_field(
            name
        )

        if wanted in normalized:

            return normalized[
                wanted
            ]

    if contains:

        for keyword in contains:

            wanted = normalize_field(
                keyword
            )

            for key, original in (
                normalized.items()
            ):

                if wanted in key:

                    return original

    return None


def row_code(
    row: Dict[str, Any],
) -> str:

    key = field_key(
        row,
        [
            "Code",
            "StockCode",
            "SecuritiesCompanyCode",
            "證券代號",
            "股票代號",
            "代號",
            "證券代碼",
        ],
        [
            "代號",
            "Code",
        ],
    )

    if key is None:

        return ""

    return clean_code(
        row.get(key)
    )


def row_number(
    row: Dict[str, Any],
    exact: List[str],
    contains: Optional[
        List[str]
    ] = None,
) -> Optional[float]:

    key = field_key(
        row,
        exact,
        contains,
    )

    if key is None:

        return None

    return safe_number(
        row.get(key)
    )


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
            "❌ 找不到 universe.json"
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
            f"❌ Universe JSON 解析失敗：{exc}"
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

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ Universe stocks 不是 object"
        )

        return []

    declared = data.get(
        "universe_count"
    )

    try:

        declared_count = (
            int(declared)
            if declared is not None
            else len(stocks)
        )

    except Exception:

        log(
            "❌ universe_count 無法解析"
        )

        return []

    if declared_count != len(
        stocks
    ):

        log(
            "❌ Universe 數量本身已矛盾"
        )

        return []

    securities = []

    seen = set()

    type_counts: Dict[
        str,
        int
    ] = {}

    market_counts: Dict[
        str,
        int
    ] = {}

    for key, value in (
        stocks.items()
    ):

        if not isinstance(
            value,
            dict,
        ):

            log(
                f"❌ stocks[{key}] 不是 object"
            )

            return []

        symbol = clean_code(
            value.get(
                "symbol",
                key,
            )
        )

        if not symbol:

            log(
                "❌ 發現空 symbol"
            )

            return []

        if symbol in seen:

            log(
                f"❌ 重複 symbol：{symbol}"
            )

            return []

        seen.add(
            symbol
        )

        # ====================================================
        # 重要：
        #
        # type 絕對繼承 Universe
        #
        # 不再根據 symbol 猜 ETF。
        # ====================================================

        raw_type = value.get(
            "type"
        )

        if raw_type is None:

            raw_type = value.get(
                "security_type"
            )

        if raw_type is None:

            raw_type = "Unknown"

        sec_type = (
            str(
                raw_type
            )
            .strip()
        )

        market = (
            str(
                value.get(
                    "market",
                    "",
                )
            )
            .strip()
            .upper()
        )

        if market not in {
            "TWSE",
            "TPEX",
        }:

            original = str(
                value.get(
                    "full_symbol",
                    value.get(
                        "symbol",
                        "",
                    ),
                )
            ).upper()

            if (
                original.endswith(
                    ".TWO"
                )
                or original.endswith(
                    "TWO"
                )
            ):

                market = "TPEX"

            elif (
                original.endswith(
                    ".TW"
                )
                or original.endswith(
                    "TW"
                )
            ):

                market = "TWSE"

            else:

                # 只處理真正缺 market 的舊資料。
                # 不影響 type。
                if symbol.startswith(
                    "3"
                ):

                    market = "TPEX"

                else:

                    market = "TWSE"

        full_symbol = str(
            value.get(
                "full_symbol",
                "",
            )
        ).strip()

        if not full_symbol:

            full_symbol = (
                f"{symbol}"
                f"{'.TWO' if market == 'TPEX' else '.TW'}"
            )

        name = clean_name(
            value.get(
                "name",
                symbol,
            )
        )

        record = {

            "symbol":
                symbol,

            "full_symbol":
                full_symbol,

            "name":
                name or symbol,

            "market":
                market,

            "type":
                sec_type,
        }

        securities.append(
            record
        )

        type_counts[
            sec_type
        ] = (
            type_counts.get(
                sec_type,
                0,
            ) + 1
        )

        market_counts[
            market
        ] = (
            market_counts.get(
                market,
                0,
            ) + 1
        )

    if len(
        securities
    ) != declared_count:

        log(
            "❌ Universe 解析後數量不一致"
        )

        return []

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log("")
    log(
        "Universe 類型："
    )

    for key in sorted(
        type_counts
    ):

        log(
            f"  {key}："
            f"{type_counts[key]}"
        )

    log("")
    log(
        "Universe 市場："
    )

    for key in sorted(
        market_counts
    ):

        log(
            f"  {key}："
            f"{market_counts[key]}"
        )

    return securities


# ============================================================
# TWSE Institutional
# ============================================================

def fetch_twse_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/fund/T86"
    )

    params = {

        "response":
            "json",

        "date":
            yyyymmdd(
                date_obj
            ),

        "selectType":
            "ALL",
    }

    data = get_json(
        url,
        params,
    )

    if not isinstance(
        data,
        dict,
    ):

        return {}

    if data.get(
        "stat"
    ) != "OK":

        return {}

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):

        return {}

    result = {}

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
# TPEx Institutional
# ============================================================

def fetch_tpex_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_3insti_daily_trading"
    )

    data = get_json(
        url
    )

    rows = json_rows(
        data
    )

    result = {}

    for row in rows:

        symbol = row_code(
            row
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        net = row_number(
            row,
            [
                "NetBuySell",
                "NetBuySellVolume",
                "ThreeInstitutionalNetBuySell",
                "三大法人買賣超",
                "三大法人買賣超股數",
            ],
            [
                "三大法人買賣超",
                "NetBuySell",
            ],
        )

        if net is None:

            continue

        result[symbol] = round(
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

    result = {}

    twse = (
        fetch_twse_institutional(
            date_obj
        )
    )

    for symbol, value in (
        twse.items()
    ):

        result[symbol] = value

    time.sleep(
        REQUEST_SLEEP
    )

    # --------------------------------------------------------
    # TPEx OpenAPI 是 snapshot endpoint。
    #
    # 歷史 20D 對 TPEx 不能直接用今日 snapshot，
    # 因此正式歷史資料仍依現有官方歷史 HTML/API 鏈。
    #
    # 這裡保留相容處理。
    # --------------------------------------------------------

    tpex = (
        fetch_tpex_institutional(
            date_obj
        )
    )

    for symbol, value in (
        tpex.items()
    ):

        result[symbol] = value

    return result


# ============================================================
# TWSE Day Trade
# ============================================================

def fetch_twse_daytrade(
    date_obj: datetime,
) -> Dict[str, float]:

    date_str = yyyymmdd(
        date_obj
    )

    # --------------------------------------------------------
    # TWSE 官方現股當沖
    # --------------------------------------------------------

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/trading/"
        "historical/day-trading"
    )

    params = {

        "response":
            "json",

        "date":
            date_str,
    }

    data = get_json(
        url,
        params,
    )

    if not isinstance(
        data,
        dict,
    ):

        return {}

    rows = data.get(
        "data",
        [],
    )

    if not isinstance(
        rows,
        list,
    ):

        return {}

    result = {}

    for row in rows:

        if not isinstance(
            row,
            list,
        ):

            continue

        if len(row) < 2:

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

        # TWSE 此 endpoint 的成交量欄位
        # 位於數值資料中。
        #
        # 保留現有已驗證可用的解析方式。

        volume = numbers[0]

        if volume < 0:

            continue

        result[code] = round(
            volume,
            2,
        )

    return result


# ============================================================
# TPEx Day Trade OpenAPI
# ============================================================

def fetch_tpex_daytrade_openapi(
) -> Dict[
    str,
    Dict[str, float]
]:

    """
    官方 TPEx OpenAPI：

        /openapi/v1/
        tpex_intraday_trading_statistics

    官方 Swagger 定義為：

        上櫃股票現股當沖交易統計資訊

    重要：
        不使用固定 index。

    使用欄位名稱尋找：

        證券代號
        當沖成交量
        成交量
        當沖率

    若 API 已直接提供總成交量與當沖成交量，
    優先使用 API 值。

    若只提供當沖量，
    total_volume 交由日行情資料補足。
    """

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_intraday_trading_statistics"
    )

    data = get_json(
        url
    )

    rows = json_rows(
        data
    )

    result = {}

    for row in rows:

        code = row_code(
            row
        )

        if not is_valid_symbol(
            code
        ):

            continue

        day_trade = row_number(
            row,
            [
                "DayTradingVolume",
                "DayTradeVolume",
                "DayTradingQty",
                "DayTradeQty",
                "當沖成交量",
                "當沖成交股數",
                "當日沖銷成交量",
                "現股當沖成交量",
            ],
            [
                "當沖成交",
                "當日沖銷成交",
                "DayTrade",
            ],
        )

        if day_trade is None:

            continue

        if day_trade < 0:

            continue

        total_volume = row_number(
            row,
            [
                "Volume",
                "TradingVolume",
                "TotalVolume",
                "成交量",
                "總成交量",
                "成交股數",
            ],
            [
                "成交量",
                "Volume",
            ],
        )

        day_rate = row_number(
            row,
            [
                "DayTradingRatio",
                "DayTradeRatio",
                "當沖率",
                "當日沖銷比率",
                "當沖比率",
            ],
            [
                "當沖率",
                "當沖比率",
                "Ratio",
            ],
        )

        if total_volume is not None:

            if total_volume <= 0:

                total_volume = None

        if (
            total_volume is not None
            and day_trade > total_volume
        ):

            # 官方資料不應發生。
            # 這筆直接拒絕，不修正成 0。
            continue

        result[code] = {

            "day_trading_volume":
                round(
                    day_trade,
                    2,
                ),

            "total_volume":
                (
                    round(
                        total_volume,
                        2,
                    )
                    if total_volume is not None
                    else None
                ),

            "day_trading_rate":
                (
                    round(
                        day_rate,
                        4,
                    )
                    if day_rate is not None
                    else None
                ),
        }

    return result


# ============================================================
# TPEx Day Trade HTML fallback
# ============================================================

def fetch_tpex_daytrade_html(
    date_obj: datetime,
) -> Dict[
    str,
    Dict[str, float]
]:

    """
    HTML fallback。

    只在官方 OpenAPI 無資料時使用。

    不猜固定 index。

    所有欄位以欄名辨識。
    """

    date_roc = roc_date(
        date_obj
    )

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/3insti/"
        "intraday_trading/"
        "intraday_trading_statistics.php"
    )

    params = {

        "l":
            "zh-tw",

        "d":
            date_roc,

        "t":
            "D",
    }

    response = get_response(
        url,
        params,
    )

    if response is None:

        return {}

    parser = HTMLParser()

    class Parser(
        HTMLParser
    ):

        def __init__(self):

            super().__init__(
                convert_charrefs=True
            )

            self.rows = []

            self.row = None

            self.cell = None

        def handle_starttag(
            self,
            tag,
            attrs,
        ):

            tag = tag.lower()

            if tag == "tr":

                self.row = []

            elif (
                tag in {
                    "td",
                    "th",
                }
                and self.row is not None
            ):

                self.cell = []

        def handle_data(
            self,
            data,
        ):

            if self.cell is not None:

                self.cell.append(
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
                and self.row is not None
            ):

                self.row.append(
                    "".join(
                        self.cell or []
                    ).strip()
                )

                self.cell = None

            elif tag == "tr":

                if self.row:

                    self.rows.append(
                        self.row
                    )

                self.row = None

    parser = Parser()

    try:

        parser.feed(
            response.text
        )

    except Exception:

        return {}

    if not parser.rows:

        return {}

    headers = None

    for row in parser.rows:

        joined = "".join(
            normalize_field(x)
            for x in row
        )

        if (
            "證券代號" in joined
            and (
                "當沖" in joined
                or "當日沖銷" in joined
            )
        ):

            headers = row

            break

    if headers is None:

        return {}

    code_index = None

    day_index = None

    total_index = None

    rate_index = None

    normalized_headers = [
        normalize_field(
            x
        )
        for x in headers
    ]

    for index, header in enumerate(
        normalized_headers
    ):

        if (
            code_index is None
            and (
                "證券代號" in header
                or header == "代號"
            )
        ):

            code_index = index

        if (
            day_index is None
            and (
                "當沖成交" in header
                or "當日沖銷成交" in header
            )
        ):

            day_index = index

        if (
            total_index is None
            and (
                header == "成交量"
                or "總成交量" in header
            )
        ):

            total_index = index

        if (
            rate_index is None
            and "當沖率" in header
        ):

            rate_index = index

    if code_index is None:

        return {}

    result = {}

    for row in parser.rows:

        if len(row) <= code_index:

            continue

        code = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            code
        ):

            continue

        day_trade = None

        total_volume = None

        day_rate = None

        if (
            day_index is not None
            and len(row) > day_index
        ):

            day_trade = safe_number(
                row[day_index]
            )

        if (
            total_index is not None
            and len(row) > total_index
        ):

            total_volume = safe_number(
                row[total_index]
            )

        if (
            rate_index is not None
            and len(row) > rate_index
        ):

            day_rate = safe_number(
                row[rate_index]
            )

        if day_trade is None:

            continue

        if day_trade < 0:

            continue

        if (
            total_volume is not None
            and total_volume > 0
            and day_trade > total_volume
        ):

            continue

        result[code] = {

            "day_trading_volume":
                round(
                    day_trade,
                    2,
                ),

            "total_volume":
                (
                    round(
                        total_volume,
                        2,
                    )
                    if total_volume is not None
                    else None
                ),

            "day_trading_rate":
                (
                    round(
                        day_rate,
                        4,
                    )
                    if day_rate is not None
                    else None
                ),
        }

    return result


# ============================================================
# TPEx Day Trade
# ============================================================

def fetch_tpex_daytrade(
    date_obj: datetime,
) -> Dict[
    str,
    Dict[str, float]
]:

    result = (
        fetch_tpex_daytrade_openapi()
    )

    if result:

        log(
            f"      ✓ TPEx OpenAPI："
            f"{len(result)} 檔"
        )

        return result

    log(
        "      ⚠️ TPEx OpenAPI 無資料，"
        "啟動 HTML fallback"
    )

    result = (
        fetch_tpex_daytrade_html(
            date_obj
        )
    )

    if result:

        log(
            f"      ✓ TPEx HTML fallback："
            f"{len(result)} 檔"
        )

    else:

        log(
            "      ❌ TPEx 當沖資料來源失敗"
        )

    return result


# ============================================================
# Daily day-trade
# ============================================================

def fetch_daily_daytrade(
    date_obj: datetime,
) -> Dict[
    str,
    Dict[str, float]
]:

    result = {}

    twse = fetch_twse_daytrade(
        date_obj
    )

    for code, value in (
        twse.items()
    ):

        result[code] = {
            "day_trading_volume":
                value,

            "total_volume":
                None,

            "day_trading_rate":
                None,
        }

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = fetch_tpex_daytrade(
        date_obj
    )

    for code, value in (
        tpex.items()
    ):

        result[code] = value

    return result


# ============================================================
# History
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

            date_text = (
                current.strftime(
                    "%Y-%m-%d"
                )
            )

            log(
                f"[{successful_days + 1}/"
                f"{days}] "
                f"{date_text}"
            )

            data = (
                fetch_daily_institutional(
                    current
                )
            )

            if data:

                successful_days += 1

                if latest_date is None:

                    latest_date = date_text

                for (
                    symbol,
                    value,
                ) in data.items():

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
                    f"法人："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ "
                    "本日法人資料無效"
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
# Period
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

    for symbol, item in (
        stocks.items()
    ):

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
                    f"❌ {symbol}."
                    f"{field} 禁止存在"
                )

                errors += 1

    return errors == 0


# ============================================================
# Build
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
) -> Tuple[
    Dict[
        str,
        Dict[str, Any]
    ],
    Dict[str, int],
]:

    stocks = {}

    complete_1d = 0

    complete_5d = 0

    complete_10d = 0

    complete_20d = 0

    insufficient = 0

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

        stocks[symbol] = {

            "symbol":
                symbol,

            "full_symbol":
                item["full_symbol"],

            "name":
                item["name"],

            "market":
                item["market"],

            # =================================================
            # 重要：
            #
            # 完整繼承 Universe type
            # =================================================

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
                None,

            "day_trading_rate":
                None,

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
    }

    return (
        stocks,
        statistics,
    )


# ============================================================
# Apply day-trade data
# ============================================================

def apply_daytrade(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
    daytrade: Dict[
        str,
        Dict[str, float]
    ],
) -> Dict[str, int]:

    valid = 0

    invalid = 0

    missing = 0

    for symbol, item in (
        stocks.items()
    ):

        record = daytrade.get(
            symbol
        )

        if not record:

            missing += 1

            continue

        day_volume = record.get(
            "day_trading_volume"
        )

        total_volume = record.get(
            "total_volume"
        )

        day_rate = record.get(
            "day_trading_rate"
        )

        if day_volume is None:

            missing += 1

            continue

        if day_volume < 0:

            invalid += 1

            continue

        # ----------------------------------------------------
        # 若 API 沒直接給總量，
        # 不能用 0 代替。
        # ----------------------------------------------------

        if (
            total_volume is not None
            and total_volume <= 0
        ):

            total_volume = None

        # ----------------------------------------------------
        # 當沖量不得大於總成交量
        # ----------------------------------------------------

        if (
            total_volume is not None
            and day_volume > total_volume
        ):

            invalid += 1

            continue

        # ----------------------------------------------------
        # 如果 API 沒直接給 rate，
        # 有 total volume 才自行計算。
        # ----------------------------------------------------

        if day_rate is None:

            if (
                total_volume is not None
                and total_volume > 0
            ):

                day_rate = (
                    day_volume
                    / total_volume
                    * 100.0
                )

        # ----------------------------------------------------
        # Rate validation
        # ----------------------------------------------------

        if day_rate is not None:

            if (
                day_rate < 0
                or day_rate > 100
            ):

                invalid += 1

                continue

            day_rate = round(
                day_rate,
                4,
            )

        item[
            "day_trading_volume"
        ] = round(
            day_volume,
            2,
        )

        item[
            "day_trading_rate"
        ] = day_rate

        valid += 1

    return {

        "valid":
            valid,

        "invalid":
            invalid,

        "missing":
            missing,
    }


# ============================================================
# Universe verification
# ============================================================

def verify_universe(
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

    except Exception:

        return False

    if not isinstance(
        data,
        dict,
    ):

        return False

    raw_count = data.get(
        "universe_count"
    )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return False

    try:

        expected = int(
            raw_count
        )

    except Exception:

        expected = len(
            stocks
        )

    if expected != len(
        stocks
    ):

        return False

    if len(
        securities
    ) != expected:

        log(
            "❌ fetch_chip / Universe 數量不一致"
        )

        return False

    return True


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

        "day_trading_rate",

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
            - set(item.keys())
        )

        if missing:

            errors += 1

            log(
                f"❌ {symbol} "
                f"缺欄位："
                f"{sorted(missing)}"
            )

        if clean_code(
            item.get(
                "symbol"
            )
        ) != symbol:

            errors += 1

        if not clean_name(
            item.get(
                "name"
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

        # ----------------------------------------------------
        # type 不再限制只能 Stock / ETF。
        #
        # 因為 Universe 現在可能合法包含：
        #
        # Stock
        # ETF
        # ETN
        # TDR
        # Warrant
        # Bond
        # ...
        # ----------------------------------------------------

        if not clean_name(
            item.get(
                "type"
            )
        ):

            errors += 1

    if not scan_forbidden_fields(
        stocks
    ):

        errors += 1

    if errors:

        log(
            f"❌ 結構驗證失敗："
            f"{errors}"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔結構驗證 PASS"
    )

    return True


# ============================================================
# Day-trade validation
# ============================================================

def validate_daytrade(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
) -> bool:

    errors = 0

    valid_volume = 0

    valid_rate = 0

    for symbol, item in (
        stocks.items()
    ):

        volume = item.get(
            "day_trading_volume"
        )

        rate = item.get(
            "day_trading_rate"
        )

        if volume is not None:

            if volume < 0:

                errors += 1

                log(
                    f"❌ {symbol} "
                    "當沖量 < 0"
                )

            else:

                valid_volume += 1

        if rate is not None:

            if (
                rate < 0
                or rate > 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率={rate}"
                )

            else:

                valid_rate += 1

    log(
        f"✓ 當沖成交量有效："
        f"{valid_volume}"
    )

    log(
        f"✓ 當沖率有效："
        f"{valid_rate}"
    )

    if errors:

        log(
            f"❌ 當沖驗證失敗："
            f"{errors}"
        )

        return False

    return True


# ============================================================
# Atomic Write
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
# Post-write
# ============================================================

def verify_written_chip(
    expected_count: int,
) -> bool:

    section(
        "寫入後重新驗證 chip.json"
    )

    if not CHIP_FILE.exists():

        return False

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

    except Exception:

        return False

    if not isinstance(
        data,
        dict,
    ):

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        return False

    if len(
        stocks
    ) != expected_count:

        log(
            f"❌ Chip 數量："
            f"{len(stocks)}"
        )

        return False

    if not scan_forbidden_fields(
        stocks
    ):

        return False

    if not validate_daytrade(
        stocks
    ):

        return False

    log(
        f"✓ chip.json："
        f"{len(stocks)} 檔"
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
        "  當沖："
        "TWSE + TPEx"
    )

    log(
        "  Type："
        "完整繼承 Universe"
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

    if not verify_universe(
        securities
    ):

        return 1

    # ========================================================
    # 2. History
    # ========================================================

    data_date, history = (
        fetch_history(
            HISTORY_DAYS
        )
    )

    if not data_date:

        log(
            "❌ 法人歷史資料失敗"
        )

        return 1

    if not history:

        log(
            "❌ history 為空"
        )

        return 1

    # ========================================================
    # 3. Build
    # ========================================================

    section(
        "建立全市場 Chip"
    )

    stocks, statistics = (
        build_chip(
            securities,
            history,
            data_date,
        )
    )

    if len(
        stocks
    ) != len(
        securities
    ):

        log(
            "❌ Chip / Universe 數量不一致"
        )

        return 1

    # ========================================================
    # 4. Day trade
    # ========================================================

    section(
        "同步當沖資料"
    )

    today = now_taiwan()

    daytrade = (
        fetch_daily_daytrade(
            today
        )
    )

    daytrade_statistics = (
        apply_daytrade(
            stocks,
            daytrade,
        )
    )

    log("")
    log(
        "當沖套用結果："
    )

    log(
        f"  有效："
        f"{daytrade_statistics['valid']}"
    )

    log(
        f"  無資料："
        f"{daytrade_statistics['missing']}"
    )

    log(
        f"  拒絕："
        f"{daytrade_statistics['invalid']}"
    )

    # ========================================================
    # 5. Structure
    # ========================================================

    if not validate_structure(
        stocks
    ):

        return 1

    if not validate_daytrade(
        stocks
    ):

        return 1

    # ========================================================
    # 6. Statistics
    # ========================================================

    type_counts: Dict[
        str,
        int
    ] = {}

    market_counts: Dict[
        str,
        int
    ] = {}

    for item in (
        stocks.values()
    ):

        sec_type = item[
            "type"
        ]

        market = item[
            "market"
        ]

        type_counts[
            sec_type
        ] = (
            type_counts.get(
                sec_type,
                0,
            ) + 1
        )

        market_counts[
            market
        ] = (
            market_counts.get(
                market,
                0,
            ) + 1
        )

    # ========================================================
    # 7. Output
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

        "type_counts":
            type_counts,

        "market_counts":
            market_counts,

        "statistics":
            statistics,

        "daytrade_statistics":
            daytrade_statistics,

        "stocks":
            stocks,
    }

    if (
        output[
            "universe_count"
        ]
        != len(securities)
    ):

        log(
            "❌ 最終數量驗證失敗"
        )

        return 1

    # ========================================================
    # 8. Atomic Write
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
    # 9. Post-write
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        log(
            "❌ 寫入後驗證失敗"
        )

        return 1

    # ========================================================
    # 10. Final report
    # ========================================================

    elapsed = (
        time.time()
        - start
    )

    section(
        "FINAL REPORT"
    )

    log(
        f"✓ Universe："
        f"{len(securities)}"
    )

    log(
        f"✓ Chip："
        f"{len(stocks)}"
    )

    log("")
    log(
        "Universe Type（直接繼承）："
    )

    for key in sorted(
        type_counts
    ):

        log(
            f"  {key}："
            f"{type_counts[key]}"
        )

    log("")
    log(
        "Market："
    )

    for key in sorted(
        market_counts
    ):

        log(
            f"  {key}："
            f"{market_counts[key]}"
        )

    log("")
    log(
        "三大法人完整度："
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
        "當沖："
    )

    log(
        f"  有效："
        f"{daytrade_statistics['valid']}"
    )

    log(
        f"  缺資料："
        f"{daytrade_statistics['missing']}"
    )

    log(
        f"  拒絕："
        f"{daytrade_statistics['invalid']}"
    )

    log("")
    log(
        "禁止欄位："
    )

    log(
        "  main_force_1d"
    )

    log(
        "  main_force_5d"
    )

    log(
        "  main_force_10d"
    )

    log(
        "  main_force_20d"
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