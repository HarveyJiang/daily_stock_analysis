# -*- coding: utf-8 -*-
"""OpenBBFetcher 单元测试：mock OpenBB REST API 响应验证 fallback 与标准化。"""

from unittest.mock import MagicMock, patch

import pytest

import data_provider.openbb_fetcher as openbb_fetcher_module
from data_provider.openbb_fetcher import OpenBBFetcher
from data_provider.base import DataFetchError

_OK_PAYLOAD = {
    "results": [
        {"date": "2026-10-23", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 100000},
        {"date": "2026-10-24", "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.6, "volume": 120000},
    ],
    "provider": "yfinance",
}


def _response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_not_configured_disables_fetcher():
    fetcher = OpenBBFetcher(priority=6)
    if not fetcher.is_configured:
        with pytest.raises(DataFetchError):
            fetcher.get_daily_data("600519")


def test_provider_fallback_and_normalization():
    fetcher = OpenBBFetcher(priority=6)
    if not fetcher.is_configured:
        pytest.skip("OPENBB_API_URL 未配置")
    if fetcher._providers == ["akshare"]:
        pytest.skip("默认 provider 链需要至少两个 provider 验证 fallback")

    calls = []
    last_provider = fetcher._providers[-1]

    def fake_get(*args, **kwargs):
        params = kwargs["params"]
        calls.append(params["provider"])
        if params["provider"] != last_provider:
            raise ConnectionError("simulated network failure")
        return _response(_OK_PAYLOAD)

    with patch.object(openbb_fetcher_module.requests.Session, "get", fake_get):
        df = fetcher.get_daily_data("600519", start_date="2026-10-20", end_date="2026-10-24")

    assert calls == fetcher._providers  # 前面的 provider 都尝试过
    assert len(df) == 2
    for col in ("date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "code"):
        assert col in df.columns
    assert df["code"].iloc[0] == "600519"
    assert df["date"].iloc[0] <= df["date"].iloc[1]  # 升序
    assert 3.0 < float(df["pct_chg"].iloc[-1]) < 4.5  # (10.6-10.2)/10.2 = 3.92%


def test_all_providers_empty_raises_for_failover():
    fetcher = OpenBBFetcher(priority=6)
    if not fetcher.is_configured:
        pytest.skip("OPENBB_API_URL 未配置")

    def fake_get(*args, **kwargs):
        return _response({"results": [], "warnings": ["no data"]})

    with patch.object(openbb_fetcher_module.requests.Session, "get", fake_get):
        with pytest.raises(DataFetchError):
            fetcher.get_daily_data("600519", start_date="2026-10-20", end_date="2026-10-24")


def test_rejects_non_cn_code():
    fetcher = OpenBBFetcher(priority=6)
    if not fetcher.is_configured:
        pytest.skip("OPENBB_API_URL 未配置")
    with pytest.raises(DataFetchError):
        fetcher.get_daily_data("AAPL")
