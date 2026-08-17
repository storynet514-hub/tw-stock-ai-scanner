#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股 AI 選股系統
fetch_prices.py V2.0
============================================================

用途：
    1. 讀取 Data/universe.json
    2. 嚴格解析台股股票代號
    3. 取得 Yahoo Finance 歷史價格
    4. 建立 Data/prices.json

重要：
    - 不接受 1.0.0 / PENDING / TW 等錯誤代號
    - 不把 Universe metadata 當成股票
    - 純 4 碼股票代號預設為 .TW
    - 已有 .TW / .TWO 直接使用
    - API 失敗不轉成 0
    - 成功率低於 50% 不覆蓋舊 prices.json
============================================================
"""

import json
import sys
import time
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V2.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_FILE = DATA_DIR / "prices.json"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

TIMEOUT = 30
REQUEST_DELAY = 0.15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


# ============================================================
# 時間
# ============================================================

def now_tw():
    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).strftime("%Y-%m-%d %H:%M:%S")


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
# 股票代號驗證
# ============================================================

def normalize_symbol(value):
    """
    嚴格將資料轉成 Yahoo Finance symbol。

    合法：

        2330
        2330.TW
        00878
        006208.TW
        3481.TW
        1234.TWO

    非法：

        1.0.0
        PENDING
        TW
        version
        status
        market
    """

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    # --------------------------------------------------------
    # 已經是 Yahoo symbol
    # --------------------------------------------------------

    if text.endswith(".TW"):
        code = text[:-3]

        if re.fullmatch(r"\d{4,6}", code):
            return text

        return None

    if text.endswith(".TWO"):
        code = text[:-4]

        if re.fullmatch(r"\d{4,6}", code):
            return text

        return None

    # --------------------------------------------------------
    # 純數字股票代號
    # --------------------------------------------------------

    if re.fullmatch(r"\d{4,6}", text):
        return text + ".TW"

    return None


# ============================================================
# Universe 讀取
# ============================================================

def load_universe():

    section("讀取 Data/universe.json")

    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            f"找不到：{UNIVERSE_FILE}"
        )

    try:

        with open(
            UNIVERSE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"universe.json 無法讀取：{exc}"
        )

    log(
        f"Universe JSON：{UNIVERSE_FILE}"
    )

    return data


# ============================================================
# Universe 解析
# ============================================================

def extract_universe(data):

    section("嚴格解析 Universe")

    records = {}

    rejected = []

    def add_symbol(
        candidate,
        metadata=None
    ):

        yahoo_symbol = normalize_symbol(
            candidate
        )

        if yahoo_symbol is None:

            if candidate is not None:

                rejected.append(
                    str(candidate)
                )

            return

        metadata = (
            metadata
            if isinstance(metadata, dict)
            else {}
        )

        record = dict(metadata)

        record["symbol"] = yahoo_symbol

        records[yahoo_symbol] = record

    # ========================================================
    # LIST
    # ========================================================

    def parse_list(items):

        if not isinstance(items, list):
            return

        for item in items:

            # ----------------------------------------------
            # "2330"
            # ----------------------------------------------

            if isinstance(item, str):

                add_symbol(item)
                continue

            # ----------------------------------------------
            # {"symbol":"2330.TW"}
            # ----------------------------------------------

            if isinstance(item, dict):

                candidate = None

                for key in (
                    "symbol",
                    "ticker",
                    "stock_code",
                    "stock_id",
                    "code"
                ):

                    if key in item:

                        candidate = item.get(key)

                        if candidate is not None:
                            break

                if candidate is not None:

                    add_symbol(
                        candidate,
                        item
                    )

    # ========================================================
    # 直接 LIST
    # ========================================================

    if isinstance(data, list):

        parse_list(data)

    # ========================================================
    # DICT
    # ========================================================

    elif isinstance(data, dict):

        # ----------------------------------------------------
        # 明確股票清單欄位
        # ----------------------------------------------------

        list_keys = [
            "stocks",
            "listed",
            "otc",
            "tpex",
            "twse",
            "securities",
            "universe",
            "items",
            "data"
        ]

        for key in list_keys:

            value = data.get(key)

            if isinstance(value, list):

                parse_list(value)

            elif isinstance(value, dict):

                # 例如：
                #
                # "stocks": {
                #   "2330": {...},
                #   "1303": {...}
                # }

                for code, item in value.items():

                    if isinstance(item, dict):

                        add_symbol(
                            code,
                            item
                        )

                    else:

                        add_symbol(code)

        # ----------------------------------------------------
        # 如果完全沒找到，才檢查頂層 key
        # ----------------------------------------------------

        if not records:

            for key, value in data.items():

                candidate = normalize_symbol(key)

                if candidate:

                    if isinstance(value, dict):

                        add_symbol(
                            key,
                            value
                        )

                    else:

                        add_symbol(key)

    # ========================================================
    # 結果
    # ========================================================

    log(
        f"合法股票代號：{len(records)}"
    )

    if rejected:

        # 去重
        rejected_unique = list(
            dict.fromkeys(rejected)
        )

        log("")
        log(
            f"忽略非股票欄位："
            f"{len(rejected_unique)}"
        )

        for item in rejected_unique[:20]:

            log(
                f"  ✗ 忽略：{item}"
            )

    if not records:

        raise RuntimeError(
            "Universe 沒有解析出任何合法台股代號。"
        )

    # --------------------------------------------------------
    # 顯示
    # --------------------------------------------------------

    log("")
    log("前 20 個合法標的：")

    for i, (symbol, record) in enumerate(
        records.items(),
        start=1
    ):

        if i > 20:
            break

        name = record.get("name", "")

        if name:

            log(
                f"  {i:>2}. "
                f"{symbol} | {name}"
            )

        else:

            log(
                f"  {i:>2}. "
                f"{symbol}"
            )

    return records


# ============================================================
# Yahoo API
# ============================================================

def fetch_history(symbol):

    url = YAHOO_URL.format(
        symbol=symbol
    )

    start_timestamp = int(
        datetime.strptime(
            START_DATE,
            "%Y-%m-%d"
        ).replace(
            tzinfo=ZoneInfo("Asia/Taipei")
        ).timestamp()
    )

    params = {
        "period1": start_timestamp,
        "period2": int(time.time()),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true"
    }

    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    payload = response.json()

    chart = payload.get(
        "chart",
        {}
    )

    if chart.get("error"):

        raise RuntimeError(
            str(chart["error"])
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

    quote_list = indicators.get(
        "quote",
        []
    )

    if not timestamps or not quote_list:

        raise RuntimeError(
            "Yahoo 沒有價格資料"
        )

    quote = quote_list[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    adj_list = indicators.get(
        "adjclose",
        []
    )

    adjusted = []

    if adj_list:

        adjusted = adj_list[0].get(
            "adjclose",
            []
        )

    rows = []

    for i, timestamp in enumerate(
        timestamps
    ):

        close = (
            closes[i]
            if i < len(closes)
            else None
        )

        if close is None:
            continue

        date = datetime.fromtimestamp(
            timestamp,
            ZoneInfo("Asia/Taipei")
        ).strftime("%Y-%m-%d")

        rows.append({
            "date": date,

            "open": (
                opens[i]
                if i < len(opens)
                else None
            ),

            "high": (
                highs[i]
                if i < len(highs)
                else None
            ),

            "low": (
                lows[i]
                if i < len(lows)
                else None
            ),

            "close": close,

            "adj_close": (
                adjusted[i]
                if i < len(adjusted)
                and adjusted[i] is not None
                else close
            ),

            "volume": (
                volumes[i]
                if i < len(volumes)
                else None
            )
        })

    if not rows:

        raise RuntimeError(
            "沒有有效交易日"
        )

    return rows


# ============================================================
# 數值清理
# ============================================================

def clean_value(value):

    if value is None:
        return None

    try:

        value = float(value)

        if value != value:
            return None

        return round(value, 6)

    except Exception:

        return None


def clean_rows(rows):

    result = []

    for row in rows:

        volume = row.get(
            "volume"
        )

        if volume is not None:

            try:
                volume = int(volume)

            except Exception:
                volume = None

        result.append({

            "date": row["date"],

            "open": clean_value(
                row.get("open")
            ),

            "high": clean_value(
                row.get("high")
            ),

            "low": clean_value(
                row.get("low")
            ),

            "close": clean_value(
                row.get("close")
            ),

            "adj_close": clean_value(
                row.get("adj_close")
            ),

            "volume": volume
        })

    return result


# ============================================================
# 建立價格
# ============================================================

def build_prices(universe):

    section("開始取得全市場歷史價格")

    total = len(universe)

    log(
        f"待處理標的：{total}"
    )

    log(
        f"歷史起始日：{START_DATE}"
    )

    prices = {}

    failed = []

    success = 0

    for index, (
        symbol,
        meta
    ) in enumerate(
        universe.items(),
        start=1
    ):

        log(
            f"[{index}/{total}] "
            f"{symbol}"
        )

        try:

            rows = fetch_history(
                symbol
            )

            rows = clean_rows(
                rows
            )

            latest = rows[-1]

            prices[symbol] = {

                "symbol": symbol,

                "name": meta.get(
                    "name",
                    ""
                ),

                "market": meta.get(
                    "market",
                    ""
                ),

                "latest_date":
                    latest["date"],

                "latest_close":
                    latest["close"],

                "latest_volume":
                    latest["volume"],

                "data": rows
            }

            success += 1

            log(
                f"    ✓ {len(rows)} 筆"
                f" | {latest['date']}"
                f" | 收盤 {latest['close']}"
            )

        except Exception as exc:

            failed.append({

                "symbol": symbol,

                "error": str(exc)

            })

            log(
                f"    ✗ {exc}"
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
    universe,
    prices,
    success,
    failed
):

    section("價格資料驗證")

    total = len(universe)

    log(
        f"Universe：{total}"
    )

    log(
        f"成功：{success}"
    )

    log(
        f"失敗：{len(failed)}"
    )

    log(
        f"輸出：{len(prices)}"
    )

    if total == 0:

        raise RuntimeError(
            "Universe 為空"
        )

    if success == 0:

        raise RuntimeError(
            "全部股票取得失敗"
        )

    ratio = success / total

    log(
        f"成功率：{ratio * 100:.2f}%"
    )

    # 全市場資料量大時，
    # 允許少量個股 API 失敗。
    if ratio < 0.50:

        raise RuntimeError(
            "成功率低於 50%，"
            "停止覆蓋 prices.json"
        )

    for symbol, record in prices.items():

        if not record.get(
            "latest_date"
        ):

            raise RuntimeError(
                f"{symbol} 缺少 latest_date"
            )

        if record.get(
            "latest_close"
        ) is None:

            raise RuntimeError(
                f"{symbol} 缺少 latest_close"
            )

        if not record.get(
            "data"
        ):

            raise RuntimeError(
                f"{symbol} 沒有歷史價格"
            )

    log(
        "✓ 價格資料結構驗證通過"
    )


# ============================================================
# 寫入
# ============================================================

def save_prices(
    prices,
    failed
):

    section("寫入 Data/prices.json")

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {

        "version": VERSION,

        "generated_at": now_tw(),

        "source": (
            "Yahoo Finance"
        ),

        "history_start":
            START_DATE,

        "count":
            len(prices),

        "failed_count":
            len(failed),

        "failed_symbols":
            failed,

        "prices":
            prices
    }

    temp_file = PRICES_FILE.with_suffix(
        ".json.tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False
        )

    temp_file.replace(
        PRICES_FILE
    )

    size = (
        PRICES_FILE.stat().st_size
        / 1024
        / 1024
    )

    log(
        "✓ prices.json 建立成功"
    )

    log(
        f"股票數：{len(prices)}"
    )

    log(
        f"檔案大小：{size:.2f} MB"
    )

    log(
        f"位置：{PRICES_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

    log("")
    log("=" * 64)
    log(
        f"台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )
    log("=" * 64)

    log(
        f"開始時間：{now_tw()}"
    )

    try:

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        universe_data = load_universe()

        # ----------------------------------------------------
        # 2. 嚴格解析
        # ----------------------------------------------------

        universe = extract_universe(
            universe_data
        )

        # ----------------------------------------------------
        # 3. 抓取
        # ----------------------------------------------------

        (
            prices,
            success,
            failed
        ) = build_prices(
            universe
        )

        # ----------------------------------------------------
        # 4. 驗證
        # ----------------------------------------------------

        validate(
            universe,
            prices,
            success,
            failed
        )

        # ----------------------------------------------------
        # 5. 寫檔
        # ----------------------------------------------------

        save_prices(
            prices,
            failed
        )

        elapsed = (
            time.time() - start
        )

        log("")
        log("=" * 64)
        log(
            "✓ fetch_prices.py 完成"
        )
        log("=" * 64)

        log(
            f"成功：{success}"
        )

        log(
            f"失敗：{len(failed)}"
        )

        log(
            f"耗時：{elapsed:.1f} 秒"
        )

        if failed:

            log("")
            log(
                "⚠ 本次取得失敗標的："
            )

            for item in failed[:30]:

                log(
                    f"  {item['symbol']}"
                    f" → "
                    f"{item['error']}"
                )

            if len(failed) > 30:

                log(
                    f"  ... "
                    f"其餘 {len(failed)-30} 檔"
                )

        return 0

    except Exception as exc:

        log("")
        log("=" * 64)
        log(
            "❌ fetch_prices.py 執行失敗"
        )
        log("=" * 64)

        log(
            f"原因：{exc}"
        )

        if PRICES_FILE.exists():

            log(
                "⚠ 保留原有 prices.json"
            )

        return 1


if __name__ == "__main__":
    sys.exit(main())
