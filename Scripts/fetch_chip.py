# ============================================================
# TWSE day-trading OpenAPI
# ============================================================

def fetch_twse_daytrade_openapi() -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    """
    TWSE 官方 OpenAPI：

        /v1/exchangeReport/TWTB4U

    官方定義：
        上市股票每日當日沖銷交易標的及統計

    回傳：

        data
        data_date
        source_success

    注意：
        不依賴固定欄位 index。
    """

    url = (
        f"{TWSE_OPENAPI_BASE}/"
        "exchangeReport/TWTB4U"
    )

    data = get_json(
        url
    )

    if not isinstance(
        data,
        list,
    ):

        return {}, None, False

    result = {}

    dates = set()

    for row in data:

        if not isinstance(
            row,
            dict,
        ):

            continue

        code = get_dict_code(
            row
        )

        if not is_valid_symbol(
            code
        ):

            continue

        row_date = get_dict_date(
            row
        )

        if row_date:

            dates.add(
                row_date
            )

        volume_key = dict_field_name(
            row,
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
                "DayTradingShares",
                "DayTradeShares",
                "IntradayTradingShares",
                "TradingShares",
            ],
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
                "DayTradingShares",
                "IntradayTradingShares",
            ],
        )

        if volume_key is None:

            continue

        volume = safe_number(
            row.get(
                volume_key
            )
        )

        if (
            volume is None
            or volume < 0
        ):

            continue

        result[code] = round(
            volume,
            2,
        )

    data_date = None

    if len(dates) == 1:

        data_date = next(
            iter(dates)
        )

    elif len(dates) > 1:

        data_date = max(
            dates
        )

    return (
        result,
        data_date,
        True,
    )


# ============================================================
# TWSE day-trading HTML fallback
# ============================================================

def fetch_twse_daytrade_html(
    date_obj: datetime,
) -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    """
    TWSE 官方 HTML fallback。

    使用：
        /exchangeReport/TWTB4U

    response=html

    不使用：
        /rwd/... response=json
    """

    url = (
        f"{TWSE_WEB_BASE}/"
        "exchangeReport/TWTB4U"
    )

    params = {
        "date": yyyymmdd(
            date_obj
        ),
        "response": "html",
        "selectType": "All",
    }

    response = get_response(
        url,
        params,
    )

    if response is None:

        return {}, None, False

    parser = TableParser()

    try:

        parser.feed(
            response.text
        )

    except Exception:

        return {}, None, False

    rows = parser.rows

    if not rows:

        return {}, None, False

    target_date = (
        date_obj.strftime(
            "%Y-%m-%d"
        )
    )

    page_date = None

    for row in rows[:20]:

        for value in row:

            parsed = normalize_date_text(
                value
            )

            if parsed:

                page_date = parsed

                break

        if page_date:

            break

    header_index = None
    code_index = None
    volume_index = None

    for index, row in enumerate(
        rows
    ):

        normalized = [
            normalize_field_name(
                x
            )
            for x in row
        ]

        has_code = any(
            (
                "證券代號" in value
                or "股票代號" in value
            )
            for value in normalized
        )

        has_volume = any(
            (
                "當日沖銷交易成交股數"
                in value
                or "當日沖銷成交股數"
                in value
            )
            for value in normalized
        )

        if (
            has_code
            and has_volume
        ):

            header_index = index

            for i, value in enumerate(
                normalized
            ):

                if (
                    "證券代號" in value
                    or "股票代號" in value
                ):

                    code_index = i
                    break

            for i, value in enumerate(
                normalized
            ):

                if (
                    "當日沖銷交易成交股數"
                    in value
                    or "當日沖銷成交股數"
                    in value
                ):

                    volume_index = i
                    break

            break

    if (
        header_index is None
        or code_index is None
        or volume_index is None
    ):

        return {}, page_date, False

    if (
        page_date is not None
        and page_date != target_date
    ):

        return {}, page_date, False

    result = {}

    for row in rows[
        header_index + 1:
    ]:

        if (
            code_index >= len(row)
            or volume_index >= len(row)
        ):

            continue

        code = clean_code(
            row[code_index]
        )

        if not is_valid_symbol(
            code
        ):

            continue

        volume = safe_number(
            row[volume_index]
        )

        if (
            volume is None
            or volume < 0
        ):

            continue

        result[code] = round(
            volume,
            2,
        )

    return (
        result,
        page_date,
        True,
    )


# ============================================================
# TPEx day-trading OpenAPI
# ============================================================

def fetch_tpex_daytrade_openapi() -> Tuple[
    Dict[str, float],
    Optional[str],
    bool,
]:

    """
    TPEx 官方 OpenAPI：

        /openapi/v1/tpex_intraday_trading_statistics

    官方名稱：

        上櫃股票現股當沖交易統計資訊

    動態尋找：
        股票代號
        資料日期
        當日沖銷成交股數
    """

    url = (
        f"{TPEX_OPENAPI_BASE}/"
        "tpex_intraday_trading_statistics"
    )

    data = get_json(
        url
    )

    if not isinstance(
        data,
        list,
    ):

        return {}, None, False

    result = {}

    dates = set()

    for row in data:

        if not isinstance(
            row,
            dict,
        ):

            continue

        code = get_dict_code(
            row
        )

        if not is_valid_symbol(
            code
        ):

            continue

        row_date = get_dict_date(
            row
        )

        if row_date:

            dates.add(
                row_date
            )

        volume_key = dict_field_name(
            row,
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
                "IntradayTradingShares",
                "DayTradingShares",
                "DayTradeShares",
                "TradingShares",
            ],
            [
                "當日沖銷交易成交股數",
                "當日沖銷成交股數",
                "IntradayTradingShares",
                "DayTradingShares",
            ],
        )

        if volume_key is None:

            continue

        volume = safe_number(
            row.get(
                volume_key
            )
        )

        if (
            volume is None
            or volume < 0
        ):

            continue

        result[code] = round(
            volume,
            2,
        )

    data_date = None

    if len(dates) == 1:

        data_date = next(
            iter(dates)
        )

    elif len(dates) > 1:

        data_date = max(
            dates
        )

    return (
        result,
        data_date,
        True,
    )