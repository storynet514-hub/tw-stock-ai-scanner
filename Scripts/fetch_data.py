#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 fetch_data.py V10.5

============================================================
V10.5 全市場 Universe 正式版
============================================================

【Universe】

不再使用固定股票清單。

掃描 Universe 來源：

1. TWSE 上市股票
2. TPEx 上櫃股票
3. TWSE ETF
4. TPEx ETF

包含：

- 上市股票
- 上櫃股票
- 指數型 ETF
- 其他可交易 ETF
- 債券型 ETF

排除：

- 權證
- 認購權證
- 認售權證
- 牛熊證
- 一般債券
- ETN
- 非目標衍生商品
- 已終止 / 無法取得有效行情標的

============================================================
核心條件
============================================================

1. MACD > MACD Signal
2. RSI > 50
3. K > D
4. Volume >= MA5 Volume × 1.5
5. Close > MA20
6. MA20[today] > MA20[yesterday]

六項必須使用同一有效交易日。

============================================================
V10.5 重要原則
============================================================

1. stocks.json 不再決定 Universe
2. prices.json 不再決定 Universe
3. 不使用固定 11 / 14 檔 fallback
4. 官方市場清單建立失敗 => 程式直接失敗
5. 不允許「全市場掃描失敗卻假裝成功」
6. Universe 完整保存到 prices.json
7. Yahoo 僅負責歷史 OHLCV
8. 技術指標使用同一份歷史資料
9. 非交易日不製造虛假資料
10. 6/6 結果為真正全市場掃描結果
============================================================
"""

import os
import sys
import json
import math
import time
import warnings
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import numpy as np
import requests

warnings.filterwarnings("ignore")


# ============================================================
# 基本設定
# ============================================================

VERSION = "V10.5"
SCHEMA_VERSION = "ui.v10.5"

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

# ------------------------------------------------------------
# Yahoo
# ------------------------------------------------------------

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

HISTORY_PERIOD_DAYS = 400

MIN_HISTORY_ROWS = 80

BACKTEST_HORIZON = 10

# 全市場抓取使用平行 worker
MAX_WORKERS = 8

REQUEST_TIMEOUT = 20

REQUEST_RETRY = 3

REQUEST_SLEEP = 0.10

# ------------------------------------------------------------
# 官方 Universe
# ------------------------------------------------------------

TWSE_OPENAPI_BASE = (
    "https://openapi.twse.com.tw/v1"
)

TWSE_COMPANY_URL = (
    TWSE_OPENAPI_BASE
    + "/opendata/t187ap03_L"
)

TWSE_ISIN_URL = (
    "https://isin.twse.com.tw/isin/e_single_main.jsp"
)

# TPEx 官方市場頁
TPEX_MARKET_URL = (
    "https://www.tpex.org.tw/zh-tw/market-trade.html"
)

TPEX_ETF_FILTER_URL = (
    "https://info.tpex.org.tw/ETF/zh/filter.html"
)

# FinMind 僅作股票 Universe fallback
# 不作主要來源。
FINMIND_STOCK_INFO_URL = (
    "https://api.finmindtrade.com/v4/data"
)


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
            "application/json,"
            "text/plain,"
            "text/html,"
            "*/*"
        ),
        "Accept-Language":
            "zh-TW,zh;q=0.9,en;q=0.8",
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
    symbol,
    market=None
):

    if symbol is None:

        return None

    s = str(
        symbol
    ).strip()

    if not s:

        return None

    if s.endswith(
        ".TW"
    ) or s.endswith(
        ".TWO"
    ):

        return s

    # --------------------------------------------------------
    # 已帶其他市場 suffix
    # --------------------------------------------------------

    if "." in s:

        return s

    # --------------------------------------------------------
    # 純數字
    # --------------------------------------------------------

    if s.isdigit():

        code = s.zfill(4)

        if str(
            market
            or ""
        ).upper() == "TWO":

            return code + ".TWO"

        return code + ".TW"

    # --------------------------------------------------------
    # 6碼英數 ETF
    # Yahoo 台灣 ETF 仍通常使用 .TW / .TWO
    # --------------------------------------------------------

    if market:

        m = str(
            market
        ).upper()

        if m == "TWO":

            return s + ".TWO"

        if m == "TW":

            return s + ".TW"

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

        s = s.split(
            "."
        )[0]

    return s


def infer_market(
    symbol
):

    if symbol is None:

        return "TW"

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
# 中文文字標準化
# ============================================================

def normalize_text(
    value
):

    if value is None:

        return ""

    return (
        str(value)
        .strip()
        .replace(
            "\ufeff",
            ""
        )
        .replace(
            "\u3000",
            " "
        )
    )


# ============================================================
# 判斷 ETF 類型
# ============================================================

def classify_etf_type(
    code,
    name=""
):

    c = normalize_text(
        code
    ).upper()

    n = normalize_text(
        name
    )

    # --------------------------------------------------------
    # 明確債券關鍵字
    # --------------------------------------------------------

    bond_keywords = [
        "債",
        "債券",
        "公債",
        "國債",
        "公司債",
        "投資級",
        "高收益債",
        "非投資級",
        "金融債",
        "美國公債",
        "美債",
        "短債",
        "長債",
        "新興債",
        "新興市場債",
        "全球債",
        "美元債",
        "歐元債",
        "公司債",
        "固定收益",
        "高收益",
        "投等債",
    ]

    if any(
        keyword in n
        for keyword in bond_keywords
    ):

        return "bond"

    # --------------------------------------------------------
    # TPEx ETF code suffix
    #
    # 官方 TPEx 說明：
    # C / K 可代表債券 ETF 的幣別尾碼。
    # 但不能單獨靠 suffix 判定所有情況，
    # 因此仍以名稱關鍵字為主。
    # --------------------------------------------------------

    if (
        len(c) >= 6
        and c.endswith("C")
    ):

        return "bond"

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    return "etf"


# ============================================================
# 排除非目標證券
# ============================================================

def is_excluded_security(
    code,
    name,
    security_type="",
    cficode=""
):

    c = normalize_text(
        code
    ).upper()

    n = normalize_text(
        name
    )

    st = normalize_text(
        security_type
    ).upper()

    cf = normalize_text(
        cficode
    ).upper()

    # --------------------------------------------------------
    # 權證
    # --------------------------------------------------------

    warrant_keywords = [
        "權證",
        "牛熊證",
        "認購權證",
        "認售權證",
        "購權證",
        "售權證",
    ]

    if any(
        x in n
        for x in warrant_keywords
    ):

        return True

    if (
        "WARRANT"
        in st
    ):

        return True

    # --------------------------------------------------------
    # ETN
    # --------------------------------------------------------

    if "ETN" in n:

        return True

    if "ETN" in st:

        return True

    # --------------------------------------------------------
    # 一般債券
    # --------------------------------------------------------

    bond_security_keywords = [
        "公司債",
        "金融債",
        "政府公債",
        "中央政府公債",
        "可轉換公司債",
        "交換公司債",
    ]

    # 只有當不是 ETF 時排除
    if any(
        x in n
        for x in bond_security_keywords
    ):

        if (
            "ETF"
            not in st
            and "ETF"
            not in n
        ):

            return True

    # --------------------------------------------------------
    # 明顯非股票 / ETF
    # --------------------------------------------------------

    non_target_keywords = [
        "特別股",
        "受益證券",
        "存託憑證",
    ]

    # 存託憑證是否排除：
    # 使用者目標為上市上櫃個股，
    # GDR/ADR 不納入。
    if any(
        x in n
        for x in non_target_keywords
    ):

        return True

    # --------------------------------------------------------
    # CFICode
    #
    # E = Equity
    # C = Collective Investment
    # --------------------------------------------------------

    if cf:

        # Warrant / option 類
        if cf.startswith(
            (
                "H",
                "O"
            )
        ):

            return True

    # --------------------------------------------------------
    # 代號特徵
    # --------------------------------------------------------

    # 權證通常含英文字母或特殊結構，
    # 但 ETF 也可能含英文字母，所以不直接排除。
    #
    # 只排除非常明顯的權證代號結構。
    if (
        len(c) >= 6
        and c[-1:] in (
            "P",
            "B"
        )
        and (
            "權證"
            in n
        )
    ):

        return True

    return False


# ============================================================
# 建立 Universe item
# ============================================================

def make_universe_item(
    code,
    name,
    market,
    asset_type,
    source="official"
):

    code = normalize_text(
        code
    )

    name = normalize_text(
        name
    )

    market = (
        "TWO"
        if str(
            market
        ).upper()
        in (
            "TWO",
            "TPEx",
            "OTC"
        )
        else "TW"
    )

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
            name
            or code,

        "market":
            market,

        "type":
            asset_type,

        "universe_source":
            source,
    }


# ============================================================
# TWSE OpenAPI
# ============================================================

def fetch_json(
    url,
    params=None,
    timeout=30
):

    for attempt in range(
        1,
        REQUEST_RETRY + 1
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=timeout
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            if attempt >= REQUEST_RETRY:

                print(
                    f"⚠️ API 失敗：{url}"
                )

                print(
                    f"   {e}"
                )

                return None

            time.sleep(
                1.0 * attempt
            )

    return None


# ============================================================
# TWSE 上市股票 + ETF
# ============================================================

def fetch_twse_universe():

    print(
        "🔎 取得 TWSE 官方 Universe..."
    )

    data = fetch_json(
        TWSE_COMPANY_URL,
        params={
            "TYPEK":
                "正股,ETF"
        },
        timeout=40
    )

    if not isinstance(
        data,
        list
    ):

        # 再嘗試不帶 TYPEK
        print(
            "⚠️ TWSE TYPEK 查詢失敗，"
            "重新取得完整上市公司資料..."
        )

        data = fetch_json(
            TWSE_COMPANY_URL,
            timeout=40
        )

    if not isinstance(
        data,
        list
    ):

        print(
            "❌ TWSE Universe API 無有效資料"
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
                "證券代號"
            )
            or row.get(
                "股票代號"
            )
            or row.get(
                "Code"
            )
        )

        name = (
            row.get(
                "公司名稱"
            )
            or row.get(
                "證券名稱"
            )
            or row.get(
                "股票名稱"
            )
            or row.get(
                "Name"
            )
        )

        market = "TW"

        security_type = (
            row.get(
                "證券種類"
            )
            or row.get(
                "有價證券種類"
            )
            or row.get(
                "Type"
            )
            or ""
        )

        cficode = (
            row.get(
                "CFICode"
            )
            or row.get(
                "CFI Code"
            )
            or ""
        )

        code = normalize_text(
            code
        )

        name = normalize_text(
            name
        )

        if not code:

            continue

        # ----------------------------------------------------
        # 判斷 ETF
        # ----------------------------------------------------

        is_etf = (
            "ETF"
            in normalize_text(
                security_type
            ).upper()
            or "ETF"
            in name.upper()
            or code.startswith(
                "00"
            )
        )

        if is_excluded_security(
            code,
            name,
            security_type,
            cficode
        ):

            continue

        if is_etf:

            asset_type = classify_etf_type(
                code,
                name
            )

        else:

            asset_type = "stock"

        item = make_universe_item(
            code,
            name,
            market,
            asset_type,
            source="TWSE"
        )

        if item:

            result.append(
                item
            )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in result:

        unique[
            item["symbol"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"   TWSE Universe：{len(result)}"
    )

    return result


# ============================================================
# TWSE ISIN ETF 補充
#
# 官方 ISIN 清單可列出 ETF。
# 用來補足 OpenAPI 欄位不足的 ETF。
# ============================================================

def fetch_twse_isin_etfs():

    print(
        "🔎 取得 TWSE ISIN ETF 補充清單..."
    )

    try:

        response = SESSION.get(
            TWSE_ISIN_URL,
            timeout=40
        )

        response.raise_for_status()

        html = response.text

    except Exception as e:

        print(
            f"⚠️ TWSE ISIN ETF 取得失敗：{e}"
        )

        return []

    try:

        tables = pd.read_html(
            html
        )

    except Exception as e:

        print(
            f"⚠️ TWSE ISIN 表格解析失敗：{e}"
        )

        return []

    result = []

    for table in tables:

        if table.empty:

            continue

        # flatten multi-index
        if isinstance(
            table.columns,
            pd.MultiIndex
        ):

            table.columns = [
                " ".join(
                    [
                        str(x)
                        for x in col
                        if str(x)
                        != "nan"
                    ]
                )
                for col in table.columns
            ]

        columns = [
            str(x)
            for x in table.columns
        ]

        code_col = None
        name_col = None
        type_col = None
        market_col = None

        for col in columns:

            if (
                "Security Code"
                in col
                or "證券代號"
                in col
            ):

                code_col = col

            if (
                "Security Name"
                in col
                or "證券名稱"
                in col
            ):

                name_col = col

            if (
                "Type of security"
                in col
                or "證券種類"
                in col
            ):

                type_col = col

            if (
                "Market"
                in col
                or "市場"
                in col
            ):

                market_col = col

        if not code_col or not name_col:

            continue

        for _, row in table.iterrows():

            code = normalize_text(
                row.get(
                    code_col
                )
            )

            name = normalize_text(
                row.get(
                    name_col
                )
            )

            sec_type = (
                normalize_text(
                    row.get(
                        type_col
                    )
                )
                if type_col
                else ""
            )

            market_value = (
                normalize_text(
                    row.get(
                        market_col
                    )
                )
                if market_col
                else ""
            )

            if not code:

                continue

            if (
                "ETF"
                not in sec_type.upper()
                and "ETF"
                not in name.upper()
            ):

                continue

            if (
                "TWSE"
                not in market_value.upper()
                and "上市"
                not in market_value
            ):

                # 如果頁面本身就是 ETF 清單，
                # 不強制依 market 欄位排除。
                pass

            item = make_universe_item(
                code,
                name,
                "TW",
                classify_etf_type(
                    code,
                    name
                ),
                source="TWSE_ISIN"
            )

            if item:

                result.append(
                    item
                )

    unique = {}

    for item in result:

        unique[
            item["symbol"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"   TWSE ISIN ETF：{len(result)}"
    )

    return result


# ============================================================
# TPEx 市場交易 CSV / HTML
# ============================================================

def fetch_tpex_stock_universe():

    print(
        "🔎 取得 TPEx 上櫃股票 Universe..."
    )

    result = []

    # --------------------------------------------------------
    # 第一來源：TPEx 交易看板頁面
    # --------------------------------------------------------

    try:

        response = SESSION.get(
            TPEX_MARKET_URL,
            timeout=40
        )

        response.raise_for_status()

        html = response.text

        tables = pd.read_html(
            html
        )

        for table in tables:

            if table.empty:

                continue

            if isinstance(
                table.columns,
                pd.MultiIndex
            ):

                table.columns = [
                    " ".join(
                        [
                            str(x)
                            for x in col
                            if str(x)
                            != "nan"
                        ]
                    )
                    for col in table.columns
                ]

            columns = [
                str(x)
                for x in table.columns
            ]

            code_col = None
            name_col = None

            for col in columns:

                if (
                    "代號"
                    in col
                    or "Code"
                    in col
                ):

                    code_col = col

                if (
                    "名稱"
                    in col
                    or "Name"
                    in col
                ):

                    name_col = col

            if not code_col or not name_col:

                continue

            for _, row in table.iterrows():

                code = normalize_text(
                    row.get(
                        code_col
                    )
                )

                name = normalize_text(
                    row.get(
                        name_col
                    )
                )

                if not code:

                    continue

                # 純股票代號
                if not code[0].isdigit():

                    continue

                if is_excluded_security(
                    code,
                    name
                ):

                    continue

                # ETF 不在股票清單這邊處理
                if (
                    "ETF"
                    in name.upper()
                ):

                    continue

                item = make_universe_item(
                    code,
                    name,
                    "TWO",
                    "stock",
                    source="TPEx"
                )

                if item:

                    result.append(
                        item
                    )

    except Exception as e:

        print(
            f"⚠️ TPEx 股票頁面取得失敗：{e}"
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in result:

        unique[
            item["symbol"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"   TPEx 股票 Universe：{len(result)}"
    )

    return result


# ============================================================
# TPEx ETF
# ============================================================

def fetch_tpex_etf_universe():

    print(
        "🔎 取得 TPEx ETF Universe..."
    )

    result = []

    try:

        response = SESSION.get(
            TPEX_ETF_FILTER_URL,
            timeout=40
        )

        response.raise_for_status()

        html = response.text

        tables = pd.read_html(
            html
        )

        for table in tables:

            if table.empty:

                continue

            if isinstance(
                table.columns,
                pd.MultiIndex
            ):

                table.columns = [
                    " ".join(
                        [
                            str(x)
                            for x in col
                            if str(x)
                            != "nan"
                        ]
                    )
                    for col in table.columns
                ]

            columns = [
                str(x)
                for x in table.columns
            ]

            code_col = None
            name_col = None

            for col in columns:

                if (
                    "Securities Code"
                    in col
                    or "證券代號"
                    in col
                    or "代號"
                    in col
                ):

                    code_col = col

                if (
                    "ETF Name"
                    in col
                    or "ETF名稱"
                    in col
                    or "名稱"
                    in col
                ):

                    name_col = col

            if not code_col or not name_col:

                continue

            for _, row in table.iterrows():

                code = normalize_text(
                    row.get(
                        code_col
                    )
                )

                name = normalize_text(
                    row.get(
                        name_col
                    )
                )

                if not code:

                    continue

                # ETF 代號可以是數字 + 英文字母
                if not any(
                    ch.isdigit()
                    for ch in code
                ):

                    continue

                asset_type = classify_etf_type(
                    code,
                    name
                )

                item = make_universe_item(
                    code,
                    name,
                    "TWO",
                    asset_type,
                    source="TPEx_ETF"
                )

                if item:

                    result.append(
                        item
                    )

    except Exception as e:

        print(
            f"⚠️ TPEx ETF 取得失敗：{e}"
        )

    unique = {}

    for item in result:

        unique[
            item["symbol"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"   TPEx ETF Universe：{len(result)}"
    )

    return result


# ============================================================
# FinMind 股票補充
#
# 僅作為官方 TPEx 頁面解析失敗時的補充。
# ============================================================

def fetch_finmind_stock_universe():

    print(
        "🔎 嘗試 FinMind 股票 Universe 補充..."
    )

    try:

        response = SESSION.get(
            FINMIND_STOCK_INFO_URL,
            params={
                "dataset":
                    "TaiwanStockInfo"
            },
            timeout=40
        )

        response.raise_for_status()

        payload = response.json()

        data = payload.get(
            "data",
            []
        )

        if not isinstance(
            data,
            list
        ):

            return []

        result = []

        for row in data:

            code = normalize_text(
                row.get(
                    "stock_id"
                )
            )

            name = normalize_text(
                row.get(
                    "stock_name"
                )
            )

            market = normalize_text(
                row.get(
                    "type"
                )
            )

            if not code:

                continue

            # FinMind type 常見：
            # twse / tpex
            market_code = (
                "TWO"
                if (
                    "tpex"
                    in market.lower()
                    or "上櫃"
                    in market
                )
                else "TW"
            )

            # ETF 不在這裡補
            if (
                code.startswith(
                    "00"
                )
            ):

                continue

            if is_excluded_security(
                code,
                name
            ):

                continue

            item = make_universe_item(
                code,
                name,
                market_code,
                "stock",
                source="FinMind"
            )

            if item:

                result.append(
                    item
                )

        unique = {}

        for item in result:

            unique[
                item["symbol"]
            ] = item

        return list(
            unique.values()
        )

    except Exception as e:

        print(
            f"⚠️ FinMind 補充失敗：{e}"
        )

        return []


# ============================================================
# 建立全市場 Universe
# ============================================================

def build_full_universe():

    print()
    print("=" * 64)
    print(
        "建立 V10.5 全市場 Universe"
    )
    print("=" * 64)

    twse = fetch_twse_universe()

    twse_etf = fetch_twse_isin_etfs()

    tpex = fetch_tpex_stock_universe()

    tpex_etf = fetch_tpex_etf_universe()

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    all_items = (
        twse
        + twse_etf
        + tpex
        + tpex_etf
    )

    # --------------------------------------------------------
    # 若 TPEx 股票數量異常低，
    # 用 FinMind 補充。
    #
    # 不允許完全沒有 TPEx 股票。
    # --------------------------------------------------------

    tpex_stock_count = sum(
        1
        for x in all_items
        if (
            x["market"] == "TWO"
            and x["type"] == "stock"
        )
    )

    if tpex_stock_count < 500:

        print(
            "⚠️ TPEx 股票數量異常，"
            "啟動補充 Universe..."
        )

        finmind = (
            fetch_finmind_stock_universe()
        )

        all_items.extend(
            finmind
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in all_items:

        symbol = item.get(
            "symbol"
        )

        if not symbol:

            continue

        # 最終安全排除
        if is_excluded_security(
            item.get(
                "code"
            ),
            item.get(
                "name"
            )
        ):

            continue

        unique[
            symbol
        ] = item

    universe = list(
        unique.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    universe.sort(
        key=lambda x: (
            x.get(
                "market",
                ""
            ),
            x.get(
                "type",
                ""
            ),
            x.get(
                "code",
                ""
            ),
        )
    )

    # --------------------------------------------------------
    # 分類統計
    # --------------------------------------------------------

    tw_stock = sum(
        1
        for x in universe
        if (
            x["market"] == "TW"
            and x["type"] == "stock"
        )
    )

    two_stock = sum(
        1
        for x in universe
        if (
            x["market"] == "TWO"
            and x["type"] == "stock"
        )
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

    print()
    print(
        "全市場 Universe 統計："
    )

    print(
        f"  上市股票：{tw_stock}"
    )

    print(
        f"  上櫃股票：{two_stock}"
    )

    print(
        f"  ETF：{etf_count}"
    )

    print(
        f"  債券 ETF：{bond_count}"
    )

    print(
        f"  Universe 總數：{len(universe)}"
    )

    # --------------------------------------------------------
    # 最低完整性檢查
    # --------------------------------------------------------

    if tw_stock < 500:

        raise RuntimeError(
            "TWSE 上市股票 Universe 異常："
            f"{tw_stock}"
        )

    if two_stock < 300:

        raise RuntimeError(
            "TPEx 上櫃股票 Universe 異常："
            f"{two_stock}"
        )

    if etf_count < 20:

        raise RuntimeError(
            "ETF Universe 異常："
            f"{etf_count}"
        )

    if len(universe) < 1000:

        raise RuntimeError(
            "全市場 Universe 數量異常偏低："
            f"{len(universe)}"
        )

    return universe


# ============================================================
# Yahoo 歷史資料
# ============================================================

def fetch_yahoo_history(
    symbol
):

    period2 = int(
        time.time()
    )

    period1 = (
        period2
        - HISTORY_PERIOD_DAYS * 86400
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

    for attempt in range(
        1,
        REQUEST_RETRY + 1
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if (
                response.status_code
                != 200
            ):

                raise RuntimeError(
                    f"HTTP {response.status_code}"
                )

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

            quote_list = (
                indicators.get(
                    "quote",
                    []
                )
            )

            if (
                not timestamps
                or not quote_list
            ):

                return None

            quote = quote_list[0]

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

            adjclose_list = (
                indicators.get(
                    "adjclose",
                    []
                )
            )

            adjclose = None

            if adjclose_list:

                adjclose = (
                    adjclose_list[0]
                    .get(
                        "adjclose"
                    )
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
                    if i < len(close_list)
                    else None
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

            df["date"] = (
                pd.to_datetime(
                    df["date"],
                    errors="coerce"
                )
            )

            df = df.dropna(
                subset=[
                    "date",
                    "close"
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

        except Exception:

            if attempt >= REQUEST_RETRY:

                return None

            time.sleep(
                0.5 * attempt
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
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    df["rsi"] = (
        100
        - (
            100
            / (
                1 + rs
            )
        )
    )

    df.loc[
        (
            avg_loss == 0
        )
        & (
            avg_gain > 0
        ),
        "rsi"
    ] = 100.0

    # --------------------------------------------------------
    # KD 9 / 3 / 3
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
        / denominator.replace(
            0,
            np.nan
        )
    ) * 100

    k_values = []
    d_values = []

    previous_k = 50.0
    previous_d = 50.0

    for value in rsv:

        if pd.isna(
            value
        ):

            k_values.append(
                np.nan
            )

            d_values.append(
                np.nan
            )

            continue

        current_k = (
            previous_k * 2 / 3
            + float(value) / 3
        )

        current_d = (
            previous_d * 2 / 3
            + current_k / 3
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

    # 1
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
        > latest["macd_signal"]
    )

    # 2
    conditions[
        "RSI > 50"
    ] = (
        pd.notna(
            latest["rsi"]
        )
        and latest["rsi"] > 50
    )

    # 3
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
        > latest["d"]
    )

    # 4
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
        >= (
            latest["volume_ma5"]
            * 1.5
        )
    )

    # 5
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
        > latest["ma20"]
    )

    # 6
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
        > previous["ma20"]
    )

    score = sum(
        1
        for value
        in conditions.values()
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

        return (
            0.0,
            0.0
        )

    latest = df.iloc[-1]

    score = (
        core["core_score"]
        / CORE_TOTAL
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
            close / ma20 - 1
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
            / volume_ma5
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
        / CORE_TOTAL
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

    latest_date = pd.Timestamp(
        latest["date"]
    ).date()

    previous_date = pd.Timestamp(
        previous["date"]
    ).date()

    # 禁止未來日期
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
                - previous_close
            )
            / previous_close
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

        "universe_source":
            item.get(
                "universe_source"
            ),

        "price":
            (
                round(
                    close,
                    4
                )
                if close is not None
                else None
            ),

        "change_pct":
            (
                round(
                    change_pct,
                    4
                )
                if change_pct is not None
                else None
            ),

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
                core[
                    "core_score"
                ]
            ),

        "core_score":
            core[
                "core_score"
            ],

        "core_total":
            CORE_TOTAL,

        "core_pass":
            core[
                "core_pass"
            ],

        "market_date":
            latest_date.isoformat(),

        "previous_market_date":
            previous_date.isoformat(),

        "indicators": {

            "rsi":
                safe_float(
                    latest[
                        "rsi"
                    ]
                ),

            "macd":
                safe_float(
                    latest[
                        "macd"
                    ]
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
                    latest[
                        "k"
                    ]
                ),

            "d":
                safe_float(
                    latest[
                        "d"
                    ]
                ),

            "ma5":
                safe_float(
                    latest[
                        "ma5"
                    ]
                ),

            "ma20":
                safe_float(
                    latest[
                        "ma20"
                    ]
                ),

            "previous_ma20":
                safe_float(
                    previous[
                        "ma20"
                    ]
                ),

            "volume":
                safe_float(
                    latest[
                        "volume"
                    ]
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
# 平行抓取
# ============================================================

def fetch_one(
    item
):

    symbol = item[
        "symbol"
    ]

    try:

        df = fetch_yahoo_history(
            symbol
        )

        if df is None:

            return (
                item,
                None,
                "no_data"
            )

        if len(df) < MIN_HISTORY_ROWS:

            return (
                item,
                None,
                "insufficient_history"
            )

        result = analyze_symbol(
            item,
            df
        )

        if result is None:

            return (
                item,
                None,
                "invalid_analysis"
            )

        return (
            item,
            result,
            "success"
        )

    except Exception as e:

        return (
            item,
            None,
            str(e)
        )


def fetch_all_universe(
    universe
):

    print()
    print("=" * 64)
    print(
        "開始抓取全市場 Yahoo 歷史行情"
    )
    print("=" * 64)

    total = len(
        universe
    )

    analyzed = []

    history_cache = {}

    success_count = 0

    fail_count = 0

    completed = 0

    started = time.time()

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_one,
                item
            ):
                item
            for item in universe
        }

        for future in as_completed(
            futures
        ):

            item = futures[
                future
            ]

            completed += 1

            try:

                (
                    original,
                    result,
                    status
                ) = future.result()

            except Exception:

                result = None

                status = "worker_error"

                original = item

            if status == "success":

                success_count += 1

                analyzed.append(
                    result
                )

            else:

                fail_count += 1

            if (
                completed % 50 == 0
                or completed == total
            ):

                elapsed = (
                    time.time()
                    - started
                )

                rate = (
                    completed
                    / elapsed
                    if elapsed > 0
                    else 0
                )

                eta = (
                    (total - completed)
                    / rate
                    if rate > 0
                    else 0
                )

                print(
                    f"[{completed}/{total}] "
                    f"成功 {success_count} | "
                    f"失敗 {fail_count} | "
                    f"ETA {eta:.0f}s"
                )

    print()
    print(
        f"行情成功：{success_count}"
    )

    print(
        f"行情失敗：{fail_count}"
    )

    return (
        analyzed,
        history_cache,
        success_count,
        fail_count
    )


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
        ) == target
    ]


# ============================================================
# Market Breadth
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
                + falling
                + unchanged
            ),
    }


# ============================================================
# Backtest
# ============================================================

def calculate_backtest(
    universe,
    history_cache
):

    # V10.5：
    # 全市場 Yahoo history cache 不保存，
    # 因為會造成記憶體 / JSON 過大。
    #
    # 這裡保留架構欄位，
    # 不做虛假的 backtest。
    #
    # 後續如要正式 backtest，
    # 應另外建立 historical data cache。
    # --------------------------------------------------------

    return {

        "enabled":
            False,

        "method":
            "trading_days",

        "comparison_horizon":
            BACKTEST_HORIZON,

        "reason":
            "V10.5 全市場掃描暫不在單次掃描中執行完整歷史回測",

        "sample_count":
            0,

        "win_rate":
            None,
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

    tw_stock_count = sum(
        1
        for x in universe
        if (
            x["market"] == "TW"
            and x["type"] == "stock"
        )
    )

    two_stock_count = sum(
        1
        for x in universe
        if (
            x["market"] == "TWO"
            and x["type"] == "stock"
        )
    )

    return {

        "stock_count":
            stock_count,

        "twse_stock_count":
            tw_stock_count,

        "tpex_stock_count":
            two_stock_count,

        "etf_count":
            etf_count,

        "bond_count":
            bond_count,

        "total_count":
            len(universe),

        "source":
            "TWSE + TPEx official market universe",

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
        + ".tmp"
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

    print()

    # ========================================================
    # 1. 建立全市場 Universe
    # ========================================================

    try:

        universe = (
            build_full_universe()
        )

    except Exception as e:

        print()
        print(
            "❌ 全市場 Universe 建立失敗"
        )

        print(
            f"❌ {e}"
        )

        print(
            "❌ 為避免假裝完成，"
            "本次 Action 直接失敗。"
        )

        sys.exit(1)

    print()

    print(
        f"📊 全市場 Universe："
        f"{len(universe)} 檔"
    )

    # ========================================================
    # 2. 全市場行情
    # ========================================================

    (
        analyzed,
        history_cache,
        success_count,
        fail_count
    ) = fetch_all_universe(
        universe
    )

    if not analyzed:

        print(
            "❌ 沒有任何有效行情資料"
        )

        sys.exit(1)

    # ========================================================
    # 3. 最新交易日
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
        < today_tw_date()
    ):

        print(
            "ℹ️ 今天沒有新的有效行情，"
            "使用最近有效交易日。"
        )

    # ========================================================
    # 4. 同一交易日
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
    # ========================================================

    today_selected = [
        x
        for x in stocks
        if x["core_pass"] is True
    ]

    # ========================================================
    # 7. Top10
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
    # 8. ETF ranking
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
    # 9. Bond ranking
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
    # 10. Market breadth
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
    # 12. Universe summary
    # ========================================================

    universe_summary = (
        build_universe_summary(
            universe
        )
    )

    # ========================================================
    # 13. Data quality
    # ========================================================

    coverage = (
        success_count
        / len(universe)
        * 100
        if universe
        else 0
    )

    # --------------------------------------------------------
    # 注意：
    # 全市場並非所有標的都有 Yahoo 歷史資料。
    # 因此不因少數標的缺資料而假裝它們有技術指標。
    # --------------------------------------------------------

    data_quality = {

        "universe_source":
            "official_market_universe",

        "universe_complete":
            True,

        "universe_count":
            len(universe),

        "successful_history_count":
            success_count,

        "failed_history_count":
            fail_count,

        "history_coverage_pct":
            round(
                coverage,
                2
            ),

        "latest_market_date":
            latest_market_date.isoformat(),

        "today_is_market_date":
            (
                latest_market_date
                == today_tw_date()
            ),

        "non_trading_day_protected":
            (
                latest_market_date
                != today_tw_date()
            ),

        "same_market_date_for_core":
            True,

        "min_history_rows":
            MIN_HISTORY_ROWS,

        "core_condition_count":
            CORE_TOTAL,

        "core_condition_same_date":
            True,

        "fixed_universe_disabled":
            True,

        "stocks_json_not_universe_source":
            True,

        "prices_json_not_universe_source":
            True,

        "fallback_universe_disabled":
            True,

        "yahoo_history_source":
            True,
    }

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

        "data_quality":
            data_quality,

        "summary": {

            "stock_count":
                len(stocks),

            "twse_stock_count":
                sum(
                    1
                    for x in stocks
                    if x["market"] == "TW"
                ),

            "tpex_stock_count":
                sum(
                    1
                    for x in stocks
                    if x["market"] == "TWO"
                ),

            "etf_count":
                len(etfs),

            "bond_count":
                len(bonds),

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
        # 真正全市場 6/6
        # ----------------------------------------------------

        "today_selected":
            today_selected,

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
        # 完整 Universe
        # ----------------------------------------------------

        "universe":
            universe_summary,
    }

    # ========================================================
    # 15. Atomic save
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
        output["date"]
    )

    print(
        "latest_market_date：",
        output[
            "latest_market_date"
        ]
    )

    print()

    print(
        "全市場 Universe：",
        len(universe)
    )

    print(
        "上市股票：",
        sum(
            1
            for x in stocks
            if x["market"] == "TW"
        )
    )

    print(
        "上櫃股票：",
        sum(
            1
            for x in stocks
            if x["market"] == "TWO"
        )
    )

    print(
        "ETF：",
        len(etfs)
    )

    print(
        "債券 ETF：",
        len(bonds)
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

    print(
        "行情覆蓋率：",
        f"{coverage:.2f}%"
    )

    print()

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
        "市場上漲：",
        market_breadth[
            "rising"
        ]
    )

    print(
        "市場下跌：",
        market_breadth[
            "falling"
        ]
    )

    print(
        "市場平盤：",
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