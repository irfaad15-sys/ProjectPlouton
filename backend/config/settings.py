"""Application settings loaded from .env with sensible defaults."""

from typing import List
from zoneinfo import ZoneInfo
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Coins (Hyperliquid perp symbols, comma-separated in .env)
    coins: List[str] = Field(default_factory=lambda: ["BTC", "ETH", "SOL", "XRP", "BNB", "SUI", "TAO", "LINK", "HYPE", "ADA"])

    @field_validator("coins", mode="before")
    @classmethod
    def split_coins(cls, v):
        if isinstance(v, str):
            return [c.strip().upper() for c in v.split(",") if c.strip()]
        return v

    # Paper trading
    # NOTE ON UNITS: risk_per_trade_pct and daily_loss_limit_pct are FRACTIONS,
    # not percentages. 0.01 = 1%, 0.15 = 15%. The Settings-UI / DB path stores
    # these as percentages (1.0 = 1%) and converts; the validators below reject
    # values that look like a percent typed into a fraction field (e.g. "1"),
    # which previously could size a single trade at 100% of the account.
    paper_balance: float = 500.0
    risk_per_trade_pct: float = 0.01
    max_open_trades: int = 3
    daily_loss_limit_pct: float = 0.15

    @field_validator("risk_per_trade_pct")
    @classmethod
    def _validate_risk(cls, v: float) -> float:
        if not 0 < v <= 0.1:
            raise ValueError(
                f"risk_per_trade_pct={v} is out of the sane range. "
                "Use a FRACTION: 0.01 = 1%. Max allowed is 0.1 (10%)."
            )
        return v

    @field_validator("daily_loss_limit_pct")
    @classmethod
    def _validate_daily_loss(cls, v: float) -> float:
        if not 0 < v <= 1.0:
            raise ValueError(
                f"daily_loss_limit_pct={v} is out of range. "
                "Use a FRACTION: 0.15 = 15%. Max allowed is 1.0 (100%)."
            )
        return v

    # Strategy selection: "golden_pocket" | "smc"
    strategy_name: str = "golden_pocket"

    # Strategy
    min_confidence_pct: float = 40.0
    execution_tf_default: str = "4h"   # 4h: moves clear fees; 5m bled (fees=84% of losses)
    confirmation_tf: str = "1h"
    trend_tf: str = "1d"
    atr_period: int = 14
    atr_sl_multiplier: float = 1.5
    min_slope_pct: float = 0.002

    # Chart
    chart_candles_before_signal: int = 400
    chart_candles_after_signal: int = 100
    chart_width_px: int = 1600
    chart_height_px: int = 800

    # Discord
    discord_webhook_url: str = ""

    # User timezone (display only)
    timezone: str = "UTC"

    @property
    def tz_info(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    # Legacy (kept temporarily so existing code doesn't crash mid-migration)
    instrument: str = "BTC"
    timeframe: str = "5m"
    trading_mode: str = "paper"
    force_market_open: bool = False


settings = Settings()
