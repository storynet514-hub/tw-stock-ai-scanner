import json
import csv
import os
from collections import Counter


INPUT_FILE = "Data/prices.json"
OUTPUT_JSON = "Data/qualified_stocks.json"
OUTPUT_CSV = "Data/qualified_stocks.csv"


CONDITION_KEYS = [
    "macd_golden_cross",
    "rsi_above_50",
    "kd_golden_cross",
    "volume_expand",
    "above_ma20",
    "ma20_up",
]


def get_code(item):
    """
    取得股票代號。
    """
    code = item.get("code")

    if code is None:
        return ""

    return str(code).strip()


def is_etf(item):
    """
    判斷 ETF。

    優先使用資料本身提供的分類欄位。
    若沒有分類欄位，再使用 ETF 代碼清單/格式判斷。

    本驗證的目標是：
        只留下一般個股 STOCK
    """

    # ------------------------------------------------------------
    # 1. 優先檢查可能存在的類型欄位
    # ------------------------------------------------------------

    type_fields = [
        "security_type",
        "category",
        "asset_type",
        "type",
        "market_type",
        "instrument_type",
    ]

    for field in type_fields:

        value = item.get(field)

        if value is None:
            continue

        text = str(value).strip().upper()

        if text in {
            "ETF",
            "ETFS",
            "ETF FUND",
            "ETF基金",
            "指數股票型基金",
        }:
            return True

        if "ETF" in text:
            return True

    # ------------------------------------------------------------
    # 2. 檢查明確 ETF 欄位
    # ------------------------------------------------------------

    for field in [
        "is_etf",
        "etf",
    ]:

        value = item.get(field)

        if isinstance(value, bool):
            if value:
                return True

        if str(value).strip().lower() in {
            "true",
            "1",
            "yes",
        }:
            return True

    # ------------------------------------------------------------
    # 3. ETF 代碼
    #
    # 台股 ETF 主要集中於：
    # 0050、0056、006208、00713、00878、
    # 00900、00919、00929、00940、00960...
    #
    # 這裡採用「00 開頭」作為 ETF 候選，
    # 避免把一般股票誤刪。
    # ------------------------------------------------------------

    code = get_code(item)

    if code.startswith("00"):
        return True

    # ------------------------------------------------------------
    # 4. 其他常見 ETF / ETN 類代碼
    #
    # 006xx、007xx、008xx、009xx 不一定全部都是 ETF，
    # 因此不能一律排除。
    #
    # 目前只將明確 00 開頭視為 ETF。
    # ------------------------------------------------------------

    return False


def get_conditions(item):

    conditions = item.get("conditions")

    if not isinstance(conditions, dict):
        conditions = {}

    return {
        key: bool(
            conditions.get(key, False)
        )
        for key in CONDITION_KEYS
    }


def main():

    print("=" * 70)
    print("台股 6/6 選股完整驗證 V2")
    print("=" * 70)

    if not os.path.exists(INPUT_FILE):
        raise SystemExit(
            f"ERROR: 找不到 {INPUT_FILE}"
        )

    # ------------------------------------------------------------
    # 讀取 prices.json
    # ------------------------------------------------------------

    with open(
        INPUT_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    stocks = data.get("stocks", [])

    if not isinstance(stocks, list):
        raise SystemExit(
            "ERROR: stocks 不是陣列"
        )

    print(
        f"原始資料筆數：{len(stocks)}"
    )

    # ------------------------------------------------------------
    # 分離 ETF / STOCK
    # ------------------------------------------------------------

    etf_items = []
    stock_items = []

    for item in stocks:

        if not isinstance(item, dict):
            continue

        if is_etf(item):
            etf_items.append(item)
        else:
            stock_items.append(item)

    print(
        f"判定 ETF：{len(etf_items)}"
    )

    print(
        f"判定個股：{len(stock_items)}"
    )

    # ------------------------------------------------------------
    # 顯示 ETF 代碼，方便驗證
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("ETF 判定前 20 檔")
    print("=" * 70)

    for item in etf_items[:20]:

        print(
            get_code(item),
            item.get("name", "")
        )

    # ------------------------------------------------------------
    # 6/6 驗證
    # ------------------------------------------------------------

    qualified = []

    distribution = Counter()

    missing_conditions = Counter()

    for item in stock_items:

        conditions = get_conditions(item)

        passed = sum(
            1
            for value in conditions.values()
            if value
        )

        distribution[passed] += 1

        if passed == 6:

            qualified.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "price": item.get("price"),
                "rsi": item.get("rsi"),
                "kd_k": item.get("kd_k"),
                "kd_d": item.get("kd_d"),
                "macd": item.get("macd"),
                "macd_signal": item.get(
                    "macd_signal"
                ),
                "volume": item.get("volume"),
                "volume_ma5": item.get(
                    "volume_ma5"
                ),
                "ma20": item.get("ma20"),
                "ai_score": item.get("ai_score"),
                "signal": item.get("signal"),
                "conditions": conditions,
            })

        else:

            for key, value in conditions.items():

                if not value:
                    missing_conditions[key] += 1

    # ------------------------------------------------------------
    # 排序
    # ------------------------------------------------------------

    qualified.sort(
        key=lambda x: (
            -(x.get("ai_score") or 0),
            str(x.get("code") or "")
        )
    )

    # ------------------------------------------------------------
    # 驗證數量
    # ------------------------------------------------------------

    expected_total = data.get(
        "statistics",
        {}
    ).get("total")

    expected_stocks = data.get(
        "statistics",
        {}
    ).get("stocks")

    expected_etf = data.get(
        "statistics",
        {}
    ).get("etf")

    print()
    print("=" * 70)
    print("資料數量交叉驗證")
    print("=" * 70)

    print(
        f"JSON statistics.total : "
        f"{expected_total}"
    )

    print(
        f"JSON statistics.stocks: "
        f"{expected_stocks}"
    )

    print(
        f"JSON statistics.etf   : "
        f"{expected_etf}"
    )

    print(
        f"實際判定 STOCK        : "
        f"{len(stock_items)}"
    )

    print(
        f"實際判定 ETF          : "
        f"{len(etf_items)}"
    )

    if (
        expected_stocks is not None
        and len(stock_items) != expected_stocks
    ):
        print()
        print(
            "WARNING: STOCK 數量與 "
            "statistics.stocks 不一致！"
        )

    if (
        expected_etf is not None
        and len(etf_items) != expected_etf
    ):
        print()
        print(
            "WARNING: ETF 數量與 "
            "statistics.etf 不一致！"
        )

    # ------------------------------------------------------------
    # 輸出 JSON
    # ------------------------------------------------------------

    output = {

        "verification_date": data.get(
            "date"
        ),

        "source_file": INPUT_FILE,

        "total_records": len(stocks),

        "stock_count": len(stock_items),

        "etf_count": len(etf_items),

        "qualified_count": len(
            qualified
        ),

        "required_conditions": (
            CONDITION_KEYS
        ),

        "distribution": {
            f"{i}/6":
                distribution.get(i, 0)
            for i in range(7)
        },

        "missing_condition_counts": dict(
            missing_conditions
        ),

        "qualified_stocks": qualified,
    }

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # ------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------

    fieldnames = [
        "code",
        "name",
        "price",
        "rsi",
        "kd_k",
        "kd_d",
        "macd",
        "macd_signal",
        "volume",
        "volume_ma5",
        "ma20",
        "ai_score",
        "signal",
    ]

    with open(
        OUTPUT_CSV,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for item in qualified:

            writer.writerow({
                key: item.get(key)
                for key in fieldnames
            })

    # ------------------------------------------------------------
    # 最終結果
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("6/6 完整符合結果")
    print("=" * 70)

    print(
        f"完整符合 6/6："
        f"{len(qualified)} 檔"
    )

    if qualified:

        for index, item in enumerate(
            qualified,
            start=1
        ):

            print(
                f"{index:>3}. "
                f"{item.get('code', ''):<8} "
                f"{item.get('name', ''):<12} "
                f"股價={item.get('price')} "
                f"RSI={item.get('rsi')} "
                f"AI={item.get('ai_score')}"
            )

    else:

        print(
            "沒有任何個股達到 6/6。"
        )

    # ------------------------------------------------------------
    # 分布
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("0/6 ～ 6/6 分布")
    print("=" * 70)

    for i in range(7):

        print(
            f"{i}/6："
            f"{distribution.get(i, 0)} 檔"
        )

    # ------------------------------------------------------------
    # 各條件未通過數
    # ------------------------------------------------------------

    print()
    print("=" * 70)
    print("各條件未通過數")
    print("=" * 70)

    for key in CONDITION_KEYS:

        print(
            f"{key:<24}"
            f"{missing_conditions.get(key, 0)}"
        )

    print()
    print("=" * 70)
    print("驗證完成")
    print("=" * 70)

    print(
        f"JSON：{OUTPUT_JSON}"
    )

    print(
        f"CSV ：{OUTPUT_CSV}"
    )


if __name__ == "__main__":
    main()
