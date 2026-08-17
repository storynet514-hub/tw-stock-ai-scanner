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
4. 保留技術分析必要欄位：
   - date
   - high
   - low
   - close
   - volume
5. 分檔寫入 Data/prices/
6. 每 100 檔股票一個 JSON
7. 產生 Data/prices/manifest.json
8. 驗證成功率
9. 驗證所有分檔
10. 驗證通過後才替換舊 Data/prices/

============================================================
技術分析用途
============================================================

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

open
adj_close

============================================================
重要修正
============================================================

V3.0：
    Data/prices.json

V4.0：
    Data/prices/
        prices_001.json
        prices_002.json
        ...
        manifest.json

避免單一 prices.json 超過 GitHub 100 MB。

============================================================
安全機制
============================================================

✓ 成功率低於 80% 不更新
✓ Universe 異常不更新
✓ 任一分檔寫入失敗不更新
✓ 任一分檔驗證失敗不更新
✓ 單檔超過安全大小不更新
✓ 使用暫存目錄建立完整資料
✓ 全部驗證通過後才替換舊 Data/prices/
✓ 不再產生 Data/prices.json
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

SCHEMA_VERSION = "prices-v4.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

# 每個分檔最多股票數
STOCKS_PER_FILE = 100

# 最低成功率
MIN_SUCCESS_RATE = 0.80

# 每檔最低歷史資料
MIN_HISTORY_ROWS = 100

# GitHub 單檔 100 MB
# 使用 80 MB 作為安全警戒
MAX_FILE_SIZE_MB = 80.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
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
    }
)


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
# 日期
# ============================================================

def date_to_timestamp(date_string):
    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d",
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
# 讀取 JSON
# ============================================================

def load_json(path):

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:

        return json.load(file)


def save_json(path, data):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


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

        data = load_json(
            UNIVERSE_FILE
        )

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
# 市場判斷
# ============================================================

def detect_market(item):

    if not isinstance(item, dict):
        return None

    possible_keys = [
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

    for key in possible_keys:

        value = item.get(key)

        if value is None:
            continue

        text = str(value).strip().upper()

        if not text:
            continue

        # ----------------------------------------------------
        # TPEx
        # ----------------------------------------------------

        tpex_values = {
            "TWO",
            "TPEX",
            "OTC",
            "O",
            "上櫃",
            "上柜",
            "櫃買",
            "柜买",
            "OTC MARKET",
        }

        if (
            text in tpex_values
            or "TPEX" in text
            or "上櫃" in text
            or "上柜" in text
            or "櫃買" in text
            or "柜买" in text
        ):

            return "TWO"

        # ----------------------------------------------------
        # TWSE
        # ----------------------------------------------------

        twse_values = {
            "TW",
            "TWSE",
            "TSE",
            "L",
            "上市",
        }

        if (
            text in twse_values
            or "TWSE" in text
            or "上市" in text
        ):

            return "TW"

    return None


# ============================================================
# 股票代號
# ============================================================

def extract_code(value):

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    # Yahoo ticker
    if text.endswith(".TW"):

        code = text[:-3]

        if code.isdigit():
            return code

        return None

    if text.endswith(".TWO"):

        code = text[:-4]

        if code.isdigit():
            return code

        return None

    # 純數字
    if text.isdigit():

        if 4 <= len(text) <= 6:
            return text

    return None


# ============================================================
# Yahoo Symbol
# ============================================================

def build_yahoo_symbol(code, market=None):

    if not code:
        return None

    code = str(code).strip()

    if not code.isdigit():
        return None

    if market == "TWO":
        return code + ".TWO"

    return code + ".TW"


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
# 解析單筆 Universe
# ============================================================

def parse_record(item, fallback_code=None):

    code = None
    market = None
    name = ""

    # --------------------------------------------------------
    # String
    # --------------------------------------------------------

    if isinstance(item, str):

        code = extract_code(
            item
        )

    # --------------------------------------------------------
    # Dict
    # --------------------------------------------------------

    elif isinstance(item, dict):

        possible_code_keys = [
            "symbol",
            "ticker",
            "code",
            "stock_id",
            "stock_code",
            "證券代號",
            "有價證券代號",
            "代號",
        ]

        for key in possible_code_keys:

            value = item.get(key)

            extracted = extract_code(
                value
            )

            if extracted:

                code = extracted

                break

        if code is None:

            code = extract_code(
                fallback_code
            )

        market = detect_market(
            item
        )

        name = extract_name(
            item
        )

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
    # 市場資訊
    # --------------------------------------------------------

    if market is None:

        if isinstance(item, str):

            text = item.upper()

            if text.endswith(".TWO"):
                market = "TWO"

            elif text.endswith(".TW"):
                market = "TW"

        if market is None:
            market = "TW"

    symbol = build_yahoo_symbol(
        code,
        market,
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
# 解析 Universe
# ============================================================

def extract_symbols(universe):

    section("嚴格解析 Universe")

    records = {}

    def add_record(
        item,
        fallback_code=None,
    ):

        parsed = parse_record(
            item,
            fallback_code,
        )

        if parsed is None:
            return

        symbol = parsed["symbol"]

        if symbol not in records:

            records[symbol] = parsed

        else:

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

            add_record(value)

            for key, child in value.items():

                key_code = extract_code(
                    key
                )

                if key_code:

                    if isinstance(
                        child,
                        dict,
                    ):

                        add_record(
                            child,
                            key,
                        )

                    elif isinstance(
                        child,
                        str,
                    ):

                        market = None

                        key_upper = (
                            str(key)
                            .strip()
                            .upper()
                        )

                        if key_upper.endswith(
                            ".TWO"
                        ):

                            market = "TWO"

                        elif key_upper.endswith(
                            ".TW"
                        ):

                            market = "TW"

                        record = {
                            "symbol": key,
                            "code": key_code,
                            "market": (
                                market
                                or "TW"
                            ),
                            "name": child,
                        }

                        symbol = build_yahoo_symbol(
                            key_code,
                            record["market"],
                        )

                        if symbol:

                            record["symbol"] = (
                                symbol
                            )

                            add_record(
                                record
                            )

                    else:

                        add_record(
                            {
                                "symbol": key
                            },
                            key,
                        )

                if isinstance(
                    child,
                    (dict, list),
                ):

                    walk(child)

    walk(universe)

    records = dict(
        sorted(
            records.items(),
            key=lambda x: (
                0
                if x[1].get("market") == "TW"
                else 1,
                x[1].get("code", ""),
            ),
        )
    )

    twse_count = sum(
        1
        for item in records.values()
        if item.get("market") == "TW"
    )

    tpex_count = sum(
        1
        for item in records.values()
        if item.get("market") == "TWO"
    )

    log(
        f"合法股票代號：{len(records)}"
    )

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEx：{tpex_count}"
    )

    if records:

        log("")
        log("前 20 個合法標的：")

        for index, (
            symbol,
            record,
        ) in enumerate(
            records.items(),
            start=1,
        ):

            if index > 20:
                break

            log(
                f"{index:4d}. "
                f"{symbol} | "
                f"{record.get('name', '')} | "
                f"{record.get('market')}"
            )

    if not records:

        raise RuntimeError(
            "Universe 沒有解析出任何股票"
        )

    return records


# ============================================================
# Yahoo 歷史資料
# ============================================================

def fetch_history(symbol):

    url = YAHOO_URL.format(
        symbol=symbol
    )

    params = {
        "period1": date_to_timestamp(
            START_DATE
        ),
        "period2": int(time.time()) + 86400,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "false",
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {},
            )

            error = chart.get(
                "error"
            )

            if error:

                if isinstance(
                    error,
                    dict,
                ):

                    description = (
                        error.get(
                            "description"
                        )
                        or "Yahoo API error"
                    )

                else:

                    description = str(
                        error
                    )

                raise RuntimeError(
                    description
                )

            results = chart.get(
                "result"
            )

            if not results:

                raise RuntimeError(
                    "Yahoo 沒有回傳 result"
                )

            result = results[0]

            timestamps = result.get(
                "timestamp"
            )

            indicators = result.get(
                "indicators",
                {},
            )

            quotes = indicators.get(
                "quote",
                [],
            )

            if not timestamps:

                raise RuntimeError(
                    "沒有 timestamp"
                )

            if not quotes:

                raise RuntimeError(
                    "沒有 quote"
                )

            quote = quotes[0]

            highs = quote.get(
                "high",
                [],
            )

            lows = quote.get(
                "low",
                [],
            )

            closes = quote.get(
                "close",
                [],
            )

            volumes = quote.get(
                "volume",
                [],
            )

            rows = []

            for i, timestamp in enumerate(
                timestamps
            ):

                if i >= len(highs):
                    continue

                if i >= len(lows):
                    continue

                if i >= len(closes):
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

                volume = (
                    safe_int(
                        volumes[i]
                    )
                    if i < len(volumes)
                    else 0
                )

                if (
                    high is None
                    or low is None
                    or close is None
                ):

                    continue

                try:

                    date = (
                        datetime.fromtimestamp(
                            timestamp,
                            tz=timezone.utc,
                        ).strftime(
                            "%Y-%m-%d"
                        )
                    )

                except Exception:

                    continue

                rows.append(
                    {
                        "date": date,
                        "high": round(
                            high,
                            4,
                        ),
                        "low": round(
                            low,
                            4,
                        ),
                        "close": round(
                            cl
