#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py
正式版 UI-DATA-2.0

============================================================
核心資料鏈
============================================================

universe.json
        +
analysis.json
        |
        v
build_ui_data.py
        |
        v
ui_data.json
        |
        v
index.html


============================================================
本程式責任邊界
============================================================

本程式只負責：

    analysis / universe
        ↓
    UI schema transformation
        ↓
    ui_data.json

本程式絕對不：

    ❌ 抓 API
    ❌ 抓股價
    ❌ 計算 RSI
    ❌ 計算 MACD
    ❌ 計算 KD
    ❌ 計算成交量
    ❌ 計算主力籌碼
    ❌ 計算 DCA
    ❌ 建立新的選股條件
    ❌ 修改 analysis.json
    ❌ 修改 universe.json
    ❌ 修改 prices shards
    ❌ 修改 chip.json


============================================================
重要架構原則
============================================================

analysis.json
    = 分析結果唯一來源

universe.json
    = 完整標的宇宙 + 名稱/市場/商品類型補充

analysis.json 不存在的股票：
    → 不得進入 UI stocks

analysis.json 的股票：
    → 必須完整進入 UI stocks

因此：

    analysis stocks N
            ↓
    UI stocks N

不能因為 UI 需要而重新篩選成：

    1985
    2143
    其他固定數字


============================================================
今日精選
============================================================

今日精選唯一來源：

    analysis.json
        short_term_candidates

前端不得重新判斷。

本程式也不得自行判斷 RSI / MACD / KD
或其他條件來建立今日精選。


============================================================
六項核心
============================================================

本版本不建立舊版「六項核心」邏輯。

也不在 UI schema 中產生：

    six_core
    core_conditions
    core_score

analysis.json 如果仍然提供舊欄位，
本程式只允許原樣放在 backend.short_term
供除錯/顯示使用，不以其重新篩選股票。


============================================================
持倉
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
UI-DATA-2.0 Contract
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

VERSION = "UI-DATA-2.0"

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
        ".TSE",
        ".OTC",
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

    if not isinstance(
        record,
        dict,
    ):
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

    if not isinstance(
        data,
        dict,
    ):

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
            "universe.json 沒有有效標的"
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

    if not isinstance(
        data,
        dict,
    ):

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

    name = first_value(
        analysis_record,
        [
            "name",
            "stock_name",
            "security_name",
            "company_name",
            "名稱",
            "股票名稱",
        ],
    )

    if name:
        return str(name).strip()

    name = first_value(
        universe_record,
        [
            "name",
            "stock_name",
            "security_name",
            "company_name",
            "名稱",
            "股票名稱",
        ],
    )

    if name:
        return str(name).strip()

    return symbol


# ============================================================
# Market
# ============================================================

def get_market(
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> str:

    market = first_value(
        analysis_record,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if market:
        return str(
            market
        ).strip()

    market = first_value(
        universe_record,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if market:
        return str(
            market
        ).strip()

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

    if any(
        token in text
        for token in (
            "etn",
            "指數投資證券",
        )
    ):
        return "etn"

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

        return rounded(
            value,
            2,
        )

    return rounded(
        first_value(
            record,
            [
                "price",
                "close",
                "latest_price",
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
                "change1_pct",
                "change_pct",
                "change_percent",
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

    return {

        "rsi":
            rounded(
                short_term.get(
                    "rsi"
                ),
                2,
            ),

        "macd":
            rounded(
                macd.get(
                    "macd"
                ),
                4,
            ),

        "macd_signal":
            rounded(
                macd.get(
                    "signal"
                ),
                4,
            ),

        "macd_histogram":
            rounded(
                macd.get(
                    "histogram"
                ),
                4,
            ),

        "macd_golden_cross":
            bool(
                macd.get(
                    "golden_cross",
                    False,
                )
            ),

        "kd_k":
            rounded(
                kd.get(
                    "k"
                ),
                2,
            ),

        "kd_d":
            rounded(
                kd.get(
                    "d"
                ),
                2,
            ),

        "kd_golden_cross":
            bool(
                kd.get(
                    "golden_cross",
                    False,
                )
            ),

        "ma5":
            rounded(
                metrics.get(
                    "ma5"
                ),
                2,
            ),

        "ma20":
            rounded(
                metrics.get(
                    "ma20"
                ),
                2,
            ),

        "ma60":
            rounded(
                metrics.get(
                    "ma60"
                ),
                2,
            ),

        "ma20_up":
            bool(
                metrics.get(
                    "ma20_up",
                    False,
                )
            ),

        "ma20_ratio":
            rounded(
                metrics.get(
                    "ma20_ratio"
                ),
                4,
            ),

        "bias20_pct":
            rounded(
                metrics.get(
                    "bias20_pct"
                ),
                2,
            ),

        "high60":
            rounded(
                metrics.get(
                    "high60"
                ),
                2,
            ),

        "low60":
            rounded(
                metrics.get(
                    "low60"
                ),
                2,
            ),

        "position60_pct":
            rounded(
                metrics.get(
                    "position60_pct"
                ),
                2,
            ),

        "change1_pct":
            rounded(
                metrics.get(
                    "change1_pct"
                ),
                2,
            ),

        "change5_pct":
            rounded(
                metrics.get(
                    "change5_pct"
                ),
                2,
            ),

        "change10_pct":
            rounded(
                metrics.get(
                    "change10_pct"
                ),
                2,
            ),

        "change20_pct":
            rounded(
                metrics.get(
                    "change20_pct"
                ),
                2,
            ),

        "volume":
            rounded(
                metrics.get(
                    "volume"
                ),
                2,
            ),

        "volume5_previous_avg":
            rounded(
                metrics.get(
                    "volume5_previous_avg"
                ),
                2,
            ),

        "volume_ratio":
            rounded(
                metrics.get(
                    "volume_ratio_vs_previous_5"
                ),
                4,
            ),

        "volume_signal":
            metrics.get(
                "volume_signal"
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
            bool(
                chip.get(
                    "ten_day_used",
                    True,
                )
            ),
    }


# ============================================================
# Recommendation
#
# 只讀 analysis.json 已產生的 DCA action。
# 不自行建立選股條件。
# ============================================================

def build_recommendation(
    record: Dict[str, Any],
) -> str:

    dca = record.get(
        "dca"
    )

    if not isinstance(
        dca,
        dict,
    ):
        dca = {}

    action = dca.get(
        "action"
    )

    if action:

        mapping = {

            "積極買進":
                "積極關注",

            "正常買進":
                "偏多，可分批",

            "觀察":
                "等待拉回",

            "高檔觀察":
                "暫不追價",

            "暫停加碼":
                "暫停操作",

            "極端負乖離":
                "偏多，可分批",

            "資料不足":
                "等待資料",
        }

        return mapping.get(
            str(action),
            str(action),
        )

    # --------------------------------------------------------
    # short_term qualified 只作後端既有結果的顯示。
    # 不以它建立新的候選集合。
    # --------------------------------------------------------

    short_term = record.get(
        "short_term"
    )

    if isinstance(
        short_term,
        dict,
    ):

        if short_term.get(
            "qualified"
        ) is True:

            return "偏多，可分批"

    return "觀察"


# ============================================================
# Strength
# ============================================================

def build_strength(
    record: Dict[str, Any],
) -> str:

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

    # --------------------------------------------------------
    # analysis 已提供 qualified
    # 只作顯示，不重新計算。
    # --------------------------------------------------------

    if short_term.get(
        "qualified"
    ) is True:

        return "強勢"

    score = number(
        short_term.get(
            "score"
        )
    )

    if score is not None:

        if score >= 4:
            return "強勢"

        if score >= 2:
            return "中性"

        return "弱勢"

    action = dca.get(
        "action"
    )

    if action in {
        "積極買進",
        "正常買進",
        "極端負乖離",
    }:

        return "強勢"

    if action in {
        "觀察",
        "高檔觀察",
    }:

        return "中性"

    if action == "暫停加碼":

        return "弱勢"

    return "中性"


# ============================================================
# Stock
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

    dca = record.get(
        "dca"
    )

    if not isinstance(
        dca,
        dict,
    ):
        dca = {}

    short_term = record.get(
        "short_term"
    )

    if not isinstance(
        short_term,
        dict,
    ):
        short_term = {}

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
            {

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

                "indicators":
                    build_indicators(
                        record
                    ),

                "chip":
                    build_chip(
                        record
                    ),
            },

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
# 唯一來源：
#
# analysis.json
#     short_term_candidates
#
# 不重新計算、不重新篩選。
# ============================================================

def build_today_picks(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    candidates = analysis.get(
        "short_term_candidates",
        [],
    )

    if not isinstance(
        candidates,
        list,
    ):

        raise RuntimeError(
            "analysis.json short_term_candidates "
            "必須是 array"
        )

    result: List[str] = []

    for raw_symbol in candidates:

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if symbol not in stocks:

            raise RuntimeError(
                "❌ short_term_candidates "
                f"包含 analysis stocks 不存在的 symbol："
                f"{symbol}"
            )

        record = stocks[
            symbol
        ]

        if record.get(
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
# Top 10 是後端已存在資料的排序展示。
#
# 不重新計算任何指標。
#
# 排序來源：
#   short_term.qualified
#   short_term.score
#   indicators.ma20_up
#   indicators.volume_ratio
#   dca.level
#
# 這些全部都已經存在 analysis。
# ============================================================

def top10_sort_key(
    symbol: str,
    record: Dict[str, Any],
) -> Tuple:

    backend = record.get(
        "backend"
    )

    if not isinstance(
        backend,
        dict,
    ):
        backend = {}

    short_term = backend.get(
        "short_term"
    )

    if not isinstance(
        short_term,
        dict,
    ):
        short_term = {}

    dca = backend.get(
        "dca"
    )

    if not isinstance(
        dca,
        dict,
    ):
        dca = {}

    indicators = backend.get(
        "indicators"
    )

    if not isinstance(
        indicators,
        dict,
    ):
        indicators = {}

    qualified = (
        1
        if short_term.get(
            "qualified",
            False,
        )
        else 0
    )

    score = number(
        short_term.get(
            "score"
        )
    )

    if score is None:
        score = 0

    ma20_up = (
        1
        if indicators.get(
            "ma20_up",
            False,
        )
        else 0
    )

    volume_ratio = number(
        indicators.get(
            "volume_ratio"
        )
    )

    if volume_ratio is None:
        volume_ratio = 0

    dca_level = number(
        dca.get(
            "level"
        )
    )

    if dca_level is None:
        dca_level = 0

    return (
        qualified,
        score,
        ma20_up,
        volume_ratio,
        dca_level,
        symbol,
    )


def build_top10(
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    symbols: List[str] = []

    for symbol, record in stocks.items():

        if not isinstance(
            record,
            dict,
        ):
            continue

        if record.get(
            "instrument_type"
        ) != "stock":
            continue

        symbols.append(
            symbol
        )

    symbols.sort(
        key=lambda symbol:
            top10_sort_key(
                symbol,
                stocks[symbol],
            ),
        reverse=True,
    )

    return symbols[:10]


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
# Watchlist
# ============================================================

def build_watchlist() -> List[str]:

    # --------------------------------------------------------
    # Watchlist 是前端本機功能。
    # 後端初始永遠空白。
    # --------------------------------------------------------

    return []


# ============================================================
# Summary
# ============================================================

def build_summary(
    today_picks: List[str],
    analysis_count: int,
    universe_count: int,
) -> Dict[str, Any]:

    return {

        "today_picks":
            len(today_picks),

        "analysis_stocks":
            analysis_count,

        "universe_stocks":
            universe_count,

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
    # analysis 是 UI 股票資料唯一來源
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

        stocks[
            symbol
        ] = build_stock(
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
    # 今日精選
    # --------------------------------------------------------

    today_picks = build_today_picks(
        analysis,
        stocks,
    )

    # --------------------------------------------------------
    # Top 10
    # --------------------------------------------------------

    top10 = build_top10(
        stocks
    )

    # --------------------------------------------------------
    # ETF
    # --------------------------------------------------------

    etf = build_etf(
        stocks
    )

    # --------------------------------------------------------
    # Bond
    # --------------------------------------------------------

    bond = build_bond(
        stocks
    )

    # --------------------------------------------------------
    # Watchlist
    # --------------------------------------------------------

    watchlist = build_watchlist()

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
                today_picks,
                len(analysis_stocks),
                len(universe),
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
    universe: Dict[str, Dict[str, Any]],
) -> None:

    section(
        "UI-DATA-2.0 Schema Contract Validation"
    )

    # --------------------------------------------------------
    # Root
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Stocks
    # --------------------------------------------------------

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

    log(
        f"universe stocks：{len(universe)}"
    )

    # --------------------------------------------------------
    # 核心斷點
    # --------------------------------------------------------

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
    # analysis → UI 股票集合必須 1:1
    # --------------------------------------------------------

    normalized_analysis_symbols = {
        normalize_symbol(symbol)
        for symbol in analysis_stocks.keys()
        if normalize_symbol(symbol)
    }

    ui_symbols = set(
        stocks.keys()
    )

    if normalized_analysis_symbols != ui_symbols:

        missing_ui = (
            normalized_analysis_symbols
            - ui_symbols
        )

        unexpected_ui = (
            ui_symbols
            - normalized_analysis_symbols
        )

        message = (
            "❌ analysis → UI 股票集合不一致"
        )

        if missing_ui:

            message += (
                "\n遺失："
                + ", ".join(
                    sorted(
                        missing_ui
                    )[:20]
                )
            )

        if unexpected_ui:

            message += (
                "\n多出："
                + ", ".join(
                    sorted(
                        unexpected_ui
                    )[:20]
                )
            )

        raise RuntimeError(
            message
        )

    log(
        "✓ analysis → UI stocks：1:1 PASS"
    )

    # --------------------------------------------------------
    # 不得把 Universe 全部灌入 UI
    #
    # 這是避免 2143 → UI 的核心防線。
    # --------------------------------------------------------

    if ui_count > len(universe):

        raise RuntimeError(
            "❌ UI stocks 數量大於 Universe"
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
                sorted(
                    missing_tabs
                )
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
    # Today picks
    # --------------------------------------------------------

    candidates = analysis.get(
        "short_term_candidates",
        [],
    )

    if not isinstance(
        candidates,
        list,
    ):

        raise RuntimeError(
            "analysis.short_term_candidates "
            "必須是 array"
        )

    expected_today_picks: List[str] = []

    for raw_symbol in candidates:

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if symbol not in ui_symbols:
            continue

        if stocks[
            symbol
        ].get(
            "instrument_type"
        ) != "stock":

            continue

        if symbol not in expected_today_picks:

            expected_today_picks.append(
                symbol
            )

    actual_today_picks = tabs[
        "today_picks"
    ]

    if expected_today_picks != actual_today_picks:

        raise RuntimeError(
            "❌ short_term_candidates "
            "→ today_picks 不一致"
        )

    log(
        "✓ short_term_candidates "
        "→ today_picks：PASS"
    )

    log(
        f"✓ 今日精選："
        f"{len(actual_today_picks)}"
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

    if summary.get(
        "today_picks"
    ) != len(
        actual_today_picks
    ):

        raise RuntimeError(
            "summary.today_picks "
            "與 tabs.today_picks 不一致"
        )

    if summary.get(
        "analysis_stocks"
    ) != analysis_count:

        raise RuntimeError(
            "summary.analysis_stocks "
            "與 analysis stocks 不一致"
        )

    if summary.get(
        "universe_stocks"
    ) != len(universe):

        raise RuntimeError(
            "summary.universe_stocks "
            "與 universe 不一致"
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
            "has_holdings=false，"
            "holdings_profit=null"
            " 才是合法初始狀態"
        )

    # --------------------------------------------------------
    # Watchlist
    # --------------------------------------------------------

    if tabs[
        "watchlist"
    ]:

        raise RuntimeError(
            "❌ watchlist 初始必須為空"
        )

    # --------------------------------------------------------
    # Stock schema
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

    required_holding_keys = {

        "shares",
        "average_cost",
        "market_value",
        "profit",
        "return_pct",
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
                    sorted(
                        missing_stock
                    )
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

        missing_holding = (
            required_holding_keys
            - set(holding.keys())
        )

        if missing_holding:

            raise RuntimeError(
                f"{symbol} holding 缺少："
                + ", ".join(
                    sorted(
                        missing_holding
                    )
                )
            )

        for key in required_holding_keys:

            if holding[
                key
            ] is not None:

                raise RuntimeError(
                    f"{symbol} 初始持倉欄位 "
                    f"{key} 不得預填"
                )

    # --------------------------------------------------------
    # 禁止舊六項核心在 UI root / tabs 層
    # --------------------------------------------------------

    forbidden_root_keys = {

        "six_core",
        "six_core_conditions",
        "core_conditions",
        "core_score",
        "core_filter",
        "six_conditions",
    }

    root_overlap = (
        forbidden_root_keys
        & set(output.keys())
    )

    if root_overlap:

        raise RuntimeError(
            "❌ ui_data root 存在舊六項核心欄位："
            + ", ".join(
                sorted(
                    root_overlap
                )
            )
        )

    forbidden_tab_keys = (
        forbidden_root_keys
        & set(tabs.keys())
    )

    if forbidden_tab_keys:

        raise RuntimeError(
            "❌ tabs 存在舊六項核心欄位："
            + ", ".join(
                sorted(
                    forbidden_tab_keys
                )
            )
        )

    log(
        "✓ Schema Contract：PASS"
    )

    log(
        "✓ analysis → UI：1:1 PASS"
    )

    log(
        "✓ Universe 不會直接灌入 UI：PASS"
    )

    log(
        "✓ 今日精選使用 backend candidates：PASS"
    )

    log(
        "✓ 舊六項核心 root/tab schema：PASS"
    )

    log(
        "✓ 初始持倉：PASS"
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

    candidates = analysis.get(
        "short_term_candidates",
        [],
    )

    if not isinstance(
        candidates,
        list,
    ):

        raise RuntimeError(
            "analysis.json "
            "short_term_candidates 必須是 array"
        )

    expected: List[str] = []

    for raw_symbol in candidates:

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if symbol not in output[
            "stocks"
        ]:

            raise RuntimeError(
                "❌ candidate 不存在於 UI stocks："
                f"{symbol}"
            )

        if output[
            "stocks"
        ][symbol].get(
            "instrument_type"
        ) != "stock":

            continue

        if symbol not in expected:

            expected.append(
                symbol
            )

    actual = output[
        "tabs"
    ][
        "today_picks"
    ]

    if expected != actual:

        raise RuntimeError(
            "❌ analysis → UI 今日精選不一致"
        )

    log(
        "✓ short_term_candidates "
        "→ today_picks：PASS"
    )

    log(
        f"✓ 後端候選：{len(candidates)}"
    )

    log(
        f"✓ UI 今日精選：{len(actual)}"
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

        # ----------------------------------------------------
        # 寫入後驗證
        # ----------------------------------------------------

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

        if verify.get(
            "summary",
            {},
        ).get(
            "has_holdings"
        ) is not False:

            raise RuntimeError(
                "❌ 拒絕寫入預設持倉"
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
        f"Universe："
        f"{summary.get('universe_stocks')}"
    )

    log(
        f"Analysis 股票："
        f"{summary.get('analysis_stocks')}"
    )

    log(
        f"UI 股票："
        f"{len(stocks)}"
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
        "✓ build_ui_data.py "
        f"{VERSION} 完成"
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
            universe,
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