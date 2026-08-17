#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股 AI 短期選股系統
fetch_chip.py V1.0
============================================================

用途：
    建立短期選股所需的籌碼資料。

資料項目：
    1. 三大法人買賣超
    2. 外資買賣超
    3. 投信買賣超
    4. 自營商買賣超
    5. 融資餘額
    6. 融券餘額
    7. 當沖成交量
    8. 當沖率

重要：
    本程式不負責：
    - Universe
    - 歷史價格
    - MACD
    - KD
    - RSI
    - UI

輸入：
    Data/universe.json

輸出：
    Data/chip.json

設計原則：
    - API 失敗不得產生假的 0
    - 單一股票失敗不應讓整批資料消失
    - 明確標示資料來源與日期
    - 台股 TWSE / TPEx 分開處理
    - 不把「法人買賣超」錯稱為真正的「主力」
============================================================
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests


# ============================================================
# 基本設定
# ============================================================

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
OUTPUT_FILE = DATA_DIR / "chip.json"

TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}


# ============================================================
# 工具
# ============================================================

def now_tw():
    """
    回傳目前台灣時間。
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Taipei")
        ).strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_tw():
    return now_tw()[:10]


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def request_json(url, params=None):
    """
    安全 GET JSON。

    失敗直接回傳 None。
    不把失敗資料轉成 0。
    """

    try:
        response = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=TIMEOUT,
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        print(f"   ⚠ API 取得失敗：{exc}")
        return None


def to_number(value):
    """
    將 API 回傳值轉成數字。

    空值、--、None：
        回傳 None

    絕不把未知值當成 0。
    """

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return value

    text = str(value).strip()

    if text in ("", "-", "--", "N/A", "null", "None"):
        return None

    text = text.replace(",", "")

    try:
        return float(text)

    except Exception:
        return None


def normalize_stock_code(value):
    """
    統一股票代號。

    例如：
        2330
        2330.TW
        2330.TWO

    最後只保留純代號。
    """

    if value is None:
        return None

    code = str(value).strip()

    if "." in code:
        code = code.split(".")[0]

    return code


# ============================================================
# Universe
# ============================================================

def load_universe():
    """
    讀取 Data/universe.json。
    """

    if not UNIVERSE_FILE.exists():
        print(f"❌ 找不到 Universe：{UNIVERSE_FILE}")
        return []

    try:
        with open(
            UNIVERSE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

    except Exception as exc:
        print(f"❌ Universe JSON 無法讀取：{exc}")
        return []

    stocks = []

    # --------------------------------------------------------
    # 格式 A
    # {
    #   "stocks": [...]
    # }
    # --------------------------------------------------------

    if isinstance(data, dict):

        if isinstance(data.get("stocks"), list):
            stocks = data["stocks"]

        elif isinstance(data.get("universe"), list):
            stocks = data["universe"]

        elif isinstance(data.get("data"), list):
            stocks = data["data"]

    # --------------------------------------------------------
    # 格式 B
    # [...]
    # --------------------------------------------------------

    elif isinstance(data, list):
        stocks = data

    result = []

    for item in stocks:

        if isinstance(item, str):

            code = normalize_stock_code(item)

            if code:
                result.append(
                    {
                        "code": code,
                        "name": "",
                        "market": "TW",
                    }
                )

            continue

        if not isinstance(item, dict):
            continue

        code = (
            item.get("code")
            or item.get("symbol")
            or item.get("stock_id")
            or item.get("ticker")
        )

        code = normalize_stock_code(code)

        if not code:
            continue

        name = (
            item.get("name")
            or item.get("stock_name")
            or ""
        )

        market = (
            item.get("market")
            or item.get("exchange")
            or "TW"
        )

        result.append(
            {
                "code": code,
                "name": name,
                "market": market,
            }
        )

    # 去除重複
    unique = {}

    for item in result:
        unique[item["code"]] = item

    result = list(unique.values())

    print(f"   Universe 股票數：{len(result)}")

    return result


# ============================================================
# TWSE 三大法人
# ============================================================

def fetch_twse_institutional(date_text):
    """
    TWSE 三大法人買賣超。

    回傳：

    {
        code: {
            foreign_net,
            investment_trust_net,
            dealer_net,
            institutional_net
        }
    }

    單位：
        張
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/fund/BFI82U"
    )

    params = {
        "dayDate": date_text.replace("-", ""),
        "type": "day",
    }

    print(
        f"🔎 TWSE 三大法人：{date_text}"
    )

    data = request_json(
        url,
        params=params,
    )

    if not data:
        return {}

    fields = data.get("fields", [])
    rows = data.get("data", [])

    if not rows:
        print("   ⚠ TWSE 沒有法人資料")
        return {}

    result = {}

    # 嘗試找欄位位置
    field_map = {}

    for idx, field in enumerate(fields):
        field_map[str(field)] = idx

    for row in rows:

        if not row:
            continue

        code = normalize_stock_code(row[0])

        if not code:
            continue

        def get_value(index):
            if index is None:
                return None

            if index >= len(row):
                return None

            return to_number(row[index])

        # ----------------------------------------------------
        # TWSE BFI82U 欄位會隨版本有所差異
        # 因此使用關鍵字尋找
        # ----------------------------------------------------

        foreign_idx = None
        trust_idx = None
        dealer_idx = None

        for name, idx in field_map.items():

            if (
                "外陸資" in name
                or "外資" in name
            ):
                foreign_idx = idx

            if "投信" in name:
                trust_idx = idx

            if "自營商" in name:
                dealer_idx = idx

        foreign_net = get_value(
            foreign_idx
        )

        trust_net = get_value(
            trust_idx
        )

        dealer_net = get_value(
            dealer_idx
        )

        institutional_net = None

        values = [
            v
            for v in (
                foreign_net,
                trust_net,
                dealer_net,
            )
            if v is not None
        ]

        if values:
            institutional_net = sum(values)

        result[code] = {
            "foreign_net": foreign_net,
            "investment_trust_net": trust_net,
            "dealer_net": dealer_net,
            "institutional_net": institutional_net,
        }

    print(
        f"   TWSE 法人資料：{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE 融資融券
# ============================================================

def fetch_twse_margin(date_text):
    """
    TWSE 融資融券資料。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/marginTrading/MI_MARGN"
    )

    params = {
        "date": date_text.replace("-", ""),
        "selectType": "ALL",
    }

    print(
        f"🔎 TWSE 融資融券：{date_text}"
    )

    data = request_json(
        url,
        params=params,
    )

    if not data:
        return {}

    tables = data.get("tables", [])

    result = {}

    for table in tables:

        fields = table.get("fields", [])
        rows = table.get("data", [])

        if not rows:
            continue

        # 找股票代號欄
        for row in rows:

            if not row:
                continue

            code = normalize_stock_code(row[0])

            if not code or len(code) != 4:
                continue

            # ------------------------------------------------
            # 因官方欄位結構可能變動，
            # 這裡只在能確認欄位名稱時寫入。
            # ------------------------------------------------

            margin_balance = None
            short_balance = None

            margin_idx = None
            short_idx = None

            for idx, field in enumerate(fields):

                field_text = str(field)

                if (
                    "融資餘額" in field_text
                    or "融資餘額(張)" in field_text
                ):
                    margin_idx = idx

                if (
                    "融券餘額" in field_text
                    or "融券餘額(張)" in field_text
                ):
                    short_idx = idx

            if (
                margin_idx is not None
                and margin_idx < len(row)
            ):
                margin_balance = to_number(
                    row[margin_idx]
                )

            if (
                short_idx is not None
                and short_idx < len(row)
            ):
                short_balance = to_number(
                    row[short_idx]
                )

            result[code] = {
                "margin_balance": margin_balance,
                "short_balance": short_balance,
            }

    print(
        f"   TWSE 融資融券：{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx
# ============================================================

def fetch_tpex_institutional(date_text):
    """
    TPEx 法人資料。

    TPEx 官方 API 結構與 TWSE 不同。
    如果官方端點無法取得，不建立虛假的 0。
    """

    url = (
        "https://www.tpex.org.tw/"
        "web/stock/3insti/"
        "3insti_summary.php"
    )

    params = {
        "l": "zh-tw",
        "d": date_text.replace("-", ""),
        "s": "0,asc",
    }

    print(
        f"🔎 TPEx 三大法人：{date_text}"
    )

    data = request_json(
        url,
        params=params,
    )

    if not data:
        print("   ⚠ TPEx 法人資料無法取得")
        return {}

    result = {}

    tables = data.get("tables", [])

    for table in tables:

        rows = table.get("data", [])

        for row in rows:

            if not row:
                continue

            code = normalize_stock_code(row[0])

            if not code or len(code) != 4:
                continue

            # TPEx 不同版本欄位可能變動，
            # 先保留原始資料，不猜欄位。
            result[code] = {
                "raw": row,
            }

    print(
        f"   TPEx 法人資料：{len(result)} 檔"
    )

    return result


# ============================================================
# 當沖
# ============================================================

def fetch_twse_daytrade(date_text):
    """
    TWSE 當沖資料。

    不存在資料時回傳空 dict。
    不把空資料變成當沖率 0。
    """

    url = (
        "https://www.twse.com.tw/"
        "rwd/zh/afterTrading/"
        "TWTB4U"
    )

    params = {
        "date": date_text.replace("-", ""),
        "selectType": "ALL",
    }

    print(
        f"🔎 TWSE 當沖資料：{date_text}"
    )

    data = request_json(
        url,
        params=params,
    )

    if not data:
        return {}

    rows = data.get("data", [])
    fields = data.get("fields", [])

    if not rows:
        print("   ⚠ 無當沖資料")
        return {}

    result = {}

    field_map = {}

    for idx, field in enumerate(fields):
        field_map[str(field)] = idx

    volume_idx = None
    daytrade_idx = None

    for name, idx in field_map.items():

        if (
            "成交量" in name
            and "當沖" not in name
        ):
            volume_idx = idx

        if (
            "當沖" in name
            or "沖銷" in name
        ):
            daytrade_idx = idx

    for row in rows:

        if not row:
            continue

        code = normalize_stock_code(row[0])

        if not code:
            continue

        total_volume = None
        daytrade_volume = None

        if (
            volume_idx is not None
            and volume_idx < len(row)
        ):
            total_volume = to_number(
                row[volume_idx]
            )

        if (
            daytrade_idx is not None
            and daytrade_idx < len(row)
        ):
            daytrade_volume = to_number(
                row[daytrade_idx]
            )

        daytrade_rate = None

        if (
            total_volume is not None
            and daytrade_volume is not None
            and total_volume > 0
        ):
            daytrade_rate = (
                daytrade_volume
                / total_volume
                * 100
            )

        result[code] = {
            "total_volume": total_volume,
            "daytrade_volume": daytrade_volume,
            "daytrade_rate": daytrade_rate,
        }

    print(
        f"   TWSE 當沖資料：{len(result)} 檔"
    )

    return result


# ============================================================
# 建立單日籌碼資料
# ============================================================

def build_daily_chip(date_text, universe):
    """
    建立指定交易日的籌碼資料。
    """

    print("")
    print("=" * 60)
    print(f"建立籌碼資料：{date_text}")
    print("=" * 60)

    twse_inst = fetch_twse_institutional(
        date_text
    )

    twse_margin = fetch_twse_margin(
        date_text
    )

    tpex_inst = fetch_tpex_institutional(
        date_text
    )

    twse_daytrade = fetch_twse_daytrade(
        date_text
    )

    result = {}

    for stock in universe:

        code = stock["code"]

        market = str(
            stock.get("market", "")
        ).upper()

        item = {
            "code": code,
            "name": stock.get("name", ""),
            "market": market,
            "date": date_text,

            # -----------------------------
            # 法人
            # -----------------------------

            "foreign_net": None,
            "investment_trust_net": None,
            "dealer_net": None,
            "institutional_net": None,

            # -----------------------------
            # 資券
            # -----------------------------

            "margin_balance": None,
            "short_balance": None,

            # -----------------------------
            # 當沖
            # -----------------------------

            "total_volume": None,
            "daytrade_volume": None,
            "daytrade_rate": None,

            # -----------------------------
            # 資料狀態
            # -----------------------------

            "data_status": "partial",
        }

        # ====================================================
        # TWSE
        # ====================================================

        if code in twse_inst:

            inst = twse_inst[code]

            item["foreign_net"] = (
                inst.get("foreign_net")
            )

            item["investment_trust_net"] = (
                inst.get(
                    "investment_trust_net"
                )
            )

            item["dealer_net"] = (
                inst.get("dealer_net")
            )

            item["institutional_net"] = (
                inst.get(
                    "institutional_net"
                )
            )

        # ====================================================
        # TPEx
        # ====================================================

        if code in tpex_inst:

            item["tpex_institutional"] = (
                tpex_inst[code]
            )

        # ====================================================
        # 融資融券
        # ====================================================

        if code in twse_margin:

            margin = twse_margin[code]

            item["margin_balance"] = (
                margin.get(
                    "margin_balance"
                )
            )

            item["short_balance"] = (
                margin.get(
                    "short_balance"
                )
            )

        # ====================================================
        # 當沖
        # ====================================================

        if code in twse_daytrade:

            daytrade = twse_daytrade[code]

            item["total_volume"] = (
                daytrade.get(
                    "total_volume"
                )
            )

            item["daytrade_volume"] = (
                daytrade.get(
                    "daytrade_volume"
                )
            )

            item["daytrade_rate"] = (
                daytrade.get(
                    "daytrade_rate"
                )
            )

        # ====================================================
        # 判斷資料完整度
        # ====================================================

        available = 0

        fields_to_check = [
            item["foreign_net"],
            item["investment_trust_net"],
            item["dealer_net"],
            item["institutional_net"],
            item["margin_balance"],
            item["short_balance"],
            item["daytrade_rate"],
        ]

        for value in fields_to_check:

            if value is not None:
                available += 1

        if available >= 6:
            item["data_status"] = "complete"

        elif available >= 3:
            item["data_status"] = "partial"

        else:
            item["data_status"] = "insufficient"

        result[code] = item

    return result


# ============================================================
# 儲存
# ============================================================

def save_output(daily_data, universe_count):
    """
    儲存 Data/chip.json
    """

    ensure_data_dir()

    complete = 0
    partial = 0
    insufficient = 0

    for item in daily_data.values():

        status = item.get(
            "data_status"
        )

        if status == "complete":
            complete += 1

        elif status == "partial":
            partial += 1

        else:
            insufficient += 1

    output = {
        "schema_version": "1.0",
        "generated_at": now_tw(),
        "data_date": today_tw(),

        "description": (
            "台股短期選股籌碼資料"
        ),

        "universe_count": universe_count,

        "statistics": {
            "complete": complete,
            "partial": partial,
            "insufficient": insufficient,
        },

        "stocks": daily_data,
    }

    temp_file = OUTPUT_FILE.with_suffix(
        ".json.tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temp_file,
        OUTPUT_FILE,
    )

    print("")
    print("=" * 60)
    print("籌碼資料建立完成")
    print("=" * 60)

    print(
        f"輸出檔案：{OUTPUT_FILE}"
    )

    print(
        f"Universe：{universe_count}"
    )

    print(
        f"完整資料：{complete}"
    )

    print(
        f"部分資料：{partial}"
    )

    print(
        f"不足資料：{insufficient}"
    )


# ============================================================
# Main
# ============================================================

def main():

    print("")
    print("=" * 64)
    print("台股 AI 短期選股系統 fetch_chip.py V1.0")
    print("=" * 64)

    print(
        f"開始時間：{now_tw()}"
    )

    ensure_data_dir()

    # --------------------------------------------------------
    # 1. Universe
    # --------------------------------------------------------

    universe = load_universe()

    if not universe:

        print("")
        print(
            "❌ Universe 為空，停止執行。"
        )

        sys.exit(1)

    # --------------------------------------------------------
    # 2. 建立最近交易日資料
    #
    # 往前尋找最多 7 天。
    # 因為週末及國定假日可能沒有資料。
    # --------------------------------------------------------

    target_date = datetime.now()

    daily_data = {}

    for _ in range(7):

        date_text = target_date.strftime(
            "%Y-%m-%d"
        )

        daily_data = build_daily_chip(
            date_text,
            universe,
        )

        # 至少有資料才接受
        usable = sum(
            1
            for item in daily_data.values()
            if item.get("data_status")
            != "insufficient"
        )

        if usable > 0:

            print("")
            print(
                f"✅ 找到可用交易日：{date_text}"
            )

            break

        target_date -= timedelta(days=1)

    if not daily_data:

        print("")
        print(
            "❌ 最近 7 天皆無法取得籌碼資料。"
        )

        sys.exit(1)

    # --------------------------------------------------------
    # 3. 儲存
    # --------------------------------------------------------

    save_output(
        daily_data,
        len(universe),
    )

    print("")
    print("=" * 64)
    print(
        f"完成時間：{now_tw()}"
    )
    print(
        "fetch_chip.py V1.0 完成"
    )
    print("=" * 64)


if __name__ == "__main__":
    main()