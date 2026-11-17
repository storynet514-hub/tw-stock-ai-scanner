#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 全市場多空型態掃描器
Scripts/scan_trade_setups.py

版本：TRADE-SCAN-1.0

資料來源：
    Data/universe.json
    Data/prices/manifest.json
    Data/prices/prices_*.json
    Data/chip.json

掃描範圍：
    TWSE + TPEX
    active STOCK only
    ETF excluded

做多：
    1. W底
    2. 右底高於左底
    3. 突破頸線
    4. 收盤站上週20MA
    5. 成交量 >= 10,000張
    6. 突破下降趨勢壓力

做空：
    1. M頭
    2. 右頭低於左頭
    3. 跌破頸線
    4. 收盤跌破週20MA
    5. 成交量 >= 10,000張
    6. 跌破上升趨勢支撐

注意：
    本程式只負責型態及技術條件掃描。
    不會把法人買賣超缺值當成零。
    不會將觀察名單冒充成已確認交易訊號。
"""

from __future__ import annotations

import json
import math
import os
import tempfile

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional


# ============================================================
# PATH
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICE_DIR = DATA_DIR / "prices"

MANIFEST_FILE = PRICE_DIR / "manifest.json"

CHIP_FILE = DATA_DIR / "chip.json"

OUTPUT_FILE = DATA_DIR / "trade_scan.json"


# ============================================================
# CONFIG
# ============================================================

VERSION = "TRADE-SCAN-1.0"

PRICE_SCHEMA = "prices-v14.0"

MARKETS = {"TWSE", "TPEX"}

MIN_HISTORY = 120

PATTERN_LOOKBACK = 60

PIVOT_RADIUS = 3

MIN_VOLUME_LOTS = 10000

SHARES_PER_LOT = 1000

MIN_PATTERN_HEIGHT_PCT = 2.0

MAX_PATTERN_HEIGHT_PCT = 30.0

MIN_RIGHT_LOW_HIGHER_PCT = 0.3

MIN_RIGHT_HIGH_LOWER_PCT = 0.3

MIN_RISK_REWARD = 2.0

WEEKLY_MA_PERIOD = 20

TRENDLINE_LOOKBACK = 60

OUTPUT_SCHEMA = "trade-scan-v1.0"


# ============================================================
# JSON
# ============================================================

def load_json(path: Path) -> Any:

    if not path.is_file():
        raise FileNotFoundError(
            f"必要檔案不存在：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8-sig"
    ) as file:

        return json.load(file)


def atomic_write_json(
    path: Path,
    payload: dict[str, Any]
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent)
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2
            )

            file.write("\n")

            file.flush()

            os.fsync(file.fileno())

        os.replace(
            temp_path,
            path
        )

    finally:

        if os.path.exists(temp_path):
            os.unlink(temp_path)


# ============================================================
# NUMBER
# ============================================================

def number(value: Any) -> Optional[float]:

    if value is None:
        return None

    try:

        result = float(
            str(value).replace(",", "")
        )

        if not math.isfinite(result):
            return None

        return result

    except (TypeError, ValueError):
        return None


def valid_positive(value: Any) -> Optional[float]:

    result = number(value)

    if result is None or result <= 0:
        return None

    return result


def parse_date(value: Any) -> Optional[date]:

    if not value:
        return None

    try:

        return date.fromisoformat(
            str(value)[:10]
        )

    except ValueError:
        return None


# ============================================================
# UNIVERSE
# ============================================================

def load_universe() -> dict[str, dict]:

    payload = load_json(
        UNIVERSE_FILE
    )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "universe.json 缺少 stocks"
        )

    result = {}

    for symbol, item in stocks.items():

        if not isinstance(item, dict):
            continue

        if item.get("status") != "active":
            continue

        if item.get("type") != "STOCK":
            continue

        if item.get("market") not in MARKETS:
            continue

        result[str(symbol)] = item

    if not result:
        raise RuntimeError(
            "沒有有效上市櫃普通股"
        )

    return result


# ============================================================
# PRICE SHARDS
# ============================================================

def load_prices() -> dict[str, list]:

    manifest = load_json(
        MANIFEST_FILE
    )

    if manifest.get("schema_version") != PRICE_SCHEMA:
        raise RuntimeError(
            "行情 schema 不符"
        )

    files = manifest.get("files")

    if not isinstance(files, list) or not files:
        raise RuntimeError(
            "manifest.files 無效"
        )

    result = {}

    for item in files:

        if isinstance(item, str):
            filename = item

        elif isinstance(item, dict):
            filename = (
                item.get("file")
                or item.get("path")
                or item.get("filename")
            )

        else:
            raise RuntimeError(
                f"無效 shard：{item}"
            )

        if Path(filename).name != filename:
            raise RuntimeError(
                f"非法 shard 路徑：{filename}"
            )

        path = PRICE_DIR / filename

        shard = load_json(path)

        if shard.get("schema_version") != PRICE_SCHEMA:
            raise RuntimeError(
                f"{filename} schema 不符"
            )

        stocks = shard.get("stocks")

        if not isinstance(stocks, dict):
            raise RuntimeError(
                f"{filename} 缺少 stocks"
            )

        for symbol, rows in stocks.items():

            if symbol in result:
                raise RuntimeError(
                    f"重複行情代號：{symbol}"
                )

            if not isinstance(rows, list):
                raise RuntimeError(
                    f"{symbol} 行情格式錯誤"
                )

            result[symbol] = rows

    return result


# ============================================================
# CHIP
# ============================================================

def load_chip() -> dict:

    payload = load_json(
        CHIP_FILE
    )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "chip.json 缺少 stocks"
        )

    return stocks


def chip_summary(
    chip: dict,
    symbol: str
) -> dict:

    item = chip.get(symbol)

    if not isinstance(item, dict):
        return {
            "available": False,
            "foreign_5d": None,
            "trust_5d": None,
            "dealer_5d": None,
            "total_5d": None,
            "foreign_20d": None,
            "trust_20d": None,
            "dealer_20d": None,
            "total_20d": None,
        }

    inst = item.get("institutional")

    if not isinstance(inst, dict):
        inst = {}

    d5 = inst.get("5d")
    d20 = inst.get("20d")

    if not isinstance(d5, dict):
        d5 = {}

    if not isinstance(d20, dict):
        d20 = {}

    return {
        "available": True,

        "foreign_5d": number(
            d5.get("foreign_net")
        ),

        "trust_5d": number(
            d5.get("trust_net")
        ),

        "dealer_5d": number(
            d5.get("dealer_net")
        ),

        "total_5d": number(
            d5.get("total_net")
        ),

        "foreign_20d": number(
            d20.get("foreign_net")
        ),

        "trust_20d": number(
            d20.get("trust_net")
        ),

        "dealer_20d": number(
            d20.get("dealer_net")
        ),

        "total_20d": number(
            d20.get("total_net")
        ),
    }


# ============================================================
# DAILY OHLCV
# ============================================================

def normalize_rows(rows: list) -> list[dict]:

    result = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        trading_date = parse_date(
            row.get("date")
        )

        open_price = valid_positive(
            row.get("open")
        )

        high = valid_positive(
            row.get("high")
        )

        low = valid_positive(
            row.get("low")
        )

        close = valid_positive(
            row.get("close")
        )

        volume = number(
            row.get("volume")
        )

        if (
            trading_date is None
            or open_price is None
            or high is None
            or low is None
            or close is None
            or volume is None
            or volume < 0
        ):
            continue

        if high < low:
            continue

        if high < max(open_price, close):
            continue

        if low > min(open_price, close):
            continue

        result.append({
            "date": trading_date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    result.sort(
        key=lambda x: x["date"]
    )

    unique = {}

    for row in result:
        unique[row["date"]] = row

    return list(
        unique.values()
    )


# ============================================================
# WEEKLY MA20
# ============================================================

def weekly_ma20(
    rows: list[dict],
    scan_date: date
) -> Optional[float]:

    current_monday = (
        scan_date
        - timedelta(
            days=scan_date.weekday()
        )
    )

    include_current_week = (
        scan_date.weekday() == 4
    )

    weekly = {}

    for row in rows:

        d = row["date"]

        monday = (
            d
            - timedelta(
                days=d.weekday()
            )
        )

        if (
            monday == current_monday
            and not include_current_week
        ):
            continue

        key = (
            d.isocalendar().year,
            d.isocalendar().week
        )

        previous = weekly.get(key)

        if previous is None or d > previous["date"]:
            weekly[key] = row

    values = sorted(
        weekly.values(),
        key=lambda x: x["date"]
    )

    if len(values) < WEEKLY_MA_PERIOD:
        return None

    closes = [
        x["close"]
        for x in values[-WEEKLY_MA_PERIOD:]
    ]

    return sum(closes) / WEEKLY_MA_PERIOD


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    values: list[float],
    radius: int = PIVOT_RADIUS
) -> tuple[list[int], list[int]]:

    lows = []
    highs = []

    for i in range(
        radius,
        len(values) - radius
    ):

        window = values[
            i-radius:i+radius+1
        ]

        value = values[i]

        if value == min(window):
            lows.append(i)

        if value == max(window):
            highs.append(i)

    return lows, highs


# ============================================================
# TRENDLINE
# ============================================================

def projected_line(
    points: list[tuple[int, float]],
    target_index: int
) -> Optional[float]:

    if len(points) < 2:
        return None

    x1, y1 = points[-2]
    x2, y2 = points[-1]

    if x2 == x1:
        return None

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return y2 + slope * (
        target_index - x2
    )


def trendline_signals(
    rows: list[dict],
    lows: list[int],
    highs: list[int]
) -> dict:

    n = len(rows)

    start = max(
        0,
        n - TRENDLINE_LOOKBACK
    )

    recent_lows = [
        i for i in lows
        if i >= start
    ]

    recent_highs = [
        i for i in highs
        if i >= start
    ]

    close = rows[-1]["close"]

    descending_resistance = None
    ascending_support = None

    if len(recent_highs) >= 2:

        points = [
            (
                i,
                rows[i]["high"]
            )
            for i in recent_highs
        ]

        if points[-1][1] < points[-2][1]:

            descending_resistance = projected_line(
                points,
                n - 1
            )

    if len(recent_lows) >= 2:

        points = [
            (
                i,
                rows[i]["low"]
            )
            for i in recent_lows
        ]

        if points[-1][1] > points[-2][1]:

            ascending_support = projected_line(
                points,
                n - 1
            )

    return {
        "descending_resistance": descending_resistance,
        "ascending_support": ascending_support,

        "bullish_trendline_break": (
            descending_resistance is not None
            and close > descending_resistance
        ),

        "bearish_trendline_break": (
            ascending_support is not None
            and close < ascending_support
        ),
    }


# ============================================================
# W BOTTOM
# ============================================================

def detect_w_bottom(
    rows: list[dict]
) -> Optional[dict]:

    start = max(
        0,
        len(rows) - PATTERN_LOOKBACK
    )

    segment = rows[start:]

    if len(segment) < 30:
        return None

    lows = [
        row["low"]
        for row in segment
    ]

    pivot_lows, _ = find_pivots(
        lows
    )

    if len(pivot_lows) < 2:
        return None

    candidates = []

    for a in range(
        len(pivot_lows) - 1
    ):

        for b in range(
            a + 1,
            len(pivot_lows)
        ):

            left = pivot_lows[a]
            right = pivot_lows[b]

            distance = right - left

            if distance < 8 or distance > 45:
                continue

            left_low = lows[left]
            right_low = lows[right]

            higher_pct = (
                right_low / left_low - 1
            ) * 100

            if higher_pct < MIN_RIGHT_LOW_HIGHER_PCT:
                continue

            if higher_pct > 15:
                continue

            between = segment[
                left:right+1
            ]

            neckline = max(
                row["high"]
                for row in between
            )

            height_pct = (
                neckline / right_low - 1
            ) * 100

            if not (
                MIN_PATTERN_HEIGHT_PCT
                <= height_pct
                <= MAX_PATTERN_HEIGHT_PCT
            ):
                continue

            candidates.append({
                "left_index": start + left,
                "right_index": start + right,
                "left_low": left_low,
                "right_low": right_low,
                "neckline": neckline,
                "height_pct": height_pct,
                "right_low_higher_pct": higher_pct,
            })

    if not candidates:
        return None

    pattern = candidates[-1]

    previous_close = rows[-2]["close"]
    current_close = rows[-1]["close"]

    pattern["breakout"] = (
        previous_close <= pattern["neckline"]
        and current_close > pattern["neckline"]
    )

    pattern["close_above_neckline"] = (
        current_close > pattern["neckline"]
    )

    pattern["pattern_target"] = (
        pattern["neckline"]
        + (
            pattern["neckline"]
            - min(
                pattern["left_low"],
                pattern["right_low"]
            )
        )
    )

    return pattern


# ============================================================
# M TOP
# ============================================================

def detect_m_top(
    rows: list[dict]
) -> Optional[dict]:

    start = max(
        0,
        len(rows) - PATTERN_LOOKBACK
    )

    segment = rows[start:]

    if len(segment) < 30:
        return None

    highs = [
        row["high"]
        for row in segment
    ]

    _, pivot_highs = find_pivots(
        highs
    )

    if len(pivot_highs) < 2:
        return None

    candidates = []

    for a in range(
        len(pivot_highs) - 1
    ):

        for b in range(
            a + 1,
            len(pivot_highs)
        ):

            left = pivot_highs[a]
            right = pivot_highs[b]

            distance = right - left

            if distance < 8 or distance > 45:
                continue

            left_high = highs[left]
            right_high = highs[right]

            lower_pct = (
                1 - right_high / left_high
            ) * 100

            if lower_pct < MIN_RIGHT_HIGH_LOWER_PCT:
                continue

            if lower_pct > 15:
                continue

            between = segment[
                left:right+1
            ]

            neckline = min(
                row["low"]
                for row in between
            )

            height_pct = (
                left_high / neckline - 1
            ) * 100

            if not (
                MIN_PATTERN_HEIGHT_PCT
                <= height_pct
                <= MAX_PATTERN_HEIGHT_PCT
            ):
                continue

            candidates.append({
                "left_index": start + left,
                "right_index": start + right,
                "left_high": left_high,
                "right_high": right_high,
                "neckline": neckline,
                "height_pct": height_pct,
                "right_high_lower_pct": lower_pct,
            })

    if not candidates:
        return None

    pattern = candidates[-1]

    previous_close = rows[-2]["close"]
    current_close = rows[-1]["close"]

    pattern["breakdown"] = (
        previous_close >= pattern["neckline"]
        and current_close < pattern["neckline"]
    )

    pattern["close_below_neckline"] = (
        current_close < pattern["neckline"]
    )

    pattern["pattern_target"] = (
        pattern["neckline"]
        - (
            max(
                pattern["left_high"],
                pattern["right_high"]
            )
            - pattern["neckline"]
        )
    )

    return pattern


# ============================================================
# VOLUME
# ============================================================

def volume_analysis(
    rows: list[dict]
) -> dict:

    current = rows[-1]["volume"]

    average_20 = sum(
        row["volume"]
        for row in rows[-20:]
    ) / min(
        20,
        len(rows)
    )

    lots = current / SHARES_PER_LOT

    return {
        "volume_shares": current,
        "volume_lots": lots,
        "average_20d_shares": average_20,
        "volume_ratio_20d": (
            current / average_20
            if average_20 > 0
            else None
        ),
        "volume_pass": (
            lots >= MIN_VOLUME_LOTS
        ),
    }


# ============================================================
# TRADE PLAN
# ============================================================

def build_trade_plan(
    direction: str,
    close: float,
    pattern: dict
) -> Optional[dict]:

    if direction == "LONG":

        stop = min(
            pattern["left_low"],
            pattern["right_low"]
        ) * 0.995

        risk = close - stop

        if risk <= 0:
            return None

        target_2r = close + risk * MIN_RISK_REWARD

        target_pattern = pattern["pattern_target"]

        target = max(
            target_2r,
            target_pattern
        )

        reward = target - close

    else:

        stop = max(
            pattern["left_high"],
            pattern["right_high"]
        ) * 1.005

        risk = stop - close

        if risk <= 0:
            return None

        target_2r = close - risk * MIN_RISK_REWARD

        target_pattern = pattern["pattern_target"]

        target = min(
            target_2r,
            target_pattern
        )

        reward = close - target

    if reward <= 0:
        return None

    rr = reward / risk

    if rr < MIN_RISK_REWARD:
        return None

    return {
        "entry_reference": round(close, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "risk_per_share": round(risk, 2),
        "reward_per_share": round(reward, 2),
        "risk_reward": round(rr, 2),
        "minimum_rr": MIN_RISK_REWARD,
        "entry_policy": (
            "以訊號日收盤價作為回測參考；"
            "實際交易須以下一交易日成交價重新計算"
        ),
    }


# ============================================================
# SCAN ONE STOCK
# ============================================================

def scan_one(
    symbol: str,
    identity: dict,
    raw_rows: list,
    chip: dict,
    scan_date: date
) -> tuple[list, str]:

    rows = normalize_rows(
        raw_rows
    )

    if len(rows) < MIN_HISTORY:
        return [], "insufficient_history"

    if rows[-1]["date"] != scan_date:
        return [], "stale_price"

    close = rows[-1]["close"]

    ma20 = weekly_ma20(
        rows,
        scan_date
    )

    if ma20 is None:
        return [], "insufficient_weekly_history"

    lows, highs = find_pivots(
        [
            row["close"]
            for row in rows
        ]
    )

    trend = trendline_signals(
        rows,
        lows,
        highs
    )

    volume = volume_analysis(
        rows
    )

    chip_data = chip_summary(
        chip,
        symbol
    )

    w_bottom = detect_w_bottom(
        rows
    )

    m_top = detect_m_top(
        rows
    )

    results = []

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if (
        w_bottom is not None
        and w_bottom["breakout"]
        and close > ma20
        and volume["volume_pass"]
        and trend["bullish_trendline_break"]
    ):

        plan = build_trade_plan(
            "LONG",
            close,
            w_bottom
        )

        if plan:

            results.append({
                "symbol": symbol,
                "name": identity.get("name"),
                "market": identity.get("market"),
                "direction": "LONG",
                "signal_date": scan_date.isoformat(),
                "close": round(close, 2),
                "weekly_ma20": round(ma20, 4),
                "weekly_ma20_pass": True,
                "pattern": "W_BOTTOM",
                "pattern_data": w_bottom,
                "trendline": trend,
                "volume": volume,
                "chip": chip_data,
                "trade_plan": plan,
            })

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if (
        m_top is not None
        and m_top["breakdown"]
        and close < ma20
        and volume["volume_pass"]
        and trend["bearish_trendline_break"]
    ):

        plan = build_trade_plan(
            "SHORT",
            close,
            m_top
        )

        if plan:

            results.append({
                "symbol": symbol,
                "name": identity.get("name"),
                "market": identity.get("market"),
                "direction": "SHORT",
                "signal_date": scan_date.isoformat(),
                "close": round(close, 2),
                "weekly_ma20": round(ma20, 4),
                "weekly_ma20_pass": True,
                "pattern": "M_TOP",
                "pattern_data": m_top,
                "trendline": trend,
                "volume": volume,
                "chip": chip_data,
                "trade_plan": plan,
            })

    return results, "scanned"


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    print("=" * 72)
    print("TAIWAN STOCK MARKET LONG / SHORT SCANNER")
    print("=" * 72)

    universe = load_universe()

    prices = load_prices()

    chip = load_chip()

    missing = (
        set(universe)
        - set(prices)
    )

    if missing:
        raise RuntimeError(
            "Universe 缺少行情："
            f"{sorted(missing)[:30]}"
        )

    all_dates = []

    for symbol in universe:

        for row in prices.get(symbol, []):

            if not isinstance(row, dict):
                continue

            d = parse_date(
                row.get("date")
            )

            if d:
                all_dates.append(d)

    if not all_dates:
        raise RuntimeError(
            "沒有可用行情日期"
        )

    scan_date = max(all_dates)

    long_candidates = []
    short_candidates = []

    excluded = Counter()

    scanned = 0

    for symbol, identity in universe.items():

        candidates, status = scan_one(
            symbol,
            identity,
            prices.get(symbol, []),
            chip,
            scan_date
        )

        excluded[status] += 1

        if status == "scanned":
            scanned += 1

        for item in candidates:

            if item["direction"] == "LONG":
                long_candidates.append(item)

            else:
                short_candidates.append(item)

    long_candidates.sort(
        key=lambda x: (
            -x["trade_plan"]["risk_reward"],
            -(
                x["chip"]["total_5d"]
                if x["chip"]["total_5d"] is not None
                else -1e30
            )
        )
    )

    short_candidates.sort(
        key=lambda x: (
            -x["trade_plan"]["risk_reward"],
            -(
                -x["chip"]["total_5d"]
                if x["chip"]["total_5d"] is not None
                else -1e30
            )
        )
    )

    payload = {
        "schema_version": OUTPUT_SCHEMA,
        "version": VERSION,
        "scan_date": scan_date.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(),
        "market_scope": [
            "TWSE",
            "TPEX"
        ],
        "instrument_scope": "active common stocks only",
        "universe_count": len(universe),
        "price_symbols": len(prices),
        "scanned_count": scanned,
        "excluded_counts": dict(excluded),
        "long_count": len(long_candidates),
        "short_count": len(short_candidates),
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "rules": {
            "weekly_ma": "20 completed weekly closes",
            "pattern_lookback": PATTERN_LOOKBACK,
            "volume_minimum_lots": MIN_VOLUME_LOTS,
            "long_pattern": "W bottom, higher right low, neckline breakout",
            "short_pattern": "M top, lower right high, neckline breakdown",
            "trendline": "descending resistance breakout / ascending support breakdown",
            "minimum_risk_reward": MIN_RISK_REWARD,
            "volume_unit": "shares converted to lots by dividing 1000",
        },
        "limitations": [
            "This stage scans technical patterns only.",
            "Fundamental validation is not yet included.",
            "Historical win rate is not yet calculated.",
            "Chip missing values remain null and are not treated as zero.",
            "Entry reference is signal-day close, not guaranteed execution price."
        ]
    }

    atomic_write_json(
        OUTPUT_FILE,
        payload
    )

    readback = load_json(
        OUTPUT_FILE
    )

    if readback != payload:
        raise RuntimeError(
            "trade_scan.json read-back validation failed"
        )

    if (
        len(readback["long_candidates"])
        != readback["long_count"]
    ):
        raise RuntimeError(
            "LONG count validation failed"
        )

    if (
        len(readback["short_candidates"])
        != readback["short_count"]
    ):
        raise RuntimeError(
            "SHORT count validation failed"
        )

    print()
    print("=" * 72)
    print("SCAN COMPLETE")
    print("=" * 72)

    print(
        f"掃描日期：{scan_date}"
    )

    print(
        f"上市櫃普通股：{len(universe)}"
    )

    print(
        f"實際完成掃描：{scanned}"
    )

    print(
        f"做多候選：{len(long_candidates)}"
    )

    print(
        f"做空候選：{len(short_candidates)}"
    )

    print(
        f"排除統計：{dict(excluded)}"
    )

    print(
        f"輸出：{OUTPUT_FILE}"
    )

    print("=" * 72)


if __name__ == "__main__":
    main()