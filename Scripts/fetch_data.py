#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 fetch_data.py V10.4

V10.4 全市場 Universe 正式版
============================================================

【Universe 核心架構】

1. 不再使用固定 11 檔 FALLBACK_UNIVERSE
2. 優先從官方市場資料建立 Universe
3. TWSE：
   - 上市股票
   - 上市 ETF
4. TPEx：
   - 上櫃股票
   - 上櫃 ETF（若官方來源可取得）
5. 保留債券 ETF，例如：
   00720B
6. 排除：
   - 權證
   - ETN
   - 一般債券
   - 其他非股票 / 非 ETF 證券
7. stocks.json 僅作快取，不再限制市場 Universe
8. prices.json universe 僅作快取，不再作為主要 Universe
9. 官方 Universe 取得數量異常時直接失敗
10. 禁止回退至舊 11 檔清單

【行情】

11. Yahoo Finance Chart API
12. 歷史資料 260 日
13. 最少 80 個交易日
14. 不使用今天日期假造行情
15. latest_market_date 使用實際行情日期
16. date 與 latest_market_date 完全一致

【六項核心條件】

17. MACD > Signal
18. RSI > 50
19. K > D
20. Volume >= MA5 Volume × 1.5
21. Close > MA20
22. MA20[today] > MA20[yesterday]

23. 六項條件必須同一交易日成立
24. today_selected = 股票 6/6

【資料品質】

25. 非交易日保護
26. 歷史資料不足保護
27. change_pct 無資料保持 null
28. Universe source 寫入 JSON
29. Universe count 寫入 JSON
30. 原子寫入 prices.json
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

VERSION = "V10.4"
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


# ============================================================
# 官方 Universe API
# ============================================================

TWSE_BASE_URL = (
    "https://openapi.twse.com.tw/v1"
)

TWSE_LISTED_COMPANY_API = (
    TWSE_BASE_URL
    + "/opendata/t187ap03_L"
)

TWSE_FUND_API = (
    TWSE_BASE_URL
    + "/opendata/t187ap47_L"
)


# TPEx 官方公開查詢頁 / API 若可取得則使用。
# 保留多組候選 URL，避免單一路徑變動造成整個 Universe 失敗。
TPEX_API_CANDIDATES = [

    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",

    "https://www.tpex.org.tw/openapi/v1/tpex_esb_latest_statistics",

    "https://www.tpex.org.tw/openapi/v1/tpex_listed_company",

]


YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)


TIMEZONE_TW = timezone(
    timedelta(hours=8)
)

HISTORY_PERIOD_DAYS = 260

MIN_HISTORY_ROWS = 80

BACKTEST_HORIZON = 10

REQUEST_SLEEP = 0.15

UNIVERSE_MIN_EXPECTED = 500


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
        "User-Agent":
            (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),

        "Accept":
            "application/json,text/plain,*/*",

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

def clean_json_value(
    value
):

    if isinstance(
        value,
        dict
    ):

        return {
            str(k):
                clean_json_value(v)
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
    symbol
):

    if symbol is None:
        return None

    s = str(
        symbol
    ).strip()

    if not s:
        return None

    if (
        s.endswith(".TW")
        or s.endswith(".TWO")
    ):

        return s

    if "." in s:
        return s

    if s.isdigit():

        if len(s) <= 4:

            return (
                s.zfill(4)
                + ".TW"
            )

    if len(s) >= 4:

        return (
            s
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


def infer_market(
    symbol
):

    if symbol is None:
        return "TW"

    s = str(
        symbol
    ).upper()

    if s.endswith(".TWO"):
        return "TWO"

    if s.endswith(".TW"):
        return "TW"

    return "OTHER"


# ============================================================
# Asset classification
#
# 重要：
# 「債券 ETF」不是 bond。
#
# 00720B：
# type = etf
#
# 一般債券：
# type = bond
#
# 本系統後續會保留 ETF，
# 但 today_selected 只使用 stock。
# ============================================================

def infer_type(
    code,
    name="",
    existing_type=None
):

    c = str(
        code or ""
    ).upper()

    n = str(
        name or ""
    ).strip()

    t = str(
        existing_type or ""
    ).lower()

    if t in (
        "stock",
        "stocks",
        "equity"
    ):

        return "stock"

    if t in (
        "etf",
        "fund",
        "index_fund"
    ):

        return "etf"

    if t in (
        "bond",
        "bond_etf",
        "bond-fund"
    ):

        # 如果來源明確說是 ETF，
        # 絕不能因名稱有債而改成 bond。
        if "etf" in t:
            return "etf"

        return "bond"

    # --------------------------------------------------------
    # 明確 ETF 關鍵字
    # --------------------------------------------------------

    etf_keywords = [

        "ETF",

        "基金",

        "指數股票型基金",

        "債券ETF",

        "債券型ETF",

        "國債ETF",

        "公司債ETF",

        "投資級債ETF",

        "高收益債ETF",

    ]

    upper_name = n.upper()

    if any(
        str(k).upper() in upper_name
        for k in etf_keywords
    ):

        return "etf"

    # --------------------------------------------------------
    # TWSE ETF 常見 00 開頭代號
    #
    # 注意：
    # 這只是輔助判斷。
    # 官方基金 API 判定為 ETF 時優先。
    # --------------------------------------------------------

    if (
        c.isdigit()
        and c.startswith("00")
    ):

        return "etf"

    # --------------------------------------------------------
    # 一般債券
    # --------------------------------------------------------

    bond_keywords = [

        "債券",

        "公債",

        "國債",

        "公司債",

        "金融債",

        "投等債",

        "高收益債",

        "新興公債",

    ]

    if any(
        k in n
        for k in bond_keywords
    ):

        return "bond"

    return "stock"


# ============================================================
# Generic field finder
# ============================================================

def find_field(
    item,
    candidates
):

    if not isinstance(
        item,
        dict
    ):

        return None

    # exact
    for key in candidates:

        if key in item:

            value = item.get(
                key
            )

            if value is not None:

                return value

    # normalized matching
    normalized = {}

    for key in item.keys():

        normalized[
            re.sub(
                r"[\s_\-（）()]+",
                "",
                str(key).lower()
            )
        ] = key

    for candidate in candidates:

        normalized_candidate = re.sub(
            r"[\s_\-（）()]+",
            "",
            str(candidate).lower()
        )

        if normalized_candidate in normalized:

            return item.get(
                normalized[
                    normalized_candidate
                ]
            )

    return None


# ============================================================
# Normalize Universe
# ============================================================

def normalize_universe_items(
    raw_items
):

    result = []

    if not isinstance(
        raw_items,
        list
    ):

        return []

    for item in raw_items:

        if isinstance(
            item,
            str
        ):

            symbol = normalize_symbol(
                item
            )

            if not symbol:
                continue

            code = extract_code(
                symbol
            )

            result.append(
                {
                    "code":
                        code,

                    "symbol":
                        symbol,

                    "name":
                        code,

                    "market":
                        infer_market(
                            symbol
                        ),

                    "type":
                        infer_type(
                            code
                        ),
                }
            )

            continue

        if not isinstance(
            item,
            dict
        ):

            continue

        code = find_field(
            item,
            [
                "公司代號",
                "股票代號",
                "證券代號",
                "代號",
                "Code",
                "code",
                "stock_id",
                "stock_code",
            ]
        )

        symbol = find_field(
            item,
            [
                "symbol",
                "ticker",
                "yahoo_symbol",
                "yf_symbol",
                "YahooSymbol",
            ]
        )

        name = find_field(
            item,
            [
                "公司簡稱",
                "證券名稱",
                "股票名稱",
                "名稱",
                "Name",
                "name",
                "stock_name",
            ]
        )

        market = find_field(
            item,
            [
                "market",
                "市場",
                "Market",
            ]
        )

        existing_type = find_field(
            item,
            [
                "type",
                "類型",
                "category",
                "asset_type",
            ]
        )

        if not code and symbol:

            code = extract_code(
                symbol
            )

        if not symbol and code:

            symbol = normalize_symbol(
                code
            )

        if not symbol:

            continue

        code = str(
            code
            or extract_code(symbol)
        ).strip()

        name = str(
            name
            or code
        ).strip()

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:

            continue

        if not market:

            market = infer_market(
                symbol
            )

        asset_type = infer_type(
            code,
            name,
            existing_type
        )

        result.append(
            {
                "code":
                    code,

                "symbol":
                    symbol,

                "name":
                    name,

                "market":
                    str(market),

                "type":
                    asset_type,
            }
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in result:

        symbol = item[
            "symbol"
        ]

        unique[
            symbol
        ] = item

    return list(
        unique.values()
    )


# ============================================================
# 官方 API JSON
# ============================================================

def get_json(
    url,
    timeout=30
):

    try:

        response = SESSION.get(
            url,
            timeout=timeout
        )

        if response.status_code != 200:

            print(
                f"⚠️ API HTTP "
                f"{response.status_code}: "
                f"{url}"
            )

            return None

        text = (
            response.text
            .lstrip("\ufeff")
            .strip()
        )

        if not text:

            return None

        return json.loads(
            text
        )

    except Exception as e:

        print(
            f"⚠️ API 讀取失敗："
            f"{url}"
        )

        print(
            f"   {e}"
        )

        return None


# ============================================================
# TWSE 上市股票
# ============================================================

def fetch_twse_listed_stocks():

    print(
        "📡 TWSE：取得上市公司基本資料..."
    )

    data = get_json(
        TWSE_LISTED_COMPANY_API
    )

    if not isinstance(
        data,
        list
    ):

        return []

    result = []

    for row in data:

        if not isinstance(
            row,
            dict
        ):

            continue

        code = find_field(
            row,
            [
                "公司代號",
                "股票代號",
                "證券代號",
                "Code",
            ]
        )

        name = find_field(
            row,
            [
                "公司簡稱",
                "證券名稱",
                "股票名稱",
                "名稱",
                "Name",
            ]
        )

        if code is None:
            continue

        code = str(
            code
        ).strip()

        # ----------------------------------------------------
        # 上市股票基本代號通常為 4 碼
        # 排除權證等長代號
        # ----------------------------------------------------

        if not re.fullmatch(
            r"\d{4}",
            code
        ):

            continue

        symbol = (
            code
            + ".TW"
        )

        result.append(
            {
                "code":
                    code,

                "symbol":
                    symbol,

                "name":
                    str(
                        name or code
                    ),

                "market":
                    "TW",

                "type":
                    "stock",
            }
        )

    print(
        f"   TWSE 上市股票："
        f"{len(result)}"
    )

    return result


# ============================================================
# TWSE ETF
# ============================================================

def fetch_twse_etfs():

    print(
        "📡 TWSE：取得基金 / ETF 基本資料..."
    )

    data = get_json(
        TWSE_FUND_API
    )

    if not isinstance(
        data,
        list
    ):

        print(
            "⚠️ TWSE ETF API 無有效資料"
        )

        return []

    result = []

    for row in data:

        if not isinstance(
            row,
            dict
        ):

            continue

        code = find_field(
            row,
            [
                "基金代號",
                "證券代號",
                "股票代號",
                "代號",
                "Code",
                "code",
            ]
        )

        name = find_field(
            row,
            [
                "基金名稱",
                "證券名稱",
                "基金簡稱",
                "名稱",
                "Name",
                "name",
            ]
        )

        if code is None:
            continue

        code = str(
            code
        ).strip()

        # ----------------------------------------------------
        # ETF 代號通常 4~6 碼。
        # 不使用「00 開頭」作唯一判斷。
        # ----------------------------------------------------

        if not re.fullmatch(
            r"\d{4,6}[A-Za-z]?",
            code
        ):

            continue

        name = str(
            name or code
        ).strip()

        result.append(
            {
                "code":
                    code,

                "symbol":
                    code + ".TW",

                "name":
                    name,

                "market":
                    "TW",

                # 關鍵：
                # 即使名稱包含「債」，
                # 官方基金資料仍然分類 ETF。
                "type":
                    "etf",
            }
        )

    print(
        f"   TWSE ETF："
        f"{len(result)}"
    )

    return result


# ============================================================
# TPEx Universe
#
# TPEx 官方公開 API 介面可能因版本調整，
# 因此採多來源容錯。
# ============================================================

def parse_tpex_items(
    data
):

    if not isinstance(
        data,
        list
    ):

        return []

    result = []

    for row in data:

        if not isinstance(
            row,
            dict
        ):

            continue

        code = find_field(
            row,
            [
                "證券代號",
                "股票代號",
                "公司代號",
                "代號",
                "SecuritiesCompanyCode",
                "Code",
                "code",
            ]
        )

        name = find_field(
            row,
            [
                "證券名稱",
                "股票名稱",
                "公司簡稱",
                "名稱",
                "SecuritiesCompanyName",
                "Name",
                "name",
            ]
        )

        if code is None:
            continue

        code = str(
            code
        ).strip()

        if not re.fullmatch(
            r"\d{4,6}[A-Za-z]?",
            code
        ):

            continue

        # ----------------------------------------------------
        # 排除明顯權證
        # ----------------------------------------------------

        upper_code = code.upper()

        if len(code) > 4:

            # 多數權證不是單純四碼股票代號
            # 但不因為 ETF 長代號而全部排除。
            if not (
                code.isdigit()
                or (
                    code[:4].isdigit()
                    and code[4:].isalpha()
                )
            ):

                continue

        result.append(
            {
                "code":
                    code,

                "symbol":
                    code + ".TWO",

                "name":
                    str(
                        name or code
                    ),

                "market":
                    "TWO",

                "type":
                    infer_type(
                        code,
                        name
                    ),
            }
        )

    return result


def fetch_tpex_universe():

    print(
        "📡 TPEx：取得上櫃市場 Universe..."
    )

    for url in TPEX_API_CANDIDATES:

        data = get_json(
            url,
            timeout=20
        )

        result = parse_tpex_items(
            data
        )

        if result:

            print(
                f"   TPEx API："
                f"{len(result)}"
            )

            return result

    # --------------------------------------------------------
    # 如果 TPEx API 介面變動，
    # 不可以假裝成功。
    # --------------------------------------------------------

    print(
        "⚠️ TPEx 公開 Universe API "
        "目前未取得有效資料"
    )

    return []


# ============================================================
# 官方全市場 Universe
# ============================================================

def build_market_universe():

    print()
    print(
        "=" * 64
    )

    print(
        "🌐 建立全台股市場 Universe"
    )

    print(
        "=" * 64
    )

    twse_stocks = (
        fetch_twse_listed_stocks()
    )

    twse_etfs = (
        fetch_twse_etfs()
    )

    tpex_items = (
        fetch_tpex_universe()
    )

    combined = (
        twse_stocks
        + twse_etfs
        + tpex_items
    )

    universe = normalize_universe_items(
        combined
    )

    # --------------------------------------------------------
    # 嚴格驗證
    # --------------------------------------------------------

    if not universe:

        raise RuntimeError(
            "官方市場 API 未取得任何 Universe"
        )

    if len(universe) < UNIVERSE_MIN_EXPECTED:

        raise RuntimeError(
            "Universe 數量異常："
            f"{len(universe)} 檔。"
            "拒絕使用縮小後的 Universe。"
        )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

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

    print()

    print(
        "🌐 官方 Universe 完成"
    )

    print(
        f"   總數：{len(universe)}"
    )

    print(
        f"   股票：{stock_count}"
    )

    print(
        f"   ETF：{etf_count}"
    )

    print(
        f"   一般債券：{bond_count}"
    )

    return universe


# ============================================================
# stocks.json
# ============================================================

def load_stocks_json():

    if not os.path.exists(
        STOCKS_FILE
    ):

        return []

    try:

        if os.path.getsize(
            STOCKS_FILE
        ) == 0:

            return []

    except Exception:

        return []

    try:

        with open(
            STOCKS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(
                f
            )

    except Exception:

        return []

    if isinstance(
        data,
        list
    ):

        raw_items = data

    elif isinstance(
        data,
        dict
    ):

        raw_items = []

        for key in (
            "stocks",
            "data",
            "items",
            "universe",
            "symbols"
        ):

            if isinstance(
                data.get(key),
                list
            ):

                raw_items = data[key]

                break

    else:

        raw_items = []

    return normalize_universe_items(
        raw_items
    )


# ============================================================
# prices.json Universe cache
# ============================================================

def load_universe_from_prices():

    if not os.path.exists(
        PRICES_FILE
    ):

        return []

    try:

        with open(
            PRICES_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(
                f
            )

    except Exception:

        return []

    if not isinstance(
        data,
        dict
    ):

        return []

    universe = data.get(
        "universe"
    )

    if not isinstance(
        universe,
        dict
    ):

        return []

    items = universe.get(
        "items"
    )

    if not isinstance(
        items,
        list
    ):

        return []

    return normalize_universe_items(
        items
    )


# ============================================================
# Universe Loader
#
# 優先順序：
#
# 1. 官方市場 API
# 2. prices.json 完整 Universe
# 3. stocks.json
#
# 注意：
# 絕不使用 11 檔 fallback。
# ============================================================

def load_existing_universe():

    # --------------------------------------------------------
    # 第一順位：官方全市場
    # --------------------------------------------------------

    try:

        universe = (
            build_market_universe()
        )

        if universe:

            print()
            print(
                "✅ Universe source："
                "official_market_api"
            )

            return (
                universe,
                "official_market_api",
                False
            )

    except Exception as e:

        print()
        print(
            "⚠️ 官方市場 Universe 建立失敗："
        )

        print(
            f"   {e}"
        )

    # --------------------------------------------------------
    # 第二順位：prices.json
    #
    # 但必須是大 Universe。
    # --------------------------------------------------------

    cached = (
        load_universe_from_prices()
    )

    if len(cached) >= UNIVERSE_MIN_EXPECTED:

        print()
        print(
            "⚠️ 官方 API 暫時不可用"
        )

        print(
            "♻️ 使用 prices.json "
            "完整 Universe 快取"
        )

        return (
            cached,
            "prices.json_cache",
            True
        )

    # --------------------------------------------------------
    # 第三順位：stocks.json
    #
    # 同樣要求不能是 11 檔。
    # --------------------------------------------------------

    stocks = (
        load_stocks_json()
    )

    if len(stocks) >= UNIVERSE_MIN_EXPECTED:

        print()
        print(
            "⚠️ 官方 API 暫時不可用"
        )

        print(
            "♻️ 使用 stocks.json "
            "完整 Universe 快取"
        )

        return (
            stocks,
            "stocks.json_cache",
            True
        )

    # --------------------------------------------------------
    # 最終失敗
    # --------------------------------------------------------

    raise RuntimeError(
        "無法建立全市場 Universe。"
        "已禁止使用舊 11 檔 fallback。"
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

        for i, ts in enumerate(
            timestamps
        ):

            try:

                dt = (
                    datetime.fromtimestamp(
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

        return None


# ============================================================
# Technical Indicators
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

    # MA5
    df["ma5"] = close.rolling(
        5,
        min_periods=5
    ).mean()

    # MA20
    df["ma20"] = close.rolling(
        20,
        min_periods=20
    ).mean()

    # MACD
    ema12 = close.ewm(
        span=12,
        adjust=False,
        min_periods=12
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False,
        min_periods=26
    ).mean()

    df["macd"] = (
        ema12
        - ema26
    )

    df["macd_signal"] = (
        df["macd"].ewm(
            span=9,
            adjust=False,
            min_periods=9
        ).mean()
    )

    df["macd_hist"] = (
        df["macd"]
        - df["macd_signal"]
    )

    # RSI
    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        adjust=False,
        min_periods=14
    ).mean()

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

    # KD
    lowest_low = low.rolling(
        9,
        min_periods=9
    ).min()

    highest_high = high.rolling(
        9,
        min_periods=9
    ).max()

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

        previous_k = current_k

        previous_d = current_d

    df["k"] = k_values

    df["d"] = d_values

    # Volume MA5
    df["volume_ma5"] = volume.rolling(
        5,
        min_periods=5
    ).mean()

    return df


# ============================================================
# Core conditions
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
        and
        pd.notna(
            latest["macd_signal"]
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
            latest["volume_ma5"]
        )
        and
        latest["volume"]
        >=
        (
            latest["volume_ma5"]
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
        and
        macd_hist > 0
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
        and
        ma20 is not None
        and
        ma20 != 0
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
        and
        volume_ma5 is not None
        and
        volume_ma5 > 0
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
        and
        macd_hist > 0
    ):

        strength += 3

    if (
        rsi is not None
        and
        rsi > 50
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

    if latest_date > today_tw_date():

        return None

    close = safe_float(
        latest["close"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    if (
        close is None
        or
        previous_close is None
        or
        previous_close == 0
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

        "core_conditions":
            {
                key:
                    bool(value)
                for
                key, value
                in
                core["conditions"].items()
            },

    }


# ============================================================
# Latest market date
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

            if dt <= today_tw_date():

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
# Filter same market date
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