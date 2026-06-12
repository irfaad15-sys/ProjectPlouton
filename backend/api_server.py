"""
FastAPI server — serves DuckDB data to the React frontend.

Replaces PocketBase as the API layer. All data comes from DuckDB.
Runs alongside the bot on port 8090 (same port PocketBase used,
so the frontend needs zero URL changes).
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)

app = FastAPI(title="Plouton API", version="2.0")


@app.exception_handler(AttributeError)
async def none_store_handler(request, exc):
    if "NoneType" in str(exc):
        return JSONResponse(
            status_code=503,
            content={"error": "Database not available. Start the bot first.", "bot_started": False},
        )
    raise exc


# CORS for the React dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DuckDB connection (read-only for the API)
_store: DuckDBStore | None = None
_shared_store: DuckDBStore | None = None

# Bot subprocess (when started via the UI)
_bot_process: subprocess.Popen | None = None
_RUN_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run.py")

# Sentinel set by run.py when the bot is running in the same process.
# Prevents the UI "Start" button from spawning a duplicate subprocess after
# the laptop wakes from sleep (heartbeat goes stale but bot is still alive).
_bot_running_in_process: bool = False


def set_bot_running_in_process(running: bool) -> None:
    global _bot_running_in_process
    _bot_running_in_process = running


def set_store(store: DuckDBStore | None) -> None:
    """Optionally inject a shared DuckDB store (used by in-process runner)."""
    global _shared_store
    _shared_store = store


def get_store() -> DuckDBStore | None:
    if _shared_store is not None:
        return _shared_store

    global _store
    if _store is None:
        db_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data", "tradingbot.duckdb",
        )
        # Standalone API opens read-only so the bot subprocess can hold the write lock.
        # If the DB doesn't exist yet (first run), _store stays None until bot creates it.
        try:
            _store = DuckDBStore(db_path, read_only=True)
        except Exception:
            _store = None
    return _store


def _serialize(obj):
    """Make DuckDB results JSON-serializable."""
    import pandas as pd
    from datetime import datetime
    import numpy as np
    if isinstance(obj, (datetime, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [_serialize(x) for x in obj.tolist()]
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    # pd.isna on a non-scalar (list/array) returns an array → ambiguous in `if`.
    # Only null-check true scalars.
    if np.isscalar(obj) or obj is None:
        try:
            if pd.isna(obj):
                return None
        except (TypeError, ValueError):
            pass
    return obj


def _clean(records):
    """Clean a list of dicts for JSON serialization."""
    if isinstance(records, dict):
        return {k: _serialize(v) for k, v in records.items()}
    return [{k: _serialize(v) for k, v in r.items()} for r in records]


def _parse_iso_ts(value) -> datetime | None:
    try:
        import pandas as pd
        if pd.isnull(value):
            return None
    except (TypeError, ValueError):
        pass
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _isnum(v) -> bool:
    """True only for a real, finite number (rejects None and NaN)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    return v == v and v not in (float("inf"), float("-inf"))


def _fmt_px(v) -> str:
    """Human price formatting that adapts to the coin's scale."""
    if v is None:
        return "?"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    if v >= 100:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.4f}"
    return f"${v:.5f}"


def _trade_explanation(t: dict) -> str:
    """Plain-language story of a trade, built from its stored fields:
    when/why it opened, the size, the risk plan, and how/when it exited."""
    g = t.get
    coin = g("instrument", "?")
    direction = (g("direction") or "").upper()
    entry = g("entry_price")
    open_ts = _parse_iso_ts(g("timestamp"))
    open_str = open_ts.strftime("%Y-%m-%d %H:%M UTC") if open_ts else "an unknown time"

    def pct_from_entry(p):
        if not entry or p is None:
            return ""
        try:
            return f" ({(float(p) / float(entry) - 1) * 100:+.1f}%)"
        except (TypeError, ValueError, ZeroDivisionError):
            return ""

    lines = []

    # 1. Headline — what, which way, when, at what price
    arrow = "📈" if direction == "LONG" else "📉"
    side = "LONG (bought)" if direction == "LONG" else "SHORT (sold)"
    lines.append(f"{arrow} {side} {coin} — opened {open_str} at {_fmt_px(entry)}.")

    # 2. Why it entered — the fib/golden-zone setup
    sh, sl_sw = g("swing_high"), g("swing_low")
    zlo, zhi = g("fib_zone_lower"), g("fib_zone_upper")
    fib = g("fib_level_triggered")
    why = []
    if _isnum(sh) and _isnum(sl_sw):
        lo, hi = (_fmt_px(sl_sw), _fmt_px(sh)) if direction == "LONG" else (_fmt_px(sh), _fmt_px(sl_sw))
        why.append(f"price retraced into the Fibonacci golden zone of the swing {lo} → {hi}")
    if _isnum(zlo) and _isnum(zhi):
        why.append(f"entry zone {_fmt_px(zlo)}–{_fmt_px(zhi)}")
    if fib:
        try:
            why.append(f"triggered at the {float(fib) * 100:.1f}% level")
        except (TypeError, ValueError):
            pass
    conf = g("confidence")
    conf_str = ""
    if conf is not None:
        try:
            conf_str = f" Confidence score {float(conf):.0f}%."
        except (TypeError, ValueError):
            pass
    lines.append("Why: " + ("; ".join(why) if why else "a valid setup formed") + f".{conf_str}")

    # 3. Size of the position
    qty, notional, lev, margin = g("quantity"), g("notional"), g("leverage"), g("initial_margin")
    bits = []
    if qty is not None:
        bits.append(f"{float(qty):.4f} {coin}")
    if notional is not None:
        bits.append(f"~${float(notional):,.2f} position")
    if lev is not None:
        bits.append(f"{float(lev):.0f}x leverage")
    if margin is not None:
        bits.append(f"${float(margin):.2f} margin at risk")
    if bits:
        lines.append("Size: " + ", ".join(bits) + ".")

    # 4. Risk plan — stop, targets, liquidation
    slp, tp1, tp2, liq = g("stop_loss"), g("tp1_price"), g("tp2_price"), g("liquidation_price")
    plan = []
    if slp is not None:
        plan.append(f"stop {_fmt_px(slp)}{pct_from_entry(slp)}")
    if tp1 is not None:
        plan.append(f"TP1 {_fmt_px(tp1)}{pct_from_entry(tp1)}")
    if tp2 is not None:
        plan.append(f"TP2 {_fmt_px(tp2)}{pct_from_entry(tp2)}")
    if liq is not None and float(liq or 0) > 0:
        plan.append(f"liquidation {_fmt_px(liq)}")
    if plan:
        lines.append("Plan: " + ", ".join(plan) + ".")

    # 5. Outcome — open status or how/when it closed
    status = (g("status") or "").upper()
    if status != "CLOSED":
        if g("tp1_hit"):
            lines.append("Status: OPEN — TP1 hit, partial profit booked, stop moved to breakeven on the rest.")
        else:
            lines.append("Status: OPEN — running, no target hit yet.")
    else:
        exit_ts = _parse_iso_ts(g("exit_timestamp"))
        exit_price, reason, pnl = g("exit_price"), (g("exit_reason") or "").upper(), g("pnl")
        be = (
            g("tp1_hit") and ("SL" in reason or "STOP" in reason or "BREAKEVEN" in reason)
            and slp is not None and entry is not None
            and abs(float(slp) - float(entry)) / float(entry) < 0.001
        )
        if "TP2" in reason or "BACKTEST" in reason:
            label = "hit TP2 — the full target"
        elif "TP1" in reason:
            label = "closed at TP1"
        elif "LIQUID" in reason:
            label = "was LIQUIDATED"
        elif be or "BREAKEVEN" in reason:
            label = "stopped at breakeven after banking TP1 (a risk-free trade)"
        elif "SL" in reason or "STOP" in reason:
            label = "hit the stop loss"
        elif "MANUAL" in reason:
            label = "was closed manually"
        else:
            label = f"closed ({reason or 'unknown reason'})"
        when = f" on {exit_ts.strftime('%Y-%m-%d %H:%M UTC')}" if exit_ts else ""
        hold = ""
        if open_ts and exit_ts:
            hrs = (exit_ts - open_ts).total_seconds() / 3600
            hold = f", held {hrs:.1f}h" if hrs < 48 else f", held {hrs / 24:.1f} days"
        pnl_str = ""
        if pnl is not None:
            pnl = float(pnl)
            pnl_str = f" Result: {'+' if pnl >= 0 else ''}${pnl:.2f}."
        lines.append(f"Exit: {label} at {_fmt_px(exit_price)}{when}{hold}.{pnl_str}")

    note = t.get("analysis_notes")
    if note:
        lines.append(f"Notes: {note}")
    return "\n".join(lines)


class SettingsPatch(BaseModel):
    instrument: str | None = None
    balance: float | None = None
    trading_mode: str | None = None
    force_market_open: bool | None = None
    # Strategy params — saved to strategy_configs.params, read live by scanner
    min_confidence_pct: float | None = None
    risk_per_trade_pct: float | None = None
    max_open_trades: int | None = None
    min_slope_pct: float | None = None


class BacktestTradePayload(BaseModel):
    instrument: str
    direction: str                       # LONG | SHORT
    entry_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    exit_price: float
    pnl: float
    quantity: float
    notional: float
    leverage: int
    initial_margin: float
    liquidation_price: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    fib_zone_upper: float | None = None
    fib_zone_lower: float | None = None
    fib_level_triggered: float | None = None
    rr_tp1: float | None = None
    rr_tp2: float | None = None
    confidence: float | None = None
    timestamp: str                       # ISO8601
    exit_timestamp: str | None = None
    analysis_notes: str | None = None


# ── Health ───────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    store = get_store()
    db_ok = store.health_check() if store else False
    return {"code": 200, "message": "OK", "data": {"duckdb": db_ok}}


# ── Bot Process Control ──────────────────────────────────────────

def _bot_process_alive() -> bool:
    return _bot_process is not None and _bot_process.poll() is None


@app.post("/api/bot/start")
async def bot_start():
    global _bot_process
    if _bot_running_in_process:
        return {"ok": True, "status": "already_running_in_process"}
    if _bot_process_alive():
        return {"ok": True, "status": "already_running", "pid": _bot_process.pid}
    try:
        _bot_process = subprocess.Popen(
            [sys.executable, os.path.abspath(_RUN_PY), "--no-api"],
            cwd=os.path.dirname(os.path.abspath(_RUN_PY)),
        )
        return {"ok": True, "status": "started", "pid": _bot_process.pid}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/bot/stop")
async def bot_stop():
    global _bot_process
    if not _bot_process_alive():
        return {"ok": True, "status": "not_running"}
    try:
        _bot_process.terminate()
        try:
            _bot_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _bot_process.kill()
        _bot_process = None
        return {"ok": True, "status": "stopped"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


# ── Bot State ────────────────────────────────────────────────────

@app.get("/api/bot_state")
async def get_bot_state():
    store = get_store()
    state = store.get_bot_state()
    return _clean(state) if state else {}


@app.get("/api/runtime")
async def get_runtime():
    """Bot runtime diagnostics (alive heartbeat + actual/effective market state)."""
    store = get_store()
    state = store.get_bot_state() or {}

    heartbeat = _parse_iso_ts(state.get("last_heartbeat") or state.get("last_updated"))
    now = datetime.now(timezone.utc)
    heartbeat_age_sec = int((now - heartbeat).total_seconds()) if heartbeat else None

    # 5m cycle by default. Consider alive up to 2 cycles (+ buffer) without heartbeat.
    alive_threshold_sec = 660
    status = str(state.get("status", "STOPPED")).upper()
    # In-process bot (run.py) stays "alive" even when heartbeat is stale
    # (e.g. laptop woke from sleep — asyncio was paused, not killed).
    bot_alive = _bot_running_in_process or _bot_process_alive() or bool(
        heartbeat_age_sec is not None
        and heartbeat_age_sec <= alive_threshold_sec
        and status in {"RUNNING", "WAITING"}
    )

    return _clean({
        "bot_alive": bot_alive,
        "heartbeat_age_sec": heartbeat_age_sec,
        "alive_threshold_sec": alive_threshold_sec,
        "last_heartbeat": heartbeat,
        "market_actual_open": True,
        "market_effective_open": True,
        "force_market_open": True,
        "market_display": "Crypto perpetuals — 24/7",
    })


@app.get("/api/settings")
async def get_settings_state():
    from config import settings as bot_settings
    store = get_store()
    state = store.get_bot_state() or {}
    strategy_params = store.get_active_strategy_params()
    return _clean({
        "instrument": state.get("instrument", "BTC"),
        "balance": state.get("balance", 500.0),
        "trading_mode": state.get("trading_mode", "paper"),
        "force_market_open": state.get("force_market_open", False),
        # Strategy params — live-tunable without restart
        "min_confidence_pct": float(strategy_params.get("min_confidence_pct", bot_settings.min_confidence_pct)),
        "risk_per_trade_pct": float(strategy_params.get("risk_per_trade_pct", bot_settings.risk_per_trade_pct * 100)),
        "max_open_trades": int(strategy_params.get("max_open_trades", bot_settings.max_open_trades)),
        "min_slope_pct": float(strategy_params.get("min_slope_pct", 0.5)),
    })


@app.patch("/api/settings")
async def patch_settings(payload: SettingsPatch):
    from config import settings as bot_settings
    store = get_store()
    bot_updates = {}
    strategy_updates = {}

    if payload.instrument is not None:
        bot_updates["instrument"] = payload.instrument

    if payload.balance is not None:
        bot_updates["balance"] = float(payload.balance)

    if payload.trading_mode is not None:
        mode = payload.trading_mode.lower().strip()
        if mode not in {"paper", "live"}:
            return JSONResponse(status_code=400, content={"error": "trading_mode must be 'paper' or 'live'"})
        bot_updates["trading_mode"] = mode

    if payload.force_market_open is not None:
        bot_updates["force_market_open"] = bool(payload.force_market_open)

    # Strategy params — written to strategy_configs.params, read live by scanner
    if payload.min_confidence_pct is not None:
        strategy_updates["min_confidence_pct"] = float(payload.min_confidence_pct)
    if payload.risk_per_trade_pct is not None:
        strategy_updates["risk_per_trade_pct"] = float(payload.risk_per_trade_pct)
    if payload.max_open_trades is not None:
        strategy_updates["max_open_trades"] = int(payload.max_open_trades)
    if payload.min_slope_pct is not None:
        strategy_updates["min_slope_pct"] = float(payload.min_slope_pct)

    if not bot_updates and not strategy_updates:
        return {"ok": True, "updated": 0}

    if bot_updates:
        store.update_bot_state(bot_updates)
    if strategy_updates:
        store.update_active_strategy_params(strategy_updates)

    return {"ok": True, "updated": len(bot_updates) + len(strategy_updates)}


# ── Candles ──────────────────────────────────────────────────────

@app.get("/api/candles")
async def get_candles(
    instrument: str = "BTC",
    timeframe: str = "5m",
    limit: int = 500,
):
    store = get_store()
    df = store.get_candles(
        instrument=instrument,
        timeframe=timeframe,
        periods=limit,
    )
    if df.empty:
        return []
    df = df.reset_index()
    df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    records = df.to_dict("records")
    return _clean(records)


@app.get("/api/live_candles")
async def get_live_candles(
    instrument: str = "BTC",
    timeframe: str = "4h",
    limit: int = 300,
):
    """Fetch candles live from Hyperliquid — always current, bypasses DuckDB cache."""
    from backend.data.hyperliquid_fetcher import HyperliquidFetcher
    try:
        fetcher = HyperliquidFetcher()
        df = fetcher.fetch_ohlcv(instrument, timeframe=timeframe, limit=limit)
        df = df.reset_index()
        df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
        records = df.to_dict("records")
        return _clean(records)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


# ── Trades ───────────────────────────────────────────────────────

@app.get("/api/trades")
async def get_trades(
    limit: int = 50,
    status: str | None = None,
    trade_type: str | None = None,
    sort: str = "-timestamp",
):
    store = get_store()
    sort_desc = sort.startswith("-")
    trades = store.get_trades(limit=limit, status=status, sort_desc=sort_desc)
    if trade_type:
        trades = [t for t in trades if (t.get("trade_type") or "paper") == trade_type]
    for tr in trades:
        tr["explanation"] = _trade_explanation(tr)
    return _clean(trades)


@app.post("/api/trades/backtest")
async def create_backtest_trade(payload: BacktestTradePayload):
    """Insert a manually verified backtest trade for record-keeping."""
    import uuid
    store = get_store()
    trade_id = str(uuid.uuid4())[:16]
    row = {
        "id": trade_id,
        "timestamp": payload.timestamp,
        "instrument": payload.instrument,
        "direction": payload.direction,
        "entry_price": payload.entry_price,
        "exit_price": payload.exit_price,
        "quantity": payload.quantity,
        "stop_loss": payload.stop_loss,
        "take_profit": payload.tp2_price,
        "tp1_price": payload.tp1_price,
        "tp2_price": payload.tp2_price,
        "tp1_hit": True,
        "pnl": payload.pnl,
        "status": "CLOSED",
        "exit_reason": "BACKTEST_TP2_HIT",
        "exit_timestamp": payload.exit_timestamp or payload.timestamp,
        "strategy_name": "Golden Pocket",
        "asset_class": "crypto",
        "trade_type": "backtest",
        "notional": payload.notional,
        "leverage": float(payload.leverage),
        "initial_margin": payload.initial_margin,
        "liquidation_price": payload.liquidation_price,
        "swing_high": payload.swing_high,
        "swing_low": payload.swing_low,
        "fib_zone_upper": payload.fib_zone_upper,
        "fib_zone_lower": payload.fib_zone_lower,
        "fib_level_triggered": payload.fib_level_triggered,
        "rr_tp1": payload.rr_tp1,
        "rr_tp2": payload.rr_tp2,
        "confidence": payload.confidence,
        "analysis_notes": payload.analysis_notes,
    }
    row = {k: v for k, v in row.items() if v is not None}
    store.insert_trade(row)
    return {"ok": True, "id": trade_id}


@app.get("/api/carry")
async def get_carry():
    """Funding-carry book: delta-neutral pairs + net yield so far."""
    store = get_store()
    try:
        rows = store.list_carry_positions()
    except Exception:
        return {"positions": [], "note": "carry table not initialised yet"}
    out = []
    for r in rows:
        net = float(r.get("funding_collected") or 0) - float(r.get("fees_paid") or 0)
        opened = _parse_iso_ts(r.get("opened_at"))
        hours = ((datetime.now(timezone.utc) - opened).total_seconds() / 3600) if opened else 0
        notional = float(r.get("notional") or 0)
        apr = (net / notional) * (8760 / hours) * 100 if notional and hours > 1 else None
        r["net_yield_usd"] = net
        r["realized_apr_pct"] = apr
        out.append(r)
    return _clean({"positions": out})


@app.get("/api/trades/{trade_id}")
async def get_trade(trade_id: str):
    store = get_store()
    trade = store.get_trade(trade_id)
    if trade is None:
        return JSONResponse(status_code=404, content={"error": "Trade not found"})
    has_initial, has_final = store.get_trade_chart_flags(trade_id)
    trade["chart_initial_png"] = has_initial
    trade["chart_final_png"] = has_final
    trade["explanation"] = _trade_explanation(trade)
    return _clean(trade)


class TradePatch(BaseModel):
    pnl: float | None = None
    status: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None


@app.patch("/api/trades/{trade_id}")
async def patch_trade(trade_id: str, payload: TradePatch):
    """Correct a closed trade's stored fields (e.g. pnl after TP1+TP2 fix)."""
    store = get_store()
    trade = store.get_trade(trade_id)
    if trade is None:
        return JSONResponse(status_code=404, content={"error": "Trade not found"})
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        return {"ok": True, "updated": 0}
    try:
        store.update_trade(trade_id, updates)
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    return {"ok": True, "updated": list(updates.keys())}


@app.get("/api/trades/{trade_id}/events")
async def get_trade_events(trade_id: str):
    store = get_store()
    if store is None:
        return []
    events = store.list_events_for_trade(trade_id)
    return _clean(events)


@app.delete("/api/trades/{trade_id}")
async def delete_trade(trade_id: str):
    store = get_store()
    trade = store.get_trade(trade_id)
    if trade is None:
        return JSONResponse(status_code=404, content={"error": "Trade not found"})
    store.delete_trade(trade_id)
    return {"ok": True, "deleted": trade_id}


# ── Signals ──────────────────────────────────────────────────────

@app.get("/api/signals")
async def get_signals(limit: int = 50):
    store = get_store()
    signals = store.get_signals(limit=limit)
    return _clean(signals)


# ── Strategy Configs ─────────────────────────────────────────────

@app.get("/api/strategy_configs")
async def get_strategy_configs():
    store = get_store()
    configs = store.get_strategy_configs()
    return _clean(configs)


@app.get("/api/strategy_configs/active")
async def get_active_strategy():
    store = get_store()
    config = store.get_active_strategy()
    return _clean(config) if config else {}


# ── MTF Trend ────────────────────────────────────────────────────

@app.get("/api/mtf_trend")
async def get_mtf_trend(instrument: str = "GC=F"):
    store = get_store()
    trend = store.compute_mtf_trend(instrument=instrument)
    return _clean(trend)


# ── Analytics ────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    """Summary stats for the dashboard."""
    store = get_store()
    state = store.get_bot_state() or {}
    candle_counts = {}
    for tf in ["5m", "15m", "1h"]:
        candle_counts[tf] = store.get_candle_count(
            instrument=state.get("instrument", "GC=F"),
            timeframe=tf,
        )
    return _clean({
        "bot_state": state,
        "candle_counts": candle_counts,
        "instruments": store.get_instruments(),
    })


@app.get("/api/monitor")
async def get_monitor():
    """Per-coin monitoring status: last scan, candle counts, signal stats."""
    from backend.config import settings as bot_settings
    store = get_store()
    coins = bot_settings.coins

    # The bot scans on its execution timeframe (4h by default), not 5m.
    exec_tf = bot_settings.execution_tf_default
    # The heartbeat updates every cycle, so it's the truthful "last scan" time
    # (the newest candle timestamp can be up to one TF-period old).
    state = store.get_bot_state() or {}
    heartbeat = state.get("last_heartbeat") or state.get("last_updated")

    result = []
    for coin in coins:
        # Last execution-TF candle = proxy for last scan time
        last_candle = store.conn.execute(
            "SELECT MAX(timestamp) FROM candles WHERE instrument = ? AND timeframe = ?",
            [coin, exec_tf],
        ).fetchone()
        candle_count = store.conn.execute(
            "SELECT COUNT(*) FROM candles WHERE instrument = ? AND timeframe = ?",
            [coin, exec_tf],
        ).fetchone()[0]

        # Latest signal
        sig_row = store.conn.execute(
            """SELECT timestamp, direction, confidence
               FROM signals WHERE instrument = ?
               ORDER BY timestamp DESC LIMIT 1""",
            [coin],
        ).fetchone()
        signal_count = store.conn.execute(
            "SELECT COUNT(*) FROM signals WHERE instrument = ?", [coin]
        ).fetchone()[0]

        # Latest close price
        price_row = store.conn.execute(
            "SELECT close FROM candles WHERE instrument = ? AND timeframe = ? ORDER BY timestamp DESC LIMIT 1",
            [coin, exec_tf],
        ).fetchone()

        result.append(_clean({
            "coin": coin,
            "last_scan": heartbeat if candle_count > 0 else (last_candle[0] if last_candle else None),
            "candle_count": candle_count,
            "last_signal_time": sig_row[0] if sig_row else None,
            "last_signal_direction": sig_row[1] if sig_row else None,
            "last_signal_confidence": sig_row[2] if sig_row else None,
            "signal_count": signal_count,
            "last_price": price_row[0] if price_row else None,
            "has_data": candle_count > 0,
        }))

    return result



@app.put("/api/trades/{trade_id}/charts")
async def put_trade_charts(trade_id: str, request: Request):
    """Receive JSON with base64-encoded initial/final PNGs and store them."""
    import base64
    store = get_store()
    trade = store.get_trade(trade_id)
    if not trade:
        return JSONResponse(status_code=404, content={"error": "Trade not found"})
    body = await request.json()
    updates = {}
    if "initial" in body:
        updates["chart_initial_png"] = base64.b64decode(body["initial"])
    if "final" in body:
        updates["chart_final_png"] = base64.b64decode(body["final"])
    if updates:
        store.update_trade(trade_id, updates)
    return {"ok": True, "updated": list(updates.keys())}


@app.get("/api/trades/{trade_id}/chart")
def get_trade_chart(trade_id: str, type: str = "final"):
    """Stream the saved chart PNG. type=initial|final, defaults to final (falls back to initial)."""
    store = get_store()
    col = "chart_initial_png" if type == "initial" else "chart_final_png"
    fallback_col = "chart_initial_png"
    row = store.conn.execute(
        f"SELECT {col}, {fallback_col} FROM trades WHERE id = ?", [trade_id]
    ).fetchone()
    if not row:
        return Response(status_code=404)
    png = row[0] if row[0] else row[1]
    if not png:
        return Response(status_code=404)
    if isinstance(png, (memoryview, bytearray)):
        png = bytes(png)
    return Response(content=png, media_type="image/png")


@app.post("/api/trades/{trade_id}/generate_chart")
async def generate_trade_chart(trade_id: str):
    """Generate (or re-generate) Fibonacci analysis chart for a trade."""
    import sys, os
    import pandas as pd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from charts.fib_chart import generate_fib_chart

    store = get_store()
    trade = store.get_trade(trade_id)
    if not trade:
        return JSONResponse(status_code=404, content={"error": "Trade not found"})

    instrument = str(trade.get("instrument", "BTC"))
    df = store.get_candles(instrument=instrument, timeframe="5m", periods=500)
    if df.empty:
        return JSONResponse(status_code=422, content={"error": f"No candle data for {instrument}"})

    df_reset = df.reset_index()
    df_reset.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    trade_plain = {k: (v.item() if hasattr(v, "item") else v) for k, v in trade.items()}

    try:
        png = generate_fib_chart(df_reset, trade_plain, title_suffix="Analysis")
        store.update_trade(trade_id, {"chart_final_png": png, "chart_initial_png": png})
        return {"ok": True, "bytes": len(png)}
    except Exception as exc:
        logger.exception("Chart generation failed")
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Live Fibonacci Analysis ───────────────────────────────────────

@app.get("/api/fib_analysis/{coin}")
async def get_fib_analysis(coin: str):
    """
    Live Golden Pocket Fibonacci analysis for any coin.
    Returns swing, zone, all retracement levels, and whether price is currently in the zone.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from strategy.golden_pocket import GoldenPocketStrategy
    from strategy.atr import compute_atr
    from config import settings as bot_settings

    store = get_store()
    df = store.get_candles(instrument=coin, timeframe="5m", periods=500)
    if df.empty:
        return JSONResponse(status_code=404, content={"error": f"No candle data for {coin}"})

    # Column names from get_candles are capitalised (Open/High/Low/Close/Volume)
    if "Close" not in df.columns and "close" in df.columns:
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                 "close": "Close", "volume": "Volume"})

    strat = GoldenPocketStrategy()
    swing = strat.detect_swing(df)
    if swing is None:
        return _clean({"coin": coin, "swing": None, "zone": None, "levels": {},
                        "current_price": float(df["Close"].iloc[-1]), "in_golden_pocket": False})

    rng = swing.high - swing.low
    direction = swing.direction  # "UP" or "DOWN"
    zone = strat.golden_pocket_zone(swing.low, swing.high, direction)

    atr_series = compute_atr(df, period=bot_settings.atr_period)
    atr = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
    current_price = float(df["Close"].iloc[-1])

    # Retracement levels — always measured from the dominant swing high/low.
    # For UP swings (LONG setups): retracement goes from high downward.
    # For DOWN swings (SHORT setups): bounce goes from low upward.
    if direction == "UP":
        levels = {
            "swing_low":  {"price": swing.low,              "label": "Swing Low (0%)",          "role": "base"},
            "fib_236":    {"price": swing.high - 0.236*rng, "label": "23.6% Retrace",            "role": "fib"},
            "fib_382":    {"price": swing.high - 0.382*rng, "label": "38.2% Retrace",            "role": "fib"},
            "gp_upper":   {"price": swing.high - 0.500*rng, "label": "50% — Golden Pocket Upper","role": "zone"},
            "gp_lower":   {"price": swing.high - 0.618*rng, "label": "61.8% — Golden Pocket Lower","role":"zone"},
            "fib_786":    {"price": swing.high - 0.786*rng, "label": "78.6% SL Zone",            "role": "sl"},
            "swing_high": {"price": swing.high,             "label": "Swing High (100%)",        "role": "base"},
            "ext_1618":   {"price": swing.high + 0.618*rng, "label": "1.618 Extension (TP2)",    "role": "tp"},
        }
    else:
        levels = {
            "swing_high": {"price": swing.high,             "label": "Swing High (100%)",        "role": "base"},
            "fib_236":    {"price": swing.low  + 0.236*rng, "label": "23.6% Bounce",             "role": "fib"},
            "fib_382":    {"price": swing.low  + 0.382*rng, "label": "38.2% Bounce",             "role": "fib"},
            "gp_lower":   {"price": swing.low  + 0.500*rng, "label": "50% — Golden Pocket Lower","role": "zone"},
            "gp_upper":   {"price": swing.low  + 0.618*rng, "label": "61.8% — Golden Pocket Upper","role":"zone"},
            "fib_786":    {"price": swing.low  + 0.786*rng, "label": "78.6% SL Zone",            "role": "sl"},
            "swing_low":  {"price": swing.low,              "label": "Swing Low (0%)",            "role": "base"},
            "ext_1618":   {"price": swing.low  - 0.618*rng, "label": "1.618 Extension (TP2)",    "role": "tp"},
        }

    in_zone = zone.lower <= current_price <= zone.upper
    mtf_trend = store.compute_mtf_trend(instrument=coin)

    high_ts = swing.high_idx
    low_ts  = swing.low_idx
    return _clean({
        "coin": coin,
        "current_price": current_price,
        "direction": direction,
        "trend_1h": mtf_trend.get("1h", {}).get("trend"),
        "slope_1h": mtf_trend.get("1h", {}).get("slope", 0),
        "in_golden_pocket": in_zone,
        "swing": {
            "high": swing.high,
            "low":  swing.low,
            "high_ts": high_ts.isoformat() if hasattr(high_ts, "isoformat") else str(high_ts),
            "low_ts":  low_ts.isoformat()  if hasattr(low_ts,  "isoformat") else str(low_ts),
            "range": rng,
        },
        "zone": {"upper": zone.upper, "lower": zone.lower},
        "levels": levels,
        "atr": atr,
    })


# ── Backtest / Manual Trade Entry ────────────────────────────────

class BacktestTradeIn(BaseModel):
    instrument: str
    direction: str           # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float       # legacy TP / TP2
    quantity: float
    timestamp: str           # ISO-8601 entry time
    status: str = "CLOSED"
    strategy_name: str = "golden_pocket"
    asset_class: str = "crypto"
    trade_type: str = "backtest"
    # Optional enrichment
    tp1_price: float | None = None
    tp2_price: float | None = None
    tp1_hit: bool | None = None
    exit_price: float | None = None
    exit_timestamp: str | None = None
    exit_reason: str | None = None
    pnl: float | None = None
    confidence: float | None = None
    leverage: float | None = None
    notional: float | None = None
    initial_margin: float | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    fib_zone_upper: float | None = None
    fib_zone_lower: float | None = None
    fib_level_triggered: float | None = None
    rr_tp1: float | None = None
    rr_tp2: float | None = None
    analysis_notes: str | None = None


@app.post("/api/trades")
async def create_backtest_trade(body: BacktestTradeIn):
    """
    Insert a backtest or manual trade record.
    Generates a UUID, stores all provided fields, and optionally
    writes trade_events for entry / TP1 / TP2 exits.
    """
    import uuid
    store = get_store()

    trade_id = str(uuid.uuid4())[:16]
    row = {
        "id": trade_id,
        **{k: v for k, v in body.model_dump().items() if v is not None},
    }
    store.insert_trade(row)

    # Persist lifecycle events so TradeDetail timeline renders properly
    events = []
    events.append({
        "id": str(uuid.uuid4())[:16],
        "trade_id": trade_id,
        "event_type": "entry",
        "price": body.entry_price,
        "pnl_partial": None,
        "timestamp": body.timestamp,
    })

    if body.tp1_price and body.tp1_hit:
        sl_dist = abs(body.entry_price - body.stop_loss)
        qty_half = body.quantity / 2
        tp1_pnl = qty_half * abs(body.tp1_price - body.entry_price)
        events.append({
            "id": str(uuid.uuid4())[:16],
            "trade_id": trade_id,
            "event_type": "TP1_hit",
            "price": body.tp1_price,
            "pnl_partial": round(tp1_pnl, 4),
            "timestamp": body.exit_timestamp or body.timestamp,
        })

    if body.exit_price and body.exit_reason:
        events.append({
            "id": str(uuid.uuid4())[:16],
            "trade_id": trade_id,
            "event_type": body.exit_reason,
            "price": body.exit_price,
            "pnl_partial": body.pnl,
            "timestamp": body.exit_timestamp or body.timestamp,
        })

    for ev in events:
        try:
            cols = list(ev.keys())
            vals = [ev[c] for c in cols]
            store.conn.execute(
                f"INSERT INTO trade_events ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})",
                vals,
            )
        except Exception as e:
            logger.warning(f"Could not insert trade_event: {e}")

    return {"id": trade_id, "status": "created", "events_inserted": len(events)}


# ── Frontend SPA (serve built React app) ─────────────────────────

_FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "frontend", "dist",
)

_STATIC_MIME = {
    ".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".woff2": "font/woff2", ".woff": "font/woff",
}

if os.path.isdir(_FRONTEND_DIST):
    # Serve hashed JS/CSS bundles from /assets/
    app.mount("/assets", StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve static root files (images, fonts) or index.html for SPA routes."""
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": "Not found"})
        # Check if it's a real file in the dist root (logo, icons, fonts, etc.)
        candidate = os.path.join(_FRONTEND_DIST, full_path)
        if os.path.isfile(candidate):
            ext = os.path.splitext(full_path)[1].lower()
            mime = _STATIC_MIME.get(ext, "application/octet-stream")
            return Response(content=open(candidate, "rb").read(), media_type=mime)
        # Everything else → SPA index
        index = os.path.join(_FRONTEND_DIST, "index.html")
        return Response(
            content=open(index, "rb").read(),
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="info")
