# 🤖 Plouton — Crypto Perpetuals Trading Bot (paper trading)

**Hermes V2** — An automated **paper-trading** bot for **Hyperliquid crypto perpetuals** with a multi-coin scanner, risk-based position sizing, a cost-aware paper broker, and a monitoring dashboard. **Live execution is NOT implemented** — `TRADING_MODE=live` intentionally refuses to start. Validate any strategy on paper (with a real edge) before considering live trading.

Project architecture, conventions, and current phase tracking are documented in `CLAUDE.md` (source of truth for AI and docs alignment).

## Architecture

- **Backend**: Python 3.11+ with asyncio — bot loop, strategy engine, risk manager, order execution
- **Exchange**: Hyperliquid REST + WebSocket — order execution, position tracking, real-time fills
- **Database**: DuckDB with WAL — local time-series store, event log, crash safety
- **Frontend**: React (Vite) + Tailwind CSS + shadcn/ui + TradingView lightweight-charts
- **Notifications**: Discord bot with TradingView embedded chart previews
- **Data**: Hyperliquid API (live) — 10-coin async scanner with signal aggregation

## Quick Start

### 1. Install Python dependencies
```bash
cd backend
pip install -r requirements.txt
```

### 2. Set up environment
```bash
cp .env.example .env
# Edit .env with your Hyperliquid API key (optional for paper trading)
```

### 3. Start the trading bot
```bash
python run.py
```
The bot will initialize DuckDB, restore any previous session state, and start monitoring 10 coins.
API server starts on `http://127.0.0.1:8090`

### 4. Start the dashboard (development)
```bash
cd frontend
npm install
npm run dev
```
Dashboard available at `http://localhost:5174`

## Configuration

Copy `.env.example` to `.env` and adjust:
- `HYPERLIQUID_API_KEY` — reserved for future live trading (unused; paper trading needs no key)
- `COINS` — comma-separated list of Hyperliquid perp symbols (default: `BTC,ETH,SOL,XRP,BNB,SUI,TAO,LINK,HYPE,ADA`)
- `TIMEFRAME` — candle interval in minutes (default: `5`)
- `PAPER_BALANCE` — starting paper balance in USD (default: `500`)
- `TRADING_MODE` — `paper` only (live is not implemented and will refuse to start)
- `STRATEGY` — active strategy name (default: `golden_pocket`) — options: `fibonacci`, `atr`, `golden_pocket`

Strategy parameters are tunable in the Settings page or via API.

## Project Structure

```
ProjectHermes/
├── backend/
│   ├── bot.py                 # Main event loop + trading engine
│   ├── run.py                 # Entry point — bot + API server
│   ├── pocketbase_client.py   # Legacy (read-only API access)
│   ├── config/
│   │   └── settings.py        # Pydantic BaseSettings (.env config)
│   ├── data/
│   │   ├── duckdb_store.py    # Time-series + event persistence
│   │   ├── market_data.py     # Market data aggregation
│   │   └── hyperliquid_fetcher.py  # Hyperliquid candle fetcher
│   ├── broker/
│   │   ├── base.py            # Broker interface
│   │   └── paper_broker.py    # Paper trading with margin tracking
│   ├── strategy/
│   │   ├── base.py            # Strategy abstract class
│   │   ├── fibonacci.py       # Fibonacci Retracement
│   │   ├── atr.py             # ATR Volatility
│   │   ├── golden_pocket.py   # Golden Pocket
│   │   └── indicators.py      # VWAP / swings / Fib (pure pandas/numpy)
│   ├── engine/
│   │   ├── signal_generator.py    # Multi-timeframe signals
│   │   ├── confidence_scorer.py   # Signal quality scoring
│   │   ├── quality_filter.py      # Trade entry filters
│   │   ├── order_manager.py       # Event sourcing + partial fills
│   │   ├── position_sizer.py      # Risk-based position sizing
│   │   └── risk_manager.py        # Stop-loss + liquidation prevention
│   ├── scanner/
│   │   ├── coin_scanner.py        # Async 10-coin monitor
│   │   └── async_runner.py        # Event loop management
│   ├── notifications/
│   │   ├── discord_notifier.py    # Discord alerts
│   │   └── chart_generator.py     # TradingView chart embeds
│   └── charts/
│       └── fib_chart.py           # Fibonacci chart generation
├── frontend/
│   ├── src/
│   │   ├── App.jsx                # Router + layout
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx      # Active trades + market status
│   │   │   ├── Monitor.jsx        # 10-coin signal monitor
│   │   │   ├── Chart.jsx          # TradingView candlestick chart
│   │   │   ├── Trades.jsx         # Trade history + timeline
│   │   │   ├── Strategy.jsx       # Strategy config
│   │   │   └── Settings.jsx       # Runtime tweaks
│   │   ├── components/
│   │   │   ├── Layout.jsx         # Sidebar + nav
│   │   │   ├── FibChart.jsx       # Fibonacci levels overlay
│   │   │   └── ui/               # shadcn/ui components
│   │   └── lib/
│   │       └── api.js            # API fetch helpers
│   └── vite.config.js
├── tests/
│   ├── broker/
│   ├── engine/
│   ├── strategy/
│   ├── scanner/
│   ├── data/
│   └── notifications/
├── pocketbase/                # Standalone server binary (optional read-only)
├── logs/                      # Runtime logs
├── CLAUDE.md                  # AI project memory (source of truth)
├── README.md                  # This file
└── run.py                     # Main entry point
```
