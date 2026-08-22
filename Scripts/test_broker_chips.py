#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
test_broker_chip.py V1.0

用途
============================================================
專門測試「全券商分點主力買賣超」資料來源。

固定測試：
    2337 旺宏
    2426 鼎元
    2368 金像電
    3081 聯亞

本程式：
1. 不修改 Data/chip.json
2. 不修改任何正式資料
3. 不把三大法人資料冒充主力分點
4. 不使用任何估算倍率
5. 嘗試檢測官方 TWSE / TPEX 公開資料端點
6. 區分：
   - 真正逐券商分點資料
   - 券商排行資料
   - 三大法人資料
   - 一般行情資料
7. 若取得真正分點資料，才計算：
   - 每日券商買進
   - 每日券商賣出
   - 每日券商買賣超
   - 1D
   - 5D
   - 10D
   - 20D
8. 所有結果直接輸出到 GitHub Actions Log
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


VERSION = "V1.0"

REQUEST_TIMEOUT = 30

TEST_STOCKS = {
    "2337": {
        "name": "旺宏",
        "market": "TWSE",
    },
    "2426": {
        "name": "鼎元",
        "market": "TWSE",
    },
    "2368": {
        "name": "金像電",
        "market": "TWSE",
    },
    "3081": {
        "name": "聯亞",
        "market": "TPEX",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.twse.com.tw/",
}


# ============================================================
# 基本工具
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 78)
    log(title)
    log("=" * 78)


def clean_number(value: Any) -> Optional[float]:
    """
    嘗試把 API 欄位轉成數字。
    不自行猜測單位。
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if text in {"", "--", "---", "－", "-", "N/A", "null", "None"}:
        return None

    text = (
        text.replace(",", "")
        .replace("，", "")
        .replace("%", "")
        .strip()
    )

    try:
        return float(text)
    except ValueError:
        return None


def roc_date(date_obj: datetime) -> str:
    """
    YYYYMMDD
    """
    return date_obj.strftime("%Y%m%d")


def iso_date(date_obj: datetime) -> str:
    return date_obj.strftime("%Y-%m-%d")


def previous_weekdays(days: int) -> List[datetime]:
    """
    產生最近工作日。
    注意：
    這裡只是候選日期。
    最終是否為實際交易日，以 API 是否有資料為準。
    """
    result: List[datetime] = []

    current = datetime.now()

    while len(result) < days:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)

    return result


def normalize_symbol(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if "." in text:
        text = text.split(".", 1)[0]

    return text


# ============================================================
# HTTP
# ============================================================

def get_json(
    session: requests.Session,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    referer: Optional[str] = None,
) -> Tuple[Optional[Any], int, str]:

    headers = dict(HEADERS)

    if referer:
        headers["Referer"] = referer

    try:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        status_code = response.status_code

        if status_code != 200:
            return None, status_code, response.text[:500]

        try:
            return response.json(), status_code, ""
        except Exception:
            return None, status_code, response.text[:500]

    except Exception as exc:
        return None, -1, str(exc)


# ============================================================
# 結構分析
# ============================================================

def recursive_rows(obj: Any) -> Iterable[Any]:
    """
    找出 JSON 中可能代表資料列的 list。
    """
    if isinstance(obj, list):
        for item in obj:
            yield item

    elif isinstance(obj, dict):
        for value in obj.values():
            yield from recursive_rows(value)


def extract_rows(payload: Any) -> List[Any]:
    """
    優先尋找常見的 data / records / rows / result 結構。
    """
    if payload is None:
        return []

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("data", "records", "rows", "result", "results"):
        value = payload.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            for subkey in ("data", "records", "rows", "result", "results"):
                subvalue = value.get(subkey)
                if isinstance(subvalue, list):
                    return subvalue

    # 最後才遞迴尋找 list
    for rows in recursive_rows(payload):
        if rows:
            return list(rows)

    return []


def print_payload_structure(
    label: str,
    payload: Any,
) -> None:

    log(f"[{label}]")

    if payload is None:
        log("  payload = None")
        return

    if isinstance(payload, dict):
        log(f"  type = dict")
        log(f"  keys = {list(payload.keys())[:30]}")

        for key in (
            "stat",
            "date",
            "data",
            "title",
            "fields",
            "result",
            "message",
        ):
            if key in payload:
                value = payload[key]

                if isinstance(value, list):
                    log(f"  {key}: list[{len(value)}]")
                elif isinstance(value, dict):
                    log(f"  {key}: dict")
                else:
                    log(f"  {key}: {value}")

    elif isinstance(payload, list):
        log(f"  type = list")
        log(f"  length = {len(payload)}")

        if payload:
            first = payload[0]

            if isinstance(first, dict):
                log(f"  first row keys = {list(first.keys())[:30]}")
            elif isinstance(first, list):
                log(f"  first row columns = {len(first)}")
                log(f"  first row = {first[:20]}")
            else:
                log(f"  first value = {first}")

    else:
        log(f"  type = {type(payload).__name__}")


# ============================================================
# TWSE：三大法人
# ============================================================

def test_twse_t86(
    session: requests.Session,
    stock_code: str,
    date_obj: datetime,
) -> Dict[str, Any]:

    date_str = roc_date(date_obj)

    url = (
        "https://www.twse.com.tw/rwd/zh/fund/T86"
    )

    payload, status, error = get_json(
        session,
        url,
        params={
            "date": date_str,
            "selectType": "ALL",
        },
    )

    result = {
        "success": False,
        "status": status,
        "stock_code": stock_code,
        "date": iso_date(date_obj),
        "payload": payload,
    }

    if payload is None:
        result["error"] = error
        return result

    rows = payload.get("data", []) if isinstance(payload, dict) else []

    for row in rows:
        if not isinstance(row, list) or not row:
            continue

        code = normalize_symbol(row[0])

        if code == stock_code:
            result["success"] = True
            result["row"] = row
            return result

    result["error"] = "找不到指定股票"

    return result


# ============================================================
# TWSE：官方券商相關端點探測
# ============================================================

def test_twse_broker_endpoints(
    session: requests.Session,
    stock_code: str,
    date_obj: datetime,
) -> List[Dict[str, Any]]:

    date_str = roc_date(date_obj)

    candidates = [
        {
            "name": "TWSE_T86",
            "url": "https://www.twse.com.tw/rwd/zh/fund/T86",
            "params": {
                "date": date_str,
                "selectType": "ALL",
            },
            "classification": "institutional",
        },
        {
            "name": "TWSE_BROKER_TRADE",
            "url": "https://www.twse.com.tw/rwd/zh/afterTrading/brokerTrade",
            "params": {
                "date": date_str,
                "selectType": "ALL",
            },
            "classification": "broker_candidate",
        },
        {
            "name": "TWSE_BROKER_TRADE_2",
            "url": "https://www.twse.com.tw/rwd/zh/afterTrading/brokerTrade",
            "params": {
                "date": date_str,
                "selectType": "ALLBUT0999",
            },
            "classification": "broker_candidate",
        },
    ]

    results = []

    for candidate in candidates:

        payload, status, error = get_json(
            session,
            candidate["url"],
            params=candidate["params"],
        )

        rows = extract_rows(payload)

        found_symbol = False

        for row in rows:

            if isinstance(row, dict):

                values = [
                    normalize_symbol(v)
                    for v in row.values()
                ]

                if stock_code in values:
                    found_symbol = True

            elif isinstance(row, list):

                values = [
                    normalize_symbol(v)
                    for v in row
                ]

                if stock_code in values:
                    found_symbol = True

        results.append({
            "name": candidate["name"],
            "status": status,
            "classification": candidate["classification"],
            "row_count": len(rows),
            "found_symbol": found_symbol,
            "payload": payload,
            "error": error,
        })

        time.sleep(0.3)

    return results


# ============================================================
# TPEx：公開 OpenAPI
# ============================================================

def test_tpex_active_broker_volume(
    session: requests.Session,
    stock_code: str,
) -> Dict[str, Any]:

    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/tpex_active_broker_volume"
    )

    payload, status, error = get_json(
        session,
        url,
        params={
            "stk_code": stock_code,
        },
        referer="https://www.tpex.org.tw/",
    )

    rows = extract_rows(payload)

    found_symbol = False

    for row in rows:

        if isinstance(row, dict):
            values = [
                normalize_symbol(v)
                for v in row.values()
            ]

            if stock_code in values:
                found_symbol = True

        elif isinstance(row, list):
            values = [
                normalize_symbol(v)
                for v in row
            ]

            if stock_code in values:
                found_symbol = True

    return {
        "name": "TPEx_tpex_active_broker_volume",
        "status": status,
        "row_count": len(rows),
        "found_symbol": found_symbol,
        "payload": payload,
        "error": error,
    }


# ============================================================
# 單位與方向分析
# ============================================================

def analyze_row(
    row: Any,
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:

    result = {
        "type": type(row).__name__,
        "numeric_values": [],
        "possible_buy": [],
        "possible_sell": [],
        "possible_net": [],
        "possible_volume": [],
    }

    if isinstance(row, dict):

        for key, value in row.items():

            number = clean_number(value)

            if number is None:
                continue

            key_lower = str(key).lower()

            result["numeric_values"].append({
                "field": key,
                "value": number,
            })

            if any(
                token in key_lower
                for token in (
                    "buy",
                    "買",
                    "買進",
                )
            ):
                result["possible_buy"].append({
                    "field": key,
                    "value": number,
                })

            if any(
                token in key_lower
                for token in (
                    "sell",
                    "賣",
                    "賣出",
                )
            ):
                result["possible_sell"].append({
                    "field": key,
                    "value": number,
                })

            if any(
                token in key_lower
                for token in (
                    "net",
                    "超",
                    "買賣超",
                )
            ):
                result["possible_net"].append({
                    "field": key,
                    "value": number,
                })

            if any(
                token in key_lower
                for token in (
                    "volume",
                    "qty",
                    "quantity",
                    "成交量",
                    "股數",
                    "張數",
                )
            ):
                result["possible_volume"].append({
                    "field": key,
                    "value": number,
                })

    elif isinstance(row, list):

        for index, value in enumerate(row):

            number = clean_number(value)

            if number is None:
                continue

            result["numeric_values"].append({
                "index": index,
                "value": number,
            })

    return result


def infer_unit(value: Optional[float]) -> str:
    """
    故意不根據數字大小猜單位。

    只有 API 欄位名稱或官方 schema 明確表示單位，
    才能在最終正式程式中轉換。
    """
    return "UNKNOWN"


# ============================================================
# 累計計算
# ============================================================

def calculate_periods(
    daily_values: List[Optional[float]],
) -> Dict[str, Optional[float]]:

    clean_values = [
        value
        for value in daily_values
        if value is not None
    ]

    if not clean_values:
        return {
            "1D": None,
            "5D": None,
            "10D": None,
            "20D": None,
        }

    return {
        "1D": clean_values[0]
        if len(clean_values) >= 1
        else None,

        "5D": round(sum(clean_values[:5]), 2)
        if len(clean_values) >= 5
        else None,

        "10D": round(sum(clean_values[:10]), 2)
        if len(clean_values) >= 10
        else None,

        "20D": round(sum(clean_values[:20]), 2)
        if len(clean_values) >= 20
        else None,
    }


# ============================================================
# 主測試
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"全券商分點主力買賣超資料源測試 {VERSION}"
    )

    log("本測試不寫入 Data/chip.json")
    log("本測試不修改任何正式資料")
    log("本測試禁止使用估算倍率")
    log("")

    session = requests.Session()

    # --------------------------------------------------------
    # 1. 測試日期
    # --------------------------------------------------------

    dates = previous_weekdays(20)

    log("候選測試日期：")

    for index, date_obj in enumerate(dates, start=1):
        log(
            f"  {index:02d}. "
            f"{iso_date(date_obj)}"
        )

    # --------------------------------------------------------
    # 2. 股票基本資料
    # --------------------------------------------------------

    section("固定測試股票")

    for code, info in TEST_STOCKS.items():
        log(
            f"{code} {info['name']} "
            f"| {info['market']}"
        )

    # --------------------------------------------------------
    # 3. TWSE / TPEx 公開資料源探測
    # --------------------------------------------------------

    for stock_code, info in TEST_STOCKS.items():

        section(
            f"{stock_code} {info['name']} "
            f"| {info['market']}"
        )

        # ----------------------------------------------------
        # 只測最新候選交易日
        # ----------------------------------------------------

        latest_date = dates[0]

        log(
            f"測試日期："
            f"{iso_date(latest_date)}"
        )

        # ====================================================
        # TWSE T86
        # ====================================================

        if info["market"] == "TWSE":

            section(
                f"{stock_code} "
                "TWSE T86 三大法人資料"
            )

            t86_result = test_twse_t86(
                session,
                stock_code,
                latest_date,
            )

            log(
                f"HTTP Status: "
                f"{t86_result['status']}"
            )

            if t86_result["success"]:

                row = t86_result["row"]

                log("✓ 找到股票資料")

                log(
                    f"原始 row 欄位數："
                    f"{len(row)}"
                )

                log("原始 row：")

                log(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                    )
                )

                log("")
                log(
                    "⚠️ 這是三大法人資料，"
                    "不是全券商分點資料。"
                )

            else:

                log(
                    "❌ 找不到指定股票"
                )

                if t86_result.get("error"):
                    log(
                        f"錯誤："
                        f"{t86_result['error']}"
                    )

        # ====================================================
        # 官方券商候選端點
        # ====================================================

        section(
            f"{stock_code} "
            "官方券商資料端點探測"
        )

        broker_results = test_twse_broker_endpoints(
            session,
            stock_code,
            latest_date,
        )

        for result in broker_results:

            log("")
            log(
                f"資料源："
                f"{result['name']}"
            )

            log(
                f"HTTP Status："
                f"{result['status']}"
            )

            log(
                f"分類："
                f"{result['classification']}"
            )

            log(
                f"資料列數："
                f"{result['row_count']}"
            )

            log(
                f"找到 {stock_code}："
                f"{result['found_symbol']}"
            )

            if result["payload"] is not None:

                print_payload_structure(
                    result["name"],
                    result["payload"],
                )

            if result["error"]:
                log(
                    f"錯誤："
                    f"{result['error']}"
                )

        # ====================================================
        # TPEx
        # ====================================================

        if info["market"] == "TPEX":

            section(
                f"{stock_code} "
                "TPEx 券商資料端點"
            )

            tpex_result = test_tpex_active_broker_volume(
                session,
                stock_code,
            )

            log(
                f"HTTP Status："
                f"{tpex_result['status']}"
            )

            log(
                f"資料列數："
                f"{tpex_result['row_count']}"
            )

            log(
                f"找到 {stock_code}："
                f"{tpex_result['found_symbol']}"
            )

            if tpex_result["payload"] is not None:

                print_payload_structure(
                    "TPEx active broker volume",
                    tpex_result["payload"],
                )

            if tpex_result["error"]:
                log(
                    f"錯誤："
                    f"{tpex_result['error']}"
                )

    # ========================================================
    # 4. 重要結論
    # ========================================================

    section("資料源判定規則")

    log("真正可以進入正式 fetch_chip.py 的資料，必須同時符合：")
    log("")
    log("1. 能取得指定股票")
    log("2. 能辨識券商 / 分點")
    log("3. 能辨識買進數量")
    log("4. 能辨識賣出數量")
    log("5. 能計算買賣超")
    log("6. 官方資料能確認單位")
    log("7. 官方資料能確認正負方向")
    log("8. 20 個交易日可以穩定取得")
    log("9. TWSE / TPEX 都有可對應來源")
    log("10. 不是三大法人資料")
    log("11. 不是熱門券商排行的有限樣本")
    log("12. 不是估算值")
    log("")

    log(
        "⚠️ 注意："
        "如果官方公開 API 只有券商排行，"
        "不能把它宣稱成「全券商主力買賣超」。"
    )

    log(
        "⚠️ 注意："
        "如果只有付費買賣日報資料，"
        "正式系統不能假裝有免費完整資料。"
    )

    # ========================================================
    # 5. 單位禁止猜測
    # ========================================================

    section("單位判定")

    log(
        "本測試程式不會依數字大小猜測「股」或「張」。"
    )

    log(
        "只有在原始資料欄位或官方文件明確定義單位後，"
        "才允許寫入正式 chip.json。"
    )

    log(
        "目前單位判定：UNKNOWN，"
        "直到原始資料來源明確確認。"
    )

    # ========================================================
    # 6. 1D / 5D / 10D / 20D 計算規則展示
    # ========================================================

    section("1D / 5D / 10D / 20D 累計規則")

    demo_values = [
        10.0,
        -5.0,
        20.0,
        -3.0,
        8.0,
        12.0,
        -4.0,
        7.0,
        3.0,
        -2.0,
        6.0,
        9.0,
        -8.0,
        5.0,
        2.0,
        -1.0,
        4.0,
        3.0,
        -6.0,
        7.0,
    ]

    periods = calculate_periods(demo_values)

    log(
        f"1D  = {periods['1D']}"
    )

    log(
        f"5D  = {periods['5D']}"
    )

    log(
        f"10D = {periods['10D']}"
    )

    log(
        f"20D = {periods['20D']}"
    )

    log("")
    log(
        "以上只是驗證累計演算法，"
        "不是任何股票的實際主力數據。"
    )

    # ========================================================
    # 7. 結束
    # ========================================================

    elapsed = time.time() - start_time

    section("測試結束")

    log(
        f"測試耗時：{elapsed:.1f} 秒"
    )

    log("")
    log(
        "本次測試沒有修改："
    )

    log(
        "  Data/chip.json"
    )

    log(
        "  Data/universe.json"
    )

    log(
        "  index.html"
    )

    log("")
    log(
        "下一步必須根據 Action Log 的實際 API 回傳內容，"
        "決定是否存在可正式使用的全券商分點資料源。"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
