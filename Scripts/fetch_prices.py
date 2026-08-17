#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V3.0

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
5. 寫入 Data/prices.json

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

V2.x 問題：

Universe：
    1087 TWSE
    890 TPEx

如果只看到：
    1087 成功
    890 失敗

通常代表 TPEx 股票被錯誤轉成：

    1234.TW

而不是：

    1234.TWO

本版會根據 universe.json 的市場資訊
正確建立 Yahoo ticker。

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
Data/prices.json

============================================================
安全機制
============================================================

✓ 不會在成功率低於 80% 時覆蓋舊 prices.json
✓ 不會在資料異常時覆蓋舊 prices.json
✓ 寫入前檢查檔案大小
✓ GitHub 100 MB 限制
✓ Yahoo API 重試
"""

import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V3.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICES_FILE = DATA_DIR / "prices.json"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

# GitHub 單檔限制為 100 MB
# 預留安全空間
MAX_FILE_SIZE_MB = 99.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)

# 價格成功率最低要求
MIN_SUCCESS_RATE = 0.80

# 每檔最少歷史資料
MIN_HISTORY_ROWS = 100


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
# 市場判斷
# ============================================================

def detect_market(item):

    """
    從股票資料判斷：
    
    TWSE：
        上市
        TWSE
        TSE
        L

    TPEx：
        上櫃
        TPEx
        TPEX
        OTC
        TWO
        O
    """

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
            "TPEx",
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
            or "TPEx".upper() in text
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

    # --------------------------------------------------------
    # Yahoo ticker
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 純數字
    # --------------------------------------------------------

    if text.isdigit():

        if 4 <= len(text) <= 6:
            return text

    return None


# ============================================================
# 建立 Yahoo Symbol
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
# 從一筆 Universe 資料抽取股票
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
    # 沒有市場資訊時：
    #
    # 不盲目假設 TPEx。
    # 預設 TWSE。
    #
    # 但如果已經明確是 .TWO，
    # parse_record 前面會保留 market。
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
# 解析 Universe
# ============================================================

def extract_symbols(universe):

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

            # 如果第一次沒有名稱，
            # 第二次有名稱則補上
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

            # 先判斷本身是不是股票資料
            add_record(
                value
            )

            for key, child in value.items():

                # key 本身可能是：
                #
                # 2330
                # 2330.TW
                # 6488.TWO
                #

                key_code = extract_code(
                    key
                )

                if key_code:

                    # child 是 dict
                    if isinstance(
                        child,
                        dict
                    ):

                        add_record(
                            child,
                            key
                        )

                    # child 是名稱
                    elif isinstance(
                        child,
                        str
                    ):

                        # 如果 key 本身帶 .TWO
                        # 優先保留
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
                            record["market"]
                        )

                        if symbol:

                            record["symbol"] = symbol

                            add_record(
                                record
                            )

                    else:

                        add_record(
                            {
                                "symbol": key
                            },
                            key
                        )

                # 繼續遞迴
                if isinstance(
                    child,
                    (dict, list)
                ):

                    walk(child)

    walk(universe)

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    records = dict(
        sorted(
            records.items(),
            key=lambda x: (
                0
                if x[1].get("market") == "TW"
                else 1,
                x[1].get("code", "")
            )
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

    # --------------------------------------------------------
    # 顯示前 20
    # --------------------------------------------------------

    if records:

        log("")
        log("前 20 個合法標的：")

        for index, (
            symbol,
            record
        ) in enumerate(
            records.items(),
            start=1
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
        # 多留一天
        "period2": int(time.time()) + 86400,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "false",
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

            error = chart.get(
                "error"
            )

            if error:

                if isinstance(
                    error,
                    dict
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
                {}
            )

            quotes = indicators.get(
                "quote",
                []
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

                    date = datetime.fromtimestamp(
                        timestamp,
                        tz=timezone.utc
                    ).strftime(
                        "%Y-%m-%d"
                    )

                except Exception:
                    continue

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
                    ),
                    "volume": volume,
                })

            if not rows:

                raise RuntimeError(
                    "沒有有效交易資料"
                )

            # ------------------------------------------------
            # 去除重複日期
            # ------------------------------------------------

            unique = {}

            for row in rows:
                unique[
                    row["date"]
                ] = row

            rows = list(
                unique.values()
            )

            rows.sort(
                key=lambda x: x["date"]
            )

            return rows

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                log(
                    f"   ↻ 重試 "
                    f"{attempt + 1}/"
                    f"{MAX_RETRIES}："
                    f"{exc}"
                )

                time.sleep(
                    RETRY_DELAY * attempt
                )

    raise RuntimeError(
        str(last_error)
        if last_error
        else "未知錯誤"
    )


# ============================================================
# 取得全部價格
# ============================================================

def fetch_all(records):

    section("開始取得歷史價格")

    total = len(records)

    log(
        f"待處理標的：{total}"
    )

    log(
        f"歷史資料起始日：{START_DATE}"
    )

    prices = {}

    failed = []

    success = 0

    for index, (
        symbol,
        meta
    ) in enumerate(
        records.items(),
        start=1
    ):

        log(
            f"[{index}/{total}] "
            f"{symbol} | "
            f"{meta.get('name', '')} | "
            f"{meta.get('market', '')}"
        )

        try:

            rows = fetch_history(
                symbol
            )

            if len(rows) < MIN_HISTORY_ROWS:

                raise RuntimeError(
                    f"歷史資料不足："
                    f"{len(rows)} 筆"
                )

            latest = rows[-1]

            prices[symbol] = {
                "symbol": symbol,
                "code": meta.get(
                    "code",
                    symbol.split(".")[0]
                ),
                "name": meta.get(
                    "name",
                    ""
                ),
                "market": meta.get(
                    "market",
                    "TW"
                ),
                "latest_date": latest[
                    "date"
                ],
                "latest_close": latest[
                    "close"
                ],
                "latest_volume": latest[
                    "volume"
                ],
                "data": rows,
            }

            success += 1

            log(
                f"   ✓ "
                f"{len(rows)} 筆"
                f" | 最新："
                f"{latest['date']}"
                f" | 收盤："
                f"{latest['close']}"
            )

        except Exception as exc:

            failed.append({
                "symbol": symbol,
                "market": meta.get(
                    "market"
                ),
                "error": str(exc),
            })

            log(
                f"   ✗ {exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    return (
        prices,
        success,
        failed
    )


# ============================================================
# 驗證
# ============================================================

def validate(
    records,
    prices,
    failed
):

    section("價格資料驗證")

    total = len(records)

    success = len(prices)

    failure = len(failed)

    log(
        f"Universe：{total}"
    )

    log(
        f"成功：{success}"
    )

    log(
        f"失敗：{failure}"
    )

    if total == 0:

        raise RuntimeError(
            "Universe 沒有股票"
        )

    if success == 0:

        raise RuntimeError(
            "完全沒有取得價格資料"
        )

    ratio = (
        success / total
    )

    log(
        f"成功率："
        f"{ratio * 100:.2f}%"
    )

    # --------------------------------------------------------
    # 市場別成功率
    # --------------------------------------------------------

    twse_total = sum(
        1
        for item in records.values()
        if item.get("market") == "TW"
    )

    tpex_total = sum(
        1
        for item in records.values()
        if item.get("market") == "TWO"
    )

    twse_success = sum(
        1
        for symbol in prices
        if prices[symbol].get("market") == "TW"
    )

    tpex_success = sum(
        1
        for symbol in prices
        if prices[symbol].get("market") == "TWO"
    )

    log("")
    log("市場別價格成功率")

    if twse_total:

        log(
            f"TWSE："
            f"{twse_success}/"
            f"{twse_total} "
            f"("
            f"{twse_success / twse_total * 100:.2f}%"
            f")"
        )

    if tpex_total:

        log(
            f"TPEx："
            f"{tpex_success}/"
            f"{tpex_total} "
            f"("
            f"{tpex_success / tpex_total * 100:.2f}%"
            f")"
        )

    # --------------------------------------------------------
    # 全市場成功率
    # --------------------------------------------------------

    if ratio < MIN_SUCCESS_RATE:

        raise RuntimeError(
            f"價格成功率只有 "
            f"{ratio * 100:.2f}%，"
            f"低於 "
            f"{MIN_SUCCESS_RATE * 100:.0f}%，"
            f"停止更新。"
        )

    # --------------------------------------------------------
    # 檢查資料筆數
    # --------------------------------------------------------

    invalid = []

    for symbol, record in prices.items():

        data = record.get(
            "data",
            []
        )

        if len(data) < MIN_HISTORY_ROWS:

            invalid.append(
                symbol
            )

    if invalid:

        preview = ", ".join(
            invalid[:20]
        )

        raise RuntimeError(
            "以下股票歷史資料不足："
            + preview
        )

    # --------------------------------------------------------
    # 檢查必要欄位
    # --------------------------------------------------------

    malformed = []

    required = {
        "date",
        "high",
        "low",
        "close",
        "volume",
    }

    for symbol, record in prices.items():

        rows = record.get(
            "data",
            []
        )

        for row in rows:

            if not required.issubset(
                row.keys()
            ):

                malformed.append(
                    symbol
                )

                break

    if malformed:

        raise RuntimeError(
            "資料欄位不完整："
            + ", ".join(
                malformed[:20]
            )
        )

    log("")
    log(
        "✓ 價格資料驗證通過"
    )


# ============================================================
# 建立輸出 JSON
# ============================================================

def build_output(
    records,
    prices,
    failed
):

    now = datetime.now(
        timezone.utc
    ).astimezone()

    twse_count = sum(
        1
        for item in prices.values()
        if item.get("market") == "TW"
    )

    tpex_count = sum(
        1
        for item in prices.values()
        if item.get("market") == "TWO"
    )

    output = {
        "version": VERSION,
        "generated_at": now.isoformat(
            timespec="seconds"
        ),
        "source": "Yahoo Finance",
        "history_start": START_DATE,
        "count": len(prices),
        "twse_count": twse_count,
        "tpex_count": tpex_count,
        "failed_count": len(failed),
        "prices": prices,
    }

    return output


# ============================================================
# Compact JSON
# ============================================================

def serialize_json(data):

    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(
            ",",
            ":"
        ),
        allow_nan=False
    )


# ============================================================
# 檔案大小檢查
# ============================================================

def check_file_size(
    content
):

    size = len(
        content.encode(
            "utf-8"
        )
    )

    size_mb = (
        size /
        1024 /
        1024
    )

    log("")
    log(
        "prices.json 預估大小："
        f"{size_mb:.2f} MB"
    )

    if size >= MAX_FILE_SIZE_BYTES:

        raise RuntimeError(
            "prices.json 預估大小 "
            f"{size_mb:.2f} MB，"
            f"已接近或超過 GitHub "
            "100 MB 單檔限制。"
        )

    log(
        f"✓ 檔案大小低於 "
        f"{MAX_FILE_SIZE_MB:.0f} MB 安全上限"
    )

    return size


# ============================================================
# 原子寫入
# ============================================================

def write_output(
    content
):

    section(
        "寫入 Data/prices.json"
    )

    temp_file = (
        DATA_DIR /
        "prices.json.tmp"
    )

    try:

        with temp_file.open(
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                content
            )

            f.flush()

        # ----------------------------------------------------
        # 確認暫存檔存在
        # ----------------------------------------------------

        if not temp_file.exists():

            raise RuntimeError(
                "暫存 prices.json 建立失敗"
            )

        # ----------------------------------------------------
        # 原子替換
        # ----------------------------------------------------

        temp_file.replace(
            PRICES_FILE
        )

    except Exception:

        if temp_file.exists():

            try:
                temp_file.unlink()
            except Exception:
                pass

        raise

    log(
        f"✓ 已寫入："
        f"{PRICES_FILE}"
    )


# ============================================================
# 顯示失敗
# ============================================================

def show_failures(
    failed
):

    if not failed:
        return

    section(
        "價格取得失敗清單"
    )

    for item in failed[:50]:

        log(
            f"{item.get('symbol')} "
            f"| "
            f"{item.get('market')} "
            f"| "
            f"{item.get('error')}"
        )

    if len(failed) > 50:

        log(
            f"... "
            f"另外 {len(failed) - 50} 檔"
        )


# ============================================================
# 主程式
# ============================================================

def main():

    section(
        f"台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    old_exists = PRICES_FILE.exists()

    try:

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        universe = load_universe()

        # ----------------------------------------------------
        # 2. Parse
        # ----------------------------------------------------

        records = extract_symbols(
            universe
        )

        if not records:

            raise RuntimeError(
                "Universe 沒有解析出任何"
                "合法台股代號。"
            )

        # ----------------------------------------------------
        # 3. 取得價格
        # ----------------------------------------------------

        (
            prices,
            success,
            failed
        ) = fetch_all(
            records
        )

        # ----------------------------------------------------
        # 4. 驗證
        # ----------------------------------------------------

        validate(
            records,
            prices,
            failed
        )

        # ----------------------------------------------------
        # 5. 建立輸出
        # ----------------------------------------------------

        output = build_output(
            records,
            prices,
            failed
        )

        # ----------------------------------------------------
        # 6. Compact JSON
        # ----------------------------------------------------

        content = serialize_json(
            output
        )

        # ----------------------------------------------------
        # 7. 檔案大小
        # ----------------------------------------------------

        check_file_size(
            content
        )

        # ----------------------------------------------------
        # 8. 寫入
        # ----------------------------------------------------

        write_output(
            content
        )

        # ----------------------------------------------------
        # 9. 顯示失敗
        # ----------------------------------------------------

        show_failures(
            failed
        )

        # ----------------------------------------------------
        # 10. 最終結果
        # ----------------------------------------------------

        section(
            "fetch_prices.py 完成"
        )

        log(
            f"成功：{success}"
        )

        log(
            f"失敗：{len(failed)}"
        )

        log(
            f"輸出：{PRICES_FILE}"
        )

        if old_exists:

            log(
                "✓ 舊 prices.json "
                "已成功替換"
            )

        else:

            log(
                "✓ 建立新的 prices.json"
            )

        return 0

    except Exception as exc:

        section(
            "❌ fetch_prices.py 執行失敗"
        )

        log(
            f"原因：{exc}"
        )

        if old_exists:

            log(
                "⚠️ 保留既有 prices.json"
            )

        else:

            log(
                "⚠️ 尚未建立 prices.json"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
