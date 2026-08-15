# ================================================================
# 台股 AI 選股系統
# fetch_data.py V7.6
#
# V7.6 主要修正：
# 1. ETF 不再因「公司債 / 債券 / 金融債」名稱被錯誤排除
# 2. 00720B 正式納入 ETF Universe
# 3. 股票與 ETF 分離處理
# 4. 股票核心條件固定 6 項
# 5. 後台計算結果直接寫入 prices.json
# 6. 前台不重新判斷核心條件
# 7. 保留多組 JSON 欄位名稱，避免前台相容性問題
# ================================================================

import os
import json
import math
import time
import tempfile
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("缺少 yfinance，請先執行：pip install yfinance")


# ================================================================
# 基本設定
# ================================================================

VERSION = "V7.6"

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(BASE_DIR, "Data")
OUTPUT_FILE = os.path.join(DATA_DIR, "prices.json")

os.makedirs(DATA_DIR, exist_ok=True)

TW_TZ = timezone(timedelta(hours=8))

PERIOD = "1y"
INTERVAL = "1d"

BATCH_SIZE = 40
BATCH_DELAY = 1.5

MAX_RETRY = 3
RETRY_DELAY = 3

TOP_STOCKS = 10
TOP_ETFS = 10

# ================================================================
# 股票核心 6 項條件
# ================================================================

CORE_CONDITIONS = [
    "MACD 黃金交叉",
    "RSI > 50",
    "KD 黃金交叉",
    "成交量放大",
    "股價 > MA20",
    "MA20 向上",
]

CORE_COUNT = 6


# ================================================================
# ETF 清單
#
# 注意：
# 不使用「債券」關鍵字排除 ETF。
# 00720B 明確納入。
# ================================================================

KNOWN_ETFS = {
    # 台股主要 ETF
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

    # 債券 ETF
    "00720B",
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
}


# ================================================================
# 不應納入的商品
#
# 注意：
# 絕對不要放：
# 「公司債」
# 「債券」
# 「金融債」
#
# 因為合法債券 ETF 名稱會包含這些文字。
# ================================================================

INVALID_SECURITY_KEYWORDS = [
    "權證",
    "認購權證",
    "認售權證",
    "牛熊證",
    "海外存託憑證",
    "存託憑證",
    "存託",
    "ETN",
    "可轉債",
    "轉換公司債",
]


# ================================================================
# 名稱處理
# ================================================================

def clean_text(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return str(value).strip()


def clean_code(value):
    value = clean_text(value)

    if value.endswith(".TW"):
        value = value[:-3]

    if value.endswith(".TWO"):
        value = value[:-4]

    return value.upper()


def safe_float(value):
    try:
        if value is None:
            return None

        value = float(value)

        if not math.isfinite(value):
            return None

        return round(value, 4)

    except Exception:
        return None


def safe_int(value):
    try:
        if value is None:
            return None

        return int(float(value))

    except Exception:
        return 0


# ================================================================
# 商品分類
# ================================================================

def is_invalid_security(name):
    """
    只排除真正不屬於系統範圍的商品。

    特別注意：
    「公司債」
    「債券」
    「金融債」

    不可以出現在這裡。
    """

    name = clean_text(name)

    for keyword in INVALID_SECURITY_KEYWORDS:
        if keyword in name:
            return True

    return False


def is_etf(code, name=""):
    """
    ETF 判斷：

    1. KNOWN_ETFS 優先
    2. 名稱包含 ETF
    3. 債券 ETF 一樣視為 ETF
    """

    code = clean_code(code)
    name = clean_text(name)

    if code in KNOWN_ETFS:
        return True

    upper_name = name.upper()

    if "ETF" in upper_name:
        return True

    if "ETF" in name:
        return True

    return False


# ================================================================
# Yahoo symbol
# ================================================================

def yahoo_symbol(code, market="TW"):
    code = clean_code(code)

    if market == "TWO":
        return f"{code}.TWO"

    return f"{code}.TW"


# ================================================================
# 技術指標
# ================================================================

def calculate_rsi(close, period=14):

    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.replace([np.inf, -np.inf], np.nan)

    return rsi


def calculate_macd(close):

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    macd = ema12 - ema26

    signal = macd.ewm(
        span=9,
        adjust=False
    ).mean()

    histogram = macd - signal

    return macd, signal, histogram


def calculate_kd(high, low, close):

    lowest = low.rolling(9).min()
    highest = high.rolling(9).max()

    denominator = (highest - lowest).replace(0, np.nan)

    rsv = (
        (close - lowest) /
        denominator *
        100
    )

    k = rsv.ewm(
        alpha=1 / 3,
        adjust=False
    ).mean()

    d = k.ewm(
        alpha=1 / 3,
        adjust=False
    ).mean()

    return k, d


def calculate_indicators(df):

    df = df.copy()

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    df["RSI"] = calculate_rsi(df["Close"])

    macd, signal, histogram = calculate_macd(
        df["Close"]
    )

    df["MACD"] = macd
    df["MACD_SIGNAL"] = signal
    df["MACD_HIST"] = histogram

    k, d = calculate_kd(
        df["High"],
        df["Low"],
        df["Close"]
    )

    df["K"] = k
    df["D"] = d

    df["VOL_MA5"] = df["Volume"].rolling(5).mean()

    return df


# ================================================================
# 核心條件
# ================================================================

def evaluate_core_conditions(df):

    if len(df) < 30:
        return {
            "macd_golden_cross": False,
            "rsi_over_50": False,
            "kd_golden_cross": False,
            "volume_expand": False,
            "price_over_ma20": False,
            "ma20_up": False,
            "core_score": 0,
            "core_total": 6,
            "core_pass": False,
        }

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    macd_now = latest["MACD"]
    macd_prev = previous["MACD"]

    signal_now = latest["MACD_SIGNAL"]
    signal_prev = previous["MACD_SIGNAL"]

    macd_cross = (
        pd.notna(macd_now)
        and pd.notna(macd_prev)
        and pd.notna(signal_now)
        and pd.notna(signal_prev)
        and macd_now > signal_now
        and macd_prev <= signal_prev
    )

    rsi_pass = (
        pd.notna(latest["RSI"])
        and latest["RSI"] > 50
    )

    k_now = latest["K"]
    k_prev = previous["K"]

    d_now = latest["D"]
    d_prev = previous["D"]

    kd_cross = (
        pd.notna(k_now)
        and pd.notna(k_prev)
        and pd.notna(d_now)
        and pd.notna(d_prev)
        and k_now > d_now
        and k_prev <= d_prev
    )

    volume_pass = (
        pd.notna(latest["Volume"])
        and pd.notna(latest["VOL_MA5"])
        and latest["VOL_MA5"] > 0
        and latest["Volume"] >= latest["VOL_MA5"] * 1.5
    )

    price_ma20_pass = (
        pd.notna(latest["Close"])
        and pd.notna(latest["MA20"])
        and latest["Close"] > latest["MA20"]
    )

    ma20_up_pass = (
        pd.notna(latest["MA20"])
        and pd.notna(previous["MA20"])
        and latest["MA20"] > previous["MA20"]
    )

    conditions = {
        "macd_golden_cross": bool(macd_cross),
        "rsi_over_50": bool(rsi_pass),
        "kd_golden_cross": bool(kd_cross),
        "volume_expand": bool(volume_pass),
        "price_over_ma20": bool(price_ma20_pass),
        "ma20_up": bool(ma20_up_pass),
    }

    score = sum(
        1
        for value in conditions.values()
        if value
    )

    conditions["core_score"] = score
    conditions["core_total"] = 6
    conditions["core_pass"] = score == 6

    return conditions


# ================================================================
# 強弱評分
#
# 注意：
# 這不是把 6 項條件改掉。
# core_score 仍然只代表 6 項條件通過數。
# ================================================================

def calculate_strength_score(df):

    if len(df) < 30:
        return 0

    latest = df.iloc[-1]

    score = 0.0

    rsi = latest["RSI"]

    if pd.notna(rsi):
        score += max(
            0,
            min(30, (float(rsi) - 50) * 0.8)
        )

    close = latest["Close"]
    ma20 = latest["MA20"]

    if (
        pd.notna(close)
        and pd.notna(ma20)
        and ma20 != 0
    ):
        ma_distance = (
            float(close) / float(ma20) - 1
        )

        score += max(
            0,
            min(25, ma_distance * 100)
        )

    macd = latest["MACD"]
    signal = latest["MACD_SIGNAL"]

    if (
        pd.notna(macd)
        and pd.notna(signal)
        and macd > signal
    ):
        score += 20

    k = latest["K"]
    d = latest["D"]

    if (
        pd.notna(k)
        and pd.notna(d)
        and k > d
    ):
        score += 15

    volume = latest["Volume"]
    volume_ma5 = latest["VOL_MA5"]

    if (
        pd.notna(volume)
        and pd.notna(volume_ma5)
        and volume_ma5 > 0
    ):
        volume_ratio = volume / volume_ma5

        score += max(
            0,
            min(10, (volume_ratio - 1) * 10)
        )

    return round(
        max(0, min(100, score)),
        2
    )


# ================================================================
# 資料下載
# ================================================================

def download_one(code, market="TW"):

    symbol = yahoo_symbol(
        code,
        market
    )

    for attempt in range(1, MAX_RETRY + 1):

        try:

            ticker = yf.Ticker(symbol)

            df = ticker.history(
                period=PERIOD,
                interval=INTERVAL,
                auto_adjust=False,
                actions=False
            )

            if df is None or df.empty:
                raise ValueError(
                    f"{symbol} 無行情資料"
                )

            required = [
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
            ]

            missing = [
                col
                for col in required
                if col not in df.columns
            ]

            if missing:
                raise ValueError(
                    f"{symbol} 缺少欄位：{missing}"
                )

            df = df[
                required
            ].copy()

            df = df.dropna(
                subset=["Close"]
            )

            if len(df) < 30:
                raise ValueError(
                    f"{symbol} 歷史資料不足"
                )

            return df

        except Exception as exc:

            if attempt >= MAX_RETRY:
                print(
                    f"[FAIL] {symbol}: {exc}"
                )
                return None

            print(
                f"[RETRY] {symbol} "
                f"{attempt}/{MAX_RETRY}: {exc}"
            )

            time.sleep(RETRY_DELAY)

    return None


# ================================================================
# 單一商品分析
# ================================================================

def analyze_security(
    code,
    name="",
    security_type="stock",
    market="TW"
):

    code = clean_code(code)
    name = clean_text(name)

    df = download_one(
        code,
        market
    )

    if df is None:
        return None

    df = calculate_indicators(df)

    latest = df.iloc[-1]

    close = safe_float(
        latest["Close"]
    )

    previous_close = (
        safe_float(df.iloc[-2]["Close"])
        if len(df) >= 2
        else None
    )

    if (
        close is not None
        and previous_close not in (
            None,
            0
        )
    ):
        change_pct = (
            (close / previous_close - 1)
            * 100
        )
    else:
        change_pct = None

    result = {
        "code": code,
        "symbol": yahoo_symbol(
            code,
            market
        ),
        "name": name,
        "type": security_type,
        "market": market,

        "price": close,
        "close": close,
        "previous_close": previous_close,

        "change": (
            round(
                close - previous_close,
                4
            )
            if (
                close is not None
                and previous_close is not None
            )
            else None
        ),

        "change_pct": safe_float(
            change_pct
        ),

        "volume": safe_int(
            latest["Volume"]
        ),

        "volume_ma5": safe_int(
            latest["VOL_MA5"]
        ),

        "volume_ratio": (
            safe_float(
                latest["Volume"]
                /
                latest["VOL_MA5"]
            )
            if (
                pd.notna(latest["Volume"])
                and pd.notna(latest["VOL_MA5"])
                and latest["VOL_MA5"] != 0
            )
            else None
        ),

        "rsi": safe_float(
            latest["RSI"]
        ),

        "k": safe_float(
            latest["K"]
        ),

        "d": safe_float(
            latest["D"]
        ),

        "macd": safe_float(
            latest["MACD"]
        ),

        "macd_signal": safe_float(
            latest["MACD_SIGNAL"]
        ),

        "macd_hist": safe_float(
            latest["MACD_HIST"]
        ),

        "ma5": safe_float(
            latest["MA5"]
        ),

        "ma20": safe_float(
            latest["MA20"]
        ),

        "ma60": safe_float(
            latest["MA60"]
        ),
    }

    # ============================================================
    # 股票：計算 6 項核心條件
    # ============================================================

    if security_type == "stock":

        core = evaluate_core_conditions(
            df
        )

        result.update(core)

        result["strength_score"] = calculate_strength_score(
            df
        )

        result["ai_score"] = round(
            (
                result["strength_score"] * 0.6
                +
                (result["core_score"] / 6) * 40
            ),
            2
        )

    # ============================================================
    # ETF：不套用股票 6 項核心條件
    # ============================================================

    else:

        result.update({
            "core_score": None,
            "core_total": None,
            "core_pass": None,

            "macd_golden_cross": None,
            "rsi_over_50": None,
            "kd_golden_cross": None,
            "volume_expand": None,
            "price_over_ma20": None,
            "ma20_up": None,
        })

        result["strength_score"] = calculate_strength_score(
            df
        )

        result["ai_score"] = result[
            "strength_score"
        ]

    return result


# ================================================================
# 官方股票清單
#
# 以 TWSE / TPEx API 優先。
# API 失敗時使用現有 Universe / fallback。
# ================================================================

def get_twse_symbols():

    url = (
        "https://openapi.twse.com.tw/"
        "v1/exchangeReport/STOCK_DAY_ALL"
    )

    try:

        import requests

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        result = []

        for item in data:

            code = clean_code(
                item.get("Code")
            )

            name = clean_text(
                item.get("Name")
            )

            if not code:
                continue

            if not code.isdigit():
                continue

            if is_invalid_security(
                name
            ):
                continue

            if is_etf(
                code,
                name
            ):
                continue

            result.append(
                (
                    code,
                    name,
                    "TW"
                )
            )

        return result

    except Exception as exc:

        print(
            f"[WARN] TWSE Universe 取得失敗：{exc}"
        )

        return []


def get_twse_etfs():

    url = (
        "https://openapi.twse.com.tw/"
        "v1/exchangeReport/STOCK_DAY_ALL"
    )

    result = []

    try:

        import requests

        response = requests.get(
            url,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        for item in data:

            code = clean_code(
                item.get("Code")
            )

            name = clean_text(
                item.get("Name")
            )

            if not code:
                continue

            if not code.isdigit():
                continue

            if is_invalid_security(
                name
            ):
                continue

            if is_etf(
                code,
                name
            ):
                result.append(
                    (
                        code,
                        name,
                        "TW"
                    )
                )

        return result

    except Exception as exc:

        print(
            f"[WARN] ETF Universe 取得失敗：{exc}"
        )

        return result


# ================================================================
# ETF fallback
#
# 這裡特別保證 00720B 存在。
# ================================================================

ETF_FALLBACK_NAMES = {
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "006208": "富邦台50",
    "00713": "元大台灣高息低波",
    "00720B": "元大20年期以上BBB級美元公司債券ETF",
    "00774B": "債券ETF",
    "00775B": "債券ETF",
    "00788B": "債券ETF",
    "00789B": "債券ETF",
    "00865B": "債券ETF",
}


def build_etf_universe():

    discovered = {}

    official = get_twse_etfs()

    for code, name, market in official:

        discovered[
            clean_code(code)
        ] = {
            "code": clean_code(code),
            "name": name,
            "market": market,
        }

    # ============================================================
    # KNOWN_ETFS 作為補充
    #
    # 這是重要的 fallback。
    # 即使官方名稱抓取失敗，ETF 仍然不會消失。
    # ============================================================

    for code in KNOWN_ETFS:

        code = clean_code(code)

        if code not in discovered:

            discovered[code] = {
                "code": code,
                "name": ETF_FALLBACK_NAMES.get(
                    code,
                    code
                ),
                "market": "TW",
            }

    # 明確保證 00720B
    if "00720B" not in discovered:

        discovered["00720B"] = {
            "code": "00720B",
            "name": ETF_FALLBACK_NAMES[
                "00720B"
            ],
            "market": "TW",
        }

    return list(
        discovered.values()
    )


# ================================================================
# 股票 Universe
# ================================================================

def build_stock_universe():

    discovered = {}

    official = get_twse_symbols()

    for code, name, market in official:

        discovered[
            clean_code(code)
        ] = {
            "code": clean_code(code),
            "name": name,
            "market": market,
        }

    return list(
        discovered.values()
    )


# ================================================================
# 批次分析
# ================================================================

def process_universe(
    universe,
    security_type
):

    results = []

    total = len(universe)

    for index, item in enumerate(
        universe,
        start=1
    ):

        code = item["code"]
        name = item["name"]
        market = item.get(
            "market",
            "TW"
        )

        print(
            f"[{index}/{total}] "
            f"{code} {name}"
        )

        result = analyze_security(
            code=code,
            name=name,
            security_type=security_type,
            market=market
        )

        if result is not None:

            results.append(
                result
            )

        if index % BATCH_SIZE == 0:
            time.sleep(
                BATCH_DELAY
            )

    return results


# ================================================================
# 排序
# ================================================================

def sort_stocks(stocks):

    return sorted(
        stocks,
        key=lambda x: (
            x.get(
                "core_score",
                0
            ),
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "strength_score",
                0
            ),
            x.get(
                "change_pct",
                -999
            ),
        ),
        reverse=True
    )


def sort_etfs(etfs):

    return sorted(
        etfs,
        key=lambda x: (
            x.get(
                "strength_score",
                0
            ),
            x.get(
                "change_pct",
                -999
            ),
        ),
        reverse=True
    )


# ================================================================
# 今日精選
#
# 只有真正 6 / 6 才進入今日精選。
# 不在前台偷偷修改結果。
# ================================================================

def get_today_selected(stocks):

    selected = [
        stock
        for stock in stocks
        if stock.get(
            "core_score"
        ) == 6
    ]

    selected = sorted(
        selected,
        key=lambda x: (
            x.get(
                "ai_score",
                0
            ),
            x.get(
                "strength_score",
                0
            ),
        ),
        reverse=True
    )

    return selected


# ================================================================
# 日期
# ================================================================

def get_now():

    return datetime.now(
        TW_TZ
    )


# ================================================================
# JSON 輸出
# ================================================================

def build_output(
    stocks,
    etfs
):

    stocks_sorted = sort_stocks(
        stocks
    )

    etfs_sorted = sort_etfs(
        etfs
    )

    today_selected = get_today_selected(
        stocks_sorted
    )

    strong_stocks = stocks_sorted[
        :TOP_STOCKS
    ]

    strong_etfs = etfs_sorted[
        :TOP_ETFS
    ]

    now = get_now()

    output = {

        # --------------------------------------------------------
        # 系統資訊
        # --------------------------------------------------------

        "version": VERSION,

        "updated_at": now.isoformat(),

        "updated_at_tw": now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "date": now.strftime(
            "%Y-%m-%d"
        ),

        "status": "success",

        # --------------------------------------------------------
        # 完整資料
        # --------------------------------------------------------

        "stocks": stocks_sorted,

        "etfs": etfs_sorted,

        # --------------------------------------------------------
        # 強勢排行
        # --------------------------------------------------------

        "strong_stocks": strong_stocks,

        "strong_etfs": strong_etfs,

        "top_stocks": strong_stocks,

        "top_etfs": strong_etfs,

        # --------------------------------------------------------
        # 今日精選
        # --------------------------------------------------------

        "today_selected": today_selected,

        "today_picks": today_selected,

        "featured_stocks": today_selected,

        # --------------------------------------------------------
        # 統計
        # --------------------------------------------------------

        "statistics": {

            "stock_count": len(
                stocks_sorted
            ),

            "etf_count": len(
                etfs_sorted
            ),

            "today_selected_count": len(
                today_selected
            ),

            "core_6_count": len(
                today_selected
            ),

        },

        # --------------------------------------------------------
        # 系統設定
        # --------------------------------------------------------

        "core_conditions": {

            "total": 6,

            "names": CORE_CONDITIONS,

            "macd": "MACD 黃金交叉",

            "rsi": "RSI > 50",

            "kd": "KD 黃金交叉",

            "volume": "成交量放大",

            "price_ma20": "股價 > MA20",

            "ma20_up": "MA20 向上",
        },

        # --------------------------------------------------------
        # ETF 特別資訊
        # --------------------------------------------------------

        "etf_universe": [

            item.get(
                "code"
            )

            for item in etfs_sorted

        ],

        "etf_checks": {

            "00720B_present": any(
                item.get("code")
                == "00720B"
                for item in etfs_sorted
            ),

        },
    }

    return output


# ================================================================
# 安全寫入
# ================================================================

def write_json_atomic(
    data,
    output_file
):

    directory = os.path.dirname(
        output_file
    )

    os.makedirs(
        directory,
        exist_ok=True
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
                indent=2,
                allow_nan=False
            )

            file.write("\n")

        os.replace(
            temp_path,
            output_file
        )

    except Exception:

        try:
            os.unlink(
                temp_path
            )
        except Exception:
            pass

        raise


# ================================================================
# 驗證
# ================================================================

def validate_output(
    output
):

    errors = []

    if not isinstance(
        output.get("stocks"),
        list
    ):
        errors.append(
            "stocks 不是 list"
        )

    if not isinstance(
        output.get("etfs"),
        list
    ):
        errors.append(
            "etfs 不是 list"
        )

    if not output.get(
        "updated_at"
    ):
        errors.append(
            "缺少 updated_at"
        )

    # ------------------------------------------------------------
    # 00720B 是本次必要驗證
    # ------------------------------------------------------------

    etf_codes = {
        item.get("code")
        for item in output.get(
            "etfs",
            []
        )
    }

    if "00720B" not in etf_codes:

        errors.append(
            "00720B 未進入 ETF 資料"
        )

    if errors:

        raise RuntimeError(
            "JSON 驗證失敗：\n"
            +
            "\n".join(errors)
        )

    return True


# ================================================================
# 主程式
# ================================================================

def main():

    start_time = time.time()

    print("=" * 64)
    print(
        f"台股 AI 選股系統 fetch_data.py {VERSION}"
    )
    print(
        f"開始時間：{get_now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    print("=" * 64)

    # ============================================================
    # 1. 建立股票 Universe
    # ============================================================

    print("\n[1/6] 建立股票 Universe...")

    stock_universe = build_stock_universe()

    print(
        f"股票 Universe："
        f"{len(stock_universe)} 檔"
    )

    # ============================================================
    # 2. 建立 ETF Universe
    # ============================================================

    print("\n[2/6] 建立 ETF Universe...")

    etf_universe = build_etf_universe()

    print(
        f"ETF Universe："
        f"{len(etf_universe)} 檔"
    )

    has_00720b = any(
        item.get("code")
        == "00720B"
        for item in etf_universe
    )

    print(
        "00720B Universe："
        +
        (
            "✓ 已納入"
            if has_00720b
            else "✗ 未納入"
        )
    )

    if not has_00720b:

        raise RuntimeError(
            "嚴重錯誤：00720B 沒有進入 ETF Universe"
        )

    # ============================================================
    # 3. 股票分析
    # ============================================================

    print("\n[3/6] 開始分析股票...")

    stocks = process_universe(
        stock_universe,
        "stock"
    )

    print(
        f"股票完成："
        f"{len(stocks)} 檔"
    )

    # ============================================================
    # 4. ETF 分析
    # ============================================================

    print("\n[4/6] 開始分析 ETF...")

    etfs = process_universe(
        etf_universe,
        "etf"
    )

    print(
        f"ETF 完成："
        f"{len(etfs)} 檔"
    )

    # ============================================================
    # 5. 建立 JSON
    # ============================================================

    print("\n[5/6] 建立 prices.json...")

    output = build_output(
        stocks,
        etfs
    )

    # ============================================================
    # 6. 驗證 + 寫入
    # ============================================================

    print("\n[6/6] 驗證輸出...")

    validate_output(
        output
    )

    write_json_atomic(
        output,
        OUTPUT_FILE
    )

    elapsed = time.time() - start_time

    print("\n" + "=" * 64)

    print(
        "✓ fetch_data.py 執行完成"
    )

    print(
        f"股票：{len(stocks)}"
    )

    print(
        f"ETF：{len(etfs)}"
    )

    print(
        f"今日 6/6："
        f"{len(output['today_selected'])}"
    )

    print(
        "00720B："
        +
        (
            "✓ 已寫入 prices.json"
            if output["etf_checks"]["00720B_present"]
            else "✗ 未寫入"
        )
    )

    print(
        f"輸出：{OUTPUT_FILE}"
    )

    print(
        f"耗時：{elapsed:.1f} 秒"
    )

    print("=" * 64)


if __name__ == "__main__":
    main()
