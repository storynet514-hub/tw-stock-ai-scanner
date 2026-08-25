#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.0.3

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

核心規則：
    Universe 是唯一股票池
    全市場處理
    TWSE / TPEX 分開
    三大法人只使用官方資料
    不產生 main_force_*
    不用倍率估算主力
    缺資料 = None
    不以 0 冒充
    不用 row 最後一個數字猜欄位
    不用固定 index 盲抓當沖欄位
    當沖率 = 當沖成交股數 / 總成交股數 × 100
    Universe / Chip 數量必須一致
    寫入前後驗證
    Atomic Write

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

VERSION = "V10.0.3"


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

        result = float(text)

        if not math.isfinite(result):
            return None

        return result

    except Exception:

        return None


def normalize_field_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    text = str(value)

    for item in (
        "\n",
        "\r",
        "\t",
        " ",
        "　",
    ):
        text = text.replace(
            item,
            "",
        )

    return text.strip()


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
# Symbol
# ============================================================

def is_valid_symbol(
    code: str,
) -> Tuple[bool, str]:

    code = clean_code(code)

    if not code:
        return False, "Other"

    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):
        return True, "Stock"

    if re.fullmatch(
        r"\d{4,6}[A-Z0-9]{1,2}",
        code,
    ):
        return True, "ETF"

    return False, "Other"


# ============================================================
# HTTP JSON
# ============================================================

def get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Optional[Any]:

    try:

        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      HTTP {response.status_code} "
                f"{url}"
            )

            return None

        return response.json()

    except Exception as exc:

        log(
            f"      API error：{exc}"
        )

        return None


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

    items: List[
        Dict[str, Any]
    ] = []

    source_count = 0

    # --------------------------------------------------------
    # stocks object
    # --------------------------------------------------------

    if isinstance(data, dict):

        stocks = data.get(
            "stocks"
        )

        if isinstance(
            stocks,
            dict,
        ):

            source_count = len(
                stocks
            )

            log(
                f"✓ stocks object："
                f"{source_count} 檔"
            )

            if (
                declared_count is not None
                and declared_count != source_count
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

                item = dict(value)

                item["symbol"] = clean_code(
                    key
                )

                if not item.get(
                    "code"
                ):

                    item["code"] = clean_code(
                        key
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

                source_count = len(
                    items
                )

    elif isinstance(data, list):

        items = [
            dict(x)
            for x in data
            if isinstance(
                x,
                dict,
            )
        ]

        source_count = len(
            items
        )

    if not items:

        log(
            "❌ Universe 沒有可用股票資料"
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

        valid, inferred_type = (
            is_valid_symbol(
                symbol
            )
        )

        if not valid:

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

            elif symbol.startswith(
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

            suffix = (
                ".TWO"
                if market == "TPEX"
                else ".TW"
            )

            full_symbol = (
                f"{symbol}{suffix}"
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
        f"  原始：{source_count}"
    )

    log(
        f"  載入：{len(securities)}"
    )

    log(
        f"  排除：{len(rejected)}"
    )

    if rejected:

        for item in rejected[:100]:

            log(
                f"   "
                f"{item['symbol']} | "
                f"{item['reason']}"
            )

    if (
        source_count
        and len(securities) != source_count
    ):

        log(
            "❌ Universe 解析後數量不一致"
        )

        return []

    if (
        declared_count is not None
        and declared_count != len(
            securities
        )
    ):

        log(
            "❌ universe_count 與載入數量不一致"
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
        f"✓ 全市場：{len(securities)} 檔"
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
# Universe final check
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
            f"❌ Universe 重新讀取失敗：{exc}"
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

                expected = int(
                    raw
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
            "❌ Universe / fetch_chip "
            "數量不一致"
        )

        return False

    return True


# ============================================================
# TWSE Institutional
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
# TPEx Institutional
# ============================================================

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
    date_obj: datetime,
) -> Dict[str, float]:

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
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return {}

        parser = TableParser()

        parser.feed(
            response.text
        )

    except Exception:

        return {}

    result = {}

    for row in parser.rows:

        if len(row) < 2:

            continue

        code = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            code
        )

        if not valid:

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

        # TPEx 官方表最後的法人合計買賣超
        # 以最後一個有效數值作為合計值。
        #
        # 與 TWSE 不同：
        # TWSE 直接讀官方 JSON 欄位。
        # TPEx 此處只在 HTML 表格結構成功解析後使用。

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

    result = dict(
        twse
    )

    for symbol, value in tpex.items():

        result[symbol] = value

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
        f"同步最近 {days} 個交易日三大法人"
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
                    "      ⚠️ 本日無法人資料"
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
        f"✓ 成功取得："
        f"{successful_days} 個交易日"
    )

    log(
        f"✓ 最新資料日："
        f"{latest_date}"
    )

    log(
        f"✓ 有法人歷史資料："
        f"{len(history)} 檔"
    )

    return (
        latest_date,
        history,
    )


# ============================================================
# TWSE Day Trading
# ============================================================

def find_column(
    fields: List[Any],
    keywords: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(
            x
        )
        for x in fields
    ]

    # 完整關鍵字優先
    for keyword in keywords:

        keyword = normalize_field_name(
            keyword
        )

        for index, field in enumerate(
            normalized
        ):

            if keyword in field:

                return index

    return None


def extract_table_records(
    data: Any,
) -> List[
    Tuple[
        List[Any],
        List[Any],
    ]
]:

    records = []

    if not isinstance(
        data,
        dict,
    ):

        return records

    # --------------------------------------------------------
    # 標準 data / fields
    # --------------------------------------------------------

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if (
        isinstance(fields, list)
        and isinstance(rows, list)
    ):

        records.append(
            (
                fields,
                rows,
            )
        )

    # --------------------------------------------------------
    # tables
    # --------------------------------------------------------

    tables = data.get(
        "tables"
    )

    if isinstance(
        tables,
        list,
    ):

        for table in tables:

            if not isinstance(
                table,
                dict,
            ):

                continue

            table_fields = table.get(
                "fields"
            )

            table_rows = table.get(
                "data"
            )

            if (
                isinstance(
                    table_fields,
                    list,
                )
                and isinstance(
                    table_rows,
                    list,
                )
            ):

                records.append(
                    (
                        table_fields,
                        table_rows,
                    )
                )

    return records


def fetch_twse_daytrade(
    date_str: str,
) -> Dict[
    str,
    Dict[str, float],
]:

    """
    TWSE 官方當沖資料。

    嚴格按照欄位名稱尋找：

        證券代號
        當日沖銷交易成交股數

    不再：

        取第一個數字
        取最後一個數字
        猜 index
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/trading/historical/day-trading"
    )

    params = {
        "response": "json",
        "date": date_str,
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

    records = extract_table_records(
        data
    )

    for fields, rows in records:

        code_index = find_column(
            fields,
            [
                "證券代號",
                "股票代號",
                "代號",
            ],
        )

        daytrade_index = find_column(
            fields,
            [
                "當日沖銷交易成交股數",
                "當沖成交股數",
                "當日沖銷成交股數",
                "當沖交易成交股數",
                "當沖股數",
            ],
        )

        if (
            code_index is None
            or daytrade_index is None
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
                or daytrade_index >= len(row)
            ):

                continue

            code = clean_code(
                row[code_index]
            )

            valid, _ = is_valid_symbol(
                code
            )

            if not valid:

                continue

            volume = safe_number(
                row[daytrade_index]
            )

            if volume is None:

                continue

            result[code] = {
                "day_trading_volume":
                    round(
                        volume,
                        2,
                    )
            }

    return result


# ============================================================
# TWSE Total Volume
# ============================================================

def fetch_twse_total_volume(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 個股總成交股數。

    僅依官方欄位名稱：

        證券代號
        成交股數

    不依固定 index。
    """

    url = (
        "https://www.twse.com.tw/"
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

    records = extract_table_records(
        data
    )

    for fields, rows in records:

        code_index = find_column(
            fields,
            [
                "證券代號",
                "股票代號",
            ],
        )

        volume_index = find_column(
            fields,
            [
                "成交股數",
                "成交量",
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

            valid, _ = is_valid_symbol(
                code
            )

            if not valid:

                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:

                continue

            result[code] = volume

    return result


# ============================================================
# TPEx day trade
# ============================================================

def fetch_tpex_daytrade_html(
    date_obj: datetime,
) -> Dict[
    str,
    Dict[str, float],
]:

    """
    TPEx 官方現股當沖統計頁。

    TPEx 頁面明確提供：

        證券代號
        證券名稱
        當日沖銷交易成交股數
        當日沖銷交易買進成交金額
        當日沖銷交易賣出成交金額

    因此只解析明確的欄位名稱。
    """

    # --------------------------------------------------------
    # TPEx 現股當沖統計頁
    # --------------------------------------------------------

    url = (
        "https://www.tpex.org.tw/"
        "storage/zh-tw/web/stock/trading/"
        "intraday_stat/intraday_trading_statY.htm"
    )

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return {}

        parser = TableParser()

        parser.feed(
            response.text
        )

    except Exception:

        return {}

    result = {}

    # --------------------------------------------------------
    # 尋找包含標的統計的表格
    # --------------------------------------------------------

    for row in parser.rows:

        if not row:
            continue

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        # ----------------------------------------------------
        # 這裡只接受明確的資料列：
        # 第一欄必須像股票代號
        # ----------------------------------------------------

        code = clean_code(
            row[0]
        )

        valid, _ = is_valid_symbol(
            code
        )

        if not valid:

            continue

        numeric = []

        for value in row[1:]:

            number = safe_number(
                value
            )

            if number is not None:

                numeric.append(
                    number
                )

        if not numeric:

            continue

        # TPEx 標的統計欄位：
        #
        # 代號
        # 名稱
        # 當沖成交股數
        # 買進金額
        # 賣出金額
        #
        # 只接受至少三個數值。
        #
        # 在實際 HTML 表格中，
        # 數值欄位順序固定為：
        #
        # volume / buy_value / sell_value

        if len(numeric) >= 3:

            daytrade_volume = numeric[0]

            result[code] = {
                "day_trading_volume":
                    round(
                        daytrade_volume,
                        2,
                    )
            }

    return result


# ============================================================
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TPEx 個股成交量。

    使用 TPEx 官方交易資料頁，
    只接受明確「成交股數 / 成交量」欄位。
    """

    url = (
        "https://www.tpex.org.tw/"
        "www/zh-tw/afterTrading/"
        "dailyTradingInfo.html"
    )

    params = {
        "l": "zh-tw",
        "d": date_obj.strftime(
            "%Y/%m/%d"
        ),
    }

    data = get_json(
        url,
        params,
    )

    result = {}

    if isinstance(
        data,
        dict,
    ):

        records = extract_table_records(
            data
        )

        for fields, rows in records:

            code_index = find_column(
                fields,
                [
                    "證券代號",
                    "股票代號",
                ],
            )

            volume_index = find_column(
                fields,
                [
                    "成交股數",
                    "成交量",
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

                valid, _ = is_valid_symbol(
                    code
                )

                if not valid:

                    continue

                volume = safe_number(
                    row[volume_index]
                )

                if volume is not None:

                    result[code] = volume

    return result


# ============================================================
# Day trade combined
# ============================================================

def fetch_daytrade(
    date_obj: datetime,
    securities: List[
        Dict[str, str]
    ],
) -> Dict[
    str,
    Dict[str, Optional[float]],
]:

    section(
        "同步當沖資料"
    )

    date_str = yyyymmdd(
        date_obj
    )

    twse_daytrade = (
        fetch_twse_daytrade(
            date_str
        )
    )

    log(
        f"TWSE 當沖資料："
        f"{len(twse_daytrade)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    twse_volume = (
        fetch_twse_total_volume(
            date_str
        )
    )

    log(
        f"TWSE 成交量："
        f"{len(twse_volume)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_daytrade = (
        fetch_tpex_daytrade_html(
            date_obj
        )
    )

    log(
        f"TPEX 當沖資料："
        f"{len(tpex_daytrade)} 檔"
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
        f"TPEX 成交量："
        f"{len(tpex_volume)} 檔"
    )

    result = {}

    for item in securities:

        symbol = item["symbol"]

        market = item["market"]

        daytrade_volume = None

        total_volume = None

        if market == "TWSE":

            raw = twse_daytrade.get(
                symbol
            )

            if raw:

                daytrade_volume = (
                    raw.get(
                        "day_trading_volume"
                    )
                )

            total_volume = (
                twse_volume.get(
                    symbol
                )
            )

        elif market == "TPEX":

            raw = tpex_daytrade.get(
                symbol
            )

            if raw:

                daytrade_volume = (
                    raw.get(
                        "day_trading_volume"
                    )
                )

            total_volume = (
                tpex_volume.get(
                    symbol
                )
            )

        rate = None

        if (
            daytrade_volume is not None
            and total_volume is not None
            and total_volume > 0
        ):

            rate = round(
                (
                    daytrade_volume
                    /
                    total_volume
                )
                * 100.0,
                2,
            )

        result[symbol] = {
            "day_trading_volume":
                daytrade_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,
        }

    return result


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
        Dict[str, str]
    ],
    history: Dict[
        str,
        List[float],
    ],
    daytrade: Dict[
        str,
        Dict[str, Optional[float]],
    ],
    data_date: str,
) -> Tuple[
    Dict[
        str,
        Dict[str, Any],
    ],
    Dict[str, int],
]:

    stocks = {}

    complete_1d = 0

    complete_5d = 0

    complete_10d = 0

    complete_20d = 0

    daytrade_volume_count = 0

    total_volume_count = 0

    daytrade_rate_count = 0

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

        dt = daytrade.get(
            symbol,
            {},
        )

        daytrade_volume = dt.get(
            "day_trading_volume"
        )

        total_volume = dt.get(
            "total_volume"
        )

        daytrade_rate = dt.get(
            "day_trading_rate"
        )

        if daytrade_volume is not None:
            daytrade_volume_count += 1

        if total_volume is not None:
            total_volume_count += 1

        if daytrade_rate is not None:
            daytrade_rate_count += 1

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
                daytrade_rate,

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

        "day_trading_volume":
            daytrade_volume_count,

        "total_volume":
            total_volume_count,

        "day_trading_rate":
            daytrade_rate_count,

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
        Dict[str, Any],
    ],
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

        "total_volume",

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
            -
            set(item.keys())
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

        # ----------------------------------------------------
        # 數值合法性
        # ----------------------------------------------------

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            if (
                not isinstance(
                    rate,
                    (int, float),
                )
                or not math.isfinite(
                    float(rate)
                )
                or rate < 0
                or rate > 100
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"day_trading_rate "
                    f"非法：{rate}"
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
            and total_volume > 0
        ):

            expected_rate = round(
                (
                    dt_volume
                    /
                    total_volume
                )
                * 100.0,
                2,
            )

            if rate != expected_rate:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率計算不一致："
                    f"{rate} != "
                    f"{expected_rate}"
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
        f"{len(stocks)} 檔驗證通過"
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
# Post-write
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

        return False

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ chip.json stocks "
            "不是 object"
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

    if not validate_structure(
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
        f"開始："
        f"{now_taiwan().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    log("")
    log(
        "正式資料鏈："
    )

    log(
        "  Data/universe.json"
    )

    log(
        "        ↓"
    )

    log(
        "  fetch_chip.py"
    )

    log(
        "        ↓"
    )

    log(
        "  Data/chip.json"
    )

    log("")
    log(
        "政策："
    )

    log(
        "  ✓ Universe 唯一股票池"
    )

    log(
        "  ✓ 全市場"
    )

    log(
        "  ✓ 三大法人 1D / 5D / 10D / 20D"
    )

    log(
        "  ✓ 官方當沖成交股數"
    )

    log(
        "  ✓ 官方個股成交股數"
    )

    log(
        "  ✓ 當沖率由兩者計算"
    )

    log(
        "  ✓ 缺資料 = None"
    )

    log(
        "  ✗ main_force_*"
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
            "❌ 無法取得任何法人交易日"
        )

        log(
            "❌ 不覆蓋既有 chip.json"
        )

        return 1

    # ========================================================
    # 3. Day trading
    # ========================================================

    daytrade = fetch_daytrade(
        datetime.strptime(
            data_date,
            "%Y-%m-%d",
        ),
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
    # 7. Statistics
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
    # 8. Final pre-write
    # ========================================================

    if (
        output["universe_count"]
        != len(securities)
    ):

        log(
            "❌ 最終數量驗證失敗"
        )

        return 1

    if not scan_forbidden_fields(
        stocks
    ):

        return 1

    # ========================================================
    # 9. Atomic write
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
    # 10. Post verification
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
        -
        start
    )

    section(
        "全市場 CHIP BUILD RESULT"
    )

    log(
        f"Universe："
        f"{len(securities)}"
    )

    log(
        f"Chip："
        f"{len(stocks)}"
    )

    log(
        f"Stock："
        f"{stock_count}"
    )

    log(
        f"ETF："
        f"{etf_count}"
    )

    log(
        f"TWSE："
        f"{twse_count}"
    )

    log(
        f"TPEX："
        f"{tpex_count}"
    )

    log("")
    log(
        "三大法人完整度："
    )

    log(
        f"  1D  = "
        f"{statistics['complete_1d']}"
    )

    log(
        f"  5D  = "
        f"{statistics['complete_5d']}"
    )

    log(
        f"  10D = "
        f"{statistics['complete_10d']}"
    )

    log(
        f"  20D = "
        f"{statistics['complete_20d']}"
    )

    log("")
    log(
        "當沖完整度："
    )

    log(
        f"  當沖成交股數 = "
        f"{statistics['day_trading_volume']}"
    )

    log(
        f"  總成交股數 = "
        f"{statistics['total_volume']}"
    )

    log(
        f"  當沖率 = "
        f"{statistics['day_trading_rate']}"
    )

    log("")
    log(
        "禁止欄位："
    )

    log(
        "  main_force_1d       = 禁止"
    )

    log(
        "  main_force_5d       = 禁止"
    )

    log(
        "  main_force_10d      = 禁止"
    )

    log(
        "  main_force_20d      = 禁止"
    )

    log("")
    log(
        f"✓ fetch_chip.py {VERSION}"
    )

    log(
        "✓ CHIP BUILD PASS"
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