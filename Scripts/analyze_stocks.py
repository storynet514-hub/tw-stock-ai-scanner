#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/analyze_stocks.py
正式版 V3.3

============================================================
分析層責任
============================================================

本程式只負責「分析」。

讀取：

    Data/universe.json
    Data/prices/manifest.json
    Data/prices/prices_001.json ~ prices_020.json
    Data/chip.json

輸出：

    Data/analysis.json

============================================================
重要架構邊界
============================================================

本程式：

    不抓 CMoney API
    不執行 fetch_chip.py
    不執行 fetch_prices.py
    不修改 chip.json
    不修改 prices
    不修改 universe.json

只使用既有資料進行分析。

============================================================
V3.2 核心修正
============================================================

短期選股核心條件正式固定為「五項」：

    1. MACD 黃金交叉
    2. KD 黃金交叉
    3. RSI > 50
    4. 今日成交量 / 前5個交易日平均成交量 > 1.5
    5. 股價 > MA20 且 MA20 向上

原本：

    5. 股價 > MA20
    6. MA20 向上

已正式合併成：

    5. 股價 > MA20 且 MA20 向上

因此：

    core_total = 5

核心分數：

    0/5
    1/5
    2/5
    3/5
    4/5
    5/5

============================================================
核心條件與籌碼分析的架構邊界
============================================================

「核心五項」是技術面過濾條件。

主力籌碼：

    1D
    5D
    10D
    20D

不是第六項核心條件。

但是：

    1D / 5D / 10D / 20D

都會實際參與籌碼方向分析。

尤其：

    10D 不可移除。

籌碼方向：

    偏多
    分歧
    偏空
    資料不足

因此：

    核心分數 ≠ 籌碼分數

兩者必須分層。

============================================================
價格資料架構
============================================================

目前價格資料：

    Data/prices/
        manifest.json
        prices_001.json
        prices_002.json
        ...
        prices_020.json

V3.2：

    1. 讀取 manifest
    2. 找出所有價格 shard
    3. 載入 shard
    4. 建立 price_index
    5. 正規化股票代號
    6. 依股票代號取得歷史資料

支援：

    {
        "2337": [...]
    }

    {
        "stocks": {
            "2337": [...]
        }
    }

    {
        "data": {
            "2337": [...]
        }
    }

    [
        {
            "symbol": "2337",
            "prices": [...]
        }
    ]

以及：

    2337.TW
    2337.TWO

都正規化為：

    2337

============================================================
資料管線硬性驗證
============================================================

如果：

    Universe > 0
    但價格成功股票 = 0

直接失敗。

如果：

    Universe > 0
    價格成功股票 > 0

正常產生 analysis.json。

============================================================
"""

from __future__ import annotations

import json
import math
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 基本設定
# ============================================================

VERSION = "V3.3"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICES_DIR = DATA_DIR / "prices"
PRICES_MANIFEST = PRICES_DIR / "manifest.json"

CHIP_FILE = DATA_DIR / "chip.json"

OUTPUT_FILE = DATA_DIR / "analysis.json"


# ============================================================
# 短期選股設定
# ============================================================

SHORT_TERM_CONFIG = {

    "rsi_period": 14,
    "rsi_min": 50.0,

    "volume_lookback": 5,
    "volume_multiplier": 1.5,

    "ma20_period": 20,

    "kd_period": 9,

    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,

    "minimum_history": 60,
}


# ============================================================
# 零股定投設定
# ============================================================

DCA_CONFIG = {

    "ma20_aggressive": 0.97,
    "ma20_normal": 1.02,
    "ma20_pause": 1.05,

    "low60_aggressive": 1.03,

    "volume_aggressive": 1.5,
    "volume_normal": 1.0,
    "volume_observe": 0.5,

    "max_loss_pct": 20.0,

    "rebalance_warn": 30.0,
    "rebalance_high": 50.0,

    "extreme_bias": -20.0,
}


# ============================================================
# Log
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# JSON
# ============================================================

def load_json(path: Path) -> Any:

    if not path.exists():
        raise RuntimeError(f"找不到檔案：{path}")

    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


# ============================================================
# Number
# ============================================================

def safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    try:
        value = float(value)

        if not math.isfinite(value):
            return None

        return value

    except Exception:
        return None


# ============================================================
# Date
# ============================================================

def parse_date(value: Any) -> Optional[datetime]:

    if value is None:
        return None

    text = str(value).strip()

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):

        try:
            return datetime.strptime(text, fmt)

        except Exception:
            pass

    return None


# ============================================================
# Symbol Normalization
# ============================================================

def normalize_symbol(value: Any) -> str:

    if value is None:
        return ""

    text = str(value).strip().upper()

    if not text:
        return ""

    for suffix in (
        ".TW",
        ".TWO",
    ):

        if text.endswith(suffix):

            text = text[
                :-len(suffix)
            ]

            break

    return text


# ============================================================
# Universe
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "universe.json 格式錯誤"
        )

    # --------------------------------------------------------
    # V10.2 正式 Universe 格式：
    #
    # {
    #     "stocks": {
    #         "2337": { ... },
    #         "3081": { ... }
    #     }
    # }
    #
    # 舊版 Universe 格式：
    #
    # {
    #     "items": [
    #         {"symbol": "2337", ...}
    #     ]
    # }
    #
    # V3.3 同時支援兩種格式，
    # 但優先使用 V10.2 的 stocks。
    # --------------------------------------------------------

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    source_records: List[Any] = []
    source_mode = ""

    v10_stocks = data.get(
        "stocks"
    )

    if isinstance(
        v10_stocks,
        dict
    ):

        source_mode = "stocks"

        for key, item in v10_stocks.items():

            if not isinstance(
                item,
                dict
            ):
                continue

            record = dict(item)

            if not record.get("symbol"):
                record["symbol"] = key

            source_records.append(record)

    else:

        items = data.get(
            "items",
            []
        )

        if isinstance(
            items,
            list
        ):

            source_mode = "items"
            source_records = items

    if not source_records:

        raise RuntimeError(
            "Universe 沒有可讀取的 stocks/items 資料"
        )

    for item in source_records:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = (
            item.get("code")
            or item.get("symbol")
            or item.get("ticker")
        )

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        stocks[symbol] = {

            "symbol":
                symbol,

            "name":
                str(
                    item.get(
                        "name",
                        ""
                    )
                ).strip(),

            "market":
                str(
                    item.get(
                        "market",
                        ""
                    )
                ).strip(),

            "type":
                str(
                    item.get(
                        "type",
                        ""
                    )
                ).strip(),

            "full_symbol":
                str(
                    item.get(
                        "full_symbol",
                        ""
                    )
                ).strip(),

        }

    if not stocks:

        raise RuntimeError(
            "Universe 沒有有效股票"
        )

    log(
        f"Universe：{len(stocks)} 檔"
    )

    log(
        f"Universe 格式：{source_mode}"
    )

    return stocks


# ============================================================
# Price Manifest
# ============================================================

def load_price_manifest() -> Dict[str, Any]:

    data = load_json(
        PRICES_MANIFEST
    )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "prices/manifest.json 格式錯誤"
        )

    return data


# ============================================================
# Manifest File List
# ============================================================

def get_price_shard_files(
    manifest: Dict[str, Any]
) -> List[Path]:

    files: List[str] = []

    manifest_files = manifest.get(
        "files"
    )

    if isinstance(
        manifest_files,
        list
    ):

        for item in manifest_files:

            if isinstance(
                item,
                str
            ):

                files.append(
                    item
                )

            elif isinstance(
                item,
                dict
            ):

                for key in (
                    "file",
                    "path",
                    "filename",
                    "name",
                ):

                    value = item.get(
                        key
                    )

                    if value:

                        files.append(
                            str(value)
                        )

                        break

    # --------------------------------------------------------
    # 舊格式相容
    # --------------------------------------------------------

    stocks = manifest.get(
        "stocks"
    )

    if isinstance(
        stocks,
        dict
    ):

        for item in stocks.values():

            if isinstance(
                item,
                str
            ):

                files.append(
                    item
                )

            elif isinstance(
                item,
                dict
            ):

                for key in (
                    "file",
                    "path",
                    "filename",
                ):

                    value = item.get(
                        key
                    )

                    if value:

                        files.append(
                            str(value)
                        )

                        break

    # --------------------------------------------------------
    # manifest 沒列出檔案時
    # --------------------------------------------------------

    if (
        not files
        and PRICES_DIR.exists()
    ):

        for path in sorted(
            PRICES_DIR.glob(
                "prices_*.json"
            )
        ):

            files.append(
                path.name
            )

    result: List[Path] = []
    seen = set()

    for filename in files:

        filename = str(
            filename
        ).replace(
            "\\",
            "/"
        )

        if filename in seen:
            continue

        seen.add(
            filename
        )

        candidates = [

            PRICES_DIR / filename,

            DATA_DIR / filename,

            BASE_DIR / filename,
        ]

        found = None

        for candidate in candidates:

            if candidate.exists():

                found = candidate
                break

        if found is not None:

            result.append(
                found
            )

    result.sort(
        key=lambda p: p.name
    )

    return result


# ============================================================
# Price Row Detection
# ============================================================

def looks_like_price_row(
    value: Any
) -> bool:

    if not isinstance(
        value,
        dict
    ):

        return False

    keys = {
        str(k).lower()
        for k in value.keys()
    }

    return bool(
        keys
        & {
            "date",
            "trade_date",
            "tradedate",
            "close",
            "price",
        }
    )


# ============================================================
# Extract History Candidate
# ============================================================

def extract_history_candidate(
    value: Any
) -> Any:

    if isinstance(
        value,
        list
    ):

        return value

    if isinstance(
        value,
        dict
    ):

        for key in (
            "prices",
            "price",
            "history",
            "data",
            "rows",
            "records",
            "daily",
        ):

            candidate = value.get(
                key
            )

            if isinstance(
                candidate,
                list
            ):

                return candidate

        if looks_like_price_row(
            value
        ):

            return [value]

    return None


# ============================================================
# Extract Stocks From Shard
# ============================================================

def extract_stocks_from_shard(
    data: Any
) -> Dict[str, Any]:

    result: Dict[
        str,
        Any
    ] = {}

    if isinstance(
        data,
        dict
    ):

        for container_key in (
            "stocks",
            "data",
            "prices",
            "history",
        ):

            container = data.get(
                container_key
            )

            if isinstance(
                container,
                dict
            ):

                for symbol, value in container.items():

                    normalized = normalize_symbol(
                        symbol
                    )

                    history = extract_history_candidate(
                        value
                    )

                    if (
                        normalized
                        and history is not None
                    ):

                        result[
                            normalized
                        ] = history

                if result:
                    return result

        # ----------------------------------------------------
        # 直接 symbol -> history
        # ----------------------------------------------------

        for key, value in data.items():

            normalized = normalize_symbol(
                key
            )

            if not normalized:
                continue

            history = extract_history_candidate(
                value
            )

            if history is not None:

                result[
                    normalized
                ] = history

        if result:
            return result

        # ----------------------------------------------------
        # 單一股票物件
        # ----------------------------------------------------

        symbol = normalize_symbol(
            data.get("symbol")
            or data.get("code")
            or data.get("ticker")
        )

        if symbol:

            history = extract_history_candidate(
                data
            )

            if history is not None:

                result[
                    symbol
                ] = history

            return result

    # --------------------------------------------------------
    # list 結構
    # --------------------------------------------------------

    if isinstance(
        data,
        list
    ):

        for item in data:

            if not isinstance(
                item,
                dict
            ):

                continue

            symbol = normalize_symbol(
                item.get("symbol")
                or item.get("code")
                or item.get("ticker")
                or item.get("stock_id")
            )

            if not symbol:
                continue

            history = extract_history_candidate(
                item
            )

            if history is not None:

                result[
                    symbol
                ] = history

    return result


# ============================================================
# Parse Price History
# ============================================================

def parse_price_history(
    data: Any
) -> List[Dict[str, Any]]:

    rows: List[
        Dict[str, Any]
    ] = []

    if isinstance(
        data,
        dict
    ):

        raw_rows = (
            data.get("prices")
            or data.get("data")
            or data.get("history")
            or data.get("rows")
            or data.get("records")
            or []
        )

        if isinstance(
            raw_rows,
            dict
        ):

            converted = []

            for date, item in raw_rows.items():

                if isinstance(
                    item,
                    dict
                ):

                    row = dict(
                        item
                    )

                    row.setdefault(
                        "date",
                        date
                    )

                    converted.append(
                        row
                    )

                else:

                    converted.append(
                        {
                            "date":
                                date,

                            "close":
                                item,
                        }
                    )

            raw_rows = converted

    elif isinstance(
        data,
        list
    ):

        raw_rows = data

    else:

        raw_rows = []

    for row in raw_rows:

        if not isinstance(
            row,
            dict
        ):

            continue

        date = (
            row.get("date")
            or row.get("Date")
            or row.get("trade_date")
            or row.get("TradeDate")
            or row.get("tradedate")
        )

        dt = parse_date(
            date
        )

        if dt is None:
            continue

        close = (
            row.get("close")
            if row.get("close") is not None
            else row.get("Close")
        )

        if close is None:
            close = row.get(
                "price"
            )

        if close is None:
            close = row.get(
                "Price"
            )

        volume = (
            row.get("volume")
            if row.get("volume") is not None
            else row.get("Volume")
        )

        high = (
            row.get("high")
            if row.get("high") is not None
            else row.get("High")
        )

        low = (
            row.get("low")
            if row.get("low") is not None
            else row.get("Low")
        )

        close = safe_float(
            close
        )

        volume = safe_float(
            volume
        )

        high = safe_float(
            high
        )

        low = safe_float(
            low
        )

        if close is None:
            continue

        rows.append(
            {

                "date":
                    dt.strftime(
                        "%Y-%m-%d"
                    ),

                "close":
                    close,

                "volume":
                    volume,

                "high":
                    high,

                "low":
                    low,
            }
        )

    unique = {}

    for row in rows:

        unique[
            row["date"]
        ] = row

    rows = list(
        unique.values()
    )

    rows.sort(
        key=lambda x:
            parse_date(
                x["date"]
            )
            or datetime.min
    )

    return rows


# ============================================================
# Load All Price Shards
# ============================================================

def load_price_index(
    manifest: Dict[str, Any]
) -> Tuple[
    Dict[str, List[Dict[str, Any]]],
    Dict[str, Any]
]:

    section(
        "載入價格分檔"
    )

    shard_files = get_price_shard_files(
        manifest
    )

    if not shard_files:

        raise RuntimeError(
            "manifest 沒有找到任何價格分檔"
        )

    log(
        f"價格分檔："
        f"{len(shard_files)} 個"
    )

    price_index: Dict[
        str,
        List[Dict[str, Any]]
    ] = {}

    shard_statistics = []

    for index, path in enumerate(
        shard_files,
        start=1
    ):

        try:

            data = load_json(
                path
            )

            stocks = extract_stocks_from_shard(
                data
            )

            loaded_count = 0

            for symbol, history in stocks.items():

                rows = parse_price_history(
                    history
                )

                if rows:

                    price_index[
                        symbol
                    ] = rows

                    loaded_count += 1

            shard_statistics.append(
                {

                    "file":
                        path.name,

                    "stocks_detected":
                        len(stocks),

                    "stocks_loaded":
                        loaded_count,
                }
            )

            log(
                f"[{index:02d}/"
                f"{len(shard_files):02d}] "
                f"{path.name} "
                f"→ "
                f"{len(stocks)} 檔 / "
                f"{loaded_count} 檔有效"
            )

        except Exception as exc:

            log(
                f"⚠️ {path.name} "
                f"讀取失敗：{exc}"
            )

            shard_statistics.append(
                {

                    "file":
                        path.name,

                    "stocks_detected":
                        0,

                    "stocks_loaded":
                        0,

                    "error":
                        str(exc),
                }
            )

    log("")

    log(
        f"價格索引完成："
        f"{len(price_index)} 檔"
    )

    if not price_index:

        raise RuntimeError(
            "價格分檔全部載入後沒有任何有效股票資料"
        )

    return (
        price_index,
        {

            "shard_count":
                len(shard_files),

            "stock_count":
                len(price_index),

            "shards":
                shard_statistics,
        },
    )


# ============================================================
# Moving Average
# ============================================================

def moving_average(
    values: List[float],
    period: int
) -> Optional[float]:

    if len(values) < period:
        return None

    return (
        sum(
            values[-period:]
        )
        / period
    )


def previous_moving_average(
    values: List[float],
    period: int
) -> Optional[float]:

    if len(values) < period + 1:
        return None

    return (
        sum(
            values[
                -period - 1:-1
            ]
        )
        / period
    )


# ============================================================
# EMA
# ============================================================

def ema_series(
    values: List[float],
    period: int
) -> List[float]:

    if len(values) < period:
        return []

    multiplier = 2.0 / (
        period + 1
    )

    result = []

    ema = (
        sum(
            values[:period]
        )
        / period
    )

    result.append(
        ema
    )

    for value in values[period:]:

        ema = (
            value - ema
        ) * multiplier + ema

        result.append(
            ema
        )

    return result


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    closes: List[float]
) -> Dict[str, Any]:

    fast = SHORT_TERM_CONFIG[
        "macd_fast"
    ]

    slow = SHORT_TERM_CONFIG[
        "macd_slow"
    ]

    signal_period = SHORT_TERM_CONFIG[
        "macd_signal"
    ]

    if len(closes) < (
        slow + signal_period
    ):

        return {

            "macd":
                None,

            "signal":
                None,

            "histogram":
                None,

            "golden_cross":
                False,

            "status":
                "insufficient",
        }

    ema_fast = ema_series(
        closes,
        fast
    )

    ema_slow = ema_series(
        closes,
        slow
    )

    if (
        not ema_fast
        or not ema_slow
    ):

        return {

            "macd":
                None,

            "signal":
                None,

            "histogram":
                None,

            "golden_cross":
                False,

            "status":
                "insufficient",
        }

    offset = (
        len(ema_fast)
        - len(ema_slow)
    )

    macd_series = []

    for i, slow_value in enumerate(
        ema_slow
    ):

        fast_value = ema_fast[
            i + offset
        ]

        macd_series.append(
            fast_value
            - slow_value
        )

    signal_series = ema_series(
        macd_series,
        signal_period
    )

    if len(signal_series) < 2:

        return {

            "macd":
                round(
                    macd_series[-1],
                    4
                ),

            "signal":
                None,

            "histogram":
                None,

            "golden_cross":
                False,

            "status":
                "partial",
        }

    current_macd = (
        macd_series[-1]
    )

    previous_macd = (
        macd_series[-2]
    )

    current_signal = (
        signal_series[-1]
    )

    previous_signal = (
        signal_series[-2]
    )

    golden_cross = (
        previous_macd
        <= previous_signal
        and
        current_macd
        > current_signal
    )

    return {

        "macd":
            round(
                current_macd,
                4
            ),

        "signal":
            round(
                current_signal,
                4
            ),

        "histogram":
            round(
                current_macd
                - current_signal,
                4
            ),

        "golden_cross":
            golden_cross,

        "status":
            "complete",
    }


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    closes: List[float],
    period: int = 14
) -> Optional[float]:

    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        change = (
            closes[i]
            - closes[i - 1]
        )

        if change > 0:

            gains.append(
                change
            )

            losses.append(
                0.0
            )

        else:

            gains.append(
                0.0
            )

            losses.append(
                abs(change)
            )

    avg_gain = (
        sum(
            gains[:period]
        )
        / period
    )

    avg_loss = (
        sum(
            losses[:period]
        )
        / period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            avg_gain
            * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss
            * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:

        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return round(
        100.0
        - (
            100.0
            / (1.0 + rs)
        ),
        2
    )


# ============================================================
# KD
# ============================================================

def calculate_kd(
    rows: List[Dict[str, Any]],
    period: int = 9
) -> Dict[str, Any]:

    if len(rows) < period:

        return {

            "k":
                None,

            "d":
                None,

            "golden_cross":
                False,

            "status":
                "insufficient",
        }

    highs = []
    lows = []
    closes = []

    for row in rows:

        close = safe_float(
            row.get("close")
        )

        if close is None:
            continue

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        highs.append(
            high
            if high is not None
            else close
        )

        lows.append(
            low
            if low is not None
            else close
        )

        closes.append(
            close
        )

    if len(closes) < period:

        return {

            "k":
                None,

            "d":
                None,

            "golden_cross":
                False,

            "status":
                "insufficient",
        }

    k = 50.0
    d = 50.0

    k_values = []
    d_values = []

    for i in range(
        period - 1,
        len(closes)
    ):

        window_high = max(
            highs[
                i - period + 1:i + 1
            ]
        )

        window_low = min(
            lows[
                i - period + 1:i + 1
            ]
        )

        denominator = (
            window_high
            - window_low
        )

        if denominator == 0:

            rsv = 50.0

        else:

            rsv = (
                (
                    closes[i]
                    - window_low
                )
                / denominator
                * 100.0
            )

        k = (
            k * 2.0
            + rsv
        ) / 3.0

        d = (
            d * 2.0
            + k
        ) / 3.0

        k_values.append(
            k
        )

        d_values.append(
            d
        )

    golden_cross = False

    if len(k_values) >= 2:

        golden_cross = (
            k_values[-2]
            <= d_values[-2]
            and
            k_values[-1]
            > d_values[-1]
        )

    return {

        "k":
            round(
                k_values[-1],
                2
            ),

        "d":
            round(
                d_values[-1],
                2
            ),

        "golden_cross":
            golden_cross,

        "status":
            "complete",
    }


# ============================================================
# Price Metrics
# ============================================================

def calculate_price_metrics(
    rows: List[Dict[str, Any]]
) -> Dict[str, Any]:

    closes = [
        row["close"]
        for row in rows
        if row.get("close") is not None
    ]

    if not closes:
        return {}

    current_price = closes[-1]

    ma5 = moving_average(
        closes,
        5
    )

    ma20 = moving_average(
        closes,
        20
    )

    previous_ma20 = previous_moving_average(
        closes,
        20
    )

    ma60 = moving_average(
        closes,
        60
    )

    volumes = [
        safe_float(
            row.get("volume")
        )
        for row in rows
    ]

    today_volume = (
        volumes[-1]
        if volumes
        else None
    )

    # --------------------------------------------------------
    # 今日成交量 / 前5個交易日平均成交量
    # 注意：前5日不包含今日
    # --------------------------------------------------------

    previous_5_volume_avg = None

    if len(volumes) >= 6:

        previous_5 = [
            value
            for value in volumes[-6:-1]
            if value is not None
        ]

        if len(previous_5) == 5:

            previous_5_volume_avg = (
                sum(previous_5)
                / 5.0
            )

    volume_ratio_vs_previous_5 = None

    if (
        today_volume is not None
        and previous_5_volume_avg is not None
        and previous_5_volume_avg > 0
    ):

        volume_ratio_vs_previous_5 = (
            today_volume
            / previous_5_volume_avg
        )

    available_volumes = [
        value
        for value in volumes
        if value is not None
    ]

    volume5_including_today = None

    if len(
        available_volumes
    ) >= 5:

        volume5_including_today = (
            sum(
                available_volumes[-5:]
            )
            / 5.0
        )

    volume20 = None

    if len(
        available_volumes
    ) >= 20:

        volume20 = (
            sum(
                available_volumes[-20:]
            )
            / 20.0
        )

    high60 = None
    low60 = None

    if len(closes) >= 60:

        high60 = max(
            closes[-60:]
        )

        low60 = min(
            closes[-60:]
        )

    elif closes:

        high60 = max(
            closes
        )

        low60 = min(
            closes
        )

    bias20_pct = None

    if (
        ma20 is not None
        and ma20 != 0
    ):

        bias20_pct = (
            current_price
            / ma20
            - 1.0
        ) * 100.0

    change1_pct = None

    if len(closes) >= 2:

        change1_pct = (
            current_price
            / closes[-2]
            - 1.0
        ) * 100.0

    change5_pct = None

    if len(closes) >= 6:

        change5_pct = (
            current_price
            / closes[-6]
            - 1.0
        ) * 100.0

    change20_pct = None

    if len(closes) >= 21:

        change20_pct = (
            current_price
            / closes[-21]
            - 1.0
        ) * 100.0

    position60_pct = None

    if (
        high60 is not None
        and low60 is not None
        and high60 != low60
    ):

        position60_pct = (
            (
                current_price
                - low60
            )
            / (
                high60
                - low60
            )
            * 100.0
        )

    ma20_ratio = None

    if (
        ma20 is not None
        and ma20 != 0
    ):

        ma20_ratio = (
            current_price
            / ma20
        )

    low60_ratio = None

    if (
        low60 is not None
        and low60 != 0
    ):

        low60_ratio = (
            current_price
            / low60
        )

    # --------------------------------------------------------
    # 第五項核心條件所需的單一結果
    #
    # 股價 > MA20
    # 且
    # MA20 向上
    #
    # 注意：
    # 這裡不是兩個核心條件，
    # 而是一個合併後的核心條件。
    # --------------------------------------------------------

    price_above_ma20 = (
        current_price is not None
        and ma20 is not None
        and current_price > ma20
    )

    ma20_up = (
        previous_ma20 is not None
        and ma20 is not None
        and ma20 > previous_ma20
    )

    price_above_ma20_and_ma20_up = (
        price_above_ma20
        and ma20_up
    )

    volume_signal = "資料不足"

    if (
        volume_ratio_vs_previous_5
        is not None
    ):

        ratio = (
            volume_ratio_vs_previous_5
        )

        if ratio >= 1.5:

            volume_signal = "放量"

        elif ratio >= 1.0:

            volume_signal = "正常"

        elif ratio >= 0.5:

            volume_signal = "量縮"

        else:

            volume_signal = "明顯量縮"

    return {

        "price":
            round(
                current_price,
                2
            ),

        "ma5":
            round(
                ma5,
                2
            )
            if ma5 is not None
            else None,

        "ma20":
            round(
                ma20,
                2
            )
            if ma20 is not None
            else None,

        "ma60":
            round(
                ma60,
                2
            )
            if ma60 is not None
            else None,

        "price_above_ma20":
            price_above_ma20,

        "ma20_up":
            ma20_up,

        "price_above_ma20_and_ma20_up":
            price_above_ma20_and_ma20_up,

        "ma20_ratio":
            round(
                ma20_ratio,
                4
            )
            if ma20_ratio is not None
            else None,

        "bias20_pct":
            round(
                bias20_pct,
                2
            )
            if bias20_pct is not None
            else None,

        "change1_pct":
            round(
                change1_pct,
                2
            )
            if change1_pct is not None
            else None,

        "change5_pct":
            round(
                change5_pct,
                2
            )
            if change5_pct is not None
            else None,

        "change20_pct":
            round(
                change20_pct,
                2
            )
            if change20_pct is not None
            else None,

        "high60":
            round(
                high60,
                2
            )
            if high60 is not None
            else None,

        "low60":
            round(
                low60,
                2
            )
            if low60 is not None
            else None,

        "position60_pct":
            round(
                position60_pct,
                2
            )
            if position60_pct is not None
            else None,

        "low60_ratio":
            round(
                low60_ratio,
                4
            )
            if low60_ratio is not None
            else None,

        "volume":
            today_volume,

        "volume5_previous_avg":
            round(
                previous_5_volume_avg,
                2
            )
            if previous_5_volume_avg is not None
            else None,

        "volume5_including_today":
            round(
                volume5_including_today,
                2
            )
            if volume5_including_today is not None
            else None,

        "volume20":
            round(
                volume20,
                2
            )
            if volume20 is not None
            else None,

        "volume_ratio_vs_previous_5":
            round(
                volume_ratio_vs_previous_5,
                4
            )
            if volume_ratio_vs_previous_5 is not None
            else None,

        "volume_signal":
            volume_signal,
    }


# ============================================================
# Chip Helpers
# ============================================================

def chip_value(
    chip: Dict[str, Any],
    key: str
) -> Optional[float]:

    value = chip.get(
        key
    )

    if value is None:

        aliases = {

            "main_force_1d": (
                "main_force_1D",
                "main_force_1D_pct",
                "main_force_1d_pct",
                "mf1",
                "1d",
            ),

            "main_force_5d": (
                "main_force_5D",
                "main_force_5D_pct",
                "main_force_5d_pct",
                "mf5",
                "5d",
            ),

            "main_force_10d": (
                "main_force_10D",
                "main_force_10D_pct",
                "main_force_10d_pct",
                "mf10",
                "10d",
            ),

            "main_force_20d": (
                "main_force_20D",
                "main_force_20D_pct",
                "main_force_20d_pct",
                "mf20",
                "20d",
            ),
        }

        for alias in aliases.get(
            key,
            ()
        ):

            if alias in chip:

                value = chip.get(
                    alias
                )

                break

    return safe_float(
        value
    )


# ============================================================
# Chip Analysis
# ============================================================

def calculate_chip_analysis(
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    mf1 = chip_value(
        chip,
        "main_force_1d"
    )

    mf5 = chip_value(
        chip,
        "main_force_5d"
    )

    mf10 = chip_value(
        chip,
        "main_force_10d"
    )

    mf20 = chip_value(
        chip,
        "main_force_20d"
    )

    values = [
        value
        for value in (
            mf1,
            mf5,
            mf10,
            mf20,
        )
        if value is not None
    ]

    positive_count = sum(
        1
        for value in values
        if value > 0
    )

    negative_count = sum(
        1
        for value in values
        if value < 0
    )

    if len(values) == 0:

        direction = "資料不足"

    elif positive_count >= 3:

        direction = "偏多"

    elif negative_count >= 3:

        direction = "偏空"

    else:

        direction = "分歧"

    # --------------------------------------------------------
    # 中期籌碼：
    #
    # 5D / 10D / 20D
    #
    # 10D 明確參與。
    # --------------------------------------------------------

    medium_values = [
        value
        for value in (
            mf5,
            mf10,
            mf20,
        )
        if value is not None
    ]

    medium_positive = sum(
        1
        for value in medium_values
        if value > 0
    )

    medium_negative = sum(
        1
        for value in medium_values
        if value < 0
    )

    if not medium_values:

        medium_direction = "資料不足"

    elif medium_positive >= 2:

        medium_direction = "中期偏多"

    elif medium_negative >= 2:

        medium_direction = "中期偏空"

    else:

        medium_direction = "中期分歧"

    # --------------------------------------------------------
    # 10D 狀態
    # --------------------------------------------------------

    if mf10 is None:

        ten_day_direction = "資料不足"

    elif mf10 > 0:

        ten_day_direction = "偏多"

    elif mf10 < 0:

        ten_day_direction = "偏空"

    else:

        ten_day_direction = "中性"

    return {

        "main_force_1d":
            mf1,

        "main_force_5d":
            mf5,

        "main_force_10d":
            mf10,

        "main_force_20d":
            mf20,

        "positive_count":
            positive_count,

        "negative_count":
            negative_count,

        "direction":
            direction,

        "medium_direction":
            medium_direction,

        "ten_day_direction":
            ten_day_direction,

        "ten_day_used":
            True,

        "analysis_basis":
            [
                "1D",
                "5D",
                "10D",
                "20D",
            ],

        "medium_analysis_basis":
            [
                "5D",
                "10D",
                "20D",
            ],
    }


# ============================================================
# Short Term Evaluation
# ============================================================

def evaluate_short_term(
    metrics: Dict[str, Any],
    macd: Dict[str, Any],
    kd: Dict[str, Any],
    rsi: Optional[float],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    # ========================================================
    # 五項核心條件
    #
    # 1. MACD 黃金交叉
    # 2. KD 黃金交叉
    # 3. RSI > 50
    # 4. 今日成交量 > 前5個交易日平均成交量 × 1.5
    # 5. 股價 > MA20 且 MA20 向上
    #
    # 注意：
    # 第五項是單一條件。
    # ========================================================

    conditions = {

        "macd_golden_cross":
            bool(
                macd.get(
                    "golden_cross",
                    False
                )
            ),

        "kd_golden_cross":
            bool(
                kd.get(
                    "golden_cross",
                    False
                )
            ),

        "rsi_above_50":
            (
                rsi is not None
                and
                rsi > SHORT_TERM_CONFIG[
                    "rsi_min"
                ]
            ),

        "volume_ratio_gt_1_5":
            (
                metrics.get(
                    "volume_ratio_vs_previous_5"
                )
                is not None

                and

                metrics.get(
                    "volume_ratio_vs_previous_5"
                )
                > SHORT_TERM_CONFIG[
                    "volume_multiplier"
                ]
            ),

        "price_above_ma20_and_ma20_up":
            bool(
                metrics.get(
                    "price_above_ma20_and_ma20_up",
                    False
                )
            ),
    }

    core_total = 5

    score = sum(
        1
        for value in conditions.values()
        if value
    )

    qualified = (
        score == core_total
    )

    chip_analysis = calculate_chip_analysis(
        chip
    )

    # --------------------------------------------------------
    # 技術強度
    #
    # 核心五項是純技術條件。
    # 籌碼另行判斷。
    # --------------------------------------------------------

    if score >= 5:

        technical_strength = "強勢"

    elif score >= 4:

        technical_strength = "偏強"

    elif score >= 3:

        technical_strength = "中性"

    else:

        technical_strength = "偏弱"

    # --------------------------------------------------------
    # 綜合方向
    #
    # 不把籌碼偷偷算成第六項。
    # --------------------------------------------------------

    chip_direction = chip_analysis.get(
        "direction",
        "資料不足"
    )

    if qualified:

        if chip_direction == "偏多":

            final_direction = "偏多"

            operation = "偏多，可分批"

        elif chip_direction == "偏空":

            final_direction = "技術偏多、籌碼偏空"

            operation = "技術符合，籌碼觀察"

        elif chip_direction == "分歧":

            final_direction = "技術偏多、籌碼分歧"

            operation = "可觀察，分批"

        else:

            final_direction = "技術偏多"

            operation = "偏多，可分批"

    else:

        if chip_direction == "偏空":

            final_direction = "偏空"

            operation = "暫停操作"

        elif chip_direction == "偏多":

            final_direction = "技術未完全符合、籌碼偏多"

            operation = "觀察"

        elif chip_direction == "分歧":

            final_direction = "分歧"

            operation = "觀察"

        else:

            final_direction = "中性"

            operation = "觀察"

    return {

        # ----------------------------------------------------
        # 核心結果
        # ----------------------------------------------------

        "qualified":
            qualified,

        "score":
            score,

        "core_score":
            score,

        "core_total":
            core_total,

        "core_pass_ratio":
            round(
                score / core_total,
                4
            ),

        "conditions":
            conditions,

        # ----------------------------------------------------
        # 技術強度
        # ----------------------------------------------------

        "technical_strength":
            technical_strength,

        "operation":
            operation,

        "direction":
            final_direction,

        # ----------------------------------------------------
        # 技術指標
        # ----------------------------------------------------

        "rsi":
            rsi,

        "macd":
            macd,

        "kd":
            kd,

        # ----------------------------------------------------
        # 成交量
        # ----------------------------------------------------

        "volume_rule":
            (
                "今日成交量 ÷ "
                "前5個交易日平均成交量 > 1.5"
            ),

        "volume_ratio":
            metrics.get(
                "volume_ratio_vs_previous_5"
            ),

        # ----------------------------------------------------
        # 籌碼
        #
        # 與核心五項分開。
        # ----------------------------------------------------

        "chip":
            chip_analysis,

        "chip_is_core_condition":
            False,

        # ----------------------------------------------------
        # 明確標記 10D
        # ----------------------------------------------------

        "ten_day_chip_used":
            True,
    }


# ============================================================
# DCA Loss Warning
# ============================================================

def calculate_loss_warning(
    current_price: Optional[float],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    cost = None

    for key in (
        "avg_cost",
        "cost",
        "average_cost",
    ):

        value = safe_float(
            chip.get(
                key
            )
        )

        if value is not None:

            cost = value
            break

    if (
        current_price is None
        or cost is None
        or cost <= 0
    ):

        return {

            "available":
                False,

            "cost":
                None,

            "loss_pct":
                None,

            "warning":
                False,

            "message":
                "無持倉成本資料",
        }

    loss_pct = (
        (
            current_price
            - cost
        )
        / cost
        * 100.0
    )

    warning = (
        loss_pct
        <= -DCA_CONFIG[
            "max_loss_pct"
        ]
    )

    return {

        "available":
            True,

        "cost":
            round(
                cost,
                2
            ),

        "loss_pct":
            round(
                loss_pct,
                2
            ),

        "warning":
            warning,

        "message":
            (
                "達到最大虧損警戒"
                if warning
                else
                "未達最大虧損警戒"
            ),
    }


# ============================================================
# DCA Evaluation
# ============================================================

def evaluate_dca(
    metrics: Dict[str, Any],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    price = metrics.get(
        "price"
    )

    ma20_ratio = metrics.get(
        "ma20_ratio"
    )

    low60_ratio = metrics.get(
        "low60_ratio"
    )

    bias20 = metrics.get(
        "bias20_pct"
    )

    position60 = metrics.get(
        "position60_pct"
    )

    volume_ratio = metrics.get(
        "volume_ratio_vs_previous_5"
    )

    chip_analysis = calculate_chip_analysis(
        chip
    )

    if ma20_ratio is None:

        action = "資料不足"
        level = 0

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_aggressive"
        ]

        and

        low60_ratio is not None

        and

        low60_ratio
        <= DCA_CONFIG[
            "low60_aggressive"
        ]
    ):

        action = "積極買進"
        level = 3

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_normal"
        ]
    ):

        action = "正常買進"
        level = 2

    elif (
        ma20_ratio
        <= DCA_CONFIG[
            "ma20_pause"
        ]
    ):

        action = "觀察"
        level = 1

    else:

        action = "暫停加碼"
        level = 0

    volume_signal = "資料不足"

    if volume_ratio is not None:

        if volume_ratio >= 1.5:

            volume_signal = "放量"

        elif volume_ratio >= 1.0:

            volume_signal = "正常"

        elif volume_ratio >= 0.5:

            volume_signal = "量縮"

        else:

            volume_signal = "明顯量縮"

    chip_direction = chip_analysis.get(
        "direction"
    )

    medium_direction = chip_analysis.get(
        "medium_direction"
    )

    rebalance_signal = "normal"

    if position60 is not None:

        if (
            position60
            >= DCA_CONFIG[
                "rebalance_high"
            ]
        ):

            rebalance_signal = "高檔"

        elif (
            position60
            >= DCA_CONFIG[
                "rebalance_warn"
            ]
        ):

            rebalance_signal = "偏高"

    extreme_discount = (
        bias20 is not None
        and
        bias20
        <= DCA_CONFIG[
            "extreme_bias"
        ]
    )

    if extreme_discount:

        if level > 0:

            level += 1

        action = "極端負乖離"

    if (
        rebalance_signal == "高檔"
        and level > 0
    ):

        action = "高檔觀察"

        level = max(
            0,
            level - 1
        )

    chip_adjustment = "neutral"

    if (
        medium_direction
        == "中期偏多"
    ):

        chip_adjustment = "positive"

    elif (
        medium_direction
        == "中期偏空"
    ):

        chip_adjustment = "negative"

    volume_adjustment = "neutral"

    if volume_signal == "放量":

        volume_adjustment = "positive"

    elif volume_signal == "明顯量縮":

        volume_adjustment = "caution"

    loss_warning = calculate_loss_warning(
        price,
        chip
    )

    risk_flags = []

    if (
        rebalance_signal
        == "高檔"
    ):

        risk_flags.append(
            "60日高檔"
        )

    if (
        volume_signal
        == "明顯量縮"
    ):

        risk_flags.append(
            "成交量明顯萎縮"
        )

    if (
        chip_adjustment
        == "negative"
    ):

        risk_flags.append(
            "中期籌碼偏空"
        )

    if loss_warning.get(
        "warning"
    ):

        risk_flags.append(
            "最大虧損警戒"
        )

    return {

        "action":
            action,

        "level":
            level,

        "ma20_ratio":
            ma20_ratio,

        "low60_ratio":
            low60_ratio,

        "bias20_pct":
            bias20,

        "position60_pct":
            position60,

        "change1_pct":
            metrics.get(
                "change1_pct"
            ),

        "change5_pct":
            metrics.get(
                "change5_pct"
            ),

        "change20_pct":
            metrics.get(
                "change20_pct"
            ),

        "volume_ratio":
            volume_ratio,

        "volume_signal":
            volume_signal,

        "volume_adjustment":
            volume_adjustment,

        "chip_direction":
            chip_direction,

        "medium_chip_direction":
            medium_direction,

        "chip_adjustment":
            chip_adjustment,

        "rebalance_signal":
            rebalance_signal,

        "extreme_discount":
            extreme_discount,

        "loss_warning":
            loss_warning,

        "risk_flags":
            risk_flags,

        "main_force_1d":
            chip_analysis.get(
                "main_force_1d"
            ),

        "main_force_5d":
            chip_analysis.get(
                "main_force_5d"
            ),

        "main_force_10d":
            chip_analysis.get(
                "main_force_10d"
            ),

        "main_force_20d":
            chip_analysis.get(
                "main_force_20d"
            ),

        "ten_day_used":
            True,
    }


# ============================================================
# Analyze One Stock
# ============================================================

def analyze_stock(
    stock: Dict[str, Any],
    rows: List[Dict[str, Any]],
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    symbol = stock[
        "symbol"
    ]

    name = stock.get(
        "name",
        ""
    )

    market = stock.get(
        "market",
        ""
    )

    minimum_history = (
        SHORT_TERM_CONFIG[
            "minimum_history"
        ]
    )

    if len(rows) < 2:

        chip_analysis = calculate_chip_analysis(
            chip
        )

        return {

            "symbol":
                symbol,

            "name":
                name,

            "market":
                market,

            "status":
                "insufficient",

            "history_count":
                len(rows),

            "error":
                "價格歷史不足",

            "metrics":
                {},

            "short_term":
                {

                    "qualified":
                        False,

                    "score":
                        0,

                    "core_score":
                        0,

                    "core_total":
                        5,

                    "core_pass_ratio":
                        0,

                    "conditions":
                        {

                            "macd_golden_cross":
                                False,

                            "kd_golden_cross":
                                False,

                            "rsi_above_50":
                                False,

                            "volume_ratio_gt_1_5":
                                False,

                            "price_above_ma20_and_ma20_up":
                                False,
                        },

                    "technical_strength":
                        "資料不足",

                    "operation":
                        "資料不足",

                    "direction":
                        "資料不足",

                    "chip":
                        chip_analysis,

                    "chip_is_core_condition":
                        False,

                    "ten_day_chip_used":
                        True,
                },

            "dca":
                {},

            "chip":
                chip_analysis,
        }

    metrics = calculate_price_metrics(
        rows
    )

    closes = [
        row["close"]
        for row in rows
        if row.get(
            "close"
        ) is not None
    ]

    macd = calculate_macd(
        closes
    )

    rsi = calculate_rsi(
        closes,
        SHORT_TERM_CONFIG[
            "rsi_period"
        ]
    )

    kd = calculate_kd(
        rows,
        SHORT_TERM_CONFIG[
            "kd_period"
        ]
    )

    short_term = evaluate_short_term(
        metrics,
        macd,
        kd,
        rsi,
        chip
    )

    short_term[
        "history_requirement"
    ] = {

        "minimum":
            minimum_history,

        "actual":
            len(rows),

        "passed":
            len(rows)
            >= minimum_history,
    }

    # --------------------------------------------------------
    # 歷史資料不足 60 日：
    #
    # 不允許列為完整核心選股。
    # --------------------------------------------------------

    if len(rows) < minimum_history:

        short_term[
            "qualified"
        ] = False

        short_term[
            "qualification_blocked"
        ] = True

        short_term[
            "qualification_block_reason"
        ] = (
            "歷史資料不足 "
            f"{minimum_history} 個交易日"
        )

    else:

        short_term[
            "qualification_blocked"
        ] = False

        short_term[
            "qualification_block_reason"
        ] = None

    dca = evaluate_dca(
        metrics,
        chip
    )

    chip_analysis = calculate_chip_analysis(
        chip
    )

    return {

        "symbol":
            symbol,

        "name":
            name,

        "market":
            market,

        "status":
            (
                "complete"
                if len(rows)
                >= minimum_history
                else "partial"
            ),

        "latest_date":
            rows[-1][
                "date"
            ],

        "history_count":
            len(rows),

        "metrics":
            metrics,

        "short_term":
            short_term,

        "dca":
            dca,

        "chip":
            chip_analysis,
    }


# ============================================================
# Main Analysis
# ============================================================

def run_analysis() -> Dict[str, Any]:

    section(
        f"台股 AI 選股分析 V{VERSION}"
    )

    universe = load_universe()

    manifest = load_price_manifest()

    chip_data = load_json(
        CHIP_FILE
    )

    if not isinstance(
        chip_data,
        dict
    ):

        raise RuntimeError(
            "chip.json 格式錯誤"
        )

    chip_stocks = chip_data.get(
        "stocks",
        {}
    )

    if not isinstance(
        chip_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    log(
        f"Chip 股票："
        f"{len(chip_stocks)}"
    )

    # --------------------------------------------------------
    # 載入所有價格 shard
    # --------------------------------------------------------

    price_index, price_info = load_price_index(
        manifest
    )

    # --------------------------------------------------------
    # Universe / Price 驗證
    # --------------------------------------------------------

    universe_symbols = set(
        universe.keys()
    )

    price_symbols = set(
        price_index.keys()
    )

    matched_symbols = (
        universe_symbols
        & price_symbols
    )

    missing_symbols = (
        universe_symbols
        - price_symbols
    )

    log("")

    log(
        "價格資料驗證："
    )

    log(
        f"Universe："
        f"{len(universe_symbols)}"
    )

    log(
        f"價格索引："
        f"{len(price_symbols)}"
    )

    log(
        f"成功對接："
        f"{len(matched_symbols)}"
    )

    log(
        f"價格缺失："
        f"{len(missing_symbols)}"
    )

    if not matched_symbols:

        raise RuntimeError(
            "❌ 價格資料沒有真正接通："
            "Universe 與 prices shard "
            "沒有任何股票成功對接"
        )

    results = {}

    short_candidates = []

    dca_buy = []
    dca_observe = []
    dca_pause = []

    complete = 0
    partial = 0
    insufficient = 0

    price_loaded = 0
    price_missing = 0

    core_score_distribution = {
        "0": 0,
        "1": 0,
        "2": 0,
        "3": 0,
        "4": 0,
        "5": 0,
    }

    chip_direction_distribution = {
        "偏多": 0,
        "分歧": 0,
        "偏空": 0,
        "資料不足": 0,
    }

    total = len(
        universe
    )

    for index, (
        symbol,
        stock
    ) in enumerate(
        universe.items(),
        start=1
    ):

        price_rows = price_index.get(
            symbol,
            []
        )

        if price_rows:

            price_loaded += 1

        else:

            price_missing += 1

        chip = chip_stocks.get(
            symbol,
            {}
        )

        if not isinstance(
            chip,
            dict
        ):

            chip = {}

        record = analyze_stock(
            stock,
            price_rows,
            chip
        )

        results[
            symbol
        ] = record

        status = record.get(
            "status"
        )

        if status == "complete":

            complete += 1

        elif status == "partial":

            partial += 1

        else:

            insufficient += 1

        # ----------------------------------------------------
        # 五項核心分數統計
        # ----------------------------------------------------

        short_term = record.get(
            "short_term",
            {}
        )

        core_score = short_term.get(
            "score"
        )

        if core_score is not None:

            core_score_int = int(
                core_score
            )

            if 0 <= core_score_int <= 5:

                core_score_distribution[
                    str(core_score_int)
                ] += 1

        # ----------------------------------------------------
        # 籌碼方向統計
        # ----------------------------------------------------

        chip_direction = (
            record.get(
                "chip",
                {}
            ).get(
                "direction",
                "資料不足"
            )
        )

        if chip_direction not in (
            chip_direction_distribution
        ):

            chip_direction = "資料不足"

        chip_direction_distribution[
            chip_direction
        ] += 1

        # ----------------------------------------------------
        # 五項全部符合
        # ----------------------------------------------------

        if short_term.get(
            "qualified",
            False
        ):

            short_candidates.append(
                symbol
            )

        # ----------------------------------------------------
        # DCA
        # ----------------------------------------------------

        dca_action = (
            record.get(
                "dca",
                {}
            ).get(
                "action"
            )
        )

        if dca_action in {
            "積極買進",
            "正常買進",
            "極端負乖離",
        }:

            dca_buy.append(
                symbol
            )

        elif dca_action in {
            "觀察",
            "高檔觀察",
        }:

            dca_observe.append(
                symbol
            )

        elif dca_action == "暫停加碼":

            dca_pause.append(
                symbol
            )

        if (
            index == 1
            or index % 100 == 0
            or index == total
        ):

            log(
                f"分析進度："
                f"{index}/{total}"
            )

    return {

        "results":
            results,

        "statistics":
            {

                "universe":
                    total,

                "price_index":
                    len(price_index),

                "price_loaded":
                    price_loaded,

                "price_missing":
                    price_missing,

                "universe_price_matched":
                    len(
                        matched_symbols
                    ),

                "complete":
                    complete,

                "partial":
                    partial,

                "insufficient":
                    insufficient,

                "short_term_candidates":
                    len(
                        short_candidates
                    ),

                "dca_buy":
                    len(
                        dca_buy
                    ),

                "dca_observe":
                    len(
                        dca_observe
                    ),

                "dca_pause":
                    len(
                        dca_pause
                    ),

                "core_total":
                    5,

                "core_score_distribution":
                    core_score_distribution,

                "chip_direction_distribution":
                    chip_direction_distribution,
            },

        "price_info":
            price_info,

        "missing_price_symbols":
            sorted(
                missing_symbols
            ),

        "short_term_candidates":
            short_candidates,

        "dca_buy":
            dca_buy,

        "dca_observe":
            dca_observe,

        "dca_pause":
            dca_pause,
    }


# ============================================================
# Save Analysis
# ============================================================

def save_analysis(
    analysis: Dict[str, Any]
) -> None:

    section(
        "寫入 Data/analysis.json"
    )

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            {

                "universe":
                    "Data/universe.json",

                "prices":
                    "Data/prices/",

                "price_manifest":
                    "Data/prices/manifest.json",

                "chip":
                    "Data/chip.json",
            },

        # ----------------------------------------------------
        # 正式規則
        # ----------------------------------------------------

        "analysis_rules":
            {

                "short_term":
                    {

                        "core_total":
                            5,

                        "core_conditions":
                            {

                                "1":
                                    "MACD 黃金交叉",

                                "2":
                                    "KD 黃金交叉",

                                "3":
                                    "RSI > 50",

                                "4":
                                    (
                                        "今日成交量 ÷ "
                                        "前5個交易日平均成交量 "
                                        ">= 1.5"
                                    ),

                                "5":
                                    (
                                        "股價 > MA20 "
                                        "且 MA20 向上"
                                    ),
                            },

                        "macd":
                            "MACD 黃金交叉",

                        "kd":
                            "KD 黃金交叉",

                        "rsi":
                            "RSI > 50",

                        "volume":
                            (
                                "今日成交量 ÷ "
                                "前5個交易日平均成交量 "
                                ">= 1.5"
                            ),

                        "price_ma20":
                            (
                                "股價 > MA20 "
                                "且 MA20 向上"
                            ),

                        "minimum_history":
                            SHORT_TERM_CONFIG[
                                "minimum_history"
                            ],

                        "qualification":
                            "五項核心條件全部符合",
                    },

                "chip":
                    {

                        "is_core_condition":
                            False,

                        "1d":
                            "保留並分析",

                        "5d":
                            "保留並分析",

                        "10d":
                            "保留並實際參與籌碼方向分析",

                        "20d":
                            "保留並分析",

                        "direction_basis":
                            [
                                "1D",
                                "5D",
                                "10D",
                                "20D",
                            ],

                        "medium_direction_basis":
                            [
                                "5D",
                                "10D",
                                "20D",
                            ],
                    },

                "dca":
                    DCA_CONFIG,
            },

        # ----------------------------------------------------
        # Data Pipeline
        # ----------------------------------------------------

        "data_pipeline":
            {

                "price_shards":
                    analysis[
                        "price_info"
                    ],

                "price_index_count":
                    analysis[
                        "statistics"
                    ][
                        "price_index"
                    ],

                "universe_price_matched":
                    analysis[
                        "statistics"
                    ][
                        "universe_price_matched"
                    ],

                "price_missing":
                    analysis[
                        "statistics"
                    ][
                        "price_missing"
                    ],

                "missing_price_symbols":
                    analysis[
                        "missing_price_symbols"
                    ],
            },

        # ----------------------------------------------------
        # Statistics
        # ----------------------------------------------------

        "statistics":
            analysis[
                "statistics"
            ],

        # ----------------------------------------------------
        # 五項核心全部符合股票
        # ----------------------------------------------------

        "short_term_candidates":
            analysis[
                "short_term_candidates"
            ],

        # ----------------------------------------------------
        # DCA
        # ----------------------------------------------------

        "dca_buy":
            analysis[
                "dca_buy"
            ],

        "dca_observe":
            analysis[
                "dca_observe"
            ],

        "dca_pause":
            analysis[
                "dca_pause"
            ],

        # ----------------------------------------------------
        # 股票分析結果
        # ----------------------------------------------------

        "stocks":
            analysis[
                "results"
            ],
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 寫入後重新讀取驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(
            f
        )

    if not isinstance(
        verify,
        dict
    ):

        raise RuntimeError(
            "analysis.json 寫入後格式錯誤"
        )

    if not isinstance(
        verify.get(
            "stocks"
        ),
        dict
    ):

        raise RuntimeError(
            "analysis.json stocks 格式錯誤"
        )

    if len(
        verify["stocks"]
    ) != len(
        analysis["results"]
    ):

        raise RuntimeError(
            "analysis.json 股票數量驗證失敗"
        )

    required_top_keys = {

        "schema_version",

        "generated_at",

        "source",

        "analysis_rules",

        "data_pipeline",

        "statistics",

        "short_term_candidates",

        "dca_buy",

        "dca_observe",

        "dca_pause",

        "stocks",
    }

    missing_keys = (
        required_top_keys
        -
        set(
            verify.keys()
        )
    )

    if missing_keys:

        raise RuntimeError(
            "analysis.json 缺少欄位："
            + ", ".join(
                sorted(
                    missing_keys
                )
            )
        )

    # --------------------------------------------------------
    # 強制驗證核心規則 = 5
    # --------------------------------------------------------

    rules = verify[
        "analysis_rules"
    ][
        "short_term"
    ]

    if rules.get(
        "core_total"
    ) != 5:

        raise RuntimeError(
            "❌ 核心條件數量驗證失敗："
            "core_total 必須為 5"
        )

    core_conditions = rules.get(
        "core_conditions"
    )

    if not isinstance(
        core_conditions,
        dict
    ):

        raise RuntimeError(
            "❌ core_conditions 格式錯誤"
        )

    if len(
        core_conditions
    ) != 5:

        raise RuntimeError(
            "❌ 核心條件驗證失敗："
            "必須正好 5 項"
        )

    if (
        core_conditions.get("5")
        !=
        "股價 > MA20 且 MA20 向上"
    ):

        raise RuntimeError(
            "❌ 第五項核心條件不是合併後的 "
            "股價 > MA20 且 MA20 向上"
        )

    # --------------------------------------------------------
    # 驗證每一檔股票都是五項核心
    # --------------------------------------------------------

    for symbol, stock in verify[
        "stocks"
    ].items():

        short_term = stock.get(
            "short_term",
            {}
        )

        if short_term.get(
            "core_total"
        ) != 5:

            raise RuntimeError(
                f"❌ {symbol} "
                "core_total != 5"
            )

        conditions = short_term.get(
            "conditions"
        )

        if not isinstance(
            conditions,
            dict
        ):

            raise RuntimeError(
                f"❌ {symbol} "
                "conditions 格式錯誤"
            )

        if len(
            conditions
        ) != 5:

            raise RuntimeError(
                f"❌ {symbol} "
                "核心條件不是 5 項"
            )

        score = short_term.get(
            "score"
        )

        if (
            not isinstance(
                score,
                int
            )
            or score < 0
            or score > 5
        ):

            raise RuntimeError(
                f"❌ {symbol} "
                "核心分數不是 0~5"
            )

        # ----------------------------------------------------
        # 籌碼不能被算成核心條件
        # ----------------------------------------------------

        if short_term.get(
            "chip_is_core_condition"
        ) is not False:

            raise RuntimeError(
                f"❌ {symbol} "
                "籌碼錯誤地被標記為核心條件"
            )

        # ----------------------------------------------------
        # 10D 必須保留並使用
        # ----------------------------------------------------

        chip = stock.get(
            "chip",
            {}
        )

        if chip.get(
            "ten_day_used"
        ) is not True:

            raise RuntimeError(
                f"❌ {symbol} "
                "10D 未被標記為實際使用"
            )

    # --------------------------------------------------------
    # 最重要的資料管線驗證
    # --------------------------------------------------------

    stats = verify[
        "statistics"
    ]

    universe_count = stats.get(
        "universe",
        0
    )

    matched_count = stats.get(
        "universe_price_matched",
        0
    )

    if (
        universe_count > 0
        and matched_count == 0
    ):

        raise RuntimeError(
            "❌ analysis.json 拒絕輸出："
            "Universe 有股票，但沒有任何價格資料成功對接"
        )

    # --------------------------------------------------------
    # 寫入正式檔案
    # --------------------------------------------------------

    temp_file.replace(
        OUTPUT_FILE
    )

    log(
        f"✓ {OUTPUT_FILE}"
    )


# ============================================================
# Final Summary
# ============================================================

def print_summary(
    analysis: Dict[str, Any],
    elapsed: float
) -> None:

    stats = analysis[
        "statistics"
    ]

    section(
        "分析完成"
    )

    log(
        f"Universe："
        f"{stats['universe']}"
    )

    log(
        f"價格索引："
        f"{stats['price_index']}"
    )

    log(
        f"成功對接："
        f"{stats['universe_price_matched']}"
    )

    log(
        f"價格缺失："
        f"{stats['price_missing']}"
    )

    log(
        f"完整分析："
        f"{stats['complete']}"
    )

    log(
        f"部分分析："
        f"{stats['partial']}"
    )

    log(
        f"歷史不足："
        f"{stats['insufficient']}"
    )

    log("")

    log(
        "================================"
    )

    log(
        "短期核心條件：5 項"
    )

    log(
        "1. MACD 黃金交叉"
    )

    log(
        "2. KD 黃金交叉"
    )

    log(
        "3. RSI > 50"
    )

    log(
        "4. 量比 >= 1.5"
    )

    log(
        "5. 股價 > MA20 且 MA20 向上"
    )

    log(
        "================================"
    )

    log("")

    log(
        "五項核心全部符合："
        f"{stats['short_term_candidates']}"
    )

    log("")

    log(
        "核心分數分布："
    )

    distribution = stats[
        "core_score_distribution"
    ]

    for score in range(
        0,
        6
    ):

        log(
            f"  {score}/5："
            f"{distribution.get(str(score), 0)} 檔"
        )

    log("")

    log(
        "籌碼方向："
    )

    chip_distribution = stats[
        "chip_direction_distribution"
    ]

    for direction in (
        "偏多",
        "分歧",
        "偏空",
        "資料不足",
    ):

        log(
            f"  {direction}："
            f"{chip_distribution.get(direction, 0)} 檔"
        )

    log("")

    log(
        "籌碼分析："
        "1D / 5D / 10D / 20D"
    )

    log(
        "10D：已實際參與籌碼方向分析"
    )

    log(
        "注意：籌碼不是第六項核心條件"
    )

    log("")

    log(
        "零股定投可買："
        f"{stats['dca_buy']}"
    )

    log(
        "零股定投觀察："
        f"{stats['dca_observe']}"
    )

    log(
        "零股定投暫停："
        f"{stats['dca_pause']}"
    )

    log("")

    log(
        "短期成交量規則："
    )

    log(
        "今日成交量 ÷ "
        "前5個交易日平均成交量 >= 1.5"
    )

    log("")

    log(
        f"耗時："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"輸出："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統 "
        f"analyze_stocks.py {VERSION}"
    )

    log("=" * 72)

    try:

        analysis = run_analysis()

        save_analysis(
            analysis
        )

        elapsed = (
            time.time()
            - start
        )

        print_summary(
            analysis,
            elapsed
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            f"❌ analyze_stocks.py "
            f"{VERSION} 執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
