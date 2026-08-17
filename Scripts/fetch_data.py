#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 fetch_data.py V10.6

============================================================
V10.6 全市場 Universe 正式版
============================================================

【Universe】
1. 全部 TWSE 上市普通股票
2. 全部 TPEx 上櫃普通股票
3. TWSE 上市 ETF
4. TPEx 上櫃 ETF
5. 指數型 ETF
6. 債券型 ETF
7. 不包含興櫃股票
8. 不包含 ETN
9. 不使用固定 11 / 14 檔股票清單
10. 不使用 lxml / pandas.read_html()

【資料來源】
TWSE
TPEx
TWSE ISIN
Yahoo Finance

【核心六項條件】
1. MACD > MACD Signal
2. RSI > 50
3. K > D
4. Volume >= Volume MA5 × 1.5
5. Close > MA20
6. MA20[today] > MA20[yesterday]

【重要規則】
- 六項條件必須全部使用同一有效交易日
- today_selected = 最新有效交易日 6/6
- 非交易日不假造價格
- change_pct 無資料時保持 null
- 歷史不足不允許誤判
- Universe 建立失敗時 Action 必須失敗
- 不以成功取得行情的數量重新建立 Universe
- prices.json 保存完整 Universe
- backtest 使用交易日

============================================================
"""

import os
import sys
import json
import math
import time
import warnings
import re

from datetime import datetime, timedelta, timezone

import pandas as pd
import numpy as np
import requests

warnings.filterwarnings("ignore")


# ============================================================
# 基本設定
# ============================================================

VERSION = "V10.6"
SCHEMA_VERSION = "ui.v10"

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "Data"
)

PRICES_FILE = os.path.join(
    DATA_DIR,
    "prices.json"
)

STOCKS_FILE = os.path.join(
    DATA_DIR,
    "stocks.json"
)

TIMEZONE_TW = timezone(
    timedelta(hours=8)
)

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

HISTORY_PERIOD_DAYS = 260

MIN_HISTORY_ROWS = 80

BACKTEST_HORIZON = 10

REQUEST_SLEEP = 0.08

REQUEST_TIMEOUT = 20

# ============================================================
# Universe 合理性門檻
# ============================================================

MIN_TWSE_STOCKS = 900
MIN_TPEX_STOCKS = 600

MIN_TOTAL_STOCKS = 1500

MIN_ETF_COUNT = 50

# ============================================================
# 核心條件
# ============================================================

CORE_CONDITION_NAMES = [
    "MACD 多方",
    "RSI > 50",
    "KD 多方",
    "成交量 ≥ MA5 × 1.5",
    "股價 > MA20",
    "MA20 今日 > 昨日",
]

CORE_TOTAL = len(
    CORE_CONDITION_NAMES
)


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,text/plain,*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,en;q=0.8"
        ),
        "Referer": (
            "https://www.twse.com.tw/"
        ),
    }
)


# ============================================================
# 時間
# ============================================================

def now_tw():

    return datetime.now(
        TIMEZONE_TW
    )


def today_tw_date():

    return now_tw().date()


# ============================================================
# 安全數字
# ============================================================

def safe_float(
    value,
    default=None
):

    try:

        if value is None:
            return default

        x = float(value)

        if not math.isfinite(x):
            return default

        return x

    except Exception:

        return default


# ============================================================
# JSON 清理
# ============================================================

def clean_json_value(
    value
):

    if isinstance(
        value,
        dict
    ):

        return {
            str(k): clean_json_value(v)
            for k, v in value.items()
        }

    if isinstance(
        value,
        list
    ):

        return [
            clean_json_value(v)
            for v in value
        ]

    if isinstance(
        value,
        np.integer
    ):

        return int(value)

    if isinstance(
        value,
        np.floating
    ):

        if not np.isfinite(value):
            return None

        return float(value)

    if isinstance(
        value,
        float
    ):

        if not math.isfinite(value):
            return None

        return value

    try:

        if pd.isna(value):

            return None

    except Exception:

        pass

    return value


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(
    symbol,
    market=None
):

    if symbol is None:

        return None

    s = str(
        symbol
    ).strip().upper()

    if not s:

        return None

    s = s.replace(
        " ",
        ""
    )

    if s.endswith(
        ".TW"
    ):

        return s

    if s.endswith(
        ".TWO"
    ):

        return s

    if "." in s:

        return s

    if market == "TWO":

        return s + ".TWO"

    if market == "TW":

        return s + ".TW"

    if s.isdigit():

        if len(s) <= 4:

            return (
                s.zfill(4)
                + ".TW"
            )

    return s


def extract_code(
    symbol
):

    if symbol is None:

        return ""

    s = str(
        symbol
    ).strip()

    if "." in s:

        s = s.split(
            "."
        )[0]

    return s


# ============================================================
# Market
# ============================================================

def infer_market(
    symbol
):

    if symbol is None:

        return "OTHER"

    s = str(
        symbol
    ).upper()

    if s.endswith(
        ".TWO"
    ):

        return "TWO"

    if s.endswith(
        ".TW"
    ):

        return "TW"

    return "OTHER"


# ============================================================
# ETF 分類
# ============================================================

BOND_KEYWORDS = [
    "債",
    "公債",
    "公司債",
    "投資級",
    "高收益債",
    "非投資等級債",
    "金融債",
    "短天期債",
    "長天期債",
    "國債",
    "國庫券",
    "美元債",
    "美債",
    "新興市場債",
    "新興債",
    "ESG債",
    "ESG債券",
    "BOND",
    "TREASURY",
    "CORPORATE BOND",
    "HIGH YIELD",
    "INVESTMENT GRADE",
]

ETF_KEYWORDS = [
    "ETF",
    "指數",
    "台灣50",
    "台灣加權",
    "科技",
    "高股息",
    "低波",
    "永續",
    "ESG",
    "半導體",
    "電子",
    "金融",
    "AI",
    "航運",
    "能源",
    "原物料",
    "REIT",
    "MSCI",
    "S&P",
    "NASDAQ",
    "NASDAQ",
    "NYSE",
    "DOW",
    "FTSE",
    "日經",
    "恒生",
    "恆生",
]


def is_bond_etf(
    code,
    name
):

    text = (
        str(code)
        + " "
        + str(name)
    ).upper()

    for keyword in BOND_KEYWORDS:

        if keyword.upper() in text:

            return True

    # --------------------------------------------------------
    # TPEx 官方分類：
    # 債券 ETF 代號第六碼通常為 B / C / D
    # --------------------------------------------------------

    c = str(
        code
    ).upper()

    if len(c) >= 6:

        sixth = c[5]

        if sixth in (
            "B",
            "C",
            "D",
        ):

            return True

    return False


def infer_asset_type(
    code,
    name,
    security_type=None
):

    text_type = str(
        security_type
        or ""
    ).upper()

    if "ETF" not in text_type:

        if is_bond_etf(
            code,
            name
        ):

            return "bond"

    if (
        "ETF"
        in text_type
    ):

        if is_bond_etf(
            code,
            name
        ):

            return "bond"

        return "etf"

    return "stock"


# ============================================================
# Universe Item
# ============================================================

def make_item(
    code,
    name,
    market,
    asset_type,
    source="official"
):

    code = str(
        code
        or ""
    ).strip().upper()

    if not code:

        return None

    symbol = normalize_symbol(
        code,
        market
    )

    if not symbol:

        return None

    return {
        "code": code,
        "symbol": symbol,
        "name": str(
            name
            or code
        ).strip(),
        "market": market,
        "type": asset_type,
        "source": source,
    }


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None,
    headers=None,
    timeout=REQUEST_TIMEOUT
):

    try:

        response = SESSION.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout
        )

        if response.status_code != 200:

            print(
                f"   ⚠️ HTTP "
                f"{response.status_code}: "
                f"{url}"
            )

            return None

        return response

    except Exception as e:

        print(
            f"   ⚠️ HTTP 讀取失敗：{e}"
        )

        return None


# ============================================================
# TWSE 上市股票
#
# 使用 TWSE 官方 JSON API
# 不使用 pandas.read_html
# ============================================================

def fetch_twse_stocks():

    print(
        "🔎 取得 TWSE 官方上市股票 Universe..."
    )

    url = (
        "https://openapi.twse.com.tw/"
        "v1/opendata/t187ap03_L"
    )

    response = http_get(
        url
    )

    if response is None:

        return []

    try:

        data = response.json()

    except Exception as e:

        print(
            f"   ⚠️ TWSE JSON 解析失敗：{e}"
        )

        return []

    if not isinstance(
        data,
        list
    ):

        print(
            "   ⚠️ TWSE 回傳格式不是 list"
        )

        return []

    result = []

    for row in data:

        if not isinstance(
            row,
            dict
        ):

            continue

        code = (
            row.get(
                "公司代號"
            )
            or row.get(
                "Code"
            )
            or row.get(
                "SecuritiesCompanyCode"
            )
        )

        name = (
            row.get(
                "公司名稱"
            )
            or row.get(
                "CompanyName"
            )
            or row.get(
                "公司簡稱"
            )
            or code
        )

        if not code:

            continue

        code = str(
            code
        ).strip()

        # ----------------------------------------------------
        # 只接受一般股票代號
        # 避免把特殊證券混進股票 Universe
        # ----------------------------------------------------

        if not re.fullmatch(
            r"[0-9]{4}",
            code
        ):

            continue

        item = make_item(
            code,
            name,
            "TW",
            "stock",
            "TWSE"
        )

        if item:

            result.append(
                item
            )

    unique = {}

    for item in result:

        unique[
            item["code"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"   TWSE 股票 Universe："
        f"{len(result)}"
    )

    return result


# ============================================================
# TPEx 上櫃股票
#
# 使用 TPEx 官方 CSV / JSON 資料
# 不使用 pandas.read_html
# ============================================================

def fetch_tpex_stocks():

    print(
        "🔎 取得 TPEx 官方上櫃股票 Universe..."
    )

    urls = [

        (
            "https://www.tpex.org.tw/"
            "storage/eb_data/"
            "1230/"
            "TPEX_mainboard.csv"
        ),

        (
            "https://www.tpex.org.tw/"
            "storage/eb_data/"
            "tpex_mainboard.csv"
        ),

        (
            "https://www.tpex.org.tw/"
            "web/stock/aftertrading/"
            "daily_trading_info/"
            "st43.php"
        ),
    ]

    for url in urls:

        response = http_get(
            url
        )

        if response is None:

            continue

        content = response.content

        # ----------------------------------------------------
        # 嘗試 CSV
        # ----------------------------------------------------

        for encoding in (
            "utf-8-sig",
            "big5",
            "cp950",
            "utf-8",
        ):

            try:

                text = content.decode(
                    encoding
                )

                if (
                    "代號" not in text
                    and "代碼" not in text
                    and "Code" not in text
                ):

                    continue

                lines = text.splitlines()

                result = parse_tpex_stock_lines(
                    lines
                )

                if len(result) >= MIN_TPEX_STOCKS:

                    print(
                        f"   TPEx 股票 Universe："
                        f"{len(result)}"
                    )

                    return result

            except Exception:

                continue

    # --------------------------------------------------------
    # 第二方案：
    # TPEx 網頁內 JSON / HTML source 中直接抓代號
    # --------------------------------------------------------

    page_urls = [
        (
            "https://www.tpex.org.tw/"
            "zh-tw/mainboard/listed/"
            "company.html"
        ),
        (
            "https://www.tpex.org.tw/"
            "web/stock/aftertrading/"
            "daily_trading_info/"
            "st43.php"
        ),
    ]

    for url in page_urls:

        response = http_get(
            url
        )

        if response is None:

            continue

        try:

            text = response.content.decode(
                "utf-8",
                errors="ignore"
            )

            result = parse_tpex_embedded_codes(
                text
            )

            if len(result) >= MIN_TPEX_STOCKS:

                print(
                    f"   TPEx 股票 Universe："
                    f"{len(result)}"
                )

                return result

        except Exception:

            continue

    print(
        "   ❌ TPEx 上櫃股票 Universe 無法取得"
    )

    return []


def parse_tpex_stock_lines(
    lines
):

    result = []

    for line in lines:

        line = line.strip()

        if not line:

            continue

        # ----------------------------------------------------
        # CSV
        # ----------------------------------------------------

        parts = re.split(
            r",|\t",
            line
        )

        if not parts:

            continue

        code = parts[0].strip(
            '" '
        )

        if not re.fullmatch(
            r"[0-9]{4,6}",
            code
        ):

            continue

        # 上櫃一般股票主要為四碼
        if len(code) != 4:

            continue

        name = (
            parts[1].strip(
                '" '
            )
            if len(parts) > 1
            else code
        )

        item = make_item(
            code,
            name,
            "TWO",
            "stock",
            "TPEx"
        )

        if item:

            result.append(
                item
            )

    unique = {}

    for item in result:

        unique[
            item["code"]
        ] = item

    return list(
        unique.values()
    )


def parse_tpex_embedded_codes(
    text
):

    result = []

    # --------------------------------------------------------
    # 只抓可能的股票代號
    # 避免把年份、電話等數字當股票
    # --------------------------------------------------------

    patterns = [
        r'"([0-9]{4})"',
        r"'([0-9]{4})'",
        r">\s*([0-9]{4})\s*<",
    ]

    codes = set()

    for pattern in patterns:

        for match in re.findall(
            pattern,
            text
        ):

            codes.add(
                match
            )

    for code in sorted(
        codes
    ):

        item = make_item(
            code,
            code,
            "TWO",
            "stock",
            "TPEx"
        )

        if item:

            result.append(
                item
            )

    return result


# ============================================================
# TWSE ETF
#
# 官方 ISIN API / HTML endpoint
# 不使用 read_html
# ============================================================

def fetch_twse_etfs():

    print(
        "🔎 取得 TWSE 官方 ETF Universe..."
    )

    urls = [

        (
            "https://isin.twse.com.tw/"
            "isin/C_public.jsp?"
            "strMode=2"
        ),

        (
            "https://isin.twse.com.tw/"
            "isin/e_C_public.jsp?"
            "strMode=2"
        ),
    ]

    for url in urls:

        response = http_get(
            url
        )

        if response is None:

            continue

        try:

            text = response.content.decode(
                "utf-8",
                errors="ignore"
            )

            result = parse_isin_etf_text(
                text,
                "TW"
            )

            if len(result) >= MIN_ETF_COUNT:

                print(
                    f"   TWSE ETF Universe："
                    f"{len(result)}"
                )

                return result

        except Exception as e:

            print(
                f"   ⚠️ TWSE ETF 解析失敗：{e}"
            )

    print(
        "   ❌ TWSE ETF Universe 無法取得"
    )

    return []


# ============================================================
# TPEx ETF
#
# 先從官方 ETF 分類頁抓代號，
# 再補名稱。
# ============================================================

def fetch_tpex_etfs():

    print(
        "🔎 取得 TPEx 官方 ETF Universe..."
    )

    urls = [

        (
            "https://www.tpex.org.tw/"
            "zh-tw/product/etf/"
            "overview/categories.html"
        ),

        (
            "https://www.tpex.org.tw/"
            "web/etf/etf_list/"
            "etf_list.php"
        ),
    ]

    all_result = []

    for url in urls:

        response = http_get(
            url
        )

        if response is None:

            continue

        try:

            text = response.content.decode(
                "utf-8",
                errors="ignore"
            )

            result = parse_tpex_etf_text(
                text
            )

            all_result.extend(
                result
            )

        except Exception:

            continue

    unique = {}

    for item in all_result:

        unique[
            item["code"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"   TPEx ETF Universe："
        f"{len(result)}"
    )

    return result


def parse_isin_etf_text(
    text,
    market
):

    result = []

    # --------------------------------------------------------
    # 官方 ISIN 表格通常含：
    #
    # ISIN Code
    # Security Code
    # Security Name
    # Market
    # Type of security
    #
    # 這裡不用 read_html，
    # 直接從文字抓取。
    # --------------------------------------------------------

    lines = text.splitlines()

    for line in lines:

        line_clean = re.sub(
            r"\s+",
            " ",
            line
        ).strip()

        if "ETF" not in line_clean.upper():

            continue

        # ----------------------------------------------------
        # 找股票代號
        # ----------------------------------------------------

        codes = re.findall(
            r"\b[0-9]{4,6}[A-Z]?\b",
            line_clean
        )

        if not codes:

            continue

        code = None

        for candidate in codes:

            if (
                4 <= len(candidate) <= 7
                and candidate[0].isdigit()
            ):

                code = candidate

                break

        if not code:

            continue

        # ----------------------------------------------------
        # 名稱
        # ----------------------------------------------------

        name = line_clean

        name = re.sub(
            r"TW[0-9A-Z]{10,}",
            " ",
            name
        )

        name = name.replace(
            code,
            " "
        )

        name = re.sub(
            r"\s+",
            " ",
            name
        ).strip()

        if not name:

            name = code

        asset_type = (
            "bond"
            if is_bond_etf(
                code,
                name
            )
            else "etf"
        )

        item = make_item(
            code,
            name,
            market,
            asset_type,
            "TWSE_ISIN"
        )

        if item:

            result.append(
                item
            )

    unique = {}

    for item in result:

        unique[
            item["code"]
        ] = item

    return list(
        unique.values()
    )


def parse_tpex_etf_text(
    text
):

    result = []

    # --------------------------------------------------------
    # 抓 ETF 代號
    #
    # TPEx ETF 代號可能為：
    # 00xxxx
    # 00xxxxA
    # 00xxxxB
    # 等
    # --------------------------------------------------------

    codes = set(
        re.findall(
            r"\b00[0-9A-Z]{4,5}\b",
            text.upper()
        )
    )

    for code in codes:

        if len(code) < 6:

            continue

        asset_type = (
            "bond"
            if is_bond_etf(
                code,
                ""
            )
            else "etf"
        )

        item = make_item(
            code,
            code,
            "TWO",
            asset_type,
            "TPEx"
        )

        if item:

            result.append(
                item
            )

    return result


# ============================================================
# 官方 ETF 名稱補充
# ============================================================

def enrich_etf_names(
    etfs
):

    if not etfs:

        return etfs

    # --------------------------------------------------------
    # 使用 TWSE ISIN 再次嘗試補名稱
    # --------------------------------------------------------

    response = http_get(
        "https://isin.twse.com.tw/"
        "isin/C_public.jsp?"
        "strMode=2"
    )

    if response is None:

        return etfs

    try:

        text = response.content.decode(
            "utf-8",
            errors="ignore"
        )

    except Exception:

        return etfs

    name_map = {}

    for line in text.splitlines():

        if "ETF" not in line.upper():

            continue

        codes = re.findall(
            r"\b[0-9]{4,6}[A-Z]?\b",
            line
        )

        if not codes:

            continue

        for code in codes:

            if code in (
                "2026",
                "2025",
                "2024",
            ):

                continue

            name_map[
                code
            ] = line.strip()

    for item in etfs:

        code = item[
            "code"
        ]

        if (
            item["name"] == code
            and code in name_map
        ):

            item["name"] = (
                name_map[code]
            )

            if is_bond_etf(
                code,
                item["name"]
            ):

                item["type"] = "bond"

    return etfs


# ============================================================
# 建立全市場 Universe
# ============================================================

def build_full_market_universe():

    print()
    print("=" * 64)
    print(
        "建立 V10.6 全市場 Universe"
    )
    print("=" * 64)

    # --------------------------------------------------------
    # 1. TWSE
    # --------------------------------------------------------

    twse_stocks = (
        fetch_twse_stocks()
    )

    if len(
        twse_stocks
    ) < MIN_TWSE_STOCKS:

        print(
            "❌ TWSE 上市股票數量異常："
            f"{len(twse_stocks)}"
        )

        raise RuntimeError(
            "TWSE Universe 不完整"
        )

    # --------------------------------------------------------
    # 2. TPEx stocks
    # --------------------------------------------------------

    tpex_stocks = (
        fetch_tpex_stocks()
    )

    if len(
        tpex_stocks
    ) < MIN_TPEX_STOCKS:

        print(
            "❌ TPEx 上櫃股票數量異常："
            f"{len(tpex_stocks)}"
        )

        raise RuntimeError(
            "TPEx Universe 不完整"
        )

    # --------------------------------------------------------
    # 3. TWSE ETF
    # --------------------------------------------------------

    twse_etfs = (
        fetch_twse_etfs()
    )

    # --------------------------------------------------------
    # 4. TPEx ETF
    # --------------------------------------------------------

    tpex_etfs = (
        fetch_tpex_etfs()
    )

    all_etfs = (
        twse_etfs
        + tpex_etfs
    )

    all_etfs = enrich_etf_names(
        all_etfs
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in (
        twse_stocks
        + tpex_stocks
        + all_etfs
    ):

        code = item[
            "code"
        ]

        if code not in unique:

            unique[
                code
            ] = item

            continue

        # ----------------------------------------------------
        # ETF 優先
        # ----------------------------------------------------

        if item["type"] in (
            "etf",
            "bond",
        ):

            unique[
                code
            ] = item

    universe = list(
        unique.values()
    )

    universe.sort(
        key=lambda x: (
            x["type"],
            x["market"],
            x["code"],
        )
    )

    stock_count = sum(
        1
        for x in universe
        if x["type"] == "stock"
    )

    etf_count = sum(
        1
        for x in universe
        if x["type"] == "etf"
    )

    bond_count = sum(
        1
        for x in universe
        if x["type"] == "bond"
    )

    total_count = len(
        universe
    )

    print()
    print(
        "全市場 Universe 統計："
    )

    print(
        f"  上市股票："
        f"{len(twse_stocks)}"
    )

    print(
        f"  上櫃股票："
        f"{len(tpex_stocks)}"
    )

    print(
        f"  ETF："
        f"{etf_count}"
    )

    print(
        f"  債券 ETF："
        f"{bond_count}"
    )

    print(
        f"  股票總數："
        f"{stock_count}"
    )

    print(
        f"  Universe 總數："
        f"{total_count}"
    )

    # --------------------------------------------------------
    # 嚴格驗證
    # --------------------------------------------------------

    if stock_count < MIN_TOTAL_STOCKS:

        raise RuntimeError(
            "全市場股票數量不足，"
            f"目前只有 {stock_count}"
        )

    if len(
        twse_stocks
    ) < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE 股票 Universe 不完整"
        )

    if len(
        tpex_stocks
    ) < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx 股票 Universe 不完整"
        )

    if not all_etfs:

        print(
            "⚠️ ETF Universe 尚未取得"
        )

    print()
    print(
        "✅ V10.6 全市場 Universe 建立成功"
    )

    return universe, {
        "twse_stock_count":
            len(twse_stocks),

        "tpex_stock_count":
            len(tpex_stocks),

        "etf_count":
            etf_count,

        "bond_etf_count":
            bond_count,

        "stock_count":
            stock_count,

        "total_count":
            total_count,

        "source":
            "TWSE + TPEx + TWSE ISIN",

        "is_full_market":
            True,
    }


# ============================================================
# Yahoo History
# ============================================================

def fetch_yahoo_history(
    symbol
):

    period2 = int(
        time.time()
    )

    period1 = (
        period2
        - HISTORY_PERIOD_DAYS
        * 86400
    )

    url = YAHOO_CHART_URL.format(
        symbol=symbol
    )

    params = {
        "period1":
            period1,

        "period2":
            period2,

        "interval":
            "1d",

        "events":
            "history",

        "includeAdjustedClose":
            "true",
    }

    response = http_get(
        url,
        params=params
    )

    if response is None:

        return None

    try:

        payload = response.json()

        chart = payload.get(
            "chart",
            {}
        )

        if chart.get(
            "error"
        ):

            return None

        results = chart.get(
            "result"
        )

        if not results:

            return None

        result = results[0]

        timestamps = result.get(
            "timestamp"
        )

        indicators = result.get(
            "indicators",
            {}
        )

        quote_list = indicators.get(
            "quote",
            []
        )

        if (
            not timestamps
            or not quote_list
        ):

            return None

        quote = quote_list[0]

        adjclose_list = indicators.get(
            "adjclose",
            []
        )

        adjclose = None

        if adjclose_list:

            adjclose = (
                adjclose_list[0]
                .get(
                    "adjclose"
                )
            )

        close_list = quote.get(
            "close",
            []
        )

        open_list = quote.get(
            "open",
            []
        )

        high_list = quote.get(
            "high",
            []
        )

        low_list = quote.get(
            "low",
            []
        )

        volume_list = quote.get(
            "volume",
            []
        )

        rows = []

        for i, ts in enumerate(
            timestamps
        ):

            try:

                dt = (
                    datetime
                    .fromtimestamp(
                        ts,
                        tz=timezone.utc
                    )
                    .astimezone(
                        TIMEZONE_TW
                    )
                )

                date_str = (
                    dt.strftime(
                        "%Y-%m-%d"
                    )
                )

            except Exception:

                continue

            close = (
                close_list[i]
                if i < len(close_list)
                else None
            )

            if close is None:

                continue

            close = safe_float(
                close
            )

            if close is None:

                continue

            open_price = (
                open_list[i]
                if i < len(open_list)
                else None
            )

            high = (
                high_list[i]
                if i < len(high_list)
                else None
            )

            low = (
                low_list[i]
                if i < len(low_list)
                else None
            )

            volume = (
                volume_list[i]
                if i < len(volume_list)
                else None
            )

            adj = (
                adjclose[i]
                if (
                    adjclose is not None
                    and i < len(adjclose)
                )
                else close
            )

            rows.append(
                {
                    "date":
                        date_str,

                    "open":
                        safe_float(
                            open_price
                        ),

                    "high":
                        safe_float(
                            high
                        ),

                    "low":
                        safe_float(
                            low
                        ),

                    "close":
                        close,

                    "adj_close":
                        safe_float(
                            adj,
                            close
                        ),

                    "volume":
                        safe_float(
                            volume,
                            0
                        ),
                }
            )

        if not rows:

            return None

        df = pd.DataFrame(
            rows
        )

        df["date"] = pd.to_datetime(
            df["date"],
            errors="coerce"
        )

        df = df.dropna(
            subset=[
                "date",
                "close",
            ]
        )

        df = df.sort_values(
            "date"
        )

        df = df.drop_duplicates(
            subset=[
                "date"
            ],
            keep="last"
        )

        df = df.reset_index(
            drop=True
        )

        return df

    except Exception as e:

        print(
            f"   ⚠️ {symbol} "
            f"行情解析失敗：{e}"
        )

        return None


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(
    df
):

    df = df.copy()

    close = pd.to_numeric(
        df["close"],
        errors="coerce"
    )

    high = pd.to_numeric(
        df["high"],
        errors="coerce"
    )

    low = pd.to_numeric(
        df["low"],
        errors="coerce"
    )

    volume = pd.to_numeric(
        df["volume"],
        errors="coerce"
    ).fillna(0)

    # --------------------------------------------------------
    # MA5
    # --------------------------------------------------------

    df["ma5"] = (
        close
        .rolling(
            5,
            min_periods=5
        )
        .mean()
    )

    # --------------------------------------------------------
    # MA20
    # --------------------------------------------------------

    df["ma20"] = (
        close
        .rolling(
            20,
            min_periods=20
        )
        .mean()
    )

    # --------------------------------------------------------
    # MACD 12 / 26 / 9
    # --------------------------------------------------------

    ema12 = (
        close
        .ewm(
            span=12,
            adjust=False,
            min_periods=12
        )
        .mean()
    )

    ema26 = (
        close
        .ewm(
            span=26,
            adjust=False,
            min_periods=26
        )
        .mean()
    )

    df["macd"] = (
        ema12
        - ema26
    )

    df["macd_signal"] = (
        df["macd"]
        .ewm(
            span=9,
            adjust=False,
            min_periods=9
        )
        .mean()
    )

    df["macd_hist"] = (
        df["macd"]
        - df["macd_signal"]
    )

    # --------------------------------------------------------
    # RSI 14
    # --------------------------------------------------------

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / 14,
            adjust=False,
            min_periods=14
        )
        .mean()
    )

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    df["rsi"] = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    df.loc[
        (
            avg_loss == 0
        )
        &
        (
            avg_gain > 0
        ),
        "rsi"
    ] = 100.0

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    lowest_low = (
        low
        .rolling(
            9,
            min_periods=9
        )
        .min()
    )

    highest_high = (
        high
        .rolling(
            9,
            min_periods=9
        )
        .max()
    )

    denominator = (
        highest_high
        - lowest_low
    )

    rsv = (
        (
            close
            - lowest_low
        )
        /
        denominator.replace(
            0,
            np.nan
        )
    ) * 100

    k_values = []

    d_values = []

    previous_k = 50.0
    previous_d = 50.0

    for value in rsv:

        if pd.isna(value):

            k_values.append(
                np.nan
            )

            d_values.append(
                np.nan
            )

            continue

        current_k = (
            previous_k * 2 / 3
            +
            float(value) / 3
        )

        current_d = (
            previous_d * 2 / 3
            +
            current_k / 3
        )

        k_values.append(
            current_k
        )

        d_values.append(
            current_d
        )

        previous_k = (
            current_k
        )

        previous_d = (
            current_d
        )

    df["k"] = (
        k_values
    )

    df["d"] = (
        d_values
    )

    # --------------------------------------------------------
    # Volume MA5
    # --------------------------------------------------------

    df["volume_ma5"] = (
        volume
        .rolling(
            5,
            min_periods=5
        )
        .mean()
    )

    return df


# ============================================================
# 六項條件
# ============================================================

def evaluate_core_conditions(
    df
):

    if (
        df is None
        or len(df) < MIN_HISTORY_ROWS
    ):

        return {
            "core_score":
                0,

            "core_total":
                CORE_TOTAL,

            "core_pass":
                False,

            "conditions":
                {},
        }

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    conditions = {}

    conditions[
        "MACD 多方"
    ] = (
        pd.notna(
            latest["macd"]
        )
        and pd.notna(
            latest["macd_signal"]
        )
        and latest["macd"]
        >
        latest["macd_signal"]
    )

    conditions[
        "RSI > 50"
    ] = (
        pd.notna(
            latest["rsi"]
        )
        and latest["rsi"] > 50
    )

    conditions[
        "KD 多方"
    ] = (
        pd.notna(
            latest["k"]
        )
        and pd.notna(
            latest["d"]
        )
        and latest["k"]
        >
        latest["d"]
    )

    conditions[
        "成交量 ≥ MA5 × 1.5"
    ] = (
        pd.notna(
            latest["volume"]
        )
        and pd.notna(
            latest["volume_ma5"]
        )
        and latest["volume"]
        >=
        latest["volume_ma5"]
        * 1.5
    )

    conditions[
        "股價 > MA20"
    ] = (
        pd.notna(
            latest["close"]
        )
        and pd.notna(
            latest["ma20"]
        )
        and latest["close"]
        >
        latest["ma20"]
    )

    conditions[
        "MA20 今日 > 昨日"
    ] = (
        pd.notna(
            latest["ma20"]
        )
        and pd.notna(
            previous["ma20"]
        )
        and latest["ma20"]
        >
        previous["ma20"]
    )

    score = sum(
        1
        for value in conditions.values()
        if bool(value)
    )

    return {
        "core_score":
            score,

        "core_total":
            CORE_TOTAL,

        "core_pass":
            score == CORE_TOTAL,

        "conditions":
            conditions,
    }


# ============================================================
# Score
# ============================================================

def calculate_score(
    df,
    core
):

    if (
        df is None
        or len(df) < MIN_HISTORY_ROWS
    ):

        return 0.0, 0.0

    latest = df.iloc[-1]

    score = (
        core["core_score"]
        /
        CORE_TOTAL
    ) * 70.0

    rsi = safe_float(
        latest.get(
            "rsi"
        )
    )

    if rsi is not None:

        if rsi >= 70:

            score += 5.0

        elif rsi > 50:

            score += 8.0

        elif rsi >= 45:

            score += 3.0

    macd_hist = safe_float(
        latest.get(
            "macd_hist"
        )
    )

    if (
        macd_hist is not None
        and macd_hist > 0
    ):

        score += 5.0

    close = safe_float(
        latest.get(
            "close"
        )
    )

    ma20 = safe_float(
        latest.get(
            "ma20"
        )
    )

    if (
        close is not None
        and ma20 is not None
        and ma20 != 0
    ):

        bias = (
            close
            /
            ma20
            - 1
        ) * 100

        if bias >= 0:

            score += min(
                max(
                    bias,
                    0
                ),
                5
            )

    volume = safe_float(
        latest.get(
            "volume"
        )
    )

    volume_ma5 = safe_float(
        latest.get(
            "volume_ma5"
        )
    )

    if (
        volume is not None
        and volume_ma5 is not None
        and volume_ma5 > 0
    ):

        ratio = (
            volume
            /
            volume_ma5
        )

        if ratio >= 1.5:

            score += 7.0

        elif ratio >= 1.0:

            score += 3.0

    score = min(
        max(
            score,
            0
        ),
        100
    )

    strength = (
        core["core_score"]
        /
        CORE_TOTAL
    ) * 100

    if (
        macd_hist is not None
        and macd_hist > 0
    ):

        strength += 3

    if (
        rsi is not None
        and rsi > 50
    ):

        strength += 3

    strength = min(
        max(
            strength,
            0
        ),
        100
    )

    return (
        round(
            score,
            2
        ),
        round(
            strength,
            2
        )
    )


# ============================================================
# Rating
# ============================================================

def rating_from_score(
    score
):

    if score >= 90:

        return "A+"

    if score >= 80:

        return "A"

    if score >= 70:

        return "B"

    if score >= 60:

        return "C"

    return "D"


def signal_from_score(
    score
):

    if score >= 80:

        return "強勢多方"

    if score >= 65:

        return "偏多"

    if score >= 50:

        return "中性"

    if score >= 35:

        return "偏弱"

    return "弱勢"


def recommendation_from_core(
    score
):

    if score == CORE_TOTAL:

        return (
            f"符合 {CORE_TOTAL}/{CORE_TOTAL} 核心條件"
        )

    if score >= CORE_TOTAL - 1:

        return "接近核心條件"

    if score >= CORE_TOTAL - 2:

        return "部分符合條件"

    return "暫不操作"


# ============================================================
# Analyze
# ============================================================

def analyze_symbol(
    item,
    df
):

    if df is None:

        return None

    if len(df) < MIN_HISTORY_ROWS:

        return None

    df = calculate_indicators(
        df
    )

    df = df.dropna(
        subset=[
            "close"
        ]
    ).reset_index(
        drop=True
    )

    if len(df) < MIN_HISTORY_ROWS:

        return None

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    latest_date = (
        pd.Timestamp(
            latest["date"]
        ).date()
    )

    previous_date = (
        pd.Timestamp(
            previous["date"]
        ).date()
    )

    # --------------------------------------------------------
    # 未來日期保護
    # --------------------------------------------------------

    if (
        latest_date
        >
        today_tw_date()
    ):

        return None

    close = safe_float(
        latest["close"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    if (
        close is None
        or previous_close is None
        or previous_close == 0
    ):

        change_pct = None

    else:

        change_pct = (
            (
                close
                -
                previous_close
            )
            /
            previous_close
        ) * 100

    core = (
        evaluate_core_conditions(
            df
        )
    )

    ai_score, strength_score = (
        calculate_score(
            df,
            core
        )
    )

    return {

        "code":
            item["code"],

        "symbol":
            item["symbol"],

        "name":
            item["name"],

        "market":
            item["market"],

        "type":
            item["type"],

        "price":
            round(
                close,
                4
            )
            if close is not None
            else None,

        "change_pct":
            round(
                change_pct,
                4
            )
            if change_pct is not None
            else None,

        "ai_score":
            ai_score,

        "strength_score":
            strength_score,

        "signal":
            signal_from_score(
                strength_score
            ),

        "rating":
            rating_from_score(
                ai_score
            ),

        "recommendation":
            recommendation_from_core(
                core["core_score"]
            ),

        "core_score":
            core["core_score"],

        "core_total":
            CORE_TOTAL,

        "core_pass":
            core["core_pass"],

        "market_date":
            latest_date.isoformat(),

        "previous_market_date":
            previous_date.isoformat(),

        "indicators": {

            "rsi":
                safe_float(
                    latest["rsi"]
                ),

            "macd":
                safe_float(
                    latest["macd"]
                ),

            "macd_signal":
                safe_float(
                    latest["macd_signal"]
                ),

            "macd_hist":
                safe_float(
                    latest["macd_hist"]
                ),

            "k":
                safe_float(
                    latest["k"]
                ),

            "d":
                safe_float(
                    latest["d"]
                ),

            "ma5":
                safe_float(
                    latest["ma5"]
                ),

            "ma20":
                safe_float(
                    latest["ma20"]
                ),

            "previous_ma20":
                safe_float(
                    previous["ma20"]
                ),

            "volume":
                safe_float(
                    latest["volume"]
                ),

            "volume_ma5":
                safe_float(
                    latest["volume_ma5"]
                ),
        },

        "core_conditions": {
            key:
                bool(value)
            for key, value
            in core[
                "conditions"
            ].items()
        },
    }


# ============================================================
# 最新交易日
# ============================================================

def determine_latest_market_date(
    results
):

    dates = []

    for item in results:

        value = item.get(
            "market_date"
        )

        if not value:

            continue

        try:

            dt = pd.Timestamp(
                value
            ).date()

            if (
                dt
                <=
                today_tw_date()
            ):

                dates.append(
                    dt
                )

        except Exception:

            continue

    if not dates:

        return None

    return max(
        dates
    )


# ============================================================
# 最新交易日過濾
# ============================================================

def filter_to_latest_market_date(
    results,
    latest_market_date
):

    if latest_market_date is None:

        return []

    target = (
        latest_market_date
        .isoformat()
    )

    return [
        item
        for item in results
        if item.get(
            "market_date"
        )
        ==
        target
    ]


# ============================================================
# Breadth
# ============================================================

def calculate_market_breadth(
    results
):

    rising = 0

    falling = 0

    unchanged = 0

    for item in results:

        change = safe_float(
            item.get(
                "change_pct"
            )
        )

        if change is None:

            continue

        if change > 0:

            rising += 1

        elif change < 0:

            falling += 1

        else:

            unchanged += 1

    return {

        "rising":
            rising,

        "falling":
            falling,

        "unchanged":
            unchanged,

        "total_with_change":
            (
                rising
                +
                falling
                +
                unchanged
            ),
    }


# ============================================================
# Backtest
# ============================================================

def calculate_backtest(
    universe,
    history_cache
):

    a_results = []

    b_results = []

    eligible_symbols = 0

    for item in universe:

        symbol = item[
            "symbol"
        ]

        # ----------------------------------------------------
        # 只對股票做核心條件回測
        # ----------------------------------------------------

        if item["type"] != "stock":

            continue

        df = history_cache.get(
            symbol
        )

        if df is None:

            continue

        if len(df) < (
            MIN_HISTORY_ROWS
            +
            BACKTEST_HORIZON
        ):

            continue

        eligible_symbols += 1

        df = calculate_indicators(
            df
        )

        idx_a = (
            len(df)
            -
            1
            -
            BACKTEST_HORIZON
        )

        idx_b = (
            idx_a
            -
            1
        )

        if idx_b < 1:

            continue

        for idx, bucket in (
            (
                idx_a,
                a_results
            ),
            (
                idx_b,
                b_results
            ),
        ):

            row = df.iloc[
                idx
            ]

            prev = df.iloc[
                idx - 1
            ]

            conditions = [

                (
                    pd.notna(
                        row["macd"]
                    )
                    and
                    pd.notna(
                        row["macd_signal"]
                    )
                    and
                    row["macd"]
                    >
                    row["macd_signal"]
                ),

                (
                    pd.notna(
                        row["rsi"]
                    )
                    and
                    row["rsi"] > 50
                ),

                (
                    pd.notna(
                        row["k"]
                    )
                    and
                    pd.notna(
                        row["d"]
                    )
                    and
                    row["k"]
                    >
                    row["d"]
                ),

                (
                    pd.notna(
                        row["volume"]
                    )
                    and
                    pd.notna(
                        row["volume_ma5"]
                    )
                    and
                    row["volume"]
                    >=
                    row["volume_ma5"]
                    * 1.5
                ),

                (
                    pd.notna(
                        row["close"]
                    )
                    and
                    pd.notna(
                        row["ma20"]
                    )
                    and
                    row["close"]
                    >
                    row["ma20"]
                ),

                (
                    pd.notna(
                        row["ma20"]
                    )
                    and
                    pd.notna(
                        prev["ma20"]
                    )
                    and
                    row["ma20"]
                    >
                    prev["ma20"]
                ),
            ]

            if not all(
                conditions
            ):

                continue

            future_idx = (
                idx
                +
                BACKTEST_HORIZON
            )

            if future_idx >= len(df):

                continue

            entry = safe_float(
                row["close"]
            )

            future = safe_float(
                df.iloc[
                    future_idx
                ]["close"]
            )

            if (
                entry is None
                or future is None
            ):

                continue

            bucket.append(
                future > entry
            )

    def win_rate(
        values
    ):

        if not values:

            return None

        return round(
            sum(values)
            /
            len(values)
            *
            100,
            2
        )

    a_rate = win_rate(
        a_results
    )

    b_rate = win_rate(
        b_results
    )

    if (
        a_rate is None
        and
        b_rate is None
    ):

        better = None

    elif b_rate is None:

        better = (
            "A_latest_cross"
        )

    elif a_rate is None:

        better = (
            "B_previous_cross"
        )

    elif a_rate > b_rate:

        better = (
            "A_latest_cross"
        )

    elif b_rate > a_rate:

        better = (
            "B_previous_cross"
        )

    else:

        better = "tie"

    return {

        "comparison_horizon":
            BACKTEST_HORIZON,

        "method":
            "trading_days",

        "better_by_win_rate":
            better,

        "A_10d_win_rate":
            a_rate,

        "B_10d_win_rate":
            b_rate,

        "A_sample_count":
            len(a_results),

        "B_sample_count":
            len(b_results),

        "eligible_history_count":
            eligible_symbols,
    }


# ============================================================
# Universe Summary
# ============================================================

def build_universe_summary(
    universe
):

    stock_count = sum(
        1
        for x in universe
        if x["type"] == "stock"
    )

    etf_count = sum(
        1
        for x in universe
        if x["type"] == "etf"
    )

    bond_count = sum(
        1
        for x in universe
        if x["type"] == "bond"
    )

    tw_count = sum(
        1
        for x in universe
        if (
            x["market"] == "TW"
            and
            x["type"] == "stock"
        )
    )

    two_count = sum(
        1
        for x in universe
        if (
            x["market"] == "TWO"
            and
            x["type"] == "stock"
        )
    )

    return {

        "stock_count":
            stock_count,

        "twse_stock_count":
            tw_count,

        "tpex_stock_count":
            two_count,

        "etf_count":
            etf_count,

        "bond_count":
            bond_count,

        "total_count":
            len(universe),

        "is_full_market":
            True,

        "items":
            universe,
    }


# ============================================================
# Save JSON
# ============================================================

def save_json(
    data
):

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    data = clean_json_value(
        data
    )

    temp_file = (
        PRICES_FILE
        +
        ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

        f.write(
            "\n"
        )

    os.replace(
        temp_file,
        PRICES_FILE
    )


# ============================================================
# Main
# ============================================================

def main():

    print("=" * 64)

    print(
        "台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
    )

    print("=" * 64)

    start_time = now_tw()

    print(
        "開始時間：",
        start_time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    # ========================================================
    # 1. 建立全市場 Universe
    # ========================================================

    try:

        universe, universe_meta = (
            build_full_market_universe()
        )

    except Exception as e:

        print()
        print(
            "❌ 全市場 Universe 建立失敗"
        )

        print(
            f"❌ 原因：{e}"
        )

        print(
            "❌ 為避免產生錯誤的 "
            "prices.json，本次 Action 直接失敗。"
        )

        sys.exit(1)

    # ========================================================
    # 2. Fetch Yahoo
    # ========================================================

    print()
    print("=" * 64)
    print(
        "開始取得全市場歷史行情"
    )
    print("=" * 64)

    analyzed = []

    history_cache = {}

    total = len(
        universe
    )

    success_count = 0

    fail_count = 0

    for idx, item in enumerate(
        universe,
        start=1
    ):

        symbol = item[
            "symbol"
        ]

        if (
            idx == 1
            or idx % 50 == 0
            or idx == total
        ):

            print(
                f"[{idx}/{total}] "
                f"成功 {success_count} / "
                f"失敗 {fail_count}"
            )

        df = fetch_yahoo_history(
            symbol
        )

        if df is None:

            fail_count += 1

            time.sleep(
                REQUEST_SLEEP
            )

            continue

        if len(df) < MIN_HISTORY_ROWS:

            fail_count += 1

            time.sleep(
                REQUEST_SLEEP
            )

            continue

        history_cache[
            symbol
        ] = df

        result = analyze_symbol(
            item,
            df
        )

        if result is None:

            fail_count += 1

            time.sleep(
                REQUEST_SLEEP
            )

            continue

        analyzed.append(
            result
        )

        success_count += 1

        time.sleep(
            REQUEST_SLEEP
        )

    print()
    print(
        f"Universe 總數：{total}"
    )

    print(
        f"行情成功：{success_count}"
    )

    print(
        f"行情失敗：{fail_count}"
    )

    if not analyzed:

        print(
            "❌ 沒有任何有效行情資料"
        )

        sys.exit(1)

    # ========================================================
    # 3. 最新有效交易日
    # ========================================================

    latest_market_date = (
        determine_latest_market_date(
            analyzed
        )
    )

    if latest_market_date is None:

        print(
            "❌ 找不到有效交易日"
        )

        sys.exit(1)

    print()
    print(
        "最新有效交易日：",
        latest_market_date.isoformat()
    )

    print(
        "今天台灣日期：",
        today_tw_date().isoformat()
    )

    if (
        latest_market_date
        <
        today_tw_date()
    ):

        print(
            "ℹ️ 今天沒有新的有效行情，"
            "使用最近一個有效交易日。"
        )

    # ========================================================
    # 4. 統一交易日
    # ========================================================

    analyzed = (
        filter_to_latest_market_date(
            analyzed,
            latest_market_date
        )
    )

    if not analyzed:

        print(
            "❌ 最新交易日沒有有效資料"
        )

        sys.exit(1)

    # ========================================================
    # 5. 分類
    # ========================================================

    stocks = [
        x
        for x in analyzed
        if x["type"] == "stock"
    ]

    etfs = [
        x
        for x in analyzed
        if x["type"] == "etf"
    ]

    bonds = [
        x
        for x in analyzed
        if x["type"] == "bond"
    ]

    # ========================================================
    # 6. 6/6
    #
    # 只有股票進入核心選股
    # ========================================================

    today_selected = [
        x
        for x in stocks
        if x["core_pass"] is True
    ]

    # ========================================================
    # 7. Top 10
    # ========================================================

    top10 = sorted(
        today_selected,
        key=lambda x: (
            x.get(
                "ai_score"
            )
            or 0,

            x.get(
                "strength_score"
            )
            or 0,
        ),
        reverse=True
    )[:10]

    # ========================================================
    # 8. ETF 排序
    # ========================================================

    etfs = sorted(
        etfs,
        key=lambda x: (
            x.get(
                "ai_score"
            )
            or 0,

            x.get(
                "strength_score"
            )
            or 0,
        ),
        reverse=True
    )

    # ========================================================
    # 9. Bond 排序
    # ========================================================

    bonds = sorted(
        bonds,
        key=lambda x: (
            x.get(
                "ai_score"
            )
            or 0,

            x.get(
                "strength_score"
            )
            or 0,
        ),
        reverse=True
    )

    # ========================================================
    # 10. Breadth
    #
    # 只統計股票市場
    # ========================================================

    market_breadth = (
        calculate_market_breadth(
            stocks
        )
    )

    # ========================================================
    # 11. Backtest
    # ========================================================

    backtest = (
        calculate_backtest(
            universe,
            history_cache
        )
    )

    # ========================================================
    # 12. Universe Summary
    # ========================================================

    universe_summary = (
        build_universe_summary(
            universe
        )
    )

    # ========================================================
    # 13. Data quality
    # ========================================================

    success_ratio = (
        success_count
        /
        total
        *
        100
        if total
        else 0
    )

    # ========================================================
    # 14. Output
    # ========================================================

    output = {

        "version":
            VERSION,

        "schema_version":
            SCHEMA_VERSION,

        "status":
            "success",

        "date":
            latest_market_date.isoformat(),

        "latest_market_date":
            latest_market_date.isoformat(),

        "updated_at":
            start_time.isoformat(),

        "updated_at_tw":
            start_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            f"fetch_data.py {VERSION}",

        # ----------------------------------------------------
        # Data Quality
        # ----------------------------------------------------

        "data_quality": {

            "is_full_market":
                True,

            "today_is_market_date":
                (
                    latest_market_date
                    ==
                    today_tw_date()
                ),

            "latest_market_date_valid":
                True,

            "non_trading_day_protected":
                (
                    latest_market_date
                    !=
                    today_tw_date()
                ),

            "universe_source":
                universe_meta[
                    "source"
                ],

            "universe_total":
                universe_meta[
                    "total_count"
                ],

            "universe_stock_count":
                universe_meta[
                    "stock_count"
                ],

            "twse_stock_count":
                universe_meta[
                    "twse_stock_count"
                ],

            "tpex_stock_count":
                universe_meta[
                    "tpex_stock_count"
                ],

            "etf_count":
                universe_meta[
                    "etf_count"
                ],

            "bond_etf_count":
                universe_meta[
                    "bond_etf_count"
                ],

            "analyzed_count":
                len(analyzed),

            "successful_history_count":
                success_count,

            "failed_history_count":
                fail_count,

            "history_success_rate":
                round(
                    success_ratio,
                    2
                ),

            "min_history_rows":
                MIN_HISTORY_ROWS,

            "backtest_horizon":
                BACKTEST_HORIZON,

            "backtest_uses_trading_days":
                True,

            "six_of_six_same_market_date":
                True,

            "lxml_required":
                False,
        },

        # ----------------------------------------------------
        # Summary
        # ----------------------------------------------------

        "summary": {

            "stock_count":
                len(stocks),

            "etf_count":
                len(etfs),

            "bond_count":
                len(bonds),

            "today_selected_count":
                len(
                    today_selected
                ),

            "top10_count":
                len(
                    top10
                ),

            "market_breadth":
                market_breadth,

            "core_condition_count":
                CORE_TOTAL,

            "latest_market_date":
                latest_market_date.isoformat(),
        },

        # ----------------------------------------------------
        # Core conditions
        # ----------------------------------------------------

        "core_conditions": {

            "total":
                CORE_TOTAL,

            "names":
                CORE_CONDITION_NAMES,

            "logic": {

                "macd":
                    "MACD > MACD Signal",

                "rsi":
                    "RSI > 50",

                "kd":
                    "K > D",

                "volume":
                    "Volume >= MA5 Volume × 1.5",

                "price_ma20":
                    "Close > MA20",

                "ma20_rising":
                    "MA20[today] > MA20[yesterday]",
            },
        },

        # ----------------------------------------------------
        # 6/6
        # ----------------------------------------------------

        "today_selected":
            today_selected,

        # ----------------------------------------------------
        # Top10
        # ----------------------------------------------------

        "top10":
            top10,

        # ----------------------------------------------------
        # ETF
        # ----------------------------------------------------

        "etfs":
            etfs,

        # ----------------------------------------------------
        # Bond ETF
        # ----------------------------------------------------

        "bonds":
            bonds,

        # ----------------------------------------------------
        # Backtest
        # ----------------------------------------------------

        "backtest_summary":
            backtest,

        # ----------------------------------------------------
        # FULL MARKET UNIVERSE
        #
        # 這裡才是整個系統最重要的資料
        # ----------------------------------------------------

        "universe":
            universe_summary,
    }

    # ========================================================
    # 15. Save
    # ========================================================

    save_json(
        output
    )

    # ========================================================
    # 16. Final
    # ========================================================

    print()
    print("=" * 64)

    print(
        f"{VERSION} 完成"
    )

    print("=" * 64)

    print(
        "date：",
        output[
            "date"
        ]
    )

    print(
        "latest_market_date：",
        output[
            "latest_market_date"
        ]
    )

    print()
    print(
        "【Universe】"
    )

    print(
        "上市股票：",
        universe_meta[
            "twse_stock_count"
        ]
    )

    print(
        "上櫃股票：",
        universe_meta[
            "tpex_stock_count"
        ]
    )

    print(
        "ETF：",
        universe_meta[
            "etf_count"
        ]
    )

    print(
        "債券 ETF：",
        universe_meta[
            "bond_etf_count"
        ]
    )

    print(
        "股票總數：",
        universe_meta[
            "stock_count"
        ]
    )

    print(
        "Universe 總數：",
        universe_meta[
            "total_count"
        ]
    )

    print()
    print(
        "【行情】"
    )

    print(
        "有效分析：",
        len(analyzed)
    )

    print(
        "行情成功：",
        success_count
    )

    print(
        "行情失敗：",
        fail_count
    )

    print()
    print(
        "【六項選股】"
    )

    print(
        "6/6 核心選股：",
        len(today_selected)
    )

    print(
        "Top 10：",
        len(top10)
    )

    print()
    print(
        "【市場廣度】"
    )

    print(
        "上漲：",
        market_breadth[
            "rising"
        ]
    )

    print(
        "下跌：",
        market_breadth[
            "falling"
        ]
    )

    print(
        "平盤：",
        market_breadth[
            "unchanged"
        ]
    )

    print()
    print(
        "輸出：",
        PRICES_FILE
    )

    print(
        "完成時間：",
        now_tw().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print("=" * 64)


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    main()