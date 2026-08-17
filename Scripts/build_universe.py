#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V3.0

============================================================
目的
============================================================

建立「台股全市場」Universe。

包含：

1. TWSE 上市普通股票
2. TPEx 上櫃普通股票
3. 不預設固定股票清單
4. 不使用使用者追蹤清單
5. 不包含 ETF
6. 不包含權證
7. 不包含特別股
8. 不包含牛熊證等非普通股票

輸出：

Data/universe.json

============================================================
重要設計
============================================================

任何單一 API 失敗：

❌ 不可以直接清空 Universe
❌ 不可以覆蓋正常 universe.json
❌ 不可以產生空 Universe

只有在：

TWSE + TPEx 都取得有效資料
且總股票數達到合理數量

才正式覆蓋 universe.json。

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

VERSION = "V3.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

REQUEST_TIMEOUT = 30

MIN_TWSE_STOCKS = 500
MIN_TPEX_STOCKS = 300
MIN_TOTAL_STOCKS = 1000

REQUEST_DELAY = 1.0


# ============================================================
# API
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)

TPEX_API = (
    "https://www.tpex.org.tw/openapi/v1/"
    "mopsfin_t187ap03_O"
)


# ============================================================
# User-Agent
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "*/*"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


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
# HTTP GET
# ============================================================

def http_get(url, retries=3):

    last_error = None

    for attempt in range(1, retries + 1):

        try:

            log(
                f"  HTTP GET attempt "
                f"{attempt}/{retries}"
            )

            response = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            log(
                f"  HTTP Status: "
                f"{response.status_code}"
            )

            response.raise_for_status()

            if not response.content:
                raise RuntimeError(
                    "API 回傳空內容"
                )

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠ API 取得失敗：{exc}"
            )

            if attempt < retries:
                time.sleep(
                    attempt * 2
                )

    raise RuntimeError(
        f"API 連續 {retries} 次失敗："
        f"{last_error}"
    )


# ============================================================
# 安全解析 JSON
# ============================================================

def parse_json_response(response, source):

    text = response.text.strip()

    if not text:
        raise RuntimeError(
            f"{source} API 回傳空內容"
        )

    # --------------------------------------------------------
    # 正常 JSON
    # --------------------------------------------------------

    try:
        data = response.json()

        return data

    except Exception:
        pass

    # --------------------------------------------------------
    # 嘗試去除 BOM
    # --------------------------------------------------------

    try:

        clean_text = (
            text
            .lstrip("\ufeff")
            .strip()
        )

        data = json.loads(
            clean_text
        )

        return data

    except Exception as exc:

        # 不把整個 API response 印出來
        preview = text[:300]

        raise RuntimeError(
            f"{source} API 回傳內容不是合法 JSON。"
            f"前300字：{preview}"
        ) from exc


# ============================================================
# 股票代號正規化
# ============================================================

def normalize_code(value):

    if value is None:
        return None

    code = str(value).strip()

    if not code:
        return None

    # 去除 BOM
    code = code.replace(
        "\ufeff",
        ""
    )

    # 去除空白
    code = code.strip()

    # --------------------------------------------------------
    # 台股普通股票代號通常 4 碼
    # --------------------------------------------------------

    if not re.fullmatch(
        r"\d{4}",
        code
    ):
        return None

    return code


# ============================================================
# 判斷是否為普通股票
# ============================================================

def is_common_stock(
    code,
    name="",
    raw=None
):

    code = normalize_code(code)

    if not code:
        return False

    name = str(name or "").strip()

    # --------------------------------------------------------
    # 排除明確非普通股票
    # --------------------------------------------------------

    exclude_keywords = [
        "ETF",
        "指數股票型基金",
        "受益證券",
        "認購權證",
        "認售權證",
        "權證",
        "牛證",
        "熊證",
        "可轉債",
        "公司債",
        "特別股",
        "存託憑證",
        "DR",
        "ETN",
        "槓桿",
        "反向",
        "期貨",
        "選擇權",
    ]

    upper_name = name.upper()

    for keyword in exclude_keywords:

        if keyword.upper() in upper_name:
            return False

    # --------------------------------------------------------
    # 代號型態排除
    #
    # 一般台股普通股票主要為 4 碼。
    # --------------------------------------------------------

    return True


# ============================================================
# 從 API Record 找股票代號
# ============================================================

def find_value(record, keys):

    if not isinstance(record, dict):
        return None

    # 精確 key
    for key in keys:

        if key in record:

            value = record.get(key)

            if value not in (
                None,
                ""
            ):
                return value

    # 忽略大小寫 / 空白
    normalized_keys = {
        str(k).strip().lower()
        for k in keys
    }

    for key, value in record.items():

        key_normalized = (
            str(key)
            .strip()
            .lower()
        )

        if key_normalized in normalized_keys:

            if value not in (
                None,
                ""
            ):
                return value

    return None


# ============================================================
# 找股票名稱
# ============================================================

def find_name(record):

    keys = [
        "公司名稱",
        "公司名稱 ",
        "公司簡稱",
        "名稱",
        "name",
        "Name",
        "SecuritiesCompanyName",
        "公司代號名稱",
        "中文簡稱",
    ]

    value = find_value(
        record,
        keys
    )

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# 找股票代號
# ============================================================

def find_stock_code(record):

    keys = [
        "公司代號",
        "股票代號",
        "證券代號",
        "代號",
        "code",
        "Code",
        "symbol",
        "Symbol",
        "SecuritiesCompanyCode",
    ]

    value = find_value(
        record,
        keys
    )

    return normalize_code(
        value
    )


# ============================================================
# 解析 TWSE
# ============================================================

def fetch_twse():

    section("取得 TWSE 上市股票")

    log(
        f"API：{TWSE_API}"
    )

    response = http_get(
        TWSE_API
    )

    data = parse_json_response(
        response,
        "TWSE"
    )

    if not isinstance(
        data,
        list
    ):
        raise RuntimeError(
            "TWSE API 回傳格式不是 list"
        )

    log(
        f"TWSE API 原始資料："
        f"{len(data)} 筆"
    )

    stocks = {}

    for record in data:

        if not isinstance(
            record,
            dict
        ):
            continue

        code = find_stock_code(
            record
        )

        name = find_name(
            record
        )

        if not code:
            continue

        if not is_common_stock(
            code,
            name,
            record
        ):
            continue

        stocks[code] = {
            "symbol": code,
            "yahoo_symbol": (
                f"{code}.TW"
            ),
            "name": name,
            "market": "TWSE",
            "type": "stock",
        }

    log(
        f"TWSE 合法普通股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE 合法股票數量異常："
            f"{len(stocks)}，"
            f"低於最低門檻 "
            f"{MIN_TWSE_STOCKS}"
        )

    return stocks


# ============================================================
# 解析 TPEx
# ============================================================

def fetch_tpex():

    section("取得 TPEx 上櫃股票")

    log(
        f"API：{TPEX_API}"
    )

    response = http_get(
        TPEX_API
    )

    data = parse_json_response(
        response,
        "TPEx"
    )

    if not isinstance(
        data,
        list
    ):
        raise RuntimeError(
            "TPEx API 回傳格式不是 list"
        )

    log(
        f"TPEx API 原始資料："
        f"{len(data)} 筆"
    )

    stocks = {}

    for record in data:

        if not isinstance(
            record,
            dict
        ):
            continue

        code = find_stock_code(
            record
        )

        name = find_name(
            record
        )

        if not code:
            continue

        if not is_common_stock(
            code,
            name,
            record
        ):
            continue

        stocks[code] = {
            "symbol": code,
            "yahoo_symbol": (
                f"{code}.TWO"
            ),
            "name": name,
            "market": "TPEx",
            "type": "stock",
        }

    log(
        f"TPEx 合法普通股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEx 合法股票數量異常："
            f"{len(stocks)}，"
            f"低於最低門檻 "
            f"{MIN_TPEX_STOCKS}"
        )

    return stocks


# ============================================================
# 建立 Universe
# ============================================================

def build_universe():

    section("建立台股全市場 Universe")

    twse = fetch_twse()

    time.sleep(
        REQUEST_DELAY
    )

    tpex = fetch_tpex()

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    stocks = {}

    for code, record in twse.items():
        stocks[code] = record

    for code, record in tpex.items():

        # 如果代號重複，優先保留 TWSE
        if code not in stocks:
            stocks[code] = record

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    sorted_items = sorted(
        stocks.values(),
        key=lambda x: x["symbol"]
    )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    listed_count = sum(
        1
        for x in sorted_items
        if x.get("market") == "TWSE"
    )

    otc_count = sum(
        1
        for x in sorted_items
        if x.get("market") == "TPEx"
    )

    section("Universe 統計")

    log(
        f"上市股票：{listed_count}"
    )

    log(
        f"上櫃股票：{otc_count}"
    )

    log(
        f"全市場普通股票："
        f"{len(sorted_items)}"
    )

    # --------------------------------------------------------
    # 最低總量保護
    # --------------------------------------------------------

    if listed_count < MIN_TWSE_STOCKS:
        raise RuntimeError(
            "上市股票數量不足，"
            "拒絕覆蓋 universe.json"
        )

    if otc_count < MIN_TPEX_STOCKS:
        raise RuntimeError(
            "上櫃股票數量不足，"
            "拒絕覆蓋 universe.json"
        )

    if len(sorted_items) < MIN_TOTAL_STOCKS:
        raise RuntimeError(
            "全市場股票總數不足，"
            "拒絕覆蓋 universe.json"
        )

    # --------------------------------------------------------
    # 建立輸出
    # --------------------------------------------------------

    output = {
        "version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "source": [
            "TWSE OpenAPI",
            "TPEx OpenAPI"
        ],
        "market": "TW",
        "total": len(sorted_items),
        "listed_stocks": listed_count,
        "otc_stocks": otc_count,
        "listed_etf": 0,
        "otc_etf": 0,
        "items": sorted_items,
    }

    return output


# ============================================================
# 寫入 Universe
# ============================================================

def save_universe(data):

    section("寫入 Data/universe.json")

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_file = (
        UNIVERSE_FILE.with_suffix(
            ".json.tmp"
        )
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

        f.write("\n")

    temp_file.replace(
        UNIVERSE_FILE
    )

    size = (
        UNIVERSE_FILE.stat().st_size
    )

    log(
        "✓ universe.json 更新成功"
    )

    log(
        f"檔案：{UNIVERSE_FILE}"
    )

    log(
        f"大小：{size / 1024:.1f} KB"
    )


# ============================================================
# 驗證輸出
# ============================================================

def validate_universe(data):

    section("驗證 Universe")

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):
        raise RuntimeError(
            "items 不是 list"
        )

    if len(items) < MIN_TOTAL_STOCKS:
        raise RuntimeError(
            f"Universe 只有 {len(items)} 筆"
        )

    symbols = set()

    invalid = []

    for item in items:

        if not isinstance(
            item,
            dict
        ):
            invalid.append(
                "非 dictionary"
            )
            continue

        symbol = item.get(
            "symbol"
        )

        if not re.fullmatch(
            r"\d{4}",
            str(symbol or "")
        ):
            invalid.append(
                str(symbol)
            )
            continue

        if symbol in symbols:
            invalid.append(
                f"duplicate:{symbol}"
            )
            continue

        symbols.add(symbol)

        yahoo_symbol = item.get(
            "yahoo_symbol"
        )

        market = item.get(
            "market"
        )

        if market == "TWSE":

            if yahoo_symbol != (
                f"{symbol}.TW"
            ):
                invalid.append(
                    f"{symbol}:Yahoo"
                )

        elif market == "TPEx":

            if yahoo_symbol != (
                f"{symbol}.TWO"
            ):
                invalid.append(
                    f"{symbol}:Yahoo"
                )

        else:

            invalid.append(
                f"{symbol}:market"
            )

    if invalid:

        raise RuntimeError(
            "Universe 驗證失敗："
            + ", ".join(
                invalid[:20]
            )
        )

    log(
        f"✓ 合法股票："
        f"{len(symbols)}"
    )

    log(
        "✓ 股票代號格式驗證通過"
    )

    log(
        "✓ Yahoo symbol 驗證通過"
    )

    log(
        "✓ 市場分類驗證通過"
    )

    log(
        "✓ Universe 驗證完成"
    )


# ============================================================
# 主程式
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
        # 1. 建立
        # ----------------------------------------------------

        data = build_universe()

        # ----------------------------------------------------
        # 2. 驗證
        # ----------------------------------------------------

        validate_universe(
            data
        )

        # ----------------------------------------------------
        # 3. 寫入
        # ----------------------------------------------------

        save_universe(
            data
        )

        # ----------------------------------------------------
        # 完成
        # ----------------------------------------------------

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
            f"全市場股票："
            f"{data['total']}"
        )

        log(
            f"上市："
            f"{data['listed_stocks']}"
        )

        log(
            f"上櫃："
            f"{data['otc_stocks']}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出："
            f"{UNIVERSE_FILE}"
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

        if UNIVERSE_FILE.exists():

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
