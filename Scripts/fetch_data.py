# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V7.4.2
#
# ============================================================
# V7.4.2 正式資料層
#
# 核心目的：
#
# 1. 建立穩定的台股上市 / 上櫃 / ETF 資料集
# 2. 後端完成所有核心技術分析
# 3. 後端產生 AI SCORE
# 4. 後端產生 TOP 30
# 5. 前端只負責讀取與顯示
# 6. 禁止空資料覆蓋有效 prices.json
#
# 核心欄位：
#
# code
# name
# market
# type
# price
# change_pct
# volume
# rsi
# kd_k
# kd_d
# macd
# macd_signal
# ma20
# volume_ma5
# ai_score
# signal
# result_tags
# action
# risk_level
#
# ============================================================


import os
import io
import json
import math
import time
import tempfile
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import requests
except ImportError:
    requests = None


# ============================================================
# 基本設定
# ============================================================

VERSION = "V7.4.2"

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "Data"
)

OUTPUT_FILE = os.path.join(
    DATA_DIR,
    "prices.json"
)

os.makedirs(
    DATA_DIR,
    exist_ok=True
)


# ============================================================
# 台灣時區
# ============================================================

TW_TZ = timezone(
    timedelta(hours=8)
)


# ============================================================
# Yahoo 設定
# ============================================================

YF_PERIOD = "1y"

YF_INTERVAL = "1d"

MAX_RETRY = 3

RETRY_DELAY = 1.5

MAX_WORKERS = 6

REQUEST_TIMEOUT = 30


# ============================================================
# 最低資料安全門檻
# ============================================================

MIN_VALID_ROWS = 30

MIN_OUTPUT_STOCKS = 50

TOP_N = 30


# ============================================================
# HTTP Headers
# ============================================================

HEADERS = {
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
    )
}


# ============================================================
# 官方 API
# ============================================================

TWSE_STOCK_API = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/STOCK_DAY_ALL"
)

TPEX_QUOTES_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/tpex_mainboard_quotes"
)

TPEX_DAILY_API = (
    "https://www.tpex.org.tw/"
    "openapi/v1/tpex_mainboard_daily_close_quotes"
)

TWSE_ISIN_API = (
    "https://isin.twse.com.tw/"
    "isin/C_public.jsp"
)


# ============================================================
# 重要標的
# ============================================================

KEY_SYMBOLS = {
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "00713": "元大台灣高息低波",
    "00878": "國泰永續高股息",
    "00919": "群益台灣精選高息",
    "2330": "台積電",
    "2337": "旺宏",
    "2426": "鼎元",
}


# ============================================================
# ETF 清單
# ============================================================

KNOWN_ETFS = {
    "0050",
    "0051",
    "0052",
    "0053",
    "0055",
    "0056",
    "0057",
    "0061",
    "006201",
    "006203",
    "006204",
    "006205",
    "006206",
    "006208",
    "00690",
    "00692",
    "00701",
    "00713",
    "00730",
    "00731",
    "00733",
    "00735",
    "00736",
    "00737",
    "00739",
    "00740",
    "00741",
    "00752",
    "00753L",
    "00757",
    "00762",
    "00770",
    "00771",
    "00772",
    "00774B",
    "00775B",
    "00783",
    "00785",
    "00786",
    "00788B",
    "00789B",
    "00830",
    "00850",
    "00858",
    "00861",
    "00865B",
    "00875",
    "00876",
    "00878",
    "00881",
    "00882",
    "00885",
    "00891",
    "00892",
    "00893",
    "00894",
    "00895",
    "00896",
    "00897",
    "00898",
    "00899",
    "00900",
    "00901",
    "00902",
    "00903",
    "00904",
    "00905",
    "00907",
    "00908",
    "00909",
    "00910",
    "00911",
    "00912",
    "00913",
    "00915",
    "00916",
    "00917",
    "00918",
    "00919",
    "00920",
    "00921",
    "00922",
    "00923",
    "00924",
    "00925",
    "00926",
    "00927",
    "00928",
    "00929",
    "00930",
    "00931",
    "00932",
    "00934",
    "00935",
    "00936",
    "00937",
    "00938",
    "00939",
    "00940",
    "00941",
    "00942",
    "00943",
    "00944",
    "00945",
    "00946",
    "00947",
    "00948",
    "00949",
    "00950",
    "00951",
    "00952",
    "00953",
    "00954",
    "00955",
    "00956",
    "00957",
    "00958",
    "00959",
    "00960",
    "00961",
    "00962",
    "00963",
    "00964",
    "00965",
    "00966",
    "00967",
    "00968",
    "00969",
    "00970",
    "00971",
    "00972",
    "00973",
    "00974",
    "00975",
    "00976",
    "00977",
    "00978",
    "00979",
    "00980",
    "00981",
    "00982",
    "00983",
    "00984",
    "00985",
    "00986",
    "00987",
    "00988",
    "00989",
    "00990",
    "00991",
    "00992",
    "00993",
    "00994",
    "00995",
    "00996",
}


# ============================================================
# 排除商品
# ============================================================

INVALID_SECURITY_KEYWORDS = [
    "權證",
    "認購權證",
    "認售權證",
    "牛熊證",
    "公司債",
    "債券",
    "金融債",
    "海外存託",
    "存託憑證",
    "存託",
    "受益證券",
    "ETN",
    "可轉債",
    "轉換公司債",
    "特別股權證",
]


# ============================================================
# Safe helpers
# ============================================================

def safe_float(value, default=None):

    try:

        if value is None:
            return default

        if isinstance(
            value,
            (
                list,
                tuple,
                dict,
                pd.Series,
                pd.DataFrame
            )
        ):
            return default

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:

        return default


def safe_int(value, default=None):

    number = safe_float(value)

    if number is None:
        return default

    try:
        return int(number)

    except Exception:
        return default


def clean_code(value):

    if value is None:
        return None

    text = str(value).strip()

    if text.lower() in {
        "",
        "nan",
        "none",
        "null"
    }:
        return None

    for suffix in (
        ".TW",
        ".tw",
        ".TWO",
        ".two"
    ):
        if text.endswith(suffix):
            text = text[:-len(suffix)]

    return text.strip()


def valid_code(code):

    code = clean_code(code)

    if not code:
        return False

    if len(code) < 4:
        return False

    if len(code) > 6:
        return False

    return all(
        char.isdigit()
        for char in code
    )


def yahoo_symbol(code, market):

    code = clean_code(code)

    if market == "TPEx":
        return f"{code}.TWO"

    return f"{code}.TW"


def is_etf(code, name=""):

    code = clean_code(code)

    if code in KNOWN_ETFS:
        return True

    text = str(name or "").upper()

    if "ETF" in text:
        return True

    if "指數股票型" in text:
        return True

    return False


def invalid_security(text):

    text = str(text or "").upper()

    return any(
        keyword.upper() in text
        for keyword in INVALID_SECURITY_KEYWORDS
    )


# ============================================================
# HTTP JSON
# ============================================================

def get_json(url):

    if requests is None:

        print(
            "requests 不存在，無法使用官方 API"
        )

        return None

    for attempt in range(1, MAX_RETRY + 1):

        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as error:

            print(
                f"API 失敗 "
                f"{attempt}/{MAX_RETRY}: "
                f"{error}"
            )

            if attempt < MAX_RETRY:
                time.sleep(
                    RETRY_DELAY * attempt
                )

    return None


# ============================================================
# TWSE
# ============================================================

def fetch_twse():

    print(
        "取得 TWSE 官方上市資料..."
    )

    data = get_json(
        TWSE_STOCK_API
    )

    if not isinstance(data, list):

        print(
            "TWSE 官方 API 無有效資料"
        )

        return []

    result = []

    for row in data:

        code = clean_code(
            row.get("Code")
        )

        name = str(
            row.get("Name") or ""
        ).strip()

        if not valid_code(code):
            continue

        if invalid_security(
            name
        ):
            continue

        etf = is_etf(
            code,
            name
        )

        result.append({
            "code": code,
            "name": name,
            "market": "TWSE",
            "type": (
                "ETF"
                if etf
                else "STOCK"
            )
        })

    print(
        f"TWSE 有效標的：{len(result)}"
    )

    return result


# ============================================================
# TPEX
# ============================================================

def fetch_tpex():

    print(
        "取得 TPEx 官方上櫃資料..."
    )

    data = get_json(
        TPEX_QUOTES_API
    )

    if not isinstance(data, list):

        print(
            "TPEx quotes API 無有效資料"
        )

        return []

    result = []

    for row in data:

        code = clean_code(
            row.get("SecuritiesCompanyCode")
            or row.get("Code")
            or row.get("股票代號")
        )

        name = str(
            row.get("CompanyName")
            or row.get("Name")
            or row.get("公司簡稱")
            or ""
        ).strip()

        if not valid_code(code):
            continue

        if invalid_security(name):
            continue

        etf = is_etf(
            code,
            name
        )

        result.append({
            "code": code,
            "name": name,
            "market": "TPEx",
            "type": (
                "ETF"
                if etf
                else "STOCK"
            )
        })

    print(
        f"TPEx 有效標的：{len(result)}"
    )

    return result


# ============================================================
# 官方市場清單
# ============================================================

def build_universe():

    twse = fetch_twse()

    tpex = fetch_tpex()

    universe = {}

    for item in (
        twse + tpex
    ):

        code = item["code"]

        universe[
            f'{item["market"]}:{code}'
        ] = item

    # --------------------------------------------------------
    # 關鍵 ETF 如果官方 API 暫時漏掉，加入備援
    # --------------------------------------------------------

    for code, name in KEY_SYMBOLS.items():

        if code in KNOWN_ETFS:

            key = f"TWSE:{code}"

            if key not in universe:

                universe[key] = {
                    "code": code,
                    "name": name,
                    "market": "TWSE",
                    "type": "ETF"
                }

    result = list(
        universe.values()
    )

    print(
        f"最終掃描 universe："
        f"{len(result)}"
    )

    return result


# ============================================================
# yfinance 資料
# ============================================================

def download_history(
    symbol
):

    for attempt in range(
        1,
        MAX_RETRY + 1
    ):

        try:

            df = yf.download(
                symbol,
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if df is None:
                raise ValueError(
                    "Yahoo 回傳 None"
                )

            if df.empty:
                raise ValueError(
                    "Yahoo 回傳空 DataFrame"
                )

            # MultiIndex 防護
            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                if symbol in df.columns.get_level_values(-1):

                    try:
                        df = df.xs(
                            symbol,
                            axis=1,
                            level=-1
                        )
                    except Exception:
                        pass

                if isinstance(
                    df.columns,
                    pd.MultiIndex
                ):

                    df.columns = [
                        col[0]
                        for col in df.columns
                    ]

            df = df.copy()

            df.columns = [
                str(col).strip()
                for col in df.columns
            ]

            required = {
                "Close",
                "Volume"
            }

            if not required.issubset(
                set(df.columns)
            ):

                raise ValueError(
                    "缺少 Close / Volume"
                )

            df = df[
                ["Close", "Volume"]
            ].copy()

            df["Close"] = pd.to_numeric(
                df["Close"],
                errors="coerce"
            )

            df["Volume"] = pd.to_numeric(
                df["Volume"],
                errors="coerce"
            )

            df = df.dropna(
                subset=[
                    "Close"
                ]
            )

            if len(df) < MIN_VALID_ROWS:

                raise ValueError(
                    f"有效資料不足：{len(df)}"
                )

            return df

        except Exception as error:

            print(
                f"{symbol} "
                f"Yahoo 失敗 "
                f"{attempt}/{MAX_RETRY}: "
                f"{error}"
            )

            if attempt < MAX_RETRY:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    return None


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    period=14
):

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    rsi = rsi.clip(
        0,
        100
    )

    return rsi


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    close
):

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    macd = (
        ema12 -
        ema26
    )

    signal = macd.ewm(
        span=9,
        adjust=False
    ).mean()

    histogram = (
        macd -
        signal
    )

    return (
        macd,
        signal,
        histogram
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(
    close,
    period=9
):

    low = close.rolling(
        period
    ).min()

    high = close.rolling(
        period
    ).max()

    denominator = (
        high - low
    ).replace(
        0,
        np.nan
    )

    rsv = (
        (close - low) /
        denominator
    ) * 100

    k = rsv.ewm(
        com=2,
        adjust=False
    ).mean()

    d = k.ewm(
        com=2,
        adjust=False
    ).mean()

    return (
        k.clip(0, 100),
        d.clip(0, 100)
    )


# ============================================================
# 技術分析
# ============================================================

def analyze_history(
    df
):

    close = df["Close"]

    volume = df["Volume"]

    ma20 = close.rolling(
        20
    ).mean()

    ma20_prev = ma20.shift(1)

    ma5_volume = volume.rolling(
        5
    ).mean()

    rsi = calculate_rsi(
        close
    )

    macd, macd_signal, macd_hist = (
        calculate_macd(
            close
        )
    )

    kd_k, kd_d = calculate_kd(
        close
    )

    latest_close = safe_float(
        close.iloc[-1]
    )

    previous_close = (
        safe_float(
            close.iloc[-2]
        )
        if len(close) >= 2
        else None
    )

    latest_volume = safe_float(
        volume.iloc[-1]
    )

    latest_ma5_volume = safe_float(
        ma5_volume.iloc[-1]
    )

    latest_ma20 = safe_float(
        ma20.iloc[-1]
    )

    previous_ma20 = safe_float(
        ma20_prev.iloc[-1]
    )

    latest_rsi = safe_float(
        rsi.iloc[-1]
    )

    previous_rsi = safe_float(
        rsi.iloc[-2]
    )

    latest_k = safe_float(
        kd_k.iloc[-1]
    )

    previous_k = safe_float(
        kd_k.iloc[-2]
    )

    latest_d = safe_float(
        kd_d.iloc[-1]
    )

    previous_d = safe_float(
        kd_d.iloc[-2]
    )

    latest_macd = safe_float(
        macd.iloc[-1]
    )

    previous_macd = safe_float(
        macd.iloc[-2]
    )

    latest_macd_signal = safe_float(
        macd_signal.iloc[-1]
    )

    previous_macd_signal = safe_float(
        macd_signal.iloc[-2]
    )

    # --------------------------------------------------------
    # 條件
    # --------------------------------------------------------

    macd_golden_cross = (
        previous_macd is not None
        and
        previous_macd_signal is not None
        and
        latest_macd is not None
        and
        latest_macd_signal is not None
        and
        previous_macd <= previous_macd_signal
        and
        latest_macd > latest_macd_signal
    )

    rsi_above_50 = (
        latest_rsi is not None
        and
        latest_rsi > 50
    )

    kd_golden_cross = (
        previous_k is not None
        and
        previous_d is not None
        and
        latest_k is not None
        and
        latest_d is not None
        and
        previous_k <= previous_d
        and
        latest_k > latest_d
    )

    volume_expand = (
        latest_volume is not None
        and
        latest_ma5_volume is not None
        and
        latest_ma5_volume > 0
        and
        latest_volume >= (
            latest_ma5_volume * 1.5
        )
    )

    above_ma20 = (
        latest_close is not None
        and
        latest_ma20 is not None
        and
        latest_close > latest_ma20
    )

    ma20_up = (
        previous_ma20 is not None
        and
        latest_ma20 is not None
        and
        latest_ma20 > previous_ma20
    )

    # --------------------------------------------------------
    # Score
    #
    # 核心條件：
    #
    # MACD 黃金交叉
    # RSI > 50
    # KD K>D
    # 成交量 >= MA5 * 1.5
    #
    # 輔助：
    # 站上 MA20
    # MA20 向上
    # --------------------------------------------------------

    score = 0

    if macd_golden_cross:
        score += 25

    if rsi_above_50:
        score += 20

    if kd_golden_cross:
        score += 20

    if volume_expand:
        score += 15

    if above_ma20:
        score += 10

    if ma20_up:
        score += 10

    score = float(
        max(
            0,
            min(
                100,
                score
            )
        )
    )

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    if score >= 85:

        signal = "CORE"

    elif score >= 70:

        signal = "STRONG"

    elif score >= 60:

        signal = "BULL"

    else:

        signal = "WATCH"

    # --------------------------------------------------------
    # Tags
    # --------------------------------------------------------

    tags = []

    if macd_golden_cross:
        tags.append(
            "MACD 黃金交叉"
        )

    if rsi_above_50:
        tags.append(
            "RSI > 50"
        )

    if kd_golden_cross:
        tags.append(
            "KD 黃金交叉"
        )

    if volume_expand:
        tags.append(
            "成交量放大"
        )

    if above_ma20:
        tags.append(
            "站上 MA20"
        )

    if ma20_up:
        tags.append(
            "MA20 向上"
        )

    if not tags:
        tags.append(
            "等待訊號改善"
        )

    # --------------------------------------------------------
    # Action
    # --------------------------------------------------------

    if score >= 85:

        action = "可列入優先觀察"

    elif score >= 70:

        action = "可分批觀察"

    elif score >= 60:

        action = "等待較佳切入點"

    else:

        action = "持續觀察"

    # --------------------------------------------------------
    # Risk
    # --------------------------------------------------------

    if above_ma20 and ma20_up:

        risk_level = "正常"

    elif above_ma20:

        risk_level = "中性"

    else:

        risk_level = "偏高"

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca = {
        "stage_1": (
            "建立小部位"
            if score >= 60
            else "暫不新增"
        ),
        "stage_2": (
            "訊號延續再加碼"
            if score >= 70
            else "等待"
        ),
        "stage_3": (
            "趨勢確認後加碼"
            if score >= 80
            else "等待"
        ),
        "stage_4": (
            "動態風控"
        )
    }

    change_pct = None

    if (
        latest_close is not None
        and
        previous_close not in (
            None,
            0
        )
    ):

        change_pct = (
            (
                latest_close -
                previous_close
            )
            /
            previous_close
        ) * 100

    return {
        "price": round_value(
            latest_close,
            2
        ),

        "change_pct": round_value(
            change_pct,
            2
        ),

        "volume": safe_int(
            latest_volume
        ),

        "volume_ma5": safe_int(
            latest_ma5_volume
        ),

        "rsi": round_value(
            latest_rsi,
            2
        ),

        "kd_k": round_value(
            latest_k,
            2
        ),

        "kd_d": round_value(
            latest_d,
            2
        ),

        "macd": round_value(
            latest_macd,
            4
        ),

        "macd_signal": round_value(
            latest_macd_signal,
            4
        ),

        "ma20": round_value(
            latest_ma20,
            2
        ),

        "ai_score": score,

        "signal": signal,

        "result_tags": tags,

        "action": action,

        "risk_level": risk_level,

        "dca": dca,

        "conditions": {
            "macd_golden_cross":
                macd_golden_cross,

            "rsi_above_50":
                rsi_above_50,

            "kd_golden_cross":
                kd_golden_cross,

            "volume_expand":
                volume_expand,

            "above_ma20":
                above_ma20,

            "ma20_up":
                ma20_up
        }
    }


# ============================================================
# 單一標的分析
# ============================================================

def process_security(
    security
):

    code = security["code"]

    name = security["name"]

    market = security["market"]

    security_type = security["type"]

    symbol = yahoo_symbol(
        code,
        market
    )

    try:

        df = download_history(
            symbol
        )

        if df is None:

            return None, {
                "code": code,
                "name": name,
                "market": market,
                "type": security_type,
                "reason": "Yahoo 無有效資料"
            }

        analysis = analyze_history(
            df
        )

        if (
            analysis["price"]
            is None
        ):

            return None, {
                "code": code,
                "name": name,
                "market": market,
                "type": security_type,
                "reason": "缺少股價"
            }

        result = {
            "code": code,
            "name": name,
            "market": market,
            "type": security_type,
            "symbol": symbol,
            **analysis
        }

        return result, None

    except Exception as error:

        return None, {
            "code": code,
            "name": name,
            "market": market,
            "type": security_type,
            "reason": str(error)
        }


# ============================================================
# 排名
# ============================================================

def build_rankings(
    stocks
):

    ranked = sorted(
        stocks,
        key=lambda item: (
            safe_float(
                item.get(
                    "ai_score"
                ),
                -1
            ),
            safe_float(
                item.get(
                    "change_pct"
                ),
                -999
            )
        ),
        reverse=True
    )

    for index, stock in enumerate(
        ranked,
        start=1
    ):

        stock["rank"] = index

    return ranked


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks,
    failed
):

    total = len(stocks)

    successful = sum(
        1
        for stock in stocks
        if stock.get("price") is not None
    )

    stock_count = sum(
        1
        for stock in stocks
        if stock.get("type") == "STOCK"
    )

    etf_count = sum(
        1
        for stock in stocks
        if stock.get("type") == "ETF"
    )

    listed_count = sum(
        1
        for stock in stocks
        if stock.get("market") == "TWSE"
    )

    otc_count = sum(
        1
        for stock in stocks
        if stock.get("market") == "TPEx"
    )

    core_count = sum(
        1
        for stock in stocks
        if stock.get("signal") == "CORE"
    )

    strong_count = sum(
        1
        for stock in stocks
        if stock.get("signal") == "STRONG"
    )

    ai_count = sum(
        1
        for stock in stocks
        if safe_float(
            stock.get("ai_score"),
            0
        ) >= 70
    )

    return {
        "total": total,
        "successful": successful,
        "failed": len(failed),
        "stocks": stock_count,
        "etf": etf_count,
        "twse": listed_count,
        "tpex": otc_count,
        "core": core_count,
        "strong": strong_count,
        "ai_70_plus": ai_count,
        "top30": min(
            TOP_N,
            total
        )
    }


# ============================================================
# JSON 安全序列化
# ============================================================

def sanitize_json(
    value
):

    if isinstance(
        value,
        dict
    ):

        return {
            str(key):
                sanitize_json(item)
            for key, item in value.items()
        }

    if isinstance(
        value,
        list
    ):

        return [
            sanitize_json(item)
            for item in value
        ]

    if isinstance(
        value,
        (
            np.integer,
            np.int64,
            np.int32
        )
    ):

        return int(value)

    if isinstance(
        value,
        (
            np.floating,
            np.float64,
            np.float32
        )
    ):

        number = float(value)

        if math.isfinite(number):
            return number

        return None

    if isinstance(
        value,
        np.bool_
    ):

        return bool(value)

    if isinstance(
        value,
        float
    ):

        if math.isfinite(value):
            return value

        return None

    return value


# ============================================================
# 讀取舊資料
# ============================================================

def load_existing_json():

    if not os.path.exists(
        OUTPUT_FILE
    ):

        return None

    try:

        with open(
            OUTPUT_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        if not isinstance(
            data,
            dict
        ):

            return None

        stocks = data.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            list
        ):

            return None

        if len(stocks) < MIN_OUTPUT_STOCKS:

            return None

        return data

    except Exception:

        return None


# ============================================================
# 原子式寫入
# ============================================================

def atomic_write_json(
    data
):

    data = sanitize_json(
        data
    )

    directory = os.path.dirname(
        OUTPUT_FILE
    )

    fd, temp_path = tempfile.mkstemp(
        prefix="prices_",
        suffix=".json",
        dir=directory
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

            file.flush()

            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            OUTPUT_FILE
        )

    except Exception:

        try:

            os.remove(
                temp_path
            )

        except Exception:
            pass

        raise


# ============================================================
# 驗證輸出
# ============================================================

def validate_output(
    data
):

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "輸出資料不是 JSON object"
        )

    stocks = data.get(
        "stocks"
    )

    top30 = data.get(
        "top30"
    )

    if not isinstance(
        stocks,
        list
    ):

        raise RuntimeError(
            "stocks 不是 list"
        )

    if not isinstance(
        top30,
        list
    ):

        raise RuntimeError(
            "top30 不是 list"
        )

    if len(stocks) < MIN_OUTPUT_STOCKS:

        raise RuntimeError(
            "有效股票資料不足，"
            "禁止覆蓋 prices.json"
        )

    if len(top30) == 0:

        raise RuntimeError(
            "top30 為空，"
            "禁止覆蓋 prices.json"
        )

    required_fields = {
        "code",
        "name",
        "market",
        "type",
        "price",
        "ai_score",
        "signal"
    }

    for stock in top30:

        missing = (
            required_fields -
            set(stock.keys())
        )

        if missing:

            raise RuntimeError(
                "TOP30 缺少欄位："
                +
                ",".join(
                    sorted(missing)
                )
            )

        if (
            stock.get("price")
            is None
        ):

            raise RuntimeError(
                f'TOP30 {stock.get("code")} '
                "缺少 price"
            )

    return True


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    now = datetime.now(
        TW_TZ
    )

    print("=" * 70)

    print(
        f"台股 AI 選股資料更新 "
        f"{VERSION}"
    )

    print(
        "開始時間："
        +
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    print("=" * 70)

    # --------------------------------------------------------
    # 1. 建立 universe
    # --------------------------------------------------------

    universe = build_universe()

    if len(universe) < MIN_OUTPUT_STOCKS:

        raise RuntimeError(
            "市場 universe 資料不足，"
            "停止更新。"
        )

    # --------------------------------------------------------
    # 2. 分析
    # --------------------------------------------------------

    results = []

    failed = []

    completed = 0

    total = len(
        universe
    )

    print(
        f"開始分析 {total} 個標的..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                process_security,
                security
            ): security
            for security in universe
        }

        for future in as_completed(
            futures
        ):

            completed += 1

            try:

                result, error = (
                    future.result()
                )

                if result is not None:

                    results.append(
                        result
                    )

                if error is not None:

                    failed.append(
                        error
                    )

            except Exception as error:

                security = futures[
                    future
                ]

                failed.append({
                    "code":
                        security.get(
                            "code"
                        ),
                    "name":
                        security.get(
                            "name"
                        ),
                    "market":
                        security.get(
                            "market"
                        ),
                    "type":
                        security.get(
                            "type"
                        ),
                    "reason":
                        str(error)
                })

            if (
                completed % 100 == 0
                or
                completed == total
            ):

                print(
                    f"進度："
                    f"{completed}/{total} "
                    f"| 成功："
                    f"{len(results)} "
                    f"| 失敗："
                    f"{len(failed)}"
                )

    # --------------------------------------------------------
    # 3. 基本安全檢查
    # --------------------------------------------------------

    print(
        f"分析完成："
        f"成功 {len(results)}"
        f" / "
        f"失敗 {len(failed)}"
    )

    if len(results) < MIN_OUTPUT_STOCKS:

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "本次資料不足，"
            "禁止覆蓋舊 prices.json"
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        raise RuntimeError(
            f"有效資料只有 {len(results)} "
            f"筆，低於安全門檻 "
            f"{MIN_OUTPUT_STOCKS}"
        )

    # --------------------------------------------------------
    # 4. 排名
    # --------------------------------------------------------

    ranked = build_rankings(
        results
    )

    top30 = ranked[
        :TOP_N
    ]

    # --------------------------------------------------------
    # 5. 統計
    # --------------------------------------------------------

    statistics = build_statistics(
        ranked,
        failed
    )

    # --------------------------------------------------------
    # 6. JSON
    # --------------------------------------------------------

    payload = {

        "version":
            VERSION,

        "updated_at":
            now.isoformat(),

        "generated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "timezone":
            "Asia/Taipei",

        "top_n":
            TOP_N,

        "statistics":
            statistics,

        "top30":
            top30,

        "rankings":
            ranked,

        "stocks":
            ranked,

        "failed":
            failed
    }

    # --------------------------------------------------------
    # 7. 輸出驗證
    # --------------------------------------------------------

    validate_output(
        payload
    )

    # --------------------------------------------------------
    # 8. 原子寫入
    # --------------------------------------------------------

    atomic_write_json(
        payload
    )

    # --------------------------------------------------------
    # 9. 寫入後再次驗證
    # --------------------------------------------------------

    if not os.path.exists(
        OUTPUT_FILE
    ):

        raise RuntimeError(
            "prices.json 寫入後不存在"
        )

    file_size = os.path.getsize(
        OUTPUT_FILE
    )

    if file_size < 1000:

        raise RuntimeError(
            "prices.json 異常過小"
        )

    # --------------------------------------------------------
    # 10. 顯示 TOP 30
    # --------------------------------------------------------

    print()

    print("=" * 70)

    print(
        f"TOP {TOP_N}"
    )

    print("=" * 70)

    for stock in top30:

        print(
            f'#{stock["rank"]:02d} '
            f'{stock["code"]} '
            f'{stock["name"]} '
            f'price={stock["price"]} '
            f'score={stock["ai_score"]} '
            f'signal={stock["signal"]}'
        )

    print("=" * 70)

    elapsed = (
        time.time() -
        start_time
    )

    print(
        f"成功寫入："
        f"{OUTPUT_FILE}"
    )

    print(
        f"檔案大小："
        f"{file_size:,} bytes"
    )

    print(
        f"成功資料："
        f"{len(results):,}"
    )

    print(
        f"失敗資料："
        f"{len(failed):,}"
    )

    print(
        f"TOP30："
        f"{len(top30)}"
    )

    print(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    print("=" * 70)

    print(
        "資料更新成功。"
    )

    print("=" * 70)


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    main()
