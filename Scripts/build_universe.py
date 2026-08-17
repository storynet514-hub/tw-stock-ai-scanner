#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
台股全市場 Universe Builder
Version: 1.0.0

用途：
    建立台灣股票 + ETF 的完整 Universe。

資料來源：
    1. TWSE 上市證券
    2. TPEx 上櫃證券

重要設計：
    - 不使用舊的 11 檔固定清單
    - 不從 prices.json 反推 Universe
    - 股票納入
    - ETF 納入
    - 債券 ETF 納入
    - 權證排除
    - 個別債券排除
    - ETN 排除
    - Universe 數量異常時直接 FAIL
============================================================
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests


# ============================================================
# 基本設定
# ============================================================

BASE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        ".."
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "Data"
)

OUTPUT_FILE = os.path.join(
    DATA_DIR,
    "stocks.json"
)

TIMEOUT = 30

# 全市場最低安全門檻
#
# 正常台股股票 + ETF Universe 應遠高於這個數字。
# 如果 API 異常只回傳少量資料，直接讓 Action FAIL，
# 絕對不能退回 11 檔。
MIN_UNIVERSE_COUNT = 1000

# 單一市場最低門檻
MIN_TWSE_COUNT = 500
MIN_TPEX_COUNT = 300


# ============================================================
# API
# ============================================================

TWSE_URLS = [
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "https://openapi.twse.com.tw/v1/opendata/t187ap03_L_1",
]

TPEX_URLS = [
    "https://www.tpex.org.tw/openapi/v1.0/"
    "tpex_mainboard_peratio"
]


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "(compatible; TWStockAIUniverse/1.0)",
        "Accept":
            "application/json,text/plain,*/*",
    }
)


# ============================================================
# 工具
# ============================================================

def now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def clean_text(value: Any) -> str:

    if value is None:
        return ""

    return str(value).strip()


def normalize_code(value: Any) -> str:

    code = clean_text(value)

    if not code:
        return ""

    code = code.upper()

    # 移除 Yahoo suffix
    code = re.sub(
        r"\.(TW|TWO)$",
        "",
        code
    )

    # 移除空白
    code = code.replace(
        " ",
        ""
    )

    return code


def is_valid_code(code: str) -> bool:

    if not code:
        return False

    # 一般股票 / ETF 代號
    #
    # 例如：
    # 2330
    # 1303
    # 0050
    # 00720B
    # 00980A
    #
    # 不接受超長、奇怪格式。
    if not re.fullmatch(
        r"[0-9]{4,6}[A-Z]?",
        code
    ):
        return False

    return True


def yahoo_symbol(
    code: str,
    market: str
) -> str:

    if market == "TWSE":
        return f"{code}.TW"

    if market == "TPEX":
        return f"{code}.TWO"

    return ""


# ============================================================
# 證券分類
# ============================================================

def detect_etf_category(
    code: str,
    name: str
) -> Optional[str]:

    """
    ETF 分類。

    注意：
        「債券 ETF」仍然是 ETF，
        所以不能因為名稱出現「債」就排除。

    回傳：
        equity
        bond
        balanced
        leveraged
        inverse
        futures
        commodity
        currency
        reit
        multi_asset
        active
        unknown
    """

    text = (
        f"{code} {name}"
    ).upper()

    # --------------------------------------------------------
    # 債券 ETF
    # --------------------------------------------------------

    bond_keywords = [
        "債",
        "債券",
        "公司債",
        "公債",
        "國債",
        "美債",
        "投資級債",
        "投等債",
        "高收益債",
        "金融債",
        "新興市場債",
        "非投資等級債",
    ]

    if any(
        keyword.upper() in text
        for keyword in bond_keywords
    ):
        return "bond"

    # --------------------------------------------------------
    # 槓桿
    # --------------------------------------------------------

    leveraged_keywords = [
        "正2",
        "正向2倍",
        "槓桿",
        "2X",
        "2倍",
    ]

    if any(
        keyword.upper() in text
        for keyword in leveraged_keywords
    ):
        return "leveraged"

    # --------------------------------------------------------
    # 反向
    # --------------------------------------------------------

    inverse_keywords = [
        "反1",
        "反向",
        "INVERSE",
        "SHORT",
    ]

    if any(
        keyword.upper() in text
        for keyword in inverse_keywords
    ):
        return "inverse"

    # --------------------------------------------------------
    # REIT
    # --------------------------------------------------------

    if (
        "REIT" in text
        or "不動產投資信託" in text
    ):
        return "reit"

    # --------------------------------------------------------
    # 貨幣
    # --------------------------------------------------------

    currency_keywords = [
        "美元",
        "日圓",
        "英鎊",
        "歐元",
        "貨幣",
        "外匯",
    ]

    if any(
        keyword.upper() in text
        for keyword in currency_keywords
    ):
        return "currency"

    # --------------------------------------------------------
    # 商品
    # --------------------------------------------------------

    commodity_keywords = [
        "黃金",
        "原油",
        "石油",
        "商品",
        "白銀",
        "天然氣",
    ]

    if any(
        keyword.upper() in text
        for keyword in commodity_keywords
    ):
        return "commodity"

    # --------------------------------------------------------
    # 期貨
    # --------------------------------------------------------

    if "期貨" in text:
        return "futures"

    # --------------------------------------------------------
    # 多資產 / 平衡
    # --------------------------------------------------------

    balanced_keywords = [
        "平衡",
        "多資產",
        "多重資產",
    ]

    if any(
        keyword.upper() in text
        for keyword in balanced_keywords
    ):
        return "balanced"

    # --------------------------------------------------------
    # 主動式
    # --------------------------------------------------------

    if (
        "主動" in text
        or "ACTIVE" in text
    ):
        return "active"

    # --------------------------------------------------------
    # 預設 ETF
    # --------------------------------------------------------

    return "equity"


# ============================================================
# 證券類型判斷
# ============================================================

def classify_security(
    code: str,
    name: str,
    raw: Dict[str, Any]
) -> Optional[str]:

    """
    回傳：

        stock
        etf
        None = 排除

    不直接靠名稱判斷所有東西，
    優先讀取官方資料中的類別欄位。
    """

    text = (
        f"{code} {name}"
    ).upper()

    raw_text = " ".join(
        clean_text(value)
        for value in raw.values()
    ).upper()

    combined = (
        text + " " + raw_text
    )

    # ========================================================
    # 1. 明確排除：權證
    # ========================================================

    warrant_keywords = [
        "權證",
        "認購權證",
        "認售權證",
        "WARRANT",
    ]

    if any(
        keyword.upper() in combined
        for keyword in warrant_keywords
    ):
        return None

    # ========================================================
    # 2. 明確排除：ETN
    # ========================================================

    etn_keywords = [
        "ETN",
        "指數投資證券",
        "槓桿型指數投資證券",
    ]

    if any(
        keyword.upper() in combined
        for keyword in etn_keywords
    ):
        return None

    # ========================================================
    # 3. 明確排除：個別債券
    #
    # 注意：
    # ETF 名稱即使包含「債」，不能在這裡排除。
    # ========================================================

    direct_bond_keywords = [
        "公司債券",
        "政府債券",
        "中央政府公債",
        "地方政府公債",
        "金融債券",
        "普通公司債",
    ]

    # 如果官方類別明確表示 ETF，保留
    official_etf = any(
        keyword in raw_text
        for keyword in [
            "ETF",
            "指數股票型",
            "交易所交易基金",
        ]
    )

    if (
        not official_etf
        and any(
            keyword.upper() in combined
            for keyword in direct_bond_keywords
        )
    ):
        return None

    # ========================================================
    # 4. ETF 判斷
    # ========================================================

    if official_etf:
        return "etf"

    # ETF 常見代號：
    #
    # 00xxxx
    #
    # 但不能只靠這個判斷。
    # 仍作為補充條件。
    if re.fullmatch(
        r"00[0-9]{2,4}[A-Z]?",
        code
    ):
        return "etf"

    # 名稱明確包含 ETF
    if "ETF" in text:
        return "etf"

    if "指數股票型" in combined:
        return "etf"

    if "交易所交易基金" in combined:
        return "etf"

    # ========================================================
    # 5. 一般股票
    # ========================================================

    # 四碼純數字通常是上市 / 上櫃股票。
    if re.fullmatch(
        r"[0-9]{4}",
        code
    ):
        return "stock"

    return None


# ============================================================
# API Request
# ============================================================

def request_json(
    url: str
) -> Any:

    print(
        f"🌐 GET {url}"
    )

    response = SESSION.get(
        url,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# TWSE
# ============================================================

def fetch_twse() -> List[Dict[str, Any]]:

    last_error = None

    for url in TWSE_URLS:

        try:

            data = request_json(
                url
            )

            if isinstance(
                data,
                list
            ) and data:

                print(
                    f"✅ TWSE API："
                    f"{len(data)} 筆"
                )

                return data

        except Exception as exc:

            last_error = exc

            print(
                f"⚠️ TWSE API 失敗：{exc}"
            )

    raise RuntimeError(
        "TWSE 全市場資料取得失敗："
        f"{last_error}"
    )


# ============================================================
# TPEx
# ============================================================

def fetch_tpex() -> List[Dict[str, Any]]:

    """
    TPEx 官方 API 端點可能依資料集調整，
    因此這裡要求回傳的是完整證券資料。

    若端點只回傳部分行情，
    不允許拿來當完整 Universe。
    """

    try:

        data = request_json(
            TPEX_URLS[0]
        )

        if not isinstance(
            data,
            list
        ):
            raise RuntimeError(
                "TPEx API 回傳格式不是 list"
            )

        if len(data) < MIN_TPEX_COUNT:

            raise RuntimeError(
                "TPEx API 回傳數量異常："
                f"{len(data)}"
            )

        print(
            f"✅ TPEx API："
            f"{len(data)} 筆"
        )

        return data

    except Exception as exc:

        raise RuntimeError(
            f"TPEx 全市場資料取得失敗：{exc}"
        )


# ============================================================
# 欄位抽取
# ============================================================

def find_value(
    raw: Dict[str, Any],
    candidates: List[str]
) -> str:

    normalized = {}

    for key, value in raw.items():

        key_norm = (
            clean_text(key)
            .replace(
                " ",
                ""
            )
            .replace(
                "_",
                ""
            )
            .lower()
        )

        normalized[
            key_norm
        ] = clean_text(value)

    for candidate in candidates:

        candidate_norm = (
            candidate
            .replace(
                " ",
                ""
            )
            .replace(
                "_",
                ""
            )
            .lower()
        )

        if candidate_norm in normalized:

            value = normalized[
                candidate_norm
            ]

            if value:
                return value

    return ""


def extract_code_from_raw(
    raw: Dict[str, Any]
) -> str:

    candidates = [
        "股票代號",
        "證券代號",
        "代號",
        "stock_code",
        "code",
        "symbol",
        "SecuritiesCompanyCode",
        "SecuritiesCompanyCode",
    ]

    value = find_value(
        raw,
        candidates
    )

    return normalize_code(
        value
    )


def extract_name_from_raw(
    raw: Dict[str, Any],
    code: str
) -> str:

    candidates = [
        "股票名稱",
        "證券名稱",
        "名稱",
        "公司名稱",
        "stock_name",
        "name",
        "title",
    ]

    value = find_value(
        raw,
        candidates
    )

    return value or code


# ============================================================
# 建立市場資料
# ============================================================

def build_item(
    raw: Dict[str, Any],
    market: str
) -> Optional[Dict[str, Any]]:

    code = extract_code_from_raw(
        raw
    )

    if not is_valid_code(
        code
    ):
        return None

    name = extract_name_from_raw(
        raw,
        code
    )

    asset_type = classify_security(
        code,
        name,
        raw
    )

    if asset_type is None:
        return None

    symbol = yahoo_symbol(
        code,
        market
    )

    if not symbol:
        return None

    result = {
        "code": code,
        "symbol": symbol,
        "name": name,
        "market": market,
        "type": asset_type,
    }

    if asset_type == "etf":

        result[
            "etf_category"
        ] = detect_etf_category(
            code,
            name
        )

    return result


# ============================================================
# Universe 建立
# ============================================================

def build_universe():

    print("=" * 60)
    print(
        "台股全市場 Universe Builder V1.0.0"
    )
    print("=" * 60)

    print(
        "📌 股票：納入"
    )

    print(
        "📌 ETF：納入"
    )

    print(
        "📌 債券 ETF：納入"
    )

    print(
        "📌 權證：排除"
    )

    print(
        "📌 ETN：排除"
    )

    print(
        "📌 個別債券：排除"
    )

    print()

    twse_raw = fetch_twse()

    tpex_raw = fetch_tpex()

    items = []

    # --------------------------------------------------------
    # TWSE
    # --------------------------------------------------------

    twse_items = []

    for raw in twse_raw:

        if not isinstance(
            raw,
            dict
        ):
            continue

        item = build_item(
            raw,
            "TWSE"
        )

        if item:

            twse_items.append(
                item
            )

    # --------------------------------------------------------
    # TPEx
    # --------------------------------------------------------

    tpex_items = []

    for raw in tpex_raw:

        if not isinstance(
            raw,
            dict
        ):
            continue

        item = build_item(
            raw,
            "TPEX"
        )

        if item:

            tpex_items.append(
                item
            )

    items.extend(
        twse_items
    )

    items.extend(
        tpex_items
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in items:

        key = (
            item["code"],
            item["market"]
        )

        unique[key] = item

    items = list(
        unique.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    items.sort(
        key=lambda item: (
            item["market"],
            item["code"]
        )
    )

    # ========================================================
    # 驗證
    # ========================================================

    twse_count = sum(
        1
        for item in items
        if item["market"] == "TWSE"
    )

    tpex_count = sum(
        1
        for item in items
        if item["market"] == "TPEX"
    )

    stock_count = sum(
        1
        for item in items
        if item["type"] == "stock"
    )

    etf_count = sum(
        1
        for item in items
        if item["type"] == "etf"
    )

    bond_etf_count = sum(
        1
        for item in items
        if (
            item["type"] == "etf"
            and item.get(
                "etf_category"
            ) == "bond"
        )
    )

    total = len(
        items
    )

    print()
    print("=" * 60)
    print(
        f"TWSE：{twse_count}"
    )
    print(
        f"TPEx：{tpex_count}"
    )
    print(
        f"股票：{stock_count}"
    )
    print(
        f"ETF：{etf_count}"
    )
    print(
        f"其中債券 ETF：{bond_etf_count}"
    )
    print(
        f"Universe 總數：{total}"
    )
    print("=" * 60)

    # --------------------------------------------------------
    # 安全門檻
    # --------------------------------------------------------

    if twse_count < MIN_TWSE_COUNT:

        raise RuntimeError(
            "❌ TWSE Universe 數量異常："
            f"{twse_count} < {MIN_TWSE_COUNT}"
        )

    if tpex_count < MIN_TPEX_COUNT:

        raise RuntimeError(
            "❌ TPEx Universe 數量異常："
            f"{tpex_count} < {MIN_TPEX_COUNT}"
        )

    if total < MIN_UNIVERSE_COUNT:

        raise RuntimeError(
            "❌ 全市場 Universe 數量異常："
            f"{total} < {MIN_UNIVERSE_COUNT}"
        )

    # ========================================================
    # 建立輸出
    # ========================================================

    os.makedirs(
        DATA_DIR,
        exist_ok=True
    )

    output = {
        "version": "1.0.0",
        "generated_at": now_iso(),
        "market": "TW",
        "universe_type":
            "TWSE_TPEX_STOCK_ETF",
        "count": total,
        "counts": {
            "twse": twse_count,
            "tpex": tpex_count,
            "stocks": stock_count,
            "etfs": etf_count,
            "bond_etfs":
                bond_etf_count,
        },
        "rules": {
            "stocks": True,
            "etfs": True,
            "bond_etfs": True,
            "warrants": False,
            "etn": False,
            "individual_bonds": False,
        },
        "items": items,
    }

    # --------------------------------------------------------
    # 寫檔
    # --------------------------------------------------------

    temp_file = (
        OUTPUT_FILE
        + ".tmp"
    )

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_file,
        OUTPUT_FILE
    )

    print()
    print(
        f"✅ 已建立：{OUTPUT_FILE}"
    )

    print(
        f"✅ Universe：{total} 檔"
    )

    # ========================================================
    # 00720B 驗證
    # ========================================================

    target = next(
        (
            item
            for item in items
            if item["code"] == "00720B"
        ),
        None
    )

    if target:

        print()
        print(
            "✅ 00720B 已存在 Universe"
        )

        print(
            json.dumps(
                target,
                ensure_ascii=False,
                indent=2
            )
        )

    else:

        print()
        print(
            "ℹ️ 00720B 未出現在目前官方 Universe 回傳資料中"
        )

        print(
            "ℹ️ 這不會因此把債券 ETF 規則判定為排除"
        )

    print()
    print(
        "🎯 Universe 建立完成"
    )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    try:

        build_universe()

    except KeyboardInterrupt:

        print(
            "\n❌ 使用者中止"
        )

        sys.exit(130)

    except Exception as exc:

        print()
        print(
            "❌ Universe Builder FAILED"
        )

        print(
            f"原因：{exc}"
        )

        sys.exit(1)
