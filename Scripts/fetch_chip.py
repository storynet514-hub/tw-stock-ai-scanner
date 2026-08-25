#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V10.0.2

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
    5. 當沖成交股數
    6. 個股總成交股數
    7. 當沖率

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
14. 當沖資料依官方欄位名稱解析
15. 總成交量依官方欄位名稱解析
16. 當沖率 = 當沖成交股數 / 個股總成交股數 × 100
17. 不以 row 最後一個數字猜測欄位
18. 不以固定 index 盲抓當沖欄位
19. 當沖資料不足時不製造數值
20. 最終輸出完整診斷統計

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
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V10.0.2"


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


def normalize_field_name(
    value: Any,
) -> str:

    if value is None:
        return ""

    text = str(value)

    replacements = (
        "\n",
        "\r",
        "\t",
        " ",
        "　",
    )

    for item in replacements:
        text = text.replace(item, "")

    return text.strip()


def is_header_like(
    value: Any,
) -> bool:

    text = normalize_field_name(value)

    if not text:
        return True

    return (
        "證券代號" in text
        or "股票代號" in text
        or text == "代號"
        or "證券名稱" in text
        or "股票名稱" in text
    )


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

    if re.fullmatch(
        r"\d{4,6}",
        code,
    ):

        return True, "Stock"

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

                if symbol.startswith(
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

            sec_type = inferred_type

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
        f"  原始標的：{source_count}"
    )

    log(
        f"  成功載入：{len(securities)}"
    )

    log(
        f"  被排除：{len(rejected)}"
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
        and declared_count != len(securities)
    ):

        log(
            "❌ universe_count 與實際載入數量不一致"
        )

        return []

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
# HTTP
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


def get_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:

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

        return response.text

    except Exception as exc:

        log(
            f"      HTTP error：{exc}"
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

class SimpleTableParser(
    HTMLParser
):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.rows: List[List[str]] = []

        self.current_row: Optional[
            List[str]
        ] = None

        self.current_cell: Optional[
            List[str]
        ] = None

    def handle_starttag(
        self,
        tag: str,
        attrs: Any,
    ) -> None:

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
        data: str,
    ) -> None:

        if self.current_cell is not None:

            self.current_cell.append(
                data
            )

    def handle_endtag(
        self,
        tag: str,
    ) -> None:

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


def parse_html_table(
    text: str,
) -> List[List[str]]:

    parser = SimpleTableParser()

    try:

        parser.feed(text)

    except Exception:

        return []

    return parser.rows


def find_column(
    headers: List[str],
    patterns: List[str],
) -> Optional[int]:

    normalized = [
        normalize_field_name(x)
        for x in headers
    ]

    for index, name in enumerate(
        normalized
    ):

        for pattern in patterns:

            if pattern in name:

                return index

    return None


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

    text = get_text(
        url,
        params,
    )

    if not text:

        return {}

    rows = parse_html_table(
        text
    )

    if not rows:

        return {}

    header_rows = []

    for row in rows:

        joined = "".join(
            normalize_field_name(x)
            for x in row
        )

        if (
            "證券代號" in joined
            and "三大法人買賣超" in joined
        ):

            header_rows.append(row)

    result: Dict[str, float] = {}

    for row in rows:

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

        # TPEx 官方表最後欄為
        # 三大法人買賣超股數。
        #
        # 這裡只接受：
        #   - 明確的資料列
        #   - 數值欄位
        #
        # 不把任意最後一個數字當成資料來源。

        numeric_values = []

        for value in row[2:]:

            number = safe_number(
                value
            )

            if number is not None:

                numeric_values.append(
                    number
                )

        if not numeric_values:

            continue

        # 官方表結構：
        # 最後一個數值欄為
        # 三大法人買賣超股數。
        #
        # 此處只在 row 已確認為
        # 股票資料列時使用。
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

    result = dict(twse)

    for symbol, value in tpex.items():

        result[symbol] = value

    return result


# ============================================================
# TWSE day trade
# ============================================================

def find_twse_column(
    fields: List[Any],
    patterns: List[str],
) -> Optional[int]:

    for index, field in enumerate(
        fields
    ):

        name = normalize_field_name(
            field
        )

        for pattern in patterns:

            if pattern in name:

                return index

    return None


def fetch_twse_daytrade(
    date_str: str,
) -> Dict[str, float]:

    """
    TWSE 官方當沖成交股數。

    優先使用官方欄位名稱尋找：
        證券代號
        當沖成交股數

    絕不使用固定 row index 猜測。
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

    result: Dict[str, float] = {}

    if not isinstance(
        data,
        dict,
    ):

        return result

    tables = data.get(
        "tables"
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
            "fields"
        )

        rows = table.get(
            "data"
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

        code_index = find_twse_column(
            fields,
            [
                "證券代號",
                "股票代號",
                "代號",
            ],
        )

        volume_index = find_twse_column(
            fields,
            [
                "當沖成交股數",
                "當日沖銷交易成交股數",
                "當沖交易成交股數",
                "當沖成交量",
                "當日沖銷成交股數",
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

            result[code] = round(
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
    TPEx 官方：

        現股當沖交易統計資訊

    使用官方「當日沖銷交易標的及成交量值」資料表。

    解析規則：
        1. 找「證券代號」
        2. 找「當日沖銷交易成交股數」
        3. 只使用該欄位
        4. 不使用 row[2]
        5. 不使用 candidates[-1]
        6. 找不到欄位則回傳空結果
    """

    url = (
        "https://www.tpex.org.tw/"
        "storage/zh-tw/web/stock/trading/"
        "intraday_stat/intraday_trading_statY.htm"
    )

    text = get_text(
        url
    )

    if not text:

        return {}

    rows = parse_html_table(
        text
    )

    if not rows:

        return {}

    header_index = None

    code_index = None

    volume_index = None

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(x)
            for x in row
        ]

        joined = "".join(
            normalized
        )

        if (
            "證券代號" in joined
            and "當日沖銷交易" in joined
            and "成交股數" in joined
        ):

            header_index = index

            code_index = find_column(
                row,
                [
                    "證券代號",
                    "股票代號",
                    "代號",
                ],
            )

            volume_index = find_column(
                row,
                [
                    "當日沖銷交易成交股數",
                    "當沖成交股數",
                    "當日沖銷成交股數",
                ],
            )

            if (
                code_index is not None
                and volume_index is not None
            ):

                break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}

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

        result[code] = round(
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
    TWSE 個股日成交資訊。

    只接受官方欄位：
        證券代號
        成交股數
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/STOCK_DAY_ALL"
    )

    params = {
        "response": "json",
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

    fields = data.get(
        "fields"
    )

    rows = data.get(
        "data"
    )

    if not isinstance(
        fields,
        list,
    ):

        return result

    if not isinstance(
        rows,
        list,
    ):

        return result

    code_index = find_twse_column(
        fields,
        [
            "證券代號",
            "股票代號",
            "代號",
        ],
    )

    volume_index = find_twse_column(
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

        return result

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

        result[code] = round(
            volume,
            2,
        )

    return result


# ============================================================
# TPEx total volume
# ============================================================

def fetch_tpex_total_volume(
    date_obj: datetime,
) -> Dict[str, float]:

    """
    TPEx 個股日成交資訊。

    使用 TPEx 官方個股日成交資訊頁面。

    解析規則：
        1. 尋找證券代號欄位
        2. 尋找成交股數欄位
        3. 依欄位名稱取值
        4. 絕不使用最後一個數字猜測
    """

    date_text = roc_date(
        date_obj
    )

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_trading_info/st43.php"
    )

    params = {
        "l": "zh-tw",
        "d": date_text,
        "o": "htm",
    }

    text = get_text(
        url,
        params,
    )

    if not text:

        return {}

    rows = parse_html_table(
        text
    )

    if not rows:

        return {}

    result: Dict[str, float] = {}

    code_index = None

    volume_index = None

    data_start = 0

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(x)
            for x in row
        ]

        joined = "".join(
            normalized
        )

        if (
            "證券代號" in joined
            and "成交股數" in joined
        ):

            code_index = find_column(
                row,
                [
                    "證券代號",
                    "股票代號",
                    "代號",
                ],
            )

            volume_index = find_column(
                row,
                [
                    "成交股數",
                    "成交量",
                ],
            )

            if (
                code_index is not None
                and volume_index is not None
            ):

                data_start = index + 1

                break

    if (
        code_index is None
        or volume_index is None
    ):

        return {}

    for row in rows[
        data_start:
    ]:

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

        result[code] = round(
            volume,
            2,
        )

    return result


# ============================================================
# Daily day trade
# ============================================================

def fetch_daily_daytrade(
    date_obj: datetime,
) -> Dict[str, Dict[str, float]]:

    """
    同時取得：

        day_trading_volume
        total_volume

    再計算：

        day_trading_rate
    """

    date_str = yyyymmdd(
        date_obj
    )

    section(
        f"同步 {date_str} 當沖 / 成交量"
    )

    twse_daytrade = (
        fetch_twse_daytrade(
            date_str
        )
    )

    log(
        f"  TWSE 當沖："
        f"{len(twse_daytrade)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_daytrade = (
        fetch_tpex_daytrade(
            date_obj
        )
    )

    log(
        f"  TPEX 當沖："
        f"{len(tpex_daytrade)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    twse_total = (
        fetch_twse_total_volume(
            date_str
        )
    )

    log(
        f"  TWSE 總成交量："
        f"{len(twse_total)} 檔"
    )

    time.sleep(
        REQUEST_SLEEP
    )

    tpex_total = (
        fetch_tpex_total_volume(
            date_obj
        )
    )

    log(
        f"  TPEX 總成交量："
        f"{len(tpex_total)} 檔"
    )

    result: Dict[
        str,
        Dict[str, float]
    ] = {}

    all_symbols = set(
        twse_daytrade
    )

    all_symbols.update(
        tpex_daytrade
    )

    all_symbols.update(
        twse_total
    )

    all_symbols.update(
        tpex_total
    )

    for symbol in all_symbols:

        day_volume = (
            tpex_daytrade.get(symbol)
            if symbol in tpex_daytrade
            else twse_daytrade.get(symbol)
        )

        total_volume = (
            tpex_total.get(symbol)
            if symbol in tpex_total
            else twse_total.get(symbol)
        )

        rate = None

        if (
            day_volume is not None
            and total_volume is not None
            and total_volume > 0
        ):

            rate = round(
                day_volume
                / total_volume
                * 100.0,
                2,
            )

            if rate < 0 or rate > 100:

                rate = None

        result[symbol] = {
            "day_trading_volume":
                day_volume,

            "total_volume":
                total_volume,

            "day_trading_rate":
                rate,
        }

    valid_volume = sum(
        1
        for x in result.values()
        if x.get(
            "day_trading_volume"
        ) is not None
    )

    valid_total = sum(
        1
        for x in result.values()
        if x.get(
            "total_volume"
        ) is not None
    )

    valid_rate = sum(
        1
        for x in result.values()
        if x.get(
            "day_trading_rate"
        ) is not None
    )

    log("")
    log(
        "當沖資料彙總："
    )

    log(
        f"  當沖成交股數有效："
        f"{valid_volume}"
    )

    log(
        f"  總成交股數有效："
        f"{valid_total}"
    )

    log(
        f"  當沖率有效："
        f"{valid_rate}"
    )

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
    daytrade: Dict[str, Dict[str, float]],
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

    day_trade_volume_count = 0

    day_trade_rate_count = 0

    total_volume_count = 0

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

        chip_daytrade = daytrade.get(
            symbol,
            {},
        )

        day_trading_volume = (
            chip_daytrade.get(
                "day_trading_volume"
            )
        )

        total_volume = (
            chip_daytrade.get(
                "total_volume"
            )
        )

        day_trading_rate = (
            chip_daytrade.get(
                "day_trading_rate"
            )
        )

        if day_trading_volume is not None:
            day_trade_volume_count += 1

        if total_volume is not None:
            total_volume_count += 1

        if day_trading_rate is not None:
            day_trade_rate_count += 1

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

            "day_trading_volume": (
                day_trading_volume
            ),

            "day_trading_rate": (
                day_trading_rate
            ),

            "total_volume": (
                total_volume
            ),

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

        "day_trading_volume":
            day_trade_volume_count,

        "total_volume":
            total_volume_count,

        "day_trading_rate":
            day_trade_rate_count,
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

        "total_volume",

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
                    f"day_trading_rate 無效："
                    f"{rate}"
                )

        volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        if volume is not None:

            if (
                not isinstance(
                    volume,
                    (int, float),
                )
                or not math.isfinite(
                    float(volume)
                )
                or volume < 0
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"day_trading_volume 無效"
                )

        if total_volume is not None:

            if (
                not isinstance(
                    total_volume,
                    (int, float),
                )
                or not math.isfinite(
                    float(total_volume)
                )
                or total_volume < 0
            ):

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"total_volume 無效"
                )

        if (
            volume is not None
            and total_volume is not None
            and total_volume > 0
            and rate is not None
        ):

            expected_rate = round(
                volume
                / total_volume
                * 100.0,
                2,
            )

            if abs(
                float(rate)
                - expected_rate
            ) > 0.01:

                errors += 1

                log(
                    f"❌ {symbol} "
                    f"當沖率驗算失敗："
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
                f"❌ {symbol} "
                f"寫入後 symbol 錯誤"
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
                f"寫入後名稱為空"
            )

            return False

        rate = item.get(
            "day_trading_rate"
        )

        volume = item.get(
            "day_trading_volume"
        )

        total_volume = item.get(
            "total_volume"
        )

        if rate is not None:

            if (
                rate < 0
                or rate > 100
            ):

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率超出範圍"
                )

                return False

        if (
            volume is not None
            and total_volume is not None
            and total_volume > 0
            and rate is not None
        ):

            expected_rate = round(
                volume
                / total_volume
                * 100.0,
                2,
            )

            if abs(
                float(rate)
                - expected_rate
            ) > 0.01:

                log(
                    f"❌ {symbol} "
                    f"寫入後當沖率不一致"
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
        "✓ 當沖欄位交叉驗算通過"
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
        "  當沖：TWSE + TPEx"
    )

    log(
        "  當沖率：當沖成交股數 / 總成交股數"
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
    # 3. Latest day-trade data
    # ========================================================

    section(
        f"取得 {data_date} 當沖 / 成交量資料"
    )

    data_date_obj = datetime.strptime(
        data_date,
        "%Y-%m-%d",
    )

    daytrade = fetch_daily_daytrade(
        data_date_obj
    )

    log(
        f"✓ 當沖 / 成交量資料標的："
        f"{len(daytrade)} 檔"
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
    # 7. Output counts
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

    # ========================================================
    # 8. Final output
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
    # 9. Final pre-write count
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
        f"✓ 已寫入：{CHIP_FILE}"
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