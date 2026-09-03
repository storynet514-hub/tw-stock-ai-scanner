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
責任邊界
============================================================

本程式只負責：

    analysis.json
          +
    universe.json
          ↓
    UI schema transformation
          ↓
    ui_data.json

本程式不：

    ❌ 呼叫 API
    ❌ 抓取股價
    ❌ 計算 RSI
    ❌ 計算 MACD
    ❌ 計算 KD
    ❌ 計算成交量
    ❌ 計算籌碼
    ❌ 建立新的選股條件
    ❌ 修改 analysis.json
    ❌ 修改 universe.json
    ❌ 修改 prices/
    ❌ 修改 chip.json

============================================================
資料來源契約
============================================================

analysis.json
    = 分析結果唯一來源

universe.json
    = 標的身份、名稱、交易市場、商品類型補充來源

UI stocks：

    analysis.json.stocks
        ↓
    必須全部進入 ui_data.json.stocks

不得：

    ❌ 用 universe 反向補入 analysis 沒有的股票
    ❌ 固定股票數量
    ❌ 因為 UI 顯示需求自行篩股票

============================================================
今日精選
============================================================

唯一來源：

    analysis.json.short_term_candidates

本程式只轉換，不重新計算條件。

============================================================
持倉
============================================================

後端不建立預設持倉。

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

stock:

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
import os
import sys
import tempfile
from datetime import datetime, timezone
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
        raise RuntimeError(f"找不到檔案：{path}")

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception as exc:
        raise RuntimeError(
            f"JSON 讀取失敗：{path}：{exc}"
        ) from exc


def atomic_write_json(path: Path, data: Any) -> None:
    """
    Atomic write：

        temp file
             ↓
        fsync
             ↓
        os.replace

    避免 GitHub Actions 在寫檔中斷時留下半份 JSON。
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )

    temp_path = Path(temp_name)

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(
            temp_path,
            path,
        )

    except Exception:
        try:
            temp_path.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        raise


# ============================================================
# Number
# ============================================================

def number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        result = float(
            str(value)
            .replace(",", "")
            .strip()
        )

        if not math.isfinite(result):
            return None

        return result

    except Exception:
        return None


def rounded(
    value: Any,
    digits: int = 2,
) -> Optional[float]:
    value = number(value)

    if value is None:
        return None

    return round(
        value,
        digits,
    )


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
            text = text[:-len(suffix)]
            break

    return text.strip()


# ============================================================
# Generic helpers
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


def as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value

    return {}


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        return value.strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
            "golden",
            "golden_cross",
            "符合",
            "是",
        }

    return False


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
            "universe.json 格式錯誤：根節點必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "universe.json stocks 格式錯誤：必須是 object"
        )

    if not stocks:
        raise RuntimeError(
            "universe.json stocks 為空"
        )

    result: Dict[
        str,
        Dict[str, Any],
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

        # 同一 symbol 不允許靜默覆蓋。
        if symbol in result:
            raise RuntimeError(
                "universe.json 發現重複 symbol："
                f"{symbol}"
            )

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
            expected = int(
                universe_count
            )
        except Exception as exc:
            raise RuntimeError(
                "universe.json universe_count 格式錯誤"
            ) from exc

        if expected != len(result):
            raise RuntimeError(
                "universe.json universe_count 不一致："
                f"header={expected}, "
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
            "analysis.json 格式錯誤：根節點必須是 object"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "analysis.json stocks 格式錯誤：必須是 object"
        )

    if not stocks:
        raise RuntimeError(
            "analysis.json stocks 為空"
        )

    return data


def normalize_analysis_stocks(
    analysis: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:

    raw_stocks = analysis.get(
        "stocks"
    )

    if not isinstance(
        raw_stocks,
        dict,
    ):
        raise RuntimeError(
            "analysis.json stocks 必須是 object"
        )

    result: Dict[
        str,
        Dict[str, Any],
    ] = {}

    for raw_symbol, item in raw_stocks.items():

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            raise RuntimeError(
                "analysis.json 出現空白 symbol"
            )

        if not isinstance(
            item,
            dict,
        ):
            raise RuntimeError(
                f"analysis.json stocks[{raw_symbol}] 必須是 object"
            )

        if symbol in result:
            raise RuntimeError(
                "analysis.json 發現 normalize 後重複 symbol："
                f"{symbol}"
            )

        record = dict(item)

        record["symbol"] = symbol

        result[symbol] = record

    if not result:
        raise RuntimeError(
            "analysis.json normalize 後沒有有效股票"
        )

    return result


# ============================================================
# Name
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

    value = first_value(
        analysis_record,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if value is None:
        value = first_value(
            universe_record,
            [
                "market",
                "exchange",
                "market_type",
            ],
        )

    if value is None:
        return ""

    text = str(value).strip()

    mapping = {
        "TWSE": "TWSE",
        "TPEX": "TPEX",
        "TSE": "TWSE",
        "OTC": "TPEX",
    }

    return mapping.get(
        text.upper(),
        text,
    )


# ============================================================
# Instrument Type
# ============================================================

def get_instrument_type(
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> str:

    # Universe 是商品身份的主要來源。
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
        return "stock"

    text = str(
        value
    ).strip().lower()

    if text in {
        "etf",
        "fund",
        "funds",
        "etf基金",
        "指數型基金",
        "指數股票型基金",
    }:
        return "etf"

    if (
        "etf" in text
        or "基金" in text
    ):
        return "etf"

    if (
        "bond" in text
        or "債券" in text
    ):
        return "bond"

    if (
        "etn" in text
        or "指數投資證券" in text
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

    return as_dict(
        metrics
    )


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

    if value is None:
        value = first_value(
            record,
            [
                "price",
                "close",
                "latest_price",
                "last_price",
            ],
        )

    return rounded(
        value,
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

    change = first_value(
        metrics,
        [
            "change",
            "price_change",
        ],
    )

    if change is None:
        change = first_value(
            record,
            [
                "change",
                "price_change",
            ],
        )

    change_pct = first_value(
        metrics,
        [
            "change1_pct",
            "change_pct",
            "change_percent",
        ],
    )

    if change_pct is None:
        change_pct = first_value(
            record,
            [
                "change1_pct",
                "change_pct",
                "change_percent",
            ],
        )

    return (
        rounded(change, 2),
        rounded(change_pct, 2),
    )


# ============================================================
# Short-term analysis
# ============================================================

def get_short_term(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    return as_dict(
        record.get(
            "short_term"
        )
    )


# ============================================================
# Indicators
# ============================================================

def build_indicators(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    short_term = get_short_term(
        record
    )

    macd = as_dict(
        short_term.get(
            "macd"
        )
    )

    kd = as_dict(
        short_term.get(
            "kd"
        )
    )

    metrics = get_metrics(
        record
    )

    return {

        "rsi":
            rounded(
                first_value(
                    short_term,
                    ["rsi"],
                ),
                2,
            ),

        "macd":
            rounded(
                first_value(
                    macd,
                    ["macd", "value"],
                ),
                4,
            ),

        "macd_signal":
            rounded(
                first_value(
                    macd,
                    ["signal"],
                ),
                4,
            ),

        "macd_histogram":
            rounded(
                first_value(
                    macd,
                    ["histogram"],
                ),
                4,
            ),

        "macd_golden_cross":
            bool_value(
                first_value(
                    macd,
                    [
                        "golden_cross",
                        "is_golden_cross",
                    ],
                )
            ),

        "kd_k":
            rounded(
                first_value(
                    kd,
                    ["k", "K"],
                ),
                2,
            ),

        "kd_d":
            rounded(
                first_value(
                    kd,
                    ["d", "D"],
                ),
                2,
            ),

        "kd_golden_cross":
            bool_value(
                first_value(
                    kd,
                    [
                        "golden_cross",
                        "is_golden_cross",
                    ],
                )
            ),

        "volume":
            rounded(
                first_value(
                    metrics,
                    [
                        "volume",
                        "latest_volume",
                    ],
                ),
                0,
            ),

        "volume_ratio":
            rounded(
                first_value(
                    metrics,
                    [
                        "volume_ratio",
                        "volume_vs_ma5",
                        "volume_ma5_ratio",
                    ],
                ),
                2,
            ),

        "ma20":
            rounded(
                first_value(
                    metrics,
                    [
                        "ma20",
                        "ma_20",
                    ],
                ),
                2,
            ),

        "ma20_rising":
            bool_value(
                first_value(
                    metrics,
                    [
                        "ma20_rising",
                        "ma20_up",
                    ],
                )
            ),

        "ma20_ratio":
            rounded(
                first_value(
                    metrics,
                    [
                        "ma20_ratio",
                    ],
                ),
                4,
            ),
    }


# ============================================================
# Backend
#
# 注意：
# analysis.json 目前仍可能帶有 legacy main_force_*。
# 這裡只做資料搬運，不重新命名、不重新計算。
# ============================================================

def build_backend(
    record: Dict[str, Any],
) -> Dict[str, Any]:

    short_term = get_short_term(
        record
    )

    backend: Dict[str, Any] = {}

    # 保留 analysis.json 原本的 short_term。
    # UI 層不得自行改寫其語意。
    if short_term:
        backend["short_term"] = dict(
            short_term
        )

    # 若 analysis 本身有 backend，也完整保留。
    raw_backend = record.get(
        "backend"
    )

    if isinstance(
        raw_backend,
        dict,
    ):
        for key, value in raw_backend.items():

            if key == "short_term":
                continue

            backend[key] = value

    # 指標是分析結果的展示層資料，
    # 不在此重新計算。
    backend["indicators"] = build_indicators(
        record
    )

    return backend


# ============================================================
# Strength
# ============================================================

def get_strength(
    record: Dict[str, Any],
) -> Optional[float]:

    short_term = get_short_term(
        record
    )

    value = first_value(
        record,
        [
            "strength",
            "score",
            "technical_score",
        ],
    )

    if value is None:
        value = first_value(
            short_term,
            [
                "strength",
                "score",
                "technical_score",
            ],
        )

    return rounded(
        value,
        2,
    )


# ============================================================
# Recommendation
#
# 不重新建立選股條件。
# 只使用 analysis.json 已經存在的 recommendation。
# ============================================================

def get_recommendation(
    record: Dict[str, Any],
) -> str:

    value = first_value(
        record,
        [
            "recommendation",
            "signal",
            "action",
        ],
    )

    if value is None:

        short_term = get_short_term(
            record
        )

        value = first_value(
            short_term,
            [
                "recommendation",
                "signal",
                "action",
            ],
        )

    if value is None:
        return ""

    return str(
        value
    ).strip()


# ============================================================
# Holding
# ============================================================

def empty_holding() -> Dict[str, Any]:
    return {
        "shares": None,
        "average_cost": None,
        "market_value": None,
        "profit": None,
        "return_pct": None,
    }


# ============================================================
# Stock record
# ============================================================

def build_stock_record(
    symbol: str,
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> Dict[str, Any]:

    price = get_price(
        analysis_record
    )

    change, change_pct = get_change(
        analysis_record
    )

    market = get_market(
        analysis_record,
        universe_record,
    )

    instrument_type = get_instrument_type(
        analysis_record,
        universe_record,
    )

    record = {
        "symbol": symbol,

        "name": get_stock_name(
            symbol,
            analysis_record,
            universe_record,
        ),

        "market": market,

        "instrument_type":
            instrument_type,

        "price": price,

        "change": change,

        "change_pct":
            change_pct,

        "strength":
            get_strength(
                analysis_record
            ),

        "recommendation":
            get_recommendation(
                analysis_record
            ),

        "backend":
            build_backend(
                analysis_record
            ),

        "holding":
            empty_holding(),
    }

    return record


# ============================================================
# Candidate normalization
# ============================================================

def extract_candidate_symbol(
    candidate: Any,
) -> str:

    if isinstance(
        candidate,
        str,
    ):
        return normalize_symbol(
            candidate
        )

    if not isinstance(
        candidate,
        dict,
    ):
        return ""

    value = first_value(
        candidate,
        [
            "symbol",
            "stock_id",
            "ticker",
            "code",
        ],
    )

    return normalize_symbol(
        value
    )


def candidate_score(
    candidate: Any,
    stock: Dict[str, Any],
) -> float:

    if isinstance(
        candidate,
        dict,
    ):
        value = first_value(
            candidate,
            [
                "score",
                "strength",
                "technical_score",
                "core_score",
            ],
        )

        parsed = number(
            value
        )

        if parsed is not None:
            return parsed

    parsed = number(
        stock.get(
            "strength"
        )
    )

    if parsed is not None:
        return parsed

    return float("-inf")


# ============================================================
# Today Picks
# ============================================================

def build_today_picks(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    raw_candidates = analysis.get(
        "short_term_candidates",
        [],
    )

    if raw_candidates is None:
        return []

    if not isinstance(
        raw_candidates,
        list,
    ):
        raise RuntimeError(
            "analysis.json short_term_candidates 必須是 array"
        )

    selected: List[
        Tuple[
            float,
            int,
            str,
        ]
    ] = []

    seen = set()

    for index, candidate in enumerate(
        raw_candidates
    ):

        symbol = extract_candidate_symbol(
            candidate
        )

        if not symbol:
            continue

        # Candidate 不得創造新股票。
        if symbol not in stocks:
            log(
                f"⚠️ short_term_candidates "
                f"忽略不存在於 analysis.stocks 的標的：{symbol}"
            )
            continue

        if symbol in seen:
            continue

        seen.add(
            symbol
        )

        selected.append(
            (
                candidate_score(
                    candidate,
                    stocks[symbol],
                ),
                index,
                symbol,
            )
        )

    # 保留 analysis 原本順序。
    #
    # 若 candidate 自身帶 score，才用 score 做穩定排序；
    # 沒有 score 時不自行建立新排名。
    scored = []
    unscored = []

    for item in selected:
        score, index, symbol = item

        if math.isfinite(score):
            scored.append(item)
        else:
            unscored.append(item)

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    unscored.sort(
        key=lambda item: item[1]
    )

    ordered = (
        scored
        + unscored
    )

    return [
        symbol
        for _, _, symbol in ordered
    ]


# ============================================================
# Top 10
#
# 不建立新的技術選股條件。
# 使用 analysis stocks 已有 strength。
# 沒有 strength 的股票排在後面。
# ============================================================

def build_top10(
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    ranked = []

    for symbol, stock in stocks.items():

        strength = number(
            stock.get(
                "strength"
            )
        )

        if strength is None:
            strength = float("-inf")

        ranked.append(
            (
                strength,
                symbol,
            )
        )

    ranked.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    return [
        symbol
        for _, symbol in ranked[:10]
    ]


# ============================================================
# ETF / Bond
# ============================================================

def build_by_instrument_type(
    stocks: Dict[str, Dict[str, Any]],
    instrument_type: str,
) -> List[str]:

    symbols = [
        symbol
        for symbol, stock in stocks.items()
        if stock.get(
            "instrument_type"
        ) == instrument_type
    ]

    return sorted(
        symbols
    )


# ============================================================
# Watchlist
#
# watchlist 不重新建立選股邏輯。
#
# 排除：
#     today_picks
#     top10
#     etf
#     bond
#
# 剩餘 analysis stocks 作為觀察清單。
# ============================================================

def build_watchlist(
    stocks: Dict[str, Dict[str, Any]],
    today_picks: List[str],
    top10: List[str],
    etf: List[str],
    bond: List[str],
) -> List[str]:

    excluded = set(
        today_picks
        + top10
        + etf
        + bond
    )

    return sorted(
        symbol
        for symbol in stocks
        if symbol not in excluded
    )


# ============================================================
# Summary
# ============================================================

def build_summary(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
    today_picks: List[str],
    top10: List[str],
    etf: List[str],
    bond: List[str],
    watchlist: List[str],
) -> Dict[str, Any]:

    analysis_summary = analysis.get(
        "summary"
    )

    if not isinstance(
        analysis_summary,
        dict,
    ):
        analysis_summary = {}

    return {
        "total_stocks":
            len(stocks),

        "today_picks":
            len(today_picks),

        "top10":
            len(top10),

        "etf":
            len(etf),

        "bond":
            len(bond),

        "watchlist":
            len(watchlist),

        "has_holdings":
            False,

        "holdings_profit":
            None,

        "analysis":
            dict(analysis_summary),
    }


# ============================================================
# Market
# ============================================================

def build_market(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    analysis_market = analysis.get(
        "market"
    )

    if isinstance(
        analysis_market,
        dict,
    ):
        market = dict(
            analysis_market
        )
    else:
        market = {}

    latest_date = first_value(
        analysis,
        [
            "latest_date",
            "trade_date",
            "data_date",
            "date",
        ],
    )

    if latest_date is not None:
        market.setdefault(
            "latest_date",
            str(latest_date),
        )

    market.setdefault(
        "name",
        "台股市場",
    )

    market.setdefault(
        "timezone",
        "Asia/Taipei",
    )

    return market


# ============================================================
# Tabs
# ============================================================

def build_tabs(
    stocks: Dict[str, Dict[str, Any]],
    analysis: Dict[str, Any],
) -> Dict[str, List[str]]:

    today_picks = build_today_picks(
        analysis,
        stocks,
    )

    top10 = build_top10(
        stocks
    )

    etf = build_by_instrument_type(
        stocks,
        "etf",
    )

    bond = build_by_instrument_type(
        stocks,
        "bond",
    )

    watchlist = build_watchlist(
        stocks,
        today_picks,
        top10,
        etf,
        bond,
    )

    return {
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
    }


# ============================================================
# UI validation
# ============================================================

REQUIRED_ROOT_FIELDS = {
    "schema_version",
    "generated_at",
    "status",
    "market",
    "summary",
    "tabs",
    "stocks",
}

REQUIRED_TAB_FIELDS = {
    "today_picks",
    "top10",
    "etf",
    "bond",
    "watchlist",
}

REQUIRED_STOCK_FIELDS = {
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

REQUIRED_HOLDING_FIELDS = {
    "shares",
    "average_cost",
    "market_value",
    "profit",
    "return_pct",
}


def validate_no_nonfinite(
    value: Any,
    path: str = "$",
) -> None:

    if isinstance(
        value,
        float,
    ):
        if not math.isfinite(value):
            raise RuntimeError(
                f"UI DATA 包含非有限數值：{path}"
            )
        return

    if isinstance(
        value,
        dict,
    ):
        for key, child in value.items():
            validate_no_nonfinite(
                child,
                f"{path}.{key}",
            )
        return

    if isinstance(
        value,
        list,
    ):
        for index, child in enumerate(value):
            validate_no_nonfinite(
                child,
                f"{path}[{index}]",
            )


def validate_ui_data(
    data: Dict[str, Any],
) -> None:

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "ui_data root 必須是 object"
        )

    missing_root = (
        REQUIRED_ROOT_FIELDS
        - set(data.keys())
    )

    if missing_root:
        raise RuntimeError(
            "ui_data 缺少 root 欄位："
            + ", ".join(
                sorted(missing_root)
            )
        )

    if data.get(
        "schema_version"
    ) != VERSION:
        raise RuntimeError(
            "ui_data schema_version 錯誤："
            f"{data.get('schema_version')}"
        )

    if data.get(
        "status"
    ) != "ok":
        raise RuntimeError(
            "ui_data status 必須為 ok"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):
        raise RuntimeError(
            "ui_data stocks 必須是 object"
        )

    tabs = data.get(
        "tabs"
    )

    if not isinstance(
        tabs,
        dict,
    ):
        raise RuntimeError(
            "ui_data tabs 必須是 object"
        )

    missing_tabs = (
        REQUIRED_TAB_FIELDS
        - set(tabs.keys())
    )

    if missing_tabs:
        raise RuntimeError(
            "ui_data 缺少 tabs 欄位："
            + ", ".join(
                sorted(missing_tabs)
            )
        )

    # --------------------------------------------------------
    # stocks
    # --------------------------------------------------------

    for symbol, stock in stocks.items():

        if not isinstance(
            stock,
            dict,
        ):
            raise RuntimeError(
                f"ui_data stocks[{symbol}] 必須是 object"
            )

        missing_stock = (
            REQUIRED_STOCK_FIELDS
            - set(stock.keys())
        )

        if missing_stock:
            raise RuntimeError(
                f"ui_data stocks[{symbol}] "
                "缺少欄位："
                + ", ".join(
                    sorted(missing_stock)
                )
            )

        normalized_symbol = normalize_symbol(
            stock.get(
                "symbol"
            )
        )

        if normalized_symbol != symbol:
            raise RuntimeError(
                "UI stock symbol mismatch："
                f"key={symbol}, "
                f"value={stock.get('symbol')}"
            )

        holding = stock.get(
            "holding"
        )

        if not isinstance(
            holding,
            dict,
        ):
            raise RuntimeError(
                f"ui_data stocks[{symbol}].holding "
                "必須是 object"
            )

        missing_holding = (
            REQUIRED_HOLDING_FIELDS
            - set(holding.keys())
        )

        if missing_holding:
            raise RuntimeError(
                f"ui_data stocks[{symbol}].holding "
                "缺少欄位："
                + ", ".join(
                    sorted(missing_holding)
                )
            )

    # --------------------------------------------------------
    # tabs
    # --------------------------------------------------------

    for tab_name in REQUIRED_TAB_FIELDS:

        items = tabs.get(
            tab_name
        )

        if not isinstance(
            items,
            list,
        ):
            raise RuntimeError(
                f"ui_data tabs.{tab_name} 必須是 array"
            )

        seen = set()

        for symbol in items:

            normalized = normalize_symbol(
                symbol
            )

            if not normalized:
                raise RuntimeError(
                    f"tabs.{tab_name} 出現空白 symbol"
                )

            if normalized != symbol:
                raise RuntimeError(
                    f"tabs.{tab_name} symbol 未正規化："
                    f"{symbol}"
                )

            if normalized not in stocks:
                raise RuntimeError(
                    f"tabs.{tab_name} "
                    f"包含不存在於 stocks 的 symbol："
                    f"{normalized}"
                )

            if normalized in seen:
                raise RuntimeError(
                    f"tabs.{tab_name} 出現重複 symbol："
                    f"{normalized}"
                )

            seen.add(
                normalized
            )

    # --------------------------------------------------------
    # Holdings contract
    # --------------------------------------------------------

    summary = data.get(
        "summary"
    )

    if not isinstance(
        summary,
        dict,
    ):
        raise RuntimeError(
            "ui_data summary 必須是 object"
        )

    if summary.get(
        "has_holdings"
    ) is not False:
        raise RuntimeError(
            "目前 UI-DATA backend 不得建立預設持倉"
        )

    if summary.get(
        "holdings_profit"
    ) is not None:
        raise RuntimeError(
            "目前 UI-DATA backend "
            "holdings_profit 必須為 null"
        )

    # --------------------------------------------------------
    # JSON number safety
    # --------------------------------------------------------

    validate_no_nonfinite(
        data
    )


# ============================================================
# Build
# ============================================================

def build_ui_data() -> Dict[str, Any]:

    section(
        "LOAD UNIVERSE"
    )

    universe = load_universe()

    section(
        "LOAD ANALYSIS"
    )

    analysis = load_analysis()

    analysis_stocks = normalize_analysis_stocks(
        analysis
    )

    log(
        f"Analysis：{len(analysis_stocks)} 檔"
    )

    # --------------------------------------------------------
    # 核心契約：
    #
    # analysis stocks N
    #       ↓
    # UI stocks N
    #
    # 不用 universe 補入 analysis 沒有的標的。
    # --------------------------------------------------------

    section(
        "BUILD UI STOCKS"
    )

    ui_stocks: Dict[
        str,
        Dict[str, Any],
    ] = {}

    missing_from_universe = 0

    for symbol, analysis_record in analysis_stocks.items():

        universe_record = universe.get(
            symbol,
            {},
        )

        if not universe_record:
            missing_from_universe += 1

        ui_stocks[symbol] = build_stock_record(
            symbol,
            analysis_record,
            universe_record,
        )

    if not ui_stocks:
        raise RuntimeError(
            "UI stocks 建立後為空"
        )

    log(
        f"UI stocks：{len(ui_stocks)} 檔"
    )

    if missing_from_universe:
        log(
            "⚠️ Analysis 中有 "
            f"{missing_from_universe} 檔 "
            "找不到 Universe 補充資料；"
            "保留 analysis 標的，不自行刪除。"
        )

    section(
        "BUILD TABS"
    )

    tabs = build_tabs(
        ui_stocks,
        analysis,
    )

    for name, items in tabs.items():
        log(
            f"{name:16s}: {len(items):4d}"
        )

    section(
        "BUILD UI DATA"
    )

    generated_at = datetime.now(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    ).replace(
        "+00:00",
        "Z",
    )

    ui_data: Dict[str, Any] = {
        "schema_version":
            VERSION,

        "generated_at":
            generated_at,

        "status":
            "ok",

        "market":
            build_market(
                analysis,
                ui_stocks,
            ),

        "summary":
            build_summary(
                analysis,
                ui_stocks,
                tabs["today_picks"],
                tabs["top10"],
                tabs["etf"],
                tabs["bond"],
                tabs["watchlist"],
            ),

        "tabs":
            tabs,

        "stocks":
            ui_stocks,
    }

    section(
        "FINAL VALIDATION"
    )

    validate_ui_data(
        ui_data
    )

    log(
        "✓ UI-DATA schema"
    )

    log(
        "✓ stocks count"
    )

    log(
        "✓ tabs references"
    )

    log(
        "✓ holding contract"
    )

    log(
        "✓ no NaN / Infinity"
    )

    return ui_data


# ============================================================
# Main
# ============================================================

def main() -> int:

    try:

        section(
            "BUILD UI DATA"
        )

        log(
            f"Version：{VERSION}"
        )

        log(
            f"Analysis：{ANALYSIS_FILE}"
        )

        log(
            f"Universe：{UNIVERSE_FILE}"
        )

        log(
            f"Output：{OUTPUT_FILE}"
        )

        ui_data = build_ui_data()

        section(
            "WRITE Data/ui_data.json"
        )

        atomic_write_json(
            OUTPUT_FILE,
            ui_data,
        )

        log(
            f"✓ 寫入：{OUTPUT_FILE}"
        )

        section(
            "READ-BACK VALIDATION"
        )

        written = load_json(
            OUTPUT_FILE
        )

        if not isinstance(
            written,
            dict,
        ):
            raise RuntimeError(
                "ui_data.json read-back root "
                "不是 object"
            )

        validate_ui_data(
            written
        )

        if len(
            written.get(
                "stocks",
                {},
            )
        ) != len(
            ui_data.get(
                "stocks",
                {},
            )
        ):
            raise RuntimeError(
                "ui_data.json read-back "
                "stocks count 不一致"
            )

        if written.get(
            "schema_version"
        ) != VERSION:
            raise RuntimeError(
                "ui_data.json read-back "
                "schema_version 不一致"
            )

        log(
            "✓ read-back validation"
        )

        section(
            "FINAL RESULT"
        )

        log(
            "✓ build_ui_data.py SUCCESS"
        )

        log(
            f"✓ UI stocks："
            f"{len(written['stocks'])}"
        )

        log(
            f"✓ Today picks："
            f"{len(written['tabs']['today_picks'])}"
        )

        log(
            f"✓ Top10："
            f"{len(written['tabs']['top10'])}"
        )

        log(
            f"✓ ETF："
            f"{len(written['tabs']['etf'])}"
        )

        log(
            f"✓ Bond："
            f"{len(written['tabs']['bond'])}"
        )

        log(
            f"✓ Watchlist："
            f"{len(written['tabs']['watchlist'])}"
        )

        return 0

    except KeyboardInterrupt:

        log(
            "❌ 使用者中斷"
        )

        return 130

    except Exception as exc:

        log(
            ""
        )

        log(
            "=" * 72
        )

        log(
            "BUILD UI DATA FAILED"
        )

        log(
            "=" * 72
        )

        log(
            f"❌ {exc}"
        )

        return 1


if __name__ == "__main__":
    sys.exit(
        main()
    )
