#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.1 TEST

============================================================
本版本用途
============================================================

這是正式 1985 檔執行前的「5 檔驗證版」。

固定測試：

2337 旺宏
2426 鼎元
2368 金像電
3081 艾訊
2330 台積電

============================================================
核心資料
============================================================

CMoney「主力進出」

使用：

1. 每日主力買賣超
2. 主力 5 日買賣超
3. 主力 10 日買賣超
4. 主力 20 日買賣超

定義：

主力 = CMoney 主力進出頁面的「買賣超」

單位：
張

正數：
主力買超

負數：
主力賣超

============================================================
明確不使用
============================================================

5日集中       不使用
10日集中      不使用
20日集中      不使用
家數差        不使用

特別注意：

CMoney「20日集中」
不是
「主力20日買賣超」。

main_force_20d 必須由最近20個交易日
每日「買賣超」逐日加總。

============================================================
V5.1 TEST 原則
============================================================

1. 只跑固定5檔
2. 不跑1985檔
3. 不修改現有 Data/chip.json
4. 成功後寫入 Data/chip_test.json
5. 任一測試股票不足20個交易日，測試失敗
6. 測試失敗不覆蓋任何既有正式資料
7. 不使用 API endpoint 探測
8. 不猜測 CMoney API
9. 先驗證解析結果，再進入1985檔正式版

============================================================
"""


from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V5.1-TEST"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

# ------------------------------------------------------------
# 注意：
# 這次絕對不寫入正式 chip.json
# ------------------------------------------------------------

TEST_OUTPUT_FILE = DATA_DIR / "chip_test.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.30

MIN_HISTORY = 20

# ============================================================
# 固定測試股票
# ============================================================

TEST_SYMBOLS = [
    "2337",
    "2426",
    "2368",
    "3081",
    "2330",
]


# ============================================================
# CMoney URL
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)


# ============================================================
# Headers
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
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,"
        "*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):

    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Universe
# ============================================================

def load_universe():

    section("讀取台股 Universe")

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 格式錯誤"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json items 不是 list"
        )

    lookup = {}

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = item.get("code")

        if symbol is None:
            symbol = item.get("symbol")

        if symbol is None:
            continue

        symbol = str(symbol).strip().upper()

        symbol = re.sub(
            r"\.(TW|TWO)$",
            "",
            symbol
        )

        if not re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol
        ):
            continue

        lookup[symbol] = {
            "symbol": symbol,
            "name": str(
                item.get("name", "")
            ).strip(),
            "market": str(
                item.get("market", "")
            ).strip(),
        }

    log(
        f"Universe 股票數量：{len(lookup)}"
    )

    return lookup


# ============================================================
# Number parser
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace("張", "")
    text = text.replace("%", "")
    text = text.replace("\u3000", "")

    if text.upper() in {
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "-",
        "--",
        "－",
        "—",
        "無",
    }:
        return None

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text
    )

    if not match:
        return None

    try:
        return float(match.group(0))
    except Exception:
        return None


# ============================================================
# Date parser
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    # 2026/08/18
    match = re.fullmatch(
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        text
    )

    if match:

        y, m, d = match.groups()

        return (
            f"{int(y):04d}/"
            f"{int(m):02d}/"
            f"{int(d):02d}"
        )

    # 2026-08-18
    match = re.fullmatch(
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        text
    )

    if match:

        y, m, d = match.groups()

        return (
            f"{int(y):04d}/"
            f"{int(m):02d}/"
            f"{int(d):02d}"
        )

    # 2026.08.18
    match = re.fullmatch(
        r"(\d{4})\.(\d{1,2})\.(\d{1,2})",
        text
    )

    if match:

        y, m, d = match.groups()

        return (
            f"{int(y):04d}/"
            f"{int(m):02d}/"
            f"{int(d):02d}"
        )

    return None


# ============================================================
# Header normalize
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    replacements = [
        "\n",
        "\r",
        "\t",
        " ",
        "\u3000",
    ]

    for value in replacements:
        text = text.replace(value, "")

    return text.strip()


# ============================================================
# 判斷「買賣超」
# ============================================================

def is_main_force_header(text):

    header = normalize_header(text)

    if header == "買賣超":
        return True

    if "買賣超" not in header:
        return False

    # 排除其他指標
    forbidden = [
        "集中",
        "家數",
        "5日",
        "10日",
        "20日",
    ]

    for word in forbidden:

        if word in header:
            return False

    return True


# ============================================================
# HTML table parser
# ============================================================

def parse_table_history(soup):

    tables = soup.find_all("table")

    best_result = []

    for table in tables:

        rows = table.find_all("tr")

        if not rows:
            continue

        header_index = None
        headers = None

        # ----------------------------------------------------
        # 找表頭
        # ----------------------------------------------------

        for row_index, tr in enumerate(rows[:20]):

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            current_headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            has_date = any(
                (
                    h == "日期"
                    or "日期" in h
                )
                for h in current_headers
            )

            has_force = any(
                is_main_force_header(h)
                for h in current_headers
            )

            if has_date and has_force:

                header_index = row_index
                headers = current_headers

                break

        if headers is None:
            continue

        date_index = -1
        force_index = -1

        for index, header in enumerate(headers):

            if (
                date_index == -1
                and (
                    header == "日期"
                    or "日期" in header
                )
            ):
                date_index = index

            if (
                force_index == -1
                and is_main_force_header(header)
            ):
                force_index = index

        if date_index < 0 or force_index < 0:
            continue

        # ----------------------------------------------------
        # 解析資料列
        # ----------------------------------------------------

        current_result = []

        for tr in rows[header_index + 1:]:

            cells = tr.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                date_index,
                force_index
            ):
                continue

            values = [
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in cells
            ]

            date = normalize_date(
                values[date_index]
            )

            if not date:
                continue

            force = parse_number(
                values[force_index]
            )

            if force is None:
                continue

            current_result.append({
                "date": date,
                "main_force": force,
            })

        if len(current_result) > len(best_result):

            best_result = current_result

    return best_result


# ============================================================
# HTML fallback parser
# ============================================================

def parse_text_history(soup):

    """
    只作第二層 fallback。

    不使用「20日集中」、
    「家數差」等欄位。

    尋找：

    日期
    ↓
    買賣超數值
    """

    text = soup.get_text(
        "\n",
        strip=True
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    result = []

    # --------------------------------------------------------
    # 先找「日期」「買賣超」所在區域
    # --------------------------------------------------------

    force_header_indices = []

    for i, line in enumerate(lines):

        if is_main_force_header(line):

            force_header_indices.append(i)

    # --------------------------------------------------------
    # 如果頁面有明確的買賣超表頭
    # 嘗試從附近日期資料解析
    # --------------------------------------------------------

    for header_index in force_header_indices:

        for i in range(
            header_index + 1,
            min(
                header_index + 150,
                len(lines)
            )
        ):

            date = normalize_date(lines[i])

            if not date:
                continue

            # 日期後面找最近的數值
            candidates = []

            for j in range(
                i + 1,
                min(
                    i + 8,
                    len(lines)
                )
            ):

                number = parse_number(lines[j])

                if number is not None:
                    candidates.append(number)

            if not candidates:
                continue

            # ------------------------------------------------
            # 這裡只在明確「日期 → 買賣超」
            # 結構存在時使用第一個數值。
            # ------------------------------------------------

            result.append({
                "date": date,
                "main_force": candidates[0],
            })

    return result


# ============================================================
# Clean history
# ============================================================

def clean_history(rows):

    unique = {}

    for row in rows:

        if not isinstance(row, dict):
            continue

        date = normalize_date(
            row.get("date")
        )

        value = parse_number(
            row.get("main_force")
        )

        if not date:
            continue

        if value is None:
            continue

        try:

            datetime.strptime(
                date,
                "%Y/%m/%d"
            )

        except Exception:

            continue

        unique[date] = float(value)

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in unique.items()
    ]

    result.sort(
        key=lambda x:
            datetime.strptime(
                x["date"],
                "%Y/%m/%d"
            ),
        reverse=True
    )

    return result


# ============================================================
# Request
# ============================================================

def request_cmoney(
    session,
    symbol
):

    urls = [
        CMONEY_URL.format(
            symbol=symbol
        ),
        CMONEY_MOBILE_URL.format(
            symbol=symbol
        ),
    ]

    errors = []

    for url in urls:

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:

                errors.append(
                    f"{response.status_code}: {url}"
                )

                continue

            if not response.text.strip():

                errors.append(
                    f"空白回應: {url}"
                )

                continue

            return (
                response.text,
                url
            )

        except Exception as exc:

            errors.append(
                str(exc)
            )

    raise RuntimeError(
        "CMoney 頁面取得失敗："
        + " | ".join(errors)
    )


# ============================================================
# Parse page
# ============================================================

def parse_page(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # --------------------------------------------------------
    # 第一優先：
    # 真正 table 表格
    # --------------------------------------------------------

    history = parse_table_history(
        soup
    )

    history = clean_history(
        history
    )

    if history:
        return history

    # --------------------------------------------------------
    # 第二優先：
    # 純文字 fallback
    # --------------------------------------------------------

    history = parse_text_history(
        soup
    )

    history = clean_history(
        history
    )

    return history


# ============================================================
# 計算 1D / 5D / 10D / 20D
# ============================================================

def calculate_periods(history):

    values = [
        float(row["main_force"])
        for row in history
        if row.get("main_force") is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": len(values),
    }

    if len(values) >= 1:

        result["main_force_1d"] = round(
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result["main_force_5d"] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result["main_force_10d"] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result["main_force_20d"] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# Fetch single stock
# ============================================================

def fetch_stock(
    session,
    stock
):

    symbol = stock["symbol"]

    log(
        f"[{symbol}]"
    )

    html, url = request_cmoney(
        session,
        symbol
    )

    history = parse_page(
        html
    )

    log(
        f"   頁面解析歷史："
        f"{len(history)} 筆"
    )

    # --------------------------------------------------------
    # 這一版不猜 API
    # 不猜 offset
    # 不猜 page
    #
    # 如果 CMoney 首頁實際只提供10筆，
    # 這裡就明確報告失敗。
    #
    # 先確認解析結果，
    # 再決定下一版真正的20D延伸方式。
    # --------------------------------------------------------

    if len(history) < MIN_HISTORY:

        raise RuntimeError(
            "目前頁面解析到 "
            f"{len(history)} 筆有效主力資料，"
            f"不足 {MIN_HISTORY} 筆。"
        )

    history = history[:MIN_HISTORY]

    periods = calculate_periods(
        history
    )

    if periods["main_force_20d"] is None:

        raise RuntimeError(
            "無法計算主力20D"
        )

    record = {
        "symbol": symbol,
        "name": stock.get(
            "name",
            ""
        ),
        "market": stock.get(
            "market",
            ""
        ),
        "source": "CMoney",

        "main_force_1d":
            periods["main_force_1d"],

        "main_force_5d":
            periods["main_force_5d"],

        "main_force_10d":
            periods["main_force_10d"],

        "main_force_20d":
            periods["main_force_20d"],

        "history_count":
            len(history),

        "status":
            "complete",

        "history":
            history,

        "error":
            None,
    }

    return record


# ============================================================
# 驗證數學
# ============================================================

def verify_record(record):

    history = record.get(
        "history",
        []
    )

    if len(history) < 20:

        raise RuntimeError(
            f"{record['symbol']} history < 20"
        )

    values = [
        float(row["main_force"])
        for row in history[:20]
    ]

    expected_1d = round(
        sum(values[:1]),
        2
    )

    expected_5d = round(
        sum(values[:5]),
        2
    )

    expected_10d = round(
        sum(values[:10]),
        2
    )

    expected_20d = round(
        sum(values[:20]),
        2
    )

    checks = {
        "1D":
            (
                record["main_force_1d"],
                expected_1d
            ),

        "5D":
            (
                record["main_force_5d"],
                expected_5d
            ),

        "10D":
            (
                record["main_force_10d"],
                expected_10d
            ),

        "20D":
            (
                record["main_force_20d"],
                expected_20d
            ),
    }

    for label, (
        actual,
        expected
    ) in checks.items():

        if actual != expected:

            raise RuntimeError(
                f"{record['symbol']} "
                f"{label} 計算錯誤："
                f"actual={actual}, "
                f"expected={expected}"
            )


# ============================================================
# 執行5檔測試
# ============================================================

def run_test(universe):

    section(
        "開始 5 檔測試"
    )

    log(
        "測試模式：TEST ONLY"
    )

    log(
        "不執行1985檔"
    )

    log(
        "不修改 Data/chip.json"
    )

    log(
        "測試輸出："
        f"{TEST_OUTPUT_FILE}"
    )

    log("")

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    failed = []

    for index, symbol in enumerate(
        TEST_SYMBOLS,
        start=1
    ):

        log(
            f"========== "
            f"{index}/{len(TEST_SYMBOLS)} "
            f"=========="
        )

        stock = universe.get(
            symbol
        )

        if stock is None:

            error = (
                f"{symbol} 不存在於 Universe"
            )

            log(
                f"   ❌ {error}"
            )

            failed.append({
                "symbol": symbol,
                "error": error,
            })

            continue

        try:

            record = fetch_stock(
                session,
                stock
            )

            # ------------------------------------------------
            # 再做數學驗證
            # ------------------------------------------------

            verify_record(
                record
            )

            results[symbol] = record

            log(
                f"   ✓ 20D 歷史："
                f"{record['history_count']} 筆"
            )

            log(
                f"   ✓ 1D  = "
                f"{record['main_force_1d']}"
            )

            log(
                f"   ✓ 5D  = "
                f"{record['main_force_5d']}"
            )

            log(
                f"   ✓ 10D = "
                f"{record['main_force_10d']}"
            )

            log(
                f"   ✓ 20D = "
                f"{record['main_force_20d']}"
            )

            log(
                "   ✓ 數學驗證通過"
            )

        except Exception as exc:

            error = str(exc)

            log(
                f"   ❌ 測試失敗："
                f"{error}"
            )

            failed.append({
                "symbol": symbol,
                "error": error,
            })

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        failed
    )


# ============================================================
# 顯示詳細結果
# ============================================================

def print_results(
    results,
    failed
):

    section(
        "5 檔測試結果"
    )

    for symbol in TEST_SYMBOLS:

        if symbol in results:

            record = results[symbol]

            log(
                f"{symbol} "
                f"{record.get('name', '')}"
            )

            log(
                f"  日期："
                f"{record['history'][0]['date']}"
                f" → "
                f"{record['history'][-1]['date']}"
            )

            log(
                f"  1D  ："
                f"{record['main_force_1d']}"
            )

            log(
                f"  5D  ："
                f"{record['main_force_5d']}"
            )

            log(
                f"  10D ："
                f"{record['main_force_10d']}"
            )

            log(
                f"  20D ："
                f"{record['main_force_20d']}"
            )

            log("")

    if failed:

        log(
            "失敗股票："
        )

        for item in failed:

            log(
                f"  {item['symbol']}："
                f"{item['error']}"
            )

    else:

        log(
            "✓ 5 檔全部成功"
        )


# ============================================================
# 儲存測試結果
# ============================================================

def save_test_result(
    results,
    failed
):

    section(
        "寫入測試檔"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now = datetime.now()

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "data_date":
            now.strftime(
                "%Y-%m-%d"
            ),

        "source":
            "CMoney",

        "test_mode":
            True,

        "test_symbols":
            TEST_SYMBOLS,

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "main_force_5d":
                "最近5個交易日主力買賣超加總",

            "main_force_10d":
                "最近10個交易日主力買賣超加總",

            "main_force_20d":
                "最近20個交易日主力買賣超加總",

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",

            "five_day_concentration":
                "不使用",

            "twenty_day_concentration":
                "不使用",

            "family_difference":
                "不使用",
        },

        "statistics": {

            "tested":
                len(TEST_SYMBOLS),

            "success":
                len(results),

            "failed":
                len(failed),
        },

        "stocks":
            results,

        "failed":
            failed,
    }

    # --------------------------------------------------------
    # 暫存檔
    # --------------------------------------------------------

    temp_file = TEST_OUTPUT_FILE.with_suffix(
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
            indent=2
        )

    # --------------------------------------------------------
    # 重新讀取驗證 JSON
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(
        verify.get("stocks"),
        dict
    ):

        raise RuntimeError(
            "測試 JSON stocks 格式錯誤"
        )

    temp_file.replace(
        TEST_OUTPUT_FILE
    )

    log(
        f"✓ 測試檔建立："
        f"{TEST_OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log("=" * 72)

    log(
        "模式：5 檔測試"
    )

    log(
        "5D：保留"
    )

    log(
        "10D：保留"
    )

    log(
        "20D：保留"
    )

    log(
        "5日集中：不使用"
    )

    log(
        "20日集中：不使用"
    )

    log(
        "家數差：不使用"
    )

    log(
        "API endpoint 探測：停用"
    )

    log(
        "正式 Data/chip.json：不修改"
    )

    try:

        universe = load_universe()

        log("")
        log(
            "固定測試標的："
        )

        for symbol in TEST_SYMBOLS:

            stock = universe.get(
                symbol,
                {}
            )

            log(
                f"  {symbol} "
                f"{stock.get('name', '')}"
            )

        results, failed = run_test(
            universe
        )

        print_results(
            results,
            failed
        )

        # ----------------------------------------------------
        # 五檔必須全部成功
        # ----------------------------------------------------

        if len(results) != len(TEST_SYMBOLS):

            log("")
            log(
                "❌ 5 檔測試未全部通過"
            )

            log(
                "本次不寫入正式 chip.json"
            )

            # 測試檔仍可保留，方便檢查
            save_test_result(
                results,
                failed
            )

            return 1

        # ----------------------------------------------------
        # 全部成功才寫測試檔
        # ----------------------------------------------------

        save_test_result(
            results,
            failed
        )

        elapsed = (
            time.time()
            - start_time
        )

        log("")
        log("=" * 72)
        log(
            "✓ 5 檔測試全部通過"
        )
        log("=" * 72)

        log(
            "2337 ✓"
        )

        log(
            "2426 ✓"
        )

        log(
            "2368 ✓"
        )

        log(
            "3081 ✓"
        )

        log(
            "2330 ✓"
        )

        log("")
        log(
            "✓ 1D / 5D / 10D / 20D"
            " 均完成驗證"
        )

        log(
            "✓ 20D 由每日買賣超加總"
        )

        log(
            "✓ 未使用20日集中"
        )

        log(
            "✓ 未使用5日集中"
        )

        log(
            "✓ 未使用家數差"
        )

        log(
            "✓ 未修改 Data/chip.json"
        )

        log(
            f"耗時：{elapsed:.1f} 秒"
        )

        log(
            f"測試輸出："
            f"{TEST_OUTPUT_FILE}"
        )

        log("")
        log(
            "下一步："
        )

        log(
            "先檢查這5檔的20D資料是否正確，"
            "確認後再切換1985檔正式模式。"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)
        log(
            "❌ fetch_chip.py 測試失敗"
        )
        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        log(
            "正式 Data/chip.json 未修改"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )