"""Funding-rate carry harvester (paper) — the one backtest-validated strategy.

Holds a delta-neutral pair per coin: SHORT the perp + LONG spot, equal notional.
Price PnL nets to ~zero by construction; the return is the hourly funding the
short perp leg collects (validated ~+7-12% APR on high-funding coins over a full
year, fees on — see backtest/FUNDING_CARRY_SCOPE.md).

Rules (from the backtest — passive carry; do NOT churn on hourly sign flips):
  - ENTER when the trailing 7-day mean funding is positive.
  - HOLD and accrue funding continuously.
  - EXIT only when the trailing 7-day mean funding turns negative (regime flip).

Paper simplifications: the spot leg fills at the perp price (basis ≈ 0 not
modelled); funding accrues at the latest hourly rate over elapsed time rather
than on exact hourly settles.
"""

import json
import logging
import urllib.request
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"


class CarryHarvester:
    COINS = ["HYPE", "TAO"]      # highest validated funding with on-venue spot
    ALLOC_PER_COIN = 50.0        # $ paper notional per leg
    FEE_PER_LEG = 0.00065        # taker + slippage, per leg per side
    TRAIL_DAYS = 7               # regime window for enter/exit

    def __init__(self, fetcher, store):
        self.fetcher = fetcher
        self.store = store
        store.ensure_carry_table()
        self._regime_cache: dict[str, tuple[datetime, float]] = {}

    async def tick(self) -> None:
        for coin in self.COINS:
            try:
                self._tick_coin(coin)
            except Exception as e:
                logger.warning(f"[carry:{coin}] tick failed: {e}")

    def _tick_coin(self, coin: str) -> None:
        now = datetime.now(timezone.utc)
        pos = self.store.get_carry_position(coin)
        is_open = pos is not None and str(pos.get("status")) == "OPEN"

        if is_open:
            # 1. Accrue funding since last accrual at the current hourly rate.
            rate = self.fetcher.fetch_funding_rate(coin)
            last = pos.get("last_accrual") or pos.get("opened_at")
            last = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
            hours = max((now - last).total_seconds() / 3600.0, 0.0)
            accrued = rate * float(pos["notional"]) * hours  # short receives positive funding
            pos["funding_collected"] = float(pos["funding_collected"]) + accrued
            pos["last_accrual"] = now
            pos["last_funding_hr"] = rate

            # 2. Exit only on a regime flip (trailing mean < 0), never on hourly noise.
            regime = self._trailing_mean_funding(coin, now)
            if regime is not None and regime < 0:
                fees = 2 * self.FEE_PER_LEG * float(pos["notional"])  # close both legs
                pos["fees_paid"] = float(pos["fees_paid"]) + fees
                pos["status"] = "CLOSED"
                pos["closed_at"] = now
                pos["exit_reason"] = f"7d funding flipped negative ({regime*24*365*100:+.1f}% ann)"
                logger.info(
                    f"[carry:{coin}] CLOSED pair — {pos['exit_reason']} | "
                    f"funding ${pos['funding_collected']:.4f} fees ${pos['fees_paid']:.4f}"
                )
            self.store.upsert_carry_position(pos)
            return

        # Flat: enter when the 7d funding regime is positive.
        regime = self._trailing_mean_funding(coin, now)
        if regime is None or regime <= 0:
            return
        price = float(self.fetcher.fetch_ohlcv(coin, "1h", limit=2)["Close"].iloc[-1])
        notional = self.ALLOC_PER_COIN
        fees = 2 * self.FEE_PER_LEG * notional  # open both legs
        self.store.upsert_carry_position({
            "coin": coin, "status": "OPEN", "notional": notional,
            "qty": notional / price, "entry_price": price,
            "opened_at": now, "closed_at": None,
            "funding_collected": 0.0, "fees_paid": fees,
            "last_accrual": now, "last_funding_hr": None, "exit_reason": None,
        })
        logger.info(
            f"[carry:{coin}] OPENED delta-neutral pair — short perp + long spot, "
            f"${notional:.0f}/leg @ {price:.4f} | 7d funding {regime*24*365*100:+.1f}% ann"
        )

    def _trailing_mean_funding(self, coin: str, now: datetime) -> float | None:
        """Mean hourly funding over TRAIL_DAYS, cached for 1h to limit API calls."""
        cached = self._regime_cache.get(coin)
        if cached and (now - cached[0]) < timedelta(hours=1):
            return cached[1]
        start = int((now - timedelta(days=self.TRAIL_DAYS)).timestamp() * 1000)
        body = json.dumps({"type": "fundingHistory", "coin": coin, "startTime": start}).encode()
        req = urllib.request.Request(INFO_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                hist = json.load(r)
            rates = [float(x["fundingRate"]) for x in hist]
            mean = sum(rates) / len(rates) if rates else None
        except Exception as e:
            logger.warning(f"[carry:{coin}] funding history fetch failed: {e}")
            return None
        if mean is not None:
            self._regime_cache[coin] = (now, mean)
        return mean
