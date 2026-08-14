# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V7.4 正式版
#
# V7.4 核心修正：
#
# 1. 官方市場清單優先
#    - TWSE ISIN
#    - TWSE 上市
#    - TPEx 上櫃
#    - ETF
#
# 2. 不依賴單一 TPEx API 欄位名稱
#
# 3. 支援：
#    - 4 碼股票
#    - 5 碼股票
#    - 6 碼 ETF
#    - A / B / L / R / U / T 等代號
#
# 4. 關鍵標的強制驗證
#
# 5. Yahoo Finance：
#    - 上市 STOCK -> .TW
#    - 上市 ETF   -> .TW
#    - 上櫃 STOCK -> .TWO
#
# 6. Yahoo Finance 重試機制
#
# 7. Yahoo Finance MultiIndex / Series 防護
#
# 8. 單一標的失敗不影響其他標的
#
# 9. RSI 強制限制 0~100
#
# 10. KD 強制限制 0~100
#
# 11. MACD 黃金交叉
#
# 12. RSI > 50
#
# 13. KD 黃金交叉
#
# 14. 成交量 >= 1.5x MA5
#
# 15. 站上 MA20
#
# 16. MA20 向上
#
# 17. AI SCORE 保留 V7.3 邏輯
#
# 18. DCA 四段式
#
# 19. rankings / statistics / failed 保留
#
# 20. JSON 結構維持 V7.3 相容
#
# 21. V7.4 強化資料型態驗證
#
# 22. V7.4 強化 Yahoo 資料完整性驗證
#
# 23. V7.4 防止 RSI / KD / Price 欄位錯位
#
# 24. V7.4 防止無效 ISIN 被當成股票代號
#
# ============================================================


import os
import sys
import json
import math
import time
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

VERSION = "V7.4"

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
# Yahoo Finance
# ============================================================

REQUEST_TIMEOUT = 20

YF_PERIOD = "1y"

YF_INTERVAL = "1d"

MAX_RETRY = 3

RETRY_DELAY = 1.2

MAX_WORKERS = 6


# ============================================================
# HTTP Headers
# ============================================================

HEADERS = {

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
        "zh-TW,zh;q=0.9,en;q=0.8"

}


# ============================================================
# 關鍵標的
# ============================================================

KEY_SYMBOLS = {

    "0050": "元大台灣50",

    "0056": "元大高股息",

    "00713": "元大台灣高息低波",

    "00878": "國泰永續高股息",

    "00919": "群益台灣精選高息",

    "2330": "台積電",

    "2337": "旺宏",

    "2426": "鼎元"

}


# ============================================================
# 安全浮點數
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
                pd.DataFrame
            )
        ):

            return default

        number = float(
            value
        )

        if not math.isfinite(
            number
        ):

            return default

        return number

    except Exception:

        return default


# ============================================================
# 安全整數
# ============================================================

def safe_int(
    value,
    default=None
):

    number = safe_float(
        value
    )

    if number is None:

        return default

    try:

        return int(
            number
        )

    except Exception:

        return default


# ============================================================
# 四捨五入
# ============================================================

def round_value(
    value,
    digits=2
):

    number = safe_float(
        value
    )

    if number is None:

        return None

    return round(
        number,
        digits
    )


# ============================================================
# 股票代號清理
# ============================================================

def clean_code(
    value
):

    if value is None:

        return None

    text = str(
        value
    ).strip()

    if text.lower() in [
        "",
        "nan",
        "none",
        "null"
    ]:

        return None

    text = (
        text
        .replace(
            ".TW",
            ""
        )
        .replace(
            ".tw",
            ""
        )
        .replace(
            ".TWO",
            ""
        )
        .replace(
            ".two",
            ""
        )
        .strip()
    )

    return text


# ============================================================
# 台股代號驗證
# ============================================================

def valid_taiwan_code(
    code
):

    code = clean_code(
        code
    )

    if not code:

        return False

    if len(code) < 4:

        return False

    if len(code) > 6:

        return False

    # 排除 ISIN
    if code.upper().startswith(
        "TW"
    ):

        return False

    for char in code:

        if not (
            char.isdigit()
            or
            (
                char.isalpha()
                and
                char.isupper()
            )
        ):

            return False

    return True


# ============================================================
# Yahoo Symbol
# ============================================================

def yahoo_symbol(
    code,
    market="上市"
):

    code = clean_code(
        code
    )

    if market == "上櫃":

        return (
            f"{code}.TWO"
        )

    return (
        f"{code}.TW"
    )


# ============================================================
# HTTP JSON
# ============================================================

def http_get_json(
    url,
    params=None
):

    if requests is None:

        print(
            "錯誤：requests 套件不存在"
        )

        return None

    for attempt in range(
        MAX_RETRY
    ):

        try:

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as error:

            print(
                f"HTTP 失敗 "
                f"{attempt + 1}/{MAX_RETRY}: "
                f"{error}"
            )

            if (
                attempt + 1
                <
                MAX_RETRY
            ):

                time.sleep(
                    RETRY_DELAY
                )

    return None


# ============================================================
# TWSE ISIN
# ============================================================

def fetch_twse_isin_list(
    mode
):

    if requests is None:

        return []

    url = (
        "https://isin.twse.com.tw/"
        "isin/C_public.jsp"
    )

    params = {
        "strMode": str(mode)
    }

    print(
        f"取得 TWSE ISIN 市場清單 "
        f"mode={mode}..."
    )

    try:

        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        tables = pd.read_html(
            response.text
        )

        if not tables:

            return []

        table = tables[0]

        if table.empty:

            return []

        result = []

        for _, row in table.iterrows():

            values = []

            for value in row.tolist():

                if pd.isna(
                    value
                ):

                    continue

                text = str(
                    value
                ).strip()

                if text:

                    values.append(
                        text
                    )

            if not values:

                continue

            code = None
            name = None

            # ------------------------------------------------
            # 第一優先：
            # 找真正股票代號
            # ------------------------------------------------

            for value in values:

                candidate = clean_code(
                    value
                )

                if not valid_taiwan_code(
                    candidate
                ):

                    continue

                # 股票代號一般為純數字
                # 或特殊英文代號
                if (
                    candidate.isdigit()
                    and
                    4 <= len(candidate) <= 6
                ):

                    code = candidate

                    break

                if (
                    any(
                        char.isalpha()
                        for char in candidate
                    )
                    and
                    4 <= len(candidate) <= 6
                ):

                    code = candidate

                    break

            if code is None:

                continue

            # ------------------------------------------------
            # 找名稱
            # ------------------------------------------------

            try:

                code_index = values.index(
                    code
                )

                if (
                    code_index + 1
                    <
                    len(values)
                ):

                    candidate_name = (
                        values[
                            code_index + 1
                        ]
                    )

                    if (
                        candidate_name
                        and
                        not valid_taiwan_code(
                            candidate_name
                        )
                    ):

                        name = candidate_name

            except Exception:

                pass

            if not name:

                name = code

            combined = " ".join(
                values
            ).lower()

            # ------------------------------------------------
            # 判斷市場
            # ------------------------------------------------

            if (
                "上櫃"
                in combined
                or
                "tpex"
                in combined
            ):

                market = "上櫃"

            else:

                market = "上市"

            # ------------------------------------------------
            # 判斷 ETF
            # ------------------------------------------------

            if (
                "etf"
                in combined
                or
                "指數股票型"
                in combined
                or
                "受益憑證"
                in combined
            ):

                security_type = "ETF"

            else:

                security_type = "STOCK"

            # ------------------------------------------------
            # 排除不需要的金融商品
            # ------------------------------------------------

            invalid_keywords = [

                "權證",

                "認購權證",

                "認售權證",

                "公司債",

                "債券",

                "海外存託",

                "存託憑證",

                "受益證券"

            ]

            invalid = False

            for keyword in invalid_keywords:

                if keyword in combined:

                    invalid = True

                    break

            if invalid:

                continue

            result.append(
                {
                    "id":
                        code,

                    "name":
                        name,

                    "market":
                        market,

                    "type":
                        security_type,

                    "source":
                        "TWSE ISIN"
                }
            )

        # ----------------------------------------------------
        # 去重
        # ----------------------------------------------------

        unique = {}

        for item in result:

            code = item.get(
                "id"
            )

            if not code:

                continue

            unique[
                code
            ] = item

        return list(
            unique.values()
        )

    except Exception as error:

        print(
            f"ISIN mode={mode} "
            f"取得失敗：{error}"
        )

        return []


# ============================================================
# TWSE 上市股票
# ============================================================

def fetch_twse_stock_list():

    print(
        ""
    )

    print(
        "取得上市股票清單..."
    )

    result = []

    data = fetch_twse_isin_list(
        2
    )

    for item in data:

        if (
            item.get(
                "market"
            ) == "上市"
            and
            item.get(
                "type"
            ) == "STOCK"
        ):

            result.append(
                item
            )

    # --------------------------------------------------------
    # TWSE API 備援
    # --------------------------------------------------------

    if len(result) < 900:

        print(
            "上市 ISIN 清單不足，"
            "啟用 TWSE STOCK_DAY_ALL 備援..."
        )

        url = (
            "https://openapi.twse.com.tw/"
            "v1/exchangeReport/STOCK_DAY_ALL"
        )

        backup = http_get_json(
            url
        )

        if isinstance(
            backup,
            list
        ):

            for row in backup:

                if not isinstance(
                    row,
                    dict
                ):

                    continue

                code = clean_code(
                    row.get(
                        "Code"
                    )
                )

                name = str(
                    row.get(
                        "Name",
                        ""
                    )
                ).strip()

                if not valid_taiwan_code(
                    code
                ):

                    continue

                if not name:

                    continue

                result.append(
                    {
                        "id":
                            code,

                        "name":
                            name,

                        "market":
                            "上市",

                        "type":
                            "STOCK",

                        "source":
                            "TWSE"
                    }
                )

    unique = {}

    for item in result:

        code = item.get(
            "id"
        )

        if code:

            unique[
                code
            ] = item

    result = list(
        unique.values()
    )

    print(
        f"上市股票："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx 上櫃股票
# ============================================================

def fetch_tpex_stock_list():

    print(
        ""
    )

    print(
        "取得上櫃股票清單..."
    )

    result = []

    urls = [

        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_peratio_analysis"
        ),

        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_quotes"
        ),

        (
            "https://www.tpex.org.tw/"
            "openapi/v1/tpex_mainboard_daily_close_quotes"
        )

    ]

    for url in urls:

        data = http_get_json(
            url
        )

        if not isinstance(
            data,
            list
        ):

            continue

        temp = []

        for row in data:

            if not isinstance(
                row,
                dict
            ):

                continue

            code = None
            name = None

            code_keys = [

                "SecuritiesCompanyCode",

                "Code",

                "證券代號",

                "公司代號",

                "SecuritiesCode"

            ]

            name_keys = [

                "CompanyName",

                "Name",

                "證券名稱",

                "公司名稱"

            ]

            for key in code_keys:

                if key in row:

                    candidate = clean_code(
                        row.get(
                            key
                        )
                    )

                    if valid_taiwan_code(
                        candidate
                    ):

                        code = candidate

                        break

            for key in name_keys:

                if key in row:

                    value = row.get(
                        key
                    )

                    if value is not None:

                        text = str(
                            value
                        ).strip()

                        if text:

                            name = text

                            break

            if not valid_taiwan_code(
                code
            ):

                continue

            if not name:

                name = code

            temp.append(
                {
                    "id":
                        code,

                    "name":
                        name,

                    "market":
                        "上櫃",

                    "type":
                        "STOCK",

                    "source":
                        "TPEx"
                }
            )

        if len(temp) > len(
            result
        ):

            result = temp

        if len(result) >= 700:

            break

    # --------------------------------------------------------
    # 官方 ISIN 補齊
    # --------------------------------------------------------

    if len(result) < 700:

        print(
            "上櫃第一來源不足，"
            "使用官方 ISIN 清單補齊..."
        )

        isin_otc = fetch_twse_isin_list(
            4
        )

        for item in isin_otc:

            if (
                item.get(
                    "market"
                ) == "上櫃"
                and
                item.get(
                    "type"
                ) == "STOCK"
            ):

                result.append(
                    item
                )

    unique = {}

    for item in result:

        code = item.get(
            "id"
        )

        if code:

            unique[
                code
            ] = item

    result = list(
        unique.values()
    )

    print(
        f"上櫃股票："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# ETF
# ============================================================

def fetch_twse_etf_list():

    print(
        ""
    )

    print(
        "取得 ETF 清單..."
    )

    result = []

    for mode in [
        2,
        4
    ]:

        data = fetch_twse_isin_list(
            mode
        )

        for item in data:

            if item.get(
                "type"
            ) == "ETF":

                result.append(
                    item
                )

    # --------------------------------------------------------
    # 關鍵 ETF 保底
    # --------------------------------------------------------

    fallback = [

        (
            "0050",
            "元大台灣50"
        ),

        (
            "0056",
            "元大高股息"
        ),

        (
            "00713",
            "元大台灣高息低波"
        ),

        (
            "00878",
            "國泰永續高股息"
        ),

        (
            "00919",
            "群益台灣精選高息"
        )

    ]

    existing = {
        item.get(
            "id"
        )
        for item in result
    }

    for code, name in fallback:

        if code in existing:

            continue

        result.append(
            {
                "id":
                    code,

                "name":
                    name,

                "market":
                    "上市",

                "type":
                    "ETF",

                "source":
                    "ETF fallback"
            }
        )

    unique = {}

    for item in result:

        code = item.get(
            "id"
        )

        if not code:

            continue

        unique[
            code
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"ETF："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# 建立全市場清單
# ============================================================

def build_market_list():

    twse = fetch_twse_stock_list()

    tpex = fetch_tpex_stock_list()

    etf = fetch_twse_etf_list()

    all_items = []

    all_items.extend(
        twse
    )

    all_items.extend(
        tpex
    )

    all_items.extend(
        etf
    )

    unique = {}

    for item in all_items:

        code = clean_code(
            item.get(
                "id"
            )
        )

        if not valid_taiwan_code(
            code
        ):

            continue

        item["id"] = code

        # ETF 優先
        if (
            code not in unique
            or
            item.get(
                "type"
            ) == "ETF"
        ):

            unique[
                code
            ] = item

    market_list = list(
        unique.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    market_order = {
        "上市": 1,
        "上櫃": 2
    }

    type_order = {
        "STOCK": 1,
        "ETF": 2
    }

    market_list.sort(
        key=lambda item: (
            market_order.get(
                item.get(
                    "market"
                ),
                9
            ),

            type_order.get(
                item.get(
                    "type"
                ),
                9
            ),

            str(
                item.get(
                    "id",
                    ""
                )
            )
        )
    )

    listed = sum(
        1
        for item in market_list
        if (
            item.get(
                "market"
            ) == "上市"
            and
            item.get(
                "type"
            ) == "STOCK"
        )
    )

    otc = sum(
        1
        for item in market_list
        if (
            item.get(
                "market"
            ) == "上櫃"
            and
            item.get(
                "type"
            ) == "STOCK"
        )
    )

    etf_count = sum(
        1
        for item in market_list
        if item.get(
            "type"
        ) == "ETF"
    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "V7.4 全市場清單完成"
    )

    print(
        f"市場清單："
        f"{len(market_list)} 檔"
    )

    print(
        f"上市股票："
        f"{listed} 檔"
    )

    print(
        f"上櫃股票："
        f"{otc} 檔"
    )

    print(
        f"ETF："
        f"{etf_count} 檔"
    )

    print(
        "================================================"
    )

    return market_list


# ============================================================
# 市場清單驗證
# ============================================================

def validate_market_list(
    market_list
):

    print(
        ""
    )

    print(
        "V7.4 市場清單完整性驗證："
    )

    code_map = {
        item.get(
            "id"
        ):
            item
        for item in market_list
    }

    passed = True

    for code, expected_name in KEY_SYMBOLS.items():

        item = code_map.get(
            code
        )

        if item is None:

            print(
                f"✗ {code} "
                f"{expected_name} "
                f"| 不在市場清單"
            )

            passed = False

        else:

            print(
                f"✓ {code} "
                f"{item.get('name')} "
                f"| "
                f"{item.get('market')} "
                f"{item.get('type')}"
            )

    if passed:

        print(
            "✓ 關鍵標的市場清單驗證全部通過"
        )

    else:

        print(
            "⚠ 關鍵標的市場清單有缺漏"
        )

    return passed


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    period=14
):

    close = pd.to_numeric(
        close,
        errors="coerce"
    )

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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

    # --------------------------------------------------------
    # RSI 防止除以 0
    # --------------------------------------------------------

    rsi = pd.Series(
        np.nan,
        index=close.index,
        dtype=float
    )

    normal = (
        avg_loss > 0
    )

    rs = (
        avg_gain[normal]
        /
        avg_loss[normal]
    )

    rsi.loc[
        normal
    ] = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    # 沒有下跌時 RSI = 100
    no_loss = (
        avg_loss == 0
    )

    rsi.loc[
        no_loss
    ] = 100

    return rsi.clip(
        lower=0,
        upper=100
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(
    high,
    low,
    close,
    period=9
):

    high = pd.to_numeric(
        high,
        errors="coerce"
    )

    low = pd.to_numeric(
        low,
        errors="coerce"
    )

    close = pd.to_numeric(
        close,
        errors="coerce"
    )

    lowest_low = low.rolling(
        period
    ).min()

    highest_high = high.rolling(
        period
    ).max()

    denominator = (
        highest_high -
        lowest_low
    )

    denominator = denominator.replace(
        0,
        np.nan
    )

    rsv = (
        (
            close -
            lowest_low
        )
        /
        denominator
    ) * 100

    rsv = rsv.clip(
        0,
        100
    )

    k = rsv.ewm(
        com=2,
        adjust=False
    ).mean()

    d = k.ewm(
        com=2,
        adjust=False
    ).mean()

    return (
        k.clip(
            0,
            100
        ),

        d.clip(
            0,
            100
        )
    )


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    close,
    fast=12,
    slow=26,
    signal=9
):

    close = pd.to_numeric(
        close,
        errors="coerce"
    )

    ema_fast = close.ewm(
        span=fast,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=slow,
        adjust=False
    ).mean()

    macd = (
        ema_fast -
        ema_slow
    )

    signal_line = macd.ewm(
        span=signal,
        adjust=False
    ).mean()

    histogram = (
        macd -
        signal_line
    )

    return (
        macd,
        signal_line,
        histogram
    )


# ============================================================
# 最新值
# ============================================================

def get_last_value(
    series
):

    try:

        if isinstance(
            series,
            pd.DataFrame
        ):

            if series.shape[1] != 1:

                return None

            series = series.iloc[
                :,
                0
            ]

        series = pd.to_numeric(
            series,
            errors="coerce"
        )

        series = series.dropna()

        if len(series) == 0:

            return None

        value = series.iloc[-1]

        return safe_float(
            value
        )

    except Exception:

        return None


# ============================================================
# Yahoo DataFrame 正規化
# ============================================================

def normalize_yahoo_dataframe(
    df
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

    try:

        # ----------------------------------------------------
        # MultiIndex
        # ----------------------------------------------------

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):

            # 常見：
            #
            # Price / Ticker
            #
            # 或：
            #
            # Ticker / Price
            #
            # 只取第一層中符合 OHLCV 的欄位。
            # ------------------------------------------------

            wanted = {
                "open",
                "high",
                "low",
                "close",
                "adj close",
                "volume"
            }

            new_columns = []

            for column in df.columns:

                parts = [
                    str(x).strip().lower()
                    for x in column
                ]

                found = None

                for part in parts:

                    if part in wanted:

                        found = part

                        break

                if found is None:

                    new_columns.append(
                        str(
                            parts[-1]
                        )
                    )

                else:

                    new_columns.append(
                        found
                    )

            df = df.copy()

            df.columns = new_columns

        else:

            df = df.copy()

            df.columns = [
                str(column)
                .strip()
                .lower()
                for column in df.columns
            ]

        # ----------------------------------------------------
        # 去除重複欄位
        # ----------------------------------------------------

        if df.columns.duplicated().any():

            df = df.loc[
                :,
                ~df.columns.duplicated()
            ]

        required = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for column in required:

            if column not in df.columns:

                return None

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        # ----------------------------------------------------
        # 清理
        # ----------------------------------------------------

        df = df.dropna(
            subset=[
                "close"
            ]
        )

        df = df[
            df["close"] > 0
        ]

        # ----------------------------------------------------
        # 日期排序
        # ----------------------------------------------------

        try:

            df = df.sort_index()

        except Exception:

            pass

        # ----------------------------------------------------
        # 最低歷史資料
        # ----------------------------------------------------

        if len(df) < 80:

            return None

        return df

    except Exception:

        return None


# ============================================================
# Yahoo Finance 下載
# ============================================================

def download_stock(
    code,
    market
):

    symbol = yahoo_symbol(
        code,
        market
    )

    for attempt in range(
        MAX_RETRY
    ):

        try:

            df = yf.download(
                symbol,
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=REQUEST_TIMEOUT
            )

            df = normalize_yahoo_dataframe(
                df
            )

            if df is None:

                raise ValueError(
                    "Yahoo 資料無效或欄位不足"
                )

            # ------------------------------------------------
            # 最後收盤價
            # ------------------------------------------------

            last_close = get_last_value(
                df["close"]
            )

            if (
                last_close is None
                or
                last_close <= 0
            ):

                raise ValueError(
                    "Yahoo 收盤價無效"
                )

            return df

        except Exception as error:

            print(
                f"{code} "
                f"Yahoo下載失敗 "
                f"{attempt + 1}/{MAX_RETRY} "
                f"| {error}"
            )

            if (
                attempt + 1
                <
                MAX_RETRY
            ):

                time.sleep(
                    RETRY_DELAY
                    *
                    (
                        attempt + 1
                    )
                )

    return None


# ============================================================
# 單檔分析
# ============================================================

def analyze_stock(
    item,
    df
):

    code = item["id"]

    name = item["name"]

    market = item["market"]

    stock_type = item["type"]

    try:

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
        )

        valid_mask = (
            close.notna()
            &
            high.notna()
            &
            low.notna()
        )

        close = close[
            valid_mask
        ]

        high = high[
            valid_mask
        ]

        low = low[
            valid_mask
        ]

        volume = volume[
            valid_mask
        ]

        if len(close) < 80:

            return None

        # ====================================================
        # MA
        # ====================================================

        ma5 = close.rolling(
            5
        ).mean()

        ma20 = close.rolling(
            20
        ).mean()

        ma60 = close.rolling(
            60
        ).mean()

        # ====================================================
        # RSI
        # ====================================================

        rsi = calculate_rsi(
            close,
            14
        )

        # ====================================================
        # KD
        # ====================================================

        k, d = calculate_kd(
            high,
            low,
            close
        )

        # ====================================================
        # MACD
        # ====================================================

        macd, macd_signal, macd_hist = (
            calculate_macd(
                close
            )
        )

        # ====================================================
        # Volume
        # ====================================================

        volume_ma5 = volume.rolling(
            5
        ).mean()

        # ====================================================
        # 最新值
        # ====================================================

        current_price = get_last_value(
            close
        )

        previous_price = (
            safe_float(
                close.iloc[-2]
            )
            if len(close) >= 2
            else None
        )

        current_ma5 = get_last_value(
            ma5
        )

        current_ma20 = get_last_value(
            ma20
        )

        current_ma60 = get_last_value(
            ma60
        )

        current_rsi = get_last_value(
            rsi
        )

        current_k = get_last_value(
            k
        )

        current_d = get_last_value(
            d
        )

        current_macd = get_last_value(
            macd
        )

        current_macd_signal = get_last_value(
            macd_signal
        )

        current_macd_hist = get_last_value(
            macd_hist
        )

        current_volume = get_last_value(
            volume
        )

        current_volume_ma5 = get_last_value(
            volume_ma5
        )

        # ====================================================
        # RSI 強制 0~100
        # ====================================================

        if current_rsi is not None:

            current_rsi = max(
                0.0,
                min(
                    100.0,
                    current_rsi
                )
            )

        # ====================================================
        # KD 強制 0~100
        # ====================================================

        if current_k is not None:

            current_k = max(
                0.0,
                min(
                    100.0,
                    current_k
                )
            )

        if current_d is not None:

            current_d = max(
                0.0,
                min(
                    100.0,
                    current_d
                )
            )

        # ====================================================
        # Volume Ratio
        # ====================================================

        if (
            current_volume is not None
            and
            current_volume_ma5 is not None
            and
            current_volume_ma5 > 0
        ):

            volume_ratio = (
                current_volume
                /
                current_volume_ma5
            )

        else:

            volume_ratio = None

        # ====================================================
        # 漲跌
        # ====================================================

        if (
            current_price is not None
            and
            previous_price is not None
        ):

            change = (
                current_price
                -
                previous_price
            )

            if previous_price != 0:

                change_percent = (
                    change
                    /
                    previous_price
                    *
                    100
                )

            else:

                change_percent = 0

        else:

            change = None

            change_percent = None

        # ====================================================
        # 前一日
        # ====================================================

        previous_k = (
            safe_float(
                k.iloc[-2]
            )
            if len(k) >= 2
            else None
        )

        previous_d = (
            safe_float(
                d.iloc[-2]
            )
            if len(d) >= 2
            else None
        )

        previous_ma20 = (
            safe_float(
                ma20.iloc[-2]
            )
            if len(ma20) >= 2
            else None
        )

        # ====================================================
        # MACD 黃金交叉
        # ====================================================

        macd_golden_cross = False

        if (
            len(macd) >= 2
            and
            len(macd_signal) >= 2
        ):

            previous_macd = safe_float(
                macd.iloc[-2]
            )

            previous_signal = safe_float(
                macd_signal.iloc[-2]
            )

            if (
                previous_macd is not None
                and
                previous_signal is not None
                and
                current_macd is not None
                and
                current_macd_signal is not None
            ):

                macd_golden_cross = (
                    previous_macd
                    <=
                    previous_signal
                    and
                    current_macd
                    >
                    current_macd_signal
                )

        # ====================================================
        # KD 黃金交叉
        # ====================================================

        kd_golden_cross = False

        if (
            current_k is not None
            and
            current_d is not None
            and
            previous_k is not None
            and
            previous_d is not None
        ):

            kd_golden_cross = (
                previous_k
                <=
                previous_d
                and
                current_k
                >
                current_d
            )

        # ====================================================
        # 核心條件
        # ====================================================

        rsi_above_50 = (
            current_rsi is not None
            and
            current_rsi > 50
        )

        volume_over_1_5x = (
            volume_ratio is not None
            and
            volume_ratio >= 1.5
        )

        above_ma20 = (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_price >
            current_ma20
        )

        ma20_up = (
            current_ma20 is not None
            and
            previous_ma20 is not None
            and
            current_ma20 >
            previous_ma20
        )

        macd_positive = (
            current_macd_hist is not None
            and
            current_macd_hist > 0
        )

        # ====================================================
        # 核心訊號
        # ====================================================

        short_term_core = all(
            [
                macd_golden_cross,
                kd_golden_cross,
                rsi_above_50,
                volume_over_1_5x,
                above_ma20,
                ma20_up
            ]
        )

        # ====================================================
        # AI SCORE
        # ====================================================

        score = 0

        # MACD
        if macd_golden_cross:

            score += 20

        elif macd_positive:

            score += 10

        # KD
        if kd_golden_cross:

            score += 15

        elif (
            current_k is not None
            and
            current_d is not None
            and
            current_k >
            current_d
        ):

            score += 8

        # RSI
        if rsi_above_50:

            score += 15

        # Volume
        if (
            volume_ratio is not None
            and
            volume_ratio >= 1.5
        ):

            score += 15

        elif (
            volume_ratio is not None
            and
            volume_ratio >= 1
        ):

            score += 8

        # MA20
        if above_ma20:

            score += 15

        if ma20_up:

            score += 10

        # MACD positive
        if macd_positive:

            score += 10

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        # ====================================================
        # Signal
        # ====================================================

        if short_term_core:

            signal = "強勢核心"

        elif score >= 70:

            signal = "強勢"

        elif score >= 50:

            signal = "偏多"

        elif score >= 30:

            signal = "觀察"

        else:

            signal = "弱勢"

        # ====================================================
        # DCA 四段
        # ====================================================

        if current_ma20 is not None:

            buy_1 = (
                current_ma20
            )

            buy_2 = (
                current_ma20
                *
                0.97
            )

            buy_3 = (
                current_ma20
                *
                0.94
            )

            buy_4 = (
                current_ma20
                *
                0.90
            )

        else:

            buy_1 = None

            buy_2 = None

            buy_3 = None

            buy_4 = None

        # ====================================================
        # DCA Action
        # ====================================================

        if (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_ma20 != 0
        ):

            distance = (
                current_price
                /
                current_ma20
                -
                1
            ) * 100

            if distance <= -10:

                dca_action = "第四批區域"

            elif distance <= -6:

                dca_action = "第三批區域"

            elif distance <= -3:

                dca_action = "第二批區域"

            elif distance <= 3:

                dca_action = "第一批區域"

            elif distance <= 8:

                dca_action = "等待回測"

            else:

                dca_action = "暫緩追價"

        else:

            dca_action = "資料不足"

        # ====================================================
        # JSON
        #
        # 保留 V7.3 結構
        # ====================================================

        return {

            "id":
                str(code),

            "name":
                str(name),

            "symbol":
                str(code),

            "type":
                stock_type,

            "market":
                market,

            "yahoo_symbol":
                yahoo_symbol(
                    code,
                    market
                ),

            "price": {

                "close":
                    round_value(
                        current_price,
                        2
                    ),

                "previous_close":
                    round_value(
                        previous_price,
                        2
                    ),

                "change":
                    round_value(
                        change,
                        2
                    ),

                "change_percent":
                    round_value(
                        change_percent,
                        2
                    )

            },

            "technical": {

                "rsi":
                    round_value(
                        current_rsi,
                        2
                    ),

                "k":
                    round_value(
                        current_k,
                        2
                    ),

                "d":
                    round_value(
                        current_d,
                        2
                    ),

                "macd":
                    round_value(
                        current_macd,
                        4
                    ),

                "macd_signal":
                    round_value(
                        current_macd_signal,
                        4
                    ),

                "macd_hist":
                    round_value(
                        current_macd_hist,
                        4
                    ),

                "ma5":
                    round_value(
                        current_ma5,
                        2
                    ),

                "ma20":
                    round_value(
                        current_ma20,
                        2
                    ),

                "ma60":
                    round_value(
                        current_ma60,
                        2
                    ),

                "volume":
                    round_value(
                        current_volume,
                        0
                    ),

                "volume_ma5":
                    round_value(
                        current_volume_ma5,
                        0
                    ),

                "volume_ratio":
                    round_value(
                        volume_ratio,
                        2
                    )

            },

            "conditions": {

                "macd_golden_cross":
                    bool(
                        macd_golden_cross
                    ),

                "kd_golden_cross":
                    bool(
                        kd_golden_cross
                    ),

                "rsi_above_50":
                    bool(
                        rsi_above_50
                    ),

                "volume_over_1_5x":
                    bool(
                        volume_over_1_5x
                    ),

                "above_ma20":
                    bool(
                        above_ma20
                    ),

                "ma20_up":
                    bool(
                        ma20_up
                    ),

                "short_term_core":
                    bool(
                        short_term_core
                    )

            },

            "short_term": {

                "score":
                    int(score),

                "signal":
                    signal

            },

            "dca": {

                "buy_1":
                    round_value(
                        buy_1,
                        2
                    ),

                "buy_2":
                    round_value(
                        buy_2,
                        2
                    ),

                "buy_3":
                    round_value(
                        buy_3,
                        2
                    ),

                "buy_4":
                    round_value(
                        buy_4,
                        2
                    ),

                "action":
                    dca_action

            }

        }

    except Exception as error:

        print(
            f"{code}: "
            f"分析失敗："
            f"{error}"
        )

        return None


# ============================================================
# 股票資料驗證
# ============================================================

def validate_stock(
    stock
):

    if not isinstance(
        stock,
        dict
    ):

        return False

    stock_id = stock.get(
        "id"
    )

    if not stock_id:

        return False

    price = stock.get(
        "price",
        {}
    )

    if not isinstance(
        price,
        dict
    ):

        return False

    close = safe_float(
        price.get(
            "close"
        )
    )

    if (
        close is None
        or
        close <= 0
    ):

        return False

    technical = stock.get(
        "technical",
        {}
    )

    if not isinstance(
        technical,
        dict
    ):

        return False

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = safe_float(
        technical.get(
            "rsi"
        )
    )

    if rsi is not None:

        if (
            rsi < 0
            or
            rsi > 100
        ):

            return False

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    for field in [
        "k",
        "d"
    ]:

        value = safe_float(
            technical.get(
                field
            )
        )

        if value is not None:

            if (
                value < 0
                or
                value > 100
            ):

                return False

    # --------------------------------------------------------
    # Volume Ratio
    # --------------------------------------------------------

    volume_ratio = safe_float(
        technical.get(
            "volume_ratio"
        )
    )

    if (
        volume_ratio is not None
        and
        volume_ratio < 0
    ):

        return False

    return True


# ============================================================
# Rankings
# ============================================================

def build_rankings(
    stocks
):

    ranking_data = sorted(

        stocks,

        key=lambda stock: (

            safe_float(
                stock.get(
                    "short_term",
                    {}
                ).get(
                    "score"
                ),
                0
            ),

            safe_float(
                stock.get(
                    "technical",
                    {}
                ).get(
                    "volume_ratio"
                ),
                0
            )

        ),

        reverse=True
    )

    short_term = [

        str(
            stock["id"]
        )

        for stock in ranking_data

    ]

    # --------------------------------------------------------
    # Core
    # --------------------------------------------------------

    core_stocks = [

        stock

        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )

    ]

    core_stocks.sort(

        key=lambda stock:

        safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        ),

        reverse=True
    )

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca_stocks = [

        stock

        for stock in stocks

        if stock.get(
            "technical",
            {}
        ).get(
            "ma20"
        ) is not None

    ]

    def dca_score(
        stock
    ):

        score = safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        )

        price = safe_float(
            stock.get(
                "price",
                {}
            ).get(
                "close"
            ),
            0
        )

        ma20 = safe_float(
            stock.get(
                "technical",
                {}
            ).get(
                "ma20"
            ),
            0
        )

        if (
            price
            and
            ma20
        ):

            distance = abs(
                price /
                ma20
                -
                1
            )

        else:

            distance = 999

        return (
            score,
            -distance
        )

    dca_stocks.sort(
        key=dca_score,
        reverse=True
    )

    return {

        "short_term":
            short_term,

        "core":
            [
                str(
                    stock["id"]
                )
                for stock in core_stocks
            ],

        "dca":
            [
                str(
                    stock["id"]
                )
                for stock in dca_stocks
            ]

    }


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks,
    market_list
):

    total_market = len(
        market_list
    )

    total_stocks = len(
        stocks
    )

    listed = sum(
        1
        for stock in stocks
        if (
            stock.get(
                "market"
            ) == "上市"
            and
            stock.get(
                "type"
            ) == "STOCK"
        )
    )

    otc = sum(
        1
        for stock in stocks
        if (
            stock.get(
                "market"
            ) == "上櫃"
            and
            stock.get(
                "type"
            ) == "STOCK"
        )
    )

    etf = sum(
        1
        for stock in stocks
        if stock.get(
            "type"
        ) == "ETF"
    )

    core_stocks = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )
    )

    ai_70 = sum(
        1
        for stock in stocks
        if safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        ) >= 70
    )

    macd_golden = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "macd_golden_cross",
            False
        )
    )

    rsi_above_50 = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "rsi_above_50",
            False
        )
    )

    kd_golden = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "kd_golden_cross",
            False
        )
    )

    volume_over_1_5x = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "volume_over_1_5x",
            False
        )
    )

    above_ma20 = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "above_ma20",
            False
        )
    )

    ma20_up = sum(
        1
        for stock in stocks
        if stock.get(
            "conditions",
            {}
        ).get(
            "ma20_up",
            False
        )
    )

    return {

        "total_market":
            total_market,

        "total_stocks":
            total_stocks,

        "listed_stocks":
            listed,

        "otc_stocks":
            otc,

        "etf":
            etf,

        "core_stocks":
            core_stocks,

        "ai_70":
            ai_70,

        "macd_golden":
            macd_golden,

        "rsi_above_50":
            rsi_above_50,

        "kd_golden":
            kd_golden,

        "volume_over_1_5x":
            volume_over_1_5x,

        "above_ma20":
            above_ma20,

        "ma20_up":
            ma20_up

    }


# ============================================================
# 單檔處理
# ============================================================

def process_one(
    item
):

    code = item["id"]

    market = item["market"]

    try:

        df = download_stock(
            code,
            market
        )

        if df is None:

            return (
                None,
                {
                    "id":
                        code,

                    "name":
                        item["name"],

                    "market":
                        market,

                    "type":
                        item["type"]
                }
            )

        stock = analyze_stock(
            item,
            df
        )

        if stock is None:

            return (
                None,
                {
                    "id":
                        code,

                    "name":
                        item["name"],

                    "market":
                        market,

                    "type":
                        item["type"]
                }
            )

        if not validate_stock(
            stock
        ):

            return (
                None,
                {
                    "id":
                        code,

                    "name":
                        item["name"],

                    "market":
                        market,

                    "type":
                        item["type"]
                }
            )

        return (
            stock,
            None
        )

    except Exception:

        return (
            None,
            {
                "id":
                    code,

                "name":
                    item["name"],

                "market":
                    market,

                "type":
                    item["type"]
            }
        )


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(
    data
):

    try:

        temp_file = (
            OUTPUT_FILE
            +
            ".tmp"
        )

        with open(
            temp_file,
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

        os.replace(
            temp_file,
            OUTPUT_FILE
        )

        print(
            ""
        )

        print(
            f"資料已寫入："
            f"{OUTPUT_FILE}"
        )

        return True

    except Exception as error:

        print(
            f"JSON 寫入失敗："
            f"{error}"
        )

        return False


# ============================================================
# 主程式
# ============================================================

def main():

    start_time = time.time()

    now = datetime.now(
        TW_TZ
    )

    print(
        "================================================"
    )

    print(
        f"台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
    )

    print(
        f"開始時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        f"平行工作數："
        f"{MAX_WORKERS}"
    )

    print(
        "================================================"
    )

    # ========================================================
    # 1. 市場清單
    # ========================================================

    market_list = build_market_list()

    if not market_list:

        print(
            "錯誤："
            "無法取得任何市場清單。"
        )

        sys.exit(1)

    # ========================================================
    # 2. 市場清單驗證
    # ========================================================

    validate_market_list(
        market_list
    )

    # ========================================================
    # 3. 全市場分析
    # ========================================================

    stocks = []

    failed = []

    total = len(
        market_list
    )

    completed = 0

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        f"開始全市場分析："
        f"{total} 檔"
    )

    print(
        "================================================"
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {

            executor.submit(
                process_one,
                item
            ):
                item

            for item in market_list

        }

        for future in as_completed(
            futures
        ):

            item = futures[
                future
            ]

            completed += 1

            try:

                stock, error_item = (
                    future.result()
                )

                if stock is not None:

                    stocks.append(
                        stock
                    )

                elif error_item is not None:

                    failed.append(
                        error_item
                    )

            except Exception:

                failed.append(
                    {
                        "id":
                            item["id"],

                        "name":
                            item["name"],

                        "market":
                            item["market"],

                        "type":
                            item["type"]
                    }
                )

            if (
                completed % 25 == 0
                or
                completed == total
            ):

                print(
                    f"[進度] "
                    f"{completed}/{total} "
                    f"| 成功 "
                    f"{len(stocks)} "
                    f"| 失敗 "
                    f"{len(failed)}"
                )

    # ========================================================
    # 4. 排序
    # ========================================================

    stocks.sort(
        key=lambda stock:
        str(
            stock.get(
                "id",
                ""
            )
        )
    )

    failed.sort(
        key=lambda item:
        str(
            item.get(
                "id",
                ""
            )
        )
    )

    # ========================================================
    # 5. 空資料防護
    # ========================================================

    if len(stocks) == 0:

        print(
            ""
        )

        print(
            "錯誤："
            "沒有任何標的成功取得資料。"
        )

        sys.exit(1)

    # ========================================================
    # 6. Rankings
    # ========================================================

    rankings = build_rankings(
        stocks
    )

    # ========================================================
    # 7. Statistics
    # ========================================================

    statistics = build_statistics(
        stocks,
        market_list
    )

    # ========================================================
    # 8. 市場統計
    # ========================================================

    market_listed = sum(
        1
        for item in market_list
        if (
            item.get(
                "market"
            ) == "上市"
            and
            item.get(
                "type"
            ) == "STOCK"
        )
    )

    market_otc = sum(
        1
        for item in market_list
        if (
            item.get(
                "market"
            ) == "上櫃"
            and
            item.get(
                "type"
            ) == "STOCK"
        )
    )

    market_etf = sum(
        1
        for item in market_list
        if item.get(
            "type"
        ) == "ETF"
    )

    listed_success = sum(
        1
        for stock in stocks
        if (
            stock.get(
                "market"
            ) == "上市"
            and
            stock.get(
                "type"
            ) == "STOCK"
        )
    )

    otc_success = sum(
        1
        for stock in stocks
        if (
            stock.get(
                "market"
            ) == "上櫃"
            and
            stock.get(
                "type"
            ) == "STOCK"
        )
    )

    etf_success = sum(
        1
        for stock in stocks
        if stock.get(
            "type"
        ) == "ETF"
    )

    # ========================================================
    # 9. Output
    # ========================================================

    output = {

        "version":
            VERSION,

        "updated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "market":
            "TW",

        "source":
            "TWSE ISIN + TPEx + Yahoo Finance",

        "market_list_count":
            len(market_list),

        "successful_count":
            len(stocks),

        "failed_count":
            len(failed),

        "market_statistics": {

            "listed":
                market_listed,

            "otc":
                market_otc,

            "etf":
                market_etf

        },

        "success_statistics": {

            "listed":
                listed_success,

            "otc":
                otc_success,

            "etf":
                etf_success

        },

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics,

        "failed":
            failed

    }

    # ========================================================
    # 10. Save
    # ========================================================

    if not save_json(
        output
    ):

        sys.exit(1)

    # ========================================================
    # 11. 執行時間
    # ========================================================

    elapsed = (
        time.time()
        -
        start_time
    )

    # ========================================================
    # 12. 完成統計
    # ========================================================

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "V7.4 全市場掃描完成"
    )

    print(
        "================================================"
    )

    print(
        f"市場清單："
        f"{len(market_list)} 檔"
    )

    print(
        f"成功分析："
        f"{len(stocks)} 檔"
    )

    print(
        f"失敗："
        f"{len(failed)} 檔"
    )

    print(
        ""
    )

    print(
        f"上市股票："
        f"{market_listed} 檔"
    )

    print(
        f"上櫃股票："
        f"{market_otc} 檔"
    )

    print(
        f"ETF："
        f"{market_etf} 檔"
    )

    print(
        ""
    )

    print(
        f"成功上市分析："
        f"{listed_success} 檔"
    )

    print(
        f"成功上櫃分析："
        f"{otc_success} 檔"
    )

    print(
        f"成功 ETF 分析："
        f"{etf_success} 檔"
    )

    print(
        ""
    )

    print(
        f"AI ≥ 70："
        f"{statistics['ai_70']} 檔"
    )

    print(
        f"核心訊號："
        f"{statistics['core_stocks']} 檔"
    )

    print(
        f"MACD 黃金交叉："
        f"{statistics['macd_golden']} 檔"
    )

    print(
        f"RSI > 50："
        f"{statistics['rsi_above_50']} 檔"
    )

    print(
        f"KD 黃金交叉："
        f"{statistics['kd_golden']} 檔"
    )

    print(
        f"成交量 > 1.5x："
        f"{statistics['volume_over_1_5x']} 檔"
    )

    print(
        f"站上 MA20："
        f"{statistics['above_ma20']} 檔"
    )

    print(
        f"MA20 向上："
        f"{statistics['ma20_up']} 檔"
    )

    print(
        ""
    )

    print(
        f"耗時："
        f"{elapsed:.2f} 秒"
    )

    print(
        "================================================"
    )

    # ========================================================
    # 13. 關鍵標的最終驗證
    # ========================================================

    print(
        ""
    )

    print(
        "V7.4 關鍵標的驗證："
    )

    stock_map = {

        str(
            stock["id"]
        ):
            stock

        for stock in stocks

    }

    market_map = {

        str(
            item["id"]
        ):
            item

        for item in market_list

    }

    for code in KEY_SYMBOLS:

        stock = stock_map.get(
            code
        )

        if stock:

            price = stock.get(
                "price",
                {}
            )

            technical = stock.get(
                "technical",
                {}
            )

            print(
                f"✓ {code} "
                f"{stock.get('name')} "
                f"| "
                f"{stock.get('market')} "
                f"{stock.get('type')} "
                f"| "
                f"價格="
                f"{price.get('close')} "
                f"| "
                f"RSI="
                f"{technical.get('rsi')}"
            )

        elif code in market_map:

            item = market_map[
                code
            ]

            print(
                f"△ {code} "
                f"{item.get('name')} "
                f"| "
                f"{item.get('market')} "
                f"{item.get('type')} "
                f"| "
                f"已在市場清單但 Yahoo "
                f"本次抓取失敗"
            )

        else:

            print(
                f"✗ {code} "
                f"{KEY_SYMBOLS[code]} "
                f"| 完全不在市場清單"
            )

    # ========================================================
    # 14. 失敗率
    # ========================================================

    if len(market_list) > 0:

        failure_rate = (
            len(failed)
            /
            len(market_list)
            *
            100
        )

    else:

        failure_rate = 100

    print(
        ""
    )

    print(
        f"失敗率："
        f"{failure_rate:.2f}%"
    )

    print(
        ""
    )

    print(
        "V7.4 執行結束。"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
