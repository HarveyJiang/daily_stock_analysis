# -*- coding: utf-8 -*-
"""
OpenBBFetcher — A 股数据源（OpenBB Data Platform 网关）

通过 OpenBB Platform 的 REST API 获取国内日线行情。
OpenBB 官方原生对中国区覆盖有限，A 股数据需在 OpenBB 侧安装
``openbb_akshare`` / ``openbb_tushare`` provider 扩展后，
由本 Fetcher 的多 provider fallback 链消费。

自建 OpenBB API 服务步骤（一次配置）：
    pip install "openbb[all]" openbb_akshare uvicorn
    python -c "import openbb; openbb.build()"
    openbb-api            # 默认 http://127.0.0.1:6900

环境变量：
    OPENBB_API_URL      OpenBB REST API 地址（必填，配置后 Fetcher 启用）
    OPENBB_API_USER     HTTP Basic 认证用户名（未开启鉴权则留空）
    OPENBB_API_PASSWORD HTTP Basic 认证密码
    OPENBB_PROVIDERS    provider fallback 链，逗号分隔，默认 "akshare,yfinance"
    OPENBB_PRIORITY     数据源优先级（数字越小越靠前，默认 6）

接口：GET /api/v1/equity/price/historical?symbol=<code>&provider=<p>&start_date=<..>&end_date=<..>
"""

import logging
import os
from typing import List, Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS

logger = logging.getLogger(__name__)


class OpenBBFetcher(BaseFetcher):
    """A 股日线数据源：包装 OpenBB Data Platform REST API。"""

    name = "OpenBBFetcher"
    priority = 6

    def __init__(self, priority: Optional[int] = None):
        from src.config import get_config

        config = get_config()
        self._api_url = (
            getattr(config, "openbb_api_url", None) or os.getenv("OPENBB_API_URL") or ""
        ).strip().rstrip("/")
        self._user = (
            getattr(config, "openbb_api_user", None) or os.getenv("OPENBB_API_USER") or ""
        ).strip()
        self._password = os.getenv("OPENBB_API_PASSWORD") or ""
        if not self._user:
            self._password = (
                getattr(config, "openbb_api_password", None) or self._password or ""
            ).strip()

        providers_env = (
            getattr(config, "openbb_providers", None)
            or os.getenv("OPENBB_PROVIDERS")
            or "akshare,yfinance"
        )
        self._providers: List[str] = [
            p.strip().lower() for p in str(providers_env).split(",") if p.strip()
        ]

        if not self._api_url:
            logger.debug("[OpenBB] OPENBB_API_URL 未配置，Fetcher 不启用")

        if priority is not None:
            self.priority = int(priority)

        self._session = requests.Session()
        if self._user:
            self._session.auth = (self._user, self._password)

    @property
    def is_configured(self) -> bool:
        return bool(self._api_url)

    def _is_available(self) -> bool:
        return self.is_configured

    def is_available_for_request(self, capability: str = "") -> Optional[bool]:
        return self.is_configured

    def _historical_url(self, stock_code: str) -> str:
        # openbb_equity 路径固定 /api/v1/equity/price/historical
        return f"{self._api_url}/api/v1/equity/price/historical"

    def _fetch_provider(
        self, stock_code: str, provider: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        try:
            resp = self._session.get(
                self._historical_url(stock_code),
                params={
                    "symbol": stock_code,
                    "provider": provider,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout=30,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise DataFetchError(
                f"[OpenBB:{provider}] 请求失败({stock_code}): {e}"
            ) from e

        rows = payload.get("results") if isinstance(payload, dict) else None
        if not rows:
            raise DataFetchError(
                f"[OpenBB:{provider}] {stock_code} 无数据"
                f"(warnings={payload.get('warnings') if isinstance(payload, dict) else None})"
            )
        return pd.DataFrame(rows)

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        if not self.is_configured:
            raise DataFetchError("[OpenBB] OPENBB_API_URL 未配置")
        if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
            raise DataFetchError(f"[OpenBB] 仅支持 6 位 A 股代码，收到: {stock_code}")

        last_error: Optional[Exception] = None
        for provider in self._providers:
            try:
                self.random_sleep(0.3, 0.8)
                df = self._fetch_provider(stock_code, provider, start_date, end_date)
                logger.info("[OpenBB] %s 通过 provider=%s 获取到 %d 行", stock_code, provider, len(df))
                return df
            except DataFetchError as e:
                last_error = e
                logger.debug("[OpenBB] provider=%s 失败: %s，尝试下一个", provider, e)

        raise last_error or DataFetchError("[OpenBB] 无可用 provider")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()

        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.rename(
            columns={
                "volume": "volume",
                "total_turnover": "amount",
                "turnover": "amount",
            }
        )
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                raise DataFetchError(f"[OpenBB] {stock_code} 返回缺少 {col} 列")
        if "amount" not in df.columns and "close" in df.columns and "volume" in df.columns:
            df["amount"] = df["volume"] * df["close"]
        df["pct_chg"] = df["close"].pct_change() * 100
        df["pct_chg"] = df["pct_chg"].fillna(0).round(2)
        df["code"] = stock_code

        keep = ["code"] + STANDARD_COLUMNS
        df = df[[col for col in keep if col in df.columns]]
        return df
