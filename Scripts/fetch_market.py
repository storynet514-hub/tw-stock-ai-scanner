#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統 - fetch_market.py
============================================================

責任：
1. 從 TWSE 官方資料取得加權指數
2. 找到最近有效交易日
3. 產生 Data/market.json
4. 提供 index.html 所需的市場資料

資料流：
TWSE 官方
    ↓
fetch_market.py
    ↓
Data/market.json
    ↓
build_ui_data.py
    ↓
Data/ui_data.json
    ↓
index.html

本程式不：
- 修改 universe.json
- 修改 prices
- 修改 chip.json
- 修改 analysis.json
- 在前端計算市場資料
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests


# ============================================================
# Path
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
OUTPUT_FILE = DATA_DIR / "market.json"


# ============================================================
# Contract
# ============================================================

SCHEMA_VERSION = "market-v1.0"

TWSE_URL = (
    "https://openapi.twse.com.tw/v1/"
    "exchangeReport/MI_INDEX"
)

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; TW-Stock-AI-Scanner/1.0)"
    ),
    "Accept": (
        "application/json, "
        "text/plain, */*"
    ),
}


# ============================================================
# Logging
# ============================================================

def section(title: str) -> None:
    print("")
    print("=" * 60)
    print(title)
    print("=" * 60)


def log(message: str) -> None:
    print(message)


# ============================================================
# Number
# ============================================================

def number(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    try:
        text = str(value).strip()

        if not text:
            return None

        text = (
            text
            .replace(",", "")
            .replace("%", "")
            .strip()
        )

        value = float(text)

        if not math.isfinite(value):
            return None

        return value

    except Exception:
        return None


# ============================================================
# ROC Date
# ============================================================

def roc_date(
    value: date,
) -> str:

    return (
        f"{value.year - 1911:03d}/"
        f"{value.month:02d}/"
        f"{value.day:02d}"
    )


# ============================================================
# Parse TWSE MI_INDEX
# ============================================================

def parse_index_response(
    payload: Any,
) -> Optional[Dict[str, Any]]:

    if not isinstance(
        payload,
        dict,
    ):
        return None

    tables = payload.get(
        "tables"
    )

    if not isinstance(
        tables,
        list,
    ):
        return None

    for table in tables:

        if not isinstance(
            table,
            dict,
        ):
            continue

        fields = table.get(
            "fields"
        )

        rows = table.get(
            "data"
        )

        if not isinstance(
            fields,
            list,
        ):
            continue

        if not isinstance(
            rows,
            list,
        ):
            continue

        field_map = {
            str(value).strip(): index
            for index, value
            in enumerate(fields)
        }

        index_name_pos = (
            field_map.get("指數")
        )

        close_pos = (
            field_map.get("收盤指數")
        )

        change_pos = (
            field_map.get("漲跌")
        )

        change_pct_pos = (
            field_map.get("漲跌百分比")
        )

        for row in rows:

            if not isinstance(
                row,
                list,
            ):
                continue

            if index_name_pos is None:
                continue

            if index_name_pos >= len(row):
                continue

            index_name = str(
                row[index_name_pos]
            ).strip()

            if (
                "發行量加權股價指數"
                not in index_name
                and "TAIEX"
                not in index_name.upper()
            ):
                continue

            # ------------------------------------------------
            # Close
            # ------------------------------------------------

            close = None

            if (
                close_pos is not None
                and close_pos < len(row)
            ):
                close = number(
                    row[close_pos]
                )

            # ------------------------------------------------
            # Change
            # ------------------------------------------------

            change = None

            if (
                change_pos is not None
                and change_pos < len(row)
            ):
                change = number(
                    row[change_pos]
                )

            # ------------------------------------------------
            # Change %
            # ------------------------------------------------

            change_pct = None

            if (
                change_pct_pos is not None
                and change_pct_pos < len(row)
            ):
                change_pct = number(
                    row[change_pct_pos]
                )

            if close is None:
                continue

            # ------------------------------------------------
            # Fallback percentage
            # ------------------------------------------------

            if (
                change_pct is None
                and change is not None
            ):

                previous = (
                    close - change
                )

                if previous != 0:
                    change_pct = (
                        change
                        / previous
                        * 100
                    )

            return {
                "value": round(
                    close,
                    2,
                ),
                "change": (
                    round(
                        change,
                        2,
                    )
                    if change is not None
                    else None
                ),
                "change_pct": (
                    round(
                        change_pct,
                        2,
                    )
                    if change_pct is not None
                    else None
                ),
            }

    return None


# ============================================================
# Fetch
# ============================================================

def fetch_for_date(
    trading_date: date,
) -> Optional[Dict[str, Any]]:

    params = {
        "response": "json",
        "date": roc_date(
            trading_date
        ),
        "type": "IND",
    }

    try:

        response = requests.get(
            TWSE_URL,
            params=params,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

    except Exception as exc:

        log(
            f"⚠️ TWSE {trading_date} "
            f"取得失敗：{exc}"
        )

        return None

    parsed = parse_index_response(
        payload
    )

    if parsed is None:

        log(
            f"⚠️ TWSE {trading_date} "
            "找不到有效加權指數"
        )

        return None

    return parsed


# ============================================================
# Latest Market Data
# ============================================================

def fetch_latest_market()
    -> tuple[date, Dict[str, Any]]:

    today = datetime.now(
        timezone(
            timedelta(
                hours=8
            )
        )
    ).date()

    # 最多向前尋找 10 天。
    # 足以涵蓋週末及一般連假。
    for offset in range(0, 11):

        target = (
            today
            - timedelta(
                days=offset
            )
        )

        result = fetch_for_date(
            target
        )

        if result is not None:

            return (
                target,
                result,
            )

    raise RuntimeError(
        "無法從 TWSE 官方資料取得最近交易日加權指數"
    )


# ============================================================
# Market Status
# ============================================================

def get_market_status(
    now: datetime,
    latest_trading_date: date,
) -> str:

    # --------------------------------------------------------
    # 非週一至週五
    # --------------------------------------------------------

    if now.weekday() >= 5:
        return "closed"

    # --------------------------------------------------------
    # 今天不是最新交易日
    #
    # 例如：
    # 週一早上尚未取得今日資料，
    # 最新交易日仍為上週五。
    # --------------------------------------------------------

    if now.date() != latest_trading_date:
        return "closed"

    current = now.time()

    if (
        time(
            9,
            0
        )
        <= current
        <= time(
            13,
            30
        )
    ):
        return "open"

    return "closed"


# ============================================================
# Sentiment
# ============================================================

def build_sentiment(
    change_pct: Optional[float],
) -> Dict[str, str]:

    """
    市場風向只在 backend 決定。

    前端只負責顯示：
        偏多
        震盪
        偏弱

    這裡暫以 TAIEX 當日漲跌幅作為
    市場風向的基礎分類。

    >= +0.50% → 偏多
    <= -0.50% → 偏弱
    其餘       → 震盪
    """

    if change_pct is None:

        return {
            "level": "資料不足",
            "description": "市場資料不足",
        }

    if change_pct >= 0.50:

        return {
            "level": "偏多",
            "description": "市場氣氛偏強",
        }

    if change_pct <= -0.50:

        return {
            "level": "偏弱",
            "description": "市場氣氛偏弱",
        }

    return {
        "level": "震盪",
        "description": "多空力量接近",
    }


# ============================================================
# Atomic Write
# ============================================================

def atomic_write(
    data: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=".market.",
        suffix=".tmp",
        dir=DATA_DIR,
    )

    temp_path = Path(
        temp_name
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:

            json.dump(
                data,
                handle,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )

            handle.write(
                "\n"
            )

            handle.flush()

            os.fsync(
                handle.fileno()
            )

        os.replace(
            temp_path,
            OUTPUT_FILE,
        )

    finally:

        temp_path.unlink(
            missing_ok=True
        )


# ============================================================
# Read Back Validation
# ============================================================

def validate_output(
    data: Dict[str, Any],
) -> None:

    if not isinstance(
        data,
        dict,
    ):
        raise RuntimeError(
            "market.json root 必須是 object"
        )

    required = {
        "schema_version",
        "generated_at",
        "market_status",
        "latest_trading_date",
        "index",
        "sentiment",
        "source",
    }

    missing = (
        required
        - set(data.keys())
    )

    if missing:

        raise RuntimeError(
            "market.json 缺少欄位："
            + ", ".join(
                sorted(missing)
            )
        )

    if data.get(
        "schema_version"
    ) != SCHEMA_VERSION:

        raise RuntimeError(
            "market.json schema_version 錯誤"
        )

    if data.get(
        "market_status"
    ) not in {
        "open",
        "closed",
    }:

        raise RuntimeError(
            "market_status 無效"
        )

    index = data.get(
        "index"
    )

    if not isinstance(
        index,
        dict,
    ):

        raise RuntimeError(
            "market.index 必須是 object"
        )

    if number(
        index.get(
            "value"
        )
    ) is None:

        raise RuntimeError(
            "market.index.value 無效"
        )

    sentiment = data.get(
        "sentiment"
    )

    if not isinstance(
        sentiment,
        dict,
    ):

        raise RuntimeError(
            "market.sentiment 必須是 object"
        )

    if sentiment.get(
        "level"
    ) not in {
        "偏多",
        "震盪",
        "偏弱",
        "資料不足",
    }:

        raise RuntimeError(
            "market.sentiment.level 無效"
        )


# ============================================================
# Main
# ============================================================

def main() -> int:

    section(
        "FETCH MARKET DATA"
    )

    now = datetime.now(
        timezone(
            timedelta(
                hours=8
            )
        )
    )

    latest_date, index = (
        fetch_latest_market()
    )

    market_status = (
        get_market_status(
            now,
            latest_date,
        )
    )

    market_sentiment = (
        build_sentiment(
            index.get(
                "change_pct"
            )
        )
    )

    data = {

        "schema_version":
            SCHEMA_VERSION,

        "generated_at":
            now.isoformat(
                timespec="seconds"
            ),

        "market_status":
            market_status,

        "latest_trading_date":
            latest_date.isoformat(),

        "index": {
            "name":
                "加權指數",

            "value":
                index.get(
                    "value"
                ),

            "change":
                index.get(
                    "change"
                ),

            "change_pct":
                index.get(
                    "change_pct"
                ),
        },

        "sentiment":
            market_sentiment,

        "source": {
            "provider":
                "TWSE",

            "endpoint":
                TWSE_URL,
        },
    }

    atomic_write(
        data
    )

    # --------------------------------------------------------
    # Read back
    # --------------------------------------------------------

    with OUTPUT_FILE.open(
        "r",
        encoding="utf-8",
    ) as handle:

        saved = json.load(
            handle
        )

    validate_output(
        saved
    )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    section(
        "MARKET DATA RESULT"
    )

    log(
        "✓ 官方來源：TWSE"
    )

    log(
        "✓ 加權指數："
        f"{saved['index']['value']}"
    )

    log(
        "✓ 漲跌："
        f"{saved['index']['change']}"
    )

    log(
        "✓ 漲跌幅："
        f"{saved['index']['change_pct']}%"
    )

    log(
        "✓ 最新交易日："
        f"{saved['latest_trading_date']}"
    )

    log(
        "✓ 市場狀態："
        f"{saved['market_status']}"
    )

    log(
        "✓ 市場風向："
        f"{saved['sentiment']['level']}"
    )

    log(
        f"✓ {OUTPUT_FILE}"
    )

    section(
        "FINAL VALIDATION"
    )

    log(
        "✓ schema"
    )

    log(
        "✓ index"
    )

    log(
        "✓ latest_trading_date"
    )

    log(
        "✓ market_status"
    )

    log(
        "✓ sentiment"
    )

    log(
        "✓ read-back"
    )

    log(
        "✓ MARKET DATA PASS"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )