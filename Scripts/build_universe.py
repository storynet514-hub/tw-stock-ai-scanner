#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.3.0

目的：
1. 建立完整台股 Universe
2. TWSE 上市股票
3. TPEx 上櫃股票
4. TPEx API 失敗時，使用官方 MOPS CSV 備援
5. 絕不因 API 失敗而把有效 universe.json 覆蓋成空檔
6. 只輸出合法股票代號
7. 保留 market / type / name 等基本資訊

輸出：
Data/universe.json
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.3.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
OUTPUT_PATH = DATA_DIR / "universe.json"
TEMP_PATH = DATA_DIR / "universe.json.tmp"

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 安全門檻
# ============================================================

MIN_TWSE = 700
MIN_TPEX = 300
MIN_TOTAL = 1200


# ============================================================
# API
# ============================================================

TWSE_API = (
    "https://openapi.twse.com.tw/v1/opendata/"
    "t187ap03_L"
)

TPEX_API = (
    "https://www.tpex.org.tw/openapi/v1/"
    "mopsfin_t187ap03_O"
)

TPEX_CSV = (
    "https://mopsfin.twse.com.tw/opendata/"
    "t187ap03_O.csv"
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
        "application/json,text/plain,"
        "text/csv,*/*"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# 工具
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def print_header(title: str) -> None:
    print("")
    print("=" * 64)
    print(title)
    print("=" * 64)


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def normalize_code(value: Any) -> str:
    """
    只接受台股股票代號。

    股票代號：
    4~6 位英數字。
    這裡主要保留一般股票，不接受空白、NaN、
    URL、中文或明顯不是股票代號的內容。
    """

    code = clean_text(value).upper()

    if not code:
        return ""

    if code in {
        "NAN",
        "NONE",
        "NULL",
        "NA",
        "N/A",
    }:
        return ""

    # 台股代號通常 4~6 碼
    if not re.fullmatch(r"[A-Z0-9]{4,6}", code):
        return ""

    return code


def safe_get(
    url: str,
    retries: int = 5,
    timeout: int = 30,
) -> requests.Response:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            print(
                f"  HTTP GET attempt "
                f"{attempt}/{retries}"
            )

            response = SESSION.get(
                url,
                timeout=timeout,
                allow_redirects=True,
            )

            print(
                f"  HTTP Status: "
                f"{response.status_code}"
            )

            print(
                f"  Content-Length: "
                f"{len(response.content)} bytes"
            )

            if response.status_code == 200:
                return response

            last_error = RuntimeError(
                f"HTTP {response.status_code}"
            )

            print(
                f"  ⚠️ attempt {attempt} "
                f"失敗：HTTP "
                f"{response.status_code}"
            )

        except Exception as exc:
            last_error = exc

            print(
                f"  ⚠️ attempt {attempt} "
                f"例外：{exc}"
            )

        if attempt < retries:
            time.sleep(2)

    raise RuntimeError(
        f"取得資料失敗：{last_error}"
    )


# ============================================================
# TWSE
# ============================================================

def fetch_twse() -> list[dict[str, Any]]:
    print_header("取得 TWSE 上市股票")

    print(f"主 API：{TWSE_API}")

    response = safe_get(TWSE_API)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"TWSE JSON 解析失敗：{exc}"
        )

    if not isinstance(data, list):
        raise RuntimeError(
            "TWSE API 回傳格式不是 list"
        )

    print(
        f"TWSE JSON 原始解析："
        f"{len(data)} 筆"
    )

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in data:
        if not isinstance(row, dict):
            continue

        code = ""

        # 官方 API 常見欄位
        for key in (
            "公司代號",
            "Code",
            "code",
            "股票代號",
        ):
            if key in row:
                code = normalize_code(row[key])

                if code:
                    break

        if not code:
            continue

        if code in seen:
            continue

        name = ""

        for key in (
            "公司簡稱",
            "公司名稱",
            "Name",
            "name",
        ):
            if key in row:
                name = clean_text(row[key])

                if name:
                    break

        result.append(
            {
                "symbol": f"{code}.TW",
                "code": code,
                "name": name,
                "market": "TWSE",
                "type": "stock",
            }
        )

        seen.add(code)

    print(
        f"✓ TWSE 驗證通過："
        f"{len(result)} 檔"
    )

    if len(result) < MIN_TWSE:
        raise RuntimeError(
            "TWSE 股票數量低於安全門檻："
            f"{len(result)} < {MIN_TWSE}"
        )

    print("✓ TWSE 主 API 成功")

    return result


# ============================================================
# TPEx：官方 OpenAPI
# ============================================================

def parse_tpex_json(
    data: Any,
) -> list[dict[str, Any]]:

    if not isinstance(data, list):
        raise RuntimeError(
            "TPEx API 回傳格式不是 list"
        )

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in data:
        if not isinstance(row, dict):
            continue

        code = ""

        for key in (
            "公司代號",
            "股票代號",
            "Code",
            "code",
            "SecuritiesCompanyCode",
        ):
            if key in row:
                code = normalize_code(row[key])

                if code:
                    break

        if not code:
            continue

        if code in seen:
            continue

        name = ""

        for key in (
            "公司簡稱",
            "公司名稱",
            "名稱",
            "Name",
            "name",
        ):
            if key in row:
                name = clean_text(row[key])

                if name:
                    break

        result.append(
            {
                "symbol": f"{code}.TWO",
                "code": code,
                "name": name,
                "market": "TPEx",
                "type": "stock",
            }
        )

        seen.add(code)

    return result


def fetch_tpex_api() -> list[dict[str, Any]]:
    print_header("TPEx 主 API")

    print(f"API：{TPEX_API}")

    response = safe_get(TPEX_API)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"TPEx JSON 解析失敗：{exc}"
        )

    result = parse_tpex_json(data)

    print(
        f"TPEx API 股票數量："
        f"{len(result)}"
    )

    if len(result) < MIN_TPEX:
        raise RuntimeError(
            "TPEx API 股票數量過低："
            f"{len(result)}"
        )

    print(
        f"✓ TPEx API 驗證通過："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx：官方 MOPS CSV 備援
# ============================================================

def fetch_tpex_csv() -> list[dict[str, Any]]:
    print_header(
        "TPEx 備援：官方 MOPS CSV"
    )

    print(f"CSV：{TPEX_CSV}")

    response = safe_get(TPEX_CSV)

    raw = response.content

    # UTF-8
    text = raw.decode(
        "utf-8-sig",
        errors="replace",
    )

    print(
        f"CSV bytes："
        f"{len(raw)}"
    )

    lines = text.splitlines()

    if not lines:
        raise RuntimeError(
            "TPEx CSV 沒有內容"
        )

    # --------------------------------------------------------
    # 自動找真正 header
    # --------------------------------------------------------

    reader = csv.reader(lines)

    rows = list(reader)

    if not rows:
        raise RuntimeError(
            "TPEx CSV 解析後沒有資料"
        )

    header_index = -1

    for index, row in enumerate(rows[:20]):

        joined = "".join(
            clean_text(x)
            for x in row
        )

        if (
            "公司代號" in joined
            or "股票代號" in joined
        ):
            header_index = index
            break

    if header_index < 0:
        raise RuntimeError(
            "TPEx CSV 找不到公司代號欄位"
        )

    header = [
        clean_text(x)
        for x in rows[header_index]
    ]

    print(
        "CSV header：",
        header[:10],
    )

    # --------------------------------------------------------
    # 找欄位
    # --------------------------------------------------------

    code_index = -1
    name_index = -1

    for i, col in enumerate(header):

        if col in {
            "公司代號",
            "股票代號",
            "證券代號",
        }:
            code_index = i
            break

    for i, col in enumerate(header):

        if col in {
            "公司簡稱",
            "公司名稱",
            "名稱",
        }:
            name_index = i
            break

    if code_index < 0:
        raise RuntimeError(
            "TPEx CSV 找不到公司代號欄位"
        )

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows[header_index + 1:]:

        if not row:
            continue

        if code_index >= len(row):
            continue

        code = normalize_code(
            row[code_index]
        )

        if not code:
            continue

        if code in seen:
            continue

        name = ""

        if (
            name_index >= 0
            and name_index < len(row)
        ):
            name = clean_text(
                row[name_index]
            )

        result.append(
            {
                "symbol": f"{code}.TWO",
                "code": code,
                "name": name,
                "market": "TPEx",
                "type": "stock",
            }
        )

        seen.add(code)

    print(
        f"TPEx CSV 解析："
        f"{len(result)} 檔"
    )

    if len(result) < MIN_TPEX:
        raise RuntimeError(
            "TPEx CSV 股票數量低於安全門檻："
            f"{len(result)} < {MIN_TPEX}"
        )

    print(
        f"✓ TPEx 官方 CSV 備援成功："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# Universe 正規化
# ============================================================

def normalize_universe(
    twse: list[dict[str, Any]],
    tpex: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in twse + tpex:

        symbol = clean_text(
            item.get("symbol")
        )

        code = normalize_code(
            item.get("code")
        )

        market = clean_text(
            item.get("market")
        )

        if not symbol or not code:
            continue

        if market not in {
            "TWSE",
            "TPEx",
        }:
            continue

        if symbol in seen:
            continue

        result.append(
            {
                "symbol": symbol,
                "code": code,
                "name": clean_text(
                    item.get("name")
                ),
                "market": market,
                "type": "stock",
            }
        )

        seen.add(symbol)

    result.sort(
        key=lambda x: (
            x["market"],
            x["code"],
        )
    )

    return result


# ============================================================
# 讀取既有 Universe
# ============================================================

def load_existing_universe() -> dict[str, Any] | None:

    if not OUTPUT_PATH.exists():
        return None

    try:
        with OUTPUT_PATH.open(
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return None

        return data

    except Exception:
        return None


def existing_count(
    data: dict[str, Any] | None,
) -> int:

    if not isinstance(data, dict):
        return 0

    items = data.get("items")

    if not isinstance(items, list):
        return 0

    return len(items)


# ============================================================
# 安全寫入
# ============================================================

def write_universe(
    items: list[dict[str, Any]],
    twse_count: int,
    tpex_count: int,
) -> None:

    listed_stocks = sum(
        1
        for item in items
        if item.get("market") == "TWSE"
    )

    otc_stocks = sum(
        1
        for item in items
        if item.get("market") == "TPEx"
    )

    if listed_stocks < MIN_TWSE:
        raise RuntimeError(
            "安全門檻失敗：TWSE "
            f"{listed_stocks} < {MIN_TWSE}"
        )

    if otc_stocks < MIN_TPEX:
        raise RuntimeError(
            "安全門檻失敗：TPEx "
            f"{otc_stocks} < {MIN_TPEX}"
        )

    if len(items) < MIN_TOTAL:
        raise RuntimeError(
            "安全門檻失敗：Total "
            f"{len(items)} < {MIN_TOTAL}"
        )

    data = {
        "version": VERSION,
        "generated_at": now_iso(),
        "source": (
            "TWSE OpenAPI + "
            "TPEx OpenAPI/MOPS CSV"
        ),
        "market": "TW",
        "total": len(items),
        "listed_stocks": listed_stocks,
        "otc_stocks": otc_stocks,
        "listed_etf": 0,
        "otc_etf": 0,
        "items": items,
    }

    with TEMP_PATH.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        f.write("\n")

    # --------------------------------------------------------
    # 寫入後再次驗證
    # --------------------------------------------------------

    with TEMP_PATH.open(
        "r",
        encoding="utf-8",
    ) as f:

        verify = json.load(f)

    verify_items = verify.get("items")

    if not isinstance(
        verify_items,
        list,
    ):
        raise RuntimeError(
            "寫入後驗證失敗：items 不是 list"
        )

    if len(verify_items) != len(items):
        raise RuntimeError(
            "寫入後驗證失敗："
            "股票數量不一致"
        )

    if verify.get("total") != len(items):
        raise RuntimeError(
            "寫入後驗證失敗：total 不一致"
        )

    # --------------------------------------------------------
    # 原子替換
    # --------------------------------------------------------

    TEMP_PATH.replace(OUTPUT_PATH)


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    print("")
    print("=" * 64)
    print(
        "台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )
    print("=" * 64)

    print(
        f"BASE_DIR：{BASE_DIR}"
    )

    print(
        f"DATA_DIR：{DATA_DIR}"
    )

    print(
        f"OUTPUT：{OUTPUT_PATH}"
    )

    print("")
    print("安全門檻：")
    print(f"  TWSE >= {MIN_TWSE}")
    print(f"  TPEx >= {MIN_TPEX}")
    print(f"  Total >= {MIN_TOTAL}")

    # --------------------------------------------------------
    # 讀取既有檔案
    # --------------------------------------------------------

    old_data = load_existing_universe()
    old_count = existing_count(old_data)

    print("")
    print(
        f"既有 universe："
        f"{old_count} stocks"
    )

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    try:

        twse = fetch_twse()

    except Exception as exc:

        print_header(
            "BUILD UNIVERSE FAILED"
        )

        print(
            f"ERROR：TWSE 取得失敗："
            f"{exc}"
        )

        print(
            "✓ 不覆蓋既有 "
            "universe.json"
        )

        return 1

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    tpex: list[dict[str, Any]] = []

    try:

        tpex = fetch_tpex_api()

    except Exception as exc:

        print("")
        print(
            f"⚠️ TPEx 主 API 失敗："
            f"{exc}"
        )

        print(
            "→ 啟用官方 MOPS CSV 備援"
        )

        try:

            tpex = fetch_tpex_csv()

        except Exception as csv_exc:

            print_header(
                "BUILD UNIVERSE FAILED"
            )

            print(
                "ERROR："
                "TPEx API 與官方 CSV "
                "備援均失敗"
            )

            print(
                f"CSV ERROR：{csv_exc}"
            )

            print(
                "✓ 不覆蓋既有 "
                "universe.json"
            )

            return 1

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    print_header(
        "建立 Universe"
    )

    items = normalize_universe(
        twse,
        tpex,
    )

    twse_count = sum(
        1
        for item in items
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in items
        if item["market"] == "TPEx"
    )

    total = len(items)

    print(
        f"TWSE：{twse_count}"
    )

    print(
        f"TPEx：{tpex_count}"
    )

    print(
        f"Total：{total}"
    )

    # --------------------------------------------------------
    # 安全門檻
    # --------------------------------------------------------

    if twse_count < MIN_TWSE:
        print(
            "❌ TWSE 安全門檻失敗"
        )

        return 1

    if tpex_count < MIN_TPEX:
        print(
            "❌ TPEx 安全門檻失敗"
        )

        return 1

    if total < MIN_TOTAL:
        print(
            "❌ Total 安全門檻失敗"
        )

        return 1

    # --------------------------------------------------------
    # 寫檔
    # --------------------------------------------------------

    print_header(
        "寫入 Data/universe.json"
    )

    try:

        write_universe(
            items,
            twse_count,
            tpex_count,
        )

    except Exception as exc:

        print(
            f"❌ 寫入失敗：{exc}"
        )

        if TEMP_PATH.exists():
            try:
                TEMP_PATH.unlink()
            except Exception:
                pass

        return 1

    # --------------------------------------------------------
    # 最終驗證
    # --------------------------------------------------------

    print_header(
        "Universe 最終驗證"
    )

    with OUTPUT_PATH.open(
        "r",
        encoding="utf-8",
    ) as f:

        final_data = json.load(f)

    final_items = final_data.get(
        "items"
    )

    if not isinstance(
        final_items,
        list,
    ):
        print(
            "❌ 最終 items 不是 list"
        )

        return 1

    if len(final_items) <= 0:
        print(
            "❌ 最終 Universe 為空"
        )

        return 1

    print(
        f"✓ Version："
        f"{final_data.get('version')}"
    )

    print(
        f"✓ TWSE："
        f"{final_data.get('listed_stocks')}"
    )

    print(
        f"✓ TPEx："
        f"{final_data.get('otc_stocks')}"
    )

    print(
        f"✓ Total："
        f"{final_data.get('total')}"
    )

    print(
        f"✓ items："
        f"{len(final_items)}"
    )

    print(
        f"✓ Output："
        f"{OUTPUT_PATH}"
    )

    print_header(
        "BUILD UNIVERSE SUCCESS"
    )

    print(
        f"✓ Universe：{total}"
    )

    print(
        f"✓ TWSE：{twse_count}"
    )

    print(
        f"✓ TPEx：{tpex_count}"
    )

    print(
        "✓ Data/universe.json "
        "已成功建立"
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:

        print(
            "\n❌ 使用者中止"
        )

        sys.exit(130)

    except Exception as exc:

        print("")
        print("=" * 64)
        print("BUILD UNIVERSE UNEXPECTED ERROR")
        print("=" * 64)

        print(
            f"ERROR：{exc}"
        )

        print(
            "✓ 不覆蓋既有 "
            "Data/universe.json"
        )

        sys.exit(1)