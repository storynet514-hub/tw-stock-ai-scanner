#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股 AI 選股系統
build_universe.py V2.0
============================================================

用途：
    自動建立「台股全市場普通股 Universe」。

市場：
    1. TWSE 上市普通股
    2. TPEx 上櫃普通股

不包含：
    - ETF
    - ETN
    - 權證
    - 特別股
    - 受益證券
    - 債券
    - 其他非普通股商品
    - 港股
    - 美股

輸出：
    Data/universe.json

輸出格式：

{
        "schema_version": "2.0",
        "market": "TW",
        "scope": "ALL_STOCKS",
        "stocks": [
            {
                "code": "2330.TW",
                "name": "...",
                "market": "TWSE"
            },
            {
                "code": "3081.TWO",
                "name": "...",
                "market": "TPEX"
            }
        ]
}

重要原則：
    - 不手動指定股票
    - 不使用固定股票清單
    - 不加入 PENDING / TW / 0.0 等錯誤資料
    - 官方來源失敗時停止，不覆蓋既有 Universe
    - 只有驗證通過才寫入 universe.json
============================================================
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V2.0"

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json,text/plain,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


session = requests.Session()

session.headers.update(HEADERS)


# ============================================================
# 時間
# ============================================================

def now_tw() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Taipei")
        ).strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        return datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )


# ============================================================
# HTTP
# ============================================================

def request_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[str]:

    try:

        response = session.get(
            url,
            params=params,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        response.encoding = response.apparent_encoding or "utf-8"

        return response.text

    except Exception as exc:

        print(
            f"   ❌ HTTP 取得失敗：{exc}"
        )

        return None


# ============================================================
# 股票代號驗證
# ============================================================

def valid_stock_code(code: Any) -> bool:

    if code is None:
        return False

    code = str(code).strip()

    # 台股普通股代號目前以 4 碼為核心範圍
    if not re.fullmatch(r"\d{4}", code):
        return False

    # 明確排除不應進入普通股 Universe 的常見區段
    if code.startswith("00"):
        return False

    return True


# ============================================================
# 名稱清理
# ============================================================

def clean_name(value: Any) -> str:

    if value is None:
        return ""

    text = str(value)

    text = (
        text.replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# TWSE
# ============================================================

def fetch_twse_stocks() -> List[Dict[str, Any]]:
    """
    取得 TWSE 上市普通股。

    使用 TWSE ISIN Listed Equities 清單。

    官方頁面：
    isin.twse.com.tw
    """

    print("")
    print("=" * 64)
    print("取得 TWSE 上市普通股")
    print("=" * 64)

    url = (
        "https://isin.twse.com.tw/"
        "isin/C_public.jsp"
    )

    params = {
        "strMode": "2",
    }

    html = request_text(
        url,
        params=params,
    )

    if not html:

        print(
            "❌ TWSE 股票清單取得失敗"
        )

        return []

    rows = re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    result: List[Dict[str, Any]] = []

    seen = set()

    for row in rows:

        cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            row,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if not cells:
            continue

        cleaned = []

        for cell in cells:

            text = re.sub(
                r"<[^>]+>",
                "",
                cell,
            )

            text = (
                text
                .replace("&nbsp;", " ")
                .replace("&#160;", " ")
            )

            text = clean_name(text)

            cleaned.append(text)

        if len(cleaned) < 4:
            continue

        # ----------------------------------------------------
        # 第一欄通常包含：
        #
        # 1101　台泥
        #
        # 以開頭 4 碼取得股票代號
        # ----------------------------------------------------

        match = re.match(
            r"^(\d{4})\s*(.*)$",
            cleaned[0],
        )

        if not match:
            continue

        code = match.group(1)

        name_from_first = clean_name(
            match.group(2)
        )

        # ----------------------------------------------------
        # 必須是 TWSE LISTED
        # ----------------------------------------------------

        row_text = " ".join(cleaned)

        if "TWSE LISTED" not in row_text.upper():
            continue

        # ----------------------------------------------------
        # 必須是 STOCKS / 普通股
        #
        # ETF / ETN / 基金等會被排除
        # ----------------------------------------------------

        if "ETF" in row_text.upper():
            continue

        if "ETN" in row_text.upper():
            continue

        if "WARRANT" in row_text.upper():
            continue

        if "BOND" in row_text.upper():
            continue

        if "FUND" in row_text.upper():
            continue

        if not valid_stock_code(code):
            continue

        # ----------------------------------------------------
        # 股票名稱
        # ----------------------------------------------------

        name = name_from_first

        if not name and len(cleaned) >= 2:
            name = cleaned[1]

        if code in seen:
            continue

        seen.add(code)

        result.append(
            {
                "code": f"{code}.TW",
                "name": name,
                "market": "TWSE",
            }
        )

    print(
        f"TWSE 普通股：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx
# ============================================================

def fetch_tpex_stocks() -> List[Dict[str, Any]]:
    """
    取得 TPEx 上櫃普通股。

    使用 TPEx Mainboard 公司清單。
    """

    print("")
    print("=" * 64)
    print("取得 TPEx 上櫃普通股")
    print("=" * 64)

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/aftertrading/"
        "company.php"
    )

    params = {
        "l": "zh-tw",
    }

    html = request_text(
        url,
        params=params,
    )

    if not html:

        print(
            "⚠️ TPEx 第一來源無法取得"
        )

        return fetch_tpex_from_page()


    result = parse_tpex_html(html)

    if result:

        print(
            f"TPEx 普通股：{len(result)} 檔"
        )

        return result

    print(
        "⚠️ TPEx 第一來源解析不到資料"
    )

    return fetch_tpex_from_page()


# ============================================================
# TPEx 備援
# ============================================================

def fetch_tpex_from_page() -> List[Dict[str, Any]]:
    """
    TPEx Mainboard 官方頁面備援來源。
    """

    url = (
        "https://www.tpex.org.tw/"
        "en-us/mainboard/listed/company.html"
    )

    html = request_text(url)

    if not html:

        print(
            "❌ TPEx 官方資料無法取得"
        )

        return []

    result = parse_tpex_html(html)

    print(
        f"TPEx 普通股：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx HTML parser
# ============================================================

def parse_tpex_html(
    html: str,
) -> List[Dict[str, Any]]:

    result: List[Dict[str, Any]] = []

    seen = set()

    # --------------------------------------------------------
    # 先嘗試表格
    # --------------------------------------------------------

    rows = re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for row in rows:

        cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            row,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if not cells:
            continue

        cleaned = []

        for cell in cells:

            text = re.sub(
                r"<[^>]+>",
                "",
                cell,
            )

            text = (
                text
                .replace("&nbsp;", " ")
                .replace("&#160;", " ")
            )

            cleaned.append(
                clean_name(text)
            )

        if not cleaned:
            continue

        code = None
        code_index = None

        for index, value in enumerate(cleaned):

            match = re.search(
                r"\b(\d{4})\b",
                value,
            )

            if match:

                candidate = match.group(1)

                if valid_stock_code(candidate):

                    code = candidate
                    code_index = index
                    break

        if not code:
            continue

        # ----------------------------------------------------
        # 排除非普通股商品
        # ----------------------------------------------------

        row_text = " ".join(cleaned).upper()

        if "ETF" in row_text:
            continue

        if "ETN" in row_text:
            continue

        if "WARRANT" in row_text:
            continue

        if "BOND" in row_text:
            continue

        if "FUND" in row_text:
            continue

        # ----------------------------------------------------
        # 股票名稱
        # ----------------------------------------------------

        name = ""

        for index, value in enumerate(cleaned):

            if index == code_index:
                continue

            if not value:
                continue

            if re.fullmatch(
                r"\d{4}",
                value,
            ):
                continue

            # 排除明顯不是名稱的欄位
            if value.lower() in {
                "tpex",
                "listed",
                "mainboard",
            }:
                continue

            name = value

            break

        if code in seen:
            continue

        seen.add(code)

        result.append(
            {
                "code": f"{code}.TWO",
                "name": name,
                "market": "TPEX",
            }
        )

    return result


# ============================================================
# Universe 驗證
# ============================================================

def validate_universe(
    stocks: List[Dict[str, Any]],
) -> bool:

    if not stocks:

        print(
            "❌ Universe 為空"
        )

        return False

    codes = set()

    invalid = []

    for stock in stocks:

        code = stock.get("code")

        if not isinstance(code, str):

            invalid.append(
                str(code)
            )

            continue

        if not re.fullmatch(
            r"\d{4}\.(TW|TWO)",
            code,
        ):

            invalid.append(code)

            continue

        if code in codes:

            invalid.append(code)

            continue

        codes.add(code)

    if invalid:

        print("")
        print(
            "❌ Universe 驗證失敗"
        )

        print(
            f"錯誤資料數：{len(invalid)}"
        )

        for item in invalid[:20]:
            print(
                f"   {item}"
            )

        return False

    twse_count = sum(
        1
        for stock in stocks
        if stock["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for stock in stocks
        if stock["market"] == "TPEX"
    )

    print("")
    print("=" * 64)
    print("Universe 驗證")
    print("=" * 64)

    print(
        f"總股票數：{len(stocks)}"
    )

    print(
        f"TWSE：{twse_count}"
    )

    print(
        f"TPEx：{tpex_count}"
    )

    # --------------------------------------------------------
    # 防止錯誤 API 回傳少量垃圾資料
    # --------------------------------------------------------

    if len(stocks) < 1000:

        print("")
        print(
            "❌ Universe 股票數異常偏低"
        )

        print(
            "❌ 為避免覆蓋正確 Universe，停止寫入。"
        )

        return False

    print(
        "✅ Universe 結構驗證通過"
    )

    return True


# ============================================================
# 儲存
# ============================================================

def save_universe(
    stocks: List[Dict[str, Any]],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stocks = sorted(
        stocks,
        key=lambda item: (
            item["market"],
            item["code"],
        ),
    )

    output = {
        "schema_version": "2.0",
        "version": VERSION,
        "market": "TW",
        "scope": "ALL_STOCKS",
        "description": (
            "台股全市場普通股 Universe"
        ),
        "updated_at": now_tw(),
        "count": len(stocks),
        "statistics": {
            "TWSE": sum(
                1
                for stock in stocks
                if stock["market"] == "TWSE"
            ),
            "TPEX": sum(
                1
                for stock in stocks
                if stock["market"] == "TPEX"
            ),
        },
        "stocks": stocks,
    }

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

    os.replace(
        temp_file,
        OUTPUT_FILE,
    )


# ============================================================
# Main
# ============================================================

def main() -> int:

    print("")
    print("=" * 70)
    print(
        f"台股 AI 選股系統 "
        f"build_universe.py {VERSION}"
    )
    print("=" * 70)

    print(
        f"開始時間：{now_tw()}"
    )

    print("")
    print(
        "建立範圍：台股全市場普通股"
    )

    print(
        "包含：TWSE + TPEx"
    )

    print(
        "排除：ETF / ETN / 權證 / 債券 / 基金"
    )

    # ========================================================
    # 1. TWSE
    # ========================================================

    twse = fetch_twse_stocks()

    if not twse:

        print("")
        print(
            "❌ TWSE Universe 建立失敗"
        )

        return 1

    # ========================================================
    # 2. TPEx
    # ========================================================

    tpex = fetch_tpex_stocks()

    if not tpex:

        print("")
        print(
            "❌ TPEx Universe 建立失敗"
        )

        return 1

    # ========================================================
    # 3. 合併
    # ========================================================

    combined: Dict[str, Dict[str, Any]] = {}

    for stock in twse + tpex:

        code = stock["code"]

        if code not in combined:

            combined[code] = stock

    stocks = list(
        combined.values()
    )

    # ========================================================
    # 4. 驗證
    # ========================================================

    if not validate_universe(stocks):

        return 1

    # ========================================================
    # 5. 儲存
    # ========================================================

    try:

        save_universe(stocks)

    except Exception as exc:

        print("")
        print(
            f"❌ Universe 寫入失敗：{exc}"
        )

        return 1

    # ========================================================
    # 6. 最終結果
    # ========================================================

    print("")
    print("=" * 70)
    print("Universe 建立完成")
    print("=" * 70)

    print(
        f"輸出：{OUTPUT_FILE}"
    )

    print(
        f"股票總數：{len(stocks)}"
    )

    print(
        f"TWSE：{len(twse)}"
    )

    print(
        f"TPEx：{len(tpex)}"
    )

    print("")
    print(
        "前 20 檔："
    )

    for index, stock in enumerate(
        stocks[:20],
        start=1,
    ):

        print(
            f"  {index:02d}. "
            f"{stock['code']} "
            f"{stock['name']} "
            f"[{stock['market']}]"
        )

    print("")
    print(
        f"完成時間：{now_tw()}"
    )

    print(
        f"build_universe.py {VERSION} 完成"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
