#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/build_ui_data.py
正式版 UI-DATA-2.0

============================================================
資料架構
============================================================

本程式是「UI 資料轉換層」。

後台：

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
重要架構邊界
============================================================

本程式：

    不抓 CMoney API
    不抓股價
    不計算 RSI
    不計算 MACD
    不計算 KD
    不重新計算成交量
    不重新計算主力籌碼
    不修改 analysis.json
    不修改 universe.json
    不修改 prices shard
    不修改 chip.json

只負責：

    後台分析資料
        ↓
    UI 所需資料格式
        ↓
    ui_data.json


============================================================
正式資料來源
============================================================

主要：

    Data/analysis.json

補充：

    Data/universe.json


============================================================
首頁固定架構
============================================================

status
market
summary
tabs
stocks


============================================================
固定五個分頁
============================================================

1. 今日精選
2. Top 10
3. ETF
4. 債券
5. 我的清單


============================================================
重要規則
============================================================

今日精選：

    analysis.json
    →
    short_term_candidates


Top 10：

    從 analysis.json 的股票分析結果
    依量化條件排序
    取前 10 檔

ETF：

    只顯示 universe 中被分類為 ETF 的股票


債券：

    只顯示 universe 中被分類為債券的商品


我的清單：

    初始一定為空

    不建立任何預設持倉。


============================================================
持倉
============================================================

前端使用者自行輸入：

    持有股數
    平均成本

本程式不偽造持倉。

因此：

    沒有持倉
        →
    holdings_profit = null
    has_holdings = false


============================================================
技術資料
============================================================

analysis.json 中的：

    RSI
    MACD
    KD
    MA5
    MA20
    MA60
    成交量
    成交量比較
    1D
    5D
    10D
    20D

全部保留在 stocks record 的 backend / indicators / chip
資料中。

index.html 是否顯示由前端決定。

主卡片不應公開這些後台技術細節。


============================================================
台股漲跌顏色
============================================================

本程式只提供數值：

    change
    change_pct

index.html 必須依台股規則：

    上漲 = 紅色
    下跌 = 綠色


============================================================
硬性驗證
============================================================

如果：

    analysis stocks > 0

但：

    ui stocks == 0

直接失敗。

避免：

    Action 顯示成功
    但首頁全部空白。


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
# Safe Number
# ============================================================

def number(value: Any) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        value = float(
            str(value).replace(",", "").strip()
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
            "universe.json 格式錯誤"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(items, list):
        raise RuntimeError(
            "universe.json items 格式錯誤"
        )

    result: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = normalize_symbol(
            item.get("code")
            or item.get("symbol")
            or item.get("ticker")
            or item.get("stock_id")
        )

        if not symbol:
            continue

        result[symbol] = dict(item)

        result[symbol]["symbol"] = symbol

    if not result:
        raise RuntimeError(
            "universe.json 沒有有效股票"
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
            "analysis.json 格式錯誤"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(stocks, dict):
        raise RuntimeError(
            "analysis.json stocks 格式錯誤"
        )

    if not stocks:
        raise RuntimeError(
            "analysis.json stocks 為空"
        )

    return data


# ============================================================
# 取得股票名稱
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
            "名稱",
        ],
    )

    if name:
        return str(name).strip()

    name = first_value(
        universe_record,
        [
            "name",
            "stock_name",
            "名稱",
        ],
    )

    if name:
        return str(name).strip()

    return symbol


# ============================================================
# 取得市場
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
        return str(market).strip()

    market = first_value(
        universe_record,
        [
            "market",
            "exchange",
            "market_type",
        ],
    )

    if market:
        return str(market).strip()

    return ""


# ============================================================
# 商品分類
# ============================================================

def get_instrument_type(
    analysis_record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> str:

    value = first_value(
        analysis_record,
        [
            "type",
            "instrument_type",
            "security_type",
            "category",
            "product_type",
        ],
    )

    if value is None:

        value = first_value(
            universe_record,
            [
                "type",
                "instrument_type",
                "security_type",
                "category",
                "product_type",
            ],
        )

    if value is None:
        return "stock"

    text = str(value).strip().lower()

    # ETF
    if any(
        token in text
        for token in (
            "etf",
            "基金",
            "指數型基金",
        )
    ):
        return "etf"

    # Bond
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
# 從 metrics 取得價格
# ============================================================

def get_price(
    record: Dict[str, Any]
) -> Optional[float]:

    metrics = record.get(
        "metrics"
    )

    if isinstance(metrics, dict):

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
                2
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
# 計算當日漲跌
# ============================================================

def get_change(
    record: Dict[str, Any]
) -> Tuple[
    Optional[float],
    Optional[float],
]:

    metrics = record.get(
        "metrics"
    )

    if not isinstance(metrics, dict):
        metrics = {}

    change_pct = first_value(
        metrics,
        [
            "change1_pct",
            "change_pct",
            "change_percent",
        ],
    )

    change_pct = rounded(
        change_pct,
        2
    )

    price = get_price(
        record
    )

    # --------------------------------------------------------
    # 如果後台已有 change
    # --------------------------------------------------------

    change = first_value(
        metrics,
        [
            "change",
            "price_change",
        ],
    )

    change = rounded(
        change,
        2
    )

    # --------------------------------------------------------
    # analysis V3.1 目前提供 change1_pct
    #
    # 沒有前收價格，因此不反推 change。
    # 避免 UI 產生假資料。
    # --------------------------------------------------------

    return (
        change,
        change_pct,
    )


# ============================================================
# 技術資料
# ============================================================

def build_indicators(
    record: Dict[str, Any]
) -> Dict[str, Any]:

    metrics = record.get(
        "metrics"
    )

    if not isinstance(metrics, dict):
        metrics = {}

    short_term = record.get(
        "short_term"
    )

    if not isinstance(short_term, dict):
        short_term = {}

    macd = short_term.get(
        "macd"
    )

    if not isinstance(macd, dict):
        macd = {}

    kd = short_term.get(
        "kd"
    )

    if not isinstance(kd, dict):
        kd = {}

    rsi = short_term.get(
        "rsi"
    )

    return {

        "rsi":
            rounded(
                rsi,
                2
            ),

        "macd":
            rounded(
                macd.get("macd"),
                4
            ),

        "macd_signal":
            rounded(
                macd.get("signal"),
                4
            ),

        "macd_histogram":
            rounded(
                macd.get("histogram"),
                4
            ),

        "macd_golden_cross":
            bool(
                macd.get(
                    "golden_cross",
                    False
                )
            ),

        "kd_k":
            rounded(
                kd.get("k"),
                2
            ),

        "kd_d":
            rounded(
                kd.get("d"),
                2
            ),

        "kd_golden_cross":
            bool(
                kd.get(
                    "golden_cross",
                    False
                )
            ),

        "ma5":
            rounded(
                metrics.get("ma5"),
                2
            ),

        "ma20":
            rounded(
                metrics.get("ma20"),
                2
            ),

        "ma60":
            rounded(
                metrics.get("ma60"),
                2
            ),

        "ma20_up":
            bool(
                metrics.get(
                    "ma20_up",
                    False
                )
            ),

        "ma20_ratio":
            rounded(
                metrics.get(
                    "ma20_ratio"
                ),
                4
            ),

        "bias20_pct":
            rounded(
                metrics.get(
                    "bias20_pct"
                ),
                2
            ),

        "high60":
            rounded(
                metrics.get(
                    "high60"
                ),
                2
            ),

        "low60":
            rounded(
                metrics.get(
                    "low60"
                ),
                2
            ),

        "position60_pct":
            rounded(
                metrics.get(
                    "position60_pct"
                ),
                2
            ),

        "change1_pct":
            rounded(
                metrics.get(
                    "change1_pct"
                ),
                2
            ),

        "change5_pct":
            rounded(
                metrics.get(
                    "change5_pct"
                ),
                2
            ),

        "change20_pct":
            rounded(
                metrics.get(
                    "change20_pct"
                ),
                2
            ),

        "volume":
            rounded(
                metrics.get(
                    "volume"
                ),
                2
            ),

        "volume5_previous_avg":
            rounded(
                metrics.get(
                    "volume5_previous_avg"
                ),
                2
            ),

        "volume_ratio":
            rounded(
                metrics.get(
                    "volume_ratio_vs_previous_5"
                ),
                4
            ),

        "volume_signal":
            metrics.get(
                "volume_signal"
            ),
    }


# ============================================================
# 籌碼
# ============================================================

def build_chip(
    record: Dict[str, Any]
) -> Dict[str, Any]:

    chip = record.get(
        "chip"
    )

    if not isinstance(chip, dict):
        chip = {}

    return {

        "main_force_1d":
            rounded(
                chip.get(
                    "main_force_1d"
                ),
                2
            ),

        "main_force_5d":
            rounded(
                chip.get(
                    "main_force_5d"
                ),
                2
            ),

        "main_force_10d":
            rounded(
                chip.get(
                    "main_force_10d"
                ),
                2
            ),

        "main_force_20d":
            rounded(
                chip.get(
                    "main_force_20d"
                ),
                2
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
                    True
                )
            ),
    }


# ============================================================
# 建議
# ============================================================

def build_recommendation(
    record: Dict[str, Any]
) -> str:

    dca = record.get(
        "dca"
    )

    if not isinstance(dca, dict):
        dca = {}

    action = dca.get(
        "action"
    )

    short_term = record.get(
        "short_term"
    )

    if not isinstance(short_term, dict):
        short_term = {}

    qualified = bool(
        short_term.get(
            "qualified",
            False
        )
    )

    # --------------------------------------------------------
    # 今日精選
    # --------------------------------------------------------

    if qualified:
        return "偏多，可分批"

    # --------------------------------------------------------
    # DCA 已整理結果
    # --------------------------------------------------------

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

    if action in mapping:
        return mapping[action]

    return "觀察"


# ============================================================
# 指數強度
# ============================================================

def build_strength(
    record: Dict[str, Any]
) -> str:

    short_term = record.get(
        "short_term"
    )

    if not isinstance(short_term, dict):
        short_term = {}

    dca = record.get(
        "dca"
    )

    if not isinstance(dca, dict):
        dca = {}

    qualified = bool(
        short_term.get(
            "qualified",
            False
        )
    )

    score = number(
        short_term.get(
            "score"
        )
    )

    # --------------------------------------------------------
    # 不把分數輸出給前台。
    # 只轉換成文字強度。
    # --------------------------------------------------------

    if qualified:
        return "強勢"

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
# 建立單一股票
# ============================================================

def build_stock(
    symbol: str,
    record: Dict[str, Any],
    universe_record: Dict[str, Any],
) -> Dict[str, Any]:

    if not isinstance(record, dict):
        record = {}

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

    if not isinstance(dca, dict):
        dca = {}

    short_term = record.get(
        "short_term"
    )

    if not isinstance(short_term, dict):
        short_term = {}

    chip = build_chip(
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

        # ----------------------------------------------------
        # 後台資料
        # ----------------------------------------------------

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
                    chip,
            },

        # ----------------------------------------------------
        # 前端持倉資料
        #
        # 初始一定為空。
        # ----------------------------------------------------

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
# 最新交易日
# ============================================================

def get_latest_trading_date(
    analysis: Dict[str, Any],
    stocks: Dict[str, Dict[str, Any]],
) -> Optional[str]:

    # --------------------------------------------------------
    # 優先從 analysis 的明確欄位取得
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 再從 stocks.latest_date 找最大日期
    # --------------------------------------------------------

    dates: List[str] = []

    for record in stocks.values():

        if not isinstance(record, dict):
            continue

        value = record.get(
            "latest_date"
        )

        if value:
            dates.append(
                str(value)
            )

    if dates:

        return max(
            dates
        )

    return None


# ============================================================
# 市場狀態
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
# 市場指數
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

    index = {

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
                2
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
                2
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
                2
            ),
    }

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
            index,

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
# 今日精選
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
        return []

    result = []

    for symbol in candidates:

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        if symbol in stocks:
            result.append(
                symbol
            )

    return result


# ============================================================
# Top 10 排序
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

    qualified = 1 if short_term.get(
        "qualified",
        False
    ) else 0

    score = number(
        short_term.get(
            "score"
        )
    )

    if score is None:
        score = 0

    volume_ratio = number(
        short_term.get(
            "volume_ratio"
        )
    )

    if volume_ratio is None:
        volume_ratio = 0

    ma20_up = 1 if (
        short_term.get(
            "conditions",
            {}
        ).get(
            "ma20_up",
            False
        )
    ) else 0

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

    stock_symbols = []

    for symbol, record in stocks.items():

        if not isinstance(record, dict):
            continue

        if record.get(
            "instrument_type"
        ) != "stock":
            continue

        stock_symbols.append(
            symbol
        )

    stock_symbols.sort(
        key=lambda symbol:
            top10_sort_key(
                symbol,
                stocks[symbol],
            ),
        reverse=True,
    )

    return stock_symbols[:10]


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
            if isinstance(record, dict)
            and record.get(
                "instrument_type"
            ) == "etf"
        ]
    )


# ============================================================
# 債券
# ============================================================

def build_bond(
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    return sorted(
        [
            symbol
            for symbol, record
            in stocks.items()
            if isinstance(record, dict)
            and record.get(
                "instrument_type"
            ) == "bond"
        ]
    )


# ============================================================
# 今日精選只允許股票
# ============================================================

def filter_stock_symbols(
    symbols: List[str],
    stocks: Dict[str, Dict[str, Any]],
) -> List[str]:

    result = []

    for symbol in symbols:

        record = stocks.get(
            symbol
        )

        if not isinstance(
            record,
            dict,
        ):
            continue

        if record.get(
            "instrument_type"
        ) != "stock":
            continue

        result.append(
            symbol
        )

    return result


# ============================================================
# Summary
# ============================================================

def build_summary(
    today_picks: List[str],
) -> Dict[str, Any]:

    # --------------------------------------------------------
    # 沒有持倉就不能假造 +0 元
    # --------------------------------------------------------

    return {

        "today_picks":
            len(today_picks),

        "holdings_profit":
            None,

        "has_holdings":
            False,
    }


# ============================================================
# 完整 UI Data
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
    # analysis.json 才是股票分析資料的唯一來源
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
            "analysis.json 有資料，但沒有成功建立任何 UI 股票資料"
        )

    # --------------------------------------------------------
    # 今日精選
    # --------------------------------------------------------

    today_picks = build_today_picks(
        analysis,
        stocks,
    )

    today_picks = filter_stock_symbols(
        today_picks,
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
    # 我的清單
    #
    # 永遠不建立預設股票。
    # --------------------------------------------------------

    watchlist: List[str] = []

    output = {

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

    return output


# ============================================================
# 驗證 UI Data
# ============================================================

def validate_ui_data(
    output: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    section(
        "UI DATA 硬性驗證"
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
            "ui_data.json 缺少欄位："
            + ", ".join(
                sorted(missing)
            )
        )

    stocks = output.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            "ui_data.json stocks 必須是 object"
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

    # --------------------------------------------------------
    # 最重要的硬性檢查
    # --------------------------------------------------------

    if (
        analysis_count > 0
        and ui_count == 0
    ):

        raise RuntimeError(
            "❌ UI DATA 驗證失敗："
            "analysis.json 有股票，但 ui_data.json stocks 為空"
        )

    if ui_count == 0:

        raise RuntimeError(
            "❌ UI DATA 驗證失敗："
            "ui_data.json 沒有任何股票"
        )

    # --------------------------------------------------------
    # tabs
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
    # 今日精選數量必須一致
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

    today_picks_count = len(
        tabs[
            "today_picks"
        ]
    )

    summary_count = summary.get(
        "today_picks"
    )

    if summary_count != today_picks_count:

        raise RuntimeError(
            "summary.today_picks 與 "
            "tabs.today_picks 數量不一致"
        )

    # --------------------------------------------------------
    # 沒有持倉時不能出現假盈虧
    # --------------------------------------------------------

    if (
        summary.get(
            "has_holdings"
        ) is False
        and
        summary.get(
            "holdings_profit"
        ) is not None
    ):

        raise RuntimeError(
            "❌ 沒有持倉時不得產生 holdings_profit"
        )

    # --------------------------------------------------------
    # 我的清單初始必須為空
    # --------------------------------------------------------

    if tabs[
        "watchlist"
    ]:

        raise RuntimeError(
            "❌ watchlist 不允許建立預設股票"
        )

    # --------------------------------------------------------
    # 股票 record 必要欄位
    # --------------------------------------------------------

    required_stock_keys = {

        "symbol",
        "name",
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
                f"{symbol} UI record 格式錯誤"
            )

        missing_stock = (
            required_stock_keys
            - set(record.keys())
        )

        if missing_stock:

            raise RuntimeError(
                f"{symbol} 缺少 UI 欄位："
                + ", ".join(
                    sorted(missing_stock)
                )
            )

        holding = record.get(
            "holding"
        )

        if not isinstance(
            holding,
            dict,
        ):

            raise RuntimeError(
                f"{symbol} holding 格式錯誤"
            )

    log(
        "✓ UI DATA 結構驗證通過"
    )

    log(
        f"✓ 股票資料：{ui_count} 檔"
    )

    log(
        f"✓ 今日精選：{today_picks_count} 檔"
    )

    log(
        f"✓ Top 10："
        f"{len(tabs['top10'])} 檔"
    )

    log(
        f"✓ ETF："
        f"{len(tabs['etf'])} 檔"
    )

    log(
        f"✓ 債券："
        f"{len(tabs['bond'])} 檔"
    )

    log(
        "✓ 我的清單：初始空白"
    )


# ============================================================
# 額外分析一致性驗證
# ============================================================

def validate_analysis_connection(
    output: Dict[str, Any],
    analysis: Dict[str, Any],
) -> None:

    section(
        "Analysis → UI 對接驗證"
    )

    analysis_candidates = analysis.get(
        "short_term_candidates",
        [],
    )

    if not isinstance(
        analysis_candidates,
        list,
    ):

        raise RuntimeError(
            "analysis.json short_term_candidates 格式錯誤"
        )

    ui_candidates = output[
        "tabs"
    ][
        "today_picks"
    ]

    normalized_analysis = []

    for symbol in analysis_candidates:

        symbol = normalize_symbol(
            symbol
        )

        if symbol:
            normalized_analysis.append(
                symbol
            )

    normalized_analysis = [
        symbol
        for symbol in normalized_analysis
        if symbol in output[
            "stocks"
        ]
        and output[
            "stocks"
        ][symbol].get(
            "instrument_type"
        ) == "stock"
    ]

    if normalized_analysis != ui_candidates:

        raise RuntimeError(
            "❌ 今日精選對接錯誤："
            "analysis.json short_term_candidates "
            "與 ui_data.json tabs.today_picks 不一致"
        )

    log(
        "✓ 今日精選來源確認："
        "analysis.json → short_term_candidates"
    )

    log(
        f"✓ 今日精選："
        f"{len(ui_candidates)} 檔"
    )


# ============================================================
# 儲存
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

    # --------------------------------------------------------
    # 寫入後重新讀取
    # --------------------------------------------------------

    try:

        with temp_file.open(
            "r",
            encoding="utf-8",
        ) as f:

            verify = json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"ui_data.json 寫入驗證失敗：{exc}"
        ) from exc

    if not isinstance(
        verify,
        dict,
    ):

        raise RuntimeError(
            "ui_data.json 寫入後不是 object"
        )

    if not isinstance(
        verify.get(
            "stocks"
        ),
        dict,
    ):

        raise RuntimeError(
            "ui_data.json stocks 寫入後格式錯誤"
        )

    if len(
        verify[
            "stocks"
        ]
    ) == 0:

        raise RuntimeError(
            "❌ 拒絕寫入空的 stocks"
        )

    temp_file.replace(
        OUTPUT_FILE
    )

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
        f"版本："
        f"{VERSION}"
    )

    log(
        f"市場："
        f"{status.get('market')}"
    )

    log(
        f"市場狀態："
        f"{status.get('market_status')}"
    )

    log(
        f"最新交易日："
        f"{status.get('latest_trading_date')}"
    )

    log(
        ""
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

    log(
        ""
    )

    log(
        "持倉狀態："
        "尚未建立持倉"
    )

    log(
        ""
    )

    log(
        "資料流："
    )

    log(
        "analysis.json"
        " → "
        "build_ui_data.py"
        " → "
        "ui_data.json"
        " → "
        "index.html"
    )

    log(
        ""
    )

    log(
        "✓ build_ui_data.py 完成"
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    section(
        f"台股 AI 選股系統 "
        f"build_ui_data.py {VERSION}"
    )

    try:

        # ----------------------------------------------------
        # 1. 讀 Universe
        # ----------------------------------------------------

        universe = load_universe()

        # ----------------------------------------------------
        # 2. 讀 Analysis
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
        # 3. 建立 UI data
        # ----------------------------------------------------

        output = build_ui_data(
            analysis,
            universe,
        )

        # ----------------------------------------------------
        # 4. 驗證
        # ----------------------------------------------------

        validate_ui_data(
            output,
            analysis,
        )

        validate_analysis_connection(
            output,
            analysis,
        )

        # ----------------------------------------------------
        # 5. 寫檔
        # ----------------------------------------------------

        save_ui_data(
            output
        )

        # ----------------------------------------------------
        # 6. 最終 summary
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


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )