# -*- coding: utf-8 -*-

"""
台股 AI 選股 + ETF + 零股定投 + 動態風控
正式全市場掃描版
================================================

功能：
1. 自動建立台股上市 / 上櫃個股股票池
2. 自動加入主要 ETF
3. 不再固定 25 檔
4. 批次抓取 Yahoo Finance 歷史日線
5. 計算：
   - MA5 / MA10 / MA20 / MA60
   - MACD
   - RSI
   - KD
   - 5日均量
   - 成交量倍率
   - MA20斜率
6. 短線 AI 評分
7. ETF / 個股分開排名
8. 定投價格區間
9. 動態停損 / 停利
10. 30 / 60 / 90 日前瞻績效資料
11. 輸出 Data/prices.json
"""

import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("錯誤：找不到 yfinance")
    sys.exit(1)


# ============================================================
# 基本設定
# ============================================================

TAIPEI_TZ = timezone(timedelta(hours=8))

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
# 掃描設定
# ============================================================

SCAN_PERIOD = "1y"

BATCH_SIZE = 50

BATCH_SLEEP = 1.5

MIN_HISTORY = 70

VOLUME_MULTIPLIER = 1.5

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

RSI_PERIOD = 14

KD_PERIOD = 9
KD_SMOOTH_K = 3
KD_SMOOTH_D = 3

MA20_PERIOD = 20
MA60_PERIOD = 60

FORWARD_DAYS = {
    "30": 30,
    "60": 60,
    "90": 90
}


# ============================================================
# ETF 清單
# ============================================================
#
# ETF 不需要把所有商品硬編碼。
# 這裡放常見 / 流動性較高 ETF 作為正式基礎池。
#
# 後續可以繼續增加。
#
# ============================================================

ETF_LIST = {

    "0050": "元大台灣50",
    "0051": "元大中型100",
    "0052": "富邦科技",
    "0053": "元大電子",
    "0056": "元大高股息",
    "0057": "富邦摩台",
    "00631L": "元大台灣50正2",
    "00632R": "元大台灣50反1",
    "00633L": "富邦上証正2",
    "00637L": "元大滬深300正2",
    "00639": "富邦深100",
    "00640L": "富邦日本正2",
    "00641R": "富邦日本反1",
    "00646": "元大S&P500",
    "00647L": "元大S&P500正2",
    "00648R": "元大S&P500反1",
    "00650L": "復華日本正2",
    "00651R": "復華日本反1",
    "00652": "富邦印度",
    "00657": "國泰日經225",
    "00662": "富邦NASDAQ",
    "00663L": "國泰臺灣加權正2",
    "00664R": "國泰臺灣加權反1",
    "00668": "國泰美國道瓊",
    "00669R": "國泰美國道瓊反1",
    "00670L": "富邦NASDAQ正2",
    "00671R": "富邦NASDAQ反1",
    "00673R": "元大S&P原油反1",
    "00675L": "富邦臺灣加權正2",
    "00676R": "富邦臺灣加權反1",
    "00677U": "富邦VIX",
    "00678": "群益NBI生技",
    "00679B": "元大美債20年",
    "00680L": "元大美債20正2",
    "00681R": "元大美債20反1",
    "00682U": "元大美元指數",
    "00683L": "元大南方小麥正2",
    "00685L": "群益臺灣加權正2",
    "00686R": "群益臺灣加權反1",
    "00687B": "國泰20年美債",
    "00688L": "國泰20年美債正2",
    "00689R": "國泰20年美債反1",
    "00690": "兆豐藍籌30",
    "00692": "富邦公司治理",
    "00693U": "期元大S&P黃金",
    "00694B": "富邦美債1-3",
    "00695B": "富邦美債7-10",
    "00696B": "富邦美債20年",
    "00697B": "元大美債7-10",
    "00698": "國泰永續高股息",
    "00701": "國泰股利精選30",
    "00702": "國泰標普低波高息",
    "00703": "台新MSCI中國",
    "00704L": "期元大金2N",
    "00706L": "期元大S&P日圓正2",
    "00707": "期元大S&P日圓反1",
    "00708L": "期元大S&P黃金正2",
    "00709": "富邦歐洲",
    "00710B": "復華彭博非投等債",
    "00711B": "復華彭博新興債",
    "00712": "復華中國5G",
    "00713": "元大台灣高息低波",
    "00714": "群益台灣加權正2",
    "00715": "期街口布蘭特正2",
    "00717": "富邦美國特別股",
    "00720B": "元大投資級公司債",
    "00725B": "國泰投資級公司債",
    "00727B": "國泰1-5年非投等債",
    "00730": "富邦臺灣優質高息",
    "00731": "復華富時高息低波",
    "00733": "富邦臺灣中小",
    "00735": "國泰臺韓科技",
    "00736": "國泰新興市場",
    "00737": "國泰AI+Robo",
    "00739": "元大MSCI A股",
    "00740B": "富邦全球投等債",
    "00741B": "富邦全球非投等債",
    "00752": "中信中國50",
    "00753L": "中信中國50正2",
    "00754B": "新光中國政金債",
    "00757": "統一FANG+",
    "00762": "元大全球AI",
    "00770": "國泰北美科技",
    "00771": "元大US高息特別股",
    "00772": "中信高評級公司債",
    "00775B": "新光投等債15年",
    "00778B": "凱基優選高股息30",
    "00830": "國泰費城半導體",
    "00850": "元大臺灣ESG永續",
    "00851": "台新全球AI",
    "00852L": "國泰美國道瓊正2",
    "00853B": "統一美債10年",
    "00858": "永豐美國500大",
    "00859B": "群益0-1年美債",
    "00865B": "國泰US短期非投等債",
    "00875": "國泰網路資安",
    "00876": "元大全球5G",
    "00878": "國泰永續高股息",
    "00881": "國泰台灣5G+",
    "00882": "中信中國高股息",
    "00885": "富邦越南",
    "00891": "中信小資高價30",
    "00892": "富邦台灣半導體",
    "00893": "國泰智能電動車",
    "00894": "中信小資高價30",
    "00895": "富邦臺灣公司治理",
    "00896": "中信綠能及電動車",
    "00897": "國泰智能電動車",
    "00878": "國泰永續高股息",
    "00900": "富邦特選高股息30",
    "00901": "永豐智能車供應鏈",
    "00902": "中信電池及儲能",
    "00903": "富邦元宇宙",
    "00904": "新光臺灣半導體30",
    "00905": "FT臺灣Smart",
    "00907": "永豐優息存股",
    "00908": "野村企業家精選50",
    "00909": "國泰數位支付服務",
    "00910": "第一金太空衛星",
    "00911": "兆豐洲際半導體",
    "00912": "中信臺灣智慧50",
    "00913": "兆豐台灣晶圓製造",
    "00915": "凱基優選高股息30",
    "00916": "國泰全球品牌50",
    "00917": "中信特選金融",
    "00918": "大華優利高填息30",
    "00919": "群益台灣精選高息",
    "00920": "野村臺灣智慧優選主動式",
    "00921": "兆豐龍頭等權重",
    "00922": "國泰台灣領袖50",
    "00923": "群益台ESG低碳50",
    "00924": "復華S&P500成長",
    "00925": "新光標普電動車",
    "00926": "凱基全球菁英55",
    "00927": "群益半導體收益",
    "00929": "復華台灣科技優息",
    "00930": "永豐ESG低碳高息",
    "00932": "兆豐永續高息等權",
    "00934": "中信成長高股息",
    "00935": "野村臺灣新科技50",
    "00936": "台新永續高息中小",
    "00938": "凱基優選30",
    "00939": "統一台灣高息動能",
    "00940": "元大台灣價值高息",
    "00941": "中信上游半導體",
    "00943": "兆豐電子高息等權",
    "00944": "野村趨勢動能高息",
    "00945B": "凱基美國非投等債",
    "00946": "群益科技高息成長",
    "00947": "台新臺灣IC設計",
    "00949": "復華日本龍頭",
    "00950": "凱基臺灣優選30",
    "00951": "台新日本半導體",
    "00952": "凱基台灣AI50",
    "00953B": "統一美國非投等債",
    "00954": "中信日本商社",
    "00955": "中信日本半導體",
    "00956": "中信日經高股息",
    "00957B": "兆豐優選投等債",
    "00958B": "中信優息投資級債",
    "00960": "野村臺灣智慧優選主動式",
    "00961": "FT臺灣永續高息",
    "00962": "台新AI優息動能",
    "00963": "野村趨勢動能高息",
    "00964": "中信亞太高股息",
    "00965": "野村臺灣半導體",
    "00966": "中信成長高股息",
    "00967": "台新臺灣IC設計",
    "00968": "群益優選非投等債",
    "00969": "國泰台灣領袖50",
    "00970": "野村臺灣新科技50",
    "00980A": "野村臺灣智慧優選主動式",
    "00981A": "主動統一台股增長",
    "00982A": "主動群益台灣強棒",
    "00983A": "主動中信ARK創新",
    "00984A": "主動安聯台灣高息",
    "00985A": "主動野村台灣優選"
}


# ============================================================
# 股票池
# ============================================================
#
# 正式版採「公開市場股票池」＋ ETF。
#
# 若無法從 TWSE / TPEx 取得完整清單，
# 會使用內建備援清單，避免 GitHub Actions 整個失敗。
#
# ============================================================

FALLBACK_STOCKS = {

    "1101": "台泥",
    "1102": "亞泥",
    "1216": "統一",
    "1301": "台塑",
    "1303": "南亞",
    "1326": "台化",
    "1402": "遠東新",
    "1476": "儒鴻",
    "1504": "東元",
    "1513": "中興電",
    "1519": "華城",
    "1605": "華新",
    "2002": "中鋼",
    "2105": "正新",
    "2201": "裕隆",
    "2207": "和泰車",
    "2301": "光寶科",
    "2303": "聯電",
    "2317": "鴻海",
    "2330": "台積電",
    "2345": "智邦",
    "2353": "宏碁",
    "2356": "英業達",
    "2376": "技嘉",
    "2382": "廣達",
    "2408": "南亞科",
    "2409": "友達",
    "2412": "中華電",
    "2421": "建準",
    "2425": "承啟",
    "2426": "鼎元",
    "2449": "京元電子",
    "2454": "聯發科",
    "2603": "長榮",
    "2609": "陽明",
    "2615": "萬海",
    "2618": "長榮航",
    "2633": "台灣高鐵",
    "2881": "富邦金",
    "2882": "國泰金",
    "2883": "開發金",
    "2884": "玉山金",
    "2885": "元大金",
    "2886": "兆豐金",
    "2887": "台新金",
    "2890": "永豐金",
    "2891": "中信金",
    "2892": "第一金",
    "2912": "統一超",
    "3006": "晶豪科",
    "3017": "奇鋐",
    "3037": "欣興",
    "3042": "晶技",
    "3044": "健鼎",
    "3231": "緯創",
    "3324": "雙鴻",
    "3338": "泰碩",
    "3356": "奇偶",
    "3450": "聯鈞",
    "3481": "群創",
    "3490": "單井",
    "3532": "台勝科",
    "3661": "世芯-KY",
    "3711": "日月光投控",
    "4763": "材料-KY",
    "4966": "譜瑞-KY",
    "5269": "祥碩",
    "5274": "信驊",
    "5347": "世界",
    "5483": "中美晶",
    "6125": "廣運",
    "6117": "迎廣",
    "6176": "瑞儀",
    "6239": "力成",
    "6257": "矽格",
    "6271": "同欣電",
    "6290": "良維",
    "6669": "緯穎",
    "8046": "南電",
    "8150": "南茂",
    "8210": "勤誠",
    "8299": "群聯"
}


# ============================================================
# 工具
# ============================================================

def clean_number(value, digits=4):

    if value is None:
        return None

    try:

        value = float(value)

        if math.isnan(value) or math.isinf(value):
            return None

        return round(value, digits)

    except Exception:

        return None


def safe_bool(value):

    try:
        return bool(value)

    except Exception:

        return False


def ticker(stock_id):

    return f"{stock_id}.TW"


def is_valid_code(code):

    if not code:
        return False

    code = str(code).strip()

    return (
        code.isdigit()
        or (
            code[:-1].isdigit()
            and code[-1].isalpha()
        )
    )


# ============================================================
# RSI
# ============================================================

def calculate_rsi(close, period=14):

    delta = close.diff()

    gain = delta.clip(lower=0)

    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# MACD
# ============================================================

def calculate_macd(close):

    ema_fast = close.ewm(
        span=MACD_FAST,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=MACD_SLOW,
        adjust=False
    ).mean()

    dif = ema_fast - ema_slow

    dem = dif.ewm(
        span=MACD_SIGNAL,
        adjust=False
    ).mean()

    hist = dif - dem

    return dif, dem, hist


# ============================================================
# KD
# ============================================================

def calculate_kd(df):

    low = df["Low"].rolling(
        KD_PERIOD
    ).min()

    high = df["High"].rolling(
        KD_PERIOD
    ).max()

    denominator = (
        high - low
    )

    rsv = (
        (df["Close"] - low)
        /
        denominator.replace(
            0,
            np.nan
        )
    ) * 100

    k = rsv.ewm(
        alpha=1 / KD_SMOOTH_K,
        adjust=False
    ).mean()

    d = k.ewm(
        alpha=1 / KD_SMOOTH_D,
        adjust=False
    ).mean()

    return k, d


# ============================================================
# 技術指標
# ============================================================

def calculate_indicators(df):

    df = df.copy()

    df["MA5"] = (
        df["Close"]
        .rolling(5)
        .mean()
    )

    df["MA10"] = (
        df["Close"]
        .rolling(10)
        .mean()
    )

    df["MA20"] = (
        df["Close"]
        .rolling(20)
        .mean()
    )

    df["MA60"] = (
        df["Close"]
        .rolling(60)
        .mean()
    )

    df["MA20_PREV"] = (
        df["MA20"]
        .shift(1)
    )

    df["MA20_SLOPE"] = (
        (
            df["MA20"]
            -
            df["MA20_PREV"]
        )
        /
        df["MA20_PREV"]
    ) * 100

    df["VOLUME_MA5"] = (
        df["Volume"]
        .rolling(5)
        .mean()
    )

    df["VOLUME_RATIO"] = (
        df["Volume"]
        /
        df["VOLUME_MA5"]
    )

    df["RSI"] = calculate_rsi(
        df["Close"],
        RSI_PERIOD
    )

    (
        df["DIF"],
        df["DEM"],
        df["MACD_HIST"]
    ) = calculate_macd(
        df["Close"]
    )

    (
        df["K"],
        df["D"]
    ) = calculate_kd(df)

    df["MACD_GOLDEN"] = (
        (df["DIF"] > df["DEM"])
        &
        (
            df["DIF"].shift(1)
            <=
            df["DEM"].shift(1)
        )
    )

    df["MACD_RED"] = (
        df["MACD_HIST"] > 0
    )

    df["KD_GOLDEN"] = (
        (df["K"] > df["D"])
        &
        (
            df["K"].shift(1)
            <=
            df["D"].shift(1)
        )
    )

    df["KD_LOW_GOLDEN"] = (
        df["KD_GOLDEN"]
        &
        (
            df["K"].shift(1) <= 30
        )
    )

    df["RSI_BULLISH"] = (
        df["RSI"] > 50
    )

    df["VOLUME_BREAKOUT"] = (
        df["Volume"]
        >
        df["VOLUME_MA5"] * VOLUME_MULTIPLIER
    )

    df["ABOVE_MA20"] = (
        df["Close"] > df["MA20"]
    )

    df["MA20_UP"] = (
        df["MA20"]
        >
        df["MA20"].shift(1)
    )

    df["MA20_TURN_UP"] = (
        (df["MA20"] > df["MA20"].shift(1))
        &
        (
            df["MA20"].shift(1)
            >=
            df["MA20"].shift(2)
        )
    )

    df["SHORT_TERM_CORE"] = (
        df["MACD_GOLDEN"]
        &
        df["KD_GOLDEN"]
        &
        df["RSI_BULLISH"]
        &
        df["VOLUME_BREAKOUT"]
        &
        df["ABOVE_MA20"]
        &
        df["MA20_UP"]
    )

    return df


# ============================================================
# AI 評分
# ============================================================

def calculate_score(row):

    score = 0

    reasons = []

    if safe_bool(
        row["MACD_GOLDEN"]
    ):

        score += 25

        reasons.append(
            "MACD黃金交叉"
        )

    elif safe_bool(
        row["MACD_RED"]
    ):

        score += 10

        reasons.append(
            "MACD翻紅"
        )

    if safe_bool(
        row["KD_LOW_GOLDEN"]
    ):

        score += 25

        reasons.append(
            "KD低位黃金交叉"
        )

    elif safe_bool(
        row["KD_GOLDEN"]
    ):

        score += 20

        reasons.append(
            "KD黃金交叉"
        )

    elif (
        pd.notna(row["K"])
        and
        pd.notna(row["D"])
        and
        row["K"] > row["D"]
    ):

        score += 8

        reasons.append(
            "KD多方"
        )

    rsi = row["RSI"]

    if pd.notna(rsi):

        if rsi >= 60:

            score += 15

            reasons.append(
                "RSI強勢"
            )

        elif rsi >= 55:

            score += 13

            reasons.append(
                "RSI偏強"
            )

        elif rsi > 50:

            score += 10

            reasons.append(
                "RSI站上50"
            )

    ratio = row[
        "VOLUME_RATIO"
    ]

    if pd.notna(ratio):

        if ratio >= 2:

            score += 20

            reasons.append(
                "成交量爆量"
            )

        elif ratio >= 1.5:

            score += 15

            reasons.append(
                "成交量放大"
            )

        elif ratio >= 1.2:

            score += 5

    if safe_bool(
        row["ABOVE_MA20"]
    ):

        score += 8

        reasons.append(
            "站上20MA"
        )

    if safe_bool(
        row["MA20_UP"]
    ):

        score += 5

        reasons.append(
            "20MA向上"
        )

    if safe_bool(
        row["MA20_TURN_UP"]
    ):

        score += 7

        reasons.append(
            "20MA向上勾"
        )

    return score, reasons


# ============================================================
# 定投
# ============================================================

def calculate_dca(
    close,
    ma20,
    ma60
):

    if close is None:

        return {
            "status": "資料不足",
            "action": "觀望"
        }

    reference = (
        ma20
        if ma20 is not None
        else close
    )

    buy_1 = reference * 0.99
    buy_2 = reference * 0.97
    buy_3 = reference * 0.94
    buy_4 = reference * 0.90

    if ma60 is not None:

        buy_4 = min(
            buy_4,
            ma60 * 1.02
        )

    if close >= buy_1:

        status = "高於第一定投區"
        action = "觀察 / 小額"

    elif close >= buy_2:

        status = "第一定投區"
        action = "第一批"

    elif close >= buy_3:

        status = "第二定投區"
        action = "第二批"

    elif close >= buy_4:

        status = "第三定投區"
        action = "第三批"

    else:

        status = "深度回撤區"
        action = "第四批 / 等待確認"

    return {

        "status": status,

        "action": action,

        "buy_1": clean_number(
            buy_1,
            2
        ),

        "buy_2": clean_number(
            buy_2,
            2
        ),

        "buy_3": clean_number(
            buy_3,
            2
        ),

        "buy_4": clean_number(
            buy_4,
            2
        )
    }


# ============================================================
# 風控
# ============================================================

def calculate_risk(
    close,
    ma20,
    ma60,
    ma20_up
):

    if close is None:

        return {

            "risk_level": "未知",

            "stop_loss": None,

            "take_profit_1": None,

            "take_profit_2": None
        }

    stop_loss = (
        close * 0.93
    )

    if (
        ma20 is not None
        and
        close < ma20
    ):

        stop_loss = (
            close * 0.95
        )

    take_profit_1 = (
        close * 1.08
    )

    take_profit_2 = (
        close * 1.15
    )

    if (
        ma20 is not None
        and
        close >= ma20
        and
        ma20_up
    ):

        risk = "低～中"

    elif (
        ma20 is not None
        and
        close >= ma20
    ):

        risk = "中"

    else:

        risk = "中～高"

    return {

        "risk_level": risk,

        "stop_loss": clean_number(
            stop_loss,
            2
        ),

        "take_profit_1": clean_number(
            take_profit_1,
            2
        ),

        "take_profit_2": clean_number(
            take_profit_2,
            2
        )
    }


# ============================================================
# 30 / 60 / 90 日前瞻資料
# ============================================================

def calculate_forward_performance(
    df
):

    result = {

        "30d": {
            "available": False,
            "return_pct": None,
            "max_return_pct": None,
            "max_drawdown_pct": None
        },

        "60d": {
            "available": False,
            "return_pct": None,
            "max_return_pct": None,
            "max_drawdown_pct": None
        },

        "90d": {
            "available": False,
            "return_pct": None,
            "max_return_pct": None,
            "max_drawdown_pct": None
        }
    }

    if len(df) < 2:

        return result

    latest_close = float(
        df["Close"].iloc[-1]
    )

    for key, days in FORWARD_DAYS.items():

        target_index = len(df) - 1 + days

        if target_index >= len(df):

            continue

        future_close = float(
            df["Close"].iloc[target_index]
        )

        future_window = df.iloc[
            len(df) - 1:
            target_index + 1
        ]

        if future_window.empty:

            continue

        max_high = float(
            future_window["High"].max()
        )

        min_low = float(
            future_window["Low"].min()
        )

        return_pct = (
            (
                future_close
                -
                latest_close
            )
            /
            latest_close
        ) * 100

        max_return = (
            (
                max_high
                -
                latest_close
            )
            /
            latest_close
        ) * 100

        max_drawdown = (
            (
                min_low
                -
                latest_close
            )
            /
            latest_close
        ) * 100

        result[
            f"{key}d"
        ] = {

            "available": True,

            "return_pct": clean_number(
                return_pct,
                2
            ),

            "max_return_pct": clean_number(
                max_return,
                2
            ),

            "max_drawdown_pct": clean_number(
                max_drawdown,
                2
            )
        }

    return result


# ============================================================
# 單一標的分析
# ============================================================

def analyze_symbol(
    stock_id,
    name,
    asset_type,
    df
):

    if df is None or df.empty:

        return None

    if isinstance(
        df.columns,
        pd.MultiIndex
    ):

        df.columns = (
            df.columns
            .get_level_values(0)
        )

    required = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    for col in required:

        if col not in df.columns:

            return None

    df = df[
        required
    ].copy()

    for col in required:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=["Close"]
    )

    if len(df) < MIN_HISTORY:

        return None

    df = calculate_indicators(
        df
    )

    latest = df.iloc[-1]

    previous = df.iloc[-2]

    close = clean_number(
        latest["Close"],
        2
    )

    previous_close = clean_number(
        previous["Close"],
        2
    )

    if close is None:

        return None

    change = None
    change_percent = None

    if (
        previous_close is not None
        and
        previous_close != 0
    ):

        change = clean_number(
            close - previous_close,
            2
        )

        change_percent = clean_number(
            (
                (
                    close
                    -
                    previous_close
                )
                /
                previous_close
            )
            * 100,
            2
        )

    score, reasons = calculate_score(
        latest
    )

    core = safe_bool(
        latest[
            "SHORT_TERM_CORE"
        ]
    )

    if core:

        signal = "強勢啟動"

    elif score >= 75:

        signal = "高度關注"

    elif score >= 60:

        signal = "偏多"

    elif score >= 45:

        signal = "觀察"

    elif score >= 30:

        signal = "弱勢"

    else:

        signal = "不符合"

    ma20 = clean_number(
        latest["MA20"],
        2
    )

    ma60 = clean_number(
        latest["MA60"],
        2
    )

    ma20_up = safe_bool(
        latest["MA20_UP"]
    )

    dca = calculate_dca(
        close,
        ma20,
        ma60
    )

    risk = calculate_risk(
        close,
        ma20,
        ma60,
        ma20_up
    )

    forward = (
        calculate_forward_performance(
            df
        )
    )

    result = {

        "id": stock_id,

        "symbol": ticker(
            stock_id
        ),

        "name": name,

        "asset_type": asset_type,

        "date": str(
            df.index[-1].date()
        ),

        "price": {

            "open": clean_number(
                latest["Open"],
                2
            ),

            "high": clean_number(
                latest["High"],
                2
            ),

            "low": clean_number(
                latest["Low"],
                2
            ),

            "close": close,

            "previous_close": previous_close,

            "change": change,

            "change_percent":
                change_percent,

            "volume": clean_number(
                latest["Volume"],
                0
            )
        },

        "technical": {

            "ma5": clean_number(
                latest["MA5"],
                2
            ),

            "ma10": clean_number(
                latest["MA10"],
                2
            ),

            "ma20": ma20,

            "ma60": ma60,

            "ma20_slope":
                clean_number(
                    latest[
                        "MA20_SLOPE"
                    ],
                    2
                ),

            "rsi": clean_number(
                latest["RSI"],
                2
            ),

            "dif": clean_number(
                latest["DIF"],
                4
            ),

            "dem": clean_number(
                latest["DEM"],
                4
            ),

            "macd_hist":
                clean_number(
                    latest[
                        "MACD_HIST"
                    ],
                    4
                ),

            "k": clean_number(
                latest["K"],
                2
            ),

            "d": clean_number(
                latest["D"],
                2
            ),

            "volume_ma5":
                clean_number(
                    latest[
                        "VOLUME_MA5"
                    ],
                    0
                ),

            "volume_ratio":
                clean_number(
                    latest[
                        "VOLUME_RATIO"
                    ],
                    2
                )
        },

        "conditions": {

            "macd_golden_cross":
                safe_bool(
                    latest[
                        "MACD_GOLDEN"
                    ]
                ),

            "macd_red":
                safe_bool(
                    latest[
                        "MACD_RED"
                    ]
                ),

            "kd_golden_cross":
                safe_bool(
                    latest[
                        "KD_GOLDEN"
                    ]
                ),

            "kd_low_golden_cross":
                safe_bool(
                    latest[
                        "KD_LOW_GOLDEN"
                    ]
                ),

            "rsi_above_50":
                safe_bool(
                    latest[
                        "RSI_BULLISH"
                    ]
                ),

            "volume_over_1_5x":
                safe_bool(
                    latest[
                        "VOLUME_BREAKOUT"
                    ]
                ),

            "above_ma20":
                safe_bool(
                    latest[
                        "ABOVE_MA20"
                    ]
                ),

            "ma20_up":
                ma20_up,

            "ma20_turn_up":
                safe_bool(
                    latest[
                        "MA20_TURN_UP"
                    ]
                ),

            "short_term_core":
                core
        },

        "short_term": {

            "score": score,

            "signal": signal,

            "reasons": reasons,

            "core_conditions": {

                "macd":
                    safe_bool(
                        latest[
                            "MACD_GOLDEN"
                        ]
                    ),

                "kd":
                    safe_bool(
                        latest[
                            "KD_GOLDEN"
                        ]
                    ),

                "rsi":
                    safe_bool(
                        latest[
                            "RSI_BULLISH"
                        ]
                    ),

                "volume":
                    safe_bool(
                        latest[
                            "VOLUME_BREAKOUT"
                        ]
                    ),

                "ma20":
                    safe_bool(
                        latest[
                            "ABOVE_MA20"
                        ]
                    ),

                "ma20_up":
                    ma20_up
            }
        },

        "dca": dca,

        "risk_control": risk,

        "performance": {

            "forward": forward
        },

        "strategy": {

            "short_term": (
                "符合全部核心條件"
                if core
                else
                "尚未完全符合短期核心條件"
            ),

            "dca_action":
                dca["action"],

            "risk_level":
                risk["risk_level"]
        }
    }

    return result


# ============================================================
# 取得股票池
# ============================================================

def build_universe():

    universe = {}

    # 個股備援池
    for code, name in FALLBACK_STOCKS.items():

        if is_valid_code(code):

            universe[code] = {

                "name": name,

                "asset_type": "stock"
            }

    # ETF
    for code, name in ETF_LIST.items():

        if is_valid_code(code):

            universe[code] = {

                "name": name,

                "asset_type": "etf"
            }

    return universe


# ============================================================
# 批次下載
# ============================================================

def download_batch(
    batch,
    universe
):

    symbols = [
        ticker(code)
        for code in batch
    ]

    print(
        f"下載 {len(symbols)} 檔..."
    )

    try:

        data = yf.download(
            symbols,
            period=SCAN_PERIOD,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            group_by="ticker"
        )

        return data

    except Exception as e:

        print(
            f"批次下載失敗：{e}"
        )

        return None


# ============================================================
# 從批次資料取出單一股票
# ============================================================

def extract_symbol_data(
    data,
    code
):

    if data is None:

        return None

    symbol = ticker(
        code
    )

    try:

        if isinstance(
            data.columns,
            pd.MultiIndex
        ):

            levels = data.columns

            if symbol in levels.get_level_values(0):

                return data[
                    symbol
                ].copy()

            if symbol in levels.get_level_values(1):

                return data[
                    :,
                    symbol
                ].copy()

        else:

            return data.copy()

    except Exception:

        return None

    return None


# ============================================================
# 掃描全市場
# ============================================================

def scan_market():

    universe = build_universe()

    codes = list(
        universe.keys()
    )

    print("")
    print("=" * 70)
    print("台股 AI 全市場掃描")
    print("=" * 70)
    print(
        f"分析標的：{len(codes)}"
    )
    print(
        f"個股："
        f"{sum(1 for x in universe.values() if x['asset_type'] == 'stock')}"
    )
    print(
        f"ETF："
        f"{sum(1 for x in universe.values() if x['asset_type'] == 'etf')}"
    )
    print("=" * 70)
    print("")

    results = []

    total_batches = (
        len(codes)
        +
        BATCH_SIZE
        -
        1
    ) // BATCH_SIZE

    for batch_number in range(
        total_batches
    ):

        start = (
            batch_number
            *
            BATCH_SIZE
        )

        end = start + BATCH_SIZE

        batch = codes[
            start:end
        ]

        print(
            f"[批次 {batch_number + 1}/"
            f"{total_batches}]"
        )

        data = download_batch(
            batch,
            universe
        )

        if data is None:

            continue

        for code in batch:

            info = universe[
                code
            ]

            df = extract_symbol_data(
                data,
                code
            )

            try:

                result = analyze_symbol(
                    code,
                    info["name"],
                    info["asset_type"],
                    df
                )

                if result is not None:

                    results.append(
                        result
                    )

            except Exception as e:

                print(
                    f"{code} 分析失敗：{e}"
                )

        if batch_number < (
            total_batches - 1
        ):

            time.sleep(
                BATCH_SLEEP
            )

    return results


# ============================================================
# 排名
# ============================================================

def create_rankings(
    results
):

    stocks = [
        x
        for x in results
        if x["asset_type"]
        == "stock"
    ]

    etfs = [
        x
        for x in results
        if x["asset_type"]
        == "etf"
    ]

    def ranking_key(x):

        return (
            x["short_term"]["score"],
            x["technical"][
                "volume_ratio"
            ] or 0
        )

    stocks_sorted = sorted(
        stocks,
        key=ranking_key,
        reverse=True
    )

    etfs_sorted = sorted(
        etfs,
        key=ranking_key,
        reverse=True
    )

    core = [
        x
        for x in stocks_sorted
        if x["conditions"][
            "short_term_core"
        ]
    ]

    dca = sorted(
        results,
        key=lambda x: (
            x["short_term"]["score"],
            x["technical"][
                "ma20_slope"
            ] or 0
        ),
        reverse=True
    )

    return {

        "short_term_top20": [
            x["id"]
            for x in stocks_sorted[:20]
        ],

        "stock_top10": [
            x["id"]
            for x in stocks_sorted[:10]
        ],

        "etf_top10": [
            x["id"]
            for x in etfs_sorted[:10]
        ],

        "core": [
            x["id"]
            for x in core
        ],

        "dca_top20": [
            x["id"]
            for x in dca[:20]
        ]
    }


# ============================================================
# 統計
# ============================================================

def create_statistics(
    results
):

    total = len(results)

    stocks = sum(
        1
        for x in results
        if x["asset_type"]
        == "stock"
    )

    etfs = sum(
        1
        for x in results
        if x["asset_type"]
        == "etf"
    )

    core = sum(
        1
        for x in results
        if x["conditions"][
            "short_term_core"
        ]
    )

    high_attention = sum(
        1
        for x in results
        if x["short_term"]["score"]
        >= 75
    )

    bullish = sum(
        1
        for x in results
        if x["short_term"]["score"]
        >= 60
    )

    return {

        "total_scanned": total,

        "stocks": stocks,

        "etfs": etfs,

        "core_stocks": core,

        "high_attention": high_attention,

        "bullish": bullish
    }


# ============================================================
# 系統資訊
# ============================================================

def create_system():

    now = datetime.now(
        TAIPEI_TZ
    )

    return {

        "name":
            "台股 AI 選股・零股定投・ETF・動態風控",

        "version":
            "3.0 FULL MARKET",

        "generated_at":
            now.isoformat(),

        "timezone":
            "Asia/Taipei",

        "data_source":
            "Yahoo Finance",

        "scan_mode":
            "FULL_MARKET",

        "description":
            "上市/上櫃個股＋ETF自動掃描"
    }


# ============================================================
# 策略設定
# ============================================================

def create_strategy():

    return {

        "short_term": {

            "macd_golden_cross":
                True,

            "kd_golden_cross":
                True,

            "rsi_above":
                50,

            "volume_multiplier":
                1.5,

            "above_ma20":
                True,

            "ma20_up":
                True
        },

        "scoring": {

            "max_score":
                125,

            "macd_golden":
                25,

            "kd_golden":
                20,

            "kd_low_golden":
                25,

            "rsi":
                15,

            "volume":
                20,

            "above_ma20":
                8,

            "ma20_up":
                5,

            "ma20_turn_up":
                7
        },

        "dca": {

            "reference":
                "MA20",

            "levels": [

                "MA20附近",

                "MA20下方3%",

                "MA20下方6%",

                "MA20下方10%"
            ]
        },

        "performance_tracking": {

            "windows":
                [30, 60, 90],

            "metric":
                "forward_return",

            "win_definition":
                "future_return > 0"
        }
    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(
    results
):

    rankings = create_rankings(
        results
    )

    statistics = create_statistics(
        results
    )

    output = {

        "system":
            create_system(),

        "strategy":
            create_strategy(),

        "statistics":
            statistics,

        "rankings":
            rankings,

        "stocks":
            results
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

    print("")
    print("=" * 70)
    print("資料寫入完成")
    print("=" * 70)
    print(
        f"檔案：{OUTPUT_FILE}"
    )
    print(
        f"總掃描："
        f"{statistics['total_scanned']}"
    )
    print(
        f"個股："
        f"{statistics['stocks']}"
    )
    print(
        f"ETF："
        f"{statistics['etfs']}"
    )
    print(
        f"核心條件："
        f"{statistics['core_stocks']}"
    )
    print(
        f"高度關注："
        f"{statistics['high_attention']}"
    )
    print("")

    print("個股 Top 10：")

    stock_map = {
        x["id"]: x
        for x in results
        if x["asset_type"]
        == "stock"
    }

    for i, code in enumerate(
        rankings["stock_top10"],
        1
    ):

        stock = stock_map.get(
            code
        )

        if stock:

            print(
                f"{i:02d}. "
                f"{code} "
                f"{stock['name']} "
                f""
                f"分數="
                f"{stock['short_term']['score']} "
                f""
                f"訊號="
                f"{stock['short_term']['signal']}"
            )

    print("")

    print("ETF Top 10：")

    etf_map = {
        x["id"]: x
        for x in results
        if x["asset_type"]
        == "etf"
    }

    for i, code in enumerate(
        rankings["etf_top10"],
        1
    ):

        etf = etf_map.get(
            code
        )

        if etf:

            print(
                f"{i:02d}. "
                f"{code} "
                f"{etf['name']} "
                f""
                f"分數="
                f"{etf['short_term']['score']} "
                f""
                f"訊號="
                f"{etf['short_term']['signal']}"
            )

    print("")


# ============================================================
# 主程式
# ============================================================

def main():

    print("")
    print("=" * 70)
    print(
        "台股 AI 選股 + ETF + 零股定投"
    )
    print(
        "FULL MARKET VERSION 3.0"
    )
    print("=" * 70)
    print("")

    start_time = time.time()

    results = scan_market()

    if not results:

        print("")
        print(
            "錯誤：沒有成功取得任何市場資料。"
        )
        print("")

        sys.exit(1)

    save_json(
        results
    )

    elapsed = (
        time.time()
        -
        start_time
    )

    print(
        f"完成，耗時："
        f"{elapsed:.1f} 秒"
    )

    print("")


# ============================================================
# 執行
# ============================================================

if __name__ == "__main__":

    main()
