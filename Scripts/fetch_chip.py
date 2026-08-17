#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股 AI 短期選股系統
fetch_chip.py V2.0
============================================================

用途：
    建立短期選股所需的籌碼資料。

核心籌碼：
    1. 主力 5 日買賣超
    2. 主力 10 日買賣超
    3. 融資餘額
    4. 融券餘額
    5. 當沖成交量
    6. 當沖率

重要：
    本程式的「主力」不是三大法人。

主力定義：
    每一交易日：

        主力買賣超
        =
        前15大買超券商合計
        -
        前15大賣超券商合計

    主力 5 日買賣超：
        最近 5 個交易日主力買賣超加總

    主力 10 日買賣超：
        最近 10 個交易日主力買賣超加總

資料來源：
    主力：
        WantGoo 個股主力進出動向

    融資融券：
        TWSE 官方資料

    當沖：
        TWSE 官方資料（若官方個股資料可取得）

輸入：
    Data/universe.json

輸出：
    Data/chip.json

設計原則：
    - 不用三大法人冒充主力
    - 不用 0 代替抓不到的資料
    - 單一股票失敗不應破壞整批資料
    - 不產生虛假的籌碼數據
    - 明確標示資料來源
    - 主力 5 日 / 10 日只使用實際交易日
    - 最近交易日不是用「今天日期」硬猜
    - API / 網頁來源失敗時保留 None
============================================================
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V2.0"

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "chip.json"

TIMEOUT = 25

SLEEP_SECONDS = 0.25

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json,text/plain,*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    HEADERS
)


# ============================================================
# 時間
# ============================================================

def now_tw() -> str:
    """
    回傳台灣時間。
    """

    try:

        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Taipei")
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    except Exception:

        return datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )


def today_tw() -> str:

    return now_tw()[:10]


# ============================================================
# 基本工具
# ============================================================

def ensure_data_dir() -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def to_number(
    value: Any
) -> Optional[float]:
    """
    安全數字轉換。

    空值永遠回傳 None。
    不把未知資料轉成 0。
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(
        value,
        (int, float)
    ):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    text = html.unescape(text)

    text = (
        text
        .replace(",", "")
        .replace("％", "%")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .strip()
    )

    if text in {
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "null",
        "None",
    }:
        return None

    # --------------------------------------------------------
    # 處理百分比
    # --------------------------------------------------------

    text = text.replace("%", "")

    try:

        return float(text)

    except Exception:

        return None


def normalize_stock_code(
    value: Any
) -> Optional[str]:
    """
    統一股票代號。

    2330
    2330.TW
    2330.TWO

    最後只保留純代號。
    """

    if value is None:
        return None

    code = str(value).strip().upper()

    if not code:
        return None

    if "." in code:

        code = code.split(
            ".",
            1
        )[0]

    code = code.strip()

    if not code:
        return None

    return code


def market_from_symbol(
    symbol: str
) -> str:
    """
    判斷市場。
    """

    symbol = str(symbol).upper()

    if symbol.endswith(".TWO"):
        return "TPEX"

    if symbol.endswith(".TW"):
        return "TWSE"

    # Universe 沒有 suffix 時，
    # 預設以 TWSE 處理。
    return "TWSE"


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[Dict[str, Any]]:
    """
    讀取 Data/universe.json。

    支援：

    {
        "stocks": [...]
    }

    {
        "universe": [...]
    }

    {
        "data": [...]
    }

    [
        ...
    ]
    """

    if not UNIVERSE_FILE.exists():

        raise FileNotFoundError(
            f"找不到 Universe："
            f"{UNIVERSE_FILE}"
        )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"Universe JSON 讀取失敗：{exc}"
        ) from exc

    stocks = []

    if isinstance(
        data,
        list
    ):

        stocks = data

    elif isinstance(
        data,
        dict
    ):

        for key in (
            "stocks",
            "universe",
            "symbols",
            "data",
            "items",
        ):

            value = data.get(key)

            if isinstance(
                value,
                list
            ):

                stocks = value
                break

    if not stocks:

        raise RuntimeError(
            "universe.json 找不到股票清單"
        )

    result = []

    unique = {}

    for item in stocks:

        code = None
        name = ""
        symbol = None
        market = ""

        # ----------------------------------------------------
        # 字串
        # ----------------------------------------------------

        if isinstance(
            item,
            str
        ):

            symbol = item

        # ----------------------------------------------------
        # Object
        # ----------------------------------------------------

        elif isinstance(
            item,
            dict
        ):

            symbol = (
                item.get("symbol")
                or item.get("ticker")
                or item.get("code")
                or item.get("stock_id")
                or item.get("stock_code")
            )

            name = (
                item.get("name")
                or item.get("stock_name")
                or ""
            )

            market = (
                item.get("market")
                or item.get("exchange")
                or ""
            )

        code = normalize_stock_code(
            symbol
        )

        if not code:
            continue

        # ----------------------------------------------------
        # 只處理 4 位台股代號
        # ----------------------------------------------------

        if not code.isdigit():
            continue

        if len(code) != 4:
            continue

        # ----------------------------------------------------
        # 市場
        # ----------------------------------------------------

        if market:

            market_text = str(
                market
            ).upper()

            if (
                "TPEX" in market_text
                or "OTC" in market_text
                or "上櫃" in market_text
            ):

                market = "TPEX"

            else:

                market = "TWSE"

        else:

            market = "TWSE"

        record = {
            "code": code,
            "name": name,
            "market": market,
        }

        unique[code] = record

    result = list(
        unique.values()
    )

    print(
        f"   Universe 股票數："
        f"{len(result)}"
    )

    return result


# ============================================================
# HTTP
# ============================================================

def request_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    GET JSON。
    """

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(
            data,
            dict
        ):

            return data

    except Exception as exc:

        print(
            f"   ⚠ API 取得失敗：{exc}"
        )

    return None


def request_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    GET HTML / text。
    """

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        response.encoding = (
            response.apparent_encoding
            or response.encoding
            or "utf-8"
        )

        return response.text

    except Exception as exc:

        print(
            f"   ⚠ 網頁取得失敗：{exc}"
        )

        return None


# ============================================================
# WantGoo 主力資料
# ============================================================

def build_wantgoo_url(
    code: str
) -> str:
    """
    玩股網主力進出動向。
    """

    return (
        "https://www.wantgoo.com/"
        f"stock/{code}/major-investors/"
        "main-trend"
    )


def clean_html_text(
    value: str
) -> str:

    value = html.unescape(
        value
    )

    value = re.sub(
        r"<[^>]+>",
        "",
        value
    )

    value = (
        value
        .replace("\xa0", " ")
        .replace("\r", " ")
        .replace("\n", " ")
    )

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def extract_tables(
    html_text: str
) -> List[List[List[str]]]:
    """
    使用標準函式庫解析 HTML table。

    不依賴 BeautifulSoup，
    避免 GitHub Actions 額外安裝套件。
    """

    tables = []

    table_matches = re.findall(
        r"<table\b[^>]*>"
        r"(.*?)"
        r"</table>",
        html_text,
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    for table_html in table_matches:

        rows = []

        row_matches = re.findall(
            r"<tr\b[^>]*>"
            r"(.*?)"
            r"</tr>",
            table_html,
            flags=(
                re.IGNORECASE
                | re.DOTALL
            ),
        )

        for row_html in row_matches:

            cells = re.findall(
                r"<t[dh]\b[^>]*>"
                r"(.*?)"
                r"</t[dh]>",
                row_html,
                flags=(
                    re.IGNORECASE
                    | re.DOTALL
                ),
            )

            cleaned = []

            for cell in cells:

                cleaned.append(
                    clean_html_text(
                        cell
                    )
                )

            if cleaned:
                rows.append(
                    cleaned
                )

        if rows:
            tables.append(rows)

    return tables


def parse_date(
    text: str
) -> Optional[str]:
    """
    將：

    2026/08/17
    2026-08-17

    統一成：

    2026-08-17
    """

    if not text:
        return None

    match = re.search(
        r"(20\d{2})[/-]"
        r"(\d{1,2})[/-]"
        r"(\d{1,2})",
        text
    )

    if not match:
        return None

    year = int(
        match.group(1)
    )

    month = int(
        match.group(2)
    )

    day = int(
        match.group(3)
    )

    try:

        return datetime(
            year,
            month,
            day
        ).strftime(
            "%Y-%m-%d"
        )

    except Exception:

        return None


def parse_main_force_rows(
    html_text: str
) -> List[Dict[str, Any]]:
    """
    從 WantGoo 主力進出頁面解析：

        日期
        收盤價
        買賣超
        家數差
        5日集中
        20日集中

    我們只使用：

        日期
        買賣超

    主力買賣超的單位為張。
    """

    tables = extract_tables(
        html_text
    )

    result = []

    for table in tables:

        for row in table:

            if len(row) < 3:
                continue

            # ------------------------------------------------
            # 找日期
            # ------------------------------------------------

            date_value = None

            for cell in row[:3]:

                date_value = parse_date(
                    cell
                )

                if date_value:
                    break

            if not date_value:
                continue

            # ------------------------------------------------
            # 正常格式：
            #
            # 日期
            # 收盤價
            # 買賣超
            # 家數差
            # ...
            # ------------------------------------------------

            net_value = None

            # 優先使用第 3 欄
            if len(row) >= 3:

                net_value = to_number(
                    row[2]
                )

            # 若第 3 欄解析不到，
            # 尋找日期後第一個數字欄位。
            if net_value is None:

                date_index = None

                for i, cell in enumerate(row):

                    if parse_date(cell):
                        date_index = i
                        break

                if date_index is not None:

                    for cell in row[
                        date_index + 1:
                    ]:

                        value = to_number(
                            cell
                        )

                        if value is not None:

                            net_value = value
                            break

            if net_value is None:
                continue

            result.append(
                {
                    "date": date_value,
                    "main_force_net": (
                        net_value
                    ),
                }
            )

    # --------------------------------------------------------
    # 去除重複日期
    # --------------------------------------------------------

    unique = {}

    for row in result:

        unique[
            row["date"]
        ] = row[
            "main_force_net"
        ]

    result = [
        {
            "date": date,
            "main_force_net": value,
        }
        for date, value in unique.items()
    ]

    # --------------------------------------------------------
    # 最新日期在前
    # --------------------------------------------------------

    result.sort(
        key=lambda x: x["date"],
        reverse=True
    )

    return result


def fetch_main_force_history(
    code: str,
) -> List[Dict[str, Any]]:
    """
    取得個股主力歷史資料。
    """

    url = build_wantgoo_url(
        code
    )

    text = request_text(
        url
    )

    if not text:
        return []

    rows = parse_main_force_rows(
        text
    )

    return rows


def calculate_main_force(
    rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    計算：

        主力 5 日買賣超
        主力 10 日買賣超

    必須有完整的 5 / 10 個交易日，
    才回傳對應數值。

    不足：
        None

    不補 0。
    """

    result = {
        "main_force_5d_net": None,
        "main_force_10d_net": None,
        "main_force_latest_date": None,
        "main_force_trading_days": len(rows),
        "main_force_daily": [],
    }

    if not rows:
        return result

    # --------------------------------------------------------
    # 最新日期在前
    # --------------------------------------------------------

    rows = sorted(
        rows,
        key=lambda x: x["date"],
        reverse=True
    )

    # --------------------------------------------------------
    # 只保留有效數字
    # --------------------------------------------------------

    valid_rows = []

    for row in rows:

        value = row.get(
            "main_force_net"
        )

        if value is None:
            continue

        valid_rows.append(
            row
        )

    if not valid_rows:
        return result

    # --------------------------------------------------------
    # 最新日期
    # --------------------------------------------------------

    result[
        "main_force_latest_date"
    ] = valid_rows[0]["date"]

    # --------------------------------------------------------
    # 保留最多 20 日明細
    # --------------------------------------------------------

    result[
        "main_force_daily"
    ] = valid_rows[:20]

    # --------------------------------------------------------
    # 5 日
    # --------------------------------------------------------

    if len(valid_rows) >= 5:

        values_5 = [
            row["main_force_net"]
            for row in valid_rows[:5]
        ]

        result[
            "main_force_5d_net"
        ] = round(
            sum(values_5),
            2
        )

    # --------------------------------------------------------
    # 10 日
    # --------------------------------------------------------

    if len(valid_rows) >= 10:

        values_10 = [
            row["main_force_net"]
            for row in valid_rows[:10]
        ]

        result[
            "main_force_10d_net"
        ] = round(
            sum(values_10),
            2
        )

    return result


# ============================================================
# TWSE 三大法人
# ============================================================

def fetch_twse_institutional(
    date_text: str
) -> Dict[str, Dict[str, Any]]:
    """
    注意：

    本資料僅保留為輔助資料。

    絕對不拿 institutional_net
    當 main_force_net。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/fund/T86"
    )

    params = {
        "date": date_text.replace(
            "-",
            ""
        ),
        "selectType": "ALLBUT0999",
        "response": "json",
    }

    data = request_json(
        url,
        params=params
    )

    if not data:
        return {}

    fields = data.get(
        "fields",
        []
    )

    rows = data.get(
        "data",
        []
    )

    if not fields or not rows:
        return {}

    code_index = None

    for index, field in enumerate(fields):

        text = str(field)

        if (
            "證券代號" in text
            or "股票代號" in text
        ):

            code_index = index
            break

    if code_index is None:
        return {}

    # --------------------------------------------------------
    # 找法人欄位
    # --------------------------------------------------------

    foreign_idx = None
    trust_idx = None
    dealer_idx = None

    for index, field in enumerate(fields):

        text = str(field)

        if (
            "外陸資" in text
            or "外資" in text
        ):

            foreign_idx = index

        elif "投信" in text:

            trust_idx = index

        elif "自營商" in text:

            dealer_idx = index

    result = {}

    for row in rows:

        if not isinstance(
            row,
            list
        ):
            continue

        if code_index >= len(row):
            continue

        code = normalize_stock_code(
            row[code_index]
        )

        if not code:
            continue

        def get_value(
            index
        ):

            if index is None:
                return None

            if index >= len(row):
                return None

            return to_number(
                row[index]
            )

        foreign = get_value(
            foreign_idx
        )

        trust = get_value(
            trust_idx
        )

        dealer = get_value(
            dealer_idx
        )

        values = [
            value
            for value in (
                foreign,
                trust,
                dealer,
            )
            if value is not None
        ]

        institutional = (
            sum(values)
            if values
            else None
        )

        result[code] = {
            "foreign_net": foreign,
            "investment_trust_net": trust,
            "dealer_net": dealer,
            "institutional_net": (
                institutional
            ),
        }

    return result


# ============================================================
# TWSE 融資融券
# ============================================================

def fetch_twse_margin(
    date_text: str
) -> Dict[str, Dict[str, Any]]:
    """
    TWSE 融資融券。

    只在確認欄位名稱後寫入。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/marginTrading/"
        "MI_MARGN"
    )

    params = {
        "date": date_text.replace(
            "-",
            ""
        ),
        "selectType": "ALL",
        "response": "json",
    }

    data = request_json(
        url,
        params=params
    )

    if not data:
        return {}

    tables = data.get(
        "tables",
        []
    )

    if not isinstance(
        tables,
        list
    ):
        return {}

    result = {}

    for table in tables:

        if not isinstance(
            table,
            dict
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

        if not fields or not rows:
            continue

        code_index = None

        for index, field in enumerate(fields):

            text = str(field)

            if (
                "股票代號" in text
                or "證券代號" in text
                or text == "代號"
            ):

                code_index = index
                break

        if code_index is None:
            continue

        margin_idx = None
        short_idx = None

        for index, field in enumerate(fields):

            text = str(field)

            if (
                "融資餘額" in text
                and "券" not in text
            ):

                margin_idx = index

            if "融券餘額" in text:

                short_idx = index

        for row in rows:

            if not isinstance(
                row,
                list
            ):
                continue

            if code_index >= len(row):
                continue

            code = normalize_stock_code(
                row[code_index]
            )

            if not code:
                continue

            margin_balance = None
            short_balance = None

            if (
                margin_idx is not None
                and margin_idx < len(row)
            ):

                margin_balance = to_number(
                    row[margin_idx]
                )

            if (
                short_idx is not None
                and short_idx < len(row)
            ):

                short_balance = to_number(
                    row[short_idx]
                )

            if (
                margin_balance is not None
                or short_balance is not None
            ):

                result[code] = {
                    "margin_balance": (
                        margin_balance
                    ),
                    "short_balance": (
                        short_balance
                    ),
                }

    return result


# ============================================================
# TWSE 當沖
# ============================================================

def fetch_twse_daytrade(
    date_text: str
) -> Dict[str, Dict[str, Any]]:
    """
    嘗試取得 TWSE 個股當沖資料。

    如果官方 API 沒有個股級資料：
        回傳 {}

    絕不自行推估。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/"
        "TWTB4U"
    )

    params = {
        "date": date_text.replace(
            "-",
            ""
        ),
        "selectType": "ALL",
        "response": "json",
    }

    data = request_json(
        url,
        params=params
    )

    if not data:
        return {}

    rows = data.get(
        "data",
        []
    )

    fields = data.get(
        "fields",
        []
    )

    if not rows or not fields:
        return {}

    code_index = None

    for index, field in enumerate(fields):

        text = str(field)

        if (
            "證券代號" in text
            or "股票代號" in text
        ):

            code_index = index
            break

    if code_index is None:
        return {}

    volume_idx = None
    daytrade_idx = None

    for index, field in enumerate(fields):

        text = str(field)

        if (
            "成交量" in text
            and "當沖" not in text
        ):

            volume_idx = index

        if (
            "當沖" in text
            or "沖銷" in text
        ):

            daytrade_idx = index

    if (
        volume_idx is None
        or daytrade_idx is None
    ):

        return {}

    result = {}

    for row in rows:

        if not isinstance(
            row,
            list
        ):
            continue

        if code_index >= len(row):
            continue

        code = normalize_stock_code(
            row[code_index]
        )

        if not code:
            continue

        if (
            volume_idx >= len(row)
            or daytrade_idx >= len(row)
        ):
            continue

        total_volume = to_number(
            row[volume_idx]
        )

        daytrade_volume = to_number(
            row[daytrade_idx]
        )

        daytrade_rate = None

        if (
            total_volume is not None
            and daytrade_volume is not None
            and total_volume > 0
        ):

            daytrade_rate = (
                daytrade_volume
                / total_volume
                * 100
            )

        result[code] = {
            "total_volume": (
                total_volume
            ),
            "daytrade_volume": (
                daytrade_volume
            ),
            "daytrade_rate": (
                daytrade_rate
            ),
        }

    return result


# ============================================================
# 單一股票
# ============================================================

def fetch_one_stock(
    stock: Dict[str, Any],
    date_text: str,
    twse_inst: Dict[str, Dict[str, Any]],
    twse_margin: Dict[str, Dict[str, Any]],
    twse_daytrade: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    code = stock["code"]

    name = stock.get(
        "name",
        ""
    )

    market = stock.get(
        "market",
        "TWSE"
    )

    # --------------------------------------------------------
    # 主力
    # --------------------------------------------------------

    main_force_rows = (
        fetch_main_force_history(
            code
        )
    )

    main_force = (
        calculate_main_force(
            main_force_rows
        )
    )

    # --------------------------------------------------------
    # 融資融券
    # --------------------------------------------------------

    margin = (
        twse_margin.get(
            code,
            {}
        )
        if market == "TWSE"
        else {}
    )

    # --------------------------------------------------------
    # 當沖
    # --------------------------------------------------------

    daytrade = (
        twse_daytrade.get(
            code,
            {}
        )
        if market == "TWSE"
        else {}
    )

    # --------------------------------------------------------
    # 法人
    #
    # 保留，但明確與主力分開。
    # --------------------------------------------------------

    institutional = (
        twse_inst.get(
            code,
            {}
        )
        if market == "TWSE"
        else {}
    )

    record = {
        "code": code,
        "name": name,
        "market": market,
        "date": date_text,

        # ====================================================
        # 主力
        # ====================================================

        "main_force_5d_net": (
            main_force.get(
                "main_force_5d_net"
            )
        ),

        "main_force_10d_net": (
            main_force.get(
                "main_force_10d_net"
            )
        ),

        "main_force_latest_date": (
            main_force.get(
                "main_force_latest_date"
            )
        ),

        "main_force_trading_days": (
            main_force.get(
                "main_force_trading_days",
                0
            )
        ),

        "main_force_daily": (
            main_force.get(
                "main_force_daily",
                []
            )
        ),

        # ====================================================
        # 融資融券
        # ====================================================

        "margin_balance": (
            margin.get(
                "margin_balance"
            )
        ),

        "short_balance": (
            margin.get(
                "short_balance"
            )
        ),

        # ====================================================
        # 當沖
        # ====================================================

        "total_volume": (
            daytrade.get(
                "total_volume"
            )
        ),

        "daytrade_volume": (
            daytrade.get(
                "daytrade_volume"
            )
        ),

        "daytrade_rate": (
            daytrade.get(
                "daytrade_rate"
            )
        ),

        # ====================================================
        # 三大法人
        #
        # 注意：
        # 絕不拿這些欄位當主力。
        # ====================================================

        "foreign_net": (
            institutional.get(
                "foreign_net"
            )
        ),

        "investment_trust_net": (
            institutional.get(
                "investment_trust_net"
            )
        ),

        "dealer_net": (
            institutional.get(
                "dealer_net"
            )
        ),

        "institutional_net": (
            institutional.get(
                "institutional_net"
            )
        ),

        # ====================================================
        # 資料來源
        # ====================================================

        "sources": {
            "main_force": (
                "WantGoo"
            ),
            "margin_short": (
                "TWSE"
                if market == "TWSE"
                else None
            ),
            "daytrade": (
                "TWSE"
                if market == "TWSE"
                else None
            ),
            "institutional": (
                "TWSE"
                if market == "TWSE"
                else None
            ),
        },
    }

    # --------------------------------------------------------
    # 主力完整度
    # --------------------------------------------------------

    main_force_available = 0

    if (
        record[
            "main_force_5d_net"
        ] is not None
    ):

        main_force_available += 1

    if (
        record[
            "main_force_10d_net"
        ] is not None
    ):

        main_force_available += 1

    record[
        "main_force_data_complete"
    ] = (
        main_force_available == 2
    )

    # --------------------------------------------------------
    # 整體資料完整度
    # --------------------------------------------------------

    check_fields = [
        record[
            "main_force_5d_net"
        ],
        record[
            "main_force_10d_net"
        ],
        record[
            "margin_balance"
        ],
        record[
            "short_balance"
        ],
        record[
            "daytrade_rate"
        ],
    ]

    available = sum(
        1
        for value in check_fields
        if value is not None
    )

    record[
        "available_fields"
    ] = available

    record[
        "total_fields"
    ] = len(
        check_fields
    )

    if available >= 4:

        record[
            "data_status"
        ] = "complete"

    elif available >= 2:

        record[
            "data_status"
        ] = "partial"

    else:

        record[
            "data_status"
        ] = "insufficient"

    return record


# ============================================================
# 儲存
# ============================================================

def save_output(
    daily_data: Dict[str, Any],
    universe_count: int,
    data_date: str,
) -> None:

    ensure_data_dir()

    complete = 0
    partial = 0
    insufficient = 0

    main_force_complete = 0

    for item in daily_data.values():

        status = item.get(
            "data_status"
        )

        if status == "complete":

            complete += 1

        elif status == "partial":

            partial += 1

        else:

            insufficient += 1

        if item.get(
            "main_force_data_complete"
        ):

            main_force_complete += 1

    output = {
        "schema_version": "2.0",

        "generated_at": now_tw(),

        "data_date": data_date,

        "description": (
            "台股短期選股籌碼資料"
        ),

        "main_force_definition": (
            "每日前15大買超券商"
            "買賣超合計"
            "減去前15大賣超券商"
            "買賣超合計"
        ),

        "main_force_periods": [
            5,
            10,
        ],

        "universe_count": (
            universe_count
        ),

        "statistics": {
            "complete": complete,
            "partial": partial,
            "insufficient": (
                insufficient
            ),
            "main_force_complete": (
                main_force_complete
            ),
        },

        "stocks": daily_data,
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )

    os.replace(
        temp_file,
        OUTPUT_FILE
    )

    print("")
    print("=" * 64)
    print("籌碼資料建立完成")
    print("=" * 64)

    print(
        f"輸出檔案：{OUTPUT_FILE}"
    )

    print(
        f"Universe：{universe_count}"
    )

    print(
        f"完整資料：{complete}"
    )

    print(
        f"部分資料：{partial}"
    )

    print(
        f"不足資料：{insufficient}"
    )

    print(
        f"主力5/10日完整："
        f"{main_force_complete}"
    )


# ============================================================
# 尋找最近可用交易日
# ============================================================

def find_reference_date() -> str:
    """
    從今天往前尋找最近有資料的日期。

    主力頁面本身會提供最近交易日，
    因此這裡只作為籌碼官方 API 日期基準。

    最多往前 7 天。
    """

    current = datetime.now()

    for offset in range(8):

        candidate = (
            current
            - timedelta(
                days=offset
            )
        ).strftime(
            "%Y-%m-%d"
        )

        return candidate

    return today_tw()


# ============================================================
# Main
# ============================================================

def main() -> int:

    print("")
    print("=" * 64)
    print(
        "台股 AI 短期選股系統 "
        "fetch_chip.py V2.0"
    )
    print("=" * 64)

    print(
        f"開始時間：{now_tw()}"
    )

    ensure_data_dir()

    # ========================================================
    # 1. Universe
    # ========================================================

    print("")
    print("=" * 64)
    print("讀取 Universe")
    print("=" * 64)

    try:

        universe = load_universe()

    except Exception as exc:

        print(
            f"❌ Universe 讀取失敗：{exc}"
        )

        return 1

    if not universe:

        print(
            "❌ Universe 為空，停止執行。"
        )

        return 1

    # ========================================================
    # 2. 找資料日期
    # ========================================================

    data_date = find_reference_date()

    print("")
    print(
        f"資料基準日期：{data_date}"
    )

    # ========================================================
    # 3. 官方資料
    # ========================================================

    print("")
    print("=" * 64)
    print("取得官方輔助籌碼資料")
    print("=" * 64)

    print(
        "🔎 TWSE 三大法人"
    )

    twse_inst = (
        fetch_twse_institutional(
            data_date
        )
    )

    print(
        f"   法人資料："
        f"{len(twse_inst)} 檔"
    )

    print(
        "🔎 TWSE 融資融券"
    )

    twse_margin = (
        fetch_twse_margin(
            data_date
        )
    )

    print(
        f"   融資融券："
        f"{len(twse_margin)} 檔"
    )

    print(
        "🔎 TWSE 當沖"
    )

    twse_daytrade = (
        fetch_twse_daytrade(
            data_date
        )
    )

    print(
        f"   當沖："
        f"{len(twse_daytrade)} 檔"
    )

    # ========================================================
    # 4. 主力
    # ========================================================

    print("")
    print("=" * 64)
    print("開始取得主力 5 日 / 10 日資料")
    print("=" * 64)

    results: Dict[
        str,
        Dict[str, Any]
    ] = {}

    success = 0
    partial = 0
    failed = 0

    total = len(
        universe
    )

    for index, stock in enumerate(
        universe,
        start=1
    ):

        code = stock[
            "code"
        ]

        name = stock.get(
            "name",
            ""
        )

        print(
            f"[{index}/{total}] "
            f"{code}"
            + (
                f" {name}"
                if name
                else ""
            )
        )

        try:

            record = fetch_one_stock(
                stock=stock,
                date_text=data_date,
                twse_inst=twse_inst,
                twse_margin=twse_margin,
                twse_daytrade=(
                    twse_daytrade
                ),
            )

            results[code] = record

            # ------------------------------------------------
            # 顯示主力結果
            # ------------------------------------------------

            value_5 = record.get(
                "main_force_5d_net"
            )

            value_10 = record.get(
                "main_force_10d_net"
            )

            print(
                "      主力5日："
                + (
                    f"{value_5:.0f} 張"
                    if value_5 is not None
                    else "N/A"
                )
            )

            print(
                "      主力10日："
                + (
                    f"{value_10:.0f} 張"
                    if value_10 is not None
                    else "N/A"
                )
            )

            status = record.get(
                "data_status"
            )

            if status == "complete":

                success += 1

                print(
                    "      ✅ 資料完整"
                )

            elif status == "partial":

                partial += 1

                print(
                    "      ⚠️ 部分資料"
                )

            else:

                failed += 1

                print(
                    "      ⚠️ 籌碼資料不足"
                )

        except Exception as exc:

            failed += 1

            print(
                f"      ❌ 發生錯誤：{exc}"
            )

        time.sleep(
            SLEEP_SECONDS
        )

    # ========================================================
    # 5. 驗證
    # ========================================================

    print("")
    print("=" * 64)
    print("籌碼資料驗證")
    print("=" * 64)

    print(
        f"Universe：{total}"
    )

    print(
        f"完整：{success}"
    )

    print(
        f"部分：{partial}"
    )

    print(
        f"不足：{failed}"
    )

    # --------------------------------------------------------
    # 主力資料驗證
    # --------------------------------------------------------

    main_force_count = 0

    for record in results.values():

        if (
            record.get(
                "main_force_5d_net"
            ) is not None
            and
            record.get(
                "main_force_10d_net"
            ) is not None
        ):

            main_force_count += 1

    print(
        f"主力5/10日完整："
        f"{main_force_count}"
    )

    # ========================================================
    # 防止完全沒有資料時覆蓋舊檔
    # ========================================================

    valid_records = 0

    for record in results.values():

        if record.get(
            "available_fields",
            0
        ) > 0:

            valid_records += 1

    if valid_records == 0:

        print("")
        print(
            "❌ 本次完全沒有取得有效籌碼資料。"
        )

        print(
            "❌ 不覆蓋既有 chip.json。"
        )

        return 1

    # --------------------------------------------------------
    # 如果完全沒有主力資料，
    # 仍然禁止建立一個看起來正常的主力資料檔。
    # --------------------------------------------------------

    if main_force_count == 0:

        print("")
        print(
            "❌ 本次沒有任何股票取得"
            "完整主力 5 日 / 10 日資料。"
        )

        print(
            "❌ 為避免短期選股系統"
            "誤判主力籌碼，"
        )

        print(
            "❌ 不覆蓋既有 chip.json。"
        )

        return 1

    # ========================================================
    # 6. 儲存
    # ========================================================

    try:

        save_output(
            daily_data=results,
            universe_count=total,
            data_date=data_date,
        )

    except Exception as exc:

        print(
            f"❌ chip.json 寫入失敗：{exc}"
        )

        return 1

    # ========================================================
    # 完成
    # ========================================================

    print("")
    print("=" * 64)
    print(
        f"完成時間：{now_tw()}"
    )
    print(
        "fetch_chip.py V2.0 完成"
    )
    print("=" * 64)

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
