#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V2.0

============================================================
責任
============================================================

建立台股全市場 Universe：

1. TWSE 上市普通股票
2. TPEx 上櫃普通股票
3. 排除 ETF
4. 排除權證
5. 排除特別股及非普通股票
6. 正規化股票代號
7. 輸出 Data/universe.json

============================================================
資料來源
============================================================

TWSE:
https://openapi.twse.com.tw/v1/opendata/t187ap03_L

TPEx:
https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V2.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
OUTPUT_FILE = DATA_DIR / "universe.json"

TWSE_URL = (
    "https://openapi.twse.com.tw/v1/opendata/"
    "t187ap03_L"
)

TPEX_URL = (
    "https://www.tpex.org.tw/openapi/v1/"
    "mopsfin_t187ap03_O"
)

TIMEOUT = 30


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
# HTTP
# ============================================================

def get_json(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# 找欄位
# ============================================================

def find_value(row, keys):
    """
    不依賴單一固定欄位名稱。
    """

    if not isinstance(row, dict):
        return None

    # 完全匹配
    for key in keys:
        if key in row:
            value = row.get(key)

            if value is not None:
                value = str(value).strip()

                if value:
                    return value

    # 忽略大小寫、底線、空白
    normalized = {}

    for key, value in row.items():

        normalized_key = re.sub(
            r"[\s_\-]",
            "",
            str(key).lower()
        )

        normalized[normalized_key] = value

    for key in keys:

        normalized_key = re.sub(
            r"[\s_\-]",
            "",
            str(key).lower()
        )

        if normalized_key in normalized:

            value = normalized[normalized_key]

            if value is not None:

                value = str(value).strip()

                if value:
                    return value

    return None


# ============================================================
# 股票代號判斷
# ============================================================

def normalize_code(value):
    if value is None:
        return None

    value = str(value).strip()

    # 去除可能的小數點
    if value.endswith(".0"):
        value = value[:-2]

    # 台股普通股票代號主要為 4 碼
    if re.fullmatch(r"\d{4}", value):
        return value

    return None


# ============================================================
# 排除非普通股票
# ============================================================

def is_normal_stock(row):
    """
    盡量保守。

    Universe 目標：
    普通上市 / 上櫃股票。

    排除：
    ETF
    ETN
    權證
    特別股
    受益證券
    債券
    存託憑證以外的特殊商品
    """

    if not isinstance(row, dict):
        return False

    text_parts = []

    for key, value in row.items():

        if value is None:
            continue

        text_parts.append(
            str(value).strip().upper()
        )

    text = " ".join(text_parts)

    # --------------------------------------------------------
    # 商品名稱排除
    # --------------------------------------------------------

    exclude_words = [
        "ETF",
        "ETN",
        "權證",
        "認購權證",
        "認售權證",
        "受益證券",
        "特別股",
        "公司債",
        "債券",
        "轉換公司債",
        "可轉換公司債",
        "存託憑證",
    ]

    for word in exclude_words:

        if word.upper() in text:
            return False

    return True


# ============================================================
# 建立股票資料
# ============================================================

def make_record(
    row,
    market
):

    code = find_value(
        row,
        [
            "公司代號",
            "有價證券代號",
            "證券代號",
            "股票代號",
            "代號",
            "SecuritiesCompanyCode",
            "CompanyCode",
            "Code",
            "code",
        ]
    )

    code = normalize_code(code)

    if not code:
        return None

    if not is_normal_stock(row):
        return None

    name = find_value(
        row,
        [
            "公司名稱",
            "有價證券名稱",
            "證券名稱",
            "股票名稱",
            "名稱",
            "CompanyName",
            "SecuritiesCompanyName",
            "Name",
            "name",
        ]
    )

    industry = find_value(
        row,
        [
            "產業別",
            "產業",
            "industry",
            "Industry",
            "industryName",
        ]
    )

    yahoo_symbol = code + (
        ".TW"
        if market == "TWSE"
        else ".TWO"
    )

    return {
        "symbol": code,
        "yahoo_symbol": yahoo_symbol,
        "name": name or "",
        "market": (
            "上市"
            if market == "TWSE"
            else "上櫃"
        ),
        "market_code": market,
        "industry": industry or "",
    }


# ============================================================
# TWSE
# ============================================================

def fetch_twse():

    section("取得 TWSE 上市股票")

    log(f"API：{TWSE_URL}")

    data = get_json(TWSE_URL)

    if not isinstance(data, list):
        raise RuntimeError(
            "TWSE API 回傳格式不是 list"
        )

    log(
        f"TWSE API 原始資料：{len(data)} 筆"
    )

    records = {}

    for row in data:

        record = make_record(
            row,
            "TWSE"
        )

        if record:
            records[
                record["symbol"]
            ] = record

    log(
        f"TWSE 合法普通股票：{len(records)}"
    )

    if not records:
        raise RuntimeError(
            "TWSE 沒有解析出任何合法股票"
        )

    return records


# ============================================================
# TPEx
# ============================================================

def fetch_tpex():

    section("取得 TPEx 上櫃股票")

    log(f"API：{TPEX_URL}")

    data = get_json(TPEX_URL)

    if not isinstance(data, list):
        raise RuntimeError(
            "TPEx API 回傳格式不是 list"
        )

    log(
        f"TPEx API 原始資料：{len(data)} 筆"
    )

    records = {}

    # --------------------------------------------------------
    # 第一階段：正常解析
    # --------------------------------------------------------

    for row in data:

        record = make_record(
            row,
            "TPEX"
        )

        if record:
            records[
                record["symbol"]
            ] = record

    log(
        f"TPEx 第一階段解析：{len(records)}"
    )

    # --------------------------------------------------------
    # 第二階段：如果 API 欄位格式特殊
    # 使用更寬鬆的代號搜尋。
    # --------------------------------------------------------

    if not records:

        log("")
        log(
            "⚠️ TPEx 固定欄位解析為 0"
        )

        log(
            "啟用 TPEx 寬鬆解析模式"
        )

        for row in data:

            if not isinstance(row, dict):
                continue

            code = None

            # 搜尋所有欄位
            for key, value in row.items():

                if value is None:
                    continue

                candidate = str(
                    value
                ).strip()

                # 找 4 碼股票代號
                if re.fullmatch(
                    r"\d{4}",
                    candidate
                ):

                    # 優先選擇欄位名稱含代號
                    key_text = str(
                        key
                    ).lower()

                    if any(
                        word in key_text
                        for word in [
                            "代號",
                            "code",
                            "symbol",
                            "securities"
                        ]
                    ):
                        code = candidate
                        break

            if not code:
                continue

            if not is_normal_stock(row):
                continue

            name = ""

            for key, value in row.items():

                if value is None:
                    continue

                key_text = str(
                    key
                ).lower()

                if any(
                    word in key_text
                    for word in [
                        "名稱",
                        "name"
                    ]
                ):

                    name = str(
                        value
                    ).strip()

                    if name:
                        break

            record = {
                "symbol": code,
                "yahoo_symbol": (
                    code + ".TWO"
                ),
                "name": name,
                "market": "上櫃",
                "market_code": "TPEX",
                "industry": "",
            }

            records[code] = record

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    log(
        f"TPEx 合法普通股票：{len(records)}"
    )

    if not records:

        # 額外印出第一筆資料方便排錯
        if data:

            log("")
            log(
                "TPEx 第一筆 API 資料欄位："
            )

            first = data[0]

            if isinstance(first, dict):

                for key, value in first.items():

                    log(
                        f"  {key}: {value}"
                    )

        raise RuntimeError(
            "TPEx 沒有解析出任何合法股票"
        )

    return records


# ============================================================
# 建立 Universe
# ============================================================

def build_universe():

    section("建立台股全市場 Universe")

    twse = fetch_twse()

    tpex = fetch_tpex()

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    all_stocks = {}

    for code, record in twse.items():
        all_stocks[code] = record

    for code, record in tpex.items():

        if code not in all_stocks:
            all_stocks[code] = record

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    all_stocks = dict(
        sorted(
            all_stocks.items(),
            key=lambda item: item[0]
        )
    )

    listed_count = len(twse)
    otc_count = len(tpex)
    total = len(all_stocks)

    # --------------------------------------------------------
    # 基本安全檢查
    # --------------------------------------------------------

    if total < 1000:

        raise RuntimeError(
            "Universe 股票數量異常偏低："
            f"{total}"
        )

    # --------------------------------------------------------
    # 輸出
    # --------------------------------------------------------

    output = {
        "version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "source": [
            TWSE_URL,
            TPEX_URL
        ],
        "market": "TW",
        "total": total,
        "listed_stocks": listed_count,
        "otc_stocks": otc_count,
        "listed_etf": 0,
        "otc_etf": 0,
        "items": list(
            all_stocks.values()
        )
    }

    return output


# ============================================================
# 儲存
# ============================================================

def save_universe(data):

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
            indent=2
        )

    temp_file.replace(
        OUTPUT_FILE
    )

    log("")
    log(
        f"✓ Universe 寫入成功："
        f"{OUTPUT_FILE}"
    )

    log(
        f"檔案大小："
        f"{OUTPUT_FILE.stat().st_size / 1024:.1f} KB"
    )


# ============================================================
# 主程式
# ============================================================

def main():

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

        data = build_universe()

        save_universe(data)

        section("Universe 建立完成")

        log(
            f"上市普通股票："
            f"{data['listed_stocks']}"
        )

        log(
            f"上櫃普通股票："
            f"{data['otc_stocks']}"
        )

        log(
            f"台股全市場股票："
            f"{data['total']}"
        )

        log("")
        log("前 10 檔：")

        for item in data["items"][:10]:

            log(
                f"  {item['symbol']} "
                f"{item['name']} "
                f"[{item['market']}]"
            )

        log("")
        log("=" * 64)
        log(
            "✓ build_universe.py 執行完成"
        )
        log("=" * 64)

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

        log(
            "⚠️ 保留原有 universe.json"
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
