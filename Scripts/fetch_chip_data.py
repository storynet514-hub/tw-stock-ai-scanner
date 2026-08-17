#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip_data.py V1.0

============================================================
用途
============================================================
本程式專門負責「短期個股籌碼資料」。

目前抓取：
1. 主力買賣超
2. 融資餘額
3. 融券餘額
4. 當沖率

本程式不負責：
- Universe 建立
- 歷史價格
- MACD
- KD
- RSI
- MA
- UI

輸入：
    Data/universe.json

輸出：
    Data/chip.json

重要原則：
- 不製造假資料
- 不用 0 代替抓不到的資料
- 單一股票失敗不應該破壞整批資料
- 最後只有在有有效資料時才寫入 chip.json
============================================================
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 20

SLEEP_SECONDS = 0.15

USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)


# ============================================================
# Session
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }
)


# ============================================================
# 共用工具
# ============================================================

def now_taiwan() -> str:
    """
    回傳台灣時間。
    """

    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).strftime("%Y-%m-%d %H:%M:%S")


def today_taiwan() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo("Asia/Taipei")
    ).strftime("%Y-%m-%d")


def safe_float(value: Any) -> Optional[float]:
    """
    安全轉換數字。

    不把空值轉成 0。
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    text = (
        text.replace(",", "")
        .replace("%", "")
        .replace("＋", "+")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )

    if text in {"-", "--", "N/A", "NA", "null", "None"}:
        return None

    try:
        return float(text)
    except Exception:
        return None


def normalize_symbol(symbol: Any) -> Optional[str]:
    """
    將股票代號統一成系統格式。

    台股：
        2330
        2330.TW

    上櫃：
        3081
        3081.TWO
    """

    if symbol is None:
        return None

    s = str(symbol).strip().upper()

    if not s:
        return None

    return s


# ============================================================
# Universe
# ============================================================

def load_universe() -> List[str]:
    """
    讀取 Data/universe.json。

    支援幾種常見結構：
    1.
    {
        "stocks": [
            {"symbol": "2330.TW"},
            ...
        ]
    }

    2.
    {
        "universe": [
            {"symbol": "2330.TW"},
            ...
        ]
    }

    3.
    [
        {"symbol": "2330.TW"},
        ...
    ]
    """

    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    records: Any = None

    if isinstance(data, list):
        records = data

    elif isinstance(data, dict):

        for key in (
            "stocks",
            "universe",
            "symbols",
            "data",
        ):
            if isinstance(data.get(key), list):
                records = data[key]
                break

    if records is None:
        raise ValueError(
            "universe.json 格式無法辨識，找不到股票清單。"
        )

    symbols: List[str] = []

    for item in records:

        symbol = None

        if isinstance(item, str):
            symbol = item

        elif isinstance(item, dict):

            for key in (
                "symbol",
                "code",
                "stock_id",
                "ticker",
            ):
                if item.get(key):
                    symbol = item[key]
                    break

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        # 本籌碼模組目前只處理台股
        if symbol.endswith(".TW") or symbol.endswith(".TWO"):
            symbols.append(symbol)

        elif symbol.isdigit():
            symbols.append(symbol)

    # 去除重複
    symbols = sorted(set(symbols))

    return symbols


# ============================================================
# TWSE / TPEx 識別
# ============================================================

def get_market(symbol: str) -> Optional[str]:
    """
    判斷市場。

    .TW  -> TWSE
    .TWO -> TPEx
    """

    symbol = symbol.upper()

    if symbol.endswith(".TW"):
        return "TWSE"

    if symbol.endswith(".TWO"):
        return "TPEX"

    return None


def clean_code(symbol: str) -> str:
    """
    2330.TW -> 2330
    3081.TWO -> 3081
    """

    return (
        symbol.upper()
        .replace(".TW", "")
        .replace(".TWO", "")
    )


# ============================================================
# TWSE 官方資料
# ============================================================

def fetch_twse_margin_short(
    code: str,
) -> Optional[Dict[str, Any]]:
    """
    嘗試取得 TWSE 融資融券資料。

    注意：
    官方資料格式可能調整，因此這裡採取
    「格式驗證 + 欄位尋找」方式。

    找不到資料時回傳 None。
    """

    url = (
        "https://www.twse.com.tw/rwd/zh/marginTrading/"
        "marginTWTAS"
    )

    params = {
        "response": "json",
        "date": today_taiwan().replace("-", ""),
        "selectType": "ALL",
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        print(
            f"      ⚠️ TWSE 融資融券取得失敗：{exc}"
        )

        return None

    if not isinstance(data, dict):
        return None

    fields = data.get("fields")

    rows = data.get("data")

    if not isinstance(fields, list):
        return None

    if not isinstance(rows, list):
        return None

    code_index = None

    for i, field in enumerate(fields):

        field_text = str(field)

        if (
            "股票代號" in field_text
            or "證券代號" in field_text
            or field_text == "代號"
        ):
            code_index = i
            break

    if code_index is None:
        return None

    for row in rows:

        if not isinstance(row, list):
            continue

        if code_index >= len(row):
            continue

        row_code = str(
            row[code_index]
        ).strip()

        if row_code != code:
            continue

        result: Dict[str, Any] = {}

        for i, field in enumerate(fields):

            if i >= len(row):
                continue

            name = str(field)

            value = safe_float(row[i])

            if "融資餘額" in name:
                result["margin_balance"] = value

            elif "融券餘額" in name:
                result["short_balance"] = value

        if result:
            return result

    return None


# ============================================================
# TPEx 官方資料
# ============================================================

def fetch_tpex_margin_short(
    code: str,
) -> Optional[Dict[str, Any]]:
    """
    嘗試取得 TPEx 融資融券資料。

    官方 API 若無法使用，回傳 None。

    不以第三方資料冒充官方資料。
    """

    url = (
        "https://www.tpex.org.tw/"
        "www/zh-tw/marginTrading/"
        "margin.html"
    )

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except Exception as exc:

        print(
            f"      ⚠️ TPEx 融資融券來源無法取得：{exc}"
        )

        return None

    # 此階段不硬解析 HTML。
    #
    # 原因：
    # TPEx 頁面格式可能變更。
    #
    # 寧可回傳 None，
    # 也不製造錯誤資料。

    return None


# ============================================================
# 當沖資料
# ============================================================

def fetch_day_trade_data(
    code: str,
) -> Optional[float]:
    """
    取得當沖率。

    這一層採取保守策略：
    若官方資料來源無法可靠取得，
    不填入假值。

    回傳：
        float = 當沖率
        None  = 無可靠資料
    """

    # --------------------------------------------------------
    # TWSE 當沖資料來源
    # --------------------------------------------------------

    url = (
        "https://www.twse.com.tw/rwd/zh/"
        "afterTrading/MI_INDEX"
    )

    params = {
        "response": "json",
        "date": today_taiwan().replace("-", ""),
        "type": "ALLBUT0999",
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # --------------------------------------------------------
    # 官方 API 不同版本可能使用不同 table
    # --------------------------------------------------------

    tables = data.get("tables")

    if not isinstance(tables, list):
        return None

    for table in tables:

        if not isinstance(table, dict):
            continue

        fields = table.get("fields")
        rows = table.get("data")

        if not isinstance(fields, list):
            continue

        if not isinstance(rows, list):
            continue

        code_index = None

        for i, field in enumerate(fields):

            name = str(field)

            if (
                "證券代號" in name
                or "股票代號" in name
            ):
                code_index = i
                break

        if code_index is None:
            continue

        for row in rows:

            if not isinstance(row, list):
                continue

            if code_index >= len(row):
                continue

            row_code = str(
                row[code_index]
            ).strip()

            if row_code != code:
                continue

            # 嘗試尋找「當沖」相關欄位
            for i, field in enumerate(fields):

                if i >= len(row):
                    continue

                name = str(field)

                if (
                    "當沖率" in name
                    or "當沖" in name
                ):
                    value = safe_float(row[i])

                    if value is not None:
                        return value

    return None


# ============================================================
# 主力買賣超
# ============================================================

def fetch_institutional_net(
    code: str,
) -> Optional[float]:
    """
    取得法人買賣超。

    注意：
    「主力買賣超」與「三大法人買賣超」不是完全相同的概念。

    本版本先保留欄位名稱：
        institutional_net

    後續我們會再決定：
        institutional_net
    是否直接作為短線「主力」條件。

    不會把三大法人數字直接假裝成主力數字。
    """

    url = (
        "https://www.twse.com.tw/rwd/zh/"
        "fund/T86"
    )

    params = {
        "response": "json",
        "date": today_taiwan().replace("-", ""),
        "selectType": "ALLBUT0999",
    }

    try:

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    fields = data.get("fields")
    rows = data.get("data")

    if not isinstance(fields, list):
        return None

    if not isinstance(rows, list):
        return None

    code_index = None

    for i, field in enumerate(fields):

        name = str(field)

        if (
            "證券代號" in name
            or "股票代號" in name
        ):
            code_index = i
            break

    if code_index is None:
        return None

    # --------------------------------------------------------
    # 找「外資及陸資」買賣超
    # --------------------------------------------------------
    #
    # 注意：
    # 這裡不命名成 main_force_net。
    #
    # 因為法人買賣超 != 主力買賣超。
    # --------------------------------------------------------

    candidate_indexes = []

    for i, field in enumerate(fields):

        name = str(field)

        if "買賣超股數" in name:
            candidate_indexes.append(i)

    for row in rows:

        if not isinstance(row, list):
            continue

        if code_index >= len(row):
            continue

        row_code = str(
            row[code_index]
        ).strip()

        if row_code != code:
            continue

        values = []

        for idx in candidate_indexes:

            if idx < len(row):

                value = safe_float(
                    row[idx]
                )

                if value is not None:
                    values.append(value)

        if values:
            # 第一階段保留官方法人買賣超。
            return values[0]

    return None


# ============================================================
# 單一股票
# ============================================================

def fetch_one_stock(
    symbol: str,
) -> Dict[str, Any]:

    code = clean_code(symbol)

    market = get_market(symbol)

    record: Dict[str, Any] = {
        "symbol": symbol,
        "code": code,
        "market": market,
        "date": today_taiwan(),
    }

    # --------------------------------------------------------
    # 融資融券
    # --------------------------------------------------------

    margin_data = None

    if market == "TWSE":
        margin_data = fetch_twse_margin_short(code)

    elif market == "TPEX":
        margin_data = fetch_tpex_margin_short(code)

    if margin_data:
        record.update(margin_data)

    else:
        record["margin_balance"] = None
        record["short_balance"] = None

    # --------------------------------------------------------
    # 當沖率
    # --------------------------------------------------------

    day_trade_ratio = fetch_day_trade_data(code)

    record["day_trade_ratio"] = day_trade_ratio

    # --------------------------------------------------------
    # 法人買賣超
    # --------------------------------------------------------

    institutional_net = None

    if market == "TWSE":
        institutional_net = fetch_institutional_net(code)

    record["institutional_net"] = institutional_net

    # --------------------------------------------------------
    # 完整性狀態
    # --------------------------------------------------------

    available = 0
    total = 3

    if institutional_net is not None:
        available += 1

    if margin_data is not None:
        available += 1

    if day_trade_ratio is not None:
        available += 1

    record["available_fields"] = available
    record["total_fields"] = total

    record["complete"] = (
        available == total
    )

    return record


# ============================================================
# JSON 寫入
# ============================================================

def write_json(
    records: Dict[str, Any],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = {
        "version": VERSION,
        "generated_at": now_taiwan(),
        "date": today_taiwan(),
        "count": len(records),
        "data": records,
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            payload,
            f,
            ensure_ascii=False,
            indent=2,
        )

    # 原子替換
    temp_file.replace(
        OUTPUT_FILE
    )


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    print("=" * 64)
    print(
        f"台股 AI 選股系統 "
        f"fetch_chip_data.py {VERSION}"
    )
    print("=" * 64)

    print(
        f"開始時間：{now_taiwan()}"
    )

    print()
    print("=" * 64)
    print("讀取 Universe")
    print("=" * 64)

    try:

        symbols = load_universe()

    except Exception as exc:

        print(
            f"❌ Universe 讀取失敗：{exc}"
        )

        return 1

    print(
        f"Universe 台股股票數量：{len(symbols)}"
    )

    if not symbols:

        print(
            "❌ Universe 沒有任何台股股票。"
        )

        return 1

    print()
    print("=" * 64)
    print("開始取得籌碼資料")
    print("=" * 64)

    results: Dict[str, Any] = {}

    success = 0
    partial = 0
    failed = 0

    for index, symbol in enumerate(symbols, start=1):

        print(
            f"[{index}/{len(symbols)}] "
            f"{symbol}"
        )

        try:

            record = fetch_one_stock(symbol)

            results[symbol] = record

            available = record.get(
                "available_fields",
                0
            )

            if available == 3:

                success += 1

                print(
                    "      ✅ 籌碼資料完整"
                )

            elif available > 0:

                partial += 1

                print(
                    f"      ⚠️ 部分資料 "
                    f"{available}/3"
                )

            else:

                failed += 1

                print(
                    "      ❌ 沒有有效籌碼資料"
                )

        except Exception as exc:

            failed += 1

            print(
                f"      ❌ 發生錯誤：{exc}"
            )

        time.sleep(
            SLEEP_SECONDS
        )

    print()
    print("=" * 64)
    print("籌碼資料抓取結果")
    print("=" * 64)

    print(
        f"Universe：{len(symbols)}"
    )

    print(
        f"完整資料：{success}"
    )

    print(
        f"部分資料：{partial}"
    )

    print(
        f"無資料 / 失敗：{failed}"
    )

    print()

    # --------------------------------------------------------
    # 防呆：
    # 完全沒有取得任何資料時，不覆蓋既有 chip.json
    # --------------------------------------------------------

    valid_records = 0

    for record in results.values():

        if record.get(
            "available_fields",
            0
        ) > 0:

            valid_records += 1

    if valid_records == 0:

        print(
            "❌ 本次完全沒有取得有效籌碼資料。"
        )

        print(
            "❌ 為避免覆蓋既有資料，"
            "本次不建立 chip.json。"
        )

        return 1

    # --------------------------------------------------------
    # 寫入
    # --------------------------------------------------------

    try:

        write_json(results)

    except Exception as exc:

        print(
            f"❌ chip.json 寫入失敗：{exc}"
        )

        return 1

    print(
        f"✅ 已建立：{OUTPUT_FILE}"
    )

    print(
        f"✅ 有效股票資料：{valid_records}"
    )

    print()
    print("=" * 64)
    print(
        f"fetch_chip_data.py {VERSION} 完成"
    )
    print("=" * 64)

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )