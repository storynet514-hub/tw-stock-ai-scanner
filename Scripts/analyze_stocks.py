#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 市場分析引擎
analyze_stocks.py V1.0

用途：
1. 讀取 Data/universe.json
2. 讀取 Data/prices.json
3. 讀取 Data/chip.json（如果存在）
4. 進行後端分析
5. 產生 Data/ui_data.json

重要：
本程式的技術指標屬於後端資料。
不直接把 RSI / MACD / KD / MA / 籌碼等技術細節輸出給前端。

前端 index.html 只讀取整理後的投資資訊。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any


# ============================================================
# 路徑
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_FILE = DATA_DIR / "prices.json"
CHIP_FILE = DATA_DIR / "chip.json"
UI_DATA_FILE = DATA_DIR / "ui_data.json"


# ============================================================
# 基本工具
# ============================================================

def load_json(path: Path, default: Any = None) -> Any:
    """
    安全讀取 JSON。
    """

    if not path.exists():
        print(f"⚠️ 找不到檔案：{path}")
        return default

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"❌ JSON 讀取失敗：{path}")
        print(f"   原因：{e}")
        return default


def save_json(path: Path, data: Any) -> None:
    """
    原子方式寫入 JSON。
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    temp_file = path.with_suffix(path.suffix + ".tmp")

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, path)


def safe_float(value: Any) -> float | None:
    """
    將資料安全轉成 float。
    """

    if value is None:
        return None

    try:
        value = float(value)

        if not math.isfinite(value):
            return None

        return value

    except (ValueError, TypeError):
        return None


def now_taiwan() -> str:
    """
    回傳目前時間。

    GitHub Actions 通常使用 UTC，
    因此這裡使用 +08:00。
    """

    from datetime import timezone, timedelta

    tz = timezone(timedelta(hours=8))

    return datetime.now(tz).isoformat()


# ============================================================
# 資料結構標準化
# ============================================================

def normalize_stock_code(code: Any) -> str:
    """
    股票代號標準化。
    """

    if code is None:
        return ""

    return str(code).strip().upper()


def extract_stock_list(universe: Any) -> list[dict]:
    """
    嘗試從不同 Universe 結構取得股票清單。

    支援：

    [
        {...},
        {...}
    ]

    或：

    {
        "stocks": [...]
    }

    或：

    {
        "universe": [...]
    }
    """

    if isinstance(universe, list):
        return universe

    if not isinstance(universe, dict):
        return []

    for key in (
        "stocks",
        "universe",
        "data",
        "items"
    ):
        value = universe.get(key)

        if isinstance(value, list):
            return value

    return []


# ============================================================
# 價格資料解析
# ============================================================

def extract_price_records(prices: Any) -> dict[str, dict]:
    """
    將 prices.json 轉成：

    {
        "1102.TW": {...},
        "2330.TW": {...}
    }

    此函式刻意保持寬鬆，
    避免因不同版本 prices.json 結構而整個系統失效。
    """

    result: dict[str, dict] = {}

    if isinstance(prices, dict):

        # ----------------------------------------------------
        # 形式：
        # {
        #   "1102.TW": {...},
        #   "2330.TW": {...}
        # }
        # ----------------------------------------------------

        for key, value in prices.items():

            code = normalize_stock_code(key)

            if isinstance(value, dict):
                result[code] = value

        # ----------------------------------------------------
        # 形式：
        # {
        #   "stocks": {
        #       "1102.TW": {...}
        #   }
        # }
        # ----------------------------------------------------

        stocks = prices.get("stocks")

        if isinstance(stocks, dict):

            for key, value in stocks.items():

                code = normalize_stock_code(key)

                if isinstance(value, dict):
                    result[code] = value

    elif isinstance(prices, list):

        for item in prices:

            if not isinstance(item, dict):
                continue

            code = (
                item.get("code")
                or item.get("symbol")
                or item.get("ticker")
            )

            code = normalize_stock_code(code)

            if code:
                result[code] = item

    return result


# ============================================================
# 最新價格
# ============================================================

def get_latest_price(record: dict) -> float | None:
    """
    嘗試取得最新股價。
    """

    possible_fields = (
        "price",
        "close",
        "latest_price",
        "current_price",
        "last"
    )

    for field in possible_fields:

        value = safe_float(record.get(field))

        if value is not None:
            return value

    return None


def get_change_pct(record: dict) -> float | None:
    """
    嘗試取得漲跌幅。
    """

    possible_fields = (
        "change_pct",
        "changePercent",
        "pct_change",
        "return_pct"
    )

    for field in possible_fields:

        value = safe_float(record.get(field))

        if value is not None:
            return value

    return None


# ============================================================
# 市場方向
# ============================================================

def determine_market_sentiment(
    stocks: list[dict]
) -> tuple[str, str]:

    if not stocks:
        return (
            "震盪",
            "市場資料不足"
        )

    changes = []

    for stock in stocks:

        change = safe_float(
            stock.get("change_pct")
        )

        if change is not None:
            changes.append(change)

    if not changes:
        return (
            "震盪",
            "市場資料不足"
        )

    average_change = sum(changes) / len(changes)

    if average_change >= 1.0:

        return (
            "偏多",
            "市場氣氛偏強"
        )

    if average_change <= -1.0:

        return (
            "偏弱",
            "市場氣氛偏弱"
        )

    return (
        "震盪",
        "多空力量接近"
    )


# ============================================================
# 個股後端分析
# ============================================================

def analyze_stock(
    code: str,
    name: str,
    price_record: dict,
    chip_record: dict | None
) -> dict:

    price = get_latest_price(price_record)

    change_pct = get_change_pct(price_record)

    # --------------------------------------------------------
    # 這裡保留後端分析接口。
    #
    # 下一版會加入：
    #
    # MACD
    # KD
    # RSI
    # 成交量
    # MA20
    # 主力買賣超
    # 資券
    # 當沖率
    #
    # 這些資料不直接輸出到 UI。
    # --------------------------------------------------------

    if change_pct is None:
        strength = "中性"
        recommendation = "等待資料"

    elif change_pct >= 3:
        strength = "強勢"
        recommendation = "偏多，可分批"

    elif change_pct >= 0:
        strength = "中性"
        recommendation = "等待確認"

    else:
        strength = "弱勢"
        recommendation = "暫停操作"

    return {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": change_pct,
        "strength": strength,
        "recommendation": recommendation
    }


# ============================================================
# Universe 建立
# ============================================================

def build_stock_names(universe: Any) -> dict[str, str]:

    stock_list = extract_stock_list(universe)

    result: dict[str, str] = {}

    for item in stock_list:

        if not isinstance(item, dict):
            continue

        code = (
            item.get("code")
            or item.get("symbol")
            or item.get("ticker")
        )

        name = (
            item.get("name")
            or item.get("stock_name")
            or item.get("short_name")
            or ""
        )

        code = normalize_stock_code(code)

        if code:
            result[code] = str(name)

    return result


# ============================================================
# 主程式
# ============================================================

def main() -> None:

    print("=" * 64)
    print("台股 AI 市場分析引擎")
    print("analyze_stocks.py V1.0")
    print("=" * 64)

    print()

    # --------------------------------------------------------
    # 讀取 Universe
    # --------------------------------------------------------

    print("🔎 讀取 Universe...")

    universe = load_json(
        UNIVERSE_FILE,
        {}
    )

    stock_names = build_stock_names(universe)

    print(
        f"   Universe 股票名稱：{len(stock_names)}"
    )

    # --------------------------------------------------------
    # 讀取價格
    # --------------------------------------------------------

    print()
    print("🔎 讀取價格資料...")

    prices_raw = load_json(
        PRICES_FILE,
        {}
    )

    prices = extract_price_records(
        prices_raw
    )

    print(
        f"   價格資料：{len(prices)}"
    )

    # --------------------------------------------------------
    # 讀取籌碼
    # --------------------------------------------------------

    print()
    print("🔎 讀取籌碼資料...")

    chip_raw = load_json(
        CHIP_FILE,
        {}
    )

    chip = extract_price_records(
        chip_raw
    )

    print(
        f"   籌碼資料：{len(chip)}"
    )

    # --------------------------------------------------------
    # 建立分析結果
    # --------------------------------------------------------

    print()
    print("🔎 建立個股分析結果...")

    analyzed_stocks = []

    for code, record in prices.items():

        if not isinstance(record, dict):
            continue

        name = stock_names.get(
            code,
            record.get("name", "")
        )

        result = analyze_stock(
            code=code,
            name=name,
            price_record=record,
            chip_record=chip.get(code)
        )

        analyzed_stocks.append(result)

    print(
        f"   已分析股票：{len(analyzed_stocks)}"
    )

    # --------------------------------------------------------
    # 今日精選
    #
    # 現階段先以後端分析結果排序。
    # 真正的多因子選股條件會在下一版加入。
    # --------------------------------------------------------

    today_picks = [
        stock
        for stock in analyzed_stocks
        if stock.get("strength") == "強勢"
    ]

    today_picks.sort(
        key=lambda x: (
            x.get("change_pct")
            if x.get("change_pct") is not None
            else -999
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # Top 10
    # --------------------------------------------------------

    top10 = sorted(
        analyzed_stocks,
        key=lambda x: (
            x.get("change_pct")
            if x.get("change_pct") is not None
            else -999
        ),
        reverse=True
    )[:10]

    # --------------------------------------------------------
    # 市場風向
    # --------------------------------------------------------

    sentiment_level, sentiment_description = (
        determine_market_sentiment(
            analyzed_stocks
        )
    )

    # --------------------------------------------------------
    # 最新交易日
    # --------------------------------------------------------

    latest_trading_date = None

    possible_dates = []

    for record in prices.values():

        if not isinstance(record, dict):
            continue

        for key in (
            "date",
            "trade_date",
            "trading_date",
            "latest_date"
        ):

            value = record.get(key)

            if value:
                possible_dates.append(
                    str(value)
                )

    if possible_dates:
        latest_trading_date = max(
            possible_dates
        )

    # --------------------------------------------------------
    # 建立 UI Data
    # --------------------------------------------------------

    ui_data = {

        "status": {
            "market": "TW",
            "market_status": "closed",
            "latest_trading_date": latest_trading_date,
            "updated_at": now_taiwan()
        },

        "market": {

            "index": {
                "name": "加權指數",
                "value": None,
                "change": None,
                "change_pct": None
            },

            "sentiment": {
                "level": sentiment_level,
                "description": sentiment_description
            }
        },

        "summary": {

            "today_picks": len(today_picks),

            # 尚未建立使用者持倉資料時，
            # 絕對不能假造 +0 元。
            "holdings_profit": None,

            "has_holdings": False
        },

        "tabs": {

            "today_picks": today_picks,

            "top10": top10,

            "etf": [],

            "bond": [],

            "watchlist": []
        },

        "stocks": {
            stock["code"]: stock
            for stock in analyzed_stocks
        }
    }

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    print()
    print("💾 寫入 ui_data.json...")

    save_json(
        UI_DATA_FILE,
        ui_data
    )

    print()
    print("=" * 64)
    print("✅ analyze_stocks.py V1.0 完成")
    print("=" * 64)
    print(
        f"輸出：{UI_DATA_FILE}"
    )
    print(
        f"股票數：{len(analyzed_stocks)}"
    )
    print(
        f"今日精選：{len(today_picks)}"
    )
    print(
        f"Top 10：{len(top10)}"
    )
    print(
        f"市場風向：{sentiment_level}"
    )
    print("=" * 64)


if __name__ == "__main__":
    main()
