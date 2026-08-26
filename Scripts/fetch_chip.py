#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V13.0.0

============================================================
全市場籌碼資料正式版
============================================================

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

重要原則：
------------------------------------------------------------
1. Universe 是唯一股票池
2. Universe type 完整繼承
3. 不自行猜 ETF
4. Bond 不得被改成 ETF
5. 不產生 main_force_*
6. 缺資料 = None
7. 不用 0 冒充缺資料
8. 單一股票缺資料不能破壞整批
9. TWSE / TPEX 分開處理
10. 法人 1D / 5D / 10D / 20D 由每日資料累計
11. 當沖量必須使用正確官方欄位
12. 當沖率 = 當沖成交股數 / 總成交股數 × 100
13. 當沖成交股數不得大於總成交股數
14. 當沖率必須 0~100
15. 當沖來源整批失敗不得宣稱 PASS
16. Universe / Chip 數量必須一致
17. Atomic Write
18. 寫入前後都驗證
19. 最終輸出完整診斷統計

資料來源：
------------------------------------------------------------
TWSE：
    https://www.twse.com.tw/rwd/zh/fund/T86
    https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX
    https://www.twse.com.tw/rwd/zh/exchangeReport/TWTB4U

TPEx：
    https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading
    https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
    https://www.tpex.org.tw/openapi/v1/tpex_intraday_trading_statistics

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

VERSION = "V13.0.0"


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


TWSE_BASE = "https://www.twse.com.tw/rwd/zh"

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

    if text.endswith(".TWO"):
        text = text[:-4]

    elif text.endswith(".TW"):
        text = text[:-3]

    return text


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
        "不適用",
    }:
        return None

    try:

        number = float(text)

        if not math.isfinite(number):
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
) -> Optional[requests.Response]:

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            log(
                f"      HTTP {response.status_code}: "
                f"{url}"
            )

            return None

        return response

    except Exception as exc:

        log(
            f"      HTTP ERROR: {exc}"
        )

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

    except Exception as exc:

        log(
            f"      JSON ERROR: {exc}"
        )

        return None


# ============================================================
# Generic JSON helpers
# ============================================================

def extract_rows(
    data: Any,
) -> List[Any]:

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "data",
        "Data",
        "result",
        "results",
    ):

        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


def normalize_key_map(
    row: Dict[str, Any],
) -> Dict[str, str]:

    result = {}

    for key in row.keys():

        result[
            normalize_field(key)
        ] = key

    return result


def find_field(
    row: Dict[str, Any],
    exact: List[str],
    contains: Optional[
        List[str]
    ] = None,
) -> Optional[str]:

    key_map = normalize_key_map(
        row
    )

    for wanted in exact:

        normalized = normalize_field(
            wanted
        )

        if normalized in key_map:

            return key_map[
                normalized
            ]

    if contains:

        for wanted in contains:

            normalized = normalize_field(
                wanted
            )

            for key, original in (
                key_map.items()
            ):

                if normalized in key:

                    return original

    return None


def row_code(
    row: Dict[str, Any],
) -> str:

    key = find_field(
        row,
        [
            "Code",
            "StockCode",
            "SecuritiesCompanyCode",
            "LocalCode",
            "證券代號",
            "股票代號",
            "代號",
            "證券代碼",
        ],
        [
            "證券代號",
            "股票代號",
            "StockCode",
            "Code",
            "代號",
        ],
    )

    if key is None:
        return ""

    return clean_code(
        row.get(key)
    )


def row_name(
    row: Dict[str, Any],
) -> str:

    key = find_field(
        row,
        [
            "Name",
            "StockName",
            "SecuritiesName",
            "證券名稱",
            "股票名稱",
            "名稱",
        ],
        [
            "證券名稱",
            "股票名稱",
            "名稱",
            "Name",
        ],
    )

    if key is None:
        return ""

    return clean_name(
        row.get(key)
    )


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[
    Dict[str, str]
]:

    section(
        "1. Universe 載入與分類驗證"
    )

    if not UNIVERSE_FILE.exists():

        log(
            "❌ 找不到 Data/universe.json"
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

        expected_count = (
            int(declared)
            if declared is not None
            else len(stocks)
        )

    except Exception:

        log(
            "❌ universe_count 無法解析"
        )

        return []

    if expected_count != len(stocks):

        log(
            "❌ Universe 本身數量矛盾"
        )

        log(
            f"   universe_count = "
            f"{expected_count}"
        )

        log(
            f"   stocks = "
            f"{len(stocks)}"
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

    for key, value in stocks.items():

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
                "❌ Universe 存在空 symbol"
            )

            return []

        if symbol in seen:

            log(
                f"❌ Universe 重複 symbol："
                f"{symbol}"
            )

            return []

        seen.add(symbol)

        # ----------------------------------------------------
        # 重要：
        # type 完全繼承 Universe。
        #
        # 不重新猜測 ETF。
        # 不把 Bond 轉 ETF。
        # 不做任何重新分類。
        # ----------------------------------------------------

        raw_type = value.get(
            "type"
        )

        if raw_type is None:

            raw_type = "Unknown"

        security_type = (
            str(raw_type)
            .strip()
        )

        if not security_type:

            security_type = "Unknown"

        market = str(
            value.get(
                "market",
                "",
            )
        ).strip().upper()

        if market not in {
            "TWSE",
            "TPEX",
        }:

            original = str(
                value.get(
                    "full_symbol",
                    "",
                )
            ).upper()

            if ".TWO" in original:

                market = "TPEX"

            elif ".TW" in original:

                market = "TWSE"

            else:

                log(
                    f"⚠️ {symbol} "
                    f"Universe market 不明，"
                    f"保留為 UNKNOWN"
                )

                market = "UNKNOWN"

        full_symbol = str(
            value.get(
                "full_symbol",
                "",
            )
        ).strip()

        if not full_symbol:

            if market == "TPEX":

                full_symbol = (
                    f"{symbol}.TWO"
                )

            elif market == "TWSE":

                full_symbol = (
                    f"{symbol}.TW"
                )

            else:

                full_symbol = symbol

        name = clean_name(
            value.get(
                "name",
                symbol,
            )
        )

        securities.append(
            {
                "symbol": symbol,
                "full_symbol": full_symbol,
                "name": name or symbol,
                "market": market,
                "type": security_type,
            }
        )

        type_counts[
            security_type
        ] = (
            type_counts.get(
                security_type,
                0,
            )
            + 1
        )

        market_counts[
            market
        ] = (
            market_counts.get(
                market,
                0,
            )
            + 1
        )

    if len(securities) != expected_count:

        log(
            "❌ Universe 載入後數量錯誤"
        )

        return []

    log(
        f"✓ Universe："
        f"{len(securities)} 檔"
    )

    log("")
    log("Universe Type：")

    for key in sorted(
        type_counts
    ):

        log(
            f"  {key}: "
            f"{type_counts[key]}"
        )

    log("")
    log("Universe Market：")

    for key in sorted(
        market_counts
    ):

        log(
            f"  {key}: "
            f"{market_counts[key]}"
        )

    # --------------------------------------------------------
    # 特別確認：
    # 不允許程式自行產生 ETF 數量。
    # --------------------------------------------------------

    log("")
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
# TWSE Institutional
# ============================================================

def fetch_twse_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    url = (
        f"{TWSE_BASE}/fund/T86"
    )

    params = {
        "response": "json",
        "date": yyyymmdd(
            date_obj
        ),
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

    result: Dict[
        str,
        float
    ] = {}

    rows = extract_rows(
        data
    )

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):

            continue

        symbol = row_code(
            row
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        # ----------------------------------------------------
        # 優先找「三大法人買賣超」
        # ----------------------------------------------------

        key = find_field(
            row,
            [
                "三大法人買賣超",
                "ThreeInstitutionalInvestorsNetBuySell",
                "ThreeInstitutionalInvestorsNet",
                "TotalNet",
                "Net",
            ],
            [
                "三大法人買賣超",
                "三大法人",
                "NetBuySell",
                "Net",
            ],
        )

        value = None

        if key is not None:

            value = safe_number(
                row.get(key)
            )

        # ----------------------------------------------------
        # 若沒有總欄位，才由外資+投信+自營商累加
        # ----------------------------------------------------

        if value is None:

            components = []

            for aliases in (
                [
                    "外資及陸資買賣超",
                    "外資買賣超",
                    "ForeignInvestmentNet",
                    "ForeignNet",
                ],
                [
                    "投信買賣超",
                    "InvestmentTrustNet",
                    "TrustNet",
                ],
                [
                    "自營商買賣超",
                    "DealerNet",
                    "DealerNetBuySell",
                ],
            ):

                key2 = find_field(
                    row,
                    aliases,
                    aliases,
                )

                if key2 is not None:

                    number = safe_number(
                        row.get(key2)
                    )

                    if number is not None:

                        components.append(
                            number
                        )

            if len(
                components
            ) == 3:

                value = sum(
                    components
                )

        if value is None:

            continue

        # TPEx 官方資料通常為股數。
        # 若資料本身已經是張數，欄位名稱會包含「張」。
        key_text = (
            normalize_field(key)
            if key
            else ""
        )

        if "張" in key_text:

            lots = value

        else:

            lots = value / 1000.0

        result[symbol] = round(
            lots,
            2,
        )

    return result


# ============================================================
# Daily institutional
# ============================================================

def fetch_daily_institutional(
    date_obj: datetime,
) -> Dict[str, float]:

    twse = (
        fetch_twse_institutional(
            date_obj
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

    for symbol, value in (
        tpex.items()
    ):

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
        f"2. 最近 {days} 個交易日三大法人"
    )

    history: Dict[
        str,
        List[float]
    ] = {}

    successful_days = 0

    attempted_days = 0

    latest_date = None

    current = now_taiwan().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    while (
        successful_days < days
        and attempted_days
        < MAX_LOOKBACK_DAYS
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

            data = (
                fetch_daily_institutional(
                    current
                )
            )

            if data:

                if latest_date is None:

                    latest_date = (
                        date_text
                    )

                successful_days += 1

                for symbol, value in (
                    data.items()
                ):

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
                    "      ⚠️ 本日沒有有效法人資料"
                )

            time.sleep(
                REQUEST_SLEEP
            )

        current -= timedelta(
            days=1
        )

        attempted_days += 1

    if successful_days == 0:

        return None, {}

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
# TWSE HTML parser
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
                self.current_cell
                or []
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


def parse_html_tables(
    text: str,
) -> List[
    List[str]
]:

    parser = TableParser()

    try:

        parser.feed(
            text
        )

    except Exception:

        return []

    return parser.rows


# ============================================================
# TWSE Day Trading
# ============================================================

def fetch_twse_daytrade(
    date_obj: datetime,
) -> Dict[
    str,
    Dict[str, float]
]:

    """
    TWSE：
        exchangeReport/TWTB4U

    重要：
        不再從 row[1:] 找第一個數字。

    改成：
        先找欄位名稱，
        再依欄位名稱取得當沖成交股數。

    若 API JSON 失敗：
        使用 HTML fallback。
    """

    date_text = yyyymmdd(
        date_obj
    )

    url = (
        f"{TWSE_BASE}/"
        "exchangeReport/TWTB4U"
    )

    params = {
        "response": "json",
        "date": date_text,
        "selectType": "ALL",
    }

    data = get_json(
        url,
        params,
    )

    result: Dict[
        str,
        Dict[str, float]
    ] = {}

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    if isinstance(
        data,
        dict,
    ):

        rows = data.get(
            "data",
            [],
        )

        fields = data.get(
            "fields",
            [],
        )

        if (
            isinstance(rows, list)
            and isinstance(fields, list)
        ):

            for raw in rows:

                if not isinstance(
                    raw,
                    list,
                ):

                    continue

                if len(raw) != len(
                    fields
                ):

                    continue

                row = {
                    str(fields[i]):
                    raw[i]
                    for i in range(
                        len(fields)
                    )
                }

                symbol = row_code(
                    row
                )

                if not is_valid_symbol(
                    symbol
                ):

                    continue

                day_volume = extract_daytrade_volume(
                    row
                )

                if day_volume is None:

                    continue

                result[symbol] = {
                    "day_trading_volume":
                        day_volume
                }

    if result:

        return result

    # --------------------------------------------------------
    # HTML fallback
    # --------------------------------------------------------

    params = {
        "response": "html",
        "date": date_text,
        "selectType": "ALL",
    }

    response = get_response(
        url,
        params,
    )

    if response is None:

        return {}

    rows = parse_html_tables(
        response.text
    )

    if not rows:

        return {}

    header = None

    for row in rows:

        normalized = [
            normalize_field(x)
            for x in row
        ]

        if any(
            "當沖" in x
            for x in normalized
        ):

            header = normalized

            continue

        if header is None:

            continue

        if len(row) != len(
            header
        ):

            continue

        item = {
            header[i]:
            row[i]
            for i in range(
                len(header)
            )
        }

        symbol = row_code(
            item
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        volume = extract_daytrade_volume(
            item
        )

        if volume is None:

            continue

        result[symbol] = {
            "day_trading_volume":
                volume
        }

    return result


# ============================================================
# Extract day-trading volume
# ============================================================

def extract_daytrade_volume(
    row: Dict[str, Any],
) -> Optional[float]:

    """
    僅接受明確與「當沖成交量」相關的欄位。

    禁止：
        找第一個數字
        找任意 volume
        找 row[1]

    優先順序：
        當沖成交股數
        當沖成交量
        當日沖銷成交股數
        DayTradingVolume
    """

    key = find_field(
        row,
        [
            "當沖成交股數",
            "當沖成交量",
            "當日沖銷成交股數",
            "當日沖銷成交量",
            "DayTradingVolume",
            "DayTradeVolume",
            "DayTradingShares",
        ],
        [
            "當沖成交股數",
            "當沖成交量",
            "當日沖銷成交股數",
            "當日沖銷成交量",
            "DayTradingVolume",
            "DayTradeVolume",
        ],
    )

    if key is None:

        return None

    value = safe_number(
        row.get(key)
    )

    if value is None:
        return None

    if value < 0:
        return None

    # TWSE 當沖資料通常以股數提供。
    # 統一輸出「股」。
    return round(
        value,
        2,
    )


# ============================================================
# TWSE total volume
# ============================================================

def fetch_twse_total_volume(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TWSE MI_INDEX：
        每日收盤行情

    使用：
        成交股數

    不使用：
        成交金額
        成交筆數
    """

    url = (
        f"{TWSE_BASE}/"
        "afterTrading/MI_INDEX"
    )

    params = {
        "response": "json",
        "date": yyyymmdd(
            date_obj
        ),
        "type": "ALLBUT0999",
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

    tables = data.get(
        "tables",
        [],
    )

    if not isinstance(
        tables,
        list,
    ):

        return result

    for table in tables:

        if not isinstance(
            table,
            dict,
        ):

            continue

        fields = table.get(
            "fields",
            []
        )

        rows = table.get(
            "data",
            []
        )

        if not isinstance(
            fields,
            list,
        ):

            continue

        if not isinstance(
            rows,
            list,
        ):

            continue

        normalized_fields = [
            normalize_field(x)
            for x in fields
        ]

        code_index = find_index(
            normalized_fields,
            [
                "證券代號",
                "股票代號",
                "代號",
            ],
        )

        volume_index = find_index(
            normalized_fields,
            [
                "成交股數",
                "成交量",
                "TradeVolume",
            ],
        )

        if (
            code_index is None
            or volume_index is None
        ):

            continue

        for raw in rows:

            if not isinstance(
                raw,
                list,
            ):

                continue

            if (
                code_index >= len(raw)
                or volume_index >= len(raw)
            ):

                continue

            symbol = clean_code(
                raw[code_index]
            )

            if not is_valid_symbol(
                symbol
            ):

                continue

            volume = safe_number(
                raw[volume_index]
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


def find_index(
    fields: List[str],
    candidates: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field(x)
        for x in fields
    ]

    for candidate in candidates:

        wanted = normalize_field(
            candidate
        )

        if wanted in normalized:

            return normalized.index(
                wanted
            )

    return None


# ============================================================
# TPEx Day Trading
# ============================================================

def fetch_tpex_daytrade() -> Dict[
    str,
    Dict[str, float]
]:

    """
    TPEx 官方 OpenAPI：

        tpex_intraday_trading_statistics

    動態辨識欄位。

    不使用：
        row[0]
        row[1]
        第一個數字
    """

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_intraday_trading_statistics"
    )

    data = get_json(
        url
    )

    result: Dict[
        str,
        Dict[str, float]
    ] = {}

    rows = extract_rows(
        data
    )

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):

            continue

        symbol = row_code(
            row
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        volume = extract_tpex_daytrade_volume(
            row
        )

        if volume is None:

            continue

        result[symbol] = {
            "day_trading_volume":
                volume
        }

    return result


def extract_tpex_daytrade_volume(
    row: Dict[str, Any],
) -> Optional[float]:

    key = find_field(
        row,
        [
            "當沖成交股數",
            "當沖成交量",
            "當日沖銷成交股數",
            "當日沖銷成交量",
            "DayTradingVolume",
            "DayTradeVolume",
            "DayTradingShares",
            "TradingVolume",
        ],
        [
            "當沖成交股數",
            "當沖成交量",
            "當日沖銷成交股數",
            "當日沖銷成交量",
            "DayTradingVolume",
            "DayTradeVolume",
        ],
    )

    if key is None:

        return None

    value = safe_number(
        row.get(key)
    )

    if value is None:
        return None

    if value < 0:
        return None

    # 若欄位明確為千股，轉成股。
    key_text = normalize_field(
        key
    )

    if (
        "千股" in key_text
        or "Thousand" in key_text
    ):

        value *= 1000

    return round(
        value,
        2,
    )


# ============================================================
# TPEx Total Volume
# ============================================================

def fetch_tpex_total_volume() -> Dict[
    str,
    float
]:

    """
    TPEx：
        tpex_mainboard_daily_close_quotes

    動態尋找：
        成交股數 / TradingVolume
    """

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_mainboard_daily_close_quotes"
    )

    data = get_json(
        url
    )

    result: Dict[
        str,
        float
    ] = {}

    rows = extract_rows(
        data
    )

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):

            continue

        symbol = row_code(
            row
        )

        if not is_valid_symbol(
            symbol
        ):

            continue

        key = find_field(
            row,
            [
                "成交股數",
                "成交量",
                "TradingVolume",
                "TradeVolume",
                "Volume",
            ],
            [
                "成交股數",
                "成交量",
                "TradingVolume",
                "TradeVolume",
            ],
        )

        if key is None:

            continue

        value = safe_number(
            row.get(key)
        )

        if value is None:
            continue

        if value < 0:
            continue

        key_text = normalize_field(
            key
        )

        if (
            "千股" in key_text
            or "Thousand" in key_text
        ):

            value *= 1000

        result[symbol] = round(
            value,
            2,
        )

    return result


# ============================================================
# Day trading package
# ============================================================

def fetch_daytrade_package(
    data_date: str,
    securities: List[
        Dict[str, str]
    ],
) -> Tuple[
    Dict[str, Dict[str, float]],
    Dict[str, int],
]:

    section(
        "3. 當沖資料"
    )

    try:

        date_obj = datetime.strptime(
            data_date,
            "%Y-%m-%d",
        )

    except Exception:

        log(
            f"❌ data_date 無法解析："
            f"{data_date}"
        )

        return {}, {
            "twse_daytrade": 0,
            "twse_total": 0,
            "tpex_daytrade": 0,
            "tpex_total": 0,
            "valid": 0,
            "invalid": 0,
        }

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    log("TWSE 當沖：")

    twse_daytrade = (
        fetch_twse_daytrade(
            date_obj
        )
    )

    log(
        f"  ✓ 當沖資料："
        f"{len(twse_daytrade)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    log("TWSE 總成交量：")

    twse_total = (
        fetch_twse_total_volume(
            date_obj
        )
    )

    log(
        f"  ✓ 總成交量："
        f"{len(twse_total)} 檔"
    )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    log("")
    log("TPEx 當沖：")

    tpex_daytrade = (
        fetch_tpex_daytrade()
    )

    log(
        f"  ✓ 當沖資料："
        f"{len(tpex_daytrade)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    log("TPEx 總成交量：")

    tpex_total = (
        fetch_tpex_total_volume()
    )

    log(
        f"  ✓ 總成交量："
        f"{len(tpex_total)} 檔"
    )

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    package: Dict[
        str,
        Dict[str, float]
    ] = {}

    valid = 0

    invalid = 0

    for item in securities:

        symbol = item[
            "symbol"
        ]

        market = item[
            "market"
        ]

        if market == "TWSE":

            day_data = (
                twse_daytrade.get(
                    symbol
                )
            )

            total = (
                twse_total.get(
                    symbol
                )
            )

        elif market == "TPEX":

            day_data = (
                tpex_daytrade.get(
                    symbol
                )
            )

            total = (
                tpex_total.get(
                    symbol
                )
            )

        else:

            continue

        day_volume = None

        if isinstance(
            day_data,
            dict,
        ):

            day_volume = safe_number(
                day_data.get(
                    "day_trading_volume"
                )
            )

        total_volume = safe_number(
            total
        )

        if (
            day_volume is None
            or total_volume is None
        ):

            continue

        if total_volume <= 0:

            continue

        if day_volume < 0:

            continue

        # ----------------------------------------------------
        # 核心防錯：
        #
        # 當沖成交量不可能大於該股票總成交量。
        # ----------------------------------------------------

        if day_volume > (
            total_volume + 1e-9
        ):

            invalid += 1

            continue

        rate = (
            day_volume
            / total_volume
            * 100.0
        )

        if rate < 0 or rate > 100:

            invalid += 1

            continue

        package[symbol] = {

            "day_trading_volume":
                round(
                    day_volume,
                    2,
                ),

            "total_volume":
                round(
                    total_volume,
                    2,
                ),

            "day_trading_rate":
                round(
                    rate,
                    2,
                ),
        }

        valid += 1

    statistics = {

        "twse_daytrade":
            len(twse_daytrade),

        "twse_total":
            len(twse_total),

        "tpex_daytrade":
            len(tpex_daytrade),

        "tpex_total":
            len(tpex_total),

        "valid":
            valid,

        "invalid":
            invalid,
    }

    log("")
    log(
        "當沖整合結果："
    )

    log(
        f"  TWSE 當沖來源："
        f"{statistics['twse_daytrade']}"
    )

    log(
        f"  TWSE 總成交量："
        f"{statistics['twse_total']}"
    )

    log(
        f"  TPEx 當沖來源："
        f"{statistics['tpex_daytrade']}"
    )

    log(
        f"  TPEx 總成交量："
        f"{statistics['tpex_total']}"
    )

    log(
        f"  有效當沖率："
        f"{statistics['valid']}"
    )

    log(
        f"  無效資料："
        f"{statistics['invalid']}"
    )

    return (
        package,
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
                    f"❌ 禁止欄位："
                    f"{symbol}.{field}"
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
    daytrade: Dict[
        str,
        Dict[str, float]
    ],
) -> Tuple[
    Dict[
        str,
        Dict[str, Any]
    ],
    Dict[str, int],
]:

    section(
        "4. 建立 Chip"
    )

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    complete_1d = 0

    complete_5d = 0

    complete_10d = 0

    complete_20d = 0

    no_institutional = 0

    daytrade_valid = 0

    daytrade_missing = 0

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
            no_institutional += 1

        day = daytrade.get(
            symbol,
            {},
        )

        if not isinstance(
            day,
            dict,
        ):

            day = {}

        day_volume = safe_number(
            day.get(
                "day_trading_volume"
            )
        )

        total_volume = safe_number(
            day.get(
                "total_volume"
            )
        )

        day_rate = safe_number(
            day.get(
                "day_trading_rate"
            )
        )

        if (
            day_volume is not None
            and total_volume is not None
            and day_rate is not None
        ):

            daytrade_valid += 1

        else:

            daytrade_missing += 1

        # ----------------------------------------------------
        # type 完整繼承 Universe。
        # ----------------------------------------------------

        stocks[symbol] = {

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

            "institutional_1d":
                inst_1d,

            "institutional_5d":
                inst_5d,

            "institutional_10d":
                inst_10d,

            "institutional_20d":
                inst_20d,

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

        "no_institutional":
            no_institutional,

        "daytrade_valid":
            daytrade_valid,

        "daytrade_missing":
            daytrade_missing,
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
            f"❌ Universe 重讀失敗："
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

    try:

        expected = int(
            expected
        )

    except Exception:

        expected = len(
            stocks
        )

    if expected != len(
        stocks
    ):

        log(
            "❌ Universe 原始資料數量錯誤"
        )

        return False

    if len(
        securities
    ) != len(
        stocks
    ):

        log(
            "❌ Universe / fetch_chip "
            "數量不一致"
        )

        log(
            f"   Universe："
            f"{len(stocks)}"
        )

        log(
            f"   fetch_chip："
            f"{len(securities)}"
        )

        return False

    return True


# ============================================================
# Structural validation
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

            errors += len(
                missing
            )

            log(
                f"❌ {symbol} 缺欄位："
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
                f"❌ {symbol} name 空白"
            )

        # ----------------------------------------------------
        # 不限制 type 為 Stock / ETF。
        #
        # Universe 可以有：
        # Stock / ETF / Bond / ETN / Warrant...
        #
        # fetch_chip 必須完整保留。
        # ----------------------------------------------------

        if not isinstance(
            item.get("type"),
            str,
        ):

            errors += 1

            log(
                f"❌ {symbol} type 無效"
            )

        market = item.get(
            "market"
        )

        if market not in {
            "TWSE",
            "TPEX",
            "UNKNOWN",
        }:

            errors += 1

            log(
                f"❌ {symbol} market 無效"
            )

        # ----------------------------------------------------
        # 當沖資料品質
        # ----------------------------------------------------

        day_volume = safe_number(
            item.get(
                "day_trading_volume"
            )
        )

        total_volume = safe_number(
            item.get(
                "total_volume"
            )
        )

        rate = safe_number(
            item.get(
                "day_trading_rate"
            )
        )

        if day_volume is not None:

            if day_volume < 0:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"day_trading_volume < 0"
                )

        if total_volume is not None:

            if total_volume < 0:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"total_volume < 0"
                )

        if (
            day_volume is not None
            and total_volume is not None
        ):

            if day_volume > (
                total_volume + 1e-9
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖量 > 總成交量"
                )

        if rate is not None:

            if rate < 0 or rate > 100:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率超出 0~100"
                )

    if not scan_forbidden_fields(
        stocks
    ):

        errors += 1

    if errors:

        log("")
        log(
            f"❌ 結構驗證 FAIL："
            f"{errors} 個錯誤"
        )

        return False

    log(
        f"✓ {len(stocks)} 檔結構驗證 PASS"
    )

    return True


# ============================================================
# Data quality gate
# ============================================================

def validate_data_quality(
    stocks: Dict[
        str,
        Dict[str, Any]
    ],
    daytrade_stats: Dict[
        str,
        int
    ],
) -> bool:

    section(
        "6. 資料品質 Gate"
    )

    errors = 0

    valid_daytrade = sum(
        1
        for item in stocks.values()
        if (
            item.get(
                "day_trading_volume"
            )
            is not None
            and item.get(
                "total_volume"
            )
            is not None
            and item.get(
                "day_trading_rate"
            )
            is not None
        )
    )

    total = len(
        stocks
    )

    log(
        f"Universe / Chip："
        f"{total} / {total}"
    )

    log(
        f"有效當沖資料："
        f"{valid_daytrade}"
    )

    log(
        f"缺當沖資料："
        f"{total - valid_daytrade}"
    )

    # --------------------------------------------------------
    # 最重要：
    # 如果兩個市場的當沖來源全部失敗，
    # 不准 CHIP BUILD PASS。
    # --------------------------------------------------------

    twse_source = (
        daytrade_stats.get(
            "twse_daytrade",
            0,
        )
    )

    tpex_source = (
        daytrade_stats.get(
            "tpex_daytrade",
            0,
        )
    )

    if (
        twse_source == 0
        and tpex_source == 0
    ):

        log("")
        log(
            "❌ TWSE + TPEx 當沖來源皆為 0"
        )

        log(
            "❌ 禁止建立 PASS"
        )

        errors += 1

    # --------------------------------------------------------
    # 至少應該存在有效資料。
    # --------------------------------------------------------

    if valid_daytrade == 0:

        log(
            "❌ 全市場有效當沖資料 = 0"
        )

        errors += 1

    # --------------------------------------------------------
    # 每筆資料再次檢查。
    # --------------------------------------------------------

    for symbol, item in (
        stocks.items()
    ):

        day_volume = safe_number(
            item.get(
                "day_trading_volume"
            )
        )

        total_volume = safe_number(
            item.get(
                "total_volume"
            )
        )

        rate = safe_number(
            item.get(
                "day_trading_rate"
            )
        )

        if (
            day_volume is None
            or total_volume is None
            or rate is None
        ):

            continue

        if (
            day_volume > total_volume
        ):

            log(
                f"❌ {symbol} "
                f"當沖量大於總量"
            )

            errors += 1

        if not (
            0 <= rate <= 100
        ):

            log(
                f"❌ {symbol} "
                f"當沖率非法"
            )

            errors += 1

        expected_rate = (
            day_volume
            / total_volume
            * 100
            if total_volume > 0
            else None
        )

        if expected_rate is not None:

            if abs(
                expected_rate - rate
            ) > 0.02:

                log(
                    f"❌ {symbol} "
                    f"當沖率計算錯誤"
                )

                errors += 1

    if errors:

        log("")
        log(
            f"❌ 資料品質 Gate FAIL："
            f"{errors}"
        )

        return False

    log("")
    log(
        "✓ 資料品質 Gate PASS"
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
        "7. chip.json 寫入後驗證"
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
            "❌ 根節點不是 object"
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
            "❌ stocks 不是 object"
        )

        return False

    if len(stocks) != expected_count:

        log(
            "❌ Chip 數量錯誤"
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

    # --------------------------------------------------------
    # type 不得被修改成只有 Stock/ETF。
    # 只要求存在且與 Universe 是合法字串。
    # --------------------------------------------------------

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

            log(
                f"❌ {symbol} "
                f"symbol 寫入後錯誤"
            )

            return False

        if not clean_name(
            item.get(
                "name",
                "",
            )
        ):

            log(
                f"❌ {symbol} "
                f"name 寫入後為空"
            )

            return False

        if not isinstance(
            item.get(
                "type"
            ),
            str,
        ):

            log(
                f"❌ {symbol} "
                f"type 寫入後錯誤"
            )

            return False

        day_volume = safe_number(
            item.get(
                "day_trading_volume"
            )
        )

        total_volume = safe_number(
            item.get(
                "total_volume"
            )
        )

        rate = safe_number(
            item.get(
                "day_trading_rate"
            )
        )

        if (
            day_volume is not None
            and total_volume is not None
        ):

            if day_volume > (
                total_volume + 1e-9
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖量 > 總量"
                )

                return False

        if rate is not None:

            if rate < 0 or rate > 100:

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率非法"
                )

                return False

    log(
        f"✓ chip.json："
        f"{len(stocks)} 檔"
    )

    log(
        "✓ 禁止 main_force_*"
    )

    log(
        "✓ type 欄位存在"
    )

    log(
        "✓ 當沖資料範圍驗證通過"
    )

    return True


# ============================================================
# Main
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
        "  當沖：TWSE + TPEx 官方資料"
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
    # 2. Institutional history
    # ========================================================

    data_date, history = (
        fetch_history(
            HISTORY_DAYS
        )
    )

    if not data_date:

        log("")
        log(
            "❌ 完全沒有取得法人資料"
        )

        log(
            "❌ 停止，避免覆蓋既有 chip.json"
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
        fetch_daytrade_package(
            data_date,
            securities,
        )
    )

    # ========================================================
    # 4. Build
    # ========================================================

    stocks, statistics = (
        build_chip(
            securities,
            history,
            data_date,
            daytrade,
        )
    )

    # ========================================================
    # 5. Count
    # ========================================================

    if len(stocks) != len(
        securities
    ):

        log(
            "❌ Chip / Universe 數量不一致"
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
    # 7. Data quality Gate
    # ========================================================

    if not validate_data_quality(
        stocks,
        daytrade_stats,
    ):

        log("")
        log(
            "❌ 本次 BUILD 不允許 PASS"
        )

        log(
            "❌ 保留既有 chip.json"
        )

        return 1

    # ========================================================
    # 8. Counts
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

        security_type = item[
            "type"
        ]

        market = item[
            "market"
        ]

        type_counts[
            security_type
        ] = (
            type_counts.get(
                security_type,
                0,
            )
            + 1
        )

        market_counts[
            market
        ] = (
            market_counts.get(
                market,
                0,
            )
            + 1
        )

    # ========================================================
    # 9. Output
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
            daytrade_stats,

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
    # 10. Atomic write
    # ========================================================

    section(
        "8. Atomic Write → Data/chip.json"
    )

    if not atomic_write(
        output
    ):

        return 1

    log(
        f"✓ 寫入完成："
        f"{CHIP_FILE}"
    )

    # ========================================================
    # 11. Post write
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
        - start_time
    )

    section(
        "9. 最終執行結果"
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
        f"✓ 資料日："
        f"{data_date}"
    )

    log("")

    log(
        "Type："
    )

    for key in sorted(
        type_counts
    ):

        log(
            f"  {key}: "
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
            f"  {key}: "
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
        "當沖資料："
    )

    log(
        f"  TWSE source："
        f"{daytrade_stats['twse_daytrade']}"
    )

    log(
        f"  TWSE volume："
        f"{daytrade_stats['twse_total']}"
    )

    log(
        f"  TPEx source："
        f"{daytrade_stats['tpex_daytrade']}"
    )

    log(
        f"  TPEx volume："
        f"{daytrade_stats['tpex_total']}"
    )

    log(
        f"  有效當沖率："
        f"{statistics['daytrade_valid']}"
    )

    log(
        f"  缺當沖資料："
        f"{statistics['daytrade_missing']}"
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