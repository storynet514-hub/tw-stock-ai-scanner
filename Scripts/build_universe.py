#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股 AI 選股系統
build_universe.py V1.0
============================================================

用途：
    建立「台股全市場」Universe。

資料來源：
    1. TWSE OpenAPI
       - 上市公司基本資料

    2. TPEx OpenAPI
       - 上櫃股票基本資料

輸出：
    Data/universe.json

市場範圍：
    ✅ 上市普通股票
    ✅ 上櫃普通股票

排除：
    ❌ ETF
    ❌ ETN
    ❌ 權證
    ❌ 興櫃
    ❌ 創櫃
    ❌ 特別股
    ❌ 其他非普通股票證券

重要：
    本程式不負責：
    - 歷史價格
    - MACD
    - KD
    - RSI
    - MA
    - 籌碼
    - 選股
    - UI

資料流程：

    TWSE OpenAPI ─────┐
                      ├──> build_universe.py
    TPEx OpenAPI ─────┘
                              ↓
                      Data/universe.json
                              ↓
                       fetch_prices.py
                              ↓
                       Data/prices.json
============================================================
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5


# ============================================================
# 官方 API
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/v1"
    "/opendata/t187ap03_L"
)

TPEX_API = (
    "https://www.tpex.org.tw/openapi/v1"
    "/tpex_mainboard_peratio_analysis"
)

# 上櫃基本資料
TPEX_BASIC_API = (
    "https://www.tpex.org.tw/openapi/v1"
    "/mopsfin_t187ap03_O"
)


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "*/*"
    ),
}


def request_json(url):
    """
    官方 API GET。

    失敗直接 raise。
    不建立假的 Universe。
    """

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        raise RuntimeError(
            f"API 回傳空資料：{url}"
        )

    return data


# ============================================================
# 輸出
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# 股票代號驗證
# ============================================================

def normalize_code(value):
    """
    將股票代號標準化。

    只接受 4 位數字。

    例如：
        "2330"      -> "2330"
        " 2330 "    -> "2330"
        "2330.TW"   -> "2330"

    非 4 位數字直接排除。
    """

    if value is None:
        return None

    text = str(value).strip().upper()

    # 去掉 Yahoo suffix
    text = text.replace(".TW", "")
    text = text.replace(".TWO", "")

    # 只接受 4 位數字
    if not re.fullmatch(r"\d{4}", text):
        return None

    return text


# ============================================================
# 名稱清理
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# TWSE
# ============================================================

def fetch_twse():
    """
    取得上市股票基本資料。

    官方來源：
        TWSE OpenAPI
        t187ap03_L
    """

    section("取得 TWSE 上市股票")

    log(
        "API："
        + TWSE_API
    )

    data = request_json(
        TWSE_API
    )

    if not isinstance(data, list):
        raise RuntimeError(
            "TWSE API 回傳格式不是 list"
        )

    log(
        f"TWSE API 原始資料："
        f"{len(data)} 筆"
    )

    result = {}

    for item in data:

        if not isinstance(item, dict):
            continue

        # TWSE 常見欄位：
        # 公司代號
        # 公司名稱
        # 公司簡稱

        code = (
            item.get("公司代號")
            or item.get("有價證券代號")
            or item.get("代號")
        )

        name = (
            item.get("公司名稱")
            or item.get("公司簡稱")
            or item.get("有價證券名稱")
            or item.get("名稱")
        )

        code = normalize_code(
            code
        )

        if not code:
            continue

        name = clean_text(
            name
        )

        if not name:
            continue

        yahoo_symbol = (
            code + ".TW"
        )

        result[code] = {
            "symbol": code,
            "yahoo_symbol": yahoo_symbol,
            "name": name,
            "market": "TWSE",
            "type": "stock",
        }

    if not result:
        raise RuntimeError(
            "TWSE 沒有解析出任何合法股票"
        )

    log(
        f"TWSE 合法普通股票："
        f"{len(result)}"
    )

    return result


# ============================================================
# TPEx
# ============================================================

def fetch_tpex():
    """
    取得上櫃股票基本資料。

    優先使用 TPEx 官方：
        mopsfin_t187ap03_O

    如果該 API 結構變更，
    不使用錯誤資料代替。
    """

    section("取得 TPEx 上櫃股票")

    log(
        "API："
        + TPEX_BASIC_API
    )

    data = request_json(
        TPEX_BASIC_API
    )

    if not isinstance(data, list):
        raise RuntimeError(
            "TPEx API 回傳格式不是 list"
        )

    log(
        f"TPEx API 原始資料："
        f"{len(data)} 筆"
    )

    result = {}

    for item in data:

        if not isinstance(item, dict):
            continue

        # TPEx 欄位可能使用：
        # 公司代號
        # 有價證券代號
        # 代號

        code = (
            item.get("公司代號")
            or item.get("有價證券代號")
            or item.get("代號")
            or item.get("SecuritiesCompanyCode")
        )

        name = (
            item.get("公司名稱")
            or item.get("公司簡稱")
            or item.get("有價證券名稱")
            or item.get("名稱")
            or item.get("SecuritiesCompanyName")
        )

        code = normalize_code(
            code
        )

        if not code:
            continue

        name = clean_text(
            name
        )

        if not name:
            continue

        yahoo_symbol = (
            code + ".TWO"
        )

        result[code] = {
            "symbol": code,
            "yahoo_symbol": yahoo_symbol,
            "name": name,
            "market": "TPEX",
            "type": "stock",
        }

    if not result:
        raise RuntimeError(
            "TPEx 沒有解析出任何合法股票"
        )

    log(
        f"TPEx 合法普通股票："
        f"{len(result)}"
    )

    return result


# ============================================================
# Universe 合併
# ============================================================

def build_universe():
    """
    合併上市＋上櫃。
    """

    section(
        "建立台股全市場 Universe"
    )

    twse = fetch_twse()

    time.sleep(
        REQUEST_DELAY
    )

    tpex = fetch_tpex()

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    items = []

    for code in sorted(twse.keys()):

        items.append(
            twse[code]
        )

    for code in sorted(tpex.keys()):

        items.append(
            tpex[code]
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in items:

        code = item["symbol"]

        unique[code] = item

    items = list(
        unique.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    items.sort(
        key=lambda x: (
            x["market"],
            x["symbol"]
        )
    )

    listed_count = sum(
        1
        for item in items
        if item["market"] == "TWSE"
    )

    otc_count = sum(
        1
        for item in items
        if item["market"] == "TPEX"
    )

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    if listed_count == 0:
        raise RuntimeError(
            "上市股票數量為 0，停止建立 Universe"
        )

    if otc_count == 0:
        raise RuntimeError(
            "上櫃股票數量為 0，停止建立 Universe"
        )

    if len(items) < 1000:
        raise RuntimeError(
            "全市場股票數量異常偏低："
            f"{len(items)}"
        )

    return {
        "version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "source": (
            "TWSE OpenAPI + TPEx OpenAPI"
        ),
        "market": "TW",
        "total": len(items),

        "listed_stocks": listed_count,
        "otc_stocks": otc_count,

        "listed_etf": 0,
        "otc_etf": 0,

        "items": items
    }


# ============================================================
# 驗證
# ============================================================

def validate_universe(data):
    section(
        "Universe 最終驗證"
    )

    items = data.get(
        "items",
        []
    )

    total = data.get(
        "total",
        0
    )

    listed = data.get(
        "listed_stocks",
        0
    )

    otc = data.get(
        "otc_stocks",
        0
    )

    log(
        f"總標的：{total}"
    )

    log(
        f"上市股票：{listed}"
    )

    log(
        f"上櫃股票：{otc}"
    )

    log(
        f"items：{len(items)}"
    )

    if total != len(items):
        raise RuntimeError(
            "total 與 items 數量不一致"
        )

    if total == 0:
        raise RuntimeError(
            "Universe 為空"
        )

    if listed == 0:
        raise RuntimeError(
            "上市股票為 0"
        )

    if otc == 0:
        raise RuntimeError(
            "上櫃股票為 0"
        )

    # --------------------------------------------------------
    # 股票代號重複檢查
    # --------------------------------------------------------

    codes = [
        item["symbol"]
        for item in items
    ]

    if len(codes) != len(set(codes)):
        raise RuntimeError(
            "發現重複股票代號"
        )

    # --------------------------------------------------------
    # 欄位檢查
    # --------------------------------------------------------

    invalid = []

    for item in items:

        code = item.get(
            "symbol"
        )

        yahoo_symbol = item.get(
            "yahoo_symbol"
        )

        name = item.get(
            "name"
        )

        market = item.get(
            "market"
        )

        if not re.fullmatch(
            r"\d{4}",
            str(code)
        ):
            invalid.append(
                f"{code}: invalid code"
            )
            continue

        if market == "TWSE":

            if yahoo_symbol != (
                str(code) + ".TW"
            ):
                invalid.append(
                    f"{code}: invalid TWSE Yahoo symbol"
                )

        elif market == "TPEX":

            if yahoo_symbol != (
                str(code) + ".TWO"
            ):
                invalid.append(
                    f"{code}: invalid TPEX Yahoo symbol"
                )

        else:

            invalid.append(
                f"{code}: unknown market"
            )

        if not name:
            invalid.append(
                f"{code}: empty name"
            )

    if invalid:

        raise RuntimeError(
            "Universe 結構驗證失敗："
            + " | ".join(
                invalid[:20]
            )
        )

    log(
        "✓ Universe 結構驗證通過"
    )


# ============================================================
# 寫入
# ============================================================

def save_universe(data):

    section(
        "寫入 Data/universe.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

    # 寫入完成後才正式替換
    temp_file.replace(
        OUTPUT_FILE
    )

    file_size = (
        OUTPUT_FILE.stat().st_size
    )

    log(
        "✓ universe.json 建立成功"
    )

    log(
        f"檔案：{OUTPUT_FILE}"
    )

    log(
        f"大小："
        f"{file_size / 1024:.2f} KB"
    )


# ============================================================
# 顯示摘要
# ============================================================

def print_summary(data):

    section(
        "Universe 建立完成"
    )

    log(
        f"總股票數："
        f"{data['total']}"
    )

    log(
        f"上市股票："
        f"{data['listed_stocks']}"
    )

    log(
        f"上櫃股票："
        f"{data['otc_stocks']}"
    )

    log(
        f"上市 ETF："
        f"{data['listed_etf']}"
    )

    log(
        f"上櫃 ETF："
        f"{data['otc_etf']}"
    )

    log("")

    log(
        "前 20 檔："
    )

    for index, item in enumerate(
        data["items"][:20],
        start=1
    ):

        log(
            f"{index:>2}. "
            f"{item['symbol']} "
            f"{item['name']} "
            f"[{item['market']}] "
            f"{item['yahoo_symbol']}"
        )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 64)
    log(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )
    log("=" * 64)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        # ----------------------------------------------------
        # 1. 建立 Universe
        # ----------------------------------------------------

        universe = build_universe()

        # ----------------------------------------------------
        # 2. 驗證
        # ----------------------------------------------------

        validate_universe(
            universe
        )

        # ----------------------------------------------------
        # 3. 寫入
        # ----------------------------------------------------

        save_universe(
            universe
        )

        # ----------------------------------------------------
        # 4. 摘要
        # ----------------------------------------------------

        print_summary(
            universe
        )

        elapsed = (
            time.time()
            - start_time
        )

        log("")
        log("=" * 64)
        log(
            "✓ build_universe.py 執行完成"
        )
        log("=" * 64)

        log(
            f"總股票："
            f"{universe['total']}"
        )

        log(
            f"上市："
            f"{universe['listed_stocks']}"
        )

        log(
            f"上櫃："
            f"{universe['otc_stocks']}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出："
            f"{OUTPUT_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 64)
        log(
            "❌ build_universe.py 執行失敗"
        )
        log("=" * 64)

        log(
            f"原因：{exc}"
        )

        # ----------------------------------------------------
        # 重要：
        # 失敗時不覆蓋既有 universe.json
        # ----------------------------------------------------

        if OUTPUT_FILE.exists():

            log(
                "⚠️ 保留原有 universe.json"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(
        main()
    )
