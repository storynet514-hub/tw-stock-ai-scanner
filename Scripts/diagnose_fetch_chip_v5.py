#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.0

診斷工具
diagnose_fetch_chip_v5.py

============================================================
目的
============================================================

本程式：

1. 不修改 fetch_chip.py
2. 不修改 V5.0 原始函式
3. 直接載入正式 fetch_chip.py V5.0
4. 固定測試：
   2337 旺宏
   2426 鼎元
   2368 金像電
   3081 艾訊
5. 分析 V5.0 的：
   - 首頁資料
   - discover_more_urls()
   - build_pagination_urls()
   - 每一個延伸 URL
6. 找出哪一個 URL 開始產生錯誤歷史資料
7. 特別標記：
   - 非近期交易日
   - 2024/2023/2022 等異常舊日期
   - 小數型主力數值
   - 重複日期
   - 不合理日期跳躍

============================================================
重要
============================================================

本程式只是診斷。

絕對不修改：

Scripts/fetch_chip.py

也不產生新的 chip.json。

輸出：

Data/chip_v5_diagnosis.json
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 路徑
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

SCRIPT_FILE = (
    BASE_DIR
    / "Scripts"
    / "fetch_chip.py"
)

DATA_DIR = (
    BASE_DIR
    / "Data"
)

OUTPUT_FILE = (
    DATA_DIR
    / "chip_v5_diagnosis.json"
)


# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {
        "symbol": "2337",
        "name": "旺宏",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
    },
    {
        "symbol": "2368",
        "name": "金像電",
    },
    {
        "symbol": "3081",
        "name": "艾訊",
    },
]


# ============================================================
# 載入正式 fetch_chip.py
# ============================================================

def load_fetch_chip():

    print("")
    print("=" * 72)
    print("載入正式 fetch_chip.py")
    print("=" * 72)

    if not SCRIPT_FILE.exists():

        raise RuntimeError(
            f"找不到正式程式：{SCRIPT_FILE}"
        )

    spec = importlib.util.spec_from_file_location(
        "fetch_chip_v5_official",
        SCRIPT_FILE,
    )

    if spec is None:
        raise RuntimeError(
            "無法建立 fetch_chip module"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    if spec.loader is None:
        raise RuntimeError(
            "無法載入 fetch_chip.py"
        )

    spec.loader.exec_module(
        module
    )

    version = getattr(
        module,
        "VERSION",
        None,
    )

    print(
        f"正式版本：{version}"
    )

    if version != "V5.0":

        raise RuntimeError(
            "目前 Scripts/fetch_chip.py "
            f"不是 V5.0，而是 {version}"
        )

    print(
        "✓ 正式 fetch_chip.py V5.0 已確認"
    )

    return module


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
# 日期
# ============================================================

def parse_date(date_text):

    if not date_text:
        return None

    try:

        return datetime.strptime(
            date_text,
            "%Y/%m/%d"
        )

    except Exception:

        return None


# ============================================================
# 判斷日期是否合理
# ============================================================

def classify_date(
    date_text,
    newest_date=None,
):

    dt = parse_date(
        date_text
    )

    if dt is None:

        return "INVALID_DATE"

    # --------------------------------------------------------
    # 明顯舊資料
    # --------------------------------------------------------

    if dt.year < 2025:

        return "ABNORMAL_OLD_DATE"

    # --------------------------------------------------------
    # 如果有最新日期
    # --------------------------------------------------------

    if newest_date is not None:

        gap = (
            newest_date - dt
        ).days

        if gap > 120:

            return "ABNORMAL_DATE_GAP"

    # --------------------------------------------------------
    # 正常
    # --------------------------------------------------------

    return "OK"


# ============================================================
# 分析單頁
# ============================================================

def analyze_page(
    module,
    html,
):

    rows = module.parse_main_force_table(
        html
    )

    rows = module.clean_history(
        rows
    )

    return rows


# ============================================================
# 印出資料
# ============================================================

def print_rows(
    rows,
    title,
    newest_date=None,
):

    print("")
    print("-" * 72)
    print(title)
    print("-" * 72)

    print(
        f"資料筆數：{len(rows)}"
    )

    for index, row in enumerate(
        rows,
        start=1,
    ):

        date_text = row.get(
            "date"
        )

        value = row.get(
            "main_force"
        )

        classification = classify_date(
            date_text,
            newest_date,
        )

        marker = ""

        if classification != "OK":
            marker = "  <<< 異常"

        print(
            f"{index:>3}. "
            f"{date_text:<12} "
            f"{str(value):>12}"
            f"  [{classification}]"
            f"{marker}"
        )


# ============================================================
# URL 分析
# ============================================================

def analyze_url(
    module,
    session,
    symbol,
    url,
    url_type,
):

    result = {
        "url_type": url_type,
        "url": url,
        "status_code": None,
        "success": False,
        "rows": [],
        "error": None,
    }

    print("")
    print(
        f"URL 類型：{url_type}"
    )

    print(
        f"URL：{url}"
    )

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=30,
        )

        result[
            "status_code"
        ] = response.status_code

        print(
            f"HTTP：{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "✗ HTTP 非 200"
            )

            return result

        rows = analyze_page(
            module,
            response.text,
        )

        result[
            "rows"
        ] = rows

        result[
            "success"
        ] = bool(
            rows
        )

        print(
            f"解析筆數：{len(rows)}"
        )

        if rows:

            for row in rows:

                print(
                    "   "
                    f"{row['date']} "
                    f"{row['main_force']}"
                )

        else:

            print(
                "   沒有解析到主力資料"
            )

    except Exception as exc:

        result[
            "error"
        ] = str(exc)

        print(
            f"✗ 錯誤：{exc}"
        )

    return result


# ============================================================
# 單一股票診斷
# ============================================================

def diagnose_stock(
    module,
    stock,
):

    symbol = stock[
        "symbol"
    ]

    name = stock[
        "name"
    ]

    print("")
    print("")
    print("=" * 72)
    print(
        f"{symbol} {name} V5.0 資料來源診斷"
    )
    print("=" * 72)

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    # --------------------------------------------------------
    # 1. 正式 request_page()
    # --------------------------------------------------------

    print("")
    print("[1] 正式首頁 request_page()")

    html, page_url = module.request_page(
        session,
        symbol,
    )

    print(
        f"首頁 URL：{page_url}"
    )

    homepage_rows = analyze_page(
        module,
        html,
    )

    newest_date = None

    if homepage_rows:

        newest_date = max(
            parse_date(
                row["date"]
            )
            for row in homepage_rows
            if parse_date(
                row["date"]
            ) is not None
        )

    print_rows(
        homepage_rows,
        "首頁解析結果",
        newest_date,
    )

    # --------------------------------------------------------
    # 2. discover_more_urls()
    # --------------------------------------------------------

    print("")
    print("[2] discover_more_urls()")

    discovered_urls = (
        module.discover_more_urls(
            html,
            page_url,
            symbol,
        )
    )

    print(
        f"發現 URL 數量："
        f"{len(discovered_urls)}"
    )

    for index, url in enumerate(
        discovered_urls,
        start=1,
    ):

        print(
            f"{index:>3}. {url}"
        )

    # --------------------------------------------------------
    # 3. build_pagination_urls()
    # --------------------------------------------------------

    print("")
    print("[3] build_pagination_urls()")

    pagination_urls = (
        module.build_pagination_urls(
            page_url,
            symbol,
        )
    )

    print(
        f"分頁 URL 數量："
        f"{len(pagination_urls)}"
    )

    for index, url in enumerate(
        pagination_urls,
        start=1,
    ):

        print(
            f"{index:>3}. {url}"
        )

    # --------------------------------------------------------
    # 4. 逐一測試 discovered URL
    # --------------------------------------------------------

    discovered_results = []

    print("")
    print("=" * 72)
    print("逐一測試 discover_more_urls()")
    print("=" * 72)

    for index, url in enumerate(
        discovered_urls,
        start=1,
    ):

        print("")
        print(
            f"[DISCOVERED {index}/"
            f"{len(discovered_urls)}]"
        )

        result = analyze_url(
            module,
            session,
            symbol,
            url,
            "discovered",
        )

        discovered_results.append(
            result
        )

        time.sleep(
            0.15
        )

    # --------------------------------------------------------
    # 5. 逐一測試 pagination URL
    # --------------------------------------------------------

    pagination_results = []

    print("")
    print("=" * 72)
    print("逐一測試 build_pagination_urls()")
    print("=" * 72)

    for index, url in enumerate(
        pagination_urls,
        start=1,
    ):

        print("")
        print(
            f"[PAGINATION {index}/"
            f"{len(pagination_urls)}]"
        )

        result = analyze_url(
            module,
            session,
            symbol,
            url,
            "pagination",
        )

        pagination_results.append(
            result
        )

        time.sleep(
            0.15
        )

    # --------------------------------------------------------
    # 6. 建立異常摘要
    # --------------------------------------------------------

    abnormal_sources = []

    all_results = (
        discovered_results
        + pagination_results
    )

    for result in all_results:

        rows = result.get(
            "rows",
            []
        )

        for row in rows:

            classification = classify_date(
                row.get("date"),
                newest_date,
            )

            if classification != "OK":

                abnormal_sources.append({
                    "url_type":
                        result.get(
                            "url_type"
                        ),
                    "url":
                        result.get(
                            "url"
                        ),
                    "date":
                        row.get(
                            "date"
                        ),
                    "main_force":
                        row.get(
                            "main_force"
                        ),
                    "classification":
                        classification,
                })

    # --------------------------------------------------------
    # 7. 找出第一個異常來源
    # --------------------------------------------------------

    first_abnormal = None

    if abnormal_sources:

        first_abnormal = (
            abnormal_sources[0]
        )

    print("")
    print("=" * 72)
    print("異常資料來源摘要")
    print("=" * 72)

    print(
        f"異常資料筆數："
        f"{len(abnormal_sources)}"
    )

    if first_abnormal:

        print("")
        print(
            "⚠️ 第一個異常來源："
        )

        print(
            f"類型："
            f"{first_abnormal['url_type']}"
        )

        print(
            f"日期："
            f"{first_abnormal['date']}"
        )

        print(
            f"數值："
            f"{first_abnormal['main_force']}"
        )

        print(
            f"URL："
            f"{first_abnormal['url']}"
        )

    else:

        print(
            "✓ 沒有發現明顯異常日期"
        )

    return {
        "symbol": symbol,
        "name": name,
        "homepage_url": page_url,
        "homepage_rows": homepage_rows,
        "discovered_urls": discovered_urls,
        "pagination_urls": pagination_urls,
        "discovered_results": discovered_results,
        "pagination_results": pagination_results,
        "abnormal_sources": abnormal_sources,
        "first_abnormal": first_abnormal,
    }


# ============================================================
# 儲存診斷結果
# ============================================================

def save_results(
    results,
):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = {
        "schema_version":
            "V5.0_DIAGNOSIS_1.0",

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            "official fetch_chip.py V5.0",

        "official_script":
            str(SCRIPT_FILE),

        "test_stocks":
            TEST_STOCKS,

        "results":
            results,
    }

    temp_file = (
        OUTPUT_FILE.with_suffix(
            ".json.tmp"
        )
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

    with temp_file.open(
        "r",
        encoding="utf-8",
    ) as f:

        json.load(f)

    temp_file.replace(
        OUTPUT_FILE
    )

    print("")
    print("=" * 72)
    print("診斷結果寫入")
    print("=" * 72)

    print(
        f"✓ {OUTPUT_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    print("")
    print("=" * 72)
    print(
        "台股 AI 選股系統"
    )
    print(
        "fetch_chip.py V5.0 "
        "資料來源診斷測試"
    )
    print("=" * 72)

    print(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        module = load_fetch_chip()

        results = []

        for stock in TEST_STOCKS:

            result = diagnose_stock(
                module,
                stock,
            )

            results.append(
                result
            )

        save_results(
            results
        )

        # ----------------------------------------------------
        # 最終摘要
        # ----------------------------------------------------

        print("")
        print("=" * 72)
        print("V5.0 診斷完成")
        print("=" * 72)

        total_abnormal = 0

        for result in results:

            abnormal_count = len(
                result.get(
                    "abnormal_sources",
                    []
                )
            )

            total_abnormal += (
                abnormal_count
            )

            first = result.get(
                "first_abnormal"
            )

            print("")
            print(
                f"{result['symbol']} "
                f"{result['name']}"
            )

            print(
                f"   首頁："
                f"{len(result['homepage_rows'])} 筆"
            )

            print(
                f"   異常資料："
                f"{abnormal_count} 筆"
            )

            if first:

                print(
                    "   第一個異常："
                    f"{first['date']} "
                    f"{first['main_force']}"
                )

                print(
                    "   來源："
                    f"{first['url']}"
                )

            else:

                print(
                    "   ✓ 未發現明顯異常"
                )

        elapsed = (
            time.time()
            - start_time
        )

        print("")
        print("=" * 72)

        if total_abnormal > 0:

            print(
                "⚠️ 已找到異常資料來源"
            )

            print(
                f"異常資料總數："
                f"{total_abnormal}"
            )

            print(
                "下一步應針對異常 URL "
                "修正 V5.0 的延伸資料取得邏輯。"
            )

        else:

            print(
                "✓ 未找到明顯異常來源"
            )

        print("=" * 72)

        print(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        print(
            f"診斷檔案："
            f"{OUTPUT_FILE}"
        )

        return 0

    except Exception as exc:

        print("")
        print("=" * 72)
        print("❌ V5.0 診斷失敗")
        print("=" * 72)

        print(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
