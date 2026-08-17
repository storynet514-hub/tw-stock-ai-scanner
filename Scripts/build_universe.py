#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
build_universe.py V5.0

============================================================
責任
============================================================

建立台股全市場 Universe：

TWSE 上市
+
TPEx 上櫃

輸出：

Data/universe.json

============================================================
重要設計
============================================================

1. API HTTP 200 不代表內容一定是 JSON
2. JSON 解析失敗時嘗試文字/CSV解析
3. TWSE / TPEx 分開驗證
4. 任一市場異常時不覆蓋既有 universe.json
5. 只接受合法台股股票代號
6. 排除 ETF、權證、債券等非普通股票
7. 支援 .TW / .TWO
8. 全市場，不使用固定股票清單
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

VERSION = "V5.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

TWSE_URL = (
    "https://openapi.twse.com.tw/"
    "v1/opendata/t187ap03_L"
)

TPEX_URL = (
    "https://www.tpex.org.tw/"
    "openapi/v1/mopsfin_t187ap03_O"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 5

RETRY_DELAY = 2

# 最低合理市場數量
MIN_TWSE = 700
MIN_TPEX = 300

# 台股普通股票代號通常 4 碼，
# 部分特殊股票可能帶數字/英文，但這裡
# 以主要股票市場資料為主。
CODE_PATTERN = re.compile(
    r"^[0-9]{4}$"
)


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
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,"
        "text/csv,"
        "*/*"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
})


# ============================================================
# HTTP 下載
# ============================================================

def download(url, name):

    section(f"取得 {name}")

    log(f"API：{url}")

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            log(
                f"  HTTP GET attempt "
                f"{attempt}/{MAX_RETRIES}"
            )

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            log(
                f"  HTTP Status: "
                f"{response.status_code}"
            )

            response.raise_for_status()

            content = response.content

            if not content:
                raise RuntimeError(
                    "HTTP 200 但回傳內容為空"
                )

            log(
                f"  Content-Length: "
                f"{len(content)} bytes"
            )

            return response

        except Exception as exc:

            last_error = exc

            log(
                f"  ⚠️ attempt {attempt} "
                f"失敗：{exc}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    raise RuntimeError(
        f"{name} API 取得失敗：{last_error}"
    )


# ============================================================
# JSON 嘗試解析
# ============================================================

def try_json(response):

    # --------------------------------------------------------
    # 方法 1：response.json()
    # --------------------------------------------------------

    try:
        data = response.json()

        if isinstance(data, list):
            return data

        if isinstance(data, dict):

            # 某些 API 可能包在 data / result
            for key in [
                "data",
                "result",
                "results",
                "items"
            ]:

                value = data.get(key)

                if isinstance(value, list):
                    return value

            return [data]

    except Exception:
        pass

    # --------------------------------------------------------
    # 方法 2：直接 decode JSON
    # --------------------------------------------------------

    try:

        text = response.content.decode(
            "utf-8-sig",
            errors="ignore"
        ).strip()

        if text:

            data = json.loads(text)

            if isinstance(data, list):
                return data

            if isinstance(data, dict):
                return [data]

    except Exception:
        pass

    return None


# ============================================================
# 文字 / CSV 備援
# ============================================================

def try_text_rows(response):

    try:

        text = response.content.decode(
            "utf-8-sig",
            errors="ignore"
        )

    except Exception:

        text = response.text

    text = text.strip()

    if not text:
        return []

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    rows = []

    for line in lines:

        # 去除 BOM
        line = line.replace("\ufeff", "")

        # CSV
        if "," in line:

            parts = [
                x.strip().strip('"')
                for x in line.split(",")
            ]

        # TSV
        elif "\t" in line:

            parts = [
                x.strip().strip('"')
                for x in line.split("\t")
            ]

        # 一般空白
        else:

            parts = line.split()

        if len(parts) >= 2:

            rows.append(parts)

    return rows


# ============================================================
# 找欄位
# ============================================================

def find_value(record, keys):

    if not isinstance(record, dict):
        return None

    # 完全符合
    for key in keys:

        if key in record:

            value = record.get(key)

            if value is not None:

                value = str(value).strip()

                if value:
                    return value

    # 模糊符合
    normalized = {}

    for key, value in record.items():

        normalized[
            str(key).strip().lower()
        ] = value

    for key in keys:

        key_lower = key.lower()

        for actual_key, value in normalized.items():

            if key_lower == actual_key:

                if value is not None:

                    value = str(value).strip()

                    if value:
                        return value

    return None


# ============================================================
# 判斷股票代號
# ============================================================

def normalize_code(value):

    if value is None:
        return None

    value = str(value).strip()

    # 去除空白
    value = value.replace(" ", "")

    # 有些資料會是：
    # 2330.TW
    # 2330.TWO
    if "." in value:

        value = value.split(".")[0]

    # 純 4 碼
    if CODE_PATTERN.fullmatch(value):

        return value

    return None


# ============================================================
# 判斷是否為普通股票
# ============================================================

def is_common_stock(record):

    if not isinstance(record, dict):
        return True

    text = " ".join(
        str(v)
        for v in record.values()
        if v is not None
    )

    upper = text.upper()

    # 明確排除
    excluded_words = [
        "ETF",
        "ETN",
        "權證",
        "認購權證",
        "認售權證",
        "公司債",
        "債券",
        "受益證券",
        "存託憑證",
        "TDR",
    ]

    for word in excluded_words:

        if word.upper() in upper:
            return False

    return True


# ============================================================
# TWSE JSON
# ============================================================

def parse_twse_json(data):

    stocks = {}

    for record in data:

        if not isinstance(record, dict):
            continue

        code = find_value(
            record,
            [
                "公司代號",
                "有價證券代號",
                "證券代號",
                "代號",
                "Code",
                "code",
                "stock_code"
            ]
        )

        name = find_value(
            record,
            [
                "公司名稱",
                "有價證券名稱",
                "證券名稱",
                "名稱",
                "Name",
                "name"
            ]
        )

        code = normalize_code(code)

        if not code:
            continue

        if not is_common_stock(record):
            continue

        stocks[code] = {
            "symbol": code,
            "name": name or "",
            "market": "TWSE",
            "yahoo_symbol": code + ".TW"
        }

    return stocks


# ============================================================
# TPEx JSON
# ============================================================

def parse_tpex_json(data):

    stocks = {}

    for record in data:

        if not isinstance(record, dict):
            continue

        code = find_value(
            record,
            [
                "公司代號",
                "有價證券代號",
                "證券代號",
                "代號",
                "Code",
                "code",
                "SecuritiesCompanyCode"
            ]
        )

        name = find_value(
            record,
            [
                "公司名稱",
                "有價證券名稱",
                "證券名稱",
                "名稱",
                "Name",
                "name",
                "SecuritiesCompanyName"
            ]
        )

        code = normalize_code(code)

        if not code:
            continue

        if not is_common_stock(record):
            continue

        stocks[code] = {
            "symbol": code,
            "name": name or "",
            "market": "TPEX",
            "yahoo_symbol": code + ".TWO"
        }

    return stocks


# ============================================================
# CSV / Text Parser
# ============================================================

def parse_text_rows(rows, market):

    stocks = {}

    for parts in rows:

        if len(parts) < 2:
            continue

        # 找第一個看起來像 4 碼股票代號的欄位
        code = None
        code_index = None

        for i, value in enumerate(parts[:10]):

            candidate = normalize_code(value)

            if candidate:

                code = candidate
                code_index = i
                break

        if not code:
            continue

        # 名稱通常就在代號後面
        name = ""

        if (
            code_index is not None
            and code_index + 1 < len(parts)
        ):

            name = parts[code_index + 1].strip()

        stocks[code] = {
            "symbol": code,
            "name": name,
            "market": market,
            "yahoo_symbol": (
                code
                + (
                    ".TW"
                    if market == "TWSE"
                    else ".TWO"
                )
            )
        }

    return stocks


# ============================================================
# 取得 TWSE
# ============================================================

def fetch_twse():

    response = download(
        TWSE_URL,
        "TWSE 上市股票"
    )

    data = try_json(response)

    if data is not None:

        log(
            f"TWSE JSON 原始資料："
            f"{len(data)} 筆"
        )

        stocks = parse_twse_json(data)

        log(
            f"TWSE JSON 合法普通股票："
            f"{len(stocks)}"
        )

        if len(stocks) >= MIN_TWSE:
            return stocks

    # --------------------------------------------------------
    # JSON 不可用 → 文字備援
    # --------------------------------------------------------

    log(
        "⚠️ TWSE JSON 解析數量不足，"
        "嘗試文字/CSV 備援解析"
    )

    rows = try_text_rows(response)

    log(
        f"TWSE 文字資料：{len(rows)} 行"
    )

    stocks = parse_text_rows(
        rows,
        "TWSE"
    )

    log(
        f"TWSE 文字解析股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TWSE:

        raise RuntimeError(
            "TWSE 合法普通股票數量異常："
            f"{len(stocks)}，"
            f"低於最低門檻 {MIN_TWSE}"
        )

    return stocks


# ============================================================
# 取得 TPEx
# ============================================================

def fetch_tpex():

    response = download(
        TPEX_URL,
        "TPEx 上櫃股票"
    )

    data = try_json(response)

    if data is not None:

        log(
            f"TPEx JSON 原始資料："
            f"{len(data)} 筆"
        )

        stocks = parse_tpex_json(data)

        log(
            f"TPEx JSON 合法普通股票："
            f"{len(stocks)}"
        )

        if len(stocks) >= MIN_TPEX:
            return stocks

    # --------------------------------------------------------
    # JSON 不可用 → 文字備援
    # --------------------------------------------------------

    log(
        "⚠️ TPEx JSON 解析數量不足，"
        "嘗試文字/CSV 備援解析"
    )

    rows = try_text_rows(response)

    log(
        f"TPEx 文字資料：{len(rows)} 行"
    )

    stocks = parse_text_rows(
        rows,
        "TPEX"
    )

    log(
        f"TPEx 文字解析股票："
        f"{len(stocks)}"
    )

    if len(stocks) < MIN_TPEX:

        raise RuntimeError(
            "TPEx 合法普通股票數量異常："
            f"{len(stocks)}，"
            f"低於最低門檻 {MIN_TPEX}"
        )

    return stocks


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

    stocks = {}

    for code, record in twse.items():

        stocks[
            record["yahoo_symbol"]
        ] = record

    for code, record in tpex.items():

        yahoo_symbol = record["yahoo_symbol"]

        stocks[yahoo_symbol] = record

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    stocks = dict(
        sorted(
            stocks.items(),
            key=lambda x: (
                x[1].get("market", ""),
                x[1].get("symbol", "")
            )
        )
    )

    return twse, tpex, stocks


# ============================================================
# 驗證
# ============================================================

def validate(twse, tpex, stocks):

    section("Universe 驗證")

    log(
        f"TWSE：{len(twse)}"
    )

    log(
        f"TPEx：{len(tpex)}"
    )

    log(
        f"全市場：{len(stocks)}"
    )

    if len(twse) < MIN_TWSE:

        raise RuntimeError(
            f"TWSE 股票數量異常：{len(twse)}"
        )

    if len(tpex) < MIN_TPEX:

        raise RuntimeError(
            f"TPEx 股票數量異常：{len(tpex)}"
        )

    if len(stocks) < (
        MIN_TWSE + MIN_TPEX
    ):

        raise RuntimeError(
            "合併後 Universe 數量異常"
        )

    # 檢查 symbol
    invalid = []

    for yahoo_symbol, record in stocks.items():

        code = record.get("symbol")

        market = record.get("market")

        if not CODE_PATTERN.fullmatch(
            str(code)
        ):

            invalid.append(
                yahoo_symbol
            )
            continue

        if market not in [
            "TWSE",
            "TPEX"
        ]:

            invalid.append(
                yahoo_symbol
            )

    if invalid:

        raise RuntimeError(
            "發現非法股票資料："
            + ", ".join(invalid[:20])
        )

    log("✓ Universe 結構驗證通過")


# ============================================================
# 儲存
# ============================================================

def save_universe(
    twse,
    tpex,
    stocks
):

    section("寫入 Data/universe.json")

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
            "TWSE OpenAPI",
            "TPEx OpenAPI"
        ],
        "market": "TW",
        "total": len(stocks),
        "listed_stocks": len(twse),
        "otc_stocks": len(tpex),
        "listed_etf": 0,
        "otc_etf": 0,
        "items": list(stocks.values())
    }

    temp_file = UNIVERSE_FILE.with_suffix(
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

    temp_file.replace(
        UNIVERSE_FILE
    )

    size = UNIVERSE_FILE.stat().st_size

    log(
        "✓ universe.json 建立成功"
    )

    log(
        f"檔案：{UNIVERSE_FILE}"
    )

    log(
        f"大小：{size / 1024:.1f} KB"
    )


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

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
        # 1. 取得全市場
        # ----------------------------------------------------

        twse, tpex, stocks = build_universe()

        # ----------------------------------------------------
        # 2. 驗證
        # ----------------------------------------------------

        validate(
            twse,
            tpex,
            stocks
        )

        # ----------------------------------------------------
        # 3. 顯示摘要
        # ----------------------------------------------------

        section("Universe 摘要")

        log(
            f"上市股票：{len(twse)}"
        )

        log(
            f"上櫃股票：{len(tpex)}"
        )

        log(
            f"全市場股票：{len(stocks)}"
        )

        log("")
        log("前 20 個標的：")

        for i, (
            yahoo_symbol,
            record
        ) in enumerate(
            stocks.items(),
            start=1
        ):

            log(
                f"{i:4d}. "
                f"{yahoo_symbol:<12} | "
                f"{record.get('name', '')}"
            )

            if i >= 20:
                break

        # ----------------------------------------------------
        # 4. 寫入
        # ----------------------------------------------------

        save_universe(
            twse,
            tpex,
            stocks
        )

        elapsed = time.time() - start

        log("")
        log("=" * 64)
        log(
            "✓ build_universe.py 執行完成"
        )
        log("=" * 64)

        log(
            f"上市：{len(twse)}"
        )

        log(
            f"上櫃：{len(tpex)}"
        )

        log(
            f"全市場：{len(stocks)}"
        )

        log(
            f"總耗時：{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{UNIVERSE_FILE}"
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


if __name__ == "__main__":
    sys.exit(main())
