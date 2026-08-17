#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V4.0

============================================================
用途
============================================================

1. 讀取 Data/universe.json
2. 正確辨識：
   - TWSE 上市 → .TW
   - TPEx 上櫃 → .TWO
3. 從 Yahoo Finance 取得歷史日線
4. 將價格資料「分檔」寫入：

   Data/prices/

例如：

Data/prices/
├── index.json
├── 1101.TW.json
├── 1102.TW.json
├── 1240.TWO.json
├── 1259.TWO.json
├── 2330.TW.json
└── ...

============================================================
技術分析必要欄位
============================================================

保留：

- date
- high
- low
- close
- volume

用途：

KD
    high / low / close

MACD
    close

RSI
    close

MA5 / MA20
    close

60日高低點
    close

成交量
    volume

============================================================
刻意移除
============================================================

- open
- adj_close

============================================================
V4.0 核心修正
============================================================

V3.0：

    Data/prices.json

問題：

    約 122 MB

GitHub 單檔限制：

    100 MB

因此 V4.0 改成：

    Data/prices/
        ├── 1101.TW.json
        ├── 1102.TW.json
        ├── ...
        └── index.json

每一檔股票獨立 JSON。

這樣：

✓ 不會產生超過 100 MB 的單一價格檔
✓ backtest 可以直接讀取 Data/prices/
✓ 未來可以單獨更新個股
✓ Git 可以正常管理分檔資料
✓ index.json 提供完整索引

============================================================
安全機制
============================================================

✓ Universe 不存在 → 失敗
✓ Universe 無合法股票 → 失敗
✓ 價格成功率 < 80% → 失敗
✓ 單一股票歷史資料不足 → 該股票失敗
✓ 不覆蓋舊資料直到整批驗證通過
✓ 先寫入暫存目錄
✓ 驗證完成後再正式替換 Data/prices/
✓ 每檔價格資料都有 JSON 驗證
✓ index.json 同步建立
✓ 不產生 Data/prices.json

============================================================
資料流程
============================================================

Data/universe.json
        ↓
解析市場
        ↓
TWSE → .TW
TPEx → .TWO
        ↓
Yahoo Finance
        ↓
暫存目錄
        ↓
完整驗證
        ↓
Data/prices/
        ↓
下一階段 backtest_winrate.py V2.0
"""

import json
import math
import shutil
import sys
import tempfile
import time

from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V4.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICES_DIR = DATA_DIR / "prices"

# 暫存目錄名稱
TEMP_DIR_NAME = ".prices_build_tmp"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

RETRY_DELAY = 1.5

# 請求間隔
REQUEST_DELAY = 0.08

# 最低價格資料成功率
MIN_SUCCESS_RATE = 0.80

# 每檔股票最低歷史資料
MIN_HISTORY_ROWS = 100

# 每個檔案的安全大小上限
# 理論上單檔股票只有幾十 KB，
# 這裡仍保留安全檢查。
MAX_SINGLE_FILE_MB = 20

MAX_SINGLE_FILE_BYTES = (
    MAX_SINGLE_FILE_MB * 1024 * 1024
)


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "*/*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
    "Connection": "keep-alive",
})


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# JSON 寫入
# ============================================================

def write_json(path, data):
    """
    UTF-8 JSON 寫入。
    ensure_ascii=False 保留中文。
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with path.open(
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False
        )

        f.write("\n")


# ============================================================
# 日期
# ============================================================

def date_to_timestamp(date_string):

    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d"
    )

    dt = dt.replace(
        tzinfo=timezone.utc
    )

    return int(
        dt.timestamp()
    )


# ============================================================
# 數值
# ============================================================

def safe_float(value):

    if value is None:
        return None

    try:

        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except Exception:
        return None


def safe_int(value):

    if value is None:
        return 0

    try:

        number = float(value)

        if not math.isfinite(number):
            return 0

        return int(number)

    except Exception:
        return 0


# ============================================================
# 讀取 Universe
# ============================================================

def load_universe():

    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到：{UNIVERSE_FILE}"
        )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8-sig"
        ) as f:

            data = json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"universe.json 讀取失敗：{exc}"
        ) from exc

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 格式錯誤："
            "頂層必須是 object"
        )

    log(
        f"Universe JSON：{UNIVERSE_FILE}"
    )

    return data


# ============================================================
# 股票代號
# ============================================================

def extract_code(value):

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    # --------------------------------------------------------
    # Yahoo TW
    # --------------------------------------------------------

    if text.endswith(".TW"):

        code = text[:-3]

        if code.isdigit():
            return code

        return None

    # --------------------------------------------------------
    # Yahoo TWO
    # --------------------------------------------------------

    if text.endswith(".TWO"):

        code = text[:-4]

        if code.isdigit():
            return code

        return None

    # --------------------------------------------------------
    # 純股票代號
    # --------------------------------------------------------

    if text.isdigit():

        if 4 <= len(text) <= 6:
            return text

    return None


# ============================================================
# 市場辨識
# ============================================================

def detect_market(item):

    if not isinstance(item, dict):
        return None

    keys = [
        "market",
        "exchange",
        "market_type",
        "marketType",
        "board",
        "type",
        "市場",
        "市場別",
        "交易所",
        "掛牌市場",
        "上市櫃",
        "上市櫃別",
        "category",
    ]

    for key in keys:

        value = item.get(key)

        if value is None:
            continue

        text = str(value).strip()

        if not text:
            continue

        upper = text.upper()

        # ----------------------------------------------------
        # TPEx
        # ----------------------------------------------------

        if (
            upper in {
                "TWO",
                "TPEX",
                "TPEX",
                "OTC",
                "O",
                "OTC MARKET",
            }
            or "TPEX" in upper
            or "TPEx".upper() in upper
            or "上櫃" in text
            or "上柜" in text
            or "櫃買" in text
            or "柜买" in text
        ):

            return "TWO"

        # ----------------------------------------------------
        # TWSE
        # ----------------------------------------------------

        if (
            upper in {
                "TW",
                "TWSE",
                "TSE",
                "L",
            }
            or "TWSE" in upper
            or "上市" in text
        ):

            return "TW"

    return None


# ============================================================
# Yahoo Symbol
# ============================================================

def build_yahoo_symbol(
    code,
    market
):

    if not code:
        return None

    if not code.isdigit():
        return None

    if market == "TWO":
        return f"{code}.TWO"

    return f"{code}.TW"


# ============================================================
# 股票名稱
# ============================================================

def extract_name(item):

    if not isinstance(item, dict):
        return ""

    keys = [
        "name",
        "stock_name",
        "company_name",
        "名稱",
        "證券名稱",
        "公司名稱",
    ]

    for key in keys:

        value = item.get(key)

        if value:

            return str(value).strip()

    return ""


# ============================================================
# 解析單一 Universe record
# ============================================================

def parse_record(
    item,
    fallback_code=None
):

    code = None
    market = None
    name = ""

    # --------------------------------------------------------
    # String
    # --------------------------------------------------------

    if isinstance(item, str):

        code = extract_code(item)

        text = item.strip().upper()

        if text.endswith(".TWO"):
            market = "TWO"

        elif text.endswith(".TW"):
            market = "TW"

    # --------------------------------------------------------
    # Dict
    # --------------------------------------------------------

    elif isinstance(item, dict):

        code_keys = [
            "symbol",
            "ticker",
            "code",
            "stock_id",
            "stock_code",
            "證券代號",
            "有價證券代號",
            "代號",
        ]

        for key in code_keys:

            value = item.get(key)

            parsed = extract_code(value)

            if parsed:

                code = parsed
                break

        if code is None:

            code = extract_code(
                fallback_code
            )

        market = detect_market(item)

        name = extract_name(item)

    # --------------------------------------------------------
    # fallback
    # --------------------------------------------------------

    if code is None:

        code = extract_code(
            fallback_code
        )

    if code is None:
        return None

    # --------------------------------------------------------
    # 如果沒有市場資訊
    # --------------------------------------------------------

    if market is None:

        fallback_text = str(
            fallback_code or ""
        ).strip().upper()

        if fallback_text.endswith(".TWO"):
            market = "TWO"

        elif fallback_text.endswith(".TW"):
            market = "TW"

        else:
            # Universe 若沒有明確市場，
            # 預設 TWSE。
            market = "TW"

    symbol = build_yahoo_symbol(
        code,
        market
    )

    if symbol is None:
        return None

    return {
        "symbol": symbol,
        "code": code,
        "market": market,
        "name": name,
    }


# ============================================================
# Universe 遞迴解析
# ============================================================

def extract_universe_records(universe):

    section("嚴格解析 Universe")

    records = {}

    def add_record(
        item,
        fallback_code=None
    ):

        parsed = parse_record(
            item,
            fallback_code
        )

        if parsed is None:
            return

        symbol = parsed["symbol"]

        if symbol not in records:

            records[symbol] = parsed

        else:

            # 補名稱
            if (
                not records[symbol].get("name")
                and parsed.get("name")
            ):

                records[symbol]["name"] = (
                    parsed["name"]
                )

    def walk(value):

        # ----------------------------------------------------
        # List
        # ----------------------------------------------------

        if isinstance(value, list):

            for item in value:

                walk(item)

            return

        # ----------------------------------------------------
        # Dict
        # ----------------------------------------------------

        if isinstance(value, dict):

            # 本身可能就是股票 record
            add_record(value)

            for key, child in value.items():

                key_code = extract_code(key)

                if key_code:

                    # child 為 dict
                    if isinstance(
                        child,
                        dict
                    ):

                        add_record(
                            child,
                            key
                        )

                    # child 為股票名稱
                    elif isinstance(
                        child,
                        str
                    ):

                        key_upper = (
                            str(key)
                            .strip()
                            .upper()
                        )

                        if key_upper.endswith(
                            ".TWO"
                        ):

                            market = "TWO"

                        else:

                            market = "TW"

                        symbol = (
                            build_yahoo_symbol(
                                key_code,
                                market
                            )
                        )

                        if symbol:

                            records[symbol] = {
                                "symbol": symbol,
                                "code": key_code,
                                "market": market,
                                "name": child,
                            }

                if isinstance(
                    child,
                    (dict, list)
                ):

                    walk(child)

    walk(universe)

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    sorted_records = sorted(
        records.values(),
        key=lambda x: (
            x["market"],
            x["code"]
        )
    )

    log(
        f"合法股票代號：{len(sorted_records)}"
    )

    if sorted_records:

        log("")
        log("前 20 個合法標的：")

        for index, item in enumerate(
            sorted_records[:20],
            start=1
        ):

            log(
                f"{index:4d}. "
                f"{item['symbol']} | "
                f"{item.get('name', '')}"
            )

    return sorted_records


# ============================================================
# Yahoo Finance
# ============================================================

def fetch_yahoo_history(
    symbol
):

    period1 = date_to_timestamp(
        START_DATE
    )

    period2 = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    ) + 86400

    url = YAHOO_URL.format(
        symbol=symbol
    )

    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "false",
        "includePrePost": "false",
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {}
            )

            result = chart.get(
                "result"
            )

            if not result:

                error = chart.get(
                    "error"
                )

                if error:

                    description = (
                        error.get(
                            "description"
                        )
                        if isinstance(
                            error,
                            dict
                        )
                        else str(error)
                    )

                    raise RuntimeError(
                        description
                    )

                raise RuntimeError(
                    "Yahoo API 沒有 result"
                )

            result = result[0]

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

            if not timestamps:
                raise RuntimeError(
                    "Yahoo 沒有 timestamp"
                )

            if not quote_list:
                raise RuntimeError(
                    "Yahoo 沒有 quote"
                )

            quote = quote_list[0]

            highs = quote.get(
                "high",
                []
            )

            lows = quote.get(
                "low",
                []
            )

            closes = quote.get(
                "close",
                []
            )

            volumes = quote.get(
                "volume",
                []
            )

            rows = []

            for i, timestamp in enumerate(
                timestamps
            ):

                if timestamp is None:
                    continue

                if i >= len(highs):
                    continue

                if i >= len(lows):
                    continue

                if i >= len(closes):
                    continue

                if i >= len(volumes):
                    continue

                high = safe_float(
                    highs[i]
                )

                low = safe_float(
                    lows[i]
                )

                close = safe_float(
                    closes[i]
                )

                volume = safe_int(
                    volumes[i]
                )

                # 技術分析必要欄位
                if (
                    high is None
                    or low is None
                    or close is None
                ):
                    continue

                if close <= 0:
                    continue

                date = datetime.fromtimestamp(
                    int(timestamp),
                    tz=timezone.utc
                ).strftime(
                    "%Y-%m-%d"
                )

                rows.append({
                    "date": date,
                    "high": round(
                        high,
                        4
                    ),
                    "low": round(
                        low,
                        4
                    ),
                    "close": round(
                        close,
                        4
            
