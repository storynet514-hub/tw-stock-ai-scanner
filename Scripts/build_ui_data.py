#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_ui_data.py

============================================================
用途
============================================================

將後台既有資料整理成：

Data/ui_data.json

供：

index.html

讀取。

============================================================
重要原則
============================================================

本程式不是資料抓取程式。

不負責：

- CMoney API
- 股價抓取
- RSI 計算
- MACD 計算
- KD 計算
- 成交量計算
- 主力買賣超抓取
- 重新定義選股條件

只負責：

後台資料
    ↓
UI 資料層
    ↓
Data/ui_data.json
    ↓
index.html

============================================================
前台固定結構
============================================================

status
market
summary
tabs
stocks

============================================================
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path


# ============================================================
# 基本設定
# ============================================================

VERSION = "UI-DATA-1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "ui_data.json"

# 可能存在的後台資料
PRICE_FILE = DATA_DIR / "prices.json"
STOCK_FILE = DATA_DIR / "stocks.json"
CHIP_FILE = DATA_DIR / "chip.json"

# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# JSON
# ============================================================

def load_json(path: Path):
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log(f"⚠️ 無法讀取 {path}: {exc}")
        return None


# ============================================================
# Safe helpers
# ============================================================

def is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def number(value):
    if is_number(value):
        return float(value)

    if value is None:
        return None

    try:
        text = str(value).replace(",", "").strip()

        if not text:
            return None

        result = float(text)

        if math.isfinite(result):
            return result

    except Exception:
        pass

    return None


def first_value(record, keys):
    if not isinstance(record, dict):
        return None

    for key in keys:
        if key in record and record[key] is not None:
            return record[key]

    return None


# ============================================================
# 股票名稱
# ============================================================

def get_stock_name(symbol, prices, stocks, chip):
    sources = [stocks, prices, chip]

    for source in sources:

        if not isinstance(source, dict):
            continue

        container = source.get("stocks")

        if isinstance(container, dict):
            record = container.get(symbol)

            if isinstance(record, dict):
                name = first_value(
                    record,
                    [
                        "name",
                        "stock_name",
                        "名稱",
                    ],
                )

                if name:
                    return str(name)

        record = source.get(symbol)

        if isinstance(record, dict):
            name = first_value(
                record,
                [
                    "name",
                    "stock_name",
                    "名稱",
                ],
            )

            if name:
                return str(name)

    return symbol


# ============================================================
# 取得股票資料容器
# ============================================================

def get_records(source):
    if not isinstance(source, dict):
        return {}

    stocks = source.get("stocks")

    if isinstance(stocks, dict):
        return stocks

    return source


# ============================================================
# 建立單一股票 UI record
# ============================================================

def build_stock_record(
    symbol,
    prices,
    stocks,
    chip,
):
    price_records = get_records(prices)
    stock_records = get_records(stocks)
    chip_records = get_records(chip)

    price_record = price_records.get(symbol, {})
    stock_record = stock_records.get(symbol, {})
    chip_record = chip_records.get(symbol, {})

    if not isinstance(price_record, dict):
        price_record = {}

    if not isinstance(stock_record, dict):
        stock_record = {}

    if not isinstance(chip_record, dict):
        chip_record = {}

    name = (
        first_value(
            stock_record,
            ["name", "stock_name", "名稱"],
        )
        or first_value(
            price_record,
            ["name", "stock_name", "名稱"],
        )
        or first_value(
            chip_record,
            ["name", "stock_name", "名稱"],
        )
        or symbol
    )

    price = first_value(
        price_record,
        [
            "price",
            "close",
            "latest_price",
            "last_price",
            "收盤價",
            "股價",
        ],
    )

    change = first_value(
        price_record,
        [
            "change",
            "price_change",
            "漲跌",
            "漲跌價差",
        ],
    )

    change_pct = first_value(
        price_record,
        [
            "change_pct",
            "change_percent",
            "漲跌幅",
        ],
    )

    # --------------------------------------------------------
    # 後台資料保留於資料層
    #
    # 主卡片不一定顯示。
    # index.html 決定呈現哪些欄位。
    # --------------------------------------------------------

    rsi = first_value(
        stock_record,
        ["rsi", "RSI"],
    )

    macd = first_value(
        stock_record,
        ["macd", "MACD"],
    )

    kd = first_value(
        stock_record,
        ["kd", "KD"],
    )

    ma5 = first_value(
        stock_record,
        ["ma5", "MA5"],
    )

    ma20 = first_value(
        stock_record,
        ["ma20", "MA20"],
    )

    volume = first_value(
        price_record,
        ["volume", "成交量"],
    )

    volume_avg5 = first_value(
        stock_record,
        [
            "volume_avg5",
            "avg_volume_5",
            "成交量5日均量",
        ],
    )

    # --------------------------------------------------------
    # 主力買賣超
    #
    # 只接收既有 chip.json。
    # 不在此重新計算。
    # --------------------------------------------------------

    main_force_1d = first_value(
        chip_record,
        ["main_force_1d"],
    )

    main_force_5d = first_value(
        chip_record,
        ["main_force_5d"],
    )

    main_force_10d = first_value(
        chip_record,
        ["main_force_10d"],
    )

    main_force_20d = first_value(
        chip_record,
        ["main_force_20d"],
    )

    # --------------------------------------------------------
    # 已整理好的前台欄位
    # --------------------------------------------------------

    strength = first_value(
        stock_record,
        [
            "strength",
            "signal_strength",
            "trend",
            "index_strength",
        ],
    )

    recommendation = first_value(
        stock_record,
        [
            "recommendation",
            "suggestion",
            "advice",
            "建議",
        ],
    )

    return {
        "symbol": str(symbol),
        "name": str(name),

        "price": number(price),
        "change": number(change),
        "change_pct": number(change_pct),

        "strength": strength,
        "recommendation": recommendation,

        # 後台資料
        "indicators": {
            "rsi": number(rsi),
            "macd": number(macd),
            "kd": number(kd),
            "ma5": number(ma5),
            "ma20": number(ma20),
            "volume": number(volume),
            "volume_avg5": number(volume_avg5),
        },

        "chip": {
            "main_force_1d": number(main_force_1d),
            "main_force_5d": number(main_force_5d),
            "main_force_10d": number(main_force_10d),
            "main_force_20d": number(main_force_20d),
        },

        # 我的清單資料由前端 localStorage 管理。
        "holding": {
            "shares": None,
            "average_cost": None,
            "market_value": None,
            "profit": None,
            "return_pct": None,
        },
    }


# ============================================================
# 取得最新交易日
# ============================================================

def get_latest_trading_date(prices):
    if not isinstance(prices, dict):
        return None

    candidates = [
        prices.get("latest_trading_date"),
        prices.get("data_date"),
        prices.get("trading_date"),
    ]

    status = prices.get("status")

    if isinstance(status, dict):
        candidates.extend(
            [
                status.get("latest_trading_date"),
                status.get("trading_date"),
            ]
        )

    for value in candidates:

        if value:
            return str(value)

    return None


# ============================================================
# 市場資料
# ============================================================

def build_market(prices):
    index = {
        "name": "加權指數",
        "value": None,
        "change": None,
        "change_pct": None,
    }

    if isinstance(prices, dict):

        market = prices.get("market")

        if isinstance(market, dict):

            source_index = market.get("index")

            if isinstance(source_index, dict):

                index["value"] = number(
                    first_value(
                        source_index,
                        [
                            "value",
                            "close",
                            "price",
                        ],
                    )
                )

                index["change"] = number(
                    first_value(
                        source_index,
                        [
                            "change",
                            "price_change",
                        ],
                    )
                )

                index["change_pct"] = number(
                    first_value(
                        source_index,
                        [
                            "change_pct",
                            "change_percent",
                        ],
                    )
                )

    return {
        "index": index,

        "sentiment": {
            "level": None,
            "description": None,
        },
    }


# ============================================================
# 市場狀態
# ============================================================

def build_status(prices):
    latest = get_latest_trading_date(prices)

    market_status = "closed"

    if isinstance(prices, dict):

        status = prices.get("status")

        if isinstance(status, dict):

            value = status.get(
                "market_status"
            )

            if value in {
                "open",
                "closed",
            }:

                market_status = value

    return {
        "market": "TW",
        "market_status": market_status,
        "latest_trading_date": latest,
        "updated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    }


# ============================================================
# 建立 UI
# ============================================================

def build_ui_data(
    prices,
    stocks,
    chip,
):
    stock_map = {}

    # --------------------------------------------------------
    # 先從三個資料來源取得所有股票代號
    # --------------------------------------------------------

    for source in [
        get_records(prices),
        get_records(stocks),
        get_records(chip),
    ]:

        if not isinstance(source, dict):
            continue

        for symbol in source.keys():

            symbol = str(symbol).strip()

            if symbol:
                stock_map[symbol] = True

    # --------------------------------------------------------
    # 建立股票資料
    # --------------------------------------------------------

    records = {}

    for symbol in sorted(stock_map.keys()):

        records[symbol] = build_stock_record(
            symbol,
            prices,
            stocks,
            chip,
        )

    # --------------------------------------------------------
    # 現階段不自行發明今日精選 / Top10
    #
    # 等既有分析資料提供整理後結果，
    # 再由資料層接入。
    # --------------------------------------------------------

    return {
        "status": build_status(prices),

        "market": build_market(prices),

        "summary": {
            "today_picks": 0,
            "holdings_profit": None,
            "has_holdings": False,
        },

        "tabs": {
            "today_picks": [],
            "top10": [],
            "etf": [],
            "bond": [],
            "watchlist": [],
        },

        "stocks": records,
    }


# ============================================================
# 驗證
# ============================================================

def validate(output):
    required = [
        "status",
        "market",
        "summary",
        "tabs",
        "stocks",
    ]

    for key in required:

        if key not in output:
            raise RuntimeError(
                f"ui_data.json 缺少必要欄位：{key}"
            )

    if not isinstance(
        output["stocks"],
        dict,
    ):
        raise RuntimeError(
            "stocks 必須為 object"
        )

    tabs = output["tabs"]

    if not isinstance(tabs, dict):
        raise RuntimeError(
            "tabs 必須為 object"
        )

    for key in [
        "today_picks",
        "top10",
        "etf",
        "bond",
        "watchlist",
    ]:

        if key not in tabs:
            raise RuntimeError(
                f"tabs 缺少：{key}"
            )

        if not isinstance(
            tabs[key],
            list,
        ):
            raise RuntimeError(
                f"tabs.{key} 必須為 array"
            )


# ============================================================
# Save
# ============================================================

def save(output):
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

    # 寫入後再次讀取
    with temp_file.open(
        "r",
        encoding="utf-8",
    ) as f:

        verify = json.load(f)

    validate(verify)

    temp_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# Main
# ============================================================

def main():

    section(
        f"台股 AI 選股系統 build_ui_data.py {VERSION}"
    )

    log(
        "建立 UI 資料橋接層"
    )

    log(
        f"輸出：{OUTPUT_FILE}"
    )

    # --------------------------------------------------------
    # 讀取既有後台資料
    # --------------------------------------------------------

    prices = load_json(PRICE_FILE)
    stocks = load_json(STOCK_FILE)
    chip = load_json(CHIP_FILE)

    log(
        f"prices.json："
        f"{'✓' if prices is not None else '⚠️ 不存在'}"
    )

    log(
        f"stocks.json："
        f"{'✓' if stocks is not None else '⚠️ 不存在'}"
    )

    log(
        f"chip.json："
        f"{'✓' if chip is not None else '⚠️ 不存在'}"
    )

    # --------------------------------------------------------
    # 建立
    # --------------------------------------------------------

    output = build_ui_data(
        prices,
        stocks,
        chip,
    )

    # --------------------------------------------------------
    # 驗證
    # --------------------------------------------------------

    validate(output)

    # --------------------------------------------------------
    # 儲存
    # --------------------------------------------------------

    save(output)

    log("")
    log("=" * 72)
    log("✓ UI 資料橋接建立完成")
    log("=" * 72)

    log(
        f"股票資料："
        f"{len(output['stocks'])}"
    )

    log(
        "今日精選："
        f"{len(output['tabs']['today_picks'])}"
    )

    log(
        "Top 10："
        f"{len(output['tabs']['top10'])}"
    )

    log(
        "ETF："
        f"{len(output['tabs']['etf'])}"
    )

    log(
        "債券："
        f"{len(output['tabs']['bond'])}"
    )

    log(
        "我的清單："
        f"{len(output['tabs']['watchlist'])}"
    )

    log(
        f"輸出檔案：{OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
