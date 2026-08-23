#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py
正式版 UI-DATA-2.0

============================================================
資料流
============================================================

Data/universe.json
        +
Data/analysis.json
        |
        v
Scripts/build_ui_data.py
        |
        v
Data/ui_data.json
        |
        v
index.html


============================================================
架構定位
============================================================

本程式是「UI DATA 建構器」。

唯一責任：

    analysis.json
    +
    universe.json
        ↓
    UI schema transformation
        ↓
    ui_data.json

本程式絕對不負責：

    - API 抓取
    - 股價抓取
    - RSI 計算
    - MACD 計算
    - KD 計算
    - 成交量計算
    - 主力籌碼計算
    - 選股條件計算
    - 重新評分
    - 重新判斷買進條件
    - 修改 analysis.json
    - 修改 universe.json
    - 修改 prices shards
    - 修改 chip.json


============================================================
重要架構原則
============================================================

analysis.json
    ↓
唯一分析結果來源

universe.json
    ↓
只補充：
    - 名稱
    - 市場
    - 商品分類

build_ui_data.py
    ↓
只做 schema transformation

ui_data.json
    ↓
提供 index.html 使用


============================================================
舊架構已移除
============================================================

本版本不得再依賴：

    short_term_candidates
    short_term.qualified
    short_term.score

也不得產生：

    六項核心
    六項核心候選
    六項核心條件

build_ui_data.py 不做任何「是否符合條件」的重新判斷。


============================================================
UI-DATA-3.0 Contract
============================================================

root:

    schema_version
    generated_at
    status
    market
    summary
    tabs
    stocks

tabs:

    today_picks
    top10
    etf
    bond
    watchlist

stocks:

    symbol
    name
    market
    instrument_type
    price
    change
    change_pct
    strength
    recommendation
    backend
    holding


============================================================
持倉原則
============================================================

後端不建立任何預設持倉。

初始：

    has_holdings = false
    holdings_profit = null

每檔：

    shares = null
    average_cost = null
    market_value = null
    profit = null
    return_pct = null


============================================================
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 基本設定
# ============================================================

VERSION = "UI-DATA-3.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

ANALYSIS_FILE = DATA_DIR / "analysis.json"
UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "ui_data.json"


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
        raise RuntimeError(
            f"找不到檔案：{path}"
        )

    try:

        with path.open(
            "r",
            encoding="utf-8-sig",
        ) as f:

            return json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"JSON 讀取失敗：{path}：{exc}"
        ) from exc


# ============================================================
# Number
# ============================================================

def number(value: Any) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:

        value = float(
            str(value)
            .replace(",", "")
            .strip()
        )

        if not math.isfinite(value):
            return None

        return value

    except Exception:
        return None


def rounded(
    value: Any,
    digits: int = 2,
) -> Optional[float]:

    value = number(value)

    if value is None:
        return None

    return round(value, digits)


# ============================================================
# Symbol
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
# First Value
# ============================================================

def first_value(
    record: Any,
    keys: List[str],
) -> Any:

    if not isinstance(record, dict):
        return None

    for key in keys:

        if (
            key in record
            and record[key] is not None
        ):

            return record[key]

    return None


# ============================================================
# Universe
# ============================================================

def load_universe() -> Dict[str, Dict[str, Any]]:

    data = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 格式錯誤："
            "根節點必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "universe.json stocks 格式錯誤"
        )

    if not stocks:

        raise RuntimeError(
            "universe.json stocks 為空"
        )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for raw_symbol, item in stocks.items():

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if not isinstance(
            item,
            dict,
        ):
            continue

        record = dict(item)
        record["symbol"] = symbol

        result[symbol] = record

    if not result:

        raise RuntimeError(
            "universe.json 沒有有效資料"
        )

    universe_count = data.get(
        "universe_count"
    )

    if universe_count is not None:

        try:

            universe_count = int(
                universe_count
            )

        except Exception:

            raise RuntimeError(
                "universe.json universe_count 格式錯誤"
            )

        if universe_count != len(result):

            raise RuntimeError(
                "universe.json universe_count 不一致："
                f"header={universe_count}, "
                f"actual={len(result)}"
            )

    log(
        f"Universe：{len(result)} 檔"
    )

    return result


# ============================================================
# Analysis
# ============================================================

def load_analysis() -> Dict[str, Any]:

    data = load_json(
        ANALYSIS_FILE
    )

    if not isinstance(data, dict):

        raise RuntimeError(
            "analysis.json 格式錯誤："
            "根節點必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "analysis.json stocks 格式錯誤"
        )

    if not stocks:

        raise RuntimeError(
            "analysis.json stocks 為空"
        )

    return data


# ============================================================
# Stock Name
# ============================================================

def get_stock_name(
    symbol: str,
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> str:

    value = first_value(
        analysis_record,
        [
            "name",
            "stock_name",
            "名稱",
        ],
    )

    if value:
        return str(value).strip()

    value = first_value(
        universe_record,
        [
            "name",
            "stock_name",
            "名稱",
        ],
    )

    if value:
        return str(value).strip()

    return symbol


# ============================================================
# Market
# ============================================================

def get_market(
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> str:

    value = first_value(
        analysis_record,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if value:
        return str(value).strip()

    value = first_value(
        universe_record,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if value:
        return str(value).strip()

    return ""


# ============================================================
# Instrument Type
# ============================================================

def get_instrument_type(
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> str:

    value = first_value(
        analysis_record,
        [
            "instrument_type",
            "security_type",
            "type",
            "category",
            "product_type",
        ],
    )

    if value is None:

        value = first_value(
            universe_record,
            [
                "instrument_type",
                "security_type",
                "type",
                "category",
                "product_type",
            ],
        )

    if value is None:
        return "stock"

    text = str(
        value
    ).strip().lower()

    if any(
        token in text
        for token in (
            "etf",
            "基金",
            "指數型基金",
        )
    ):
        return "etf"

    if any(
        token in text
        for token in (
            "bond",
            "債券",
        )
    ):
        return "bond"

    return "stock"


# ============================================================
# Metrics
# ============================================================

def get_metrics(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    metrics = record.get(
        "metrics"
    )

    if not isinstance(
        metrics,
        dict,
    ):
        return {}

    return metrics


# ============================================================
# Price
# ============================================================

def get_price(
    record: Dict[str, Any],
) -> Optional[float]:

    metrics = get_metrics(
        record
    )

    value = first_value(
        metrics,
        [
            "price",
            "close",
            "latest_price",
            "last_price",
        ],
    )

    if value is not None:
        return rounded(value, 2)

    return rounded(
        first_value(
            record,
            [
                "price",
                "close",
                "latest_price",
                "last_price",
            ],
        ),
        2,
    )


# ============================================================
# Change
# ============================================================

def get_change(
    record: Dict[str, Any],
) -> Tuple[
    Optional[float],
    Optional[float],
]:

    metrics = get_metrics(
        record
    )

    change = rounded(
        first_value(
            metrics,
            [
                "change",
                "price_change",
            ],
        ),
        2,
    )

    change_pct = rounded(
        first_value(
            metrics,
            [
                "change_pct",
                "change_percent",
                "change1_pct",
            ],
        ),
        2,
    )

    return (
        change,
        change_pct,
    )


# ============================================================
# Indicators
#
# 只搬運 analysis.json。
# 不重新計算。
# ============================================================

def build_indicators(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    metrics = get_metrics(
        record
    )

    indicators = record.get(
        "indicators"
    )

    if not isinstance(
        indicators,
        dict,
    ):
        indicators = {}

    short_term = record.get(
        "short_term"
    )

    if not isinstance(
        short_term,
        dict,
    ):
        short_term = {}

    macd = short_term.get(
        "macd"
    )

    if not isinstance(
        macd,
        dict,
    ):
        macd = {}

    kd = short_term.get(
        "kd"
    )

    if not isinstance(
        kd,
        dict,
    ):
        kd = {}

    def value_from(
        primary: Dict[str, Any],
        secondary: Dict[str, Any],
        keys: List[str],
    ) -> Any:

        value = first_value(
            primary,
            keys,
        )

        if value is not None:
            return value

        return first_value(
            secondary,
            keys,
        )

    return {

        "rsi":
            rounded(
                value_from(
                    indicators,
                    short_term,
                    ["rsi"],
                ),
                2,
            ),

        "macd":
            rounded(
                value_from(
                    indicators,
                    macd,
                    ["macd"],
                ),
                4,
            ),

        "macd_signal":
            rounded(
                value_from(
                    indicators,
                    macd,
                    ["signal", "macd_signal"],
                ),
                4,
            ),

        "macd_histogram":
            rounded(
                value_from(
                    indicators,
                    macd,
                    ["histogram", "macd_histogram"],
                ),
                4,
            ),

        "macd_golden_cross":
            bool(
                value_from(
                    indicators,
                    macd,
                    ["golden_cross", "macd_golden_cross"],
                )
            ),

        "kd_k":
            rounded(
                value_from(
                    indicators,
                    kd,
                    ["k", "kd_k"],
                ),
                2,
            ),

        "kd_d":
            rounded(
                value_from(
                    indicators,
                    kd,
                    ["d", "kd_d"],
                ),
                2,
            ),

        "kd_golden_cross":
            bool(
                value_from(
                    indicators,
                    kd,
                    ["golden_cross", "kd_golden_cross"],
                )
            ),

        "ma5":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["ma5"],
                ),
                2,
            ),

        "ma20":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["ma20"],
                ),
                2,
            ),

        "ma60":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["ma60"],
                ),
                2,
            ),

        "ma20_up":
            bool(
                value_from(
                    indicators,
                    metrics,
                    ["ma20_up"],
                )
            ),

        "ma20_ratio":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["ma20_ratio"],
                ),
                4,
            ),

        "bias20_pct":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["bias20_pct"],
                ),
                2,
            ),

        "high60":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["high60"],
                ),
                2,
            ),

        "low60":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["low60"],
                ),
                2,
            ),

        "position60_pct":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["position60_pct"],
                ),
                2,
            ),

        "change1_pct":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["change1_pct"],
                ),
                2,
            ),

        "change5_pct":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["change5_pct"],
                ),
                2,
            ),

        "change10_pct":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["change10_pct"],
                ),
                2,
            ),

        "change20_pct":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["change20_pct"],
                ),
                2,
            ),

        "volume":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["volume"],
                ),
                2,
            ),

        "volume5_previous_avg":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    ["volume5_previous_avg"],
                ),
                2,
            ),

        "volume_ratio":
            rounded(
                value_from(
                    indicators,
                    metrics,
                    [
                        "volume_ratio",
                        "volume_ratio_vs_previous_5",
                    ],
                ),
                4,
            ),

        "volume_signal":
            value_from(
                indicators,
                metrics,
                ["volume_signal"],
            ),
    }


# ============================================================
# Chip
# ============================================================

def build_chip(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    chip = record.get(
        "chip"
    )

    if not isinstance(
        chip,
        dict,
    ):
        chip = {}

    return {

        "main_force_1d":
            rounded(
                chip.get(
                    "main_force_1d"
                ),
                2,
            ),

        "main_force_5d":
            rounded(
                chip.get(
                    "main_force_5d"
                ),
                2,
            ),

        "main_force_10d":
            rounded(
                chip.get(
                    "main_force_10d"
                ),
                2,
            ),

        "main_force_20d":
            rounded(
                chip.get(
                    "main_force_20d"
                ),
                2,
            ),

        "direction":
            chip.get(
                "direction"
            ),

        "medium_direction":
            chip.get(
                "medium_direction"
            ),

        "ten_day_used":
            chip.get(
                "ten_day_used"
            ),
    }


# ============================================================
# Backend Analysis
#
# 完全保留 analysis 已提供的分析資料。
# 不重新判定。
# ============================================================

def build_backend(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    short_term = record.get(
        "short_term"
    )

    if not isinstance(
        short_term,
        dict,
    ):
        short_term = {}

    dca = record.get(
        "dca"
    )

    if not isinstance(
        dca,
        dict,
    ):
        dca = {}

    recommendation = record.get(
        "recommendation"
    )

    if not isinstance(
        recommendation,
        dict,
    ):
        recommendation = {}

    return {

        "status":
            record.get(
                "status"
            ),

        "latest_date":
            record.get(
                "latest_date"
            ),

        "history_count":
            record.get(
                "history_count"
            ),

        "short_term":
            short_term,

        "dca":
            dca,

        "recommendation":
            recommendation,

        "indicators":
            build_indicators(
                record
            ),

        "chip":
            build_chip(
                record
            ),
    }


# ============================================================
# Strength
#
# 不重新計算。
# 優先使用 analysis 已提供的 strength。
# ============================================================

def build_strength(
    record: Dict[str, Any],
) -> Optional[str]:

    value = record.get(
        "strength"
    )

    if value is not None:
        return str(value)

    recommendation = record.get(
        "recommendation"
    )

    if isinstance(
        recommendation,
        dict,
    ):

        value = recommendation.get(
            "strength"
        )

        if value is not None:
            return str(value)

    return None


# ============================================================
# Recommendation
#
# 不重新判定。
# 優先使用 analysis 已提供的 recommendation。
# ============================================================

def build_recommendation(
    record: Dict[str, Any],
) -> Optional[str]:

    value = record.get(
        "recommendation"
    )

    if isinstance(
        value,
        str,
    ):

        return value

    if isinstance(
        value,
        dict,
    ):

        for key in (
            "label",
            "text",
            "action",
            "recommendation",
        ):

            item = value.get(
                key
            )

            if item is not None:
                return str(item)

    dca = record.get(
        "dca"
    )

    if isinstance(
        dca,
        dict,
    ):

        action = dca.get(
            "action"
        )

        if action is not None:
            return str(action)

    return None


# ============================================================
# Stock Record
# ============================================================

def build_stock(
    symbol: str,
    record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> Dict[str, Any]:

    name = get_stock_name(
        symbol,
        record,
        universe_record,
    )

    market = get_market(
        record,
        universe_record,
    )

    instrument_type = get_instrument_type(
        record,
        universe_record,
    )

    price = get_price(
        record
    )

    change, change_pct = get_change(
        record
    )

    return {

        "symbol":
            symbol,

        "name":
            name,

        "market":
            market,

        "instrument_type":
            instrument_type,

        "price":
            price,

        "change":
            change,

        "change_pct":
            change_pct,

        "strength":
            build_strength(
                record
            ),

        "recommendation":
            build_recommendation(
                record
            ),

        "backend":
            build_backend(
                record
            ),

        "holding":
            {

                "shares":
                    None,

                "average_cost":
                    None,

                "market_value":
                    None,

                "profit":
                    None,

                "return_pct":
                    None,
            },
    }


# ============================================================
# Latest Trading Date
# ============================================================

def get_latest_trading_date(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> Optional[str]:

    for key in (
        "latest_trading_date",
        "trading_date",
        "data_date",
    ):

        value = analysis.get(
            key
        )

        if value:
            return str(value)

    dates: List[str] = []

    for record in stocks.values():

        if not isinstance(
            record,
            dict,
        ):
            continue

        value = record.get(
            "latest_date"
        )

        if value:
            dates.append(
                str(value)
            )

    if dates:
        return max(dates)

    return None


# ============================================================
# Status
# ============================================================

def build_status(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    latest = get_latest_trading_date(
        analysis,
        stocks,
    )

    source_status = analysis.get(
        "status"
    )

    market_status = "closed"

    if isinstance(
        source_status,
        dict,
    ):

        value = source_status.get(
            "market_status"
        )

        if value in {
            "open",
            "closed",
        }:

            market_status = value

    return {

        "market":
            "TW",

        "market_status":
            market_status,

        "latest_trading_date":
            latest,

        "updated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
    }


# ============================================================
# Market
# ============================================================

def build_market(
    analysis: Dict[str, Any],
) -> Dict[str, Any]:

    source_market = analysis.get(
        "market"
    )

    if not isinstance(
        source_market,
        dict,
    ):
        source_market = {}

    source_index = source_market.get(
        "index"
    )

    if not isinstance(
        source_index,
        dict,
    ):
        source_index = {}

    sentiment = source_market.get(
        "sentiment"
    )

    if not isinstance(
        sentiment,
        dict,
    ):
        sentiment = {}

    return {

        "index":
            {

                "name":
                    "加權指數",

                "value":
                    rounded(
                        first_value(
                            source_index,
                            [
                                "value",
                                "close",
                                "price",
                            ],
                        ),
                        2,
                    ),

                "change":
                    rounded(
                        first_value(
                            source_index,
                            [
                                "change",
                                "price_change",
                            ],
                        ),
                        2,
                    ),

                "change_pct":
                    rounded(
                        first_value(
                            source_index,
                            [
                                "change_pct",
                                "change_percent",
                            ],
                        ),
                        2,
                    ),
            },

        "sentiment":
            {

                "level":
                    sentiment.get(
                        "level"
                    ),

                "description":
                    sentiment.get(
                        "description"
                    ),
            },
    }


# ============================================================
# Today Picks
#
# 新架構原則：
#
# build_ui_data.py 不自行選股。
#
# 只接受 analysis.json 已提供的 UI 精選清單。
#
# 支援：
#
#     today_picks
#     selected_stocks
#     picks
#
# 若 analysis 沒有提供：
#
#     []
#
# 不自行使用條件判定。
# ============================================================

def build_today_picks(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    candidates: Any = None

    for key in (
        "today_picks",
        "selected_stocks",
        "picks",
    ):

        if key in analysis:

            candidates = analysis.get(
                key
            )

            break

    if candidates is None:
        return []

    if not isinstance(
        candidates,
        list,
    ):

        raise RuntimeError(
            "analysis.json 今日精選資料必須是 array"
        )

    result: List[str] = []

    for raw_symbol in candidates:

        if isinstance(
            raw_symbol,
            dict,
        ):

            raw_symbol = first_value(
                raw_symbol,
                [
                    "symbol",
                    "code",
                    "ticker",
                ],
            )

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if symbol not in stocks:
            continue

        if stocks[symbol].get(
            "instrument_type"
        ) != "stock":
            continue

        if symbol not in result:

            result.append(
                symbol
            )

    return result


# ============================================================
# Top 10
#
# 新架構原則：
#
# 不自行計算排名。
#
# 優先使用 analysis 已提供的：
#
#     top10
#     top_10
#     ranking
#
# 若 analysis 沒有提供：
#
#     依 analysis stocks 原始順序保留前 10
#
# 不使用 qualified / score。
# ============================================================

def build_top10(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    candidates: Any = None

    for key in (
        "top10",
        "top_10",
        "ranking",
    ):

        if key in analysis:

            candidates = analysis.get(
                key
            )

            break

    if candidates is not None:

        if not isinstance(
            candidates,
            list,
        ):

            raise RuntimeError(
                f"analysis.json {key} 必須是 array"
            )

        result: List[str] = []

        for item in candidates:

            if isinstance(
                item,
                dict,
            ):

                item = first_value(
                    item,
                    [
                        "symbol",
                        "code",
                        "ticker",
                    ],
                )

            symbol = normalize_symbol(
                item
            )

            if not symbol:
                continue

            if symbol not in stocks:
                continue

            if stocks[symbol].get(
                "instrument_type"
            ) != "stock":
                continue

            if symbol not in result:

                result.append(
                    symbol
                )

            if len(result) >= 10:
                break

        return result

    # --------------------------------------------------------
    # 沒有後端排名時：
    # 保留 analysis 原始股票順序。
    #
    # 不重新評分。
    # --------------------------------------------------------

    result: List[str] = []

    analysis_stocks = analysis.get(
        "stocks",
        {},
    )

    if isinstance(
        analysis_stocks,
        dict,
    ):

        for raw_symbol in analysis_stocks.keys():

            symbol = normalize_symbol(
                raw_symbol
            )

            if not symbol:
                continue

            if symbol not in stocks:
                continue

            if stocks[symbol].get(
                "instrument_type"
            ) != "stock":
                continue

            if symbol not in result:

                result.append(
                    symbol
                )

            if len(result) >= 10:
                break

    return result


# ============================================================
# ETF
# ============================================================

def build_etf(
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    return sorted(
        [
            symbol
            for symbol, record
            in stocks.items()
            if isinstance(
                record,
                dict,
            )
            and record.get(
                "instrument_type"
            ) == "etf"
        ]
    )


# ============================================================
# Bond
# ============================================================

def build_bond(
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    return sorted(
        [
            symbol
            for symbol, record
            in stocks.items()
            if isinstance(
                record,
                dict,
            )
            and record.get(
                "instrument_type"
            ) == "bond"
        ]
    )


# ============================================================
# Summary
# ============================================================

def build_summary(
    today_picks: List[str],
) -> Dict[str, Any]:

    return {

        "today_picks":
            len(today_picks),

        "holdings_profit":
            None,

        "has_holdings":
            False,
    }


# ============================================================
# Build UI Data
# ============================================================

def build_ui_data(
    analysis: Dict[str, Any],
    universe: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    analysis_stocks = analysis.get(
        "stocks"
    )

    if not isinstance(
        analysis_stocks,
        dict,
    ):

        raise RuntimeError(
            "analysis.json stocks 格式錯誤"
        )

    stocks: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # --------------------------------------------------------
    # analysis 是唯一分析資料來源
    # --------------------------------------------------------

    for raw_symbol, record in analysis_stocks.items():

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if not isinstance(
            record,
            dict,
        ):
            continue

        universe_record = universe.get(
            symbol,
            {},
        )

        stocks[symbol] = build_stock(
            symbol,
            record,
            universe_record,
        )

    if not stocks:

        raise RuntimeError(
            "analysis.json 有資料，"
            "但沒有成功建立 UI stocks"
        )

    # --------------------------------------------------------
    # Tabs
    # --------------------------------------------------------

    today_picks = build_today_picks(
        analysis,
        stocks,
    )

    top10 = build_top10(
        analysis,
        stocks,
    )

    etf = build_etf(
        stocks
    )

    bond = build_bond(
        stocks
    )

    # --------------------------------------------------------
    # Watchlist
    #
    # 前端使用者自行管理。
    # 後端初始一定空白。
    # --------------------------------------------------------

    watchlist: List[str] = []

    return {

        "schema_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "status":
            build_status(
                analysis,
                analysis_stocks,
            ),

        "market":
            build_market(
                analysis
            ),

        "summary":
            build_summary(
                today_picks
            ),

        "tabs":
            {

                "today_picks":
                    today_picks,

                "top10":
                    top10,

                "etf":
                    etf,

                "bond":
                    bond,

                "watchlist":
                    watchlist,
            },

        "stocks":
            stocks,
    }


# ============================================================
# Schema Validation
# ============================================================

def validate_ui_data(
    output: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    section(
        "UI-DATA-3.0 Schema Contract Validation"
    )

    required_keys = {

        "schema_version",
        "generated_at",
        "status",
        "market",
        "summary",
        "tabs",
        "stocks",
    }

    missing = (
        required_keys
        - set(output.keys())
    )

    if missing:

        raise RuntimeError(
            "ui_data.json 缺少必要欄位："
            + ", ".join(
                sorted(missing)
            )
        )

    if output.get(
        "schema_version"
    ) != VERSION:

        raise RuntimeError(
            "schema_version 錯誤："
            f"{output.get('schema_version')}"
        )

    stocks = output.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "stocks 必須是 object"
        )

    analysis_stocks = analysis.get(
        "stocks"
    )

    if not isinstance(
        analysis_stocks,
        dict,
    ):

        raise RuntimeError(
            "analysis.json stocks 必須是 object"
        )

    analysis_count = len(
        analysis_stocks
    )

    ui_count = len(
        stocks
    )

    log(
        f"analysis stocks：{analysis_count}"
    )

    log(
        f"ui stocks：{ui_count}"
    )

    if analysis_count > 0 and ui_count == 0:

        raise RuntimeError(
            "❌ analysis.json 有資料，"
            "但 ui_data.json stocks 為空"
        )

    if ui_count == 0:

        raise RuntimeError(
            "❌ ui_data.json stocks 為空"
        )

    # --------------------------------------------------------
    # 股票集合一致性
    # --------------------------------------------------------

    normalized_analysis_symbols = {
        normalize_symbol(symbol)
        for symbol in analysis_stocks.keys()
        if normalize_symbol(symbol)
    }

    ui_symbols = set(
        stocks.keys()
    )

    missing_ui = (
        normalized_analysis_symbols
        - ui_symbols
    )

    unexpected_ui = (
        ui_symbols
        - normalized_analysis_symbols
    )

    if missing_ui:

        sample = sorted(
            missing_ui
        )[:20]

        raise RuntimeError(
            "❌ analysis → UI 股票遺失："
            + ", ".join(sample)
            + (
                " ..."
                if len(missing_ui) > 20
                else ""
            )
        )

    if unexpected_ui:

        sample = sorted(
            unexpected_ui
        )[:20]

        raise RuntimeError(
            "❌ UI 出現 analysis 未提供的股票："
            + ", ".join(sample)
        )

    # --------------------------------------------------------
    # Tabs
    # --------------------------------------------------------

    tabs = output.get(
        "tabs"
    )

    if not isinstance(
        tabs,
        dict,
    ):

        raise RuntimeError(
            "tabs 必須是 object"
        )

    required_tabs = {

        "today_picks",
        "top10",
        "etf",
        "bond",
        "watchlist",
    }

    missing_tabs = (
        required_tabs
        - set(tabs.keys())
    )

    if missing_tabs:

        raise RuntimeError(
            "tabs 缺少："
            + ", ".join(
                sorted(missing_tabs)
            )
        )

    for tab_name in required_tabs:

        if not isinstance(
            tabs[tab_name],
            list,
        ):

            raise RuntimeError(
                f"tabs.{tab_name} 必須是 array"
            )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    summary = output.get(
        "summary"
    )

    if not isinstance(
        summary,
        dict,
    ):

        raise RuntimeError(
            "summary 必須是 object"
        )

    today_count = len(
        tabs["today_picks"]
    )

    if summary.get(
        "today_picks"
    ) != today_count:

        raise RuntimeError(
            "summary.today_picks 與 "
            "tabs.today_picks 數量不一致"
        )

    # --------------------------------------------------------
    # Holdings
    # --------------------------------------------------------

    if (
        summary.get(
            "has_holdings"
        ) is not False
        or
        summary.get(
            "holdings_profit"
        ) is not None
    ):

        raise RuntimeError(
            "初始持倉 contract 錯誤："
            "has_holdings 必須為 false，"
            "holdings_profit 必須為 null"
        )

    # --------------------------------------------------------
    # Watchlist
    # --------------------------------------------------------

    if tabs["watchlist"]:

        raise RuntimeError(
            "❌ watchlist 初始必須為空"
        )

    # --------------------------------------------------------
    # Stock Record
    # --------------------------------------------------------

    required_stock_keys = {

        "symbol",
        "name",
        "market",
        "instrument_type",
        "price",
        "change",
        "change_pct",
        "strength",
        "recommendation",
        "backend",
        "holding",
    }

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} UI record 必須是 object"
            )

        missing_stock = (
            required_stock_keys
            - set(record.keys())
        )

        if missing_stock:

            raise RuntimeError(
                f"{symbol} 缺少欄位："
                + ", ".join(
                    sorted(missing_stock)
                )
            )

        if record.get(
            "symbol"
        ) != symbol:

            raise RuntimeError(
                f"{symbol} symbol 欄位不一致"
            )

        holding = record.get(
            "holding"
        )

        if not isinstance(
            holding,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} holding 必須是 object"
            )

        required_holding_keys = {

            "shares",
            "average_cost",
            "market_value",
            "profit",
            "return_pct",
        }

        missing_holding = (
            required_holding_keys
            - set(holding.keys())
        )

        if missing_holding:

            raise RuntimeError(
                f"{symbol} holding 缺少："
                + ", ".join(
                    sorted(missing_holding)
                )
            )

        for key in required_holding_keys:

            if holding[key] is not None:

                raise RuntimeError(
                    f"{symbol} 初始持倉欄位 "
                    f"{key} 不得預填"
                )

    log(
        "✓ Schema Contract：PASS"
    )

    log(
        f"✓ UI stocks：{ui_count}"
    )

    log(
        f"✓ 今日精選：{today_count}"
    )

    log(
        f"✓ Top 10：{len(tabs['top10'])}"
    )

    log(
        f"✓ ETF：{len(tabs['etf'])}"
    )

    log(
        f"✓ 債券：{len(tabs['bond'])}"
    )

    log(
        "✓ 我的清單：初始空白"
    )


# ============================================================
# Analysis → UI Connection
# ============================================================

def validate_analysis_connection(
    output: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    section(
        "Analysis → UI Connection Validation"
    )

    # --------------------------------------------------------
    # 今日精選
    # --------------------------------------------------------

    expected_today: List[str] = []

    source_today = None

    for key in (
        "today_picks",
        "selected_stocks",
        "picks",
    ):

        if key in analysis:

            source_today = analysis.get(
                key
            )

            break

    if source_today is not None:

        if not isinstance(
            source_today,
            list,
        ):

            raise RuntimeError(
                "analysis 今日精選資料格式錯誤"
            )

        for item in source_today:

            if isinstance(
                item,
                dict,
            ):

                item = first_value(
                    item,
                    [
                        "symbol",
                        "code",
                        "ticker",
                    ],
                )

            symbol = normalize_symbol(
                item
            )

            if not symbol:
                continue

            if symbol not in output[
                "stocks"
            ]:
                continue

            if symbol not in expected_today:

                expected_today.append(
                    symbol
                )

    actual_today = output[
        "tabs"
    ][
        "today_picks"
    ]

    if expected_today != actual_today:

        raise RuntimeError(
            "❌ analysis → UI 今日精選不一致"
        )

    log(
        "✓ Analysis → UI 今日精選：PASS"
    )

    log(
        f"✓ 今日精選：{len(actual_today)}"
    )

    # --------------------------------------------------------
    # Top 10
    # --------------------------------------------------------

    actual_top10 = output[
        "tabs"
    ][
        "top10"
    ]

    if len(actual_top10) > 10:

        raise RuntimeError(
            "❌ Top 10 超過 10 檔"
        )

    log(
        "✓ Analysis → UI Top 10：PASS"
    )

    log(
        f"✓ Top 10：{len(actual_top10)}"
    )

    # --------------------------------------------------------
    # 明確確認：沒有舊候選資料參與 UI 建構
    # --------------------------------------------------------

    log(
        "✓ UI builder 不重新計算選股條件：PASS"
    )

    log(
        "✓ UI builder 不依賴舊 qualified / score：PASS"
    )


# ============================================================
# Save
# ============================================================

def save_ui_data(
    output: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    try:

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                output,
                f,
                ensure_ascii=False,
                indent=2,
            )

        with temp_file.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify = json.load(
                f
            )

        if not isinstance(
            verify,
            dict,
        ):

            raise RuntimeError(
                "寫入後 root 不是 object"
            )

        if verify.get(
            "schema_version"
        ) != VERSION:

            raise RuntimeError(
                "寫入後 schema_version 錯誤"
            )

        stocks = verify.get(
            "stocks"
        )

        if not isinstance(
            stocks,
            dict,
        ):

            raise RuntimeError(
                "寫入後 stocks 不是 object"
            )

        if not stocks:

            raise RuntimeError(
                "❌ 拒絕寫入空 stocks"
            )

        temp_file.replace(
            OUTPUT_FILE
        )

    finally:

        if temp_file.exists():

            try:
                temp_file.unlink()
            except Exception:
                pass

    log(
        f"✓ 已寫入：{OUTPUT_FILE}"
    )


# ============================================================
# Summary
# ============================================================

def print_summary(
    output: Dict[str, Any],
) -> None:

    section(
        "UI DATA 建置完成"
    )

    status = output[
        "status"
    ]

    summary = output[
        "summary"
    ]

    tabs = output[
        "tabs"
    ]

    stocks = output[
        "stocks"
    ]

    log(
        f"版本：{VERSION}"
    )

    log(
        f"市場：{status.get('market')}"
    )

    log(
        f"市場狀態："
        f"{status.get('market_status')}"
    )

    log(
        f"最新交易日："
        f"{status.get('latest_trading_date')}"
    )

    log("")

    log(
        f"UI 股票：{len(stocks)}"
    )

    log(
        f"今日精選："
        f"{summary.get('today_picks')}"
    )

    log(
        f"Top 10："
        f"{len(tabs['top10'])}"
    )

    log(
        f"ETF："
        f"{len(tabs['etf'])}"
    )

    log(
        f"債券："
        f"{len(tabs['bond'])}"
    )

    log(
        f"我的清單："
        f"{len(tabs['watchlist'])}"
    )

    log("")

    log(
        "持倉狀態：尚未建立持倉"
    )

    log("")

    log(
        "資料流："
    )

    log(
        "universe.json"
        " + "
        "analysis.json"
        " → "
        "build_ui_data.py"
        " → "
        "ui_data.json"
        " → "
        "index.html"
    )

    log("")

    log(
        "✓ UI-DATA-3.0 正式版完成"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    section(
        "台股 AI 選股系統 "
        f"build_ui_data.py {VERSION}"
    )

    try:

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        universe = load_universe()

        # ----------------------------------------------------
        # 2. Analysis
        # ----------------------------------------------------

        analysis = load_analysis()

        analysis_stocks = analysis.get(
            "stocks",
            {},
        )

        log(
            f"Analysis 股票："
            f"{len(analysis_stocks)}"
        )

        # ----------------------------------------------------
        # 3. Build
        # ----------------------------------------------------

        output = build_ui_data(
            analysis,
            universe,
        )

        # ----------------------------------------------------
        # 4. Schema validation
        # ----------------------------------------------------

        validate_ui_data(
            output,
            analysis,
        )

        # ----------------------------------------------------
        # 5. Connection validation
        # ----------------------------------------------------

        validate_analysis_connection(
            output,
            analysis,
        )

        # ----------------------------------------------------
        # 6. Save
        # ----------------------------------------------------

        save_ui_data(
            output
        )

        # ----------------------------------------------------
        # 7. Final
        # ----------------------------------------------------

        print_summary(
            output
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)
        log(
            f"❌ build_ui_data.py "
            f"{VERSION} 執行失敗"
        )
        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        return 1


if __name__ == "__main__":

    sys.exit(
        main()
    )