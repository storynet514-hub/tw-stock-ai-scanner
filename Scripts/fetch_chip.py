#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V5.1

============================================================
目的
============================================================

取得 CMoney「主力進出」：

1. 主力 1 日買賣超
2. 主力 5 日買賣超
3. 主力 10 日買賣超
4. 主力 20 日買賣超

============================================================
重要定義
============================================================

資料來源：

CMoney「主力進出」

只使用：

「買賣超」

單位：

張

正數 = 主力買超
負數 = 主力賣超

============================================================
絕對禁止
============================================================

不可使用：

5日集中
20日集中
家數差
其他欄位
文字 fallback 推測

============================================================
V5.1 修正
============================================================

上一版問題：

1. 探測大量未知 URL
2. 猜測 pagination
3. 使用 text fallback
4. 容易將其他數字欄位誤判成「買賣超」
5. 產生非整數主力買賣超

V5.1：

1. 嚴格鎖定 CMoney 主力進出表
2. 嚴格尋找「日期」
3. 嚴格尋找「買賣超」
4. 不使用 text fallback
5. 不使用 20日集中
6. 不猜 API
7. 測試模式只跑 4 檔
8. 若資料不足 20 日，直接報錯
9. 不產生可疑資料

============================================================
測試股票
============================================================

3081
2337
2368
2426

確認這 4 檔完全正確後，
再把 TEST_MODE 改成 False，
才跑完整 Universe。

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
# Version
# ============================================================

VERSION = "V5.1"


# ============================================================
# Path
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"


# ============================================================
# 測試模式
# ============================================================

# True：
# 只抓 4 檔測試股票
#
# False：
# 抓完整 universe.json
#
# 目前一定保持 True。
TEST_MODE = True


TEST_SYMBOLS = {
    "3081",
    "2337",
    "2368",
    "2426",
}


# ============================================================
# 基本設定
# ============================================================

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.20

MIN_HISTORY = 20


# ============================================================
# CMoney
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

    "Accept-Language":
        "zh-TW,zh;q=0.9,en;q=0.8",

    "Connection":
        "keep-alive",
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

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "universe.json 格式錯誤"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):

        raise RuntimeError(
            "universe.json items 不是 list"
        )

    stocks = []

    seen = set()

    for item in items:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = item.get(
            "code"
        )

        if symbol is None:

            symbol = item.get(
                "symbol"
            )

        if symbol is None:
            continue

        symbol = str(
            symbol
        ).strip().upper()

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

        if symbol in seen:
            continue

        seen.add(symbol)

        stocks.append({
            "symbol": symbol,

            "name": str(
                item.get(
                    "name",
                    ""
                )
            ).strip(),

            "market": str(
                item.get(
                    "market",
                    ""
                )
            ).strip(),
        })

    if not stocks:

        raise RuntimeError(
            "Universe 沒有任何合法股票"
        )

    # --------------------------------------------------------
    # 測試模式
    # --------------------------------------------------------

    if TEST_MODE:

        original_count = len(
            stocks
        )

        stocks = [
            stock
            for stock in stocks
            if stock["symbol"]
            in TEST_SYMBOLS
        ]

        stocks.sort(
            key=lambda x:
                list(
                    TEST_SYMBOLS
                ).index(
                    x["symbol"]
                )
            if x["symbol"]
            in TEST_SYMBOLS
            else 999
        )

        log(
            "⚠️ TEST_MODE = True"
        )

        log(
            f"完整 Universe："
            f"{original_count}"
        )

        log(
            f"本次只測試："
            f"{len(stocks)} 檔"
        )

    else:

        log(
            "TEST_MODE = False"
        )

        log(
            f"完整 Universe："
            f"{len(stocks)} 檔"
        )

    if not stocks:

        raise RuntimeError(
            "測試模式找不到指定股票"
        )

    return stocks


# ============================================================
# Number
# ============================================================

def parse_integer_number(
    text
):

    if text is None:
        return None

    text = str(
        text
    ).strip()

    if not text:
        return None

    # --------------------------------------------------------
    # 移除千分位
    # --------------------------------------------------------

    text = text.replace(
        ",",
        ""
    )

    text = text.replace(
        "張",
        ""
    )

    text = text.strip()

    # --------------------------------------------------------
    # CMoney 無資料
    # --------------------------------------------------------

    if text in {
        "",
        "-",
        "--",
        "－",
        "—",
        "N/A",
        "NA",
        "null",
        "None",
    }:

        return None

    # --------------------------------------------------------
    # 主力買賣超必須是整數
    #
    # CMoney 此欄位顯示例如：
    #
    # -14,816
    # 10,999
    # 35,474
    #
    # 不接受：
    #
    # 15.23
    # -8.7%
    # --------------------------------------------------------

    if not re.fullmatch(
        r"[+-]?\d+",
        text
    ):

        return None

    try:

        return int(
            text
        )

    except Exception:

        return None


# ============================================================
# Date
# ============================================================

def normalize_date(
    text
):

    if text is None:
        return None

    text = str(
        text
    ).strip()

    patterns = [
        r"^\d{4}/\d{1,2}/\d{1,2}$",
        r"^\d{4}-\d{1,2}-\d{1,2}$",
    ]

    for pattern in patterns:

        if re.fullmatch(
            pattern,
            text
        ):

            text = text.replace(
                "-",
                "/"
            )

            try:

                datetime.strptime(
                    text,
                    "%Y/%m/%d"
                )

                return text

            except Exception:

                return None

    return None


# ============================================================
# Header normalize
# ============================================================

def normalize_header(
    text
):

    if text is None:
        return ""

    text = str(
        text
    )

    text = text.replace(
        "\n",
        ""
    )

    text = text.replace(
        "\r",
        ""
    )

    text = text.replace(
        " ",
        ""
    )

    text = text.replace(
        "\u3000",
        ""
    )

    return text.strip()


# ============================================================
# Request
# ============================================================

def request_page(
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

            html = response.text

            if not html:

                raise RuntimeError(
                    "CMoney 回傳空白"
                )

            return html, url

        except Exception as exc:

            last_error = exc

    raise RuntimeError(
        f"{symbol} 無法取得 CMoney 頁面："
        f"{last_error}"
    )


# ============================================================
# 找正確的主力進出 table
# ============================================================

def find_main_force_table(
    soup
):

    tables = soup.find_all(
        "table"
    )

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        for header_row in rows[:15]:

            cells = header_row.find_all(
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

            has_date = any(
                header == "日期"
                for header in headers
            )

            has_close = any(
                header == "收盤價"
                for header in headers
            )

            has_force = any(
                header == "買賣超"
                for header in headers
            )

            has_5d = any(
                header == "5日集中"
                for header in headers
            )

            has_20d = any(
                header == "20日集中"
                for header in headers
            )

            # ------------------------------------------------
            # 必須符合 CMoney 主力進出表結構
            # ------------------------------------------------

            if (
                has_date
                and has_close
                and has_force
                and has_5d
                and has_20d
            ):

                return table, headers

    return None, None


# ============================================================
# Parse exact table
# ============================================================

def parse_main_force_table(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    table, headers = (
        find_main_force_table(
            soup
        )
    )

    if table is None:

        return []

    date_index = -1
    force_index = -1

    for index, header in enumerate(
        headers
    ):

        if header == "日期":

            date_index = index

        elif header == "買賣超":

            force_index = index

    if (
        date_index < 0
        or force_index < 0
    ):

        return []

    rows = table.find_all(
        "tr"
    )

    history = []

    for row in rows:

        cells = row.find_all(
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

        main_force = (
            parse_integer_number(
                values[force_index]
            )
        )

        if main_force is None:

            # ------------------------------------------------
            # 注意：
            # 不再 fallback 到其他數字。
            # ------------------------------------------------

            continue

        history.append({
            "date": date,
            "main_force": main_force,
        })

    return clean_history(
        history
    )


# ============================================================
# Clean
# ============================================================

def clean_history(
    rows
):

    unique = {}

    for row in rows:

        date = row.get(
            "date"
        )

        value = row.get(
            "main_force"
        )

        if not date:
            continue

        if not isinstance(
            value,
            int
        ):
            continue

        unique[date] = value

    result = []

    for date, value in unique.items():

        result.append({
            "date": date,
            "main_force": value,
        })

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
# Fetch history
# ============================================================

def fetch_history(
    session,
    symbol
):

    html, url = request_page(
        session,
        symbol
    )

    history = parse_main_force_table(
        html
    )

    log(
        f"   CMoney 主力進出表："
        f"{len(history)} 筆"
    )

    if not history:

        raise RuntimeError(
            "找不到有效的 CMoney "
            "主力進出「買賣超」資料"
        )

    # --------------------------------------------------------
    # 目前 CMoney 首頁通常約 10 筆。
    #
    # 這裡不再亂猜 API。
    #
    # 如果只有 10 筆，就明確失敗。
    # 不拿其他欄位湊 20D。
    # --------------------------------------------------------

    if len(history) < MIN_HISTORY:

        raise RuntimeError(
            f"CMoney 主力進出目前只取得 "
            f"{len(history)} 筆「買賣超」，"
            f"不足 {MIN_HISTORY} 筆。"
            f"目前不允許使用其他欄位或 "
            f"文字 fallback 補資料。"
        )

    return history[:MIN_HISTORY]


# ============================================================
# Calculate
# ============================================================

def calculate_periods(
    history
):

    values = [
        row["main_force"]
        for row in history
    ]

    if len(values) < 20:

        raise RuntimeError(
            "calculate_periods："
            "history 不足 20 筆"
        )

    return {

        "main_force_1d":
            values[0],

        "main_force_5d":
            sum(
                values[:5]
            ),

        "main_force_10d":
            sum(
                values[:10]
            ),

        "main_force_20d":
            sum(
                values[:20]
            ),

        "history_count":
            len(values),
    }


# ============================================================
# Status
# ============================================================

def get_status(
    record
):

    fields = [
        "main_force_1d",
        "main_force_5d",
        "main_force_10d",
        "main_force_20d",
    ]

    if all(
        record.get(field)
        is not None
        for field in fields
    ):

        return "complete"

    return "insufficient"


# ============================================================
# Fetch all
# ============================================================

def fetch_all(
    stocks
):

    section(
        "開始取得 CMoney 主力買賣超"
    )

    total = len(
        stocks
    )

    log(
        f"本次處理：{total} 檔"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0

    insufficient = 0

    for index, stock in enumerate(
        stocks,
        start=1
    ):

        symbol = stock[
            "symbol"
        ]

        name = stock[
            "name"
        ]

        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

        record = {

            "symbol":
                symbol,

            "name":
                name,

            "market":
                stock["market"],

            "source":
                "CMoney",

            "main_force_1d":
                None,

            "main_force_5d":
                None,

            "main_force_10d":
                None,

            "main_force_20d":
                None,

            "history_count":
                0,

            "status":
                "insufficient",

            "history":
                [],

            "error":
                None,
        }

        try:

            history = fetch_history(
                session,
                symbol
            )

            periods = calculate_periods(
                history
            )

            record.update(
                periods
            )

            record["history"] = (
                history[:20]
            )

            record["status"] = (
                get_status(
                    record
                )
            )

            if (
                record["status"]
                == "complete"
            ):

                complete += 1

            else:

                insufficient += 1

            log(
                f"   1D  = "
                f"{record['main_force_1d']}"
            )

            log(
                f"   5D  = "
                f"{record['main_force_5d']}"
            )

            log(
                f"   10D = "
                f"{record['main_force_10d']}"
            )

            log(
                f"   20D = "
                f"{record['main_force_20d']}"
            )

            log(
                f"   history = "
                f"{record['history_count']}"
            )

            # ------------------------------------------------
            # 顯示實際 20 日資料
            # ------------------------------------------------

            log(
                "   最近20日買賣超："
            )

            for row in history[:20]:

                log(
                    f"      "
                    f"{row['date']} "
                    f"{row['main_force']:+d}"
                )

        except Exception as exc:

            insufficient += 1

            record["error"] = (
                str(exc)
            )

            log(
                f"   ❌ "
                f"{exc}"
            )

        results[
            symbol
        ] = record

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        complete,
        insufficient
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results,
    total,
    complete,
    insufficient
):

    section(
        "籌碼資料驗證"
    )

    valid_1d = 0

    valid_5d = 0

    valid_10d = 0

    valid_20d = 0

    for symbol, record in (
        results.items()
    ):

        if record.get(
            "main_force_1d"
        ) is not None:

            valid_1d += 1

        if record.get(
            "main_force_5d"
        ) is not None:

            valid_5d += 1

        if record.get(
            "main_force_10d"
        ) is not None:

            valid_10d += 1

        if record.get(
            "main_force_20d"
        ) is not None:

            valid_20d += 1

        if (
            record.get(
                "status"
            )
            == "complete"
        ):

            history = record.get(
                "history",
                []
            )

            if len(history) != 20:

                raise RuntimeError(
                    f"{symbol} "
                    "history 不是20筆"
                )

            for row in history:

                if not isinstance(
                    row.get(
                        "main_force"
                    ),
                    int
                ):

                    raise RuntimeError(
                        f"{symbol} "
                        "存在非整數買賣超"
                    )

    log(
        f"Universe：{total}"
    )

    log(
        f"完整：{complete}"
    )

    log(
        f"不足：{insufficient}"
    )

    log(
        f"1D 有效：{valid_1d}"
    )

    log(
        f"5D 有效：{valid_5d}"
    )

    log(
        f"10D 有效：{valid_10d}"
    )

    log(
        f"20D 有效：{valid_20d}"
    )

    if valid_20d != total:

        raise RuntimeError(
            "本次測試沒有全部取得 "
            "有效主力20D。"
        )

    log(
        "✓ 所有測試股票 20D 通過"
    )


# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    total,
    complete,
    insufficient
):

    section(
        "寫入 Data/chip.json"
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

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "main_force_1d":
                "最近1個交易日主力買賣超",

            "main_force_5d":
                "最近5個交易日主力買賣超加總",

            "main_force_10d":
                "最近10個交易日主力買賣超加總",

            "main_force_20d":
                "最近20個交易日主力買賣超加總",

            "NOT_main_force_20d":
                "CMoney 20日集中不是主力20日買賣超",

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",
        },

        "test_mode":
            TEST_MODE,

        "test_symbols":
            sorted(
                TEST_SYMBOLS
            ),

        "universe_count":
            total,

        "statistics": {

            "complete":
                complete,

            "insufficient":
                insufficient,
        },

        "stocks":
            results,
    }

    temp_file = CHIP_FILE.with_suffix(
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
    # 寫入驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(
            f
        )

    verify_stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        verify_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    if len(
        verify_stocks
    ) != len(
        results
    ):

        raise RuntimeError(
            "chip.json 股票數量錯誤"
        )

    for symbol, record in (
        verify_stocks.items()
    ):

        if (
            record.get(
                "status"
            )
            != "complete"
        ):

            raise RuntimeError(
                f"{symbol} "
                "不是 complete"
            )

        history = record.get(
            "history",
            []
        )

        if len(history) != 20:

            raise RuntimeError(
                f"{symbol} "
                "history 必須為20筆"
            )

        for row in history:

            if not isinstance(
                row.get(
                    "main_force"
                ),
                int
            ):

                raise RuntimeError(
                    f"{symbol} "
                    "history 存在非整數值"
                )

        # ----------------------------------------------------
        # 再次驗證計算
        # ----------------------------------------------------

        values = [
            row["main_force"]
            for row in history
        ]

        if record[
            "main_force_1d"
        ] != values[0]:

            raise RuntimeError(
                f"{symbol} 1D 計算錯誤"
            )

        if record[
            "main_force_5d"
        ] != sum(
            values[:5]
        ):

            raise RuntimeError(
                f"{symbol} 5D 計算錯誤"
            )

        if record[
            "main_force_10d"
        ] != sum(
            values[:10]
        ):

            raise RuntimeError(
                f"{symbol} 10D 計算錯誤"
            )

        if record[
            "main_force_20d"
        ] != sum(
            values[:20]
        ):

            raise RuntimeError(
                f"{symbol} 20D 計算錯誤"
            )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 建立成功"
    )

    log(
        f"股票：{len(results)}"
    )

    log(
        f"檔案：{CHIP_FILE}"
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
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        stocks = load_universe()

        (
            results,
            complete,
            insufficient
        ) = fetch_all(
            stocks
        )

        validate(
            results,
            len(stocks),
            complete,
            insufficient
        )

        save_chip(
            results,
            len(stocks),
            complete,
            insufficient
        )

        elapsed = (
            time.time()
            - start_time
        )

        log("")
        log("=" * 72)

        log(
            "✓ fetch_chip.py 執行完成"
        )

        log("=" * 72)

        log(
            f"完整：{complete}"
        )

        log(
            f"不足：{insufficient}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            "❌ fetch_chip.py 執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        if CHIP_FILE.exists():

            log(
                "⚠️ 保留既有 chip.json"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )