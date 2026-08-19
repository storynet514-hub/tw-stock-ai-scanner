#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
test_fetch_chip_10.py V6.0-TEST

============================================================
目的
============================================================

本程式不是正式版 fetch_chip.py。

用途：

只測試 10 檔股票，確認 CMoney「主力進出」頁面的：

「買賣超」

是否真的被正確解析。

============================================================
本次固定測試 10 檔
============================================================

2337 旺宏
6770 力積電
6695 芯鼎
6914 阜爾運通
2426 鼎元
2368 金像電
3081 聯亞
2303 聯電
5483 中美晶
6120 達運

============================================================
重要原則
============================================================

只接受 CMoney 表格中明確標示：

「買賣超」

的欄位。

絕對不使用：

20日集中
5日集中
10日集中
家數差
買進家數
賣出家數
法人買賣超
其他籌碼欄位

============================================================
計算
============================================================

1D  = 最近 1 個交易日買賣超
5D  = 最近 5 個交易日買賣超加總
10D = 最近 10 個交易日買賣超加總
20D = 最近 20 個交易日買賣超加總

單位：

張

============================================================
重要
============================================================

每檔股票：

取得 20 個交易日後立即停止。

不掃 Universe。

不寫 GitHub。

不修改既有 Data/chip.json。

只產生：

Data/chip_test_10.json

============================================================
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.0-TEST"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "chip_test_10.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.30

MIN_HISTORY = 20


# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {
        "symbol": "2337",
        "name": "旺宏",
        "market": "TW",
    },
    {
        "symbol": "6770",
        "name": "力積電",
        "market": "TW",
    },
    {
        "symbol": "6695",
        "name": "芯鼎",
        "market": "TW",
    },
    {
        "symbol": "6914",
        "name": "阜爾運通",
        "market": "TW",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
        "market": "TW",
    },
    {
        "symbol": "2368",
        "name": "金像電",
        "market": "TW",
    },
    {
        "symbol": "3081",
        "name": "聯亞",
        "market": "TWO",
    },
    {
        "symbol": "2303",
        "name": "聯電",
        "market": "TW",
    },
    {
        "symbol": "5483",
        "name": "中美晶",
        "market": "TWO",
    },
    {
        "symbol": "6120",
        "name": "達運",
        "market": "TW",
    },
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
# User-Agent
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
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
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
    log("=" * 78)
    log(title)
    log("=" * 78)


# ============================================================
# Number
# ============================================================

def parse_number(text):
    """
    解析數字。

    注意：
    本函式只負責把指定欄位轉成數字。

    不負責猜欄位。
    """

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("張", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    if text in {
        "-",
        "--",
        "—",
        "－",
        "N/A",
        "NA",
        "None",
        "null",
        "無",
    }:
        return None

    match = re.fullmatch(
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
# Date
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    text = text.replace(
        "-",
        "/"
    )

    # YYYY/MM/DD
    match = re.fullmatch(
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        text
    )

    if match:

        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))

        try:

            datetime(
                year,
                month,
                day
            )

            return (
                f"{year:04d}/"
                f"{month:02d}/"
                f"{day:02d}"
            )

        except Exception:

            return None

    return None


# ============================================================
# Header normalization
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = (
        text
        .replace("\n", "")
        .replace("\r", "")
        .replace("\t", "")
        .replace(" ", "")
        .replace("\u3000", "")
    )

    return text.strip()


# ============================================================
# 判斷「買賣超」欄位
# ============================================================

def is_exact_buy_sell_header(header):
    """
    最嚴格模式。

    只接受：

    買賣超

    或 HTML 因格式造成的：

    買賣超(張)

    買賣超張

    不接受：

    20日集中
    5日集中
    法人買賣超
    外資買賣超
    投信買賣超
    自營商買賣超
    家數差
    """

    h = normalize_header(header)

    if h == "買賣超":
        return True

    if h in {
        "買賣超張",
        "買賣超(張)",
        "買賣超（張）",
    }:
        return True

    return False


# ============================================================
# Request
# ============================================================

def request_html(
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

    last_error = None

    for url in urls:

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            if not response.text.strip():

                raise RuntimeError(
                    "CMoney 回傳空白 HTML"
                )

            return (
                response.text,
                url
            )

        except Exception as exc:

            last_error = exc

    raise RuntimeError(
        f"無法取得 {symbol} CMoney 頁面："
        f"{last_error}"
    )


# ============================================================
# 印出所有候選表格
# ============================================================

def inspect_tables(soup, symbol):
    """
    第一次測試最重要的 debug。

    把頁面中所有 table 的 headers 印出來，
    讓我們確認實際 HTML 結構。

    不猜資料。
    """

    tables = soup.find_all("table")

    log(
        f"   發現 table 數量：{len(tables)}"
    )

    for table_index, table in enumerate(
        tables,
        start=1
    ):

        rows = table.find_all("tr")

        if not rows:
            continue

        header_found = None

        for tr in rows[:10]:

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            if any(headers):

                header_found = headers

                break

        if header_found:

            log(
                f"   TABLE #{table_index}"
                f" headers："
                f"{header_found}"
            )


# ============================================================
# 找正確 table
# ============================================================

def find_main_force_table(
    soup,
    symbol
):
    """
    僅接受：

    同一個 table 裡：

    日期

    +

    明確的「買賣超」

    """

    tables = soup.find_all("table")

    candidates = []

    for table_index, table in enumerate(
        tables,
        start=1
    ):

        rows = table.find_all("tr")

        if not rows:
            continue

        for tr in rows[:15]:

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            date_index = -1
            buy_sell_index = -1

            for index, header in enumerate(
                headers
            ):

                if (
                    date_index < 0
                    and (
                        header == "日期"
                        or header.endswith("日期")
                    )
                ):

                    date_index = index

                if (
                    buy_sell_index < 0
                    and is_exact_buy_sell_header(
                        header
                    )
                ):

                    buy_sell_index = index

            if (
                date_index >= 0
                and buy_sell_index >= 0
            ):

                candidates.append({
                    "table": table,
                    "table_index": table_index,
                    "headers": headers,
                    "date_index": date_index,
                    "buy_sell_index":
                        buy_sell_index,
                })

                break

    if not candidates:

        return None

    # 第一候選直接使用。
    # 因為我們已經要求 exact「買賣超」。
    return candidates[0]


# ============================================================
# Parse 正確 table
# ============================================================

def parse_main_force_table(
    html,
    symbol
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # --------------------------------------------------------
    # 先把 HTML 結構印出來
    # --------------------------------------------------------

    inspect_tables(
        soup,
        symbol
    )

    candidate = find_main_force_table(
        soup,
        symbol
    )

    if candidate is None:

        return {
            "history": [],
            "table_found": False,
            "headers": [],
            "table_index": None,
        }

    table = candidate[
        "table"
    ]

    date_index = candidate[
        "date_index"
    ]

    buy_sell_index = candidate[
        "buy_sell_index"
    ]

    headers = candidate[
        "headers"
    ]

    log(
        f"   ✓ 鎖定 TABLE #"
        f"{candidate['table_index']}"
    )

    log(
        f"   ✓ 日期欄位 index："
        f"{date_index}"
    )

    log(
        f"   ✓ 買賣超欄位 index："
        f"{buy_sell_index}"
    )

    log(
        f"   ✓ 使用欄位："
        f"{headers[buy_sell_index]}"
    )

    rows = table.find_all("tr")

    history = []

    for tr in rows:

        cells = tr.find_all(
            ["th", "td"]
        )

        if len(cells) <= max(
            date_index,
            buy_sell_index
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

        raw_buy_sell = values[
            buy_sell_index
        ]

        value = parse_number(
            raw_buy_sell
        )

        if value is None:
            continue

        history.append({
            "date": date,
            "main_force":
                value,
            "raw_main_force":
                raw_buy_sell,
        })

    # --------------------------------------------------------
    # 日期去重
    # --------------------------------------------------------

    unique = {}

    for row in history:

        unique[
            row["date"]
        ] = row

    history = list(
        unique.values()
    )

    history.sort(
        key=lambda row:
            datetime.strptime(
                row["date"],
                "%Y/%m/%d"
            ),
        reverse=True
    )

    return {
        "history":
            history,
        "table_found":
            True,
        "headers":
            headers,
        "table_index":
            candidate[
                "table_index"
            ],
    }


# ============================================================
# 顯示原始 20 日
# ============================================================

def print_raw_history(
    symbol,
    name,
    history
):

    section(
        f"{symbol} {name}｜CMoney 原始「買賣超」"
    )

    log(
        "日期              買賣超"
    )

    log(
        "-" * 40
    )

    for index, row in enumerate(
        history[:20],
        start=1
    ):

        value = row[
            "main_force"
        ]

        raw = row[
            "raw_main_force"
        ]

        log(
            f"{row['date']}    "
            f"{raw}"
            f"    "
            f"=> "
            f"{value:g} 張"
        )

    log(
        "-" * 40
    )

    log(
        f"歷史筆數："
        f"{len(history)}"
    )


# ============================================================
# 計算
# ============================================================

def calculate_periods(
    history
):

    values = [
        row["main_force"]
        for row in history[:20]
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = values[0]

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = sum(
            values[:5]
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = sum(
            values[:10]
        )

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = sum(
            values[:20]
        )

    return result


# ============================================================
# 驗證計算
# ============================================================

def verify_calculation(
    history,
    periods
):

    values = [
        row["main_force"]
        for row in history[:20]
    ]

    checks = {}

    if len(values) >= 1:

        checks[
            "1D"
        ] = (
            periods[
                "main_force_1d"
            ]
            ==
            values[0]
        )

    if len(values) >= 5:

        checks[
            "5D"
        ] = (
            periods[
                "main_force_5d"
            ]
            ==
            sum(values[:5])
        )

    if len(values) >= 10:

        checks[
            "10D"
        ] = (
            periods[
                "main_force_10d"
            ]
            ==
            sum(values[:10])
        )

    if len(values) >= 20:

        checks[
            "20D"
        ] = (
            periods[
                "main_force_20d"
            ]
            ==
            sum(values[:20])
        )

    return checks


# ============================================================
# 單檔測試
# ============================================================

def test_stock(
    session,
    stock
):

    symbol = stock[
        "symbol"
    ]

    name = stock[
        "name"
    ]

    section(
        f"測試 {symbol} {name}"
    )

    log(
        f"CMoney："
        f"{CMONEY_URL.format(symbol=symbol)}"
    )

    result = {
        "symbol": symbol,
        "name": name,
        "market": stock[
            "market"
        ],
        "source": "CMoney",
        "status": "failed",
        "table_found": False,
        "table_index": None,
        "headers": [],
        "history": [],
        "history_count": 0,
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "calculation_check": {},
        "error": None,
    }

    try:

        html, page_url = request_html(
            session,
            symbol
        )

        log(
            f"   ✓ HTML取得成功"
        )

        log(
            f"   URL：{page_url}"
        )

        parsed = parse_main_force_table(
            html,
            symbol
        )

        result[
            "table_found"
        ] = parsed[
            "table_found"
        ]

        result[
            "table_index"
        ] = parsed[
            "table_index"
        ]

        result[
            "headers"
        ] = parsed[
            "headers"
        ]

        history = parsed[
            "history"
        ]

        # ----------------------------------------------------
        # 核心：不足20筆直接失敗
        # ----------------------------------------------------

        if len(history) < MIN_HISTORY:

            raise RuntimeError(
                f"明確的「買賣超」欄位"
                f"只有 {len(history)} 筆，"
                f"不足 {MIN_HISTORY} 筆"
            )

        # ----------------------------------------------------
        # 只取最近20日
        # ----------------------------------------------------

        history = history[
            :MIN_HISTORY
        ]

        result[
            "history"
        ] = history

        result[
            "history_count"
        ] = len(history)

        # ----------------------------------------------------
        # 顯示原始資料
        # ----------------------------------------------------

        print_raw_history(
            symbol,
            name,
            history
        )

        # ----------------------------------------------------
        # 計算
        # ----------------------------------------------------

        periods = calculate_periods(
            history
        )

        result.update(
            periods
        )

        # ----------------------------------------------------
        # 驗證計算
        # ----------------------------------------------------

        checks = verify_calculation(
            history,
            periods
        )

        result[
            "calculation_check"
        ] = checks

        # ----------------------------------------------------
        # 顯示結果
        # ----------------------------------------------------

        section(
            f"{symbol} {name}｜計算結果"
        )

        log(
            f"1D  = "
            f"{periods['main_force_1d']}"
        )

        log(
            f"5D  = "
            f"{periods['main_force_5d']}"
        )

        log(
            f"10D = "
            f"{periods['main_force_10d']}"
        )

        log(
            f"20D = "
            f"{periods['main_force_20d']}"
        )

        log("")

        log(
            f"1D 驗證："
            f"{'PASS' if checks.get('1D') else 'FAIL'}"
        )

        log(
            f"5D 驗證："
            f"{'PASS' if checks.get('5D') else 'FAIL'}"
        )

        log(
            f"10D 驗證："
            f"{'PASS' if checks.get('10D') else 'FAIL'}"
        )

        log(
            f"20D 驗證："
            f"{'PASS' if checks.get('20D') else 'FAIL'}"
        )

        if all(checks.values()):

            result[
                "status"
            ] = "complete"

            log("")
            log(
                "✓ 此股票驗證通過"
            )

        else:

            result[
                "status"
            ] = "calculation_error"

            log("")
            log(
                "❌ 計算驗證失敗"
            )

    except Exception as exc:

        result[
            "error"
        ] = str(exc)

        result[
            "status"
        ] = "failed"

        log("")
        log(
            f"❌ {symbol} {name} 測試失敗"
        )

        log(
            f"   原因：{exc}"
        )

    return result


# ============================================================
# 儲存測試結果
# ============================================================

def save_results(
    results
):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    complete = sum(
        1
        for item in results.values()
        if item[
            "status"
        ] == "complete"
    )

    failed = len(
        results
    ) - complete

    output = {
        "schema_version":
            VERSION,

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            "CMoney",

        "purpose":
            "10檔主力買賣超欄位驗證",

        "definition": {
            "main_force":
                "CMoney 主力進出頁面之明確「買賣超」欄位",

            "main_force_1d":
                "最近1個交易日買賣超",

            "main_force_5d":
                "最近5個交易日買賣超加總",

            "main_force_10d":
                "最近10個交易日買賣超加總",

            "main_force_20d":
                "最近20個交易日買賣超加總",

            "unit":
                "張",

            "excluded":
                [
                    "20日集中",
                    "10日集中",
                    "5日集中",
                    "家數差",
                    "法人買賣超",
                    "外資買賣超",
                    "投信買賣超",
                    "自營商買賣超",
                ],
        },

        "test_stock_count":
            len(TEST_STOCKS),

        "complete":
            complete,

        "failed":
            failed,

        "stocks":
            results,
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".tmp"
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
    # 寫入前驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if len(
        verify.get(
            "stocks",
            {}
        )
    ) != len(
        TEST_STOCKS
    ):

        raise RuntimeError(
            "測試結果數量驗證失敗"
        )

    temp_file.replace(
        OUTPUT_FILE
    )

    log("")
    log(
        f"✓ 測試結果已建立："
        f"{OUTPUT_FILE}"
    )


# ============================================================
# Final Summary
# ============================================================

def print_summary(
    results
):

    section(
        "10 檔測試最終結果"
    )

    log(
        f"{'股票':<12}"
        f"{'狀態':<14}"
        f"{'1D':>12}"
        f"{'5D':>14}"
        f"{'10D':>14}"
        f"{'20D':>14}"
        f"{'歷史':>8}"
    )

    log(
        "-" * 90
    )

    for stock in TEST_STOCKS:

        symbol = stock[
            "symbol"
        ]

        record = results.get(
            symbol
        )

        if record is None:
            continue

        status = record[
            "status"
        ]

        if status == "complete":
            status_text = "PASS"

        elif status == "failed":
            status_text = "FAILED"

        else:
            status_text = status

        def fmt(value):

            if value is None:
                return "None"

            if float(value).is_integer():
                return str(
                    int(value)
                )

            return f"{value:.2f}"

        log(
            f"{symbol} {record['name']:<6}"
            f"{status_text:<14}"
            f"{fmt(record['main_force_1d']):>12}"
            f"{fmt(record['main_force_5d']):>14}"
            f"{fmt(record['main_force_10d']):>14}"
            f"{fmt(record['main_force_20d']):>14}"
            f"{record['history_count']:>8}"
        )

    log("")
    log(
        "注意："
    )

    log(
        "本次結果只是「欄位解析驗證」。"
    )

    log(
        "在 10 檔確認正確前，"
        "不要重新跑 1,985 檔 Universe。"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    section(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log(
        "本次模式：10 檔驗證模式"
    )

    log(
        "不讀 Universe"
    )

    log(
        "不掃 1,985 檔"
    )

    log(
        "不修改 Data/chip.json"
    )

    log(
        f"輸出：{OUTPUT_FILE}"
    )

    log("")
    log(
        "測試股票："
    )

    for stock in TEST_STOCKS:

        log(
            f"  {stock['symbol']} "
            f"{stock['name']}"
        )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    for index, stock in enumerate(
        TEST_STOCKS,
        start=1
    ):

        log("")
        log(
            f"##############################"
        )

        log(
            f"測試進度 "
            f"[{index}/{len(TEST_STOCKS)}]"
        )

        log(
            f"##############################"
        )

        record = test_stock(
            session,
            stock
        )

        results[
            stock["symbol"]
        ] = record

        # ----------------------------------------------------
        # 每檔完成後稍微等待
        # ----------------------------------------------------

        if index < len(TEST_STOCKS):

            time.sleep(
                REQUEST_DELAY
            )

    # --------------------------------------------------------
    # 儲存
    # --------------------------------------------------------

    save_results(
        results
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print_summary(
        results
    )

    complete = sum(
        1
        for record in results.values()
        if record[
            "status"
        ] == "complete"
    )

    failed = len(
        results
    ) - complete

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "TEST RESULT"
    )

    log(
        f"測試總數："
        f"{len(TEST_STOCKS)}"
    )

    log(
        f"PASS："
        f"{complete}"
    )

    log(
        f"FAILED："
        f"{failed}"
    )

    log(
        f"總耗時："
        f"{elapsed:.1f} 秒"
    )

    log(
        f"輸出："
        f"{OUTPUT_FILE}"
    )

    # --------------------------------------------------------
    # 重要：
    #
    # 只要有一檔失敗，
    # Action 就回傳失敗。
    #
    # 防止我們誤以為 parser 已經完全正確。
    # --------------------------------------------------------

    if complete != len(
        TEST_STOCKS
    ):

        log("")
        log(
            "❌ 10 檔尚未全部驗證通過"
        )

        return 1

    log("")
    log(
        "✓ 10 檔全部通過"
    )

    return 0


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )