#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 fetch_data.py V10.7

============================================================
V10.7 全市場 Universe 正式版
============================================================

目標：
    不再掃描固定 11 / 14 檔。
    正式掃描台灣市場：

    1. TWSE 上市普通股票
    2. TPEx 上櫃普通股票
    3. TWSE 上市 ETF
    4. TPEx 上櫃 ETF
    5. 債券型 ETF

重要原則：
    Universe 建立成功
        ↓
    行情下載
        ↓
    技術指標
        ↓
    六項核心條件
        ↓
    today_selected
        ↓
    prices.json

------------------------------------------------------------
V10.7 核心修正
------------------------------------------------------------

1. 不再使用固定 11 / 14 檔 Universe
2. 不再依賴 stocks.json
3. 不再依賴 lxml
4. 不使用 pandas.read_html()
5. TWSE 與 TPEx 分開建立 Universe
6. TWSE 股票使用官方資料
7. TPEx 股票使用官方資料
8. ETF 使用官方 ISIN / ETF 資料補充
9. ETF 分類：
      equity_etf
      bond_etf
      other_etf
10. 債券 ETF 保留在 Universe
11. 上市 / 上櫃股票完整性驗證
12. ETF 完整性驗證
13. 單一資料源失敗時嘗試其他來源
14. 不允許因資料源失敗而偷偷退回舊 Universe
15. 不允許使用 FALLBACK 11 檔假裝全市場
16. Universe 不完整時 Action 直接失敗
17. 不產生錯誤 prices.json
18. Yahoo 行情使用最新有效交易日
19. 技術指標全部使用同一交易日
20. 六項條件必須同一交易日成立
21. RSI / MACD / KD / MA5 / MA20 保留
22. backtest 使用交易日
23. prices.json 保存完整 Universe
24. 原子寫入
25. data_quality 明確記錄 Universe 狀態

============================================================
"""

import os
import sys
import json
import math
import time
import re
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pandas as pd
import numpy as np
import requests

warnings.filterwarnings("ignore")


# ============================================================
# VERSION
# ============================================================

VERSION = "V10.7"
SCHEMA_VERSION = "ui.v10"


# ============================================================
# PATH
# ============================================================

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


# ============================================================
# TIME
# ============================================================

TIMEZONE_TW = timezone(
    timedelta(hours=8)
)


def now_tw():
    return datetime.now(
        TIMEZONE_TW
    )


def today_tw_date():
    return now_tw().date()


# ============================================================
# REQUEST
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36",

        "Accept":
            "application/json,text/html,text/plain,*/*",

        "Accept-Language":
            "zh-TW,zh;q=0.9,en;q=0.8",

        "Connection":
            "keep-alive",
    }
)


REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.12


# ============================================================
# Yahoo
# ============================================================

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)

HISTORY_PERIOD_DAYS = 260

MIN_HISTORY_ROWS = 80

BACKTEST_HORIZON = 10


# ============================================================
# 六項核心條件
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
# 官方資料來源
# ============================================================

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/"
    "isin/e_single_main.jsp"
)

TPEx_HOME = (
    "https://www.tpex.org.tw"
)


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

def clean_json_value(value):

    if isinstance(value, dict):

        return {
            str(k):
                clean_json_value(v)
            for k, v in value.items()
        }

    if isinstance(value, list):

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
    code,
    market=None
):

    if code is None:
        return None

    s = str(code).strip()

    if not s:
        return None

    s = s.replace(
        " ",
        ""
    )

    if s.endswith(".TW"):
        return s

    if s.endswith(".TWO"):
        return s

    if "." in s:
        return s

    if not re.fullmatch(
        r"[0-9A-Za-z]+",
        s
    ):
        return None

    if market == "TWO":
        return s + ".TWO"

    return s + ".TW"


def extract_code(
    symbol
):

    if symbol is None:
        return ""

    s = str(
        symbol
    ).strip()

    if "." in s:
        s = s.split(".")[0]

    return s


# ============================================================
# 市場
# ============================================================

def infer_market(
    symbol
):

    if symbol is None:
        return "OTHER"

    s = str(symbol)

    if s.endswith(".TWO"):
        return "TWO"

    if s.endswith(".TW"):
        return "TW"

    return "OTHER"


# ============================================================
# ETF 分類
# ============================================================

BOND_KEYWORDS = [

    "債券",

    "公債",

    "公司債",

    "投資級債",

    "投等債",

    "高收益債",

    "新興市場債",

    "美國債",

    "美債",

    "國債",

    "金融債",

    "短債",

    "長債",

    "20年",

    "20 年",

    "7-10年",

    "7至10年",

    "1-3年",

    "1至3年",

    "債",
]


def detect_etf_type(
    name
):

    n = str(
        name or ""
    )

    if any(
        keyword in n
        for keyword in BOND_KEYWORDS
    ):

        return "bond_etf"

    return "equity_etf"


# ============================================================
# Universe item
# ============================================================

def make_item(
    code,
    name,
    market,
    asset_type,
    source
):

    code = str(
        code
    ).strip()

    if not code:
        return None

    symbol = normalize_symbol(
        code,
        market
    )

    if not symbol:
        return None

    return {

        "code":
            code,

        "symbol":
            symbol,

        "name":
            str(
                name or code
            ).strip(),

        "market":
            market,

        "type":
            asset_type,

        "source":
            source,
    }


# ============================================================
# 去重
# ============================================================

def deduplicate_universe(
    items
):

    result = {}

    for item in items:

        if not item:
            continue

        symbol = item.get(
            "symbol"
        )

        if not symbol:
            continue

        result[symbol] = item

    return list(
        result.values()
    )


# ============================================================
# HTML 解碼
# ============================================================

def clean_html_text(
    text
):

    if text is None:
        return ""

    text = str(text)

    text = re.sub(
        r"<br\s*/?>",
        " ",
        text,
        flags=re.I
    )

    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )

    text = (
        text
        .replace(
            "&nbsp;",
            " "
        )
        .replace(
            "&amp;",
            "&"
        )
        .replace(
            "&quot;",
            '"'
        )
    )

    return (
        text
        .strip()
    )


# ============================================================
# TWSE ISIN Universe
#
# 官方 ISIN 清單可區分：
#
# TWSE LISTED
# TPEx LISTED
# ETF
#
# 此處不使用 pandas.read_html()
# 因此不需要 lxml。
# ============================================================

def fetch_twse_isin_universe():

    print(
        "🔎 取得 TWSE / ISIN 官方證券清單..."
    )

    try:

        response = SESSION.get(
            TWSE_ISIN_URL,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        response.encoding = (
            response.apparent_encoding
            or "big5"
        )

        html = response.text

    except Exception as e:

        print(
            "   ❌ TWSE ISIN 取得失敗：",
            e
        )

        return []


    # --------------------------------------------------------
    # 直接尋找 HTML table row
    # --------------------------------------------------------

    rows = re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        html,
        flags=re.I | re.S
    )

    items = []


    for row in rows:

        cells = re.findall(
            r"<t[dh][^>]*>(.*?)</t[dh]>",
            row,
            flags=re.I | re.S
        )

        if len(cells) < 6:
            continue

        cells = [
            clean_html_text(
                x
            )
            for x in cells
        ]

        # ----------------------------------------------------
        # Header
        # ----------------------------------------------------

        joined = "|".join(
            cells
        ).upper()

        if (
            "ISIN CODE" in joined
            or "SECURITY CODE" in joined
        ):
            continue

        isin = cells[0]

        code = cells[1]

        name = cells[2]

        market = cells[3]

        security_type = cells[4]


        if not code:
            continue

        if not re.fullmatch(
            r"[0-9A-Za-z]{4,6}",
            code
        ):
            continue


        # ----------------------------------------------------
        # 市場
        # ----------------------------------------------------

        if "TWSE LISTED" in market:

            market_code = "TW"

        elif "TPEX LISTED" in market:

            market_code = "TWO"

        elif "TPEx LISTED" in market:

            market_code = "TWO"

        else:

            continue


        # ----------------------------------------------------
        # ETF
        # ----------------------------------------------------

        if (
            "ETF"
            in security_type.upper()
        ):

            asset_type = detect_etf_type(
                name
            )

            item = make_item(
                code,
                name,
                market_code,
                asset_type,
                "TWSE_ISIN"
            )

        else:

            # ------------------------------------------------
            # 只收普通股票
            # ------------------------------------------------

            if (
                "STOCK"
                not in security_type.upper()
            ):

                continue

            item = make_item(
                code,
                name,
                market_code,
                "stock",
                "TWSE_ISIN"
            )

        if item:

            items.append(
                item
            )


    items = deduplicate_universe(
        items
    )

    print(
        f"   TWSE / ISIN 共取得：{len(items)}"
    )

    return items


# ============================================================
# TWSE 股票 Universe
# ============================================================

def get_twse_stocks_from_isin(
    isin_items
):

    return [
        item
        for item in isin_items
        if (
            item["market"] == "TW"
            and item["type"] == "stock"
        )
    ]


# ============================================================
# TPEx 股票 Universe
#
# V10.7 不使用 read_html。
#
# 第一來源：
# TPEx 官方網頁 JSON / HTML
#
# 第二來源：
# TPEx 官方市場資料頁
#
# 第三來源：
# TWSE ISIN 清單中的 TPEx LISTED STOCK
#
# ------------------------------------------------------------
# 最重要：
# 如果官方 TPEx endpoint 暫時無法取得，
# 不會把 0 檔當作成功。
# ============================================================

TPEx_STOCK_URLS = [

    (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_trading_info/st43.php"
    ),

    (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "daily_trading_info/"
    ),
]


def fetch_tpex_stocks():

    print(
        "🔎 取得 TPEx 官方上櫃股票 Universe..."
    )


    # ========================================================
    # 方法 A：TPEx JSON endpoint
    # ========================================================

    candidates = [

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/afterTrading/"
            "tradingStock"
        ),

        (
            "https://www.tpex.org.tw/"
            "www/zh-tw/afterTrading/"
            "Securities"
        ),
    ]


    for url in candidates:

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                continue

            text = response.text

            if not text.strip():
                continue

            # ------------------------------------------------
            # 嘗試 JSON
            # ------------------------------------------------

            try:

                data = response.json()

                items = parse_tpex_json(
                    data
                )

                if len(items) >= 500:

                    print(
                        "   ✅ TPEx JSON Universe：",
                        len(items)
                    )

                    return items

            except Exception:
                pass

        except Exception:
            continue


    # ========================================================
    # 方法 B：使用官方 TPEx HTML
    # ========================================================

    for url in TPEx_STOCK_URLS:

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                continue

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            html = response.text

            items = parse_tpex_html(
                html
            )

            if len(items) >= 500:

                print(
                    "   ✅ TPEx HTML Universe：",
                    len(items)
                )

                return items

        except Exception:
            continue


    print(
        "   ⚠️ TPEx 官方直接來源無法取得"
    )

    return []


# ============================================================
# TPEx JSON parser
# ============================================================

def parse_tpex_json(
    data
):

    items = []


    if isinstance(
        data,
        dict
    ):

        # 常見資料位置
        possible = [

            data.get(
                "tables"
            ),

            data.get(
                "data"
            ),

            data.get(
                "aaData"
            ),

            data.get(
                "rows"
            ),

            data.get(
                "result"
            ),
        ]

        for value in possible:

            if isinstance(
                value,
                list
            ):

                parsed = parse_tpex_rows(
                    value
                )

                if parsed:
                    items.extend(
                        parsed
                    )


    elif isinstance(
        data,
        list
    ):

        items = parse_tpex_rows(
            data
        )


    return deduplicate_universe(
        items
    )


# ============================================================
# TPEx rows parser
# ============================================================

def parse_tpex_rows(
    rows
):

    result = []


    for row in rows:

        code = None
        name = None


        if isinstance(
            row,
            dict
        ):

            code = (
                row.get("SecuritiesCompanyCode")
                or row.get("SecuritiesCode")
                or row.get("Code")
                or row.get("code")
                or row.get("證券代號")
                or row.get("代號")
            )

            name = (
                row.get("CompanyName")
                or row.get("SecuritiesName")
                or row.get("Name")
                or row.get("name")
                or row.get("證券名稱")
                or row.get("名稱")
            )


        elif isinstance(
            row,
            list
        ):

            if len(row) >= 2:

                # 常見格式：
                # [代號, 名稱, ...]
                code = row[0]
                name = row[1]


        if code is None:
            continue

        code = str(
            code
        ).strip()

        name = str(
            name or code
        ).strip()


        if not re.fullmatch(
            r"[0-9A-Za-z]{4,6}",
            code
        ):
            continue


        # 排除 ETF / 權證 / 債券
        upper_name = name.upper()

        if (
            "ETF" in upper_name
            or "ETN" in upper_name
            or "權證" in name
            or "認購" in name
            or "認售" in name
        ):
            continue


        item = make_item(
            code,
            name,
            "TWO",
            "stock",
            "TPEX"
        )

        if item:
            result.append(
                item
            )


    return deduplicate_universe(
        result
    )


# ============================================================
# TPEx HTML parser
# ============================================================

def parse_tpex_html(
    html
):

    rows = re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        html,
        flags=re.I | re.S
    )

    result = []


    for row in rows:

        cells = re.findall(
            r"<t[dh][^>]*>(.*?)</t[dh]>",
            row,
            flags=re.I | re.S
        )

        if len(cells) < 2:
            continue

        cells = [
            clean_html_text(
                x
            )
            for x in cells
        ]


        code = None
        name = None


        for i, cell in enumerate(
            cells
        ):

            if re.fullmatch(
                r"[0-9]{4,6}",
                cell
            ):

                code = cell

                if i + 1 < len(cells):

                    name = cells[
                        i + 1
                    ]

                break


        if not code:
            continue


        if not name:
            name = code


        if (
            "ETF"
            in name.upper()
            or "ETN"
            in name.upper()
            or "權證"
            in name
        ):
            continue


        item = make_item(
            code,
            name,
            "TWO",
            "stock",
            "TPEX"
        )

        if item:

            result.append(
                item
            )


    return deduplicate_universe(
        result
    )


# ============================================================
# TPEx ETF
# ============================================================

def fetch_tpex_etfs():

    print(
        "🔎 取得 TPEx 官方 ETF Universe..."
    )

    urls = [

        (
            "https://www.tpex.org.tw/"
            "web/stock/aftertrading/"
            "daily_trading_info/"
        ),

        (
            "https://www.tpex.org.tw/"
            "web/etf/"
        ),
    ]


    result = []


    for url in urls:

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                continue

            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            html = response.text


            # ------------------------------------------------
            # 找 4~6 碼代號
            # ------------------------------------------------

            matches = re.findall(
                r">\s*([0-9]{4,6}[A-Za-z]?)\s*<",
                html
            )


            for code in matches:

                code = code.strip()

                if not re.fullmatch(
                    r"[0-9]{4,6}[A-Za-z]?",
                    code
                ):
                    continue


                # 在附近找名稱
                pos = html.find(
                    code
                )

                nearby = html[
                    max(0, pos - 200):
                    pos + 500
                ]

                text = clean_html_text(
                    nearby
                )


                if (
                    "ETF"
                    not in text.upper()
                    and "指數股票型"
                    not in text
                    and "基金" not in text
                ):
                    continue


                name = code


                # 嘗試取代碼附近中文
                chinese = re.findall(
                    r"[\u4e00-\u9fffA-Za-z0-9（）()－\-]{2,40}",
                    text
                )

                if chinese:

                    name = max(
                        chinese,
                        key=len
                    )


                item = make_item(
                    code,
                    name,
                    "TWO",
                    detect_etf_type(
                        name
                    ),
                    "TPEX_ETF"
                )

                if item:

                    result.append(
                        item
                    )


        except Exception:
            continue


    result = deduplicate_universe(
        result
    )


    print(
        f"   TPEx ETF：{len(result)}"
    )


    return result


# ============================================================
# 建立全市場 Universe
# ============================================================

def build_full_universe():

    print()
    print("=" * 64)
    print(
        "建立 V10.7 全市場 Universe"
    )
    print("=" * 64)


    # ========================================================
    # 1. TWSE ISIN
    # ========================================================

    isin_items = (
        fetch_twse_isin_universe()
    )


    twse_stocks = (
        get_twse_stocks_from_isin(
            isin_items
        )
    )


    # ========================================================
    # 2. TPEx
    # ========================================================

    tpex_stocks = (
        fetch_tpex_stocks()
    )


    # ========================================================
    # 3. ETF
    # ========================================================

    twse_etfs = [

        item
        for item in isin_items

        if item["type"]
        in (
            "equity_etf",
            "bond_etf",
        )

        and item["market"] == "TW"
    ]


    # ========================================================
    # 4. TPEx ETF
    # ========================================================

    tpex_etfs = (
        fetch_tpex_etfs()
    )


    # ========================================================
    # 5. 如果 TPEx 官方直接抓不到
    #
    # 從 TWSE ISIN 清單恢復 TPEx 股票。
    #
    # 這不是 fallback 假資料。
    # 因為 ISIN 官方清單本身包含：
    # TPEx LISTED。
    # ========================================================

    if len(tpex_stocks) < 500:

        print()
        print(
            "⚠️ TPEx 直接來源不足"
        )

        isin_tpex_stocks = [

            item
            for item in isin_items

            if (
                item["market"] == "TWO"
                and item["type"] == "stock"
            )
        ]


        if len(
            isin_tpex_stocks
        ) >= 500:

            print(
                "♻️ 使用官方 ISIN "
                "TPEx LISTED STOCK 補充"
            )

            tpex_stocks = (
                isin_tpex_stocks
            )


    # ========================================================
    # 6. TPEx ETF
    #
    # ISIN 也是正式補充來源
    # ========================================================

    if len(tpex_etfs) == 0:

        isin_tpex_etfs = [

            item
            for item in isin_items

            if (
                item["market"] == "TWO"
                and item["type"]
                in (
                    "equity_etf",
                    "bond_etf",
                )
            )
        ]


        if isin_tpex_etfs:

            print(
                "♻️ 使用官方 ISIN "
                "TPEx ETF 補充"
            )

            tpex_etfs = (
                isin_tpex_etfs
            )


    # ========================================================
    # 7. 合併
    # ========================================================

    universe = []

    universe.extend(
        twse_stocks
    )

    universe.extend(
        tpex_stocks
    )

    universe.extend(
        twse_etfs
    )

    universe.extend(
        tpex_etfs
    )


    universe = deduplicate_universe(
        universe
    )


    # ========================================================
    # 8. 分類統計
    # ========================================================

    listed_stocks = [

        x
        for x in universe
        if (
            x["market"] == "TW"
            and x["type"] == "stock"
        )
    ]


    otc_stocks = [

        x
        for x in universe
        if (
            x["market"] == "TWO"
            and x["type"] == "stock"
        )
    ]


    equity_etfs = [

        x
        for x in universe
        if x["type"]
        == "equity_etf"
    ]


    bond_etfs = [

        x
        for x in universe
        if x["type"]
        == "bond_etf"
    ]


    print()
    print(
        "全市場 Universe 統計："
    )

    print(
        "  上市股票：",
        len(listed_stocks)
    )

    print(
        "  上櫃股票：",
        len(otc_stocks)
    )

    print(
        "  指數/股票型 ETF：",
        len(equity_etfs)
    )

    print(
        "  債券型 ETF：",
        len(bond_etfs)
    )

    print(
        "  Universe 總數：",
        len(universe)
    )


    # ========================================================
    # 9. 完整性驗證
    #
    # 絕不允許 0 檔 TPEx 被當成成功。
    # ========================================================

    errors = []


    if len(
        listed_stocks
    ) < 900:

        errors.append(
            "TWSE 上市股票數量異常"
        )


    if len(
        otc_stocks
    ) < 500:

        errors.append(
            "TPEx 上櫃股票數量異常"
        )


    if len(
        equity_etfs
    ) < 50:

        errors.append(
            "股票型 ETF 數量異常"
        )


    if len(
        bond_etfs
    ) < 5:

        errors.append(
            "債券型 ETF 數量異常"
        )


    if len(
        universe
    ) < 1500:

        errors.append(
            "全市場 Universe 總數異常"
        )


    if errors:

        print()
        print(
            "❌ 全市場 Universe 建立失敗"
        )

        for error in errors:

            print(
                "❌",
                error
            )

        print()
        print(
            "❌ 為避免產生錯誤的 "
            "prices.json，本次 Action 直接失敗。"
        )

        raise RuntimeError(
            "Universe incomplete"
        )


    print()
    print(
        "✅ V10.7 全市場 Universe "
        "完整性驗證通過"
    )


    return (
        universe,
        {
            "listed_stock_count":
                len(listed_stocks),

            "otc_stock_count":
                len(otc_stocks),

            "equity_etf_count":
                len(equity_etfs),

            "bond_etf_count":
                len(bond_etfs),

            "total_count":
                len(universe),

            "source":
                "TWSE_ISIN + TPEx_OFFICIAL",
        }
    )


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


    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=20
        )


        if response.status_code != 200:

            return None


        payload = (
            response.json()
        )


        results = (
            payload
            .get("chart", {})
            .get("result")
        )


        if not results:
            return None


        result = results[0]


        timestamps = (
            result.get(
                "timestamp"
            )
        )


        indicators = (
            result.get(
                "indicators",
                {}
            )
        )


        quotes = (
            indicators
            .get("quote", [])
        )


        if (
            not timestamps
            or not quotes
        ):
            return None


        quote = quotes[0]


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

                dt = datetime.fromtimestamp(
                    ts,
                    tz=timezone.utc
                ).astimezone(
                    TIMEZONE_TW
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
                if i < len(
                    close_list
                )
                else None
            )


            close = safe_float(
                close
            )


            if close is None:
                continue


            rows.append(
                {

                    "date":
                        date_str,

                    "open":
                        safe_float(
                            open_list[i]
                            if i < len(
                                open_list
                            )
                            else None
                        ),

                    "high":
                        safe_float(
                            high_list[i]
                            if i < len(
                                high_list
                            )
                            else None
                        ),

                    "low":
                        safe_float(
                            low_list[i]
                            if i < len(
                                low_list
                            )
                            else None
                        ),

                    "close":
                        close,

                    "volume":
                        safe_float(
                            volume_list[i]
                            if i < len(
                                volume_list
                            )
                            else 0,
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
                "close"
            ]
        )


        df = (
            df
            .sort_values("date")
            .drop_duplicates(
                subset=["date"],
                keep="last"
            )
            .reset_index(
                drop=True
            )
        )


        return df


    except Exception:

        return None


# ============================================================
# Indicators
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
    # MACD
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
        ema12 - ema26
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

    lowest = (
        low
        .rolling(
            9,
            min_periods=9
        )
        .min()
    )


    highest = (
        high
        .rolling(
            9,
            min_periods=9
        )
        .max()
    )


    denominator = (
        highest
        - lowest
    )


    rsv = (
        (
            close
            - lowest
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


    df["k"] = k_values

    df["d"] = d_values


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
# Core Conditions
# ============================================================

def evaluate_core_conditions(
    df
):

    if (
        df is None
        or len(df)
        < MIN_HISTORY_ROWS
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

        and

        pd.notna(
            latest[
                "macd_signal"
            ]
        )

        and

        latest["macd"]
        >
        latest["macd_signal"]
    )


    conditions[
        "RSI > 50"
    ] = (

        pd.notna(
            latest["rsi"]
        )

        and

        latest["rsi"] > 50
    )


    conditions[
        "KD 多方"
    ] = (

        pd.notna(
            latest["k"]
        )

        and

        pd.notna(
            latest["d"]
        )

        and

        latest["k"]
        >
        latest["d"]
    )


    conditions[
        "成交量 ≥ MA5 × 1.5"
    ] = (

        pd.notna(
            latest["volume"]
        )

        and

        pd.notna(
            latest[
                "volume_ma5"
            ]
        )

        and

        latest["volume"]
        >=
        (
            latest[
                "volume_ma5"
            ]
            * 1.5
        )
    )


    conditions[
        "股價 > MA20"
    ] = (

        pd.notna(
            latest["close"]
        )

        and

        pd.notna(
            latest["ma20"]
        )

        and

        latest["close"]
        >
        latest["ma20"]
    )


    conditions[
        "MA20 今日 > 昨日"
    ] = (

        pd.notna(
            latest["ma20"]
        )

        and

        pd.notna(
            previous["ma20"]
        )

        and

        latest["ma20"]
        >
        previous["ma20"]
    )


    score = sum(
        bool(x)
        for x in conditions.values()
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
        or len(df)
        < MIN_HISTORY_ROWS
    ):

        return (
            0.0,
            0.0
        )


    latest = df.iloc[-1]


    score = (
        core["core_score"]
        /
        CORE_TOTAL
    ) * 70.0


    rsi = safe_float(
        latest.get("rsi")
    )


    if rsi is not None:

        if rsi >= 70:

            score += 5

        elif rsi > 50:

            score += 8

        elif rsi >= 45:

            score += 3


    macd_hist = safe_float(
        latest.get(
            "macd_hist"
        )
    )


    if (
        macd_hist is not None
        and macd_hist > 0
    ):

        score += 5


    close = safe_float(
        latest.get("close")
    )

    ma20 = safe_float(
        latest.get("ma20")
    )


    if (
        close is not None
        and ma20 is not None
        and ma20 != 0
    ):

        bias = (
            close
            / ma20
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

            score += 7

        elif ratio >= 1.0:

            score += 3


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


    if (
        latest_date
        > today_tw_date()
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


    core = evaluate_core_conditions(
        df
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
            (
                f"符合 "
                f"{core['core_score']}/"
                f"{CORE_TOTAL} "
                f"核心條件"
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
                    latest[
                        "macd_signal"
                    ]
                ),

            "macd_hist":
                safe_float(
                    latest[
                        "macd_hist"
                    ]
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
                    latest[
                        "volume_ma5"
                    ]
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
                <= today_tw_date()
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
# Filter latest date
# ============================================================

def filter_to_latest_market_date(
    results,
    latest_market_date
):

    target = (
        latest_market_date
        .isoformat()
    )


    return [

        item
        for item in results

        if item.get(
            "market_date"
        ) == target
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


        idx = (
            len(df)
            -
            1
            -
            BACKTEST_HORIZON
        )


        if idx < 1:
            continue


        row = df.iloc[
            idx
        ]


        previous = df.iloc[
            idx - 1
        ]


        conditions = [

            (
                pd.notna(
                    row["macd"]
                )
                and
                pd.notna(
                    row[
                        "macd_signal"
                    ]
                )
                and
                row["macd"]
                >
                row[
                    "macd_signal"
                ]
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
                    row[
                        "volume_ma5"
                    ]
                )
                and
                row["volume"]
                >=
                row[
                    "volume_ma5"
                ] * 1.5
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
                    previous["ma20"]
                )
                and
                row["ma20"]
                >
                previous["ma20"]
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


        if (
            future_idx
            >= len(df)
        ):

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


        a_results.append(
            future > entry
        )


    if a_results:

        win_rate = round(
            sum(a_results)
            /
            len(a_results)
            *
            100,
            2
        )

    else:

        win_rate = None


    return {

        "method":
            "trading_days",

        "comparison_horizon":
            BACKTEST_HORIZON,

        "A_10d_win_rate":
            win_rate,

        "A_sample_count":
            len(a_results),

        "eligible_history_count":
            eligible_symbols,
    }


# ============================================================
# Universe Summary
# ============================================================

def build_universe_summary(
    universe,
    stats
):

    return {

        "listed_stock_count":
            stats[
                "listed_stock_count"
            ],

        "otc_stock_count":
            stats[
                "otc_stock_count"
            ],

        "equity_etf_count":
            stats[
                "equity_etf_count"
            ],

        "bond_etf_count":
            stats[
                "bond_etf_count"
            ],

        "total_count":
            len(universe),

        "items":
            universe,
    }


# ============================================================
# Atomic Save
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
# MAIN
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
    # 1. Universe
    # ========================================================

    universe, universe_stats = (
        build_full_universe()
    )


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
            or idx % 100 == 0
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

            continue


        if len(df) < MIN_HISTORY_ROWS:

            fail_count += 1

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
        "行情成功：",
        success_count
    )

    print(
        "行情失敗：",
        fail_count
    )


    if not analyzed:

        raise RuntimeError(
            "沒有任何有效行情資料"
        )


    # ========================================================
    # 3. Latest market date
    # ========================================================

    latest_market_date = (
        determine_latest_market_date(
            analyzed
        )
    )


    if latest_market_date is None:

        raise RuntimeError(
            "找不到有效交易日"
        )


    print()

    print(
        "最新有效交易日：",
        latest_market_date.isoformat()
    )

    print(
        "今天台灣日期：",
        today_tw_date().isoformat()
    )


    # ========================================================
    # 4. Same market date
    # ========================================================

    analyzed = (
        filter_to_latest_market_date(
            analyzed,
            latest_market_date
        )
    )


    if not analyzed:

        raise RuntimeError(
            "最新交易日沒有有效資料"
        )


    # ========================================================
    # 5. Classification
    # ========================================================

    stocks = [

        x
        for x in analyzed

        if x["type"] == "stock"
    ]


    equity_etfs = [

        x
        for x in analyzed

        if x["type"]
        == "equity_etf"
    ]


    bond_etfs = [

        x
        for x in analyzed

        if x["type"]
        == "bond_etf"
    ]


    # ========================================================
    # 6. 6/6
    # ========================================================

    today_selected = [

        x
        for x in stocks

        if x[
            "core_pass"
        ] is True
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
    # 8. ETF
    # ========================================================

    equity_etfs = sorted(

        equity_etfs,

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

    )[:100]


    # ========================================================
    # 9. Bond ETF
    # ========================================================

    bond_etfs = sorted(

        bond_etfs,

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

    )[:100]


    # ========================================================
    # 10. Breadth
    # ========================================================

    market_breadth = (
        calculate_market_breadth(
            analyzed
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
    # 12. Output
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
        # Data quality
        # ----------------------------------------------------

        "data_quality": {

            "universe_mode":
                "FULL_TAIWAN_MARKET",

            "universe_source":
                "TWSE_ISIN + TPEx_OFFICIAL",

            "universe_complete":
                True,

            "listed_stock_count":
                universe_stats[
                    "listed_stock_count"
                ],

            "otc_stock_count":
                universe_stats[
                    "otc_stock_count"
                ],

            "equity_etf_count":
                universe_stats[
                    "equity_etf_count"
                ],

            "bond_etf_count":
                universe_stats[
                    "bond_etf_count"
                ],

            "universe_count":
                len(universe),

            "analyzed_count":
                len(analyzed),

            "successful_history_count":
                success_count,

            "failed_history_count":
                fail_count,

            "latest_market_date_valid":
                True,

            "non_trading_day_protected":
                (
                    latest_market_date
                    != today_tw_date()
                ),

            "six_of_six_same_market_date":
                True,

            "backtest_uses_trading_days":
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

            "equity_etf_count":
                len(equity_etfs),

            "bond_etf_count":
                len(bond_etfs),

            "today_selected_count":
                len(today_selected),

            "top10_count":
                len(top10),

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
        # Top 10
        # ----------------------------------------------------

        "top10":
            top10,


        # ----------------------------------------------------
        # ETF
        # ----------------------------------------------------

        "etfs":
            equity_etfs,


        # ----------------------------------------------------
        # Bond ETF
        # ----------------------------------------------------

        "bonds":
            bond_etfs,


        # ----------------------------------------------------
        # Backtest
        # ----------------------------------------------------

        "backtest_summary":
            backtest,


        # ----------------------------------------------------
        # COMPLETE UNIVERSE
        # ----------------------------------------------------

        "universe":
            build_universe_summary(
                universe,
                universe_stats
            ),
    }


    # ========================================================
    # 13. Save
    # ========================================================

    save_json(
        output
    )


    # ========================================================
    # 14. Final
    # ========================================================

    print()

    print("=" * 64)

    print(
        f"{VERSION} 完成"
    )

    print("=" * 64)

    print(
        "最新交易日：",
        output[
            "latest_market_date"
        ]
    )

    print(
        "上市股票：",
        universe_stats[
            "listed_stock_count"
        ]
    )

    print(
        "上櫃股票：",
        universe_stats[
            "otc_stock_count"
        ]
    )

    print(
        "股票型 ETF：",
        universe_stats[
            "equity_etf_count"
        ]
    )

    print(
        "債券型 ETF：",
        universe_stats[
            "bond_etf_count"
        ]
    )

    print(
        "Universe 總數：",
        len(universe)
    )

    print(
        "有效行情：",
        len(analyzed)
    )

    print(
        "6/6 個股：",
        len(today_selected)
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
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print()
        print(
            "============================================================"
        )

        print(
            "❌ fetch_data.py V10.7 執行失敗"
        )

        print(
            "❌",
            str(e)
        )

        print(
            "============================================================"
        )

        sys.exit(1)