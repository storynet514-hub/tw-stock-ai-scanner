#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V4.1

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
10. 驗證 manifest
11. 全部驗證通過後才替換舊 Data/prices/
12. 不再產生 Data/prices.json

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
安全機制
============================================================

✓ Universe 必須有效
✓ Universe 不得為空
✓ 股票數量必須合理
✓ 成功率低於 80% 不更新
✓ 任一股票歷史資料不足不計入成功
✓ 任一分檔寫入失敗不更新
✓ 任一分檔驗證失敗不更新
✓ Manifest 驗證失敗不更新
✓ 單檔超過 80 MB 不更新
✓ 使用暫存目錄建立完整資料
✓ 全部驗證通過後才替換正式 Data/prices/
✓ 不會產生半成品正式資料
✓ 任何未預期錯誤 exit code 1

============================================================
V4.1 修正
============================================================

修正：

    ERROR：'str' object has no attribute 'get'

原因：
    manifest 的 files 資料結構在建立與驗證階段不一致。

V4.1 統一：

    manifest["files"]

每一筆都是：

    {
        "file": "prices_001.json",
        "stocks": 100,
        "size_bytes": 123456,
        "size_mb": 6.32
    }

所有 manifest 驗證均針對 dict 處理。
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

VERSION = "V4.1"

SCHEMA_VERSION = "prices-v4.1"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

STOCKS_PER_FILE = 100

MIN_SUCCESS_RATE = 0.80

MIN_HISTORY_ROWS = 100

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
# Universe
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

    items = data.get("items")

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json 缺少有效 items"
        )

    if not items:

        raise RuntimeError(
            "universe.json items 為空"
        )

    log(
        f"Universe JSON：{UNIVERSE_FILE}"
    )

    log(
        f"Universe total 欄位："
        f"{data.get('total')}"
    )

    log(
        f"Universe items："
        f"{len(items)}"
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

    if text.isdigit():

        if 4 <= len(text) <= 6:
            return text

    return None


# ============================================================
# Yahoo Symbol
# ============================================================

def build_yahoo_symbol(
    code,
    market=None,
):

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

    if isinstance(item, str):

        code = extract_code(
            item
        )

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

    if code is None:

        code = extract_code(
            fallback_code
        )

    if code is None:
        return None

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
# Universe 解析
# ============================================================

def extract_symbols(universe):

    section(
        "嚴格解析 Universe"
    )

    records = {}

    items = universe.get(
        "items",
        [],
    )

    if not isinstance(items, list):

        raise RuntimeError(
            "Universe items 必須是 list"
        )

    for item in items:

        parsed = parse_record(
            item
        )

        if parsed is None:
            continue

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

    if not records:

        raise RuntimeError(
            "Universe 沒有解析出任何股票"
        )

    expected_total = universe.get(
        "total"
    )

    if isinstance(
        expected_total,
        int,
    ):

        if expected_total != len(records):

            log(
                "⚠️ 注意："
                f"Universe total={expected_total}，"
                f"解析後={len(records)}"
            )

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
                            close,
                            4,
                        ),
                        "volume": volume,
                    }
                )

            if len(rows) < MIN_HISTORY_ROWS:

                raise RuntimeError(
                    "歷史資料不足："
                    f"{len(rows)} rows"
                )

            rows.sort(
                key=lambda x: x["date"]
            )

            return rows

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        f"{symbol} 取得歷史資料失敗："
        f"{last_error}"
    )


# ============================================================
# 驗證單一股票歷史資料
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
            f"{symbol}: history 不足"
        )

    previous_date = None

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: history row 不是 object"
            )

        required = [
            "date",
            "high",
            "low",
            "close",
            "volume",
        ]

        for key in required:

            if key not in row:

                raise RuntimeError(
                    f"{symbol}: 缺少 {key}"
                )

        date = row["date"]

        high = safe_float(
            row["high"]
        )

        low = safe_float(
            row["low"]
        )

        close = safe_float(
            row["close"]
        )

        volume = safe_int(
            row["volume"]
        )

        if not isinstance(
            date,
            str,
        ):

            raise RuntimeError(
                f"{symbol}: date 格式錯誤"
            )

        if (
            high is None
            or low is None
            or close is None
        ):

            raise RuntimeError(
                f"{symbol}: 價格資料無效"
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

        if high < low:

            raise RuntimeError(
                f"{symbol}: high < low"
            )

        if volume < 0:

            raise RuntimeError(
                f"{symbol}: volume < 0"
            )

        if (
            previous_date is not None
            and date < previous_date
        ):

            raise RuntimeError(
                f"{symbol}: 日期未排序"
            )

        previous_date = date

    return True


# ============================================================
# 建立股票資料物件
# ============================================================

def build_stock_record(
    record,
    history,
):

    return {
        "symbol": record["symbol"],
        "code": record["code"],
        "market": record["market"],
        "name": record.get(
            "name",
            "",
        ),
        "history": history,
    }


# ============================================================
# 建立分檔
# ============================================================

def build_price_files(
    temp_dir,
    successful_records,
):

    section(
        "建立價格分檔"
    )

    items = list(
        successful_records.items()
    )

    items.sort(
        key=lambda x: x[0]
    )

    chunks = []

    for start in range(
        0,
        len(items),
        STOCKS_PER_FILE,
    ):

        chunks.append(
            items[
                start:
                start + STOCKS_PER_FILE
            ]
        )

    if not chunks:

        raise RuntimeError(
            "沒有成功股票可建立分檔"
        )

    manifest_files = []

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = (
            Path(temp_dir)
            / filename
        )

        stocks = {}

        for symbol, data in chunk:

            stocks[symbol] = data

        payload = {
            "version": VERSION,
            "schema_version": SCHEMA_VERSION,
            "file": filename,
            "stocks": len(stocks),
            "generated_at": (
                datetime.now(
                    timezone.utc
                )
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                )
            ),
            "data": stocks,
        }

        save_json(
            path,
            payload,
        )

        size_bytes = path.stat().st_size

        size_mb = (
            size_bytes
            / 1024
            / 1024
        )

        if (
            size_bytes
            > MAX_FILE_SIZE_BYTES
        ):

            raise RuntimeError(
                f"{filename} 超過安全大小："
                f"{size_mb:.2f} MB"
            )

        manifest_item = {
            "file": filename,
            "stocks": len(stocks),
            "size_bytes": size_bytes,
            "size_mb": round(
                size_mb,
                2,
            ),
        }

        manifest_files.append(
            manifest_item
        )

        log(
            f"✓ {filename} | "
            f"{len(stocks)} stocks | "
            f"{size_mb:.2f} MB"
        )

    return manifest_files


# ============================================================
# 建立 Manifest
# ============================================================

def build_manifest(
    universe_total,
    successful_records,
    failed_records,
    manifest_files,
):

    section(
        "建立 manifest.json"
    )

    success_count = len(
        successful_records
    )

    failed_count = len(
        failed_records
    )

    total = (
        success_count
        + failed_count
    )

    if total <= 0:

        raise RuntimeError(
            "Manifest total 不得為 0"
        )

    success_rate = (
        success_count / total
    )

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

    manifest = {
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "universe_total": universe_total,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": round(
            success_rate,
            6,
        ),
        "file_count": len(
            manifest_files
        ),
        "files": manifest_files,
    }

    return manifest


# ============================================================
# Manifest 驗證
# ============================================================

def validate_manifest(
    manifest,
    successful_records,
    failed_records,
):

    section(
        "驗證 manifest.json"
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest 必須是 object"
        )

    files = manifest.get(
        "files"
    )

    if not isinstance(
        files,
        list,
    ):

        raise RuntimeError(
            "manifest.files 必須是 list"
        )

    file_count = manifest.get(
        "file_count"
    )

    if file_count != len(files):

        raise RuntimeError(
            "manifest.file_count 不一致"
        )

    success_count = manifest.get(
        "success_count"
    )

    failed_count = manifest.get(
        "failed_count"
    )

    universe_total = manifest.get(
        "universe_total"
    )

    if success_count != len(
        successful_records
    ):

        raise RuntimeError(
            "manifest.success_count 不一致"
        )

    if failed_count != len(
        failed_records
    ):

        raise RuntimeError(
            "manifest.failed_count 不一致"
        )

    if universe_total != (
        success_count
        + failed_count
    ):

        raise RuntimeError(
            "manifest universe_total 不一致"
        )

    calculated_rate = (
        success_count
        / universe_total
        if universe_total
        else 0
    )

    manifest_rate = manifest.get(
        "success_rate"
    )

    if (
        not isinstance(
            manifest_rate,
            (int, float),
        )
    ):

        raise RuntimeError(
            "manifest.success_rate 格式錯誤"
        )

    if abs(
        float(manifest_rate)
        - calculated_rate
    ) > 0.00001:

        raise RuntimeError(
            "manifest.success_rate 不一致"
        )

    calculated_stocks = 0

    for item in files:

        # ----------------------------------------------------
        # V4.1 關鍵修正：
        # files 裡每一筆必須是 dict
        # ----------------------------------------------------

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "manifest.files "
                "存在非 object 項目"
            )

        filename = item.get(
            "file"
        )

        stocks = item.get(
            "stocks"
        )

        size_bytes = item.get(
            "size_bytes"
        )

        size_mb = item.get(
            "size_mb"
        )

        if not isinstance(
            filename,
            str,
        ) or not filename:

            raise RuntimeError(
                "manifest file 欄位錯誤"
            )

        if not isinstance(
            stocks,
            int,
        ):

            raise RuntimeError(
                f"{filename}: stocks 欄位錯誤"
            )

        if stocks <= 0:

            raise RuntimeError(
                f"{filename}: stocks <= 0"
            )

        if not isinstance(
            size_bytes,
            int,
        ):

            raise RuntimeError(
                f"{filename}: size_bytes 欄位錯誤"
            )

        if size_bytes <= 0:

            raise RuntimeError(
                f"{filename}: size_bytes <= 0"
            )

        if not isinstance(
            size_mb,
            (int, float),
        ):

            raise RuntimeError(
                f"{filename}: size_mb 欄位錯誤"
            )

        if size_bytes > MAX_FILE_SIZE_BYTES:

            raise RuntimeError(
                f"{filename}: 超過安全大小"
            )

        calculated_stocks += stocks

    if calculated_stocks != success_count:

        raise RuntimeError(
            "Manifest 分檔股票數量與成功數不一致："
            f"{calculated_stocks} != "
            f"{success_count}"
        )

    if success_count <= 0:

        raise RuntimeError(
            "Manifest success_count <= 0"
        )

    success_rate = (
        success_count
        / universe_total
    )

    if success_rate < MIN_SUCCESS_RATE:

        raise RuntimeError(
            "成功率低於安全門檻："
            f"{success_rate:.2%}"
        )

    log(
        f"✓ Manifest files："
        f"{file_count}"
    )

    log(
        f"✓ Manifest stocks："
        f"{calculated_stocks}"
    )

    log(
        f"✓ Success rate："
        f"{success_rate:.2%}"
    )

    log(
        "✓ Manifest validation passed"
    )

    return True


# ============================================================
# 驗證所有價格分檔
# ============================================================

def validate_price_files(
    temp_dir,
    manifest,
    successful_records,
):

    section(
        "驗證所有價格分檔"
    )

    files = manifest.get(
        "files"
    )

    expected_symbols = set(
        successful_records.keys()
    )

    actual_symbols = set()

    for item in files:

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "分檔 manifest item "
                "不是 object"
            )

        filename = item.get(
            "file"
        )

        expected_stocks = item.get(
            "stocks"
        )

        path = (
            Path(temp_dir)
            / filename
        )

        if not path.exists():

            raise RuntimeError(
                f"找不到分檔：{filename}"
            )

        actual_size = path.stat().st_size

        if (
            actual_size
            > MAX_FILE_SIZE_BYTES
        ):

            raise RuntimeError(
                f"{filename} 超過安全大小"
            )

        try:

            payload = load_json(
                path
            )

        except Exception as exc:

            raise RuntimeError(
                f"{filename} JSON 讀取失敗："
                f"{exc}"
            ) from exc

        if not isinstance(
            payload,
            dict,
        ):

            raise RuntimeError(
                f"{filename}: "
                "頂層不是 object"
            )

        if payload.get(
            "file"
        ) != filename:

            raise RuntimeError(
                f"{filename}: file 欄位錯誤"
            )

        data = payload.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):

            raise RuntimeError(
                f"{filename}: data 不是 object"
            )

        if payload.get(
            "stocks"
        ) != len(data):

            raise RuntimeError(
                f"{filename}: stocks 數量不一致"
            )

        if len(data) != expected_stocks:

            raise RuntimeError(
                f"{filename}: "
                f"預期 {expected_stocks}，"
                f"實際 {len(data)}"
            )

        for symbol, stock in data.items():

            if not isinstance(
                symbol,
                str,
            ):

                raise RuntimeError(
                    f"{filename}: symbol 錯誤"
                )

            if symbol in actual_symbols:

                raise RuntimeError(
                    f"股票重複：{symbol}"
                )

            actual_symbols.add(
                symbol
            )

            if symbol not in expected_symbols:

                raise RuntimeError(
                    f"{filename}: "
                    f"出現非成功股票 {symbol}"
                )

            if not isinstance(
                stock,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol}: "
                    "stock record 不是 object"
                )

            if stock.get(
                "symbol"
            ) != symbol:

                raise RuntimeError(
                    f"{symbol}: symbol 不一致"
                )

            history = stock.get(
                "history"
            )

            validate_history(
                symbol,
                history,
            )

        log(
            f"✓ {filename} "
            f"validated | "
            f"{len(data)} stocks"
        )

    if actual_symbols != expected_symbols:

        missing = (
            expected_symbols
            - actual_symbols
        )

        extra = (
            actual_symbols
            - expected_symbols
        )

        raise RuntimeError(
            "分檔股票集合不一致；"
            f"missing={len(missing)}；"
            f"extra={len(extra)}"
        )

    log(
        f"✓ 全部分檔驗證通過："
        f"{len(actual_symbols)} stocks"
    )

    return True


# ============================================================
# 驗證暫存目錄完整性
# ============================================================

def validate_staging_area(
    temp_dir,
    manifest,
    successful_records,
    failed_records,
):

    section(
        "最終資料完整性驗證"
    )

    validate_manifest(
        manifest,
        successful_records,
        failed_records,
    )

    validate_price_files(
        temp_dir,
        manifest,
        successful_records,
    )

    expected_files = {
        item["file"]
        for item in manifest["files"]
    }

    actual_files = {
        path.name
        for path in Path(
            temp_dir
        ).glob(
            "prices_*.json"
        )
    }

    if actual_files != expected_files:

        missing = (
            expected_files
            - actual_files
        )

        extra = (
            actual_files
            - expected_files
        )

        raise RuntimeError(
            "暫存目錄分檔集合不一致；"
            f"missing={missing}; "
            f"extra={extra}"
        )

    manifest_path = (
        Path(temp_dir)
        / "manifest.json"
    )

    if not manifest_path.exists():

        raise RuntimeError(
            "manifest.json 不存在"
        )

    log(
        "✓ staging area 完整性驗證通過"
    )

    return True


# ============================================================
# 原子替換 Data/prices
# ============================================================

def replace_output_directory(
    staging_dir,
):

    section(
        "替換正式 Data/prices/"
    )

    OUTPUT_DIR.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    backup_dir = None

    # --------------------------------------------------------
    # 如果已有正式資料，先改名成 backup
    # --------------------------------------------------------

    if OUTPUT_DIR.exists():

        backup_dir = (
            OUTPUT_DIR.parent
            / (
                ".prices_backup_"
                + str(
                    int(
                        time.time()
                    )
                )
            )
        )

        log(
            f"建立舊資料備份："
            f"{backup_dir}"
        )

        OUTPUT_DIR.rename(
            backup_dir
        )

    try:

        shutil.move(
            str(staging_dir),
            str(OUTPUT_DIR),
        )

    except Exception:

        # ----------------------------------------------------
        # 新資料替換失敗時盡可能恢復舊資料
        # ----------------------------------------------------

        if (
            not OUTPUT_DIR.exists()
            and backup_dir is not None
            and backup_dir.exists()
        ):

            backup_dir.rename(
                OUTPUT_DIR
            )

        raise

    # --------------------------------------------------------
    # 新資料成功後刪除 backup
    # --------------------------------------------------------

    if (
        backup_dir is not None
        and backup_dir.exists()
    ):

        try:

            shutil.rmtree(
                backup_dir
            )

        except Exception as exc:

            log(
                "⚠️ 舊資料備份刪除失敗："
                f"{exc}"
            )

    log(
        "✓ Data/prices/ 替換完成"
    )


# ============================================================
# 顯示失敗股票
# ============================================================

def print_failed_records(
    failed_records,
):

    if not failed_records:
        return

    log("")

    log(
        f"失敗股票："
        f"{len(failed_records)}"
    )

    for symbol, reason in list(
        failed_records.items()
    )[:30]:

        log(
            f"  ✗ {symbol}："
            f"{reason}"
        )

    if len(failed_records) > 30:

        log(
            f"  ... 其餘 "
            f"{len(failed_records) - 30} 檔"
        )


# ============================================================
# 主流程
# ============================================================

def main():

    section(
        "台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
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

    staging_dir = None

    try:

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ====================================================
        # 1. Universe
        # ====================================================

        universe = load_universe()

        records = extract_symbols(
            universe
        )

        universe_total = len(records)

        if universe_total <= 0:

            raise RuntimeError(
                "Universe total <= 0"
            )

        # ====================================================
        # 2. 建立暫存目錄
        # ====================================================

        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=".prices_staging_",
                dir=DATA_DIR,
            )
        )

        log("")
        log(
            f"暫存目錄："
            f"{staging_dir}"
        )

        # ====================================================
        # 3. 取得 Yahoo 資料
        # ====================================================

        section(
            "開始取得 Yahoo Finance 歷史資料"
        )

        successful_records = {}

        failed_records = {}

        processed = 0

        for symbol, record in records.items():

            processed += 1

            try:

                history = fetch_history(
                    symbol
                )

                validate_history(
                    symbol,
                    history,
                )

                successful_records[
                    symbol
                ] = build_stock_record(
                    record,
                    history,
                )

                log(
                    f"[{processed}/{universe_total}] "
                    f"✓ {symbol} "
                    f"{len(history)} rows"
                )

            except Exception as exc:

                failed_records[
                    symbol
                ] = str(exc)

                log(
                    f"[{processed}/{universe_total}] "
                    f"✗ {symbol} "
                    f"{exc}"
                )

            time.sleep(
                REQUEST_DELAY
            )

        # ====================================================
        # 4. 統計
        # ====================================================

        section(
            "資料取得統計"
        )

        success_count = len(
            successful_records
        )

        failed_count = len(
            failed_records
        )

        total_count = (
            success_count
            + failed_count
        )

        success_rate = (
            success_count
            / total_count
            if total_count
            else 0
        )

        log(
            f"Universe："
            f"{total_count}"
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

        print_failed_records(
            failed_records
        )

        # ====================================================
        # 5. 成功率安全門
        # ====================================================

        if (
            success_rate
            < MIN_SUCCESS_RATE
        ):

            raise RuntimeError(
                "成功率低於安全門檻："
                f"{success_rate:.2%} "
                f"< "
                f"{MIN_SUCCESS_RATE:.2%}"
            )

        if success_count <= 0:

            raise RuntimeError(
                "沒有任何成功股票"
            )

        # ====================================================
        # 6. 建立價格分檔
        # ====================================================

        manifest_files = build_price_files(
            staging_dir,
            successful_records,
        )

        # ====================================================
        # 7. 建立 Manifest
        # ====================================================

        manifest = build_manifest(
            universe_total,
            successful_records,
            failed_records,
            manifest_files,
        )

        manifest_path = (
            staging_dir
            / "manifest.json"
        )

        save_json(
            manifest_path,
            manifest,
        )

        # ====================================================
        # 8. 最終驗證
        # ====================================================

        validate_staging_area(
            staging_dir,
            manifest,
            successful_records,
            failed_records,
        )

        # ====================================================
        # 9. 正式替換
        # ====================================================

        replace_output_directory(
            staging_dir
        )

        staging_dir = None

        # ====================================================
        # 10. 最終成功訊息
        # ====================================================

        section(
            "FETCH PRICES SUCCESS"
        )

        log(
            f"✓ Version：{VERSION}"
        )

        log(
            f"✓ Universe："
            f"{universe_total}"
        )

        log(
            f"✓ 成功："
            f"{success_count}"
        )

        log(
            f"✓ 失敗："
            f"{failed_count}"
        )

        log(
            f"✓ 成功率："
            f"{success_rate:.2%}"
        )

        log(
            f"✓ 分檔："
            f"{len(manifest_files)}"
        )

        log(
            f"✓ Output："
            f"{OUTPUT_DIR}"
        )

        return 0

    except KeyboardInterrupt:

        log("")

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
        # 清理 staging
        # ----------------------------------------------------

        if (
            staging_dir is not None
            and staging_dir.exists()
        ):

            try:

                shutil.rmtree(
                    staging_dir
                )

                log(
                    "✓ 已清除暫存資料"
                )

            except Exception as cleanup_exc:

                log(
                    "⚠️ 暫存資料清除失敗："
                    f"{cleanup_exc}"
                )

        # ----------------------------------------------------
        # 正式資料完全不動
        # ----------------------------------------------------

        if OUTPUT_DIR.exists():

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
