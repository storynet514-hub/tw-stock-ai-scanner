#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V4.0

============================================================
功能
============================================================

建立「台股全市場普通股票 Universe」

資料來源：

1. TWSE 上市股票
2. TPEx 上櫃股票

輸出：

Data/universe.json

============================================================
重要設計
============================================================

本程式：

✓ 不使用固定股票清單
✓ 不只抓 14 檔
✓ 不只抓上市
✓ 不只抓上櫃
✓ 自動建立全市場股票 Universe
✓ 自動轉換 Yahoo Finance symbol
✓ 自動排除 ETF / ETN / 權證 / 債券等非普通股票
✓ TPEx JSON API 失敗時自動嘗試 CSV
✓ API 欄位名稱變動時使用多組候選欄位
✓ 資料異常時不覆蓋既有 universe.json
"""

import csv
import io
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

VERSION = "V4.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "universe.json"

TWSE_API = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
)

TPEX_API = (
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
)

# 政府資料開放平台 / MOPS CSV fallback
TPEX_CSV_URLS = [
    "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O.csv",
]

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

# 最低安全門檻
MIN_TWSE = 700
MIN_TPEX = 300
MIN_TOTAL = 1200


# ============================================================
# HTTP Header
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,text/plain,text/csv,"
        "application/csv,*/*"
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

def http_get(url):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            log(
                f"  HTTP GET attempt "
                f"{attempt}/{MAX_RETRIES}"
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

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠ HTTP 取得失敗：{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"HTTP GET 失敗：{url} | {last_error}"
    )


# ============================================================
# 清理字串
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


# ============================================================
# 找欄位
# ============================================================

def find_value(record, candidates):
    """
    支援：

    公司代號
    公司代码
    股票代號
    股票代碼
    代號
    code
    symbol
    ticker

    等不同欄位名稱。
    """

    if not isinstance(record, dict):
        return ""

    normalized = {}

    for key, value in record.items():

        key_clean = clean_text(key)

        normalized[key_clean] = value

        normalized[
            key_clean.lower()
        ] = value

    # 精確比對
    for candidate in candidates:

        candidate = clean_text(candidate)

        if candidate in normalized:
            return clean_text(
                normalized[candidate]
            )

        if candidate.lower() in normalized:
            return clean_text(
                normalized[candidate.lower()]
            )

    # 模糊比對
    for key, value in record.items():

        key_text = clean_text(key).lower()

        for candidate in candidates:

            candidate_text = (
                clean_text(candidate).lower()
            )

            if candidate_text == key_text:
                return clean_text(value)

    return ""


# ============================================================
# 股票代號判斷
# ============================================================

def normalize_code(value):
    """
    台股普通股票主要使用 4 碼代號。

    允許：
    4 碼數字
    少數特殊 5 碼 / 6 碼證券代號

    但排除：
    空值
    非數字
    太短
    明顯權證格式
    """

    value = clean_text(value)

    if not value:
        return None

    # 去除可能出現的空白
    value = value.replace(" ", "")

    # 純數字
    if not re.fullmatch(r"\d{4,6}", value):
        return None

    # 主要普通股票範圍
    if len(value) == 4:
        return value

    # 部分市場特殊代號保留
    if len(value) in (5, 6):
        return value

    return None


# ============================================================
# 排除非普通股票
# ============================================================

def is_excluded_security(record, code, name):
    """
    排除 ETF / ETN / 權證 / 債券等。

    不用只依代號判斷。
    同時檢查名稱、證券種類等欄位。
    """

    text_parts = [
        clean_text(name),
        find_value(
            record,
            [
                "證券種類",
                "證券類別",
                "商品類型",
                "種類",
                "有價證券種類",
                "type",
                "security_type",
            ]
        ),
        find_value(
            record,
            [
                "英文簡稱",
                "英文名稱",
                "English Name",
            ]
        ),
    ]

    text = " ".join(
        text_parts
    ).upper()

    # 中文排除關鍵字
    excluded_keywords = [
        "ETF",
        "ETN",
        "ETP",
        "權證",
        "認購權證",
        "認售權證",
        "牛證",
        "熊證",
        "債券",
        "公司債",
        "轉換公司債",
        "可轉債",
        "存託憑證",
        "受益證券",
        "特別股",
        "特別股權",
        "基金",
    ]

    for keyword in excluded_keywords:

        if keyword.upper() in text:
            return True

    # 權證常見代號通常不是普通 4 碼股票
    # 但這裡不直接排除所有特殊代號，
    # 避免誤殺正常股票。

    return False


# ============================================================
# 建立股票紀錄
# ============================================================

def make_record(
    record,
    market,
    source
):
    if not isinstance(record, dict):
        return None

    code = find_value(
        record,
        [
            "公司代號",
            "公司代碼",
            "股票代號",
            "股票代碼",
            "證券代號",
            "證券代碼",
            "代號",
            "代碼",
            "code",
            "Code",
            "symbol",
            "Symbol",
            "ticker",
            "Ticker",
        ]
    )

    code = normalize_code(code)

    if not code:
        return None

    name = find_value(
        record,
        [
            "公司簡稱",
            "公司名称",
            "公司名稱",
            "股票名稱",
            "股票名稱",
            "證券名稱",
            "名稱",
            "name",
            "Name",
        ]
    )

    if not name:
        name = find_value(
            record,
            [
                "公司全稱",
                "公司全名",
                "full_name",
            ]
        )

    name = clean_text(name)

    if is_excluded_security(
        record,
        code,
        name
    ):
        return None

    if market == "TWSE":
        yahoo_symbol = f"{code}.TW"
    else:
        yahoo_symbol = f"{code}.TWO"

    return {
        "code": code,
        "symbol": code,
        "yahoo_symbol": yahoo_symbol,
        "name": name,
        "market": market,
        "source": source,
    }


# ============================================================
# JSON 解析
# ============================================================

def parse_json_records(payload):
    """
    支援：

    [
        {...},
        {...}
    ]

    以及：

    {
        "data": [...]
    }

    {
        "result": [...]
    }

    {
        "records": [...]
    }
    """

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    candidates = [
        "data",
        "result",
        "records",
        "items",
        "Data",
        "Result",
        "Records",
        "Items",
    ]

    for key in candidates:

        value = payload.get(key)

        if isinstance(value, list):
            return value

    # 如果頂層 dict 本身就是：
    #
    # {
    #   "2330": {...},
    #   "2317": {...}
    # }
    #
    records = []

    for key, value in payload.items():

        if isinstance(value, dict):

            item = dict(value)

            if not any(
                item.get(k)
                for k in [
                    "公司代號",
                    "公司代碼",
                    "股票代號",
                    "股票代碼",
                    "代號",
                    "code",
                    "symbol",
                ]
            ):
                item["代號"] = key

            records.append(item)

    return records


# ============================================================
# TWSE
# ============================================================

def fetch_twse():
    section("取得 TWSE 上市股票")

    log(
        f"API：{TWSE_API}"
    )

    response = http_get(
        TWSE_API
    )

    text = response.text.strip()

    if not text:
        raise RuntimeError(
            "TWSE API 回傳空白內容"
        )

    try:
        payload = response.json()

    except Exception as exc:

        raise RuntimeError(
            f"TWSE JSON 解析失敗：{exc}"
        )

    records = parse_json_records(
        payload
    )

    log(
        f"TWSE API 原始資料："
        f"{len(records)} 筆"
    )

    stocks = {}

    for record in records:

        item = make_record(
            record,
            "TWSE",
            "TWSE"
        )

        if item:

            stocks[
                item["code"]
            ] = item

    log(
        f"TWSE 合法普通股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TWSE:

        raise RuntimeError(
            "TWSE 合法股票數量異常："
            f"{len(stocks)}"
        )

    return stocks


# ============================================================
# TPEx JSON
# ============================================================

def fetch_tpex_json():
    section("取得 TPEx 上櫃股票")

    log(
        f"API：{TPEX_API}"
    )

    response = http_get(
        TPEX_API
    )

    text = response.text.strip()

    if not text:
        raise RuntimeError(
            "TPEx API 回傳空白內容"
        )

    try:
        payload = response.json()

    except Exception as exc:

        raise RuntimeError(
            f"TPEx JSON 解析失敗：{exc}"
        )

    records = parse_json_records(
        payload
    )

    log(
        f"TPEx API 原始資料："
        f"{len(records)} 筆"
    )

    stocks = {}

    for record in records:

        item = make_record(
            record,
            "TPEX",
            "TPEx"
        )

        if item:

            stocks[
                item["code"]
            ] = item

    log(
        f"TPEx JSON 合法普通股票："
        f"{len(stocks)}"
    )

    return stocks


# ============================================================
# CSV parser
# ============================================================

def parse_csv_text(text):
    """
    自動處理：

    UTF-8
    UTF-8 BOM
    Big5
    半形 / 全形
    """

    text = text.lstrip(
        "\ufeff"
    )

    if not text.strip():
        return []

    # csv.Sniffer 有時會誤判
    # 優先使用一般逗號
    try:

        reader = csv.DictReader(
            io.StringIO(text)
        )

        records = list(reader)

        if records:
            return records

    except Exception:
        pass

    # fallback
    try:

        lines = [
            line
            for line in text.splitlines()
            if line.strip()
        ]

        if len(lines) < 2:
            return []

        reader = csv.DictReader(
            lines
        )

        return list(reader)

    except Exception:
        return []


# ============================================================
# TPEx CSV
# ============================================================

def fetch_tpex_csv():
    section(
        "TPEx JSON 解析不足，啟用 CSV fallback"
    )

    last_error = None

    for url in TPEX_CSV_URLS:

        try:

            log(
                f"CSV：{url}"
            )

            response = http_get(
                url
            )

            raw = response.content

            # 優先 UTF-8
            try:
                text = raw.decode(
                    "utf-8-sig"
                )

            except UnicodeDecodeError:

                text = raw.decode(
                    "big5",
                    errors="replace"
                )

            records = parse_csv_text(
                text
            )

            log(
                f"CSV 原始資料："
                f"{len(records)} 筆"
            )

            stocks = {}

            for record in records:

                item = make_record(
                    record,
                    "TPEX",
                    "TPEx CSV"
                )

                if item:

                    stocks[
                        item["code"]
                    ] = item

            log(
                f"TPEx CSV 合法普通股票："
                f"{len(stocks)}"
            )

            if len(stocks) >= MIN_TPEX:
                return stocks

            last_error = RuntimeError(
                "CSV 取得資料不足"
            )

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠ CSV fallback 失敗："
                f"{exc}"
            )

    raise RuntimeError(
        "TPEx JSON / CSV 都無法取得足夠股票資料。"
        f"最後錯誤：{last_error}"
    )


# ============================================================
# 取得 TPEx
# ============================================================

def fetch_tpex():
    try:

        stocks = fetch_tpex_json()

        if len(stocks) >= MIN_TPEX:
            return stocks

        log(
            ""
        )

        log(
            "⚠ TPEx JSON 合法股票不足，"
            "改用 CSV fallback。"
        )

    except Exception as exc:

        log(
            f"⚠ TPEx JSON 失敗：{exc}"
        )

    return fetch_tpex_csv()


# ============================================================
# 合併 Universe
# ============================================================

def build_universe(
    twse,
    tpex
):

    section(
        "合併台股全市場 Universe"
    )

    stocks = {}

    # TWSE
    for code, record in twse.items():

        stocks[
            record["yahoo_symbol"]
        ] = record

    # TPEx
    for code, record in tpex.items():

        stocks[
            record["yahoo_symbol"]
        ] = record

    # 排序
    stocks = dict(
        sorted(
            stocks.items(),
            key=lambda x: (
                x[1]["market"],
                x[1]["code"]
            )
        )
    )

    return stocks


# ============================================================
# 驗證
# ============================================================

def validate(
    twse,
    tpex,
    stocks
):

    section(
        "Universe 資料驗證"
    )

    twse_count = len(twse)
    tpex_count = len(tpex)
    total = len(stocks)

    log(
        f"TWSE：{twse_count}"
    )

    log(
        f"TPEx：{tpex_count}"
    )

    log(
        f"總股票數：{total}"
    )

    if twse_count < MIN_TWSE:

        raise RuntimeError(
            f"TWSE 股票數量異常："
            f"{twse_count}"
        )

    if tpex_count < MIN_TPEX:

        raise RuntimeError(
            f"TPEx 股票數量異常："
            f"{tpex_count}"
        )

    if total < MIN_TOTAL:

        raise RuntimeError(
            f"全市場股票總數異常："
            f"{total}"
        )

    # 檢查重複
    codes = [
        item["code"]
        for item in stocks.values()
    ]

    if len(codes) != len(set(codes)):

        raise RuntimeError(
            "發現重複股票代號"
        )

    # 檢查必要欄位
    invalid = []

    for symbol, item in stocks.items():

        if not item.get("code"):
            invalid.append(symbol)

        if not item.get("yahoo_symbol"):
            invalid.append(symbol)

        if item.get("market") not in (
            "TWSE",
            "TPEX"
        ):
            invalid.append(symbol)

    if invalid:

        raise RuntimeError(
            "發現無效股票資料："
            + ", ".join(
                invalid[:20]
            )
        )

    log(
        "✓ Universe 結構驗證通過"
    )


# ============================================================
# 寫入 JSON
# ============================================================

def save_universe(
    twse,
    tpex,
    stocks
):

    section(
        "寫入 Data/universe.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {
        "version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "source": [
            "TWSE",
            "TPEx"
        ],
        "market": "TW",
        "total": len(stocks),
        "listed_stocks": len(twse),
        "otc_stocks": len(tpex),
        "listed_etf": 0,
        "otc_etf": 0,
        "items": list(
            stocks.values()
        )
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False
        )

    # 寫入成功後才正式替換
    temp_file.replace(
        OUTPUT_FILE
    )

    size = OUTPUT_FILE.stat().st_size

    log(
        "✓ universe.json 建立成功"
    )

    log(
        f"檔案：{OUTPUT_FILE}"
    )

    log(
        f"大小：{size / 1024:.1f} KB"
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

        section(
            "建立台股全市場 Universe"
        )

        # ----------------------------------------------------
        # 1. TWSE
        # ----------------------------------------------------

        twse = fetch_twse()

        # ----------------------------------------------------
        # 2. TPEx
        # ----------------------------------------------------

        tpex = fetch_tpex()

        # ----------------------------------------------------
        # 3. 合併
        # ----------------------------------------------------

        stocks = build_universe(
            twse,
            tpex
        )

        # ----------------------------------------------------
        # 4. 驗證
        # ----------------------------------------------------

        validate(
            twse,
            tpex,
            stocks
        )

        # ----------------------------------------------------
        # 5. 寫檔
        # ----------------------------------------------------

        save_universe(
            twse,
            tpex,
            stocks
        )

        # ----------------------------------------------------
        # 顯示結果
        # ----------------------------------------------------

        section(
            "Universe 建立完成"
        )

        log(
            f"上市普通股票："
            f"{len(twse)}"
        )

        log(
            f"上櫃普通股票："
            f"{len(tpex)}"
        )

        log(
            f"全市場普通股票："
            f"{len(stocks)}"
        )

        log("")
        log(
            "前 20 檔："
        )

        for index, (
            symbol,
            record
        ) in enumerate(
            stocks.items(),
            start=1
        ):

            log(
                f"{index:>2}. "
                f"{record['code']} "
                f"{record.get('name', '')} "
                f"[{record['market']}] "
                f"{symbol}"
            )

            if index >= 20:
                break

        elapsed = time.time() - start_time

        log("")
        log("=" * 64)
        log(
            "✓ build_universe.py 執行完成"
        )
        log("=" * 64)

        log(
            f"總股票數：{len(stocks)}"
        )

        log(
            f"總耗時：{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{OUTPUT_FILE}"
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

        log(
            "⚠️ 保留原有 universe.json"
        )

        # 清理暫存檔
        temp_file = OUTPUT_FILE.with_suffix(
            ".json.tmp"
        )

        if temp_file.exists():

            try:
                temp_file.unlink()
            except Exception:
                pass

        return 1


if __name__ == "__main__":
    sys.exit(main())
