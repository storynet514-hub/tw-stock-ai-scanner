#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
00838B TPEx 官方價格資料診斷
============================================================

目的：
1. 直接測試 TPEx 官方 endpoint
2. 不使用 Universe
3. 不使用 fetch_prices.py
4. 不使用 Yahoo
5. 不修改任何 Data 檔案
6. 保留 HTTP / JSON / aaData 原始結構資訊
7. 測試 00838B 是否存在於官方回應
8. 測試目前 fetch_prices.py 的 row[0]~row[7] parser
9. 找出 00838B 無法建立 OHLCV 的真正原因
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

import requests


SYMBOL = "00838B"

TPEX_URL = (
    "https://www.tpex.org.tw/"
    "web/stock/aftertrading/"
    "otc_quotes_no1430/"
    "stk_wn1430_result.php"
)

TIMEOUT = 30

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Connection": "keep-alive",
    }
)


def log(text=""):
    print(text, flush=True)


def normalize_text(value):
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


def normalize_symbol(value):
    text = normalize_text(value)

    if not text:
        return ""

    for suffix in (
        ".TW",
        ".TWO",
        ".HK",
    ):
        if text.upper().endswith(suffix):
            text = text[:-len(suffix)]
            break

    return text.strip()


def test_date(date_text):
    dt = datetime.strptime(
        date_text,
        "%Y-%m-%d",
    )

    roc_date = (
        f"{dt.year - 1911:03d}"
        f"/{dt.month:02d}"
        f"/{dt.day:02d}"
    )

    params = {
        "l": "zh-tw",
        "o": "json",
        "d": roc_date,
    }

    log("")
    log("=" * 72)
    log(f"TEST DATE：{date_text}")
    log(f"TPEx DATE：{roc_date}")
    log("=" * 72)

    try:
        response = SESSION.get(
            TPEX_URL,
            params=params,
            timeout=TIMEOUT,
        )

    except Exception as exc:
        log("")
        log("❌ HTTP REQUEST FAILED")
        log(f"原因：{repr(exc)}")
        return False

    log("")
    log("HTTP STATUS")
    log(f"status_code：{response.status_code}")
    log(f"content_type：{response.headers.get('Content-Type')}")
    log(f"content_length：{len(response.content)}")

    if response.status_code != 200:
        log("")
        log("❌ TPEx HTTP STATUS 非 200")
        log("")
        log("Response 前 2000 bytes：")
        print(
            response.text[:2000],
            flush=True,
        )
        return False

    try:
        data = response.json()

    except Exception as exc:
        log("")
        log("❌ RESPONSE 不是有效 JSON")
        log(f"JSON parse error：{repr(exc)}")
        log("")
        log("Response 前 5000 chars：")
        print(
            response.text[:5000],
            flush=True,
        )
        return False

    log("")
    log("JSON ROOT")
    log(f"type：{type(data).__name__}")

    if not isinstance(data, dict):
        log("❌ JSON root 不是 object")
        return False

    log("")
    log("ROOT KEYS")
    for key in data.keys():
        log(f"  {key}")

    aa_data = data.get("aaData")

    log("")
    log("aaData")
    log(f"type：{type(aa_data).__name__}")

    if not isinstance(aa_data, list):
        log("❌ aaData 不是 list")

        log("")
        log("完整 JSON：")
        print(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )[:10000],
            flush=True,
        )

        return False

    log(f"rows：{len(aa_data)}")

    if not aa_data:
        log("⚠️ aaData 為空")
        return False

    # --------------------------------------------------------
    # 搜尋 00838B
    # --------------------------------------------------------

    matches = []

    for index, row in enumerate(aa_data):

        if not isinstance(row, list):
            continue

        if not row:
            continue

        first = normalize_symbol(row[0])

        if first == SYMBOL:
            matches.append(
                (index, row)
            )

    log("")
    log("=" * 72)
    log("SEARCH 00838B")
    log("=" * 72)

    log(f"找到筆數：{len(matches)}")

    if matches:

        for index, row in matches:

            log("")
            log(f"✓ 找到 00838B")
            log(f"row index：{index}")
            log(f"row length：{len(row)}")

            log("")
            log("RAW ROW：")

            for i, value in enumerate(row):
                log(
                    f"  [{i}] "
                    f"{repr(value)}"
                )

            # ------------------------------------------------
            # 模擬目前正式程式 parser
            # ------------------------------------------------

            log("")
            log("目前 fetch_prices.py parser：")

            if len(row) < 8:

                log(
                    "❌ len(row) < 8"
                )

            else:

                log(
                    f"代號 row[0]："
                    f"{repr(row[0])}"
                )

                log(
                    f"收盤 row[2]："
                    f"{repr(row[2])}"
                )

                log(
                    f"開盤 row[4]："
                    f"{repr(row[4])}"
                )

                log(
                    f"最高 row[5]："
                    f"{repr(row[5])}"
                )

                log(
                    f"最低 row[6]："
                    f"{repr(row[6])}"
                )

                log(
                    f"成交量 row[7]："
                    f"{repr(row[7])}"
                )

            return True

    # --------------------------------------------------------
    # 沒找到時，搜尋所有可能包含 00838B 的資料
    # --------------------------------------------------------

    log("")
    log("❌ aaData 中沒有直接找到 00838B")

    possible = []

    for index, row in enumerate(aa_data):

        if not isinstance(row, list):
            continue

        for column_index, value in enumerate(row):

            text = normalize_text(value)

            if SYMBOL in text:
                possible.append(
                    (
                        index,
                        column_index,
                        row,
                    )
                )

    if possible:

        log("")
        log("⚠️ 發現包含 00838B 的資料：")

        for (
            index,
            column_index,
            row,
        ) in possible[:20]:

            log("")
            log(
                f"row={index}, "
                f"column={column_index}"
            )

            log(
                json.dumps(
                    row,
                    ensure_ascii=False,
                )
            )

    else:

        log("")
        log(
            "目前 response 中完全沒有 "
            "包含 00838B 的欄位。"
        )

    # --------------------------------------------------------
    # 顯示前 5 筆 row 結構
    # --------------------------------------------------------

    log("")
    log("=" * 72)
    log("SAMPLE aaData STRUCTURE")
    log("=" * 72)

    for index, row in enumerate(
        aa_data[:5]
    ):

        log("")
        log(
            f"ROW {index} "
            f"length={len(row) if isinstance(row, list) else 'N/A'}"
        )

        log(
            json.dumps(
                row,
                ensure_ascii=False,
            )
        )

    return False


def find_recent_dates(days=14):
    today = datetime.now().date()

    dates = []

    for offset in range(days):

        dt = (
            today
            - timedelta(days=offset)
        )

        # 星期一～五
        if dt.weekday() < 5:
            dates.append(
                dt.strftime("%Y-%m-%d")
            )

    return dates


def main():

    log("")
    log("=" * 72)
    log("00838B TPEx OFFICIAL PRICE DIAGNOSTIC")
    log("=" * 72)

    log("")
    log(f"測試商品：{SYMBOL}")
    log(f"Endpoint：{TPEX_URL}")
    log("資料來源：TPEx 官方")
    log("Yahoo：NO")
    log("Universe：NO")
    log("正式價格管線：NO")

    dates = find_recent_dates(14)

    found = False

    for date_text in dates:

        result = test_date(
            date_text
        )

        if result:
            found = True
            break

        time.sleep(1)

    log("")
    log("=" * 72)
    log("DIAGNOSTIC RESULT")
    log("=" * 72)

    if found:

        log("")
        log(
            "✓ TPEx 官方 response 中找到 00838B"
        )

        log(
            "✓ 已取得 RAW ROW"
        )

        log(
            "✓ 下一步可以依實際欄位修正 parser"
        )

        return 0

    log("")
    log(
        "❌ 最近測試日期沒有在 TPEx "
        "aaData 找到 00838B"
    )

    log("")
    log(
        "這不是直接證明 00838B 沒有市場資料。"
    )

    log(
        "目前只能確定："
        "stk_wn1430_result.php 的 response "
        "沒有被目前搜尋方式找到。"
    )

    return 1


if __name__ == "__main__":

    try:
        raise SystemExit(
            main()
        )

    except KeyboardInterrupt:

        log("")
        log("❌ 使用者中止")
        raise SystemExit(130)

    except Exception as exc:

        log("")
        log("=" * 72)
        log("DIAGNOSTIC FAILED")
        log("=" * 72)
        log(f"❌ {repr(exc)}")

        raise SystemExit(1)
