#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.0.0

============================================================
全市場籌碼資料正式版
============================================================

目的
------------------------------------------------------------
建立 Data/chip.json

資料來源：
    Data/universe.json

資料內容：
    1. 三大法人 1D
    2. 三大法人 5D
    3. 三大法人 10D
    4. 三大法人 20D
    5. 當沖資料

重要原則
------------------------------------------------------------
1. Universe 是唯一股票池
2. 全市場處理
3. 不固定驗證特定股票
4. 不產生 main_force_*
5. 不用三大法人倍率估算主力
6. 缺資料 = None，不以 0 冒充
7. 單一股票缺資料不能破壞整批
8. 官方 API 整批失敗才停止
9. TWSE / TPEX 分開處理
10. 1D / 5D / 10D / 20D 都由每日原始資料累計
11. Universe / Chip 數量必須一致
12. 寫入前後都驗證
13. Atomic Write
14. 最終輸出完整診斷統計

注意
------------------------------------------------------------
本程式的 institutional_* 定義為：

    三大法人買賣超

不是：

    主力買賣超

因此絕對不產生：

    main_force_1d
    main_force_5d
    main_force_10d
    main_force_20d

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

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V10.0.0"


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

REQUEST_SLEEP = 0.8

MAX_LOOKBACK_DAYS = 60

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

    return now_taiwan().strftime(
        "%Y-%m-%d"
    )


# ============================================================
# Basic helpers
# ============================================================

def clean_code(value: Any) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


def clean_name(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


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
        "－",
        "None",
        "null",
        "N/A",
        "NA",
    }:
        return None

    try:

        value_float = float(text)

        if not math.isfinite(value_float):
            return None

        return value_float

    except Exception:

        return None


def roc_date(
    date_obj: datetime,
) -> str:

    roc_year = date_obj.year - 1911

    return (
        f"{roc_year:03d}/"
        f"{date_obj.month:02d}/"
        f"{date_obj.day:02d}"
    )


def yyyymmdd(
    date_obj: datetime,
) -> str:

    return date_obj.strftime(
        "%Y%m%d"
    )


# ============================================================
# Symbol validation
# ============================================================

def is_valid_symbol(
    code: str,
) -> Tuple[bool, str]:

    code = clean_code(code)

    if not code:
        return False, "Other"

    # --------------------------------------------------------
    # 4~6 碼純數字
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):

        return True, "Stock"

    # --------------------------------------------------------
    # 4~6 碼數字 + 1~2 碼英數尾碼
    #
    # 例如：
    # 00400A
    # 00631L
    # 00632R
    # 00710B
    # 2887Z1
    # --------------------------------------------------------

    if re.fullmatch(
        r"\d{4,6}[A-Z0-9]{1,2}",
        code,
    ):

        suffix_match = re.search(
            r"[A-Z]+[A-Z0-9]*$",
            code,
        )

        if suffix_match:

            return True, "ETF"

    return False, "Other"


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[Dict[str, str]]:

    section(
        "讀取 Data/universe.json"
    )

    if not UNIVERSE_FILE.exists():

        log(
            f"❌ 找不到：{UNIVERSE_FILE}"
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

    declared_count = None

    if isinstance(data, dict):

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
                    "❌ universe_count 無法轉成整數"
                )

                return []

    items: List[Dict[str, Any]] = []

    source_count = 0

    # --------------------------------------------------------
    # V10/V11 stocks object
    # --------------------------------------------------------

    if isinstance(data, dict):

        stocks = data.get("stocks")

        if isinstance(stocks, dict):

            source_count = len(stocks)

            log(
                f"✓ 偵測 stocks object："
                f"{source_count} 檔"
            )

            if (
                declared_count is not None
                and declared_count != source_count
            ):

                log(
                    "❌ Universe 數量矛盾"
                )

                log(
                    f"   universe_count = "
                    f"{declared_count}"
                )

                log(
                    f"   stocks object = "
                    f"{source_count}"
                )

                return []

            for key, value in stocks.items():

                if not isinstance(
                    value,
                    dict,
                ):

                    log(
                        f"❌ stocks[{key}] 不是 object"
                    )

                    return []

                item = dict(value)

                item["symbol"] = clean_code(
                    key
                )

                if not item.get("code"):

                    item["code"] = clean_code(
                        key
                    )

                items.append(item)

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
                    if isinstance(x, dict)
                ]

                source_count = len(items)

    elif isinstance(data, list):

        items = [
            dict(x)
            for x in data
            if isinstance(x, dict)
        ]

        source_count = len(items)

    if not items:

        log(
            "❌ Universe 沒有可用股票資料"
        )

        return []

    # --------------------------------------------------------
    # Parse
    # --------------------------------------------------------

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

        valid, inferred_type = (
            is_valid_symbol(symbol)
        )

        if not valid:

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
                "",
            )
        )

        market = (
            str(
                item.get(
                    "market",
                    "",
                )
            )
            .strip()
            .upper()
        )

        full_symbol = str(
            item.get(
                "full_symbol",
                "",
            )
        ).strip()

        original_symbol = str(
            item.get(
                "symbol",
                "",
            )
        ).strip().upper()

        # ----------------------------------------------------
        # Market
        # ----------------------------------------------------

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

            else:

                # 只有 Universe 沒市場資訊時才使用
                # 保守 fallback。
                #
                # 不作為資料來源判斷的主要依據。

                if symbol.startswith(
                    "3"
                ):

                    market = "TPEX"

                else:

                    market = "TWSE"

        # ----------------------------------------------------
        # Type
        # ----------------------------------------------------

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

            sec_type = inferred_type

        # ----------------------------------------------------
        # Full symbol
        # ----------------------------------------------------

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

    # --------------------------------------------------------
    # Universe integrity
    # --------------------------------------------------------

    log("")
    log("Universe 驗證")
    log(
        f"  原始標的：{source_count}"
    )
    log(
        f"  成功載入：{len(securities)}"
    )
    log(
        f"  被排除：{len(rejected)}"
    )

    if rejected:

        log("")
        log(
            "❌ 被排除標的："
        )

        for item in rejected[:100]:

            log(
                f"   "
                f"{item['symbol']} | "
                f"{item['reason']}"
            )

    # stocks object 必須 100% 通過
    if (
        source_count
        and len(securities) != source_count
    ):

        log("")
        log(
            "❌ Universe 解析後數量不一致"
        )

        log(
            f"   原始：{source_count}"
        )

        log(
            f"   載入：{len(securities)}"
        )

        return []

    if (
        declared_count is not None
        and declared_count != len(securities)
    ):

        log(
            "❌ universe_count 與實際載入數量不一致"
        )

        return []

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    stock_count = sum(
        1
        for x in securities
        if x["type"] == "Stock"
    )

    etf_count = sum(
        1
        for x in securities
        if x["type"] == "ETF"
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

    log("")
    log(
        f"✓ 全市場 Universe："
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
# HTTP helper
# ============================================================

def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      HTTP {response.status_code}"
            )

            return None

        return response.json()

    except Exception as exc:

        log(
            f"      API error：{exc}"
        )

        return None


# ============================================================
# TWSE institutional
# ============================================================

def fetch_twse_institutional(
    date_str: str,
) -> Dict[str, float]:

    url = (
        "https://www.twse.com.tw/"
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

    # --------------------------------------------------------
    # T86 欄位：
    #
    # row[0] = 證券代號
    # row[18] = 三大法人買賣超股數
    #
    # 官方資料以股數提供。
    # 系統統一轉成「張」。
    # --------------------------------------------------------

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

        valid, _ = is_valid_symbol(
            symbol
        )

        if not valid:

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

    """
    TPEx 官方三大法人日交易資訊。

    官方頁面格式：
        3itrade_hedge_result.php

    使用 ROC 日期。

    TPEx 欄位為買進 / 賣出 / 買賣超三元組。

    依官方資料結構：
        外資合計買賣超
        投信買賣超
        自營商買賣超
        三大法人買賣超

    最終採用最後一欄：
        三大法人買賣超股數

    單位：
        股 -> 張
    """

    roc = roc_date(
        date_obj
    )

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/3insti/daily_trade/"
        "3itrade_hedge_result.php"
    )

    params = {
        "l": "zh-tw",
        "se": "EW",
        "t": "D",
        "d": roc,
    }

    try:

        response = session.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return {}

        text = response.text

    except Exception:

        return {}

    result: Dict[str, float] = {}

    # --------------------------------------------------------
    # TPEx 現行頁面是 HTML table。
    #
    # 使用 pandas 不必要；
    # 直接解析 table row。
    # --------------------------------------------------------

    try:

        from html.parser import HTMLParser

    except Exception:

        return {}

    class TableParser(
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

    parser = TableParser()

    try:

        parser.feed(text)

    except Exception:

        return {}

    # --------------------------------------------------------
    # 找資料列
    # --------------------------------------------------------

    for row in parser.rows:

        if len(row) < 20:

            continue

        code = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            code
        )

        if not valid:

            continue

        # ----------------------------------------------------
        # 最後一欄通常為三大法人買賣超股數
        # ----------------------------------------------------

        candidates = []

        for value in row[2:]:

            number = safe_number(
                value
            )

            if number is not None:

                candidates.append(
                    number
                )

        if not candidates:

            continue

        # 官方 TPEx 表格最後欄：
        # 三大法人買賣超股數

        net = candidates[-1]

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

    for symbol, value in tpex.items():

        result[symbol] = value

    return result


# ============================================================
# TWSE day trade
# ============================================================

def fetch_twse_daytrade(
    date_str: str,
) -> Dict[str, Dict[str, float]]:

    """
    TWSE 當沖資料。

    這個 endpoint 的資料重點是：
        當沖成交股數

    當沖率不是 API 直接給出的股票欄位，
    因此由：

        當沖成交股數
        /
        該股票總成交股數

    計算。

    本函式只負責取得官方當沖成交量。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/trading/"
        "historical/day-trading"
    )

    params = {
        "response": "json",
        "date": date_str,
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[
        str,
        Dict[str, float]
    ] = {}

    if not isinstance(
        data,
        dict,
    ):

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

        if len(row) < 3:

            continue

        code = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            code
        )

        if not valid:

            continue

        # ----------------------------------------------------
        # 嘗試從 row 中找數值
        # ----------------------------------------------------

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

        # 當沖成交量位於資料欄位中。
        # 不硬指定唯一 index，
        # 避免官方欄位調整時直接錯位。

        volume = None

        for number in numbers:

            if number >= 0:

                volume = number

                break

        if volume is None:

            continue

        result[code] = {
            "day_trading_volume": round(
                volume,
                2,
            )
        }

    return result


# ============================================================
# Daily history
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
                    f"TWSE/TPEX："
                    f"{len(data)} 檔"
                )

            else:

                log(
                    "      ⚠️ 本日無可用法人資料"
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
        f"✓ 有歷史籌碼資料的標的："
        f"{len(history)}"
    )

    return latest_date, history


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
    stocks: Dict[str, Dict[str, Any]],
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
# Build records
# ============================================================

def build_chip(
    securities: List[Dict[str, str]],
    history: Dict[str, List[float]],
    data_date: str,
) -> Tuple[
    Dict[str, Dict[str, Any]],
    Dict[str, int],
]:

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    complete_20d = 0

    complete_10d = 0

    complete_5d = 0

    complete_1d = 0

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

        stocks[symbol] = {

            "symbol": symbol,

            "full_symbol": item[
                "full_symbol"
            ],

            "name": (
                item["name"]
                or symbol
            ),

            "market": item[
                "market"
            ],

            "type": item[
                "type"
            ],

            # ------------------------------------------------
            # 三大法人
            # ------------------------------------------------

            "institutional_1d": (
                inst_1d
            ),

            "institutional_5d": (
                inst_5d
            ),

            "institutional_10d": (
                inst_10d
            ),

            "institutional_20d": (
                inst_20d
            ),

            # ------------------------------------------------
            # 當沖
            #
            # 目前保留獨立欄位。
            # 沒有可靠資料時為 None，
            # 絕不使用 0 冒充。
            # ------------------------------------------------

            "day_trading_volume": None,

            "day_trading_rate": None,

            "updated_at": data_date,
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

    return stocks, statistics


# ============================================================
# Universe final verification
# ============================================================

def verify_universe_count(
    securities: List[Dict[str, str]],
) -> bool:

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ Universe 重新讀取失敗："
            f"{exc}"
        )

        return False

    expected = None

    actual = None

    if isinstance(data, dict):

        raw = data.get(
            "universe_count"
        )

        if raw is not None:

            try:

                expected = int(raw)

            except Exception:

                return False

        stocks = data.get(
            "stocks"
        )

        if isinstance(
            stocks,
            dict,
        ):

            actual = len(stocks)

    if actual is not None:

        if (
            expected is not None
            and expected != actual
        ):

            log(
                "❌ Universe 原始數量錯誤"
            )

            return False

        if len(securities) != actual:

            log(
                "❌ fetch_chip 載入數量錯誤"
            )

            log(
                f"   Universe：{actual}"
            )

            log(
                f"   fetch_chip："
                f"{len(securities)}"
            )

            return False

    if (
        expected is not None
        and len(securities) != expected
    ):

        log(
            "❌ Universe header / "
            "fetch_chip 數量不一致"
        )

        return False

    return True


# ============================================================
# Structural validation
# ============================================================

def validate_structure(
    stocks: Dict[str, Dict[str, Any]],
) -> bool:

    section(
        "全市場 Chip 結構驗證"
    )

    errors = 0

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

        "day_trading_rate",

        "updated_at",
    }

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
                f"❌ {symbol} symbol 錯誤"
            )

        if not clean_name(
            item.get(
                "name",
                "",
            )
        ):

            errors += 1

            log(
                f"❌ {symbol} name 為空"
            )

        if item.get(
            "market"
        ) not in {
            "TWSE",
            "TPEX",
        }:

            errors += 1

            log(
                f"❌ {symbol} market 無效"
            )

        if item.get(
            "type"
        ) not in {
            "Stock",
            "ETF",
        }:

            errors += 1

            log(
                f"❌ {symbol} type 無效"
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
        f"{len(stocks)} 檔結構驗證通過"
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

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ chip.json JSON 錯誤："
            f"{exc}"
        )

        return False

    if not isinstance(
        data,
        dict,
    ):

        log(
            "❌ chip.json 根節點不是 object"
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
            "❌ chip.json stocks 不是 object"
        )

        return False

    if len(stocks) != expected_count:

        log(
            "❌ chip.json 數量錯誤"
        )

        log(
            f"   預期：{expected_count}"
        )

        log(
            f"   實際：{len(stocks)}"
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
                f"❌ {symbol} 寫入後 symbol 錯誤"
            )

            return False

        if not clean_name(
            item.get(
                "name",
                "",
            )
        ):

            log(
                f"❌ {symbol} 寫入後名稱為空"
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
        "  三大法人：TWSE + TPEx 官方資料"
    )

    log(
        "  期間：1D / 5D / 10D / 20D"
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

        log("")
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

        log("")
        log(
            "❌ 全部交易日 API 都沒有取得有效資料"
        )

        log(
            "❌ 為避免覆蓋既有 chip.json，"
            "本次停止"
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

    stocks, statistics = build_chip(
        securities,
        history,
        data_date,
    )

    # ========================================================
    # 4. Count
    # ========================================================

    if len(stocks) != len(
        securities
    ):

        log(
            "❌ Chip / Universe 數量不一致"
        )

        return 1

    # ========================================================
    # 5. Structure
    # ========================================================

    if not validate_structure(
        stocks
    ):

        return 1

    # ========================================================
    # 6. Output
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

        "stocks":
            stocks,
    }

    # ========================================================
    # 7. Final pre-write count
    # ========================================================

    if (
        output["universe_count"]
        != len(securities)
    ):

        log(
            "❌ 最終 Universe / Chip 數量錯誤"
        )

        return 1

    # ========================================================
    # 8. Write
    # ========================================================

    section(
        "Atomic Write → Data/chip.json"
    )

    if not atomic_write(
        output
    ):

        return 1

    log(
        f"✓ 已寫入：{CHIP_FILE}"
    )

    # ========================================================
    # 9. Post verification
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 10. Final report
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