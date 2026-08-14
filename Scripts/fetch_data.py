# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V7.5
#
# 核心修正版：
# 1. 官方 TWSE / TPEx 建立市場 universe
# 2. Yahoo Finance 改採「批次下載」
# 3. 禁止 2159 檔逐檔呼叫 Yahoo
# 4. 批次下載失敗才進行有限 fallback
# 5. 保留 RSI / KD / MACD / MA20 / Volume
# 6. 後端產生 AI SCORE
# 7. 後端產生 TOP30
# 8. 有效資料不足時禁止覆蓋舊 prices.json
# 9. 原子式寫入
# 10. 輸出後再次驗證
# ============================================================

import os
import json
import math
import time
import tempfile
from datetime import datetime, timezone, timedelta

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

VERSION = "V7.5"

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

# 重要：
# 不再逐檔呼叫 Yahoo。
# 改成批次下載。
BATCH_SIZE = 40

BATCH_DELAY = 2.0

MAX_BATCH_RETRY = 3

MAX_SINGLE_RETRY = 2

SINGLE_RETRY_DELAY = 3.0

MIN_VALID_ROWS = 30

MIN_OUTPUT_STOCKS = 50

TOP_N = 30


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
]


# ============================================================
# HTTP
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
    ),
}


REQUEST_TIMEOUT = 30


# ============================================================
# Safe helpers
# ============================================================

def safe_float(
    value,
    default=None
):
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
                pd.DataFrame,
            ),
        ):
            return default

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:
        return default


def safe_int(
    value,
    default=None
):
    number = safe_float(value)

    if number is None:
        return default

    try:
        return int(number)
    except Exception:
        return default


def round_value(
    value,
    digits=2
):
    number = safe_float(value)

    if number is None:
        return None

    return round(
        number,
        digits
    )


def clean_code(value):

    if value is None:
        return None

    text = str(value).strip()

    if text.lower() in {
        "",
        "nan",
        "none",
        "null",
    }:
        return None

    for suffix in (
        ".TW",
        ".tw",
        ".TWO",
        ".two",
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


def yahoo_symbol(
    code,
    market
):

    code = clean_code(code)

    if market == "TPEx":
        return f"{code}.TWO"

    return f"{code}.TW"


def is_etf(
    code,
    name=""
):

    code = clean_code(code)

    if code in KNOWN_ETFS:
        return True

    text = str(
        name or ""
    ).upper()

    if "ETF" in text:
        return True

    if "指數股票型" in text:
        return True

    return False


def invalid_security(text):

    text = str(
        text or ""
    ).upper()

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

    for attempt in range(
        1,
        4
    ):
        try:

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as error:

            print(
                f"官方 API 失敗 "
                f"{attempt}/3: "
                f"{error}"
            )

            if attempt < 3:
                time.sleep(
                    attempt * 2
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

    if not isinstance(
        data,
        list
    ):
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

        if invalid_security(name):
            continue

        result.append({
            "code": code,
            "name": name,
            "market": "TWSE",
            "type": (
                "ETF"
                if is_etf(
                    code,
                    name
                )
                else "STOCK"
            ),
        })

    print(
        f"TWSE 有效標的：{len(result)}"
    )

    return result


# ============================================================
# TPEx
# ============================================================

def fetch_tpex():

    print(
        "取得 TPEx 官方上櫃資料..."
    )

    data = get_json(
        TPEX_QUOTES_API
    )

    if not isinstance(
        data,
        list
    ):
        print(
            "TPEx 官方 API 無有效資料"
        )
        return []

    result = []

    for row in data:

        code = clean_code(
            row.get(
                "SecuritiesCompanyCode"
            )
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

        result.append({
            "code": code,
            "name": name,
            "market": "TPEx",
            "type": (
                "ETF"
                if is_etf(
                    code,
                    name
                )
                else "STOCK"
            ),
        })

    print(
        f"TPEx 有效標的：{len(result)}"
    )

    return result


# ============================================================
# Universe
# ============================================================

def build_universe():

    twse = fetch_twse()

    tpex = fetch_tpex()

    universe = {}

    for item in (
        twse + tpex
    ):

        key = (
            f'{item["market"]}:'
            f'{item["code"]}'
        )

        universe[key] = item

    # --------------------------------------------------------
    # 關鍵標的備援
    # --------------------------------------------------------

    for code, name in KEY_SYMBOLS.items():

        key = f"TWSE:{code}"

        if key not in universe:

            universe[key] = {
                "code": code,
                "name": name,
                "market": "TWSE",
                "type": (
                    "ETF"
                    if code in KNOWN_ETFS
                    else "STOCK"
                ),
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
# Yahoo dataframe normalize
# ============================================================

def normalize_single_dataframe(
    df,
    symbol
):

    if df is None:
        return None

    if not isinstance(
        df,
        pd.DataFrame
    ):
        return None

    if df.empty:
        return None

    df = df.copy()

    # --------------------------------------------------------
    # MultiIndex
    # --------------------------------------------------------

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        # 常見格式：
        # ('Close', '2330.TW')
        #
        # 或：
        # ('2330.TW', 'Close')

        level0 = [
            str(x)
            for x in df.columns.get_level_values(0)
        ]

        level1 = [
            str(x)
            for x in df.columns.get_level_values(1)
        ]

        if "Close" in level0:

            selected = {}

            for col in df.columns:

                if str(col[0]) in {
                    "Close",
                    "Volume",
                }:
                    selected[
                        str(col[0])
                    ] = df[col]

            if selected:

                df = pd.DataFrame(
                    selected,
                    index=df.index
                )

        elif "Close" in level1:

            selected = {}

            for col in df.columns:

                if str(col[1]) in {
                    "Close",
                    "Volume",
                }:
                    selected[
                        str(col[1])
                    ] = df[col]

            if selected:

                df = pd.DataFrame(
                    selected,
                    index=df.index
                )

        else:

            try:

                if symbol in (
                    df.columns
                    .get_level_values(1)
                ):

                    df = df.xs(
                        symbol,
                        axis=1,
                        level=1
                    )

                elif symbol in (
                    df.columns
                    .get_level_values(0)
                ):

                    df = df.xs(
                        symbol,
                        axis=1,
                        level=0
                    )

            except Exception:
                pass

    df.columns = [
        str(col).strip()
        for col in df.columns
    ]

    if "Close" not in df.columns:
        return None

    if "Volume" not in df.columns:

        df["Volume"] = 0

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
        subset=["Close"]
    )

    df = df.sort_index()

    if len(df) < MIN_VALID_ROWS:
        return None

    return df


# ============================================================
# 批次 Yahoo 下載
# ============================================================

def download_batch(
    symbols
):

    if not symbols:
        return {}

    symbol_string = " ".join(
        symbols
    )

    for attempt in range(
        1,
        MAX_BATCH_RETRY + 1
    ):

        try:

            print(
                f"Yahoo 批次下載："
                f"{len(symbols)} 檔 "
                f"(第 {attempt} 次)"
            )

            raw = yf.download(
                tickers=symbol_string,
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
                group_by="column",
                timeout=30,
                multi_level_index=True,
            )

            if raw is None:
                raise ValueError(
                    "Yahoo 回傳 None"
                )

            if raw.empty:
                raise ValueError(
                    "Yahoo 回傳空資料"
                )

            result = {}

            # ------------------------------------------------
            # 單檔
            # ------------------------------------------------

            if len(symbols) == 1:

                symbol = symbols[0]

                df = normalize_single_dataframe(
                    raw,
                    symbol
                )

                if df is not None:
                    result[symbol] = df

                return result

            # ------------------------------------------------
            # 多檔 MultiIndex
            # ------------------------------------------------

            if isinstance(
                raw.columns,
                pd.MultiIndex
            ):

                level0 = set(
                    str(x)
                    for x in raw.columns
                    .get_level_values(0)
                )

                level1 = set(
                    str(x)
                    for x in raw.columns
                    .get_level_values(1)
                )

                # --------------------------------------------
                # 格式 A：
                # Close / Volume 第一層
                # ticker 第二層
                # --------------------------------------------

                if (
                    "Close" in level0
                    or
                    "Volume" in level0
                ):

                    for symbol in symbols:

                        selected = {}

                        for field in (
                            "Close",
                            "Volume",
                        ):

                            try:

                                selected[field] = (
                                    raw[
                                        (
                                            field,
                                            symbol
                                        )
                                    ]
                                )

                            except Exception:
                                pass

                        if "Close" not in selected:
                            continue

                        df = pd.DataFrame(
                            selected,
                            index=raw.index
                        )

                        df = normalize_single_dataframe(
                            df,
                            symbol
                        )

                        if df is not None:
                            result[symbol] = df

                # --------------------------------------------
                # 格式 B：
                # ticker 第一層
                # Close / Volume 第二層
                # --------------------------------------------

                elif (
                    "Close" in level1
                    or
                    "Volume" in level1
                ):

                    for symbol in symbols:

                        try:

                            sub = raw[symbol]

                            df = normalize_single_dataframe(
                                sub,
                                symbol
                            )

                            if df is not None:
                                result[symbol] = df

                        except Exception:
                            continue

            else:

                # 非 MultiIndex 防護
                if len(symbols) == 1:

                    symbol = symbols[0]

                    df = normalize_single_dataframe(
                        raw,
                        symbol
                    )

                    if df is not None:
                        result[symbol] = df

            print(
                f"批次成功："
                f"{len(result)}/{len(symbols)}"
            )

            return result

        except Exception as error:

            print(
                f"Yahoo 批次失敗 "
                f"{attempt}/{MAX_BATCH_RETRY}: "
                f"{error}"
            )

            if attempt < MAX_BATCH_RETRY:

                time.sleep(
                    attempt * 5
                )

    return {}


# ============================================================
# 單檔 fallback
# ============================================================

def download_single_fallback(
    symbol
):

    for attempt in range(
        1,
        MAX_SINGLE_RETRY + 1
    ):

        try:

            print(
                f"fallback：{symbol} "
                f"{attempt}/{MAX_SINGLE_RETRY}"
            )

            raw = yf.download(
                tickers=symbol,
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
                group_by="column",
                timeout=30,
                multi_level_index=True,
            )

            df = normalize_single_dataframe(
                raw,
                symbol
            )

            if df is not None:
                return df

        except Exception as error:

            print(
                f"{symbol} fallback 失敗："
                f"{error}"
            )

        if attempt < MAX_SINGLE_RETRY:
            time.sleep(
                SINGLE_RETRY_DELAY
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

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = 100 - (
        100 /
        (1 + rs)
    )

    return rsi.clip(
        0,
        100
    )


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
    # AI SCORE
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

        "stage_4": "動態風控",
    }

    change_pct = None

    if (
        latest_close is not None
        and
        previous_close not in (
            None,
            0,
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

        "price":
            round_value(
                latest_close,
                2
            ),

        "change_pct":
            round_value(
                change_pct,
                2
            ),

        "volume":
            safe_int(
                latest_volume
            ),

        "volume_ma5":
            safe_int(
                latest_ma5_volume
            ),

        "rsi":
            round_value(
                latest_rsi,
                2
            ),

        "kd_k":
            round_value(
                latest_k,
                2
            ),

        "kd_d":
            round_value(
                latest_d,
                2
            ),

        "macd":
            round_value(
                latest_macd,
                4
            ),

        "macd_signal":
            round_value(
                latest_macd_signal,
                4
            ),

        "ma20":
            round_value(
                latest_ma20,
                2
            ),

        "ai_score":
            score,

        "signal":
            signal,

        "result_tags":
            tags,

        "action":
            action,

        "risk_level":
            risk_level,

        "dca":
            dca,

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
                ma20_up,
        },
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
            ),
        ),
        reverse=True,
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
        if stock.get(
            "price"
        ) is not None
    )

    stock_count = sum(
        1
        for stock in stocks
        if stock.get(
            "type"
        ) == "STOCK"
    )

    etf_count = sum(
        1
        for stock in stocks
        if stock.get(
            "type"
        ) == "ETF"
    )

    listed_count = sum(
        1
        for stock in stocks
        if stock.get(
            "market"
        ) == "TWSE"
    )

    otc_count = sum(
        1
        for stock in stocks
        if stock.get(
            "market"
        ) == "TPEx"
    )

    core_count = sum(
        1
        for stock in stocks
        if stock.get(
            "signal"
        ) == "CORE"
    )

    strong_count = sum(
        1
        for stock in stocks
        if stock.get(
            "signal"
        ) == "STRONG"
    )

    ai_count = sum(
        1
        for stock in stocks
        if safe_float(
            stock.get(
                "ai_score"
            ),
            0
        ) >= 70
    )

    return {

        "total":
            total,

        "successful":
            successful,

        "failed":
            len(failed),

        "stocks":
            stock_count,

        "etf":
            etf_count,

        "twse":
            listed_count,

        "tpex":
            otc_count,

        "core":
            core_count,

        "strong":
            strong_count,

        "ai_70_plus":
            ai_count,

        "top30":
            min(
                TOP_N,
                total
            ),
    }


# ============================================================
# JSON sanitize
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
            for key, item
            in value.items()
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
            np.int32,
        )
    ):

        return int(value)

    if isinstance(
        value,
        (
            np.floating,
            np.float64,
            np.float32,
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
# Atomic write
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
# Validation
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
        "signal",
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

        if stock.get(
            "price"
        ) is None:

            raise RuntimeError(
                f'TOP30 '
                f'{stock.get("code")} '
                f'缺少 price'
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
    # 1. Universe
    # --------------------------------------------------------

    universe = build_universe()

    if len(universe) < MIN_OUTPUT_STOCKS:

        raise RuntimeError(
            "市場 universe 資料不足，"
            "停止更新。"
        )

    # --------------------------------------------------------
    # 2. 建立 symbol 對照
    # --------------------------------------------------------

    symbol_map = {}

    for security in universe:

        symbol = yahoo_symbol(
            security["code"],
            security["market"]
        )

        symbol_map[symbol] = security

    symbols = list(
        symbol_map.keys()
    )

    total = len(symbols)

    print(
        f"開始批次分析："
        f"{total} 個標的"
    )

    print(
        f"批次大小："
        f"{BATCH_SIZE}"
    )

    # --------------------------------------------------------
    # 3. 批次下載
    # --------------------------------------------------------

    history_map = {}

    failed = []

    total_batches = (
        (
            len(symbols)
            +
            BATCH_SIZE
            -
            1
        )
        //
        BATCH_SIZE
    )

    for batch_index in range(
        total_batches
    ):

        start = (
            batch_index *
            BATCH_SIZE
        )

        end = min(
            start +
            BATCH_SIZE,
            len(symbols)
        )

        batch = symbols[
            start:end
        ]

        print()
        print(
            "=" * 60
        )

        print(
            f"批次 "
            f"{batch_index + 1}/"
            f"{total_batches}"
        )

        print(
            f"範圍："
            f"{start + 1}-"
            f"{end}"
        )

        print(
            "=" * 60
        )

        result = download_batch(
            batch
        )

        history_map.update(
            result
        )

        print(
            f"本批成功："
            f"{len(result)}/"
            f"{len(batch)}"
        )

        if (
            batch_index + 1
            < total_batches
        ):

            time.sleep(
                BATCH_DELAY
            )

    # --------------------------------------------------------
    # 4. 對批次中失敗的少量標的做 fallback
    # --------------------------------------------------------

    missing_symbols = [
        symbol
        for symbol in symbols
        if symbol not in history_map
    ]

    print()
    print(
        "=" * 70
    )

    print(
        f"批次下載完成"
    )

    print(
        f"成功："
        f"{len(history_map)}"
    )

    print(
        f"缺失："
        f"{len(missing_symbols)}"
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # fallback 限制
    #
    # 如果 Yahoo 整體掛掉：
    # 不允許再把 2159 檔全部逐檔打回去。
    #
    # 最多只對前 100 檔做 fallback。
    # --------------------------------------------------------

    FALLBACK_LIMIT = 100

    if missing_symbols:

        fallback_symbols = missing_symbols[
            :FALLBACK_LIMIT
        ]

        print(
            f"開始有限 fallback："
            f"{len(fallback_symbols)} 檔"
        )

        for index, symbol in enumerate(
            fallback_symbols,
            start=1
        ):

            df = download_single_fallback(
                symbol
            )

            if df is not None:

                history_map[
                    symbol
                ] = df

            else:

                security = symbol_map[
                    symbol
                ]

                failed.append({
                    "code":
                        security["code"],

                    "name":
                        security["name"],

                    "market":
                        security["market"],

                    "type":
                        security["type"],

                    "reason":
                        "Yahoo 無有效資料",
                })

            if index % 10 == 0:

                print(
                    f"fallback 進度："
                    f"{index}/"
                    f"{len(fallback_symbols)}"
                )

        # ----------------------------------------------------
        # 剩餘未嘗試者直接記錄
        # ----------------------------------------------------

        for symbol in missing_symbols[
            FALLBACK_LIMIT:
        ]:

            security = symbol_map[
                symbol
            ]

            failed.append({
                "code":
                    security["code"],

                "name":
                    security["name"],

                "market":
                    security["market"],

                "type":
                    security["type"],

                "reason":
                    "批次下載無資料，"
                    "超過 fallback 上限",
            })

    # --------------------------------------------------------
    # 5. 分析成功資料
    # --------------------------------------------------------

    results = []

    for symbol, df in history_map.items():

        security = symbol_map.get(
            symbol
        )

        if security is None:
            continue

        try:

            analysis = analyze_history(
                df
            )

            if analysis.get(
                "price"
            ) is None:

                failed.append({
                    "code":
                        security["code"],

                    "name":
                        security["name"],

                    "market":
                        security["market"],

                    "type":
                        security["type"],

                    "reason":
                        "缺少最新股價",
                })

                continue

            result = {
                "code":
                    security["code"],

                "name":
                    security["name"],

                "market":
                    security["market"],

                "type":
                    security["type"],

                "symbol":
                    symbol,

                **analysis,
            }

            results.append(
                result
            )

        except Exception as error:

            failed.append({
                "code":
                    security["code"],

                "name":
                    security["name"],

                "market":
                    security["market"],

                "type":
                    security["type"],

                "reason":
                    str(error),
            })

    # --------------------------------------------------------
    # 6. 安全檢查
    # --------------------------------------------------------

    print()
    print(
        "=" * 70
    )

    print(
        f"分析完成："
        f"成功 {len(results)}"
        f" / "
        f"失敗 {len(failed)}"
    )

    print(
        "=" * 70
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
            f"有效資料只有 "
            f"{len(results)} 筆，"
            f"低於安全門檻 "
            f"{MIN_OUTPUT_STOCKS}"
        )

    # --------------------------------------------------------
    # 7. 排名
    # --------------------------------------------------------

    ranked = build_rankings(
        results
    )

    top30 = ranked[
        :TOP_N
    ]

    # --------------------------------------------------------
    # 8. Statistics
    # --------------------------------------------------------

    statistics = build_statistics(
        ranked,
        failed
    )

    # --------------------------------------------------------
    # 9. Payload
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
            failed,
    }

    # --------------------------------------------------------
    # 10. Validate
    # --------------------------------------------------------

    validate_output(
        payload
    )

    # --------------------------------------------------------
    # 11. Atomic write
    # --------------------------------------------------------

    atomic_write_json(
        payload
    )

    # --------------------------------------------------------
    # 12. Post-write validation
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
    # 13. TOP30
    # --------------------------------------------------------

    print()
    print(
        "=" * 70
    )

    print(
        f"TOP {TOP_N}"
    )

    print(
        "=" * 70
    )

    for stock in top30:

        print(
            f'#{stock["rank"]:02d} '
            f'{stock["code"]} '
            f'{stock["name"]} '
            f'price={stock["price"]} '
            f'score={stock["ai_score"]} '
            f'signal={stock["signal"]}'
        )

    print(
        "=" * 70
    )

    elapsed = (
        time.time()
        -
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

    print(
        "=" * 70
    )

    print(
        "資料更新成功。"
    )

    print(
        "=" * 70
    )


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    main()
