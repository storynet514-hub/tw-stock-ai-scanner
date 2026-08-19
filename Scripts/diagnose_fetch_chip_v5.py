#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.0.1

診斷工具
diagnose_fetch_chip_v5.py

============================================================
目的
============================================================

本程式：

1. 不修改 fetch_chip.py
2. 不執行正式 fetch_all()
3. 不讀取 universe.json 進行全市場測試
4. 固定測試 4 檔：
   2337 旺宏
   2426 鼎元
   2368 金像電
   3081 聯亞
5. 分析正式 fetch_chip.py V5.0.1 的：
   - 首頁資料
   - discover_more_urls()
   - build_pagination_urls()
   - 延伸 URL
6. 找出異常歷史資料來源
7. 不把正常資料逐筆輸出到 Actions Log
8. 只輸出異常摘要
9. 完整結果寫入：

   Data/chip_v5_diagnosis.json

============================================================
重要
============================================================

本程式只是診斷。

絕對不修改：

Scripts/fetch_chip.py

也不產生新的：

Data/chip.json

============================================================
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time

from datetime import datetime
from pathlib import Path

import requests


# ============================================================
# 診斷版本
# ============================================================

DIAGNOSIS_VERSION = "V5.0.1"


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
        "name": "聯亞",
    },
]


# ============================================================
# HTTP Headers
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
        "text/html,"
        "application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,"
        "image/webp,"
        "*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# 載入正式 fetch_chip.py
# ============================================================

def load_fetch_chip():

    print("")
    print("=" * 72)
    print(
        "確認正式 fetch_chip.py "
        f"{DIAGNOSIS_VERSION}"
    )
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

    if spec.loader is None:

        raise RuntimeError(
            "無法載入 fetch_chip.py"
        )

    module = (
        importlib.util.module_from_spec(
            spec
        )
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

    if version != DIAGNOSIS_VERSION:

        raise RuntimeError(
            "目前 Scripts/fetch_chip.py "
            f"不是 {DIAGNOSIS_VERSION}，"
            f"而是 {version}"
        )

    print(
        f"✓ 正式 fetch_chip.py "
        f"{DIAGNOSIS_VERSION} 已確認"
    )

    return module


# ============================================================
# 日期解析
# ============================================================

def parse_date(
    date_text,
):

    if not date_text:

        return None

    try:

        return datetime.strptime(
            str(date_text),
            "%Y/%m/%d",
        )

    except Exception:

        return None


# ============================================================
# 分析單筆資料是否異常
# ============================================================

def classify_row(
    row,
    newest_date,
    seen_dates,
):

    reasons = []

    date_text = row.get(
        "date"
    )

    date_obj = parse_date(
        date_text
    )

    # --------------------------------------------------------
    # 日期格式錯誤
    # --------------------------------------------------------

    if date_obj is None:

        reasons.append(
            "INVALID_DATE"
        )

    else:

        # ----------------------------------------------------
        # 明顯舊資料
        # ----------------------------------------------------

        if date_obj.year < 2025:

            reasons.append(
                "ABNORMAL_OLD_DATE"
            )

        # ----------------------------------------------------
        # 距離首頁最新日期過遠
        # ----------------------------------------------------

        if newest_date is not None:

            gap_days = (
                newest_date
                - date_obj
            ).days

            if gap_days > 120:

                reasons.append(
                    "ABNORMAL_DATE_GAP"
                )

        # ----------------------------------------------------
        # 重複日期
        # ----------------------------------------------------

        if date_text in seen_dates:

            reasons.append(
                "DUPLICATE_DATE"
            )

    # --------------------------------------------------------
    # 主力數值檢查
    # --------------------------------------------------------

    value = row.get(
        "main_force"
    )

    if isinstance(
        value,
        float,
    ):

        if not value.is_integer():

            reasons.append(
                "DECIMAL_MAIN_FORCE"
            )

    return reasons


# ============================================================
# 分析頁面
# ============================================================

def analyze_page(
    module,
    html,
):

    rows = (
        module.parse_main_force_table(
            html
        )
    )

    rows = (
        module.clean_history(
            rows
        )
    )

    return rows


# ============================================================
# 分析資料列
# ============================================================

def analyze_rows(
    rows,
    newest_date,
):

    seen_dates = set()

    abnormal_rows = []

    for row in rows:

        reasons = classify_row(
            row,
            newest_date,
            seen_dates,
        )

        date_text = row.get(
            "date"
        )

        if date_text:

            seen_dates.add(
                date_text
            )

        if reasons:

            abnormal_rows.append(
                {
                    "date":
                        date_text,

                    "main_force":
                        row.get(
                            "main_force"
                        ),

                    "reasons":
                        reasons,
                }
            )

    return abnormal_rows


# ============================================================
# 測試單一 URL
# ============================================================

def inspect_url(
    module,
    session,
    url,
    url_type,
    newest_date,
):

    result = {

        "url_type":
            url_type,

        "url":
            url,

        "status_code":
            None,

        "row_count":
            0,

        "abnormal_count":
            0,

        "abnormal_rows":
            [],

        "error":
            None,
    }

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=30,
        )

        result[
            "status_code"
        ] = response.status_code

        if response.status_code != 200:

            result[
                "error"
            ] = (
                f"HTTP "
                f"{response.status_code}"
            )

            return result

        rows = analyze_page(
            module,
            response.text,
        )

        result[
            "row_count"
        ] = len(
            rows
        )

        abnormal_rows = (
            analyze_rows(
                rows,
                newest_date,
            )
        )

        result[
            "abnormal_rows"
        ] = abnormal_rows

        result[
            "abnormal_count"
        ] = len(
            abnormal_rows
        )

    except Exception as exc:

        result[
            "error"
        ] = str(exc)

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
        f"{symbol} {name}"
    )
    print("=" * 72)

    session = (
        requests.Session()
    )

    session.headers.update(
        HEADERS
    )

    # ========================================================
    # 1. 首頁
    # ========================================================

    print("")
    print(
        "[1] 首頁資料"
    )

    try:

        html, page_url = (
            module.request_page(
                session,
                symbol,
            )
        )

        homepage_rows = (
            analyze_page(
                module,
                html,
            )
        )

    except Exception as exc:

        print(
            f"❌ 首頁取得失敗：{exc}"
        )

        return {

            "symbol":
                symbol,

            "name":
                name,

            "error":
                str(exc),

            "homepage_count":
                0,

            "discovered_count":
                0,

            "pagination_count":
                0,

            "abnormal_count":
                0,

            "first_abnormal":
                None,
        }

    # ========================================================
    # 首頁最新日期
    # ========================================================

    valid_dates = []

    for row in homepage_rows:

        date_obj = parse_date(
            row.get(
                "date"
            )
        )

        if date_obj is not None:

            valid_dates.append(
                date_obj
            )

    newest_date = None

    if valid_dates:

        newest_date = max(
            valid_dates
        )

    print(
        f"首頁資料："
        f"{len(homepage_rows)} 筆"
    )

    if newest_date:

        print(
            "首頁最新日期："
            + newest_date.strftime(
                "%Y/%m/%d"
            )
        )

    # ========================================================
    # 首頁本身異常
    # ========================================================

    homepage_abnormal = (
        analyze_rows(
            homepage_rows,
            newest_date,
        )
    )

    print(
        "首頁異常："
        f"{len(homepage_abnormal)} 筆"
    )

    # ========================================================
    # 2. discover_more_urls()
    # ========================================================

    print("")
    print(
        "[2] discover_more_urls()"
    )

    discovered_urls = (
        module.discover_more_urls(
            html,
            page_url,
            symbol,
        )
    )

    print(
        "發現 URL："
        f"{len(discovered_urls)}"
    )

    # ========================================================
    # 3. build_pagination_urls()
    # ========================================================

    print("")
    print(
        "[3] build_pagination_urls()"
    )

    pagination_urls = (
        module.build_pagination_urls(
            page_url,
            symbol,
        )
    )

    print(
        "Pagination URL："
        f"{len(pagination_urls)}"
    )

    # ========================================================
    # 4. 測試 discovered URL
    # ========================================================

    print("")
    print(
        "[4] 測試 discovered URL"
    )

    discovered_results = []

    for index, url in enumerate(
        discovered_urls,
        start=1,
    ):

        result = inspect_url(
            module,
            session,
            url,
            "discovered",
            newest_date,
        )

        discovered_results.append(
            result
        )

        if (
            result[
                "abnormal_count"
            ]
            > 0
        ):

            print(
                "⚠ discovered #"
                f"{index}："
                f"{result['abnormal_count']}"
                " 筆異常"
            )

        if result[
            "error"
        ]:

            print(
                "⚠ discovered #"
                f"{index}："
                f"{result['error']}"
            )

        time.sleep(
            0.15
        )

    # ========================================================
    # 5. 測試 pagination URL
    # ========================================================

    print("")
    print(
        "[5] 測試 pagination URL"
    )

    pagination_results = []

    for index, url in enumerate(
        pagination_urls,
        start=1,
    ):

        result = inspect_url(
            module,
            session,
            url,
            "pagination",
            newest_date,
        )

        pagination_results.append(
            result
        )

        if (
            result[
                "abnormal_count"
            ]
            > 0
        ):

            print(
                "⚠ pagination #"
                f"{index}："
                f"{result['abnormal_count']}"
                " 筆異常"
            )

        if result[
            "error"
        ]:

            print(
                "⚠ pagination #"
                f"{index}："
                f"{result['error']}"
            )

        time.sleep(
            0.15
        )

    # ========================================================
    # 6. 統整異常來源
    # ========================================================

    all_results = (
        discovered_results
        + pagination_results
    )

    abnormal_sources = []

    for result in all_results:

        for abnormal in result[
            "abnormal_rows"
        ]:

            abnormal_sources.append(
                {

                    "url_type":
                        result[
                            "url_type"
                        ],

                    "url":
                        result[
                            "url"
                        ],

                    "status_code":
                        result[
                            "status_code"
                        ],

                    "date":
                        abnormal[
                            "date"
                        ],

                    "main_force":
                        abnormal[
                            "main_force"
                        ],

                    "reasons":
                        abnormal[
                            "reasons"
                        ],
                }
            )

    # ========================================================
    # 第一個異常
    # ========================================================

    first_abnormal = None

    if abnormal_sources:

        first_abnormal = (
            abnormal_sources[0]
        )

    # ========================================================
    # 終端摘要
    # ========================================================

    if first_abnormal:

        print("")
        print(
            "⚠ 第一個異常來源"
        )

        print(
            "  類型："
            + str(
                first_abnormal[
                    "url_type"
                ]
            )
        )

        print(
            "  日期："
            + str(
                first_abnormal[
                    "date"
                ]
            )
        )

        print(
            "  主力："
            + str(
                first_abnormal[
                    "main_force"
                ]
            )
        )

        print(
            "  原因："
            + ", ".join(
                first_abnormal[
                    "reasons"
                ]
            )
        )

        print(
            "  URL："
            + str(
                first_abnormal[
                    "url"
                ]
            )
        )

    else:

        print("")
        print(
            "✓ 未發現異常"
        )

    # ========================================================
    # 返回
    # ========================================================

    return {

        "symbol":
            symbol,

        "name":
            name,

        "homepage_url":
            page_url,

        "homepage_count":
            len(
                homepage_rows
            ),

        "homepage_abnormal":
            homepage_abnormal,

        "discovered_count":
            len(
                discovered_urls
            ),

        "pagination_count":
            len(
                pagination_urls
            ),

        "discovered_results":
            discovered_results,

        "pagination_results":
            pagination_results,

        "abnormal_sources":
            abnormal_sources,

        "abnormal_count":
            len(
                abnormal_sources
            ),

        "first_abnormal":
            first_abnormal,
    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_results(
    results,
    elapsed,
):

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = {

        "schema_version":
            (
                f"{DIAGNOSIS_VERSION}"
                "_DIAGNOSIS_2.0"
            ),

        "generated_at":
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "source":
            (
                "official fetch_chip.py "
                f"{DIAGNOSIS_VERSION}"
            ),

        "test_scope":
            "4 fixed stocks only",

        "test_stocks":
            TEST_STOCKS,

        "elapsed_seconds":
            round(
                elapsed,
                2,
            ),

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

    # ========================================================
    # 驗證 JSON
    # ========================================================

    with temp_file.open(
        "r",
        encoding="utf-8",
    ) as f:

        json.load(
            f
        )

    temp_file.replace(
        OUTPUT_FILE
    )

    print("")
    print(
        "✓ 診斷結果已寫入："
    )

    print(
        OUTPUT_FILE
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = (
        time.time()
    )

    print("")
    print("=" * 72)
    print(
        "台股 AI 選股系統"
    )
    print(
        f"fetch_chip.py "
        f"{DIAGNOSIS_VERSION}"
    )
    print(
        "四檔異常來源診斷"
    )
    print("=" * 72)

    print("")
    print(
        "固定測試："
    )

    print(
        "2337 旺宏"
    )

    print(
        "2426 鼎元"
    )

    print(
        "2368 金像電"
    )

    print(
        "3081 聯亞"
    )

    print("")
    print(
        "測試限制："
    )

    print(
        "✓ 不執行 fetch_all()"
    )

    print(
        "✓ 不測試 universe.json"
    )

    print(
        "✓ 不修改 fetch_chip.py"
    )

    print(
        "✓ 不輸出正常歷史明細"
    )

    print(
        "✓ 只輸出異常摘要"
    )

    try:

        # ====================================================
        # 載入正式程式
        # ====================================================

        module = (
            load_fetch_chip()
        )

        results = []

        # ====================================================
        # 固定 4 檔
        # ====================================================

        for stock in TEST_STOCKS:

            result = (
                diagnose_stock(
                    module,
                    stock,
                )
            )

            results.append(
                result
            )

        # ====================================================
        # 儲存
        # ====================================================

        elapsed = (
            time.time()
            - start_time
        )

        save_results(
            results,
            elapsed,
        )

        # ====================================================
        # 最終統計
        # ====================================================

        total_abnormal = sum(
            result.get(
                "abnormal_count",
                0,
            )
            for result in results
        )

        print("")
        print("=" * 72)
        print(
            f"{DIAGNOSIS_VERSION} "
            "診斷完成"
        )
        print("=" * 72)

        print(
            "固定測試股票："
            f"{len(TEST_STOCKS)} 檔"
        )

        print(
            "異常來源總數："
            f"{total_abnormal}"
        )

        print(
            "總耗時："
            f"{elapsed:.1f} 秒"
        )

        print(
            "診斷檔案："
            f"{OUTPUT_FILE}"
        )

        print("=" * 72)

        return 0

    except Exception as exc:

        print("")
        print("=" * 72)
        print(
            f"❌ {DIAGNOSIS_VERSION} "
            "診斷失敗"
        )
        print("=" * 72)

        print(
            f"原因：{exc}"
        )

        print("=" * 72)

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
