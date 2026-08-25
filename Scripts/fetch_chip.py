#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.2.0

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
V10.2.0 本次修正重點
============================================================

1. 不重新分類 Universe
2. 不猜 ETF / Bond / Stock
3. 不產生 main_force_*
4. 三大法人只使用官方資料
5. 1D / 5D / 10D / 20D 都由每日資料累計
6. TWSE 當沖欄位採欄位名稱定位
7. TWSE 總成交股數採欄位名稱定位
8. TPEx 當沖欄位採明確欄位名稱定位
9. TPEx 總成交量必須對應目標交易日
10. 禁止使用「最後一列」冒充目標交易日
11. 當沖率必須由：
        當沖成交股數 / 總成交股數 × 100
12. 當沖率必須介於 0~100
13. 缺資料 = None
14. 不使用 0 補資料
15. Universe / Chip 數量必須一致
16. 寫入前後驗證
17. Atomic Write
18. 完整輸出當沖資料品質統計

============================================================
"""

from __future__ import annotations

import json
import math
import re
import sys
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V10.2.0"


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

TPEX_VOLUME_WORKERS = 4


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


def today_taiwan() -> str:

    return now_taiwan().strftime(
        "%Y-%m-%d"
    )


# ============================================================
# Basic helpers
# ============================================================

def clean_code(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


def clean_name(
    value: Any,
) -> str:

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

    for char in (
        "\n",
        "\r",
        "\t",
        " ",
        "　",
    ):
        text = text.replace(
            char,
            "",
        )

    return text.strip()


def normalize_date_text(
    value: Any,
) -> Optional[str]:
    """
    將常見日期格式轉成 YYYY-MM-DD。

    支援：
        2026/08/24
        2026-08-24
        115/08/24
        115年08月24日
        20260824
    """

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
    )

    text = text.strip()

    # YYYYMMDD
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

    parts = text.split("/")

    if len(parts) == 3:

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
            ).strftime(
                "%Y-%m-%d"
            )

        except Exception:

            return None

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
# HTTP JSON
# ============================================================

def get_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    headers: Optional[
        Dict[str, str]
    ] = None,
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
                f"      HTTP "
                f"{response.status_code} "
                f"{url}"
            )

            return None

        text = response.text.strip()

        if not text:
            return None

        return response.json()

    except Exception as exc:

        log(
            f"      API error：{exc}"
        )

        return None


# ============================================================
# Generic table helpers
# ============================================================

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

        records.append(
            (
                fields,
                rows,
            )
        )

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


def find_column(
    fields: List[Any],
    keywords: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(x)
        for x in fields
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
            tag in {
                "td",
                "th",
            }
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

            data = json.load(f)

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

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        log(
            "❌ Universe stocks "
            "不是 object"
        )

        return []

    declared_count = data.get(
        "universe_count"
    )

    try:

        declared_count = (
            int(declared_count)
            if declared_count is not None
            else None
        )

    except Exception:

        log(
            "❌ universe_count "
            "無法解析"
        )

        return []

    source_count = len(
        stocks
    )

    if (
        declared_count is not None
        and declared_count != source_count
    ):

        log(
            "❌ Universe header "
            "數量錯誤"
        )

        log(
            f"   header = "
            f"{declared_count}"
        )

        log(
            f"   stocks = "
            f"{source_count}"
        )

        return []

    securities = []

    seen = set()

    for key, raw in stocks.items():

        if not isinstance(
            raw,
            dict,
        ):

            log(
                f"❌ stocks[{key}] "
                f"不是 object"
            )

            return []

        symbol = clean_code(
            raw.get(
                "symbol",
                key,
            )
        )

        if not is_valid_symbol(
            symbol
        ):

            log(
                f"❌ 無效代號："
                f"{symbol}"
            )

            return []

        if symbol in seen:

            log(
                f"❌ Universe 重複代號："
                f"{symbol}"
            )

            return []

        seen.add(
            symbol
        )

        market = str(
            raw.get(
                "market",
                "",
            )
        ).strip().upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            log(
                f"❌ {symbol} market "
                f"無效：{market}"
            )

            return []

        name = clean_name(
            raw.get(
                "name",
                symbol,
            )
        )

        if not name:

            log(
                f"❌ {symbol} name 為空"
            )

            return []

        full_symbol = str(
            raw.get(
                "full_symbol",
                "",
            )
        ).strip()

        if not full_symbol:

            suffix = (
                ".TWO"
                if market == "TPEX"
                else ".TW"
            )

            full_symbol = (
                f"{symbol}{suffix}"
            )

        raw_type = str(
            raw.get(
                "type",
                "",
            )
        ).strip()

        instrument_type = str(
            raw.get(
                "instrument_type",
                "",
            )
        ).strip()

        status = str(
            raw.get(
                "status",
                "",
            )
        ).strip()

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": name,
                "market": market,
                "type": raw_type,
                "instrument_type":
                    instrument_type,
                "status": status,
            }
        )

    if len(
        securities
    ) != source_count:

        log(
            "❌ Universe / fetch_chip "
            "數量不一致"
        )

        return []

    log("")
    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    stock_count = sum(
        1
        for item in securities
        if item["type"].lower()
        == "stock"
    )

    etf_count = sum(
        1
        for item in securities
        if item["type"].lower()
        == "etf"
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

    result: Dict[
        str,
        float
    ] = {}

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

    result: Dict[
        str,
        float
    ] = {}

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
# Daily institutional
# ============================================================

def fetch_daily_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    date_str = yyyymmdd(
        date_obj
    )

    twse = (
        fetch_twse_institutional(
            date_str
        )
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex = (
        fetch_tpex_institutional(
            date_obj
        )
    )

    result = dict(
        twse
    )

    result.update(
        tpex
    )

    return result


# ============================================================
# History
# ============================================================

def fetch_history(
    days: int = HISTORY_DAYS,
) -> Tuple[
    Optional[str],
    Dict[
        str,
        List[float]
    ],
]:

    section(
        f"同步最近 {days} 個交易日 "
        f"三大法人資料"
    )

    history: Dict[
        str,
        List[float]
    ] = {}

    successful_days = 0

    attempted = 0

    latest_date = None

    current = (
        now_taiwan()
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
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
                f"{successful_days + 1}"
                f"/"
                f"{days}"
                f"] "
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

                    latest_date = (
                        date_text
                    )

                for symbol, value in (
                    data.items()
                ):

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

        return (
            None,
            {},
        )

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
        f"✓ 歷史標的："
        f"{len(history)}"
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
# TWSE day trade
# ============================================================

def fetch_twse_daytrade(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 官方當沖資料。

    僅接受明確欄位：

        證券代號
        當日沖銷交易成交股數

    不猜 index。
    不取第一個數字。
    不取最後一個數字。
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
        float
    ] = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    records = (
        extract_table_records(
            data
        )
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
                "當日沖銷交易成交股數",
                "當沖成交股數",
                "當日沖銷成交股數",
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):

            continue

        for row in rows:

            if (
                code_index >= len(row)
                or volume_index >= len(row)
            ):

                continue

            symbol = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                symbol
            ):

                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:

                continue

            result[symbol] = round(
                volume,
                2,
            )

    return result


# ============================================================
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 每日總成交股數。

    必須在同一 table 中同時找到：

        證券代號
        成交股數

    避免不同 table 欄位錯位。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/MI_INDEX"
    )

    params = {
        "response": "json",
        "date": date_str,
        "type": "ALL",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[
        str,
        float
    ] = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    records = (
        extract_table_records(
            data
        )
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
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):

            continue

        # ----------------------------------------------------
        # 確認欄位不是市場統計欄
        # ----------------------------------------------------

        normalized_fields = [
            normalize_field_name(x)
            for x in fields
        ]

        if code_index >= len(
            normalized_fields
        ):

            continue

        for row in rows:

            if (
                code_index >= len(row)
                or volume_index >= len(row)
            ):

                continue

            symbol = clean_code(
                row[code_index]
            )

            if not is_valid_symbol(
                symbol
            ):

                continue

            volume = safe_number(
                row[volume_index]
            )

            if volume is None:

                continue

            if volume < 0:

                continue

            result[symbol] = round(
                volume,
                2,
            )

    return result


# ============================================================
# TPEx day trade
# ============================================================

def fetch_tpex_daytrade(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TPEx 官方現股當沖統計。

    官方頁面：

        intraday_trading_statY.htm

    僅接受明確欄位：

        證券代號
        當日沖銷交易成交股數

    不使用：
        第一個數字
        最後一個數字
        固定 index

    另外：

        頁面必須能辨識目標日期。

    若日期無法確認，
    本函式直接回傳空資料，
    避免把其他交易日資料寫進 chip.json。
    """

    url = (
        "https://www.tpex.org.tw/"
        "storage/zh-tw/web/stock/trading/"
        "intraday_stat/"
        "intraday_trading_statY.htm"
    )

    target_date = (
        date_obj.strftime(
            "%Y-%m-%d"
        )
    )

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      TPEx 當沖 HTTP "
                f"{response.status_code}"
            )

            return {}

        text = response.text

        parser = TableParser()

        parser.feed(
            text
        )

    except Exception as exc:

        log(
            f"      TPEx 當沖取得失敗："
            f"{exc}"
        )

        return {}

    rows = parser.rows

    # --------------------------------------------------------
    # 先尋找當沖個股 table header
    # --------------------------------------------------------

    header_index = None

    code_index = None

    volume_index = None

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(
                value
            )
            for value in row
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
                or "當沖成交股數"
                in value
            )
            for value in normalized
        )

        if (
            has_code
            and has_volume
        ):

            header_index = index

            for i, field in enumerate(
                normalized
            ):

                if (
                    "證券代號" in field
                    or "股票代號" in field
                ):

                    code_index = i

                if (
                    "當日沖銷交易成交股數"
                    in field
                    or "當沖成交股數"
                    in field
                ):

                    volume_index = i

            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        log(
            "      ⚠️ TPEx 當沖找不到 "
            "明確欄位"
        )

        return {}

    result: Dict[
        str,
        float
    ] = {}

    for row in rows[
        header_index + 1:
    ]:

        if (
            code_index >= len(row)
            or volume_index >= len(row)
        ):

            continue

        symbol = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        volume = safe_number(
            row[volume_index]
        )

        if volume is None:

            continue

        if volume < 0:

            continue

        result[symbol] = round(
            volume,
            2,
        )

    return result


# ============================================================
# TPEx individual total volume
# ============================================================

def fetch_tpex_stock_month(
    symbol: str,
    target_date: datetime,
) -> Optional[float]:

    """
    TPEx 官方個股日成交資訊。

    Endpoint：
        tradingStock

    重要修正：

    舊版：
        直接拿最後一列。

    V10.2.0：
        必須找到 target_date 對應資料列。

    API 的成交張數：
        成交張數 × 1000
        = 成交股數
    """

    url = (
        "https://www.tpex.org.tw/"
        "www/zh-tw/afterTrading/"
        "tradingStock"
    )

    month_date = target_date.replace(
        day=1
    )

    params = {
        "date": month_date.strftime(
            "%Y/%m/%d"
        ),
        "code": symbol,
        "response": "json",
    }

    data = get_json(
        url,
        params,
    )

    if not isinstance(
        data,
        dict,
    ):

        return None

    target_text = (
        target_date.strftime(
            "%Y-%m-%d"
        )
    )

    target_roc = roc_date(
        target_date
    ).replace(
        "/",
        "-",
    )

    records = (
        extract_table_records(
            data
        )
    )

    for fields, rows in records:

        volume_index = find_column(
            fields,
            [
                "成交張數",
            ],
        )

        date_index = find_column(
            fields,
            [
                "日期",
                "交易日期",
            ],
        )

        if (
            volume_index is None
            or date_index is None
        ):

            continue

        for row in rows:

            if (
                date_index >= len(row)
                or volume_index >= len(row)
            ):

                continue

            row_date_raw = str(
                row[date_index]
            ).strip()

            row_date = (
                normalize_date_text(
                    row_date_raw
                )
            )

            if row_date is None:

                # 嘗試民國日期
                candidate = (
                    row_date_raw
                    .replace(
                        "/",
                        "-",
                    )
                )

                if candidate == target_roc:

                    row_date = target_text

            if row_date != target_text:

                continue

            volume_lots = safe_number(
                row[volume_index]
            )

            if volume_lots is None:

                continue

            if volume_lots < 0:

                continue

            return round(
                volume_lots * 1000.0,
                2,
            )

    return None


# ============================================================
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume(
    symbols: List[str],
    target_date: datetime,
) -> Dict[str, float]:

    section(
        "同步 TPEx 個股總成交股數"
    )

    result: Dict[
        str,
        float
    ] = {}

    unique_symbols = sorted(
        set(symbols)
    )

    if not unique_symbols:

        log(
            "TPEx 無需查詢總成交量"
        )

        return result

    log(
        f"TPEx 需要查詢："
        f"{len(unique_symbols)} 檔"
    )

    with ThreadPoolExecutor(
        max_workers=TPEX_VOLUME_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_tpex_stock_month,
                symbol,
                target_date,
            ): symbol
            for symbol in unique_symbols
        }

        for index, future in enumerate(
            as_completed(futures),
            start=1,
        ):

            symbol = futures[
                future
            ]

            try:

                value = (
                    future.result()
                )

            except Exception:

                value = None

            if value is not None:

                result[
                    symbol
                ] = value

            if (
                index % 100 == 0
                or index == len(
                    unique_symbols
                )
            ):

                log(
                    f"      "
                    f"{index}/"
                    f"{len(unique_symbols)} "
                    f"有效={len(result)}"
                )

    return result


# ============================================================
# Day trade combined
# ============================================================

def fetch_daytrade(
    data_date: str,
    securities: List[
        Dict[str, Any]
    ],
) -> Tuple[
    Dict[
        str,
        Dict[str, Optional[float]]
    ],
    Dict[str, int]
]:

    section(
        "同步當沖資料"
    )

    date_obj = datetime.strptime(
        data_date,
        "%Y-%m-%d",
    )

    date_str = yyyymmdd(
        date_obj
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse_daytrade = (
        fetch_twse_daytrade(
            date_str
        )
    )

    log(
        f"TWSE 當沖來源："
        f"{len(twse_daytrade)}"
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
        f"TWSE 總成交量來源："
        f"{len(twse_volume)}"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    tpex_daytrade = (
        fetch_tpex_daytrade(
            date_obj
        )
    )

    log(
        f"TPEx 當沖來源："
        f"{len(tpex_daytrade)}"
    )

    tpex_symbols = sorted(
        set(
            tpex_daytrade.keys()
        )
    )

    tpex_volume = (
        fetch_tpex_total_volume(
            tpex_symbols,
            date_obj,
        )
    )

    log(
        f"TPEx 總成交量來源："
        f"{len(tpex_volume)}"
    )

    # --------------------------------------------------------
    # 建立結果
    # --------------------------------------------------------

    result: Dict[
        str,
        Dict[str, Optional[float]]
    ] = {}

    for item in securities:

        symbol = item[
            "symbol"
        ]

        market = item[
            "market"
        ]

        daytrade_volume = None

        total_volume = None

        if market == "TWSE":

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

        elif market == "TPEX":

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

        rate = None

        # ----------------------------------------------------
        # 嚴格計算當沖率
        # ----------------------------------------------------

        if (
            daytrade_volume is not None
            and total_volume is not None
            and total_volume > 0
        ):

            calculated = (
                daytrade_volume
                /
                total_volume
                *
                100.0
            )

            if (
                math.isfinite(
                    calculated
                )
                and 0 <= calculated <= 100
            ):

                rate = round(
                    calculated,
                    2,
                )

        result[
            symbol
        ] = {

            "day_trading_volume":
                daytrade_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,
        }

    statistics = {

        "twse_daytrade_source":
            len(
                twse_daytrade
            ),

        "twse_total_volume_source":
            len(
                twse_volume
            ),

        "tpex_daytrade_source":
            len(
                tpex_daytrade
            ),

        "tpex_total_volume_source":
            len(
                tpex_volume
            ),

        "day_trading_volume":
            sum(
                1
                for item in result.values()
                if item[
                    "day_trading_volume"
                ] is not None
            ),

        "total_volume":
            sum(
                1
                for item in result.values()
                if item[
                    "total_volume"
                ] is not None
            ),

        "day_trading_rate":
            sum(
                1
                for item in result.values()
                if item[
                    "day_trading_rate"
                ] is not None
            ),

        "rate_missing_volume":
            sum(
                1
                for item in result.values()
                if (
                    item[
                        "day_trading_volume"
                    ] is not None
                    and item[
                        "total_volume"
                    ] is None
                )
            ),

        "rate_missing_daytrade":
            sum(
                1
                for item in result.values()
                if (
                    item[
                        "day_trading_volume"
                    ] is None
                    and item[
                        "total_volume"
                    ] is not None
                )
            ),

        "rate_invalid":
            sum(
                1
                for item in result.values()
                if (
                    item[
                        "day_trading_rate"
                    ] is None
                    and item[
                        "day_trading_volume"
                    ] is not None
                    and item[
                        "total_volume"
                    ] is not None
                )
            ),
    }

    return (
        result,
        statistics,
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
                    f"{symbol}."
                    f"{field} "
                    f"禁止存在"
                )

                return False

    return True


# ============================================================
# Build chip
# ============================================================

def build_chip(
    securities: List[
        Dict[str, Any]
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
    Dict[str, int]
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

        if inst_1d is None:
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

        stocks[
            symbol
        ] = {

            "symbol":
                symbol,

            "full_symbol":
                item[
                    "full_symbol"
                ],

            "name":
                item[
                    "name"
                ],

            "market":
                item[
                    "market"
                ],

            "type":
                item[
                    "type"
                ],

            "instrument_type":
                item[
                    "instrument_type"
                ],

            "status":
                item[
                    "status"
                ],

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
# Universe final verification
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

            data = json.load(f)

    except Exception as exc:

        log(
            f"❌ Universe 重新讀取失敗："
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

        return False

    expected = data.get(
        "universe_count"
    )

    actual = len(
        stocks
    )

    if expected is not None:

        try:

            expected = int(
                expected
            )

        except Exception:

            return False

        if expected != actual:

            log(
                "❌ Universe header "
                "數量錯誤"
            )

            return False

    if len(
        securities
    ) != actual:

        log(
            "❌ Universe / Chip "
            "數量不一致"
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
        "全市場 Chip 結構驗證"
    )

    required_fields = {

        "symbol",

        "full_symbol",

        "name",

        "market",

        "type",

        "instrument_type",

        "status",

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

        # ----------------------------------------------------
        # 當沖率合理性
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
                    f"當沖率異常："
                    f"{rate}"
                )

        daytrade_volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        # ----------------------------------------------------
        # 如果三者都有值，重新驗算
        # ----------------------------------------------------

        if (
            daytrade_volume is not None
            and total_volume is not None
            and total_volume > 0
            and rate is not None
        ):

            expected_rate = round(
                daytrade_volume
                /
                total_volume
                *
                100.0,
                2,
            )

            if (
                abs(
                    float(rate)
                    -
                    expected_rate
                )
                > 0.01
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率公式不一致"
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
        f"✓ "
        f"{len(stocks)} 檔結構驗證通過"
    )

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
            f"❌ chip.json JSON "
            f"錯誤：{exc}"
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

    if len(
        stocks
    ) != expected_count:

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

            return False

        rate = item.get(
            "day_trading_rate"
        )

        if rate is not None:

            if (
                not math.isfinite(
                    float(rate)
                )
                or rate < 0
                or rate > 100
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率異常"
                )

                return False

        daytrade_volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        if (
            daytrade_volume is not None
            and total_volume is not None
            and total_volume > 0
            and rate is not None
        ):

            expected_rate = round(
                daytrade_volume
                /
                total_volume
                *
                100.0,
                2,
            )

            if (
                abs(
                    float(rate)
                    -
                    expected_rate
                )
                > 0.01
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後公式驗證失敗"
                )

                return False

    log(
        f"✓ chip.json 寫入後："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 禁止欄位掃描通過"
    )

    log(
        "✓ 當沖率公式驗證通過"
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
        "  Universe："
        "Data/universe.json"
    )

    log(
        "  Output："
        "Data/chip.json"
    )

    log(
        "  三大法人："
        "TWSE + TPEx 官方資料"
    )

    log(
        "  期間："
        "1D / 5D / 10D / 20D"
    )

    log(
        "  當沖："
        "官方當沖成交股數"
    )

    log(
        "  當沖率："
        "當沖成交股數 / "
        "總成交股數 × 100"
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

    data_date, history = (
        fetch_history(
            HISTORY_DAYS
        )
    )

    if not data_date:

        log(
            "❌ 沒有取得有效法人資料"
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
    # 3. Day trade
    # ========================================================

    daytrade, daytrade_stats = (
        fetch_daytrade(
            data_date,
            securities,
        )
    )

    # --------------------------------------------------------
    # 當沖資料來源品質檢查
    # --------------------------------------------------------

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
        "當沖來源品質："
    )

    log(
        f"  TWSE Universe："
        f"{twse_count}"
    )

    log(
        f"  TWSE 當沖來源："
        f"{daytrade_stats['twse_daytrade_source']}"
    )

    log(
        f"  TWSE 總成交量來源："
        f"{daytrade_stats['twse_total_volume_source']}"
    )

    log(
        f"  TPEx Universe："
        f"{tpex_count}"
    )

    log(
        f"  TPEx 當沖來源："
        f"{daytrade_stats['tpex_daytrade_source']}"
    )

    log(
        f"  TPEx 總成交量來源："
        f"{daytrade_stats['tpex_total_volume_source']}"
    )

    log(
        f"  實際取得當沖成交量："
        f"{daytrade_stats['day_trading_volume']}"
    )

    log(
        f"  實際取得總成交量："
        f"{daytrade_stats['total_volume']}"
    )

    log(
        f"  成功計算當沖率："
        f"{daytrade_stats['day_trading_rate']}"
    )

    log(
        f"  缺總成交量："
        f"{daytrade_stats['rate_missing_volume']}"
    )

    log(
        f"  缺當沖成交量："
        f"{daytrade_stats['rate_missing_daytrade']}"
    )

    log(
        f"  無效公式："
        f"{daytrade_stats['rate_invalid']}"
    )

    # ========================================================
    # 4. Build
    # ========================================================

    section(
        "建立全市場 Chip"
    )

    stocks, statistics = (
        build_chip(
            securities,
            history,
            daytrade,
            data_date,
        )
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
    # 7. Output statistics
    # ========================================================

    stock_count = sum(
        1
        for item in stocks.values()
        if str(
            item["type"]
        ).lower()
        == "stock"
    )

    etf_count = sum(
        1
        for item in stocks.values()
        if str(
            item["type"]
        ).lower()
        == "etf"
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

        "daytrade_statistics":
            daytrade_stats,

        "stocks":
            stocks,
    }

    # ========================================================
    # 8. Final count
    # ========================================================

    if (
        output[
            "universe_count"
        ]
        != len(securities)
    ):

        log(
            "❌ 最終 Universe / "
            "Chip 數量錯誤"
        )

        return 1

    # ========================================================
    # 9. Atomic Write
    # ========================================================

    section(
        "Atomic Write → "
        "Data/chip.json"
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

    log(
        f"  無資料："
        f"{statistics['insufficient']}"
    )

    log("")
    log(
        "當沖資料完整度："
    )

    log(
        f"  當沖成交股數："
        f"{statistics['day_trading_volume']}"
    )

    log(
        f"  總成交股數："
        f"{statistics['total_volume']}"
    )

    log(
        f"  當沖率："
        f"{statistics['day_trading_rate']}"
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
        f"✓ 全市場 "
        f"{len(stocks)} 檔"
    )

    log(
        f"✓ 當沖率成功計算 "
        f"{statistics['day_trading_rate']} 檔"
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