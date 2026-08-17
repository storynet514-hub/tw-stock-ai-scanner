#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V4.0

============================================================
用途
============================================================

1. 讀取 Data/universe.json
2. 正確辨識 TWSE / TPEx
3. TWSE -> Yahoo .TW
4. TPEx -> Yahoo .TWO
5. 從 Yahoo Finance 取得歷史日線
6. 保留技術分析必要欄位：
   - date
   - high
   - low
   - close
   - volume
7. 將價格資料分割寫入 Data/prices/
8. 每 100 檔股票一個 JSON
9. 建立 Data/prices/manifest.json
10. 驗證成功率
11. 驗證所有分檔
12. 所有驗證通過後才替換舊資料

============================================================
輸出
============================================================

Data/
└── prices/
    ├── prices_001.json
    ├── prices_002.json
    ├── prices_003.json
    ├── ...
    └── manifest.json

不再產生：

Data/prices.json

============================================================
安全機制
============================================================

✓ 成功率 < 80% 不更新
✓ 歷史資料不足不更新
✓ 分檔驗證失敗不更新
✓ 單檔 > 80 MB 不更新
✓ 所有資料先寫入暫存目錄
✓ 驗證完成後才替換舊 Data/prices/
✓ 舊資料不會因單一股票失敗而消失

============================================================
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

# 每個分檔最多股票數
STOCKS_PER_FILE = 100

# 最低成功率
MIN_SUCCESS_RATE = 0.80

# 每檔至少需要的歷史資料
MIN_HISTORY_ROWS = 100

# Yahoo Finance
YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

START_DATE = "2023-01-01"

# HTTP
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
REQUEST_DELAY = 0.08
RETRY_DELAY = 1.5

# 單檔安全上限
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
# JSON
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


def safe_round(value, digits=4):
    number = safe_float(value)

    if number is None:
        return None

    return round(number, digits)


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
# 股票代號
# ============================================================

def extract_code(value):
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    if text.endswith(".TWO"):
        code = text[:-4]

        if code.isdigit():
            return code

        return None

    if text.endswith(".TW"):
        code = text[:-3]

        if code.isdigit():
            return code

        return None

    if text.isdigit():
        if 4 <= len(text) <= 6:
            return text

    return None


# ============================================================
# 市場判斷
# ============================================================

def normalize_market(value):
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    tpex_values = {
        "TWO",
        "TPEX",
        "TPEx".upper(),
        "OTC",
        "O",
        "上櫃",
        "上柜",
        "櫃買",
        "柜买",
    }

    twse_values = {
        "TW",
        "TWSE",
        "TSE",
        "L",
        "上市",
    }

    if text in tpex_values:
        return "TWO"

    if text in twse_values:
        return "TW"

    if (
        "TPEx".upper() in text
        or "上櫃" in text
        or "上柜" in text
        or "櫃買" in text
        or "柜买" in text
    ):
        return "TWO"

    if (
        "TWSE" in text
        or "上市" in text
    ):
        return "TW"

    return None


# ============================================================
# Yahoo Symbol
# ============================================================

def build_yahoo_symbol(code, market):
    if not code:
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

    keys = (
        "name",
        "stock_name",
        "company_name",
        "名稱",
        "證券名稱",
        "公司名稱",
        "股票名稱",
    )

    for key in keys:
        value = item.get(key)

        if value is None:
            continue

        text = str(value).strip()

        if text:
            return text

    return ""


# ============================================================
# Universe 單筆解析
# ============================================================

def parse_record(item, fallback_code=None):
    code = None
    market = None
    name = ""

    if isinstance(item, str):
        code = extract_code(item)

        text = item.strip().upper()

        if text.endswith(".TWO"):
            market = "TWO"
        elif text.endswith(".TW"):
            market = "TW"

    elif isinstance(item, dict):

        code_keys = (
            "symbol",
            "ticker",
            "code",
            "stock_id",
            "stock_code",
            "證券代號",
            "有價證券代號",
            "股票代號",
            "代號",
        )

        for key in code_keys:
            if key not in item:
                continue

            extracted = extract_code(
                item.get(key)
            )

            if extracted:
                code = extracted
                break

        if code is None:
            code = extract_code(
                fallback_code
            )

        market_keys = (
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
        )

        for key in market_keys:
            if key not in item:
                continue

            market = normalize_market(
                item.get(key)
            )

            if market:
                break

        name = extract_name(item)

    if code is None:
        code = extract_code(
            fallback_code
        )

    if code is None:
        return None

    if market is None:
        market = "TW"

    symbol = build_yahoo_symbol(
        code,
        market,
    )

    if symbol is None:
        return None

    return {
        "code": code,
        "name": name,
        "market": market,
        "symbol": symbol,
    }


# ============================================================
# Universe 解析
# ============================================================

def extract_universe_records(data):
    records = {}

    def add(item, fallback_code=None):
        parsed = parse_record(
            item,
            fallback_code,
        )

        if parsed is None:
            return

        code = parsed["code"]

        if code not in records:
            records[code] = parsed
            return

        old = records[code]

        if not old.get("name") and parsed.get("name"):
            old["name"] = parsed["name"]

        if old.get("market") == "TW":
            if parsed.get("market") == "TWO":
                old["market"] = "TWO"
                old["symbol"] = (
                    code + ".TWO"
                )

    def walk(value):

        if isinstance(value, list):
            for item in value:
                walk(item)
            return

        if not isinstance(value, dict):
            return

        # 如果本身就是股票資料
        add(value)

        for key, child in value.items():

            key_code = extract_code(key)

            if key_code:

                if isinstance(child, dict):
                    add(
                        child,
                        key,
                    )

                elif isinstance(child, str):

                    key_upper = (
                        str(key)
                        .strip()
                        .upper()
                    )

                    if key_upper.endswith(".TWO"):
                        market = "TWO"
                    else:
                        market = "TW"

                    add(
                        {
                            "code": key_code,
                            "name": child,
                            "market": market,
                        },
                        key,
                    )

            if isinstance(child, (dict, list)):
                walk(child)

    walk(data)

    return records


def load_universe():
    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    try:
        data = load_json(
            UNIVERSE_FILE
        )
    except Exception as error:
        raise RuntimeError(
            "universe.json 讀取失敗："
            + str(error)
        ) from error

    records = extract_universe_records(
        data
    )

    if not records:
        raise RuntimeError(
            "universe.json 沒有解析出有效股票"
        )

    twse_count = sum(
        1
        for item in records.values()
        if item["market"] == "TW"
    )

    tpex_count = sum(
        1
        for item in records.values()
        if item["market"] == "TWO"
    )

    log(
        "Universe 股票數量："
        + str(len(records))
    )

    log(
        "TWSE："
        + str(twse_count)
    )

    log(
        "TPEx："
        + str(tpex_count)
    )

    log("")
    log("前 20 個合法標的：")

    sorted_records = sorted(
        records.values(),
        key=lambda x: (
            0
            if x["market"] == "TW"
            else 1,
            x["code"],
        ),
    )

    for index, item in enumerate(
        sorted_records[:20],
        start=1,
    ):
        log(
            f"{index:4d}. "
            f"{item['symbol']} | "
            f"{item['name']} | "
            f"{item['market']}"
        )

    return {
        item["code"]: item
        for item in sorted_records
    }


# ============================================================
# Yahoo 歷史價格
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
                if isinstance(error, dict):
                    description = (
                        error.get(
                            "description"
                        )
                        or "Yahoo API error"
                    )
                else:
                    description = str(error)

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

                if timestamp is None:
                    continue

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

                if high <= 0:
                    continue

                if low <= 0:
                    continue

                if close <= 0:
                    continue

                try:
                    date_text = (
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
                        "date": date_text,
                        "high": safe_round(
                            high
                        ),
                        "low": safe_round(
                            low
                        ),
                        "close": safe_round(
                            close
                        ),
                        "volume": volume,
                    }
                )

            if not rows:
                raise RuntimeError(
                    "沒有有效歷史價格"
                )

            # 去除重複日期
            unique = {}

            for row in rows:
                unique[row["date"]] = row

            rows = list(
                unique.values()
            )

            rows.sort(
                key=lambda x: x["date"]
            )

            if len(rows) < MIN_HISTORY_ROWS:
                raise RuntimeError(
                    "歷史資料不足："
                    + str(len(rows))
                    + " 筆"
                )

            return rows

        except Exception as error:

            last_error = error

            log(
                "      ⚠ attempt "
                + str(attempt)
                + "/"
                + str(MAX_RETRIES)
                + " 失敗："
                + str(error)
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        "Yahoo 取得失敗："
        + str(last_error)
    )


# ============================================================
# 取得全部股票
# ============================================================

def fetch_all(records):
    section("開始取得歷史價格")

    total = len(records)

    success = {}
    failed = {}

    market_total = {
        "TW": 0,
        "TWO": 0,
    }

    market_success = {
        "TW": 0,
        "TWO": 0,
    }

    market_failed = {
        "TW": 0,
        "TWO": 0,
    }

    for index, stock in enumerate(
        records.values(),
        start=1,
    ):

        code = stock["code"]
        name = stock["name"]
        market = stock["market"]
        symbol = stock["symbol"]

        if market not in market_total:
            market_total[market] = 0
            market_success[market] = 0
            market_failed[market] = 0

        market_total[market] += 1

        log(
            f"[{index}/{total}] "
            f"{code} "
            f"{name} "
            f"| {market} "
            f"| {symbol}"
        )

        try:

            rows = fetch_history(
                symbol
            )

            record = {
                "code": code,
                "symbol": symbol,
                "name": name,
                "market": market,
                "data_start": rows[0]["date"],
                "data_end": rows[-1]["date"],
                "count": len(rows),
                "data": rows,
            }

            success[code] = record

            market_success[market] += 1

            log(
                "      ✓ "
                + str(len(rows))
                + " 筆"
            )

        except Exception as error:

            failed[code] = {
                "code": code,
                "symbol": symbol,
                "name": name,
        
