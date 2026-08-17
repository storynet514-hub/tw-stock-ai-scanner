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


# ============================================================
# 儲存 JSON
# ============================================================

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

    section(
        "讀取 Data/universe.json"
    )

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

def parse_record(
    item,
    fallback_code=None,
):

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

    section(
        "嚴格解析 Universe"
    )

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

                        key_upper = (
                            str(key)
                            .strip()
                            .upper()
                        )

                        market = None

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
        log(
            "前 20 個合法標的："
        )

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

                if high <= 0:
                    continue

                if low <= 0:
                    continue

                if close <= 0:
                    continue

                if low > high:
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
                            close,
                            4,
                        ),
                        "volume": volume,
                    }
                )

            # ------------------------------------------------
            # 日期排序
            # ------------------------------------------------

            rows.sort(
                key=lambda row: row["date"]
            )

            # ------------------------------------------------
            # 日期去重
            # ------------------------------------------------

            unique_rows = []
            seen_dates = set()

            for row in rows:

                date = row["date"]

                if date in seen_dates:
                    continue

                seen_dates.add(date)

                unique_rows.append(
                    row
                )

            rows = unique_rows

            if len(rows) < MIN_HISTORY_ROWS:

                raise RuntimeError(
                    f"歷史資料不足："
                    f"{len(rows)} 筆"
                )

            return rows

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"{symbol} Yahoo 取得失敗："
        f"{last_error}"
    )


# ============================================================
# 歷史資料驗證
# ============================================================

def validate_history(
    symbol,
    rows,
):

    if not isinstance(
        rows,
        list,
    ):

        raise RuntimeError(
            f"{symbol}: history 不是 list"
        )

    if len(rows) < MIN_HISTORY_ROWS:

        raise RuntimeError(
            f"{symbol}: 歷史資料不足 "
            f"{len(rows)} < "
            f"{MIN_HISTORY_ROWS}"
        )

    required_fields = {
        "date",
        "high",
        "low",
        "close",
        "volume",
    }

    previous_date = None

    for index, row in enumerate(rows):

        if not isinstance(
            row,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: 第 {index} 筆不是 object"
            )

        if set(row.keys()) != required_fields:

            raise RuntimeError(
                f"{symbol}: 第 {index} 筆欄位錯誤"
            )

        date = row.get("date")

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        close = safe_float(
            row.get("close")
        )

        volume = row.get(
            "volume"
        )

        if not isinstance(
            date,
            str,
        ):

            raise RuntimeError(
                f"{symbol}: 日期格式錯誤"
            )

        try:

            datetime.strptime(
                date,
                "%Y-%m-%d",
            )

        except Exception:

            raise RuntimeError(
                f"{symbol}: 日期無法解析："
                f"{date}"
            )

        if high is None:
            raise RuntimeError(
                f"{symbol}: high 無效"
            )

        if low is None:
            raise RuntimeError(
                f"{symbol}: low 無效"
            )

        if close is None:
            raise RuntimeError(
                f"{symbol}: close 無效"
            )

        if high <= 0:
            raise RuntimeError(
                f"{symbol}: high <= 0"
            )

        if low <= 0:
            raise RuntimeError(
                f"{symbol}: low <= 0"
            )

        if close <= 0:
            raise RuntimeError(
                f"{symbol}: close <= 0"
            )

        if low > high:

            raise RuntimeError(
                f"{symbol}: low > high"
            )

        if not isinstance(
            volume,
            int,
        ):

            raise RuntimeError(
                f"{symbol}: volume 不是 int"
            )

        if volume < 0:

            raise RuntimeError(
                f"{symbol}: volume < 0"
            )

        if (
            previous_date is not None
            and date <= previous_date
        ):

            raise RuntimeError(
                f"{symbol}: 日期未嚴格遞增"
            )

        previous_date = date

    return True


# ============================================================
# 建立單一股票資料
# ============================================================

def build_stock_data(
    record,
):

    symbol = record["symbol"]

    code = record.get(
        "code",
        "",
    )

    market = record.get(
        "market",
        "",
    )

    name = record.get(
        "name",
        "",
    )

    rows = fetch_history(
        symbol
    )

    validate_history(
        symbol,
        rows,
    )

    return {
        "symbol": symbol,
        "code": code,
        "market": market,
        "name": name,
        "rows": rows,
    }


# ============================================================
# 分割股票
# ============================================================

def chunk_list(
    items,
    chunk_size,
):

    for i in range(
        0,
        len(items),
        chunk_size,
    ):

        yield items[
            i:i + chunk_size
        ]


# ============================================================
# 建立分檔資料
# ============================================================

def build_chunk_payload(
    chunk,
    chunk_number,
    generated_at,
):

    stocks = {}

    for record in chunk:

        symbol = record["symbol"]

        stocks[symbol] = {
            "code": record.get(
                "code",
                "",
            ),
            "market": record.get(
                "market",
                "",
            ),
            "name": record.get(
                "name",
                "",
            ),
            "data": record["rows"],
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "generated_at": generated_at,
        "file_index": chunk_number,
        "stock_count": len(stocks),
        "stocks": stocks,
    }


# ============================================================
# 驗證分檔 JSON
# ============================================================

def validate_chunk_file(
    path,
):

    if not path.exists():

        raise RuntimeError(
            f"分檔不存在：{path}"
        )

    size = path.stat().st_size

    if size <= 0:

        raise RuntimeError(
            f"分檔為空：{path}"
        )

    if size > MAX_FILE_SIZE_BYTES:

        raise RuntimeError(
            f"分檔超過安全大小："
            f"{path.name} "
            f"{size / 1024 / 1024:.2f} MB"
        )

    try:

        data = load_json(
            path
        )

    except Exception as exc:

        raise RuntimeError(
            f"JSON 解析失敗："
            f"{path.name}：{exc}"
        ) from exc

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            f"{path.name}: 頂層不是 object"
        )

    if data.get(
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            f"{path.name}: schema_version 錯誤"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            f"{path.name}: stocks 不是 object"
        )

    if not stocks:

        raise RuntimeError(
            f"{path.name}: stocks 為空"
        )

    stock_count = data.get(
        "stock_count"
    )

    if stock_count != len(stocks):

        raise RuntimeError(
            f"{path.name}: stock_count 不一致"
        )

    for symbol, stock in stocks.items():

        if not isinstance(
            stock,
            dict,
        ):

            raise RuntimeError(
                f"{path.name}: "
                f"{symbol} 格式錯誤"
            )

        code = stock.get(
            "code"
        )

        market = stock.get(
            "market"
        )

        history = stock.get(
            "data"
        )

        if not code:

            raise RuntimeError(
                f"{path.name}: "
                f"{symbol} code 空白"
            )

        if market not in (
            "TW",
            "TWO",
        ):

            raise RuntimeError(
                f"{path.name}: "
                f"{symbol} market 錯誤"
            )

        expected_symbol = (
            build_yahoo_symbol(
                code,
                market,
            )
        )

        if expected_symbol != symbol:

            raise RuntimeError(
                f"{path.name}: "
                f"{symbol} ticker 錯誤"
            )

        validate_history(
            symbol,
            history,
        )

    return True


# ============================================================
# 建立 Manifest
# ============================================================

def build_manifest(
    stock_records,
    successful_records,
    failed_records,
    files,
    generated_at,
):

    total_universe = len(
        stock_records
    )

    success_count = len(
        successful_records
    )

    failed_count = len(
        failed_records
    )

    success_rate = (
        success_count / total_universe
        if total_universe > 0
        else 0.0
    )

    twse_total = sum(
        1
        for item in stock_records
        if item.get("market") == "TW"
    )

    tpex_total = sum(
        1
        for item in stock_records
        if item.get("market") == "TWO"
    )

    twse_success = sum(
        1
        for item in successful_records
        if item.get("market") == "TW"
    )

    tpex_success = sum(
        1
        for item in successful_records
        if item.get("market") == "TWO"
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "generated_at": generated_at,
        "start_date": START_DATE,
        "data_type": "daily",
        "interval": "1d",
        "fields": [
            "date",
            "high",
            "low",
            "close",
            "volume",
        ],
        "stocks_per_file": STOCKS_PER_FILE,
        "minimum_history_rows": MIN_HISTORY_ROWS,
        "minimum_success_rate": MIN_SUCCESS_RATE,
        "total_universe": total_universe,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": round(
            success_rate,
            6,
        ),
        "twse": {
            "universe": twse_total,
            "success": twse_success,
            "failed": (
                twse_total
                - twse_success
            ),
        },
        "tpex": {
            "universe": tpex_total,
            "success": tpex_success,
            "failed": (
                tpex_total
                - tpex_success
            ),
        },
        "file_count": len(files),
        "files": files,
        "failed_symbols": failed_records,
    }

    return manifest


# ============================================================
# 驗證 Manifest
# ============================================================

def validate_manifest(
    manifest,
):

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest 不是 object"
        )

    if manifest.get(
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            "manifest schema_version 錯誤"
        )

    required_fields = [
        "generated_at",
        "total_universe",
        "success_count",
        "failed_count",
        "success_rate",
        "file_count",
        "files",
    ]

    for field in required_fields:

        if field not in manifest:

            raise RuntimeError(
                f"manifest 缺少欄位："
                f"{field}"
            )

    total = manifest[
        "total_universe"
    ]

    success = manifest[
        "success_count"
    ]

    failed = manifest[
        "failed_count"
    ]

    rate = manifest[
        "success_rate"
    ]

    if total <= 0:

        raise RuntimeError(
            "manifest total_universe <= 0"
        )

    if success + failed != total:

        raise RuntimeError(
            "manifest 數量不一致"
        )

    if not (
        0 <= rate <= 1
    ):

        raise RuntimeError(
            "manifest success_rate 無效"
        )

    if rate < MIN_SUCCESS_RATE:

        raise RuntimeError(
            f"成功率不足："
            f"{rate:.2%} < "
            f"{MIN_SUCCESS_RATE:.2%}"
        )

    files = manifest[
        "files"
    ]

    if not isinstance(
        files,
        list,
    ):

        raise RuntimeError(
            "manifest files 不是 list"
        )

    if len(files) != manifest[
        "file_count"
    ]:

        raise RuntimeError(
            "manifest file_count 不一致"
        )

    return True


# ============================================================
# 驗證整個暫存目錄
# ============================================================

def validate_output_directory(
    output_dir,
    expected_success_records,
    manifest,
):

    section(
        "驗證全部 prices 分檔"
    )

    if not output_dir.exists():

        raise RuntimeError(
            "暫存 prices 目錄不存在"
        )

    expected_symbols = {
        record["symbol"]
        for record in expected_success_records
    }

    found_symbols = set()

    json_files = sorted(
        output_dir.glob(
            "prices_*.json"
        )
    )

    if not json_files:

        raise RuntimeError(
            "沒有任何 prices 分檔"
        )

    log(
        f"找到分檔：{len(json_files)}"
    )

    for path in json_files:

        log(
            f"驗證：{path.name}"
        )

        validate_chunk_file(
            path
        )

        data = load_json(
            path
        )

        for symbol in data[
            "stocks"
        ]:

            if symbol in found_symbols:

                raise RuntimeError(
                    f"股票重複出現在多個分檔："
                    f"{symbol}"
                )

            found_symbols.add(
                symbol
            )

    if found_symbols != expected_symbols:

        missing = (
            expected_symbols
            - found_symbols
        )

        extra = (
            found_symbols
            - expected_symbols
        )

        raise RuntimeError(
            "分檔股票集合不一致；"
            f"missing={list(missing)[:10]}; "
            f"extra={list(extra)[:10]}"
        )

    if len(found_symbols) != manifest[
        "success_count"
    ]:

        raise RuntimeError(
            "分檔股票數與 manifest "
            "success_count 不一致"
        )

    log(
        f"✓ 分檔驗證通過："
        f"{len(json_files)} 檔"
    )

    log(
        f"✓ 股票驗證通過："
        f"{len(found_symbols)} 檔"
    )

    return True


# ============================================================
# 原子替換整個 prices 目錄
# ============================================================

def atomic_replace_directory(
    temp_dir,
    output_dir,
):

    backup_dir = None

    try:

        output_dir.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # 如果舊資料存在，先改名成 backup
        # ----------------------------------------------------

        if output_dir.exists():

            backup_dir = (
                output_dir.parent
                / (
                    f".prices_backup_"
                    f"{int(time.time())}"
                )
            )

            output_dir.rename(
                backup_dir
            )

        # ----------------------------------------------------
        # 暫存目錄改成正式目錄
        # ----------------------------------------------------

        Path(temp_dir).rename(
            output_dir
        )

        # ----------------------------------------------------
        # 成功後刪除舊版本
        # ----------------------------------------------------

        if (
            backup_dir is not None
            and backup_dir.exists()
        ):

            shutil.rmtree(
                backup_dir
            )

    except Exception:

        # ----------------------------------------------------
        # 如果正式目錄尚未建立，
        # 嘗試恢復舊資料
        # ----------------------------------------------------

        if (
            not output_dir.exists()
            and backup_dir is not None
            and backup_dir.exists()
        ):

            try:

                backup_dir.rename(
                    output_dir
                )

            except Exception:

                pass

        raise


# ============================================================
# 建立完整價格資料
# ============================================================

def build_prices():

    section(
        f"台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"UNIVERSE：{UNIVERSE_FILE}"
    )

    log(
        f"OUTPUT：{OUTPUT_DIR}"
    )

    log(
        f"START_DATE：{START_DATE}"
    )

    log(
        f"STOCKS_PER_FILE："
        f"{STOCKS_PER_FILE}"
    )

    log(
        f"MIN_SUCCESS_RATE："
        f"{MIN_SUCCESS_RATE:.0%}"
    )

    log(
        f"MIN_HISTORY_ROWS："
        f"{MIN_HISTORY_ROWS}"
    )

    log(
        f"MAX_FILE_SIZE："
        f"{MAX_FILE_SIZE_MB:.1f} MB"
    )

    # ========================================================
    # 1. 讀取 Universe
    # ========================================================

    universe = load_universe()

    records = extract_symbols(
        universe
    )

    # ========================================================
    # 2. Universe 基本安全驗證
    # ========================================================

    universe_total = len(
        records
    )

    if universe_total <= 0:

        raise RuntimeError(
            "Universe 股票數為 0"
        )

    log("")
    log(
        f"Universe 總股票數："
        f"{universe_total}"
    )

    # ========================================================
    # 3. 建立暫存目錄
    # ========================================================

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_root = tempfile.mkdtemp(
        prefix=".prices_build_",
        dir=DATA_DIR,
    )

    temp_dir = Path(
        temp_root
    )

    log("")
    log(
        f"暫存目錄：{temp_dir}"
    )

    successful_records = []

    failed_records = []

    start_time = time.time()

    # ========================================================
    # 4. 逐檔取得 Yahoo
    # ========================================================

    section(
        "開始取得 Yahoo Finance 歷史資料"
    )

    record_list = list(
        records.values()
    )

    for index, record in enumerate(
        record_list,
        start=1,
    ):

        symbol = record[
            "symbol"
        ]

        log(
            f"[{index}/{universe_total}] "
            f"{symbol} "
            f"{record.get('name', '')}"
        )

        try:

            data = build_stock_data(
                record
            )

            record_with_data = {
                "symbol": data["symbol"],
                "code": data["code"],
                "market": data["market"],
                "name": data["name"],
                "rows": data["rows"],
            }

            successful_records.append(
                record_with_data
            )

            log(
                f"  ✓ 成功 "
                f"{len(data['rows'])} 筆"
            )

        except Exception as exc:

            failed_records.append(
                {
                    "symbol": symbol,
                    "code": record.get(
                        "code",
                        "",
                    ),
                    "market": record.get(
                        "market",
                        "",
                    ),
                    "name": record.get(
                        "name",
                        "",
                    ),
                    "error": str(exc),
                }
            )

            log(
                f"  ✗ 失敗：{exc}"
            )

        if (
            index < universe_total
        ):

            time.sleep(
                REQUEST_DELAY
            )

    # ========================================================
    # 5. 成功率
    # ========================================================

    success_count = len(
        successful_records
    )

    failed_count = len(
        failed_records
    )

    success_rate = (
        success_count
        / universe_total
    )

    section(
        "資料取得統計"
    )

    log(
        f"Universe："
        f"{universe_total}"
    )

    log(
        f"成功："
        f"{success_count}"
    )

    log(
        f"失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    if success_rate < MIN_SUCCESS_RATE:

        raise RuntimeError(
            f"成功率不足："
            f"{success_rate:.2%} < "
            f"{MIN_SUCCESS_RATE:.2%}"
        )

    if success_count <= 0:

        raise RuntimeError(
            "沒有任何股票成功取得資料"
        )

    # ========================================================
    # 6. 產生時間
    # ========================================================

    generated_at = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )

    # ========================================================
    # 7. 分檔
    # ========================================================

    section(
        "建立價格分檔"
    )

    chunks = list(
        chunk_list(
            successful_records,
            STOCKS_PER_FILE,
        )
    )

    file_entries = []

    for chunk_number, chunk in enumerate(
        chunks,
        start=1,
    ):

        filename = (
            f"prices_"
            f"{chunk_number:03d}.json"
        )

        path = (
            temp_dir
            / filename
        )

        payload = build_chunk_payload(
            chunk,
            chunk_number,
            generated_at,
        )

        save_json(
            path,
            payload,
        )

        size = path.stat().st_size

        if size > MAX_FILE_SIZE_BYTES:

            raise RuntimeError(
                f"{filename} 超過安全大小："
                f"{size / 1024 / 1024:.2f} MB"
            )

        # ----------------------------------------------------
        # 分檔立即驗證
        # ----------------------------------------------------

        validate_chunk_file(
            path
        )

        file_entries.append(
            {
                "file": filename,
                "stock_count": len(
                    chunk
                ),
                "size_bytes": size,
                "size_mb": round(
                    size / 1024 / 1024,
                    4,
                ),
            }
        )

        log(
            f"✓ {filename} | "
            f"{len(chunk)} stocks | "
            f"{size / 1024 / 1024:.2f} MB"
        )

    # ========================================================
    # 8. Manifest
    # ========================================================

    section(
        "建立 manifest.json"
    )

    manifest = build_manifest(
        records,
        successful_records,
        failed_records,
        file_entries,
        generated_at,
    )

    validate_manifest(
        manifest
    )

    manifest_path = (
        temp_dir
        / "manifest.json"
    )

    save_json(
        manifest_path,
        manifest,
    )

    manifest_size = (
        manifest_path.stat().st_size
    )

    log(
        f"✓ manifest.json："
        f"{manifest_size} bytes"
    )

    # ========================================================
    # 9. 驗證完整暫存資料
    # ========================================================

    validate_output_directory(
        temp_dir,
        successful_records,
        manifest,
    )

    # ========================================================
    # 10. 確認沒有多餘檔案
    # ========================================================

    allowed_names = {
        "manifest.json"
    }

    for entry in file_entries:

        allowed_names.add(
            entry["file"]
        )

    actual_names = {
        path.name
        for path in temp_dir.iterdir()
        if path.is_file()
    }

    if actual_names != allowed_names:

        raise RuntimeError(
            "暫存目錄存在非預期檔案；"
            f"actual={sorted(actual_names)}; "
            f"expected={sorted(allowed_names)}"
        )

    # ========================================================
    # 11. 最終統計
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    total_rows = 0

    for record in successful_records:

        total_rows += len(
            record["rows"]
        )

    manifest["total_history_rows"] = (
        total_rows
    )

    manifest["elapsed_seconds"] = round(
        elapsed,
        2,
    )

    # 重新寫入含統計資訊的 manifest
    save_json(
        manifest_path,
        manifest,
    )

    validate_manifest(
        manifest
    )

    # ========================================================
    # 12. 再次確認
    # ========================================================

    validate_output_directory(
        temp_dir,
        successful_records,
        manifest,
    )

    # ========================================================
    # 13. 正式替換
    # ========================================================

    section(
        "正式替換 Data/prices/"
    )

    atomic_replace_directory(
        temp_dir,
        OUTPUT_DIR,
    )

    log(
        "✓ Data/prices/ 替換成功"
    )

    # ========================================================
    # 14. 最終結果
    # ========================================================

    section(
        "FETCH PRICES SUCCESS"
    )

    log(
        f"Version：{VERSION}"
    )

    log(
        f"Generated：{generated_at}"
    )

    log(
        f"Universe：{universe_total}"
    )

    log(
        f"Success：{success_count}"
    )

    log(
        f"Failed：{failed_count}"
    )

    log(
        f"Success Rate："
        f"{success_rate:.2%}"
    )

    log(
        f"Price Files："
        f"{len(file_entries)}"
    )

    log(
        f"Total History Rows："
        f"{total_rows}"
    )

    log(
        f"Elapsed："
        f"{elapsed:.2f} sec"
    )

    # --------------------------------------------------------
    # 失敗股票摘要
    # --------------------------------------------------------

    if failed_records:

        log("")
        log(
            f"⚠️ 有 {len(failed_records)} "
            f"檔股票取得失敗"
        )

        for failed in failed_records[
            :20
        ]:

            log(
                f"  - "
                f"{failed['symbol']}："
                f"{failed['error']}"
            )

        if len(failed_records) > 20:

            log(
                f"  ... "
                f"其餘 "
                f"{len(failed_records) - 20} "
                f"檔請查看 manifest.json"
            )

    return 0


# ============================================================
# Main
# ============================================================

def main():

    try:

        return build_prices()

    except KeyboardInterrupt:

        section(
            "FETCH PRICES ABORTED"
        )

        log(
            "⚠️ 使用者中止執行"
        )

        return 130

    except Exception as exc:

        section(
            "FETCH PRICES FAILED"
        )

        log(
            f"ERROR：{exc}"
        )

        # ----------------------------------------------------
        # 重要：
        # build_prices 使用暫存目錄，
        # 所以正式 Data/prices/ 不應被半成品污染。
        # ----------------------------------------------------

        if OUTPUT_DIR.exists():

            try:

                log(
                    "✓ 保留既有 "
                    "Data/prices/"
                )

                existing_files = list(
                    OUTPUT_DIR.iterdir()
                )

                log(
                    f"既有檔案/目錄數："
                    f"{len(existing_files)}"
                )

            except Exception:

                log(
                    "✓ 保留既有 "
                    "Data/prices/"
                )

        else:

            log(
                "ℹ️ 目前沒有既有 "
                "Data/prices/"
            )

        return 1


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
