#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/analyze_stocks.py

正式版 V3.4
Entry Timing：ENTRY-TIMING-2.0

============================================================
分析層責任
============================================================

Data/universe.json
Data/prices/
Data/chip.json
        ↓
技術分析
        ↓
籌碼分析
        ↓
DCA 分析
        ↓
五項技術條件
        ↓
強勢股判定
        ↓
Entry Timing 10D / 30D / 60D / 90D 歷史回測
        ↓
Data/analysis.json

本程式不：

    - 抓 API
    - 修改 prices
    - 修改 chip.json
    - 修改 universe.json
    - 修改其他資料來源

============================================================
短期選股：五項技術條件
============================================================

1. MACD 黃金交叉
2. KD 黃金交叉
3. RSI > 50
4. 今日成交量 / 前5個交易日平均成交量 >= 1.5
5. 股價 > MA20 且 MA20 向上

注意：

第五項是單一合併條件。

因此：

    core_total = 5

籌碼不是技術核心條件。

============================================================
Entry Timing 2.0
============================================================

只有當前五項全部符合的強勢股才進行歷史回測。

比較：

A. 立即進場
   歷史訊號成立日收盤價進場

B. 等待回測
   訊號成立後等待價格回到：
   MA20 × 0.98 ~ MA20 × 1.02

   使用區間上緣 MA20 × 1.02
   作為標準化回測進場價格。

回測持有期間：

    10D
    30D
    60D
    90D

每個週期獨立統計：

    trades
    wins
    losses
    win_rate
    avg_return_pct
    median_return_pct
    avg_mfe_pct
    avg_mae_pct

另外統計：

    pullback_trigger_rate

避免只看成功回測的案例。

============================================================
重要
============================================================

90D 回測不會拿不足 90D 的資料硬算。

若歷史資料不足：

    status = insufficient

不會把短資料冒充成完整回測。

============================================================
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import time

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 基本設定
# ============================================================

VERSION = "V3.4"
ENTRY_TIMING_SCHEMA = "ENTRY-TIMING-2.0"

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

    # 五項技術條件最低歷史資料
    "minimum_history": 60,
}


# ============================================================
# DCA
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
# Entry Timing 2.0
# ============================================================

ENTRY_TIMING_CONFIG = {
    # 最低歷史資料。
    # 90D 回測另外會檢查實際可用交易日。
    "minimum_history": 60,

    # 四個回測持有週期
    "holding_periods": [10, 30, 60, 90],

    # 訊號後最多等待回測
    "pullback_wait_days": 10,

    # MA20 合理回測區
    "pullback_lower_pct": 0.98,
    "pullback_upper_pct": 1.02,

    # 判定策略明顯優勢的最小勝率差
    "win_rate_edge": 0.05,

    # 最少歷史訊號
    "minimum_signals": 5,
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

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as exc:
        raise RuntimeError(
            f"JSON 讀取失敗：{path}：{exc}"
        ) from exc


# ============================================================
# Number
# ============================================================

def safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None

    try:
        result = float(
            str(value).replace(",", "").strip()
        )

        if not math.isfinite(result):
            return None

        return result
    except Exception:
        return None


# ============================================================
# Date
# ============================================================

def parse_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    text = str(value).strip()

    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    )

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass

    return None


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip().upper()

    if not text:
        return ""

    for suffix in (".TW", ".TWO"):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
            break

    return text


# ============================================================
# Universe
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:
    data = load_json(UNIVERSE_FILE)

    if not isinstance(data, dict):
        raise RuntimeError("universe.json 格式錯誤")

    stocks = data.get("stocks")
    result: Dict[str, Dict[str, Any]] = {}

    if isinstance(stocks, dict):
        items = stocks.items()

        for raw_symbol, item in items:
            if not isinstance(item, dict):
                continue

            symbol = normalize_symbol(
                item.get("symbol") or raw_symbol
            )

            if not symbol:
                continue

            result[symbol] = {
                "symbol": symbol,
                "name": str(
                    item.get("name", "")
                ).strip(),
                "market": str(
                    item.get("market", "")
                ).strip(),
                "type": str(
                    item.get("type", "")
                ).strip(),
                "full_symbol": str(
                    item.get("full_symbol", "")
                ).strip(),
            }

    else:
        items = data.get("items", [])

        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue

                symbol = normalize_symbol(
                    item.get("symbol")
                    or item.get("code")
                    or item.get("ticker")
                )

                if not symbol:
                    continue

                result[symbol] = {
                    "symbol": symbol,
                    "name": str(
                        item.get("name", "")
                    ).strip(),
                    "market": str(
                        item.get("market", "")
                    ).strip(),
                    "type": str(
                        item.get("type", "")
                    ).strip(),
                    "full_symbol": str(
                        item.get("full_symbol", "")
                    ).strip(),
                }

    if not result:
        raise RuntimeError(
            "Universe 沒有有效股票資料"
        )

    log(f"Universe：{len(result)} 檔")
    return result


# ============================================================
# Price Manifest
# ============================================================

def load_price_manifest() -> Dict[str, Any]:
    data = load_json(PRICES_MANIFEST)

    if not isinstance(data, dict):
        raise RuntimeError(
            "prices/manifest.json 格式錯誤"
        )

    return data


# ============================================================
# Price Shards
# ============================================================

def get_price_shard_files(
    manifest: Dict[str, Any]
) -> List[Path]:

    filenames: List[str] = []

    files = manifest.get("files")

    if isinstance(files, list):
        for item in files:

            if isinstance(item, str):
                filenames.append(item)

            elif isinstance(item, dict):
                for key in (
                    "file",
                    "path",
                    "filename",
                    "name",
                ):
                    value = item.get(key)

                    if value:
                        filenames.append(str(value))
                        break

    if not filenames and PRICES_DIR.exists():
        filenames = [
            path.name
            for path in sorted(
                PRICES_DIR.glob("prices_*.json")
            )
        ]

    result: List[Path] = []
    seen = set()

    for filename in filenames:

        filename = str(filename).replace(
            "\\", "/"
        )

        if filename in seen:
            continue

        seen.add(filename)

        candidates = (
            PRICES_DIR / filename,
            DATA_DIR / filename,
            BASE_DIR / filename,
        )

        for candidate in candidates:
            if candidate.exists():
                result.append(candidate)
                break

    result.sort(key=lambda x: x.name)
    return result


# ============================================================
# Price extraction
# ============================================================

def extract_history_candidate(value: Any) -> Any:

    if isinstance(value, list):
        return value

    if isinstance(value, dict):

        for key in (
            "prices",
            "history",
            "data",
            "rows",
            "records",
            "daily",
        ):
            candidate = value.get(key)

            if isinstance(candidate, list):
                return candidate

        keys = {
            str(k).lower()
            for k in value.keys()
        }

        if keys & {
            "date",
            "trade_date",
            "close",
            "price",
        }:
            return [value]

    return None


def extract_stocks_from_shard(
    data: Any
) -> Dict[str, Any]:

    result: Dict[str, Any] = {}

    if isinstance(data, dict):

        for container_key in (
            "stocks",
            "data",
            "prices",
            "history",
        ):

            container = data.get(container_key)

            if isinstance(container, dict):

                for raw_symbol, value in container.items():

                    symbol = normalize_symbol(raw_symbol)
                    history = extract_history_candidate(value)

                    if symbol and history is not None:
                        result[symbol] = history

                if result:
                    return result

        for raw_symbol, value in data.items():

            symbol = normalize_symbol(raw_symbol)

            if not symbol:
                continue

            history = extract_history_candidate(value)

            if history is not None:
                result[symbol] = history

        if result:
            return result

        symbol = normalize_symbol(
            data.get("symbol")
            or data.get("code")
            or data.get("ticker")
        )

        if symbol:
            history = extract_history_candidate(data)

            if history is not None:
                result[symbol] = history

    elif isinstance(data, list):

        for item in data:

            if not isinstance(item, dict):
                continue

            symbol = normalize_symbol(
                item.get("symbol")
                or item.get("code")
                or item.get("ticker")
                or item.get("stock_id")
            )

            if not symbol:
                continue

            history = extract_history_candidate(item)

            if history is not None:
                result[symbol] = history

    return result


# ============================================================
# Parse history
# ============================================================

def parse_price_history(
    data: Any
) -> List[Dict[str, Any]]:

    raw_rows: List[Any] = []

    if isinstance(data, list):
        raw_rows = data

    elif isinstance(data, dict):

        candidate = (
            data.get("prices")
            or data.get("data")
            or data.get("history")
            or data.get("rows")
            or data.get("records")
            or []
        )

        if isinstance(candidate, dict):

            for date, value in candidate.items():

                if isinstance(value, dict):
                    row = dict(value)
                    row.setdefault("date", date)
                    raw_rows.append(row)

                else:
                    raw_rows.append({
                        "date": date,
                        "close": value,
                    })

        elif isinstance(candidate, list):
            raw_rows = candidate

    rows: List[Dict[str, Any]] = []

    for row in raw_rows:

        if not isinstance(row, dict):
            continue

        date_value = (
            row.get("date")
            or row.get("Date")
            or row.get("trade_date")
            or row.get("TradeDate")
            or row.get("tradedate")
        )

        dt = parse_date(date_value)

        if dt is None:
            continue

        close = (
            row.get("close")
            if row.get("close") is not None
            else row.get("Close")
        )

        if close is None:
            close = row.get("price") or row.get("Price")

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

        close = safe_float(close)
        volume = safe_float(volume)
        high = safe_float(high)
        low = safe_float(low)

        if close is None:
            continue

        rows.append({
            "date": dt.strftime("%Y-%m-%d"),
            "close": close,
            "volume": volume,
            "high": high,
            "low": low,
        })

    unique: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        unique[row["date"]] = row

    rows = list(unique.values())
    rows.sort(key=lambda row: row["date"])

    return rows


# ============================================================
# Price index
# ============================================================

def load_price_index(
    manifest: Dict[str, Any]
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:

    section("載入價格分檔")

    shard_files = get_price_shard_files(manifest)

    if not shard_files:
        raise RuntimeError(
            "找不到任何 prices shard"
        )

    price_index: Dict[str, List[Dict[str, Any]]] = {}
    shard_statistics = []

    for index, path in enumerate(
        shard_files,
        start=1
    ):

        try:
            data = load_json(path)
            stocks = extract_stocks_from_shard(data)

            loaded = 0

            for symbol, history in stocks.items():

                rows = parse_price_history(history)

                if rows:
                    price_index[symbol] = rows
                    loaded += 1

            shard_statistics.append({
                "file": path.name,
                "stocks_detected": len(stocks),
                "stocks_loaded": loaded,
            })

            log(
                f"[{index:02d}/{len(shard_files):02d}] "
                f"{path.name} → {loaded} 檔有效"
            )

        except Exception as exc:

            log(
                f"⚠️ {path.name} 讀取失敗：{exc}"
            )

            shard_statistics.append({
                "file": path.name,
                "stocks_detected": 0,
                "stocks_loaded": 0,
                "error": str(exc),
            })

    if not price_index:
        raise RuntimeError(
            "價格 shard 載入後沒有有效股票"
        )

    log(
        f"價格索引完成：{len(price_index)} 檔"
    )

    return (
        price_index,
        {
            "shard_count": len(shard_files),
            "stock_count": len(price_index),
            "shards": shard_statistics,
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

    return sum(values[-period:]) / period


def previous_moving_average(
    values: List[float],
    period: int
) -> Optional[float]:

    if len(values) < period + 1:
        return None

    return sum(
        values[-period - 1:-1]
    ) / period


# ============================================================
# EMA
# ============================================================

def ema_series(
    values: List[float],
    period: int
) -> List[float]:

    if len(values) < period:
        return []

    multiplier = 2.0 / (period + 1)

    ema = sum(values[:period]) / period
    result = [ema]

    for value in values[period:]:
        ema = (
            (value - ema) * multiplier
            + ema
        )
        result.append(ema)

    return result


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    closes: List[float]
) -> Dict[str, Any]:

    fast = SHORT_TERM_CONFIG["macd_fast"]
    slow = SHORT_TERM_CONFIG["macd_slow"]
    signal_period = SHORT_TERM_CONFIG["macd_signal"]

    if len(closes) < slow + signal_period:
        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)

    if not ema_fast or not ema_slow:
        return {
            "macd": None,
            "signal": None,
            "histogram": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    offset = len(ema_fast) - len(ema_slow)
    macd_series = []

    for i, slow_value in enumerate(ema_slow):
        fast_value = ema_fast[i + offset]
        macd_series.append(
            fast_value - slow_value
        )

    signal_series = ema_series(
        macd_series,
        signal_period
    )

    if len(signal_series) < 2:
        return {
            "macd": round(macd_series[-1], 4),
            "signal": None,
            "histogram": None,
            "golden_cross": False,
            "status": "partial",
        }

    current_macd = macd_series[-1]
    previous_macd = macd_series[-2]
    current_signal = signal_series[-1]
    previous_signal = signal_series[-2]

    golden_cross = (
        previous_macd <= previous_signal
        and current_macd > current_signal
    )

    return {
        "macd": round(current_macd, 4),
        "signal": round(current_signal, 4),
        "histogram": round(
            current_macd - current_signal,
            4
        ),
        "golden_cross": golden_cross,
        "status": "complete",
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

    for i in range(1, len(closes)):

        change = closes[i] - closes[i - 1]

        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return round(
        100.0 - 100.0 / (1.0 + rs),
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
            "k": None,
            "d": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    highs = []
    lows = []
    closes = []

    for row in rows:

        close = safe_float(row.get("close"))

        if close is None:
            continue

        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))

        highs.append(
            high if high is not None else close
        )

        lows.append(
            low if low is not None else close
        )

        closes.append(close)

    if len(closes) < period:
        return {
            "k": None,
            "d": None,
            "golden_cross": False,
            "status": "insufficient",
        }

    k = 50.0
    d = 50.0

    k_values = []
    d_values = []

    for i in range(period - 1, len(closes)):

        high_window = highs[
            i - period + 1:i + 1
        ]

        low_window = lows[
            i - period + 1:i + 1
        ]

        window_high = max(high_window)
        window_low = min(low_window)

        denominator = window_high - window_low

        if denominator == 0:
            rsv = 50.0
        else:
            rsv = (
                (closes[i] - window_low)
                / denominator
                * 100.0
            )

        k = (k * 2.0 + rsv) / 3.0
        d = (d * 2.0 + k) / 3.0

        k_values.append(k)
        d_values.append(d)

    golden_cross = False

    if len(k_values) >= 2:
        golden_cross = (
            k_values[-2] <= d_values[-2]
            and k_values[-1] > d_values[-1]
        )

    return {
        "k": round(k_values[-1], 2),
        "d": round(d_values[-1], 2),
        "golden_cross": golden_cross,
        "status": "complete",
    }


# ============================================================
# Price metrics
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

    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    previous_ma20 = previous_moving_average(
        closes,
        20
    )
    ma60 = moving_average(closes, 60)

    volumes = [
        safe_float(row.get("volume"))
        for row in rows
    ]

    today_volume = volumes[-1] if volumes else None

    previous_5_avg = None

    if len(volumes) >= 6:

        previous_5 = [
            value
            for value in volumes[-6:-1]
            if value is not None
        ]

        if len(previous_5) == 5:
            previous_5_avg = sum(previous_5) / 5.0

    volume_ratio = None

    if (
        today_volume is not None
        and previous_5_avg is not None
        and previous_5_avg > 0
    ):
        volume_ratio = (
            today_volume / previous_5_avg
        )

    if len(closes) >= 60:
        high60 = max(closes[-60:])
        low60 = min(closes[-60:])
    else:
        high60 = max(closes)
        low60 = min(closes)

    bias20_pct = None

    if ma20 is not None and ma20 != 0:
        bias20_pct = (
            current_price / ma20 - 1.0
        ) * 100.0

    position60_pct = None

    if high60 != low60:
        position60_pct = (
            (current_price - low60)
            / (high60 - low60)
            * 100.0
        )

    ma20_ratio = (
        current_price / ma20
        if ma20 is not None and ma20 != 0
        else None
    )

    low60_ratio = (
        current_price / low60
        if low60
        else None
    )

    price_above_ma20 = (
        ma20 is not None
        and current_price > ma20
    )

    ma20_up = (
        ma20 is not None
        and previous_ma20 is not None
        and ma20 > previous_ma20
    )

    change1_pct = None
    change5_pct = None
    change20_pct = None

    if len(closes) >= 2:
        change1_pct = (
            current_price / closes[-2] - 1.0
        ) * 100.0

    if len(closes) >= 6:
        change5_pct = (
            current_price / closes[-6] - 1.0
        ) * 100.0

    if len(closes) >= 21:
        change20_pct = (
            current_price / closes[-21] - 1.0
        ) * 100.0

    if volume_ratio is None:
        volume_signal = "資料不足"
    elif volume_ratio >= 1.5:
        volume_signal = "放量"
    elif volume_ratio >= 1.0:
        volume_signal = "正常"
    elif volume_ratio >= 0.5:
        volume_signal = "量縮"
    else:
        volume_signal = "明顯量縮"

    return {
        "price": round(current_price, 2),
        "ma5": round(ma5, 2) if ma5 is not None else None,
        "ma20": round(ma20, 2) if ma20 is not None else None,
        "ma60": round(ma60, 2) if ma60 is not None else None,

        "price_above_ma20": price_above_ma20,
        "ma20_up": ma20_up,
        "price_above_ma20_and_ma20_up": (
            price_above_ma20 and ma20_up
        ),

        "ma20_ratio": (
            round(ma20_ratio, 4)
            if ma20_ratio is not None
            else None
        ),

        "bias20_pct": (
            round(bias20_pct, 2)
            if bias20_pct is not None
            else None
        ),

        "change1_pct": (
            round(change1_pct, 2)
            if change1_pct is not None
            else None
        ),

        "change5_pct": (
            round(change5_pct, 2)
            if change5_pct is not None
            else None
        ),

        "change20_pct": (
            round(change20_pct, 2)
            if change20_pct is not None
            else None
        ),

        "high60": round(high60, 2),
        "low60": round(low60, 2),

        "position60_pct": (
            round(position60_pct, 2)
            if position60_pct is not None
            else None
        ),

        "low60_ratio": (
            round(low60_ratio, 4)
            if low60_ratio is not None
            else None
        ),

        "volume": today_volume,

        "volume5_previous_avg": (
            round(previous_5_avg, 2)
            if previous_5_avg is not None
            else None
        ),

        "volume_ratio_vs_previous_5": (
            round(volume_ratio, 4)
            if volume_ratio is not None
            else None
        ),

        "volume_signal": volume_signal,
    }


# ============================================================
# Chip
# ============================================================

def chip_value(
    chip: Dict[str, Any],
    key: str
) -> Optional[float]:

    value = chip.get(key)

    aliases = {
        "main_force_1d": (
            "main_force_1D",
            "main_force_1d_pct",
            "main_force_1D_pct",
            "mf1",
            "1d",
        ),
        "main_force_5d": (
            "main_force_5D",
            "main_force_5d_pct",
            "main_force_5D_pct",
            "mf5",
            "5d",
        ),
        "main_force_10d": (
            "main_force_10D",
            "main_force_10d_pct",
            "main_force_10D_pct",
            "mf10",
            "10d",
        ),
        "main_force_20d": (
            "main_force_20D",
            "main_force_20d_pct",
            "main_force_20D_pct",
            "mf20",
            "20d",
        ),
    }

    if value is None:

        for alias in aliases.get(key, ()):

            if alias in chip:
                value = chip.get(alias)
                break

    return safe_float(value)


def calculate_chip_analysis(
    chip: Dict[str, Any]
) -> Dict[str, Any]:

    mf1 = chip_value(chip, "main_force_1d")
    mf5 = chip_value(chip, "main_force_5d")
    mf10 = chip_value(chip, "main_force_10d")
    mf20 = chip_value(chip, "main_force_20d")

    values = [
        value
        for value in (mf1, mf5, mf10, mf20)
        if value is not None
    ]

    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)

    if not values:
        direction = "資料不足"
    elif positive >= 3:
        direction = "偏多"
    elif negative >= 3:
        direction = "偏空"
    else:
        direction = "分歧"

    medium_values = [
        value
        for value in (mf5, mf10, mf20)
        if value is not None
    ]

    medium_positive = sum(
        value > 0
        for value in medium_values
    )

    medium_negative = sum(
        value < 0
        for value in medium_values
    )

    if not medium_values:
        medium_direction = "資料不足"
    elif medium_positive >= 2:
        medium_direction = "中期偏多"
    elif medium_negative >= 2:
        medium_direction = "中期偏空"
    else:
        medium_direction = "中期分歧"

    if mf10 is None:
        ten_day_direction = "資料不足"
    elif mf10 > 0:
        ten_day_direction = "偏多"
    elif mf10 < 0:
        ten_day_direction = "偏空"
    else:
        ten_day_direction = "中性"

    return {
        "main_force_1d": mf1,
        "main_force_5d": mf5,
        "main_force_10d": mf10,
        "main_force_20d": mf20,

        "positive_count": positive,
        "negative_count": negative,

        "direction": direction,
        "medium_direction": medium_direction,
        "ten_day_direction": ten_day_direction,

        "ten_day_used": True,

        "analysis_basis": [
            "1D",
            "5D",
            "10D",
            "20D",
        ],

        "medium_analysis_basis": [
            "5D",
            "10D",
            "20D",
        ],
    }


# ============================================================
# 五項核心判定
# ============================================================

def evaluate_short_term(
    metrics: Dict[str, Any],
    macd: Dict[str, Any],
    kd: Dict[str, Any],
    rsi: Optional[float],
    chip: Dict[str, Any],
) -> Dict[str, Any]:

    conditions = {
        "macd_golden_cross": bool(
            macd.get("golden_cross", False)
        ),

        "kd_golden_cross": bool(
            kd.get("golden_cross", False)
        ),

        "rsi_above_50": (
            rsi is not None
            and rsi > SHORT_TERM_CONFIG["rsi_min"]
        ),

        "volume_ratio_gt_1_5": (
            metrics.get(
                "volume_ratio_vs_previous_5"
            ) is not None
            and
            metrics.get(
                "volume_ratio_vs_previous_5"
            ) >= SHORT_TERM_CONFIG["volume_multiplier"]
        ),

        "price_above_ma20_and_ma20_up": bool(
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

    qualified = score == core_total

    chip_analysis = calculate_chip_analysis(chip)

    if score >= 5:
        technical_strength = "強勢"
    elif score >= 4:
        technical_strength = "偏強"
    elif score >= 3:
        technical_strength = "中性"
    else:
        technical_strength = "偏弱"

    chip_direction = chip_analysis.get(
        "direction",
        "資料不足"
    )

    if qualified:

        if chip_direction == "偏多":
            direction = "偏多"
            operation = "偏多，可分批"

        elif chip_direction == "偏空":
            direction = "技術偏多、籌碼偏空"
            operation = "技術符合，籌碼觀察"

        elif chip_direction == "分歧":
            direction = "技術偏多、籌碼分歧"
            operation = "可觀察，分批"

        else:
            direction = "技術偏多"
            operation = "偏多，可分批"

    else:

        if chip_direction == "偏空":
            direction = "偏空"
            operation = "暫停操作"

        elif chip_direction == "偏多":
            direction = "技術未完全符合、籌碼偏多"
            operation = "觀察"

        else:
            direction = "中性"
            operation = "觀察"

    return {
        "qualified": qualified,

        "score": score,
        "core_score": score,
        "core_total": core_total,

        "core_pass_ratio": round(
            score / core_total,
            4
        ),

        "conditions": conditions,

        "technical_strength": technical_strength,
        "operation": operation,
        "direction": direction,

        "rsi": rsi,
        "macd": macd,
        "kd": kd,

        "volume_ratio": metrics.get(
            "volume_ratio_vs_previous_5"
        ),

        "chip": chip_analysis,

        "chip_is_core_condition": False,
        "ten_day_chip_used": True,
    }


# ============================================================
# DCA
# ============================================================

def calculate_loss_warning(
    current_price: Optional[float],
    chip: Dict[str, Any],
) -> Dict[str, Any]:

    cost = None

    for key in (
        "avg_cost",
        "average_cost",
        "cost",
    ):

        cost = safe_float(chip.get(key))

        if cost is not None:
            break

    if (
        current_price is None
        or cost is None
        or cost <= 0
    ):

        return {
            "available": False,
            "cost": None,
            "loss_pct": None,
            "warning": False,
            "message": "無持倉成本資料",
        }

    loss_pct = (
        (current_price - cost)
        / cost
        * 100.0
    )

    warning = (
        loss_pct
        <= -DCA_CONFIG["max_loss_pct"]
    )

    return {
        "available": True,
        "cost": round(cost, 2),
        "loss_pct": round(loss_pct, 2),
        "warning": warning,
        "message": (
            "達到最大虧損警戒"
            if warning
            else "未達最大虧損警戒"
        ),
    }


def evaluate_dca(
    metrics: Dict[str, Any],
    chip: Dict[str, Any],
) -> Dict[str, Any]:

    ma20_ratio = metrics.get("ma20_ratio")
    low60_ratio = metrics.get("low60_ratio")
    bias20 = metrics.get("bias20_pct")
    position60 = metrics.get("position60_pct")
    volume_ratio = metrics.get(
        "volume_ratio_vs_previous_5"
    )

    chip_analysis = calculate_chip_analysis(chip)

    if ma20_ratio is None:
        action = "資料不足"
        level = 0

    elif (
        ma20_ratio <= DCA_CONFIG["ma20_aggressive"]
        and low60_ratio is not None
        and low60_ratio <= DCA_CONFIG["low60_aggressive"]
    ):
        action = "積極買進"
        level = 3

    elif ma20_ratio <= DCA_CONFIG["ma20_normal"]:
        action = "正常買進"
        level = 2

    elif ma20_ratio <= DCA_CONFIG["ma20_pause"]:
        action = "觀察"
        level = 1

    else:
        action = "暫停加碼"
        level = 0

    if volume_ratio is None:
        volume_signal = "資料不足"
    elif volume_ratio >= 1.5:
        volume_signal = "放量"
    elif volume_ratio >= 1.0:
        volume_signal = "正常"
    elif volume_ratio >= 0.5:
        volume_signal = "量縮"
    else:
        volume_signal = "明顯量縮"

    medium_direction = chip_analysis.get(
        "medium_direction"
    )

    if medium_direction == "中期偏多":
        chip_adjustment = "positive"
    elif medium_direction == "中期偏空":
        chip_adjustment = "negative"
    else:
        chip_adjustment = "neutral"

    rebalance_signal = "normal"

    if position60 is not None:

        if position60 >= DCA_CONFIG["rebalance_high"]:
            rebalance_signal = "高檔"

        elif position60 >= DCA_CONFIG["rebalance_warn"]:
            rebalance_signal = "偏高"

    extreme_discount = (
        bias20 is not None
        and bias20 <= DCA_CONFIG["extreme_bias"]
    )

    if extreme_discount:
        action = "極端負乖離"
        level += 1

    if (
        rebalance_signal == "高檔"
        and level > 0
    ):
        action = "高檔觀察"
        level = max(0, level - 1)

    risk_flags = []

    if rebalance_signal == "高檔":
        risk_flags.append("60日高檔")

    if volume_signal == "明顯量縮":
        risk_flags.append("成交量明顯萎縮")

    if chip_adjustment == "negative":
        risk_flags.append("中期籌碼偏空")

    loss_warning = calculate_loss_warning(
        metrics.get("price"),
        chip
    )

    if loss_warning.get("warning"):
        risk_flags.append("最大虧損警戒")

    return {
        "action": action,
        "level": level,

        "ma20_ratio": ma20_ratio,
        "low60_ratio": low60_ratio,
        "bias20_pct": bias20,
        "position60_pct": position60,

        "change1_pct": metrics.get("change1_pct"),
        "change5_pct": metrics.get("change5_pct"),
        "change20_pct": metrics.get("change20_pct"),

        "volume_ratio": volume_ratio,
        "volume_signal": volume_signal,

        "chip_direction": chip_analysis.get("direction"),
        "medium_chip_direction": medium_direction,
        "chip_adjustment": chip_adjustment,

        "rebalance_signal": rebalance_signal,
        "extreme_discount": extreme_discount,
        "loss_warning": loss_warning,
        "risk_flags": risk_flags,

        "main_force_1d": chip_analysis.get("main_force_1d"),
        "main_force_5d": chip_analysis.get("main_force_5d"),
        "main_force_10d": chip_analysis.get("main_force_10d"),
        "main_force_20d": chip_analysis.get("main_force_20d"),

        "ten_day_used": True,
    }


# ============================================================
# Entry Timing：歷史訊號
# ============================================================

def historical_signal(
    rows: List[Dict[str, Any]],
    index: int,
) -> Optional[Dict[str, Any]]:

    minimum_history = ENTRY_TIMING_CONFIG[
        "minimum_history"
    ]

    if index < minimum_history - 1:
        return None

    history = rows[:index + 1]

    closes = [
        row["close"]
        for row in history
        if row.get("close") is not None
    ]

    if len(closes) < minimum_history:
        return None

    metrics = calculate_price_metrics(history)
    macd = calculate_macd(closes)
    kd = calculate_kd(history)
    rsi = calculate_rsi(
        closes,
        SHORT_TERM_CONFIG["rsi_period"]
    )

    if not metrics:
        return None

    conditions = {
        "macd": bool(
            macd.get("golden_cross", False)
        ),

        "kd": bool(
            kd.get("golden_cross", False)
        ),

        "rsi": (
            rsi is not None
            and rsi > SHORT_TERM_CONFIG["rsi_min"]
        ),

        "volume": (
            metrics.get(
                "volume_ratio_vs_previous_5"
            ) is not None
            and
            metrics.get(
                "volume_ratio_vs_previous_5"
            ) >= SHORT_TERM_CONFIG["volume_multiplier"]
        ),

        "ma20": bool(
            metrics.get(
                "price_above_ma20_and_ma20_up",
                False
            )
        ),
    }

    if not all(conditions.values()):
        return None

    return {
        "index": index,
        "date": history[-1]["date"],
        "close": history[-1]["close"],
        "ma20": metrics.get("ma20"),
        "conditions": conditions,
    }


# ============================================================
# Entry Timing：空統計
# ============================================================

def empty_entry_stats() -> Dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "avg_return_pct": None,
        "median_return_pct": None,
        "avg_mfe_pct": None,
        "avg_mae_pct": None,
    }


# ============================================================
# Entry Timing：統計
# ============================================================

def summarize_entry_trades(
    trades: List[Dict[str, Any]]
) -> Dict[str, Any]:

    if not trades:
        return empty_entry_stats()

    returns = [
        trade["return_pct"]
        for trade in trades
        if trade.get("return_pct") is not None
    ]

    mfe_values = [
        trade["mfe_pct"]
        for trade in trades
        if trade.get("mfe_pct") is not None
    ]

    mae_values = [
        trade["mae_pct"]
        for trade in trades
        if trade.get("mae_pct") is not None
    ]

    wins = sum(value > 0 for value in returns)
    losses = sum(value <= 0 for value in returns)

    return {
        "trades": len(returns),
        "wins": wins,
        "losses": losses,

        "win_rate": (
            round(wins / len(returns), 4)
            if returns
            else None
        ),

        "avg_return_pct": (
            round(statistics.mean(returns), 2)
            if returns
            else None
        ),

        "median_return_pct": (
            round(statistics.median(returns), 2)
            if returns
            else None
        ),

        "avg_mfe_pct": (
            round(statistics.mean(mfe_values), 2)
            if mfe_values
            else None
        ),

        "avg_mae_pct": (
            round(statistics.mean(mae_values), 2)
            if mae_values
            else None
        ),
    }


# ============================================================
# Entry Timing：立即進場
# ============================================================

def simulate_immediate_entry(
    rows: List[Dict[str, Any]],
    signal_index: int,
    holding_days: int,
) -> Optional[Dict[str, Any]]:

    exit_index = signal_index + holding_days

    if exit_index >= len(rows):
        return None

    entry_price = safe_float(
        rows[signal_index].get("close")
    )

    exit_price = safe_float(
        rows[exit_index].get("close")
    )

    if (
        entry_price is None
        or exit_price is None
        or entry_price <= 0
    ):
        return None

    future_rows = rows[
        signal_index + 1:
        exit_index + 1
    ]

    highs = [
        safe_float(row.get("high"))
        for row in future_rows
    ]

    lows = [
        safe_float(row.get("low"))
        for row in future_rows
    ]

    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]

    mfe = (
        (
            max(highs) / entry_price - 1.0
        ) * 100.0
        if highs
        else None
    )

    mae = (
        (
            min(lows) / entry_price - 1.0
        ) * 100.0
        if lows
        else None
    )

    return {
        "signal_date": rows[signal_index]["date"],
        "entry_date": rows[signal_index]["date"],
        "exit_date": rows[exit_index]["date"],

        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),

        "return_pct": round(
            (
                exit_price / entry_price - 1.0
            ) * 100.0,
            2
        ),

        "mfe_pct": (
            round(mfe, 2)
            if mfe is not None
            else None
        ),

        "mae_pct": (
            round(mae, 2)
            if mae is not None
            else None
        ),
    }


# ============================================================
# Entry Timing：尋找回測
# ============================================================

def find_pullback_entry(
    rows: List[Dict[str, Any]],
    signal_index: int,
    ma20: float,
) -> Optional[Dict[str, Any]]:

    lower = (
        ma20
        * ENTRY_TIMING_CONFIG["pullback_lower_pct"]
    )

    upper = (
        ma20
        * ENTRY_TIMING_CONFIG["pullback_upper_pct"]
    )

    wait_days = ENTRY_TIMING_CONFIG[
        "pullback_wait_days"
    ]

    last_index = min(
        len(rows) - 1,
        signal_index + wait_days
    )

    for index in range(
        signal_index + 1,
        last_index + 1
    ):

        row = rows[index]

        low = safe_float(row.get("low"))
        high = safe_float(row.get("high"))
        close = safe_float(row.get("close"))

        if low is None:
            continue

        if low <= upper:

            if (
                high is not None
                and high < lower
            ):
                continue

            return {
                "index": index,
                "date": row["date"],
                "entry_price": upper,
                "zone_lower": lower,
                "zone_upper": upper,
                "close": close,
            }

    return None


# ============================================================
# Entry Timing：回測進場
# ============================================================

def simulate_pullback_entry(
    rows: List[Dict[str, Any]],
    signal_index: int,
    ma20: float,
    holding_days: int,
) -> Tuple[
    Optional[Dict[str, Any]],
    bool,
]:

    pullback = find_pullback_entry(
        rows,
        signal_index,
        ma20
    )

    if pullback is None:
        return None, False

    entry_index = pullback["index"]

    exit_index = entry_index + holding_days

    # 已觸發回測，但後續資料不足
    if exit_index >= len(rows):
        return None, True

    entry_price = pullback["entry_price"]

    exit_price = safe_float(
        rows[exit_index].get("close")
    )

    if (
        entry_price is None
        or exit_price is None
        or entry_price <= 0
    ):
        return None, True

    future_rows = rows[
        entry_index + 1:
        exit_index + 1
    ]

    highs = [
        safe_float(row.get("high"))
        for row in future_rows
    ]

    lows = [
        safe_float(row.get("low"))
        for row in future_rows
    ]

    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]

    mfe = (
        (
            max(highs) / entry_price - 1.0
        ) * 100.0
        if highs
        else None
    )

    mae = (
        (
            min(lows) / entry_price - 1.0
        ) * 100.0
        if lows
        else None
    )

    return {
        "signal_date": rows[signal_index]["date"],
        "entry_date": pullback["date"],
        "exit_date": rows[exit_index]["date"],

        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),

        "return_pct": round(
            (
                exit_price / entry_price - 1.0
            ) * 100.0,
            2
        ),

        "mfe_pct": (
            round(mfe, 2)
            if mfe is not None
            else None
        ),

        "mae_pct": (
            round(mae, 2)
            if mae is not None
            else None
        ),

        "wait_days": (
            entry_index - signal_index
        ),

        "pullback_zone": {
            "lower": round(
                pullback["zone_lower"],
                2
            ),
            "upper": round(
                pullback["zone_upper"],
                2
            ),
        },
    }, True


# ============================================================
# Entry Timing：單一週期
# ============================================================

def backtest_one_period(
    rows: List[Dict[str, Any]],
    holding_days: int,
) -> Dict[str, Any]:

    minimum_history = ENTRY_TIMING_CONFIG[
        "minimum_history"
    ]

    required_history = (
        minimum_history
        + holding_days
    )

    if len(rows) < required_history:
        return {
            "status": "insufficient",
            "holding_days": holding_days,
            "signal_count": 0,
            "immediate": empty_entry_stats(),
            "pullback": empty_entry_stats(),
            "pullback_trigger_rate": None,
            "pullback_completed_trades": 0,
            "pullback_triggered": 0,
            "reason": (
                f"至少需要約 {required_history} "
                "個交易日資料"
            ),
        }

    immediate_trades = []
    pullback_trades = []

    signal_count = 0
    pullback_triggered = 0

    start_index = minimum_history - 1

    last_signal_index = (
        len(rows)
        - holding_days
        - 1
    )

    for signal_index in range(
        start_index,
        last_signal_index + 1
    ):

        signal = historical_signal(
            rows,
            signal_index
        )

        if signal is None:
            continue

        signal_count += 1

        immediate = simulate_immediate_entry(
            rows,
            signal_index,
            holding_days
        )

        if immediate is not None:
            immediate_trades.append(immediate)

        ma20 = safe_float(
            signal.get("ma20")
        )

        if ma20 is None or ma20 <= 0:
            continue

        pullback, triggered = simulate_pullback_entry(
            rows,
            signal_index,
            ma20,
            holding_days
        )

        if triggered:
            pullback_triggered += 1

        if pullback is not None:
            pullback_trades.append(pullback)

    immediate_stats = summarize_entry_trades(
        immediate_trades
    )

    pullback_stats = summarize_entry_trades(
        pullback_trades
    )

    trigger_rate = None

    if signal_count > 0:
        trigger_rate = round(
            pullback_triggered
            / signal_count,
            4
        )

    minimum_signals = ENTRY_TIMING_CONFIG[
        "minimum_signals"
    ]

    immediate_win = immediate_stats.get(
        "win_rate"
    )

    pullback_win = pullback_stats.get(
        "win_rate"
    )

    preferred = "observe"

    if (
        signal_count >= minimum_signals
        and immediate_win is not None
        and pullback_win is not None
        and pullback_stats["trades"] >= minimum_signals
    ):

        edge = ENTRY_TIMING_CONFIG[
            "win_rate_edge"
        ]

        if immediate_win >= pullback_win + edge:
            preferred = "buy_now"

        elif pullback_win >= immediate_win + edge:
            preferred = "wait_pullback"

        else:
            preferred = "observe"

    elif (
        signal_count >= minimum_signals
        and immediate_win is not None
        and pullback_win is None
    ):

        preferred = "buy_now"

    elif (
        signal_count >= minimum_signals
        and pullback_win is not None
        and immediate_win is not None
    ):

        preferred = (
            "wait_pullback"
            if pullback_win > immediate_win
            else "buy_now"
            if immediate_win > pullback_win
            else "observe"
        )

    status = (
        "complete"
        if signal_count >= minimum_signals
        else "insufficient"
    )

    if status == "insufficient":
        preferred = "observe"

    return {
        "status": status,
        "holding_days": holding_days,

        "signal_count": signal_count,

        "immediate": immediate_stats,
        "pullback": pullback_stats,

        "pullback_trigger_rate": trigger_rate,

        "pullback_triggered": pullback_triggered,

        "pullback_completed_trades": (
            len(pullback_trades)
        ),

        "preferred": preferred,

        "configuration": {
            "holding_days": holding_days,
            "pullback_wait_days": (
                ENTRY_TIMING_CONFIG[
                    "pullback_wait_days"
                ]
            ),
            "pullback_lower_pct": (
                ENTRY_TIMING_CONFIG[
                    "pullback_lower_pct"
                ]
            ),
            "pullback_upper_pct": (
                ENTRY_TIMING_CONFIG[
                    "pullback_upper_pct"
                ]
            ),
            "minimum_signals": minimum_signals,
        },
    }


# ============================================================
# Entry Timing：四週期總回測
# ============================================================

def backtest_entry_timing(
    rows: List[Dict[str, Any]]
) -> Dict[str, Any]:

    periods = ENTRY_TIMING_CONFIG[
        "holding_periods"
    ]

    period_results: Dict[str, Any] = {}

    for days in periods:
        period_results[str(days)] = backtest_one_period(
            rows,
            days
        )

    # --------------------------------------------------------
    # 綜合判定
    #
    # 不讓單一 10D 結果直接控制目前策略。
    #
    # 完整週期中：
    #   buy_now
    #   wait_pullback
    #   observe
    #
    # 取多數方向。
    # --------------------------------------------------------

    complete_results = [
        result
        for result in period_results.values()
        if result.get("status") == "complete"
    ]

    buy_now_count = sum(
        result.get("preferred") == "buy_now"
        for result in complete_results
    )

    wait_count = sum(
        result.get("preferred") == "wait_pullback"
        for result in complete_results
    )

    observe_count = sum(
        result.get("preferred") == "observe"
        for result in complete_results
    )

    if not complete_results:
        overall_preferred = "observe"

    elif buy_now_count > wait_count and buy_now_count > observe_count:
        overall_preferred = "buy_now"

    elif wait_count > buy_now_count and wait_count > observe_count:
        overall_preferred = "wait_pullback"

    else:
        overall_preferred = "observe"

    complete_periods = [
        int(
            key
        )
        for key, value in period_results.items()
        if value.get("status") == "complete"
    ]

    insufficient_periods = [
        int(
            key
        )
        for key, value in period_results.items()
        if value.get("status") == "insufficient"
    ]

    return {
        "status": (
            "complete"
            if complete_results
            else "insufficient"
        ),

        "periods": period_results,

        "overall_preferred": overall_preferred,

        "decision_basis": {
            "buy_now_periods": buy_now_count,
            "wait_pullback_periods": wait_count,
            "observe_periods": observe_count,
            "complete_periods": complete_periods,
            "insufficient_periods": insufficient_periods,
        },

        "configuration": {
            "holding_periods": periods,
            "minimum_history": (
                ENTRY_TIMING_CONFIG[
                    "minimum_history"
                ]
            ),
            "pullback_wait_days": (
                ENTRY_TIMING_CONFIG[
                    "pullback_wait_days"
                ]
            ),
            "pullback_lower_pct": (
                ENTRY_TIMING_CONFIG[
                    "pullback_lower_pct"
                ]
            ),
            "pullback_upper_pct": (
                ENTRY_TIMING_CONFIG[
                    "pullback_upper_pct"
                ]
            ),
            "minimum_signals": (
                ENTRY_TIMING_CONFIG[
                    "minimum_signals"
                ]
            ),
            "win_rate_edge": (
                ENTRY_TIMING_CONFIG[
                    "win_rate_edge"
                ]
            ),
        },
    }


# ============================================================
# Entry Timing：目前價格
# ============================================================

def build_current_entry_timing(
    metrics: Dict[str, Any],
    backtest: Dict[str, Any],
) -> Dict[str, Any]:

    price = safe_float(metrics.get("price"))
    ma20 = safe_float(metrics.get("ma20"))

    preferred = backtest.get(
        "overall_preferred",
        "observe"
    )

    if (
        price is None
        or ma20 is None
        or ma20 <= 0
    ):
        return {
            "status": "observe",
            "preferred": preferred,
            "message": "目前價格資料不足",
            "pullback_zone": None,
        }

    lower = (
        ma20
        * ENTRY_TIMING_CONFIG[
            "pullback_lower_pct"
        ]
    )

    upper = (
        ma20
        * ENTRY_TIMING_CONFIG[
            "pullback_upper_pct"
        ]
    )

    if price > upper:
        price_position = "above_zone"

    elif price >= lower:
        price_position = "inside_zone"

    else:
        price_position = "below_zone"

    if preferred == "buy_now":

        status = "buy_now"
        message = (
            "多週期歷史回測顯示立即進場較有利"
        )

    elif preferred == "wait_pullback":

        if price > upper:

            status = "wait_pullback"

            message = (
                "目前價格高於合理回測區，"
                "等待回測較有利"
            )

        else:

            status = "buy_now"

            message = (
                "歷史回測偏向等待回測，"
                "但目前價格已進入合理區間"
            )

    else:

        status = "observe"

        message = (
            "多週期回測未形成明確進場優勢"
        )

    return {
        "status": status,
        "preferred": preferred,
        "price_position": price_position,

        "current_price": round(price, 2),
        "ma20": round(ma20, 2),

        "pullback_zone": {
            "lower": round(lower, 2),
            "upper": round(upper, 2),
        },

        "message": message,
    }


# ============================================================
# Entry Timing 完整結果
# ============================================================

def build_entry_timing(
    rows: List[Dict[str, Any]],
    metrics: Dict[str, Any],
    qualified: bool,
) -> Dict[str, Any]:

    if not qualified:

        return {
            "schema": ENTRY_TIMING_SCHEMA,

            "status": "not_qualified",

            "current": {
                "status": "observe",
                "preferred": "observe",
                "message": "目前未達強勢股條件",
            },

            "backtest": None,
        }

    backtest = backtest_entry_timing(rows)

    current = build_current_entry_timing(
        metrics,
        backtest
    )

    return {
        "schema": ENTRY_TIMING_SCHEMA,
        "status": current.get(
            "status",
            "observe"
        ),
        "current": current,
        "backtest": backtest,
    }


# ============================================================
# Analyze One Stock
# ============================================================

def analyze_stock(
    stock: Dict[str, Any],
    rows: List[Dict[str, Any]],
    chip: Dict[str, Any],
) -> Dict[str, Any]:

    symbol = stock["symbol"]
    name = stock.get("name", "")
    market = stock.get("market", "")

    minimum_history = SHORT_TERM_CONFIG[
        "minimum_history"
    ]

    chip_analysis = calculate_chip_analysis(chip)

    if len(rows) < 2:

        return {
            "symbol": symbol,
            "name": name,
            "market": market,

            "status": "insufficient",
            "history_count": len(rows),
            "latest_date": None,

            "metrics": {},

            "short_term": {
                "qualified": False,
                "score": 0,
                "core_score": 0,
                "core_total": 5,
                "core_pass_ratio": 0,

                "conditions": {
                    "macd_golden_cross": False,
                    "kd_golden_cross": False,
                    "rsi_above_50": False,
                    "volume_ratio_gt_1_5": False,
                    "price_above_ma20_and_ma20_up": False,
                },

                "technical_strength": "資料不足",
                "operation": "資料不足",
                "direction": "資料不足",

                "chip": chip_analysis,

                "chip_is_core_condition": False,
                "ten_day_chip_used": True,
            },

            "dca": {},

            "chip": chip_analysis,

            "entry_timing": {
                "schema": ENTRY_TIMING_SCHEMA,
                "status": "insufficient",

                "current": {
                    "status": "observe",
                    "preferred": "observe",
                    "message": "價格歷史不足",
                },

                "backtest": None,
            },
        }

    metrics = calculate_price_metrics(rows)

    closes = [
        row["close"]
        for row in rows
        if row.get("close") is not None
    ]

    macd = calculate_macd(closes)

    rsi = calculate_rsi(
        closes,
        SHORT_TERM_CONFIG["rsi_period"]
    )

    kd = calculate_kd(
        rows,
        SHORT_TERM_CONFIG["kd_period"]
    )

    short_term = evaluate_short_term(
        metrics,
        macd,
        kd,
        rsi,
        chip
    )

    short_term["history_requirement"] = {
        "minimum": minimum_history,
        "actual": len(rows),
        "passed": len(rows) >= minimum_history,
    }

    if len(rows) < minimum_history:

        short_term["qualified"] = False
        short_term["qualification_blocked"] = True

        short_term["qualification_block_reason"] = (
            f"歷史資料不足 {minimum_history} 個交易日"
        )

    else:

        short_term["qualification_blocked"] = False
        short_term["qualification_block_reason"] = None

    dca = evaluate_dca(
        metrics,
        chip
    )

    qualified = bool(
        short_term.get("qualified", False)
    )

    entry_timing = build_entry_timing(
        rows,
        metrics,
        qualified
    )

    return {
        "symbol": symbol,
        "name": name,
        "market": market,

        "status": (
            "complete"
            if len(rows) >= minimum_history
            else "partial"
        ),

        "latest_date": rows[-1]["date"],
        "history_count": len(rows),

        "metrics": metrics,
        "short_term": short_term,
        "dca": dca,
        "chip": chip_analysis,
        "entry_timing": entry_timing,
    }


# ============================================================
# Run Analysis
# ============================================================

def run_analysis() -> Dict[str, Any]:

    section(
        f"台股 AI 選股分析 {VERSION}"
    )

    universe = load_universe()
    manifest = load_price_manifest()

    chip_data = load_json(CHIP_FILE)

    if not isinstance(chip_data, dict):
        raise RuntimeError(
            "chip.json 格式錯誤"
        )

    chip_stocks = chip_data.get(
        "stocks",
        {}
    )

    if not isinstance(chip_stocks, dict):
        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    log(
        f"Chip 股票：{len(chip_stocks)}"
    )

    price_index, price_info = load_price_index(
        manifest
    )

    universe_symbols = set(universe.keys())
    price_symbols = set(price_index.keys())

    matched_symbols = (
        universe_symbols
        & price_symbols
    )

    missing_symbols = (
        universe_symbols
        - price_symbols
    )

    log("")
    log("價格資料驗證")
    log(f"Universe：{len(universe_symbols)}")
    log(f"價格索引：{len(price_symbols)}")
    log(f"成功對接：{len(matched_symbols)}")
    log(f"價格缺失：{len(missing_symbols)}")

    if not matched_symbols:
        raise RuntimeError(
            "Universe 與 prices 沒有任何股票成功對接"
        )

    results: Dict[str, Any] = {}

    short_candidates = []
    dca_buy = []
    dca_observe = []
    dca_pause = []

    entry_buy_now = []
    entry_wait_pullback = []
    entry_observe = []

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

    entry_timing_distribution = {
        "buy_now": 0,
        "wait_pullback": 0,
        "observe": 0,
        "not_qualified": 0,
        "insufficient": 0,
    }

    total = len(universe)

    for index, (symbol, stock) in enumerate(
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

        if not isinstance(chip, dict):
            chip = {}

        record = analyze_stock(
            stock,
            price_rows,
            chip
        )

        results[symbol] = record

        status = record.get("status")

        if status == "complete":
            complete += 1
        elif status == "partial":
            partial += 1
        else:
            insufficient += 1

        short_term = record.get(
            "short_term",
            {}
        )

        score = short_term.get("score")

        if (
            isinstance(score, int)
            and 0 <= score <= 5
        ):
            core_score_distribution[
                str(score)
            ] += 1

        chip_direction = (
            record.get("chip", {})
            .get("direction", "資料不足")
        )

        if chip_direction not in chip_direction_distribution:
            chip_direction = "資料不足"

        chip_direction_distribution[
            chip_direction
        ] += 1

        if short_term.get(
            "qualified",
            False
        ):
            short_candidates.append(symbol)

        dca_action = (
            record.get("dca", {})
            .get("action")
        )

        if dca_action in {
            "積極買進",
            "正常買進",
            "極端負乖離",
        }:
            dca_buy.append(symbol)

        elif dca_action in {
            "觀察",
            "高檔觀察",
        }:
            dca_observe.append(symbol)

        elif dca_action == "暫停加碼":
            dca_pause.append(symbol)

        entry_status = (
            record.get(
                "entry_timing",
                {}
            ).get(
                "status",
                "not_qualified"
            )
        )

        if entry_status not in entry_timing_distribution:
            entry_status = "observe"

        entry_timing_distribution[
            entry_status
        ] += 1

        if entry_status == "buy_now":
            entry_buy_now.append(symbol)

        elif entry_status == "wait_pullback":
            entry_wait_pullback.append(symbol)

        elif entry_status == "observe":
            entry_observe.append(symbol)

        if (
            index == 1
            or index % 100 == 0
            or index == total
        ):
            log(
                f"分析進度：{index}/{total}"
            )

    return {
        "results": results,

        "statistics": {
            "universe": total,

            "price_index": len(price_index),
            "price_loaded": price_loaded,
            "price_missing": price_missing,

            "universe_price_matched": len(
                matched_symbols
            ),

            "complete": complete,
            "partial": partial,
            "insufficient": insufficient,

            "short_term_candidates": len(
                short_candidates
            ),

            "dca_buy": len(dca_buy),
            "dca_observe": len(dca_observe),
            "dca_pause": len(dca_pause),

            "core_total": 5,

            "core_score_distribution":
                core_score_distribution,

            "chip_direction_distribution":
                chip_direction_distribution,

            "entry_timing_distribution":
                entry_timing_distribution,

            "entry_buy_now": len(entry_buy_now),
            "entry_wait_pullback": len(
                entry_wait_pullback
            ),
            "entry_observe": len(entry_observe),
        },

        "price_info": price_info,

        "missing_price_symbols":
            sorted(missing_symbols),

        "short_term_candidates":
            short_candidates,

        "dca_buy":
            dca_buy,

        "dca_observe":
            dca_observe,

        "dca_pause":
            dca_pause,

        "entry_buy_now":
            entry_buy_now,

        "entry_wait_pullback":
            entry_wait_pullback,

        "entry_observe":
            entry_observe,
    }


# ============================================================
# Save Analysis
# ============================================================

def save_analysis(
    analysis: Dict[str, Any]
) -> None:

    section("寫入 Data/analysis.json")

    output = {
        "schema_version": VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source": {
            "universe":
                "Data/universe.json",

            "prices":
                "Data/prices/",

            "price_manifest":
                "Data/prices/manifest.json",

            "chip":
                "Data/chip.json",
        },

        "analysis_rules": {

            "short_term": {
                "core_total": 5,

                "core_conditions": {
                    "1": "MACD 黃金交叉",
                    "2": "KD 黃金交叉",
                    "3": "RSI > 50",
                    "4": (
                        "今日成交量 ÷ "
                        "前5個交易日平均成交量 >= 1.5"
                    ),
                    "5": (
                        "股價 > MA20 "
                        "且 MA20 向上"
                    ),
                },

                "minimum_history":
                    SHORT_TERM_CONFIG[
                        "minimum_history"
                    ],
            },

            "chip": {
                "is_core_condition": False,

                "direction_basis": [
                    "1D",
                    "5D",
                    "10D",
                    "20D",
                ],

                "medium_direction_basis": [
                    "5D",
                    "10D",
                    "20D",
                ],

                "ten_day_used": True,
            },

            "dca": DCA_CONFIG,

            "entry_timing": {
                "schema": ENTRY_TIMING_SCHEMA,

                "description": (
                    "比較強勢股歷史訊號在 "
                    "10D / 30D / 60D / 90D "
                    "持有期間下，"
                    "立即進場與等待回測兩種方式"
                ),

                "holding_periods":
                    ENTRY_TIMING_CONFIG[
                        "holding_periods"
                    ],

                "minimum_history":
                    ENTRY_TIMING_CONFIG[
                        "minimum_history"
                    ],

                "pullback_wait_days":
                    ENTRY_TIMING_CONFIG[
                        "pullback_wait_days"
                    ],

                "pullback_lower_pct":
                    ENTRY_TIMING_CONFIG[
                        "pullback_lower_pct"
                    ],

                "pullback_upper_pct":
                    ENTRY_TIMING_CONFIG[
                        "pullback_upper_pct"
                    ],

                "win_rate_definition":
                    "持有期結束價格 > 進場價格",

                "minimum_signals":
                    ENTRY_TIMING_CONFIG[
                        "minimum_signals"
                    ],

                "win_rate_edge":
                    ENTRY_TIMING_CONFIG[
                        "win_rate_edge"
                    ],
            },
        },

        "data_pipeline": {
            "price_shards":
                analysis["price_info"],

            "price_index_count":
                analysis["statistics"]["price_index"],

            "universe_price_matched":
                analysis["statistics"][
                    "universe_price_matched"
                ],

            "price_missing":
                analysis["statistics"]["price_missing"],

            "missing_price_symbols":
                analysis["missing_price_symbols"],
        },

        "statistics":
            analysis["statistics"],

        "short_term_candidates":
            analysis["short_term_candidates"],

        "dca_buy":
            analysis["dca_buy"],

        "dca_observe":
            analysis["dca_observe"],

        "dca_pause":
            analysis["dca_pause"],

        "entry_buy_now":
            analysis["entry_buy_now"],

        "entry_wait_pullback":
            analysis["entry_wait_pullback"],

        "entry_observe":
            analysis["entry_observe"],

        "stocks":
            analysis["results"],
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

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(verify, dict):
        raise RuntimeError(
            "analysis.json 寫入後 root 格式錯誤"
        )

    stocks = verify.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "analysis.json stocks 格式錯誤"
        )

    if len(stocks) != len(analysis["results"]):
        raise RuntimeError(
            "analysis.json 股票數量驗證失敗"
        )

    # ========================================================
    # 五項核心規則驗證
    # ========================================================

    rules = verify[
        "analysis_rules"
    ][
        "short_term"
    ]

    if rules.get("core_total") != 5:
        raise RuntimeError(
            "core_total 必須為 5"
        )

    conditions = rules.get(
        "core_conditions"
    )

    if not isinstance(conditions, dict):
        raise RuntimeError(
            "core_conditions 格式錯誤"
        )

    if len(conditions) != 5:
        raise RuntimeError(
            "核心條件必須正好 5 項"
        )

    if conditions.get("5") != (
        "股價 > MA20 且 MA20 向上"
    ):
        raise RuntimeError(
            "第五項核心條件錯誤"
        )

    # ========================================================
    # Entry Timing Schema
    # ========================================================

    entry_rules = verify[
        "analysis_rules"
    ].get("entry_timing")

    if not isinstance(entry_rules, dict):
        raise RuntimeError(
            "Entry Timing 規則不存在"
        )

    if entry_rules.get(
        "schema"
    ) != ENTRY_TIMING_SCHEMA:
        raise RuntimeError(
            "Entry Timing schema 錯誤"
        )

    if entry_rules.get(
        "holding_periods"
    ) != ENTRY_TIMING_CONFIG[
        "holding_periods"
    ]:
        raise RuntimeError(
            "Entry Timing 持有週期設定錯誤"
        )

    # ========================================================
    # 每檔股票驗證
    # ========================================================

    allowed_status = {
        "buy_now",
        "wait_pullback",
        "observe",
        "not_qualified",
        "insufficient",
    }

    for symbol, stock in stocks.items():

        short_term = stock.get(
            "short_term",
            {}
        )

        if short_term.get("core_total") != 5:
            raise RuntimeError(
                f"{symbol}: core_total != 5"
            )

        stock_conditions = short_term.get(
            "conditions"
        )

        if not isinstance(
            stock_conditions,
            dict
        ):
            raise RuntimeError(
                f"{symbol}: conditions 格式錯誤"
            )

        if len(stock_conditions) != 5:
            raise RuntimeError(
                f"{symbol}: 核心條件不是 5 項"
            )

        if short_term.get(
            "chip_is_core_condition"
        ) is not False:
            raise RuntimeError(
                f"{symbol}: 籌碼錯誤成為核心條件"
            )

        chip = stock.get("chip", {})

        if chip.get(
            "ten_day_used"
        ) is not True:
            raise RuntimeError(
                f"{symbol}: 10D 未標記為使用"
            )

        entry_timing = stock.get(
            "entry_timing"
        )

        if not isinstance(
            entry_timing,
            dict
        ):
            raise RuntimeError(
                f"{symbol}: entry_timing 缺失"
            )

        if entry_timing.get(
            "schema"
        ) != ENTRY_TIMING_SCHEMA:
            raise RuntimeError(
                f"{symbol}: entry_timing schema 錯誤"
            )

        status = entry_timing.get("status")

        if status not in allowed_status:
            raise RuntimeError(
                f"{symbol}: "
                f"非法 entry_timing.status={status}"
            )

        qualified = bool(
            short_term.get(
                "qualified",
                False
            )
        )

        if not qualified:

            if status not in {
                "not_qualified",
                "insufficient",
            }:
                raise RuntimeError(
                    f"{symbol}: "
                    "未符合強勢股卻產生有效 Entry Timing"
                )

        if qualified:

            backtest = entry_timing.get(
                "backtest"
            )

            if not isinstance(
                backtest,
                dict
            ):
                raise RuntimeError(
                    f"{symbol}: "
                    "強勢股缺少 Entry Timing backtest"
                )

            periods = backtest.get(
                "periods"
            )

            if not isinstance(
                periods,
                dict
            ):
                raise RuntimeError(
                    f"{symbol}: "
                    "Entry Timing periods 格式錯誤"
                )

            for days in ENTRY_TIMING_CONFIG[
                "holding_periods"
            ]:

                key = str(days)

                if key not in periods:
                    raise RuntimeError(
                        f"{symbol}: "
                        f"缺少 {days}D 回測"
                    )

                period = periods[key]

                if not isinstance(period, dict):
                    raise RuntimeError(
                        f"{symbol}: "
                        f"{days}D 回測格式錯誤"
                    )

                if period.get(
                    "holding_days"
                ) != days:
                    raise RuntimeError(
                        f"{symbol}: "
                        f"{days}D holding_days 錯誤"
                    )

                if not isinstance(
                    period.get("immediate"),
                    dict
                ):
                    raise RuntimeError(
                        f"{symbol}: "
                        f"{days}D immediate stats 錯誤"
                    )

                if not isinstance(
                    period.get("pullback"),
                    dict
                ):
                    raise RuntimeError(
                        f"{symbol}: "
                        f"{days}D pullback stats 錯誤"
                    )

    stats = verify["statistics"]

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
            "Universe 有資料，但價格完全沒有對接"
        )

    temp_file.replace(OUTPUT_FILE)

    log(
        f"✓ 已寫入：{OUTPUT_FILE}"
    )


# ============================================================
# Summary
# ============================================================

def print_summary(
    analysis: Dict[str, Any],
    elapsed: float,
) -> None:

    stats = analysis["statistics"]

    section("分析完成")

    log(
        f"Universe：{stats['universe']}"
    )

    log(
        f"價格索引：{stats['price_index']}"
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

    # --------------------------------------------------------
    # 注意：固定五項，不再使用舊六項名稱
    # --------------------------------------------------------

    log("短期技術條件：5 項")
    log("1. MACD 黃金交叉")
    log("2. KD 黃金交叉")
    log("3. RSI > 50")
    log("4. 成交量比 >= 1.5")
    log("5. 股價 > MA20 且 MA20 向上")

    log("")

    log(
        f"強勢股："
        f"{stats['short_term_candidates']}"
    )

    log("")

    log("Entry Timing 2.0")

    log(
        f"可直接進場："
        f"{stats['entry_buy_now']}"
    )

    log(
        f"等待回測："
        f"{stats['entry_wait_pullback']}"
    )

    log(
        f"觀察："
        f"{stats['entry_observe']}"
    )

    log("")

    log("Entry Timing 回測週期：10D / 30D / 60D / 90D")

    log(
        "最低歷史資料："
        f"{ENTRY_TIMING_CONFIG['minimum_history']} "
        "交易日"
    )

    log(
        "等待回測："
        f"{ENTRY_TIMING_CONFIG['pullback_wait_days']} "
        "交易日"
    )

    log(
        "合理回測區："
        "MA20 × 0.98 ~ MA20 × 1.02"
    )

    log(
        "勝率定義："
        "持有期結束價格 > 進場價格"
    )

    log("")

    log("籌碼：1D / 5D / 10D / 20D")
    log("10D：已實際參與籌碼方向分析")
    log("籌碼不是技術核心條件")

    log("")

    # --------------------------------------------------------
    # 各週期回測摘要
    # --------------------------------------------------------

    qualified_count = stats[
        "short_term_candidates"
    ]

    log(
        f"強勢股 Entry Timing："
        f"{qualified_count} 檔"
    )

    log("")

    log(
        f"耗時：{elapsed:.1f} 秒"
    )

    log(
        f"輸出：{OUTPUT_FILE}"
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
    log(
        "Entry Timing："
        f"{ENTRY_TIMING_SCHEMA}"
    )
    log("=" * 72)

    try:

        analysis = run_analysis()

        save_analysis(analysis)

        elapsed = time.time() - start

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
        log(f"原因：{exc}")

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(main())