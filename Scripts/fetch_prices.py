#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V3.0

============================================================
本程式責任
============================================================

1. 讀取 Data/universe.json
2. 取得台股全市場歷史價格
3. 保留技術指標所需資料：
   - high
   - low
   - close
   - volume
4. 寫入 Data/prices.json

============================================================
資料用途
============================================================

KD
    → high / low / close

MACD
    → close

RSI
    → close

MA5 / MA20 / 60日高低點
    → close

成交量條件
    → volume

============================================================
刻意移除
============================================================

❌ open
❌ adj_close

以降低 prices.json 大小。

============================================================
資料流程
============================================================

Data/universe.json
        ↓
fetch_prices.py
        ↓
Data/prices.json
"""

import json
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

REQUEST_DELAY = 0.08

MAX_FILE_SIZE_MB = 99.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
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
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
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

    return int(dt.timestamp())


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
            "universe.json 格式錯誤"
        )

    log(
        f"Universe JSON：{UNIVERSE_FILE}"
    )

    return data


# ============================================================
# 股票代號正規化
# ============================================================

def normalize_symbol(value):

    if value is None:
        return None

    value = str(value).strip().upper()

    if not value:
        return None

    # 已經是 Yahoo 格式
    if value.endswith(".TW"):
        code = value[:-3]

        if code.isdigit():
            return value

        return None

    if value.endswith(".TWO"):
        code = value[:-4]

        if code.isdigit():
            return value

        return None

    # 純數字台股代號
    if value.isdigit():

        if 4 <= len(value) <= 6:

            return value + ".TW"

    return None


# ============================================================
# 從 Universe 找股票
# ============================================================

def extract_symbols(universe):

    section("解析 Universe")

    records = {}

    def add_record(item, fallback_symbol=None):

        symbol = None
        name = ""

        if isinstance(item, str):

            symbol = item

        elif isinstance(item, dict):

            possible_keys = [
                "symbol",
                "ticker",
                "code",
                "stock_id",
                "stock_code",
                "證券代號",
                "有價證券代號"
            ]

            for key in possible_keys:

                value = item.get(key)

                if value is not None:

                    symbol = str(value).strip()

                    if symbol:
                        break

            name_keys = [
                "name",
                "stock_name",
                "名稱",
                "證券名稱"
            ]

            for key in name_keys:

                value = item.get(key)

                if value:

                    name = str(value).strip()

                    break

        if symbol is None:
            symbol = fallback_symbol

        yahoo_symbol = normalize_symbol(symbol)

        if yahoo_symbol is None:
            return

        if yahoo_symbol not in records:

            records[yahoo_symbol] = {
                "symbol": yahoo_symbol,
                "code": yahoo_symbol.split(".")[0],
                "name": name
            }

    # --------------------------------------------------------
    # 遞迴解析 Universe
    # --------------------------------------------------------

    def walk(value, fallback=None):

        if isinstance(value, list):

            for item in value:

                walk(item)

            return

        if isinstance(value, dict):

            # 如果本身就是股票資料
            add_record(
                value,
                fallback
            )

            for key, child in value.items():

                # key 本身可能就是股票代號
                possible = normalize_symbol(key)

                if possible:

                    if isinstance(child, dict):

                        add_record(
                            child,
                            key
                        )

                    elif isinstance(child, str):

                        add_record(
                            {
                                "symbol": key,
                                "name": child
                            }
                        )

                    else:

                        add_record(
                            {
                                "symbol": key
                            }
                        )

                # 繼續往下找
                if isinstance(child, (dict, list)):

                    walk(
                        child,
                        None
                    )

    walk(universe)

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    records = dict(
        sorted(
            records.items(),
            key=lambda x: x[0]
        )
    )

    log(
        f"合法股票代號：{len(records)}"
    )

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
                f"{record.get('name', '')}"
            )

    return records


# ============================================================
# 取得 Yahoo 歷史資料
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

    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    try:

        payload = response.json()

    except Exception as exc:

        raise RuntimeError(
            f"Yahoo JSON 解析失敗：{exc}"
        ) from exc

    chart = payload.get(
        "chart",
        {}
    )

    error = chart.get(
        "error"
    )

    if error:

        description = (
            error.get("description")
            if isinstance(error, dict)
            else str(error)
        )

        raise RuntimeError(
            description or "Yahoo API error"
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

    if not timestamps:
        raise RuntimeError(
            "沒有 timestamp"
        )

    if not quote_list:
        raise RuntimeError(
            "沒有 quote"
        )

    quote = quote_list[0]

    opens = quote.get(
        "open",
        []
    )

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

        close = (
            closes[i]
            if i < len(closes)
            else None
        )

        high = (
            highs[i]
            if i < len(highs)
            else None
        )

        low = (
            lows[i]
            if i < len(lows)
            else None
        )

        volume = (
            volumes[i]
            if i < len(volumes)
            else None
        )

        if close is None:
            continue

        if high is None:
            continue

        if low is None:
            continue

        date = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc
        ).strftime(
            "%Y-%m-%d"
        )

        # ----------------------------------------------------
        # 只保留技術分析真正需要的資料
        # ----------------------------------------------------

        row = {
            "date": date,
            "high": round(float(high), 4),
            "low": round(float(low), 4),
            "close": round(float(close), 4),
            "volume": (
                int(volume)
                if volume is not None
                else 0
            )
        }

        rows.append(row)

    if not rows:

        raise RuntimeError(
            "沒有有效交易資料"
        )

    return rows


# ============================================================
# 取得全市場價格
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

    log("")

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
            f"{symbol} "
            f"{meta.get('name', '')}"
        )

        try:

            rows = fetch_history(
                symbol
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
                "latest_date": latest["date"],
                "latest_close": latest["close"],
                "latest_volume": latest["volume"],
                "data": rows
            }

            success += 1

            log(
                f"   ✓ {len(rows)} 筆"
                f" | 最新：{latest['date']}"
                f" | 收盤：{latest['close']}"
            )

        except Exception as exc:

            failed.append({
                "symbol": symbol,
                "error": str(exc)
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

    ratio = success / total

    log(
        f"成功率：{ratio * 100:.2f}%"
    )

    # 全市場資料不能大規模失敗
    if ratio < 0.80:

        raise RuntimeError(
            f"價格成功率只有 "
            f"{ratio * 100:.2f}%，"
            f"低於 80%，停止更新。"
        )

    # 每支資料至少要有 100 筆
    invalid = []

    for symbol, record in prices.items():

        data = record.get(
            "data",
            []
        )

        if len(data) < 100:

            invalid.append(
                symbol
            )

    if invalid:

        raise RuntimeError(
            "以下股票歷史資料不足："
            + ", ".join(
                invalid[:20]
            )
        )

    log(
        "✓ 價格資料完整性驗證通過"
    )


# ============================================================
# 建立輸出資料
# ============================================================

def build_output(
    prices,
    failed
):

    return {
        "version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "source": "Yahoo Finance",
        "market": "TW",
        "history_start": START_DATE,
        "count": len(prices),
        "failed_count": len(failed),
        "failed_symbols": failed,
        "fields": [
            "date",
            "high",
            "low",
            "close",
            "volume"
        ],
        "prices": prices
    }


# ============================================================
# 寫入
# ============================================================

def save_prices(output):

    section("建立 Data/prices.json")

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = DATA_DIR / (
        "prices.json.tmp"
    )

    # --------------------------------------------------------
    # 先使用最緊湊 JSON 格式
    # --------------------------------------------------------

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            separators=(
                ",",
                ":"
            ),
            allow_nan=False
        )

    size = temp_file.stat().st_size

    size_mb = (
        size /
        1024 /
        1024
    )

    log(
        f"暫存檔大小："
        f"{size_mb:.2f} MB"
    )

    # --------------------------------------------------------
    # GitHub 100MB 限制
    # --------------------------------------------------------

    if size >= MAX_FILE_SIZE_BYTES:

        temp_file.unlink(
            missing_ok=True
        )

        raise RuntimeError(
            f"prices.json 預估大小 "
            f"{size_mb:.2f} MB，"
            f"已接近/超過 GitHub 100MB 限制。"
        )

    # --------------------------------------------------------
    # 正式替換
    # --------------------------------------------------------

    temp_file.replace(
        PRICES_FILE
    )

    final_size = PRICES_FILE.stat().st_size

    log(
        f"✓ prices.json 建立成功"
    )

    log(
        f"檔案大小："
        f"{final_size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# 主程式
# ============================================================

def main():

    started = time.time()

    log("")
    log("=" * 64)
    log(
        f"台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )
    log("=" * 64)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        universe = load_universe()

        # ----------------------------------------------------
        # 2. 股票解析
        # ----------------------------------------------------

        records = extract_symbols(
            universe
        )

        if not records:

            raise RuntimeError(
                "Universe 沒有解析出任何合法台股代號。"
            )

        # ----------------------------------------------------
        # 3. 抓價格
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
            prices,
            failed
        )

        # ----------------------------------------------------
        # 6. 寫檔
        # ----------------------------------------------------

        save_prices(
            output
        )

        elapsed = (
            time.time() -
            started
        )

        # ----------------------------------------------------
        # 完成
        # ----------------------------------------------------

        log("")
        log("=" * 64)
        log(
            "✓ fetch_prices.py 執行完成"
        )
        log("=" * 64)

        log(
            f"Universe：{len(records)}"
        )

        log(
            f"成功：{success}"
        )

        log(
            f"失敗：{len(failed)}"
        )

        log(
            f"總耗時：{elapsed / 60:.1f} 分鐘"
        )

        log(
            f"輸出：{PRICES_FILE}"
        )

        if failed:

            log("")
            log(
                "⚠️ 部分股票取得失敗："
            )

            for item in failed[:30]:

                log(
                    f"   {item['symbol']}"
                    f" → {item['error']}"
                )

            if len(failed) > 30:

                log(
                    f"   ... "
                    f"另外 {len(failed) - 30} 檔"
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

        log(
            "⚠️ 保留既有 prices.json"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
