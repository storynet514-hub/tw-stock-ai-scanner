#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CMoney 主力買賣超 10 檔驗證測試

目的：
============================================================
只測試 10 檔股票。

本測試不是正式 fetch_chip.py。

目的只有一個：

確認 CMoney「主力進出」頁面實際抓到的
到底是哪一個欄位。

特別檢查：

1. 日期
2. 表格完整 Header
3. 每一欄實際名稱
4. 每一列完整原始資料
5. 買賣超欄位
6. 解析後數值
7. 1D / 5D / 10D / 20D

不寫入正式 chip.json。

輸出：

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

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "TEST-10-V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "chip_test_10.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5


# ============================================================
# 固定 10 檔
# ============================================================

TEST_STOCKS = [
    ("2337", "旺宏"),
    ("2426", "鼎元"),
    ("2368", "金像電"),
    ("3081", "艾訊"),
    ("6770", "力積電"),
    ("6695", "芯鼎"),
    ("6914", "阜爾運通"),
    ("6753", "龍德造船"),
    ("6805", "富世達"),
    ("9904", "寶成"),
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
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 80)
    log(title)
    log("=" * 80)


# ============================================================
# Number
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

    if text.upper() in {
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "-",
        "--",
        "－",
        "—",
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
# Date
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    patterns = [
        r"\d{4}/\d{1,2}/\d{1,2}",
        r"\d{4}-\d{1,2}-\d{1,2}",
    ]

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            text
        )

        if match:
            return text.replace("-", "/")

    return None


# ============================================================
# Header normalization
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace("\n", "")
    text = text.replace("\r", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    return text.strip()


# ============================================================
# Request
# ============================================================

def request_page(session, symbol):

    urls = [
        CMONEY_URL.format(symbol=symbol),
        CMONEY_MOBILE_URL.format(symbol=symbol),
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

            if not response.text:
                raise RuntimeError(
                    "CMoney 回傳空白"
                )

            return response.text, url

        except Exception as exc:

            last_error = exc

    raise last_error


# ============================================================
# 掃描所有 Table
# ============================================================

def inspect_tables(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    tables = soup.find_all("table")

    table_results = []

    for table_index, table in enumerate(
        tables
    ):

        rows = table.find_all("tr")

        if not rows:
            continue

        table_data = []

        for row_index, tr in enumerate(
            rows
        ):

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            values = []

            for cell in cells:

                values.append(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )

            table_data.append({
                "row_index": row_index,
                "values": values,
            })

        if table_data:

            table_results.append({
                "table_index": table_index,
                "rows": table_data,
            })

    return table_results


# ============================================================
# 找出可能的籌碼表
# ============================================================

def identify_candidate_tables(
    table_results
):

    candidates = []

    for table in table_results:

        rows = table["rows"]

        for row in rows[:15]:

            values = row["values"]

            normalized = [
                normalize_header(v)
                for v in values
            ]

            joined = "|".join(
                normalized
            )

            score = 0

            if any(
                "日期" in x
                for x in normalized
            ):
                score += 2

            if any(
                "買賣超" in x
                for x in normalized
            ):
                score += 5

            if any(
                "主力" in x
                for x in normalized
            ):
                score += 3

            if any(
                "集中" in x
                for x in normalized
            ):
                score += 1

            if score > 0:

                candidates.append({
                    "table_index":
                        table["table_index"],
                    "header_row":
                        row["row_index"],
                    "headers":
                        values,
                    "score":
                        score,
                    "joined":
                        joined,
                })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates


# ============================================================
# 解析候選表
# ============================================================

def parse_candidate_table(
    table,
    header_row_index
):

    rows = table["rows"]

    header = None

    for row in rows:

        if row["row_index"] == header_row_index:

            header = row["values"]
            break

    if not header:
        return []

    headers = [
        normalize_header(x)
        for x in header
    ]

    date_index = None
    buy_sell_index = None

    for i, h in enumerate(headers):

        if date_index is None:

            if (
                h == "日期"
                or "日期" in h
            ):

                date_index = i

        if buy_sell_index is None:

            if (
                h == "買賣超"
                or (
                    "買賣超" in h
                    and "家數" not in h
                    and "集中" not in h
                )
            ):

                buy_sell_index = i

    parsed = []

    for row in rows:

        if row["row_index"] <= header_row_index:
            continue

        values = row["values"]

        if date_index is None:
            continue

        if len(values) <= date_index:
            continue

        date = normalize_date(
            values[date_index]
        )

        if not date:
            continue

        record = {
            "date": date,
            "raw_values": values,
        }

        if buy_sell_index is not None:

            if len(values) > buy_sell_index:

                record[
                    "buy_sell_raw"
                ] = values[
                    buy_sell_index
                ]

                record[
                    "buy_sell_value"
                ] = parse_number(
                    values[
                        buy_sell_index
                    ]
                )

        parsed.append(record)

    return parsed


# ============================================================
# 計算
# ============================================================

def calculate_periods(values):

    result = {
        "1d": None,
        "5d": None,
        "10d": None,
        "20d": None,
    }

    if len(values) >= 1:

        result["1d"] = round(
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result["5d"] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result["10d"] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result["20d"] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# 單檔測試
# ============================================================

def test_stock(
    session,
    symbol,
    name
):

    section(
        f"{symbol} {name}"
    )

    result = {
        "symbol": symbol,
        "name": name,
        "url": None,
        "table_count": 0,
        "candidate_tables": [],
        "selected_table": None,
        "history": [],
        "periods": {},
        "error": None,
    }

    try:

        html, url = request_page(
            session,
            symbol
        )

        result["url"] = url

        log(
            f"URL：{url}"
        )

        table_results = inspect_tables(
            html
        )

        result["table_count"] = len(
            table_results
        )

        log(
            f"HTML tables："
            f"{len(table_results)}"
        )

        candidates = identify_candidate_tables(
            table_results
        )

        log(
            f"候選表格："
            f"{len(candidates)}"
        )

        # ----------------------------------------------------
        # 顯示候選表格 Header
        # ----------------------------------------------------

        for candidate_index, candidate in enumerate(
            candidates[:10],
            start=1
        ):

            log("")
            log(
                f"候選 #{candidate_index}"
            )

            log(
                f"Table："
                f"{candidate['table_index']}"
            )

            log(
                f"Header row："
                f"{candidate['header_row']}"
            )

            log(
                f"Score："
                f"{candidate['score']}"
            )

            log(
                "欄位："
                + " | ".join(
                    candidate["headers"]
                )
            )

        result[
            "candidate_tables"
        ] = candidates[:10]

        if not candidates:

            raise RuntimeError(
                "找不到包含日期/買賣超的候選表格"
            )

        # ----------------------------------------------------
        # 逐候選表測試
        # ----------------------------------------------------

        selected = None

        for candidate in candidates:

            table_index = candidate[
                "table_index"
            ]

            header_row = candidate[
                "header_row"
            ]

            table = next(
                (
                    t
                    for t in table_results
                    if t["table_index"]
                    == table_index
                ),
                None
            )

            if table is None:
                continue

            parsed = parse_candidate_table(
                table,
                header_row
            )

            if parsed:

                selected = {
                    "candidate": candidate,
                    "rows": parsed,
                }

                break

        if selected is None:

            raise RuntimeError(
                "候選表格無法解析"
            )

        candidate = selected[
            "candidate"
        ]

        parsed = selected[
            "rows"
        ]

        result[
            "selected_table"
        ] = {
            "table_index":
                candidate["table_index"],
            "header_row":
                candidate["header_row"],
            "headers":
                candidate["headers"],
            "score":
                candidate["score"],
        }

        # ----------------------------------------------------
        # 顯示前 20 筆「完整原始欄位」
        # ----------------------------------------------------

        log("")
        log(
            "------------------------------------------------"
        )

        log(
            "前 20 筆原始表格資料"
        )

        log(
            "------------------------------------------------"
        )

        for index, row in enumerate(
            parsed[:20],
            start=1
        ):

            log(
                f"{index:02d}. "
                f"{row['date']} | "
                + " | ".join(
                    row["raw_values"]
                )
            )

        # ----------------------------------------------------
        # 買賣超
        # ----------------------------------------------------

        valid = []

        for row in parsed:

            value = row.get(
                "buy_sell_value"
            )

            if value is None:
                continue

            valid.append({
                "date":
                    row["date"],
                "raw":
                    row.get(
                        "buy_sell_raw"
                    ),
                "value":
                    value,
            })

        valid.sort(
            key=lambda x:
                datetime.strptime(
                    x["date"],
                    "%Y/%m/%d"
                ),
            reverse=True
        )

        valid = valid[:20]

        result["history"] = valid

        values = [
            x["value"]
            for x in valid
        ]

        periods = calculate_periods(
            values
        )

        result["periods"] = periods

        # ----------------------------------------------------
        # 顯示計算結果
        # ----------------------------------------------------

        log("")
        log(
            "------------------------------------------------"
        )

        log(
            "解析後買賣超"
        )

        log(
            "------------------------------------------------"
        )

        for index, row in enumerate(
            valid,
            start=1
        ):

            log(
                f"{index:02d}. "
                f"{row['date']} "
                f"raw={row['raw']} "
                f"value={row['value']}"
            )

        log("")
        log(
            "================================================"
        )

        log(
            f"{symbol} {name} 計算結果"
        )

        log(
            "================================================"
        )

        log(
            f"1D  = {periods['1d']}"
        )

        log(
            f"5D  = {periods['5d']}"
        )

        log(
            f"10D = {periods['10d']}"
        )

        log(
            f"20D = {periods['20d']}"
        )

        log(
            f"歷史 = {len(valid)}"
        )

        return result

    except Exception as exc:

        result["error"] = str(exc)

        log(
            f"❌ {symbol} {name} 失敗："
            f"{exc}"
        )

        return result


# ============================================================
# Main
# ============================================================

def main():

    start = time.time()

    section(
        f"CMoney 主力買賣超 10 檔驗證 {VERSION}"
    )

    log(
        "本測試只抓固定 10 檔"
    )

    log(
        "不執行正式 fetch_chip.py"
    )

    log(
        "不修改正式 chip.json"
    )

    log(
        f"輸出：{OUTPUT_FILE}"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    for index, (
        symbol,
        name
    ) in enumerate(
        TEST_STOCKS,
        start=1
    ):

        log("")
        log(
            f"[{index}/10] "
            f"{symbol} {name}"
        )

        result = test_stock(
            session,
            symbol,
            name
        )

        results[symbol] = result

        if index < len(
            TEST_STOCKS
        ):

            time.sleep(
                REQUEST_DELAY
            )

    # ========================================================
    # 儲存
    # ========================================================

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {
        "version": VERSION,
        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        "purpose":
            "CMoney 主力買賣超欄位驗證",
        "stocks":
            results,
    }

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # ========================================================
    # 最終摘要
    # ========================================================

    section(
        "10 檔測試摘要"
    )

    for symbol, name in TEST_STOCKS:

        result = results[symbol]

        periods = result.get(
            "periods",
            {}
        )

        log(
            f"{symbol} {name} | "
            f"1D={periods.get('1d')} | "
            f"5D={periods.get('5d')} | "
            f"10D={periods.get('10d')} | "
            f"20D={periods.get('20d')} | "
            f"歷史={len(result.get('history', []))}"
        )

    elapsed = time.time() - start

    log("")
    log(
        "=" * 80
    )

    log(
        "✓ 10 檔 CMoney 測試完成"
    )

    log(
        f"總耗時：{elapsed:.1f} 秒"
    )

    log(
        f"輸出：{OUTPUT_FILE}"
    )

    log(
        "=" * 80
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )
