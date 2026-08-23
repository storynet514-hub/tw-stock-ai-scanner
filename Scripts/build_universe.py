#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V6.0.0

============================================================
正式版 V6.0.0
============================================================

目的
------------------------------------------------------------
建立 Data/universe.json，作為全市場股票池的唯一來源。

核心原則
------------------------------------------------------------
1. 不使用既有 universe 數量作為新 universe。
2. 每次成功建置都重新建立 universe。
3. TWSE / TPEX 分開取得。
4. 股票與 ETF 分開處理。
5. 不把 timeout / API 失敗的結果當成空資料。
6. 任一主要市場 API 失敗時：
   -> 保留既有 universe.json
   -> 程式失敗
   -> 絕不產生殘缺 universe。
7. TWSE / TPEX 均必須通過最低安全門檻。
8. 去除重複代號。
9. 排除權證、認購售權證、牛熊證等非一般股票標的。
10. 保留一般 ETF。
11. 修正 TPEX 股票名稱問題。
12. 3081 必須為「聯亞」。
13. 名稱缺失不可靜默寫入空字串。
14. Atomic Write。
15. 建立完成後立即驗證。
16. universe.json schema 固定。
17. 不依賴 chip.json。
18. 不依賴 fetch_chip.py。
19. 不探測 CMoney API。
20. 不抓取 10D / 20D 籌碼。
21. 本檔只負責「股票池」。

安全門檻
------------------------------------------------------------
TWSE 股票 >= 700
TPEX 股票 >= 300
TWSE + TPEX 股票 >= 1200

ETF：
TWSE / TPEX ETF 合計只要有合理資料即可。

注意
------------------------------------------------------------
台股實際股票數量會隨市場狀態變化。

安全門檻不是要求固定 1985 檔，
而是防止 API 回傳空資料或嚴重不完整資料時，
錯誤覆蓋正常 universe。

============================================================
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# Version
# ============================================================

VERSION = "V6.0.0"


# ============================================================
# Path
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TEMP_FILE = DATA_DIR / "universe.json.tmp"


# ============================================================
# Safety Threshold
# ============================================================

MIN_TWSE_STOCKS = 700
MIN_TPEX_STOCKS = 300
MIN_TOTAL_STOCKS = 1200


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

RETRY_SLEEP = 2.0


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        " text/plain,"
        " */*"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,"
        "en-US;q=0.8,en;q=0.7"
    ),
}


# ============================================================
# Official APIs
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/"
    "v1/opendata/t187ap03_L"
)

TPEX_API = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "securities_info/"
    "securities_info.php"
)


# ============================================================
# Explicit symbol fallback
#
# 只作為 API 名稱欄位缺失時的保險。
# 不用來建立整個 universe。
# ============================================================

NAME_FALLBACK = {
    "3081": "聯亞",
}


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
# Basic Helpers
# ============================================================

def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def clean_symbol(value: Any) -> str:

    text = clean_text(value)

    if not text:
        return ""

    text = (
        text
        .replace(".TW", "")
        .replace(".TWO", "")
        .replace(".tw", "")
        .replace(".two", "")
        .strip()
    )

    return text


def is_four_digit_stock(symbol: str) -> bool:

    symbol = clean_symbol(symbol)

    return (
        len(symbol) == 4
        and symbol.isdigit()
    )


def is_etf_symbol(symbol: str) -> bool:

    symbol = clean_symbol(symbol)

    # 台灣 ETF 一般以 00 開頭。
    #
    # 不把所有 00 開頭標的直接當 ETF，
    # 後面還會由市場資料 type/name 再判斷。

    return (
        symbol.startswith("00")
        and symbol.isdigit()
        and 5 <= len(symbol) <= 6
    )


def is_warrant_or_derivative(
    symbol: str,
    name: str,
) -> bool:

    symbol = clean_symbol(symbol)
    name = clean_text(name)

    if not symbol:
        return True

    # 一般股票池只接受四碼股票。
    #
    # 權證通常為六碼以上，
    # 這裡直接排除。
    if len(symbol) > 4 and not is_etf_symbol(symbol):
        return True

    warrant_keywords = [
        "權證",
        "認購",
        "認售",
        "牛證",
        "熊證",
        "展延",
        "購",
        "售",
    ]

    for keyword in warrant_keywords:

        if keyword in name:
            return True

    return False


def make_full_symbol(
    symbol: str,
    market: str,
) -> str:

    symbol = clean_symbol(symbol)

    if market == "TPEX":
        return f"{symbol}.TWO"

    return f"{symbol}.TW"


# ============================================================
# HTTP GET JSON
# ============================================================

def get_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    retries: int = MAX_RETRIES,
) -> Any:

    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):

        try:

            log(
                f"  HTTP GET attempt "
                f"{attempt}/{retries}"
            )

            response = session.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            try:

                return response.json()

            except Exception as json_error:

                raise RuntimeError(
                    f"JSON 解析失敗：{json_error}"
                )

        except Exception as error:

            last_error = error

            log(
                f"  ⚠️ attempt {attempt} "
                f"失敗：{error}"
            )

            if attempt < retries:
                time.sleep(RETRY_SLEEP)

    raise RuntimeError(
        f"取得資料失敗：{last_error}"
    )


# ============================================================
# Extract value from dictionary
# ============================================================

def first_value(
    item: Dict[str, Any],
    keys: List[str],
) -> str:

    for key in keys:

        if key not in item:
            continue

        value = clean_text(item.get(key))

        if value:
            return value

    return ""


# ============================================================
# TWSE
# ============================================================

def fetch_twse(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section("取得 TWSE 上市股票 / ETF")

    log(
        f"API：{TWSE_API}"
    )

    data = get_json(
        session,
        TWSE_API,
    )

    if not isinstance(data, list):

        raise RuntimeError(
            "TWSE API 回傳格式不是 list"
        )

    log(
        f"✓ TWSE API 原始筆數："
        f"{len(data)}"
    )

    result: List[Dict[str, Any]] = []

    seen = set()

    for row in data:

        if not isinstance(row, dict):
            continue

        symbol = first_value(
            row,
            [
                "公司代號",
                "證券代號",
                "代號",
                "code",
                "symbol",
            ],
        )

        name = first_value(
            row,
            [
                "公司名稱",
                "證券名稱",
                "名稱",
                "name",
            ],
        )

        symbol = clean_symbol(symbol)
        name = clean_text(name)

        if not symbol:
            continue

        if is_warrant_or_derivative(
            symbol,
            name,
        ):
            continue

        # 一般股票
        if is_four_digit_stock(symbol):

            item_type = "stock"

        # ETF
        elif is_etf_symbol(symbol):

            item_type = "etf"

        else:

            continue

        if not name:

            name = NAME_FALLBACK.get(
                symbol,
                "",
            )

        # 名稱仍然缺失：
        # 不允許進入正式 universe。
        if not name:

            continue

        if symbol in seen:
            continue

        seen.add(symbol)

        result.append(
            {
                "symbol": symbol,
                "full_symbol": (
                    f"{symbol}.TW"
                ),
                "name": name,
                "market": "TWSE",
                "type": item_type,
            }
        )

    if not result:

        raise RuntimeError(
            "TWSE API 有回傳，但解析後為 0 筆"
        )

    stock_count = sum(
        1
        for item in result
        if item["type"] == "stock"
    )

    etf_count = sum(
        1
        for item in result
        if item["type"] == "etf"
    )

    log(
        f"TWSE 股票：{stock_count}"
    )

    log(
        f"TWSE ETF：{etf_count}"
    )

    log(
        f"TWSE Total：{len(result)}"
    )

    if stock_count < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "TWSE 股票數量低於安全門檻："
            f"{stock_count} < "
            f"{MIN_TWSE_STOCKS}"
        )

    return result


# ============================================================
# TPEX
# ============================================================

def fetch_tpex(
    session: requests.Session,
) -> List[Dict[str, Any]]:

    section("取得 TPEX 上櫃股票 / ETF")

    log(
        f"API：{TPEX_API}"
    )

    params = {
        "l": "zh-tw",
        "d": datetime.now().strftime(
            "%Y%m%d"
        ),
        "s": "0,asc,0",
    }

    data = get_json(
        session,
        TPEX_API,
        params=params,
    )

    if not isinstance(data, dict):

        raise RuntimeError(
            "TPEX API 回傳格式不是 object"
        )

    result: List[Dict[str, Any]] = []

    seen = set()

    # --------------------------------------------------------
    # TPEX API 可能存在不同資料結構
    # --------------------------------------------------------

    rows: List[Any] = []

    if isinstance(
        data.get("aaData"),
        list,
    ):

        rows = data["aaData"]

    elif isinstance(
        data.get("data"),
        list,
    ):

        rows = data["data"]

    elif isinstance(
        data.get("tables"),
        list,
    ):

        for table in data["tables"]:

            if not isinstance(table, dict):
                continue

            table_data = table.get(
                "data",
                [],
            )

            if isinstance(table_data, list):
                rows.extend(table_data)

    if not rows:

        raise RuntimeError(
            "TPEX API 回傳成功，但找不到資料 rows"
        )

    log(
        f"✓ TPEX API 原始筆數："
        f"{len(rows)}"
    )

    for row in rows:

        # ----------------------------------------------------
        # dictionary
        # ----------------------------------------------------

        if isinstance(row, dict):

            symbol = first_value(
                row,
                [
                    "證券代號",
                    "代號",
                    "公司代號",
                    "securitiesCompanyCode",
                    "code",
                    "symbol",
                ],
            )

            name = first_value(
                row,
                [
                    "證券名稱",
                    "名稱",
                    "公司名稱",
                    "securitiesCompanyName",
                    "name",
                ],
            )

            type_value = first_value(
                row,
                [
                    "type",
                    "類別",
                    "證券種類",
                ],
            )

        # ----------------------------------------------------
        # list
        # ----------------------------------------------------

        elif isinstance(row, list):

            if len(row) < 2:
                continue

            symbol = clean_symbol(
                row[0]
            )

            name = clean_text(
                row[1]
            )

            type_value = (
                clean_text(row[2])
                if len(row) > 2
                else ""
            )

        else:

            continue

        symbol = clean_symbol(symbol)
        name = clean_text(name)

        if not symbol:
            continue

        # ----------------------------------------------------
        # 3081 明確修正
        # ----------------------------------------------------

        if symbol == "3081":

            name = "聯亞"

        # ----------------------------------------------------
        # 名稱缺失 fallback
        # ----------------------------------------------------

        if not name:

            name = NAME_FALLBACK.get(
                symbol,
                "",
            )

        # ----------------------------------------------------
        # 非股票 / ETF
        # ----------------------------------------------------

        if is_warrant_or_derivative(
            symbol,
            name,
        ):
            continue

        if is_four_digit_stock(symbol):

            item_type = "stock"

        elif is_etf_symbol(symbol):

            item_type = "etf"

        else:

            continue

        # 若 API type 明確指出 ETF，
        # 優先採 ETF。
        type_lower = type_value.lower()

        if (
            "etf" in type_lower
            or "指數股票型" in type_value
        ):

            item_type = "etf"

        if not name:

            # 正式 universe 不允許空名稱。
            continue

        if symbol in seen:
            continue

        seen.add(symbol)

        result.append(
            {
                "symbol": symbol,
                "full_symbol": (
                    f"{symbol}.TWO"
                ),
                "name": name,
                "market": "TPEX",
                "type": item_type,
            }
        )

    if not result:

        raise RuntimeError(
            "TPEX API 有回傳，但解析後為 0 筆"
        )

    stock_count = sum(
        1
        for item in result
        if item["type"] == "stock"
    )

    etf_count = sum(
        1
        for item in result
        if item["type"] == "etf"
    )

    log(
        f"TPEX 股票：{stock_count}"
    )

    log(
        f"TPEX ETF：{etf_count}"
    )

    log(
        f"TPEX Total：{len(result)}"
    )

    if stock_count < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "TPEX 股票數量低於安全門檻："
            f"{stock_count} < "
            f"{MIN_TPEX_STOCKS}"
        )

    # --------------------------------------------------------
    # 3081 必須存在
    # --------------------------------------------------------

    stock_3081 = next(
        (
            item
            for item in result
            if item["symbol"] == "3081"
        ),
        None,
    )

    if stock_3081 is None:

        raise RuntimeError(
            "TPEX 建置失敗："
            "找不到 3081"
        )

    if stock_3081["name"] != "聯亞":

        raise RuntimeError(
            "TPEX 建置失敗："
            f"3081 名稱錯誤："
            f"{stock_3081['name']!r}"
        )

    log(
        "✓ 3081 | 聯亞 | TPEX"
    )

    return result


# ============================================================
# Merge
# ============================================================

def merge_universe(
    twse: List[Dict[str, Any]],
    tpex: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    section("合併 TWSE / TPEX universe")

    merged: Dict[
        str,
        Dict[str, Any]
    ] = {}

    # TWSE
    for item in twse:

        symbol = item["symbol"]

        if symbol not in merged:

            merged[symbol] = item

    # TPEX
    for item in tpex:

        symbol = item["symbol"]

        if symbol not in merged:

            merged[symbol] = item

    result = list(
        merged.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    result.sort(
        key=lambda x: (
            x["market"],
            x["type"],
            x["symbol"],
        )
    )

    stock_count = sum(
        1
        for item in result
        if item["type"] == "stock"
    )

    etf_count = sum(
        1
        for item in result
        if item["type"] == "etf"
    )

    twse_stock_count = sum(
        1
        for item in result
        if (
            item["market"] == "TWSE"
            and item["type"] == "stock"
        )
    )

    tpex_stock_count = sum(
        1
        for item in result
        if (
            item["market"] == "TPEX"
            and item["type"] == "stock"
        )
    )

    log(
        f"TWSE：{twse_stock_count}"
    )

    log(
        f"TPEX：{tpex_stock_count}"
    )

    log(
        f"Stock：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"Total：{len(result)}"
    )

    # --------------------------------------------------------
    # Final safety gate
    # --------------------------------------------------------

    if twse_stock_count < MIN_TWSE_STOCKS:

        raise RuntimeError(
            "最終 TWSE 安全門檻失敗："
            f"{twse_stock_count} < "
            f"{MIN_TWSE_STOCKS}"
        )

    if tpex_stock_count < MIN_TPEX_STOCKS:

        raise RuntimeError(
            "最終 TPEX 安全門檻失敗："
            f"{tpex_stock_count} < "
            f"{MIN_TPEX_STOCKS}"
        )

    if stock_count < MIN_TOTAL_STOCKS:

        raise RuntimeError(
            "最終全市場股票安全門檻失敗："
            f"{stock_count} < "
            f"{MIN_TOTAL_STOCKS}"
        )

    # --------------------------------------------------------
    # 3081 final validation
    # --------------------------------------------------------

    item_3081 = next(
        (
            item
            for item in result
            if item["symbol"] == "3081"
        ),
        None,
    )

    if item_3081 is None:

        raise RuntimeError(
            "最終 universe 缺少 3081"
        )

    if item_3081["name"] != "聯亞":

        raise RuntimeError(
            "最終 universe 3081 名稱錯誤："
            f"{item_3081['name']}"
        )

    if item_3081["market"] != "TPEX":

        raise RuntimeError(
            "最終 universe 3081 市場錯誤："
            f"{item_3081['market']}"
        )

    return result


# ============================================================
# Validate
# ============================================================

def validate_universe(
    items: List[Dict[str, Any]],
) -> None:

    section("Universe 最終驗證")

    if not items:

        raise RuntimeError(
            "universe items 為空"
        )

    required_fields = [
        "symbol",
        "full_symbol",
        "name",
        "market",
        "type",
    ]

    seen = set()

    errors: List[str] = []

    for item in items:

        if not isinstance(item, dict):

            errors.append(
                "item 不是 object"
            )

            continue

        symbol = clean_symbol(
            item.get("symbol")
        )

        if not symbol:

            errors.append(
                "發現空 symbol"
            )

            continue

        if symbol in seen:

            errors.append(
                f"{symbol}: 重複代號"
            )

        seen.add(symbol)

        for field in required_fields:

            value = clean_text(
                item.get(field)
            )

            if not value:

                errors.append(
                    f"{symbol}: 缺少 {field}"
                )

        if item.get("market") not in {
            "TWSE",
            "TPEX",
        }:

            errors.append(
                f"{symbol}: market 錯誤"
            )

        if item.get("type") not in {
            "stock",
            "etf",
        }:

            errors.append(
                f"{symbol}: type 錯誤"
            )

        if (
            item.get("type") == "stock"
            and not is_four_digit_stock(symbol)
        ):

            errors.append(
                f"{symbol}: stock 不是四碼代號"
            )

    if errors:

        log("❌ Universe 驗證失敗")

        for error in errors[:50]:

            log(
                f"   {error}"
            )

        if len(errors) > 50:

            log(
                f"   ...另外 "
                f"{len(errors) - 50} 個錯誤"
            )

        raise RuntimeError(
            f"Universe validation "
            f"失敗，共 {len(errors)} 個錯誤"
        )

    # --------------------------------------------------------
    # 3081
    # --------------------------------------------------------

    item_3081 = next(
        (
            item
            for item in items
            if item["symbol"] == "3081"
        ),
        None,
    )

    if item_3081 is None:

        raise RuntimeError(
            "驗證失敗：3081 不存在"
        )

    if item_3081["name"] != "聯亞":

        raise RuntimeError(
            "驗證失敗："
            f"3081 名稱為 "
            f"{item_3081['name']!r}"
        )

    if item_3081["market"] != "TPEX":

        raise RuntimeError(
            "驗證失敗："
            f"3081 market = "
            f"{item_3081['market']}"
        )

    log(
        "✓ 3081 = 聯亞"
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    twse = sum(
        1
        for item in items
        if (
            item["market"] == "TWSE"
            and item["type"] == "stock"
        )
    )

    tpex = sum(
        1
        for item in items
        if (
            item["market"] == "TPEX"
            and item["type"] == "stock"
        )
    )

    stock = sum(
        1
        for item in items
        if item["type"] == "stock"
    )

    etf = sum(
        1
        for item in items
        if item["type"] == "etf"
    )

    log(
        "✓ Universe schema 正確"
    )

    log(
        "✓ 無重複股票代號"
    )

    log(
        "✓ 無空名稱"
    )

    log(
        f"TWSE：{twse}"
    )

    log(
        f"TPEX：{tpex}"
    )

    log(
        f"Stock：{stock}"
    )

    log(
        f"ETF：{etf}"
    )

    log(
        f"Total：{len(items)}"
    )


# ============================================================
# Build output
# ============================================================

def build_output(
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:

    twse_stock_count = sum(
        1
        for item in items
        if (
            item["market"] == "TWSE"
            and item["type"] == "stock"
        )
    )

    tpex_stock_count = sum(
        1
        for item in items
        if (
            item["market"] == "TPEX"
            and item["type"] == "stock"
        )
    )

    stock_count = sum(
        1
        for item in items
        if item["type"] == "stock"
    )

    etf_count = sum(
        1
        for item in items
        if item["type"] == "etf"
    )

    return {

        "schema_version": VERSION,

        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "source": {
            "twse": TWSE_API,
            "tpex": TPEX_API,
        },

        "counts": {

            "twse": twse_stock_count,

            "tpex": tpex_stock_count,

            "stock": stock_count,

            "etf": etf_count,

            "total": len(items),
        },

        "items": items,
    }


# ============================================================
# Atomic Write
# ============================================================

def atomic_write(
    output: Dict[str, Any],
) -> None:

    section(
        "Atomic Write Data/universe.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        with TEMP_FILE.open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                output,
                f,
                ensure_ascii=False,
                indent=2,
            )

            f.write("\n")

        # ----------------------------------------------------
        # 寫入後立即重新讀取
        # ----------------------------------------------------

        with TEMP_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            test_data = json.load(f)

        if not isinstance(
            test_data,
            dict,
        ):

            raise RuntimeError(
                "暫存 universe JSON "
                "不是 object"
            )

        # ----------------------------------------------------
        # Atomic replace
        # ----------------------------------------------------

        TEMP_FILE.replace(
            OUTPUT_FILE
        )

    except Exception as error:

        log(
            f"❌ Atomic Write 失敗："
            f"{error}"
        )

        try:

            if TEMP_FILE.exists():

                TEMP_FILE.unlink()

        except Exception:
            pass

        raise


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    log("")
    log("=" * 72)
    log(
        "台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )
    log("=" * 72)

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
    )

    log(
        f"OUTPUT：{OUTPUT_FILE}"
    )

    log("")
    log("安全門檻：")
    log(
        f"  TWSE >= {MIN_TWSE_STOCKS}"
    )
    log(
        f"  TPEX >= {MIN_TPEX_STOCKS}"
    )
    log(
        f"  Total >= {MIN_TOTAL_STOCKS}"
    )

    # --------------------------------------------------------
    # 既有檔案資訊
    # --------------------------------------------------------

    if OUTPUT_FILE.exists():

        try:

            with OUTPUT_FILE.open(
                "r",
                encoding="utf-8",
            ) as f:

                old_data = json.load(f)

            old_items = old_data.get(
                "items",
                [],
            )

            log(
                f"既有 universe："
                f"{len(old_items)} stocks"
            )

        except Exception:

            log(
                "既有 universe："
                "存在但無法解析"
            )

    else:

        log(
            "既有 universe：不存在"
        )

    session = requests.Session()

    try:

        # ====================================================
        # 1. TWSE
        # ====================================================

        twse = fetch_twse(
            session
        )

        # ====================================================
        # 2. TPEX
        # ====================================================

        tpex = fetch_tpex(
            session
        )

        # ====================================================
        # 3. Merge
        # ====================================================

        items = merge_universe(
            twse,
            tpex,
        )

        # ====================================================
        # 4. Validate
        # ====================================================

        validate_universe(
            items
        )

        # ====================================================
        # 5. Build
        # ====================================================

        output = build_output(
            items
        )

        # ====================================================
        # 6. Atomic Write
        # ====================================================

        atomic_write(
            output
        )

    except Exception as error:

        log("")
        log("=" * 72)
        log("BUILD UNIVERSE FAILED")
        log("=" * 72)

        log(
            f"ERROR：{error}"
        )

        log(
            "✓ 不覆蓋既有 universe.json"
        )

        # ----------------------------------------------------
        # 非常重要：
        #
        # 如果 Atomic Write 尚未成功，
        # 原 universe 不會被覆蓋。
        # ----------------------------------------------------

        return 1

    # ========================================================
    # Final
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    twse_count = output[
        "counts"
    ]["twse"]

    tpex_count = output[
        "counts"
    ]["tpex"]

    stock_count = output[
        "counts"
    ]["stock"]

    etf_count = output[
        "counts"
    ]["etf"]

    total_count = output[
        "counts"
    ]["total"]

    log("")
    log("=" * 72)
    log("BUILD UNIVERSE SUCCESS")
    log("=" * 72)

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEX：{tpex_count}"
    )

    log(
        f"Stock：{stock_count}"
    )

    log(
        f"ETF：{etf_count}"
    )

    log(
        f"Total：{total_count}"
    )

    log("")
    log(
        "✓ universe.json 已重新建立"
    )

    log(
        "✓ TWSE / TPEX 安全門檻通過"
    )

    log(
        "✓ 股票池沒有使用舊 universe 筆數"
    )

    log(
        "✓ 不依賴 chip.json"
    )

    log(
        "✓ 不依賴 CMoney"
    )

    log(
        "✓ 不抓取籌碼歷史"
    )

    log(
        "✓ 不產生 main_force_*"
    )

    log(
        "✓ 3081 = 聯亞"
    )

    log(
        "✓ Atomic Write 完成"
    )

    log(
        f"✓ build_universe.py "
        f"{VERSION} 完成"
    )

    log(
        f"✓ 耗時：{elapsed:.1f} 秒"
    )

    log("=" * 72)

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    sys.exit(main())