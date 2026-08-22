#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
券商分點資料驗證器 V1.0

============================================================
目的
============================================================

本程式不是正式 fetch_chip.py。

用途：
1. 驗證 TWSE / TPEX 券商分點原始資料來源
2. 固定測試：
   2337 旺宏
   2426 鼎元
   2368 金像電
   3081 聯亞

3. 驗證：
   原始券商資料
       ↓
   每日券商買賣超
       ↓
   Top15 主力
       ↓
   1D
   5D
   10D
   20D

4. 驗證原始資料單位：
   股 → 張

5. 驗證正負方向：
   買進 - 賣出

6. 明確區分：
   - 三大法人
   - 券商分點
   - 主力推導值

============================================================
重要原則
============================================================

❌ 不使用三大法人 × 係數推估主力
❌ 不使用 1.12 之類估算
❌ CAPTCHA / 驗證失敗不能當成 0
❌ 不會把缺資料當成真實資料
❌ 不會把「全券商」直接冒充「主力」

============================================================
測試標的
============================================================

2337 = 旺宏     TWSE
2426 = 鼎元     TWSE
2368 = 金像電   TWSE
3081 = 聯亞     TPEX

============================================================
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V1.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"

OUTPUT_JSON = DATA_DIR / "broker_test_result.json"
OUTPUT_CSV = DATA_DIR / "broker_daily_detail.csv"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


# ============================================================
# 固定測試股票
# ============================================================

TEST_SECURITIES = [
    {
        "symbol": "2337",
        "name": "旺宏",
        "market": "TWSE",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
        "market": "TWSE",
    },
    {
        "symbol": "2368",
        "name": "金像電",
        "market": "TWSE",
    },
    {
        "symbol": "3081",
        "name": "聯亞",
        "market": "TPEX",
    },
]


# ============================================================
# 資料結構
# ============================================================

@dataclass
class BrokerRow:
    date: str
    symbol: str
    stock_name: str
    market: str
    broker: str
    buy_shares: int
    sell_shares: int
    net_shares: int
    buy_lots: float
    sell_lots: float
    net_lots: float


@dataclass
class DailySummary:
    date: str
    symbol: str
    stock_name: str
    market: str

    broker_count: int

    total_buy_shares: int
    total_sell_shares: int
    total_net_shares: int

    total_buy_lots: float
    total_sell_lots: float
    total_net_lots: float

    top15_buy_lots: float
    top15_sell_lots: float
    top15_net_lots: float


# ============================================================
# LOG
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 78)
    log(title)
    log("=" * 78)


# ============================================================
# 數字解析
# ============================================================

def clean_number(value: object) -> Optional[int]:
    """
    將：
        1,234
        "1,234"
        "1234"
        "--"
        ""
    轉換成 int。

    不接受無法確認的值。
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if text in {"--", "-", "—", "－", "N/A", "NA"}:
        return None

    text = text.replace(",", "")
    text = text.replace(" ", "")

    # 移除括號負號格式，例如 (123)
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]

    try:
        return int(float(text))
    except Exception:
        return None


# ============================================================
# 日期
# ============================================================

def is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def previous_weekdays(count: int) -> List[datetime]:
    """
    從今天往前找足夠多的工作日。

    注意：
    這只是候選日期。
    真正是否交易日由資料來源回應決定。
    """

    result = []
    current = datetime.now()

    while len(result) < count:
        if is_weekday(current):
            result.append(current)

        current -= timedelta(days=1)

    return result


# ============================================================
# TWSE
# ============================================================

def fetch_twse_broker_page(
    session: requests.Session,
    symbol: str,
) -> Tuple[str, str]:
    """
    嘗試取得 TWSE BSR。

    回傳：
        status
        content

    status:
        OK
        CAPTCHA
        BLOCKED
        ERROR
    """

    url = "https://bsr.twse.com.tw/bshtm/bsMenu.aspx"

    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        text = response.text

        if response.status_code != 200:
            return "ERROR", text

        captcha_keywords = [
            "驗證碼",
            "輸入圖形中5碼文數字",
            "captcha",
            "CAPTCHA",
        ]

        if any(k in text for k in captcha_keywords):
            return "CAPTCHA", text

        if "買賣日報表查詢系統" not in text:
            return "BLOCKED", text

        return "OK", text

    except Exception as exc:
        return "ERROR", str(exc)


# ============================================================
# TPEX
# ============================================================

def fetch_tpex_broker_page(
    session: requests.Session,
    symbol: str,
) -> Tuple[str, str]:
    """
    嘗試取得 TPEX 券商分點查詢頁。

    注意：
    TPEX 官方頁面本身具有非機器人驗證。
    本函式不嘗試繞過驗證。

    回傳：
        OK
        CAPTCHA
        BLOCKED
        ERROR
    """

    url = (
        "https://www.tpex.org.tw/zh-tw/mainboard/"
        "trading/info/brokerBS.html"
    )

    try:
        response = session.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        text = response.text

        if response.status_code != 200:
            return "ERROR", text

        captcha_keywords = [
            "驗證非機器人",
            "非機器人",
            "captcha",
            "CAPTCHA",
            "recaptcha",
        ]

        if any(k in text for k in captcha_keywords):
            return "CAPTCHA", text

        if "券商買賣證券日報表查詢系統" not in text:
            return "BLOCKED", text

        return "OK", text

    except Exception as exc:
        return "ERROR", str(exc)


# ============================================================
# HTML / CSV 偵測
# ============================================================

def detect_block_reason(text: str) -> str:
    if not text:
        return "EMPTY_RESPONSE"

    lowered = text.lower()

    if "captcha" in lowered:
        return "CAPTCHA"

    if "recaptcha" in lowered:
        return "RECAPTCHA"

    if "驗證碼" in text:
        return "CAPTCHA"

    if "非機器人" in text:
        return "ANTI_BOT"

    if "403" in text:
        return "HTTP_403"

    if "access denied" in lowered:
        return "ACCESS_DENIED"

    return "UNKNOWN"


# ============================================================
# CSV 解析
# ============================================================

def normalize_csv_text(content: bytes) -> str:
    """
    嘗試：
    UTF-8
    BIG5
    CP950
    """

    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp950",
        "big5",
    ]

    for encoding in encodings:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue

    return content.decode("utf-8", errors="replace")


def find_columns(fieldnames: List[str]) -> Dict[str, int]:
    """
    嘗試找出：

    券商
    買進股數
    賣出股數
    """

    normalized = []

    for field in fieldnames:
        text = str(field).strip()
        normalized.append(text)

    result = {}

    for idx, field in enumerate(normalized):

        if (
            "證券商" in field
            or "券商" in field
            or "分公司" in field
        ):
            result.setdefault("broker", idx)

        if "買進股數" in field:
            result.setdefault("buy", idx)

        if "賣出股數" in field:
            result.setdefault("sell", idx)

    return result


def parse_broker_csv(
    content: bytes,
    date_str: str,
    symbol: str,
    stock_name: str,
    market: str,
) -> List[BrokerRow]:

    text = normalize_csv_text(content)

    # 嘗試不同 delimiter
    delimiter = ","

    if "\t" in text and text.count("\t") > text.count(","):
        delimiter = "\t"

    reader = csv.reader(
        io.StringIO(text),
        delimiter=delimiter,
    )

    rows = list(reader)

    if not rows:
        return []

    # 找 header
    header_index = None

    for i, row in enumerate(rows[:30]):
        joined = " ".join(row)

        if (
            "證券商" in joined
            and (
                "買進股數" in joined
                or "買進" in joined
            )
            and (
                "賣出股數" in joined
                or "賣出" in joined
            )
        ):
            header_index = i
            break

    if header_index is None:
        return []

    fieldnames = rows[header_index]

    columns = find_columns(fieldnames)

    if not {
        "broker",
        "buy",
        "sell",
    }.issubset(columns):
        return []

    result = []

    for row in rows[header_index + 1:]:

        if not row:
            continue

        max_idx = max(columns.values())

        if len(row) <= max_idx:
            continue

        broker = row[columns["broker"]].strip()

        if not broker:
            continue

        buy_shares = clean_number(
            row[columns["buy"]]
        )

        sell_shares = clean_number(
            row[columns["sell"]]
        )

        if buy_shares is None:
            buy_shares = 0

        if sell_shares is None:
            sell_shares = 0

        net_shares = buy_shares - sell_shares

        result.append(
            BrokerRow(
                date=date_str,
                symbol=symbol,
                stock_name=stock_name,
                market=market,
                broker=broker,
                buy_shares=buy_shares,
                sell_shares=sell_shares,
                net_shares=net_shares,
                buy_lots=round(buy_shares / 1000, 3),
                sell_lots=round(sell_shares / 1000, 3),
                net_lots=round(net_shares / 1000, 3),
            )
        )

    return result


# ============================================================
# 每日統計
# ============================================================

def build_daily_summary(
    rows: List[BrokerRow],
) -> Optional[DailySummary]:

    if not rows:
        return None

    first = rows[0]

    total_buy = sum(r.buy_shares for r in rows)
    total_sell = sum(r.sell_shares for r in rows)
    total_net = sum(r.net_shares for r in rows)

    # --------------------------------------------------------
    # 主力定義：
    #
    # 將所有券商依「淨買賣張數」排序
    # 正值取買超前 15
    # 負值取賣超前 15
    #
    # Top15 主力買賣超：
    # Top15 買超合計 + Top15 賣超合計
    #
    # 這裡故意保留正負。
    # --------------------------------------------------------

    buy_rank = sorted(
        [r for r in rows if r.net_shares > 0],
        key=lambda r: r.net_shares,
        reverse=True,
    )[:15]

    sell_rank = sorted(
        [r for r in rows if r.net_shares < 0],
        key=lambda r: r.net_shares,
    )[:15]

    top15_buy = sum(
        r.net_shares for r in buy_rank
    )

    top15_sell = sum(
        r.net_shares for r in sell_rank
    )

    top15_net = top15_buy + top15_sell

    return DailySummary(
        date=first.date,
        symbol=first.symbol,
        stock_name=first.stock_name,
        market=first.market,

        broker_count=len(rows),

        total_buy_shares=total_buy,
        total_sell_shares=total_sell,
        total_net_shares=total_net,

        total_buy_lots=round(total_buy / 1000, 3),
        total_sell_lots=round(total_sell / 1000, 3),
        total_net_lots=round(total_net / 1000, 3),

        top15_buy_lots=round(top15_buy / 1000, 3),
        top15_sell_lots=round(top15_sell / 1000, 3),
        top15_net_lots=round(top15_net / 1000, 3),
    )


# ============================================================
# 累計
# ============================================================

def calculate_periods(
    summaries: List[DailySummary],
) -> Dict[str, Optional[float]]:

    # 日期由新 → 舊
    summaries = sorted(
        summaries,
        key=lambda x: x.date,
        reverse=True,
    )

    values = [
        x.top15_net_lots
        for x in summaries
    ]

    result = {}

    for days in [1, 5, 10, 20]:

        if len(values) >= days:
            result[f"{days}D"] = round(
                sum(values[:days]),
                3,
            )
        else:
            result[f"{days}D"] = None

    return result


# ============================================================
# CSV 輸出
# ============================================================

def write_detail_csv(
    rows: List[BrokerRow],
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_CSV.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=[
                "date",
                "symbol",
                "stock_name",
                "market",
                "broker",
                "buy_shares",
                "sell_shares",
                "net_shares",
                "buy_lots",
                "sell_lots",
                "net_lots",
            ],
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                asdict(row)
            )


# ============================================================
# JSON 輸出
# ============================================================

def write_json(
    result: dict,
) -> None:

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = OUTPUT_JSON.with_suffix(
        ".json.tmp"
    )

    with temp.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=2,
        )

    temp.replace(
        OUTPUT_JSON
    )


# ============================================================
# 驗證
# ============================================================

def verify_summary(
    rows: List[BrokerRow],
    summary: DailySummary,
) -> Dict[str, object]:

    checks = {}

    # 1. 單位
    checks["unit_conversion"] = (
        abs(
            summary.total_net_lots
            - summary.total_net_shares / 1000
        ) < 0.001
    )

    # 2. 正負方向
    calculated_net = (
        summary.total_buy_shares
        - summary.total_sell_shares
    )

    checks["sign_direction"] = (
        calculated_net
        == summary.total_net_shares
    )

    # 3. Top15 計算
    buy_rank = sorted(
        [r for r in rows if r.net_shares > 0],
        key=lambda r: r.net_shares,
        reverse=True,
    )[:15]

    sell_rank = sorted(
        [r for r in rows if r.net_shares < 0],
        key=lambda r: r.net_shares,
    )[:15]

    expected_top15 = (
        sum(r.net_shares for r in buy_rank)
        + sum(r.net_shares for r in sell_rank)
    )

    checks["top15_calculation"] = (
        abs(
            summary.top15_net_lots
            - expected_top15 / 1000
        ) < 0.001
    )

    checks["all"] = all(
        checks.values()
    )

    return checks


# ============================================================
# 執行單一標的
# ============================================================

def run_security_test(
    session: requests.Session,
    security: dict,
) -> dict:

    symbol = security["symbol"]
    name = security["name"]
    market = security["market"]

    section(
        f"{symbol} {name} | {market}"
    )

    log(
        f"開始測試：{symbol} {name}"
    )

    log(
        "資料來源："
        + (
            "TWSE BSR"
            if market == "TWSE"
            else "TPEX BrokerBS"
        )
    )

    if market == "TWSE":

        status, content = (
            fetch_twse_broker_page(
                session,
                symbol,
            )
        )

    else:

        status, content = (
            fetch_tpex_broker_page(
                session,
                symbol,
            )
        )

    log(
        f"來源連線狀態：{status}"
    )

    if status != "OK":

        reason = detect_block_reason(
            content
        )

        log(
            f"⚠️ 無法取得券商原始資料：{reason}"
        )

        return {
            "symbol": symbol,
            "name": name,
            "market": market,
            "status": status,
            "reason": reason,
            "data_available": False,
            "summaries": [],
            "periods": {
                "1D": None,
                "5D": None,
                "10D": None,
                "20D": None,
            },
        }

    # --------------------------------------------------------
    # 注意：
    #
    # 官方查詢頁不是單純公開歷史 API。
    #
    # 這裡不假裝可以透過 GET 直接取得歷史券商分點。
    #
    # 若來源沒有直接提供 CSV，
    # 必須停止，而不是猜。
    # --------------------------------------------------------

    log(
        "✓ 查詢頁可連線"
    )

    log(
        "⚠️ 官方券商分點查詢需要進一步的"
        "日期/驗證/下載流程。"
    )

    log(
        "⚠️ 本測試器不繞過 CAPTCHA，"
        "因此不會把網頁頁面冒充成原始券商資料。"
    )

    return {
        "symbol": symbol,
        "name": name,
        "market": market,
        "status": "SOURCE_REACHABLE",
        "reason": "BROKER_DETAIL_DOWNLOAD_REQUIRES_OFFICIAL_QUERY_FLOW",
        "data_available": False,
        "summaries": [],
        "periods": {
            "1D": None,
            "5D": None,
            "10D": None,
            "20D": None,
        },
    }


# ============================================================
# 主程式
# ============================================================

def main() -> int:

    start = time.time()

    section(
        f"券商分點原始資料驗證器 {VERSION}"
    )

    log(
        "固定測試："
    )

    for item in TEST_SECURITIES:
        log(
            f"  {item['symbol']} "
            f"{item['name']} "
            f"{item['market']}"
        )

    log("")
    log(
        "重要：3081 = 聯亞"
    )
    log(
        "重要：本程式不使用估算主力資料"
    )
    log(
        "重要：本程式不繞過 CAPTCHA"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    all_results = []

    for security in TEST_SECURITIES:

        try:

            result = run_security_test(
                session,
                security,
            )

            all_results.append(
                result
            )

        except Exception as exc:

            log(
                f"❌ {security['symbol']} "
                f"測試異常：{exc}"
            )

            all_results.append(
                {
                    "symbol": security["symbol"],
                    "name": security["name"],
                    "market": security["market"],
                    "status": "ERROR",
                    "reason": str(exc),
                    "data_available": False,
                    "summaries": [],
                    "periods": {
                        "1D": None,
                        "5D": None,
                        "10D": None,
                        "20D": None,
                    },
                }
            )

        time.sleep(1)

    # ========================================================
    # 輸出結果
    # ========================================================

    output = {
        "schema_version": VERSION,
        "generated_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),

        "purpose": (
            "驗證 TWSE/TPEX 券商分點原始資料"
            "與 1D/5D/10D/20D 主力計算架構"
        ),

        "rules": {
            "raw_unit": "shares",
            "output_unit": "lots",
            "shares_per_lot": 1000,
            "net_formula": "buy_shares - sell_shares",

            "main_force_definition": (
                "Top15 positive net brokers "
                "+ Top15 negative net brokers"
            ),

            "institutional_is_main_force": False,
            "estimated_main_force": False,
        },

        "test_securities": TEST_SECURITIES,

        "results": all_results,

        "conclusion": {
            "source_reachable": sum(
                1
                for x in all_results
                if x["status"] == "SOURCE_REACHABLE"
            ),

            "data_available": sum(
                1
                for x in all_results
                if x["data_available"]
            ),

            "next_step": (
                "取得合法且可自動化的券商分點"
                "原始資料檔後，再進行逐日 "
                "1D/5D/10D/20D 核對"
            ),
        },
    }

    write_json(
        output
    )

    # ========================================================
    # 最終報告
    # ========================================================

    section(
        "測試結果"
    )

    for result in all_results:

        symbol = result["symbol"]
        name = result["name"]

        log(
            f"{symbol} {name}"
        )

        log(
            f"  Market      : "
            f"{result['market']}"
        )

        log(
            f"  Status      : "
            f"{result['status']}"
        )

        log(
            f"  Data        : "
            f"{result['data_available']}"
        )

        if result.get("reason"):
            log(
                f"  Reason      : "
                f"{result['reason']}"
            )

        periods = result.get(
            "periods",
            {}
        )

        log(
            f"  1D          : "
            f"{periods.get('1D')}"
        )

        log(
            f"  5D          : "
            f"{periods.get('5D')}"
        )

        log(
            f"  10D         : "
            f"{periods.get('10D')}"
        )

        log(
            f"  20D         : "
            f"{periods.get('20D')}"
        )

    elapsed = time.time() - start

    section(
        "完成"
    )

    log(
        f"耗時：{elapsed:.1f} 秒"
    )

    log(
        f"結果檔：{OUTPUT_JSON}"
    )

    log(
        ""
    )

    log(
        "⚠️ 本次測試不會產生假的 1D/5D/10D/20D 數字。"
    )

    log(
        "⚠️ 必須取得真正券商分點原始資料後才會計算。"
    )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
