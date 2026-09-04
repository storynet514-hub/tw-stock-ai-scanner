#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台股 AI 選股系統
Scripts/fetch_market.py
MARKET ENVIRONMENT V2.1
============================================================
資料鏈
------------------------------------------------------------
TWSE 官方
    ├─ MI_INDEX
    ├─ MI_5MINS_HIST
    └─ T86
TPEx 官方
    └─ tpex_3insti_daily_trading
Data/prices/
    └─ fetch_prices.py V14.0 官方優先價格 shard
                ↓
Data/market.json
                ↓
Scripts/build_ui_data.py
                ↓
Data/ui_data.json
                ↓
index.html
核心原則
------------------------------------------------------------
1. Data/universe.json 是 Universe 唯一來源
2. 只使用 status == active
3. ETF / ETN / 權證 / REIT / 債券等非一般股票
   不納入市場 breadth
4. Data/prices/manifest.json 決定價格 shard
5. prices-v14.0 shard 必須全部合併
6. 同一股票跨 shard 必須合併，不得互相覆蓋
7. breadth 以 latest_trading_date 為基準
8. 股票資料使用 <= latest_trading_date 的最後有效交易日
9. 資料不足 = unavailable，不得當成 fail
10. unavailable 不計分
11. 有效條件 < 6 → 資料不足
12. JSON 不允許 NaN / Infinity
市場核心條件
------------------------------------------------------------
1. TAIEX > MA20
2. MA20 上升
3. TAIEX RSI14 > 50
4. 上漲家數 / 下跌家數 >= 1
5. 站上 MA20 比例 >= 50%
6. 市場成交量 / 20 日均量 >= 1
7. 外資買賣超 > 0
8. 投信買賣超 > 0
9. 20 日新高 / 新低 >= 1
10. TAIEX ATR14% <= 3%
市場風向
------------------------------------------------------------
8~10  → 偏多
5~7   → 震盪
0~4   → 偏弱
特殊規則
------------------------------------------------------------
上漲 > 0 且下跌 = 0
    → A/D ratio = +∞
    → 條件 pass
20日新高 > 0 且20日新低 = 0
    → New High / Low ratio = +∞
    → 條件 pass
但 JSON 中不可寫入 Infinity。
因此輸出：
    ratio = None
    ratio_status = "infinite"
    condition pass = True
"""
from __future__ import annotations
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import (
    date,
    datetime,
    time,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
)
import requests
# ============================================================
# PATH
# ============================================================
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "Data"
OUTPUT_FILE = DATA_DIR / "market.json"
UNIVERSE_FILE = DATA_DIR / "universe.json"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_FILE = PRICES_DIR / "manifest.json"
# ============================================================
# VERSION
# ============================================================
SCHEMA_VERSION = "market-v2.1"
PRICE_SCHEMA_VERSION = "prices-v14.0"
TAIWAN_TZ = timezone(
    timedelta(hours=8)
)
REQUEST_TIMEOUT = 30
MIN_INDEX_HISTORY = 21
MIN_STOCK_HISTORY_FOR_MA20 = 20
MIN_VOLUME_HISTORY = 21
# ============================================================
# OFFICIAL DATA SOURCES
# ============================================================
TWSE_INDEX_URL = (
    "https://openapi.twse.com.tw/"
    "v1/exchangeReport/MI_INDEX"
)
TWSE_INDEX_HISTORY_URL = (
    "https://openapi.twse.com.tw/"
    "v1/indicesReport/MI_5MINS_HIST"
)
TWSE_T86_URL = (
    "https://www.twse.com.tw/"
    "rwd/zh/fund/T86"
)
TPEX_INSTITUTIONAL_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/"
    "tpex_3insti_daily_trading"
)
# ============================================================
# CONFIG
# ============================================================
CONFIG = {
    "ma_period": 20,
    "rsi_period": 14,
    "atr_period": 14,
    "volume_ma_period": 20,
    "new_high_low_period": 20,
    "advance_decline_min_ratio": 1.00,
    "breadth_min_pct": 0.50,
    "volume_ratio_min": 1.00,
    "new_high_low_min_ratio": 1.00,
    "atr_pct_max": 0.03,
    "score_bullish": 8,
    "score_sideways": 5,
    "minimum_valid_conditions": 6,
}
# ============================================================
# HTTP
# ============================================================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; TW-Stock-AI-Scanner/2.1)"
    ),
    "Accept": (
        "application/json, "
        "text/plain, */*"
    ),
}
def log(message: str) -> None:
    print(
        message,
        flush=True,
    )
def request_json(
    url: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Any:
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    try:
        return response.json()
    except Exception as exc:
        preview = (
            response.text[:500]
            .replace("\n", " ")
        )
        raise RuntimeError(
            f"非 JSON 回應：{url}; "
            f"{preview}"
        ) from exc
# ============================================================
# NUMBER
# ============================================================
def number(
    value: Any,
) -> Optional[float]:
    if value is None:
        return None
    if isinstance(
        value,
        bool,
    ):
        return None
    if isinstance(
        value,
        (int, float),
    ):
        result = float(value)
        return (
            result
            if math.isfinite(result)
            else None
        )
    text = str(
        value
    ).strip()
    if not text:
        return None
    text = (
        text
        .replace(",", "")
        .replace("%", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )
    if text in {
        "",
        "-",
        "--",
        "---",
        "N/A",
        "NA",
        "null",
        "None",
    }:
        return None
    try:
        result = float(text)
        return (
            result
            if math.isfinite(result)
            else None
        )
    except Exception:
        return None
# ============================================================
# SYMBOL
# ============================================================
def normalize_symbol(
    value: Any,
) -> str:
    text = str(
        value or ""
    ).strip().upper()
    for suffix in (
        ".TW",
        ".TWO",
        ".TSE",
        ".OTC",
    ):
        if text.endswith(
            suffix
        ):