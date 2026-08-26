#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V14.0.0

============================================================
全市場籌碼資料正式版
============================================================

核心資料：

1. 三大法人
   - 1D
   - 5D
   - 10D
   - 20D

2. 當沖
   - TWSE 官方 TWTB4U
   - TPEx 官方 OpenAPI
   - 當沖成交股數
   - 總成交股數
   - 當沖率

3. Universe
   - Data/universe.json
   - Universe 是唯一股票池
   - 不重新分類 Universe

============================================================
重要原則
============================================================

1. Universe 是唯一股票池
2. 全市場處理
3. 不固定測試特定股票
4. 不產生 main_force_*
5. 不使用三大法人倍率估算主力
6. 缺資料 = None
7. 不以 0 冒充缺資料
8. TWSE / TPEx 分開處理
9. 官方 API 整批失敗才停止
10. 當沖率必須由官方個股當沖成交股數 / 官方總成交股數計算
11. Universe / Chip 數量必須一致
12. 寫入前後都驗證
13. Atomic Write
14. 當沖來源全部為 0 時禁止 PASS
15. 不使用舊 HTML 當沖 endpoint
16. TWSE 使用官方 OpenAPI
17. TPEx 使用官方 OpenAPI
18. 不把 API HTTP 200 但空資料視為成功
19. 不把 API 回傳的其他欄位誤當成交量
20. 欄位名稱採動態辨識
21. API 失敗自動 retry
22. 當沖資料與總成交量必須同市場配對

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

VERSION = "V14.0.0"


# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# API
# ============================================================

TWSE_OPENAPI_BASE = (
    "https://openapi.twse.com.tw/v1"
)

TPEX_OPENAPI_BASE = (
    "https://www.tpex.org.tw/openapi/v1"
)


# ------------------------------------------------------------
# 三大法人
# ------------------------------------------------------------

TWSE_INSTITUTIONAL_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/fund/T86"
)


TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)


# ------------------------------------------------------------
# TWSE 官方 OpenAPI
# ------------------------------------------------------------

TWSE_DAYTRADE_URL = (
    TWSE_OPENAPI_BASE
    + "/exchangeReport/TWTB4U"
)


TWSE_DAILY_QUOTES_URL = (
    TWSE_OPENAPI_BASE
    + "/exchangeReport/STOCK_DAY_ALL"
)


# ------------------------------------------------------------
# TPEx 官方 OpenAPI
# ------------------------------------------------------------

TPEX_DAYTRADE_URL = (
    TPEX_OPENAPI_BASE
    + "/tpex_intraday_trading_statistics"
)


TPEX_DAILY_QUOTES_URL = (
    TPEX_OPENAPI_BASE
    + "/tpex_mainboard_daily_close_quotes"
)


# ============================================================
# Network
# ============================================================

REQUEST_TIMEOUT = 30

REQUEST_SLEEP = 0.5

API_RETRIES = 3

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

session.headers.update(
    HEADERS
)


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

    text = str(value).strip().upper()

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
        .replace(" ", "")
    )

    if text in {
        "-",
        "--",
        "---",
        "－",
        "None",
        "null",
        "NULL",
        "N/A",
        "NA",
    }:
        return None

    try:

        number = float(text)

        if not math.isfinite(number):
            return None

        return number

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


def date_iso(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text
        .replace("/", "")
        .replace("-", "")
        .replace(".", "")
    )

    if re.fullmatch(
        r"\d{7}",
        text,
    ):

        try:

            year = (
                int(text[:3])
                + 1911
            )

            month = int(text[3:5])
            day = int(text[5:7])

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except Exception:
            return None

    if re.fullmatch(
        r"\d{8}",
        text,
    ):

        try:

            year = int(text[:4])
            month = int(text[4:6])
            day = int(text[6:8])

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except Exception:
            return None

    return None


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
            f"❌ Universe JSON 解析失敗："
            f"{exc}"
        )

        return []

    items = []

    declared_count = None

    if isinstance(data, dict):

        if data.get(
            "universe_count"
        ) is not None:

            try:

                declared_count = int(
                    data[
                        "universe_count"
                    ]
                )

            except Exception:

                log(
                    "❌ universe_count 無法解析"
                )

                return []

        stocks = data.get(
            "stocks"
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
                    continue

                item = dict(value)

                item["symbol"] = (
                    clean_code(key)
                )

                items.append(item)

        elif isinstance(
            data.get("items"),
            list,
        ):

            items = [
                dict(x)
                for x in data["items"]
                if isinstance(x, dict)
            ]

    elif isinstance(data, list):

        items = [
            dict(x)
            for x in data
            if isinstance(x, dict)
        ]

    if not items:

        log(
            "❌ Universe 沒有可用資料"
        )

        return []

    securities = []

    seen = set()

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
            continue

        if symbol in seen:
            continue

        if not is_valid_symbol(symbol):
            continue

        seen.add(symbol)

        name = clean_name(
            item.get(
                "name",
                symbol,
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

        if market not in {
            "TWSE",
            "TPEX",
        }:

            original = str(
                item.get(
                    "symbol",
                    "",
                )
            ).upper()

            if (
                ".TWO" in original
                or original.endswith("TWO")
            ):

                market = "TPEX"

            elif (
                ".TW" in original
                or original.endswith("TW")
            ):

                market = "TWSE"

            else:

                if symbol.startswith("3"):

                    market = "TPEX"

                else:

                    market = "TWSE"

        raw_type = str(
            item.get(
                "type",
                "",
            )
        ).strip().upper()

        # ----------------------------------------------------
        # 重要：
        # 完整繼承 Universe type
        # 不重新判斷 ETF
        # 不把 BOND 改成 ETF
        # ----------------------------------------------------

        if raw_type in {
            "STOCK",
            "ETF",
            "BOND",
        }:

            sec_type = raw_type

        else:

            sec_type = "STOCK"

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

    # --------------------------------------------------------
    # 數量驗證
    # --------------------------------------------------------

    if declared_count is not None:

        if len(securities) != declared_count:

            log(
                "❌ Universe 數量不一致"
            )

            log(
                f"   header：{declared_count}"
            )

            log(
                f"   parsed：{len(securities)}"
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

    log(
        f"  BOND: {bond_count}"
    )

    log(
        f"  ETF: {etf_count}"
    )

    log(
        f"  STOCK: {stock_count}"
    )

    log("")
    log("Universe Market：")

    log(
        f"  TPEX: {tpex_count}"
    )

    log(
        f"  TWSE: {twse_count}"
    )

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
# HTTP JSON
# ============================================================

def get_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
    retries: int = API_RETRIES,
) -> Optional[Any]:

    last_error = None

    for attempt in range(
        1,
        retries + 1,
    ):

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

                if attempt < retries:

                    time.sleep(
                        attempt
                    )

                    continue

                log(
                    f"      {last_error}"
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
                    f"      {last_error}"
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
                    f"      {last_error}"
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
                f"      {last_error}"
            )

            return None

    return None


# ============================================================
# Generic record normalization
# ============================================================

def normalize_records(
    data: Any,
) -> List[Dict[str, Any]]:

    if isinstance(
        data,
        list,
    ):

        return [
            x
            for x in data
            if isinstance(x, dict)
        ]

    if isinstance(
        data,
        dict,
    ):

        # ----------------------------------------------------
        # OpenAPI 常見：
        # { "data": [...] }
        # ----------------------------------------------------

        for key in (
            "data",
            "Data",
            "result",
            "results",
            "Result",
            "records",
            "Records",
        ):

            value = data.get(key)

            if isinstance(
                value,
                list,
            ):

                return [
                    x
                    for x in value
                    if isinstance(x, dict)
                ]

        # ----------------------------------------------------
        # 某些 API：
        # { "tables": [...] }
        # ----------------------------------------------------

        tables = data.get(
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

                rows = table.get(
                    "data"
                )

                fields = table.get(
                    "fields"
                )

                if (
                    isinstance(rows, list)
                    and isinstance(fields, list)
                ):

                    for row in rows:

                        if not isinstance(
                            row,
                            list,
                        ):
                            continue

                        record = {}

                        for idx, field in enumerate(
                            fields
                        ):

                            if idx < len(row):

                                record[
                                    str(field)
                                ] = row[idx]

                        result.append(
                            record
                        )

            return result

    return []


# ============================================================
# Generic field lookup
# ============================================================

def find_field(
    row: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized = {}

    for key, value in row.items():

        k = (
            str(key)
            .strip()
            .lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("-", "")
            .replace("/", "")
        )

        normalized[k] = value

    for alias in aliases:

        a = (
            alias
            .strip()
            .lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("-", "")
            .replace("/", "")
        )

        if a in normalized:

            return normalized[a]

    return None


def find_code(
    row: Dict[str, Any],
) -> str:

    value = find_field(
        row,
        [
            "Code",
            "code",
            "證券代號",
            "股票代號",
            "證券代碼",
            "股票代碼",
            "SecuritiesCode",
            "SecurityCode",
        ],
    )

    return clean_code(value)


# ============================================================
# TWSE institutional
# ============================================================

def fetch_twse_institutional(
    date_str: str,
) -> Dict[str, float]:

    url = TWSE_INSTITUTIONAL_URL

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

        if not is_valid_symbol(code):

            continue

        net = safe_number(
            row[18]
        )

        if net is None:

            continue

        result[code] = round(
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

    params = {
        "l": "zh-tw",
        "se": "EW",
        "t": "D",
        "d": roc,
    }

    try:

        response = session.get(
            TPEX_INSTITUTIONAL_URL,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            return {}

        text = response.text

    except Exception:

        return {}

    if not text.strip():

        return {}

    from html.parser import HTMLParser

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

    result = {}

    for row in parser.rows:

        if len(row) < 10:

            continue

        code = clean_code(
            row[0]
        )

        if not is_valid_symbol(code):

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

    result.update(tpex)

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

                    history[
                        symbol
                    ].append(value)

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

        attempted += 1

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
# Official TWSE Day Trading
# ============================================================

def fetch_twse_daytrade() -> Dict[
    str,
    float,
]:

    log(
        "TWSE 當沖："
    )

    data = get_json(
        TWSE_DAYTRADE_URL
    )

    rows = normalize_records(
        data
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
                    "tradeShares",
                    "當日沖銷交易成交股數",
                    "當日沖銷交易成交量",
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
        f"  ✓ 當沖資料："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Official TWSE total volume
# ============================================================

def fetch_twse_total_volume() -> Dict[
    str,
    float,
]:

    log(
        "TWSE 總成交量："
    )

    data = get_json(
        TWSE_DAILY_QUOTES_URL
    )

    rows = normalize_records(
        data
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
                    "tradeVolume",
                    "成交股數",
                    "成交量",
                    "Volume",
                    "TradingShares",
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
# Official TPEx Day Trading
# ============================================================

def fetch_tpex_daytrade() -> Dict[
    str,
    float,
]:

    log(
        "TPEx 當沖："
    )

    data = get_json(
        TPEX_DAYTRADE_URL
    )

    rows = normalize_records(
        data
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
                    "TradeVolume",
                    "TradingShares",
                    "DayTradingVolume",
                    "DayTradeVolume",
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
        f"  ✓ 當沖資料："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Official TPEx total volume
# ============================================================

def fetch_tpex_total_volume() -> Dict[
    str,
    float,
]:

    log(
        "TPEx 總成交量："
    )

    data = get_json(
        TPEX_DAILY_QUOTES_URL
    )

    rows = normalize_records(
        data
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
# Day Trading Integration
# ============================================================

def build_daytrade_data(
    securities: List[
        Dict[str, str]
    ],
) -> Tuple[
    Dict[str, Dict[str, Optional[float]]],
    Dict[str, int],
]:

    section(
        "3. 當沖資料"
    )

    twse_day = fetch_twse_daytrade()

    time.sleep(
        REQUEST_SLEEP
    )

    twse_total = fetch_twse_total_volume()

    time.sleep(
        REQUEST_SLEEP
    )

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

        symbol = item["symbol"]

        market = item["market"]

        day_volume = None

        total_volume = None

        if market == "TWSE":

            day_volume = twse_day.get(
                symbol
            )

            total_volume = twse_total.get(
                symbol
            )

        elif market == "TPEX":

            day_volume = tpex_day.get(
                symbol
            )

            total_volume = tpex_total.get(
                symbol
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
                (
                    day_volume
                    / total_volume
                ) * 100.0,
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
            "day_trading_volume": (
                day_volume
            ),
            "total_volume": (
                total_volume
            ),
            "day_trading_rate": (
                rate
            ),
        }

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

    return result, statistics


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
# Build Chip
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

        symbol = item["symbol"]

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

    return stocks, statistics


# ============================================================
# Structure Validation
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

        if not clean_name(
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
# Data Quality Gate
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

    if chip_count != universe_count:

        log(
            "❌ Universe / Chip 數量不一致"
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
    # 市場級別檢查
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

    # --------------------------------------------------------
    # 不要求 2391/2391 全部有當沖
    #
    # 因為 ETF / BOND / 特殊證券可能沒有當沖資料。
    # Gate 的核心是：
    #
    # 來源必須真的有資料
    # 且至少有實際個股能計算
    # --------------------------------------------------------

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
# Universe Verification
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
            f"❌ Universe 重新讀取失敗："
            f"{exc}"
        )

        return False

    expected = None

    actual = None

    if isinstance(data, dict):

        if data.get(
            "universe_count"
        ) is not None:

            try:

                expected = int(
                    data[
                        "universe_count"
                    ]
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
                "❌ fetch_chip Universe 數量錯誤"
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
# Atomic Write
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
# Post Write Verification
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

    valid_rate = 0

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

        if not clean_name(
            item.get(
                "name",
                "",
            )
        ):

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
        "  三大法人：TWSE + TPEx"
    )

    log(
        "  期間：1D / 5D / 10D / 20D"
    )

    log(
        "  當沖：TWSE TWTB4U + TPEx OpenAPI"
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

        log(
            "❌ 為避免破壞既有 chip.json，停止"
        )

        return 1

    if not history:

        log(
            "❌ history 為空"
        )

        return 1

    # ========================================================
    # 3. Day Trade
    # ========================================================

    daytrade, daytrade_statistics = (
        build_daytrade_data(
            securities
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
    # 7. Data Quality Gate
    # ========================================================

    if not data_quality_gate(
        securities,
        stocks,
        daytrade_statistics,
    ):

        return 1

    # ========================================================
    # 8. Output Statistics
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
    # 10. Final pre-write validation
    # ========================================================

    if (
        output["universe_count"]
        != len(securities)
    ):

        log(
            "❌ 最終 Universe / Chip 數量錯誤"
        )

        return 1

    if valid_daytrade <= 0:

        log(
            "❌ 最終驗證：有效當沖率仍為 0"
        )

        return 1

    # ========================================================
    # 11. Atomic Write
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
    # 12. Post Verification
    # ========================================================

    if not verify_written_chip(
        len(securities)
    ):

        return 1

    # ========================================================
    # 13. Final Report
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
        f"✓ 有效當沖率 {valid_daytrade} 檔"
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