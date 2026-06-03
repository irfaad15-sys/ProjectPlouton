"""
DuckDB Analytics Store.

Provides an OLAP-optimized storage layer alongside PocketBase.
DuckDB is used for:
- Fast analytical queries across large candle datasets
- Pre-computed indicator storage
- Cross-instrument / cross-asset-class analytics
- Backtesting data access

PocketBase remains the primary CRUD store for the dashboard API.
DuckDB is a read-heavy companion for the trading engine.
"""

import logging
import os
import threading
from typing import Optional

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)


class DuckDBStore:
    """Persistent DuckDB analytics store for the trading bot."""

    def __init__(self, db_path: str | None = None, read_only: bool = False):
        """
        Initialize DuckDB connection.

        Args:
            db_path: Path to the DuckDB database file.
                     Defaults to backend/data/tradingbot.duckdb
            read_only: Open the database in read-only mode.
        """
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "tradingbot.duckdb",
            )

        self.db_path = db_path
        self._lock = threading.RLock()
        # DuckDB connection objects are NOT thread-safe. The bot loop and the
        # FastAPI server share one DuckDBStore (see run.py `set_store`), so a
        # single shared connection means concurrent execute() calls from the
        # bot thread and the dashboard's polling threads clobber each other's
        # results (fetchone()->None, .description->floats) and eventually trigger
        # a C++ null-deref crash. Hand each thread its own cursor on the same
        # database instead — the documented DuckDB multithreading pattern.
        self._base_conn = duckdb.connect(db_path, read_only=read_only)
        self._local = threading.local()
        if not read_only:
            self._create_tables()
            self._migrate_v2()
        logger.info(f"DuckDB store opened: {db_path}")

    @property
    def conn(self):
        """Per-thread DuckDB cursor on the shared database (thread-safe access)."""
        cursor = getattr(self._local, "cursor", None)
        if cursor is None:
            cursor = self._base_conn.cursor()
            self._local.cursor = cursor
        return cursor

    def _create_tables(self) -> None:
        """Create tables if they don't exist."""
        with self._lock:
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                timestamp TIMESTAMP NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE DEFAULT 0,
                instrument VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                asset_class VARCHAR DEFAULT 'futures',
                PRIMARY KEY (timestamp, instrument, timeframe)
            )
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                timestamp TIMESTAMP NOT NULL,
                instrument VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                indicator_name VARCHAR NOT NULL,
                value DOUBLE NOT NULL,
                PRIMARY KEY (timestamp, instrument, timeframe, indicator_name)
            )
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id VARCHAR PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                instrument VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                entry_price DOUBLE NOT NULL,
                exit_price DOUBLE,
                quantity DOUBLE NOT NULL,
                stop_loss DOUBLE NOT NULL,
                take_profit DOUBLE NOT NULL,
                pnl DOUBLE,
                status VARCHAR NOT NULL,
                strategy_name VARCHAR NOT NULL,
                asset_class VARCHAR DEFAULT 'futures',
                exit_timestamp TIMESTAMP,
                exit_reason VARCHAR
            )
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id VARCHAR DEFAULT (uuid()),
                timestamp TIMESTAMP NOT NULL,
                instrument VARCHAR NOT NULL,
                direction VARCHAR NOT NULL,
                strategy VARCHAR NOT NULL,
                entry_price DOUBLE NOT NULL,
                confidence DOUBLE,
                triggered_level DOUBLE,
                trend_direction VARCHAR,
                asset_class VARCHAR DEFAULT 'futures'
            )
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY DEFAULT 1,
                status VARCHAR DEFAULT 'STOPPED',
                instrument VARCHAR DEFAULT 'GC=F',
                strategy VARCHAR DEFAULT 'fibonacci_retracement',
                trading_mode VARCHAR DEFAULT 'paper',
                balance DOUBLE DEFAULT 500.0,
                initial_balance DOUBLE DEFAULT 500.0,
                daily_pnl DOUBLE DEFAULT 0.0,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                last_updated TIMESTAMP,
                last_heartbeat TIMESTAMP,
                error_message VARCHAR DEFAULT '',
                force_market_open BOOLEAN DEFAULT FALSE,
                market_status BOOLEAN DEFAULT FALSE,
                market_status_effective BOOLEAN DEFAULT FALSE,
                market_display VARCHAR DEFAULT ''
            )
            """)

            # Backward-compatible schema upgrades for existing DB files
            self.conn.execute("ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS trading_mode VARCHAR DEFAULT 'paper'")
            self.conn.execute("ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS last_heartbeat TIMESTAMP")
            self.conn.execute("ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS force_market_open BOOLEAN DEFAULT FALSE")
            self.conn.execute("ALTER TABLE bot_state ADD COLUMN IF NOT EXISTS market_status_effective BOOLEAN DEFAULT FALSE")

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_configs (
                id INTEGER PRIMARY KEY DEFAULT 1,
                strategy_name VARCHAR NOT NULL,
                display_name VARCHAR,
                params JSON NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                description VARCHAR DEFAULT ''
            )
            """)

            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_events (
                id          VARCHAR PRIMARY KEY,
                trade_id    VARCHAR NOT NULL,
                event_type  VARCHAR NOT NULL,
                price       DOUBLE,
                pnl_partial DOUBLE,
                timestamp   TIMESTAMP NOT NULL
            )
            """)

        # Seed bot_state if empty
        count = self.conn.execute(
            "SELECT COUNT(*) FROM bot_state"
        ).fetchone()[0]
        if count == 0:
            self.conn.execute("""
                INSERT INTO bot_state (id, status, instrument, strategy, balance, initial_balance)
                VALUES (1, 'STOPPED', 'GC=F', 'fibonacci_retracement', 500.0, 500.0)
            """)

        # Seed default strategy config if empty
        count = self.conn.execute(
            "SELECT COUNT(*) FROM strategy_configs"
        ).fetchone()[0]
        if count == 0:
            self.conn.execute("""
                INSERT INTO strategy_configs
                    (id, strategy_name, display_name, is_active, description, params)
                VALUES (1, 'golden_pocket', 'Golden Pocket', TRUE,
                    'Enters trades at the Golden Pocket retracement zone (0.618–0.65) of the most recent swing, confirmed by multi-timeframe trend alignment and ATR-based stop loss.',
                    '{"entry_zone_low": 0.618, "entry_zone_high": 0.65, "atr_sl_multiplier": 1.5, "atr_period": 14, "min_slope_pct": 0.002, "execution_tf": "5m", "confirmation_tf": "15m", "trend_tf": "1h", "risk_per_trade_pct": 3.0, "max_open_positions": 3, "max_daily_loss_pct": 15.0}'
                )
            """)

    def _migrate_v2(self) -> None:
        """Idempotent migration to v2 schema — confidence, chart blobs, asset_class default."""
        cols = self.conn.execute("PRAGMA table_info(trades)").fetchdf()
        existing = set(cols["name"].tolist()) if not cols.empty else set()

        if "confidence" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN confidence DOUBLE DEFAULT 0.0")
        if "chart_initial_png" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN chart_initial_png BLOB")
        if "chart_final_png" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN chart_final_png BLOB")
        if "tp1_price" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN tp1_price DOUBLE")
        if "tp2_price" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN tp2_price DOUBLE")
        if "tp1_hit" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN tp1_hit BOOLEAN DEFAULT FALSE")
        if "leverage" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN leverage DOUBLE DEFAULT 1.0")
        if "notional" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN notional DOUBLE DEFAULT 0.0")
        if "initial_margin" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN initial_margin DOUBLE DEFAULT 0.0")
        if "liquidation_price" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN liquidation_price DOUBLE")
        if "funding_rate_hr" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN funding_rate_hr DOUBLE")
        if "trade_type" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN trade_type VARCHAR DEFAULT 'paper'")
        if "fib_zone_upper" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN fib_zone_upper DOUBLE")
        if "fib_zone_lower" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN fib_zone_lower DOUBLE")
        if "swing_high" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN swing_high DOUBLE")
        if "swing_low" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN swing_low DOUBLE")
        if "fib_level_triggered" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN fib_level_triggered DOUBLE")
        if "rr_tp1" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN rr_tp1 DOUBLE")
        if "rr_tp2" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN rr_tp2 DOUBLE")
        if "analysis_notes" not in existing:
            self.conn.execute("ALTER TABLE trades ADD COLUMN analysis_notes VARCHAR")

        self.conn.execute(
            "UPDATE candles SET asset_class = 'crypto' WHERE asset_class IS NULL OR asset_class = 'futures'"
        )

        # Create trade_events table if missing (added after initial DB creation)
        existing_tables = {row[0] for row in self.conn.execute("SHOW TABLES").fetchall()}
        if "trade_events" not in existing_tables:
            self.conn.execute("""
            CREATE TABLE trade_events (
                id          VARCHAR PRIMARY KEY,
                trade_id    VARCHAR NOT NULL,
                event_type  VARCHAR NOT NULL,
                price       DOUBLE,
                pnl_partial DOUBLE,
                timestamp   TIMESTAMP NOT NULL
            )
            """)

    # ── Candle Operations ────────────────────────────────────────

    def store_candles(
        self,
        df: pd.DataFrame,
        instrument: str = "GC=F",
        timeframe: str = "5m",
        asset_class: str = "futures",
    ) -> int:
        """
        Bulk insert/update candles from a DataFrame.

        Uses INSERT OR REPLACE for upsert behavior.

        Args:
            df: DataFrame with OHLCV data and DatetimeIndex
            instrument: Instrument symbol
            timeframe: Candle timeframe
            asset_class: Asset class (futures, stocks, crypto)

        Returns:
            Number of rows inserted
        """
        if df.empty:
            return 0

        # Prepare data for insert
        records = []
        for ts, row in df.iterrows():
            records.append((
                pd.Timestamp(ts).to_pydatetime(),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row.get("Volume", 0)),
                instrument,
                timeframe,
                asset_class,
            ))

        # Use INSERT OR REPLACE for upsert
        self.conn.executemany("""
            INSERT OR REPLACE INTO candles
            (timestamp, open, high, low, close, volume,
             instrument, timeframe, asset_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)

        logger.debug(
            f"DuckDB: stored {len(records)} candles for "
            f"{instrument}/{timeframe}"
        )
        return len(records)

    def get_candles(
        self,
        instrument: str = "GC=F",
        timeframe: str = "5m",
        periods: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """
        Retrieve candles from DuckDB.

        Args:
            instrument: Instrument symbol
            timeframe: Candle timeframe
            periods: Number of most recent candles (overrides date range)
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with OHLCV data
        """
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE instrument = ? AND timeframe = ?
        """
        params = [instrument, timeframe]

        if start_date:
            query += " AND timestamp >= ?"
            params.append(start_date)
        if end_date:
            query += " AND timestamp <= ?"
            params.append(end_date)

        query += " ORDER BY timestamp DESC"

        if periods:
            query += f" LIMIT {periods}"

        df = self.conn.execute(query, params).fetchdf()

        if not df.empty:
            df = df.sort_values("timestamp")
            df = df.set_index("timestamp")
            df.columns = ["Open", "High", "Low", "Close", "Volume"]

        return df

    # ── Multi-Timeframe Trend Engine ──────────────────────────────

    def compute_mtf_trend(
        self,
        instrument: str = "GC=F",
    ) -> dict:
        """
        Compute trend direction across all stored timeframes using
        DuckDB SQL window functions.

        The MTF trend engine uses a VMA (Volume-weighted Moving Average)
        slope approach:
        1. For each TF, compute a 20-period VMA
        2. Compare current VMA to VMA 10 periods ago → slope
        3. Positive slope = UP, Negative slope = DOWN
        4. Stack all three TFs to determine trade eligibility

        Returns:
            {
                "1d":  {"trend": "UP"|"DOWN", "slope": float, "vma": float, "candles": int},
                "4h":  {"trend": "UP"|"DOWN", "slope": float, "vma": float, "candles": int},
                "1h":  {"trend": "UP"|"DOWN", "slope": float, "vma": float, "candles": int},
                "stacked": True|False,       # 1d and 1h agree?
                "direction": "UP"|"DOWN"|"MIXED",
            }
        """
        result = {}

        for tf in ["1d", "4h", "1h"]:
            try:
                row = self.conn.execute("""
                    WITH vma_calc AS (
                        SELECT
                            timestamp,
                            close,
                            volume,
                            -- VMA: volume-weighted moving average (20 periods)
                            SUM(close * volume) OVER w / NULLIF(SUM(volume) OVER w, 0)
                                AS vma_20,
                            ROW_NUMBER() OVER (
                                PARTITION BY instrument, timeframe
                                ORDER BY timestamp DESC
                            ) AS rn
                        FROM candles
                        WHERE instrument = ? AND timeframe = ?
                        WINDOW w AS (
                            PARTITION BY instrument, timeframe
                            ORDER BY timestamp
                            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                        )
                    )
                    SELECT
                        curr.vma_20 AS current_vma,
                        prev.vma_20 AS prev_vma,
                        curr.close AS current_price,
                        (SELECT COUNT(*) FROM candles
                         WHERE instrument = ? AND timeframe = ?) AS total_candles
                    FROM vma_calc curr
                    JOIN vma_calc prev ON prev.rn = 11
                    WHERE curr.rn = 1
                """, [instrument, tf, instrument, tf]).fetchone()

                if row and row[0] is not None and row[1] is not None:
                    current_vma = row[0]
                    prev_vma = row[1]
                    current_price = row[2]
                    total_candles = row[3]

                    # Slope is a FRACTION (e.g. 0.0064 = +0.64% VMA change over 10
                    # periods). All consumers — golden_pocket.min_slope_pct,
                    # confidence_scorer._slope_strength, the coin_scanner exec-TF
                    # switch, and the Discord summary's `slope*100` — expect a
                    # fraction. Do NOT multiply by 100 here.
                    slope = (current_vma - prev_vma) / prev_vma if prev_vma != 0 else 0
                    trend = "UP" if slope > 0 else "DOWN"

                    result[tf] = {
                        "trend": trend,
                        "slope": round(slope, 6),
                        "vma": round(current_vma, 2),
                        "price": round(current_price, 2),
                        "candles": total_candles,
                    }
                else:
                    result[tf] = {
                        "trend": "UNKNOWN",
                        "slope": 0,
                        "vma": 0,
                        "price": 0,
                        "candles": 0,
                    }
            except Exception as e:
                logger.warning(f"MTF trend calc failed for {tf}: {e}")
                result[tf] = {
                    "trend": "UNKNOWN", "slope": 0, "vma": 0,
                    "price": 0, "candles": 0,
                }

        # Stack consensus: 1d (macro) + 1h (intermediate) must agree.
        # 4h is the execution TF where the pullback forms — it retraces against
        # the macro trend by definition, so it is excluded from consensus.
        anchor_trends = [
            result[tf]["trend"]
            for tf in ("1d", "1h")
            if result.get(tf, {}).get("trend") not in (None, "UNKNOWN")
        ]
        if len(anchor_trends) == 2 and len(set(anchor_trends)) == 1:
            stacked = True
            direction = anchor_trends[0]
        else:
            stacked = False
            direction = "MIXED"

        result["stacked"] = stacked
        result["direction"] = direction

        return result

    def compute_and_store_indicators(
        self,
        instrument: str = "GC=F",
        timeframe: str = "5m",
    ) -> None:
        """
        Compute common indicators using DuckDB SQL and store them.

        Computes: SMA_20, SMA_50, VMA_20, VWAP
        Uses DuckDB window functions for efficient computation.
        """
        # SMA computations using window functions
        self.conn.execute("""
            INSERT OR REPLACE INTO indicators
                (timestamp, instrument, timeframe, indicator_name, value)
            SELECT
                timestamp,
                instrument,
                timeframe,
                'SMA_20',
                AVG(close) OVER (
                    PARTITION BY instrument, timeframe
                    ORDER BY timestamp
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                )
            FROM candles
            WHERE instrument = ? AND timeframe = ?
        """, [instrument, timeframe])

        self.conn.execute("""
            INSERT OR REPLACE INTO indicators
                (timestamp, instrument, timeframe, indicator_name, value)
            SELECT
                timestamp,
                instrument,
                timeframe,
                'SMA_50',
                AVG(close) OVER (
                    PARTITION BY instrument, timeframe
                    ORDER BY timestamp
                    ROWS BETWEEN 49 PRECEDING AND CURRENT ROW
                )
            FROM candles
            WHERE instrument = ? AND timeframe = ?
        """, [instrument, timeframe])

        # VMA (Volume-weighted Moving Average, 20 periods)
        self.conn.execute("""
            INSERT OR REPLACE INTO indicators
                (timestamp, instrument, timeframe, indicator_name, value)
            SELECT timestamp, instrument, timeframe, indicator_name, value FROM (
                SELECT
                    timestamp,
                    instrument,
                    timeframe,
                    'VMA_20' AS indicator_name,
                    SUM(close * volume) OVER (
                        PARTITION BY instrument, timeframe
                        ORDER BY timestamp
                        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                    ) /
                    NULLIF(
                        SUM(volume) OVER (
                            PARTITION BY instrument, timeframe
                            ORDER BY timestamp
                            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                        ), 0
                    ) AS value
                FROM candles
                WHERE instrument = ? AND timeframe = ?
            ) sub WHERE value IS NOT NULL
        """, [instrument, timeframe])

        # VWAP (cumulative session)
        self.conn.execute("""
            INSERT OR REPLACE INTO indicators
                (timestamp, instrument, timeframe, indicator_name, value)
            SELECT timestamp, instrument, timeframe, indicator_name, value FROM (
                SELECT
                    timestamp,
                    instrument,
                    timeframe,
                    'VWAP' AS indicator_name,
                    SUM((high + low + close) / 3.0 * volume) OVER (
                        PARTITION BY instrument, timeframe
                        ORDER BY timestamp
                        ROWS UNBOUNDED PRECEDING
                    ) /
                    NULLIF(
                        SUM(volume) OVER (
                            PARTITION BY instrument, timeframe
                            ORDER BY timestamp
                            ROWS UNBOUNDED PRECEDING
                        ), 0
                    ) AS value
                FROM candles
                WHERE instrument = ? AND timeframe = ?
            ) sub WHERE value IS NOT NULL
        """, [instrument, timeframe])

        logger.debug(
            f"DuckDB: computed indicators for {instrument}/{timeframe}"
        )

    def get_indicator_values(
        self,
        instrument: str = "GC=F",
        timeframe: str = "5m",
        indicator_name: str = "SMA_20",
        periods: int | None = None,
    ) -> pd.DataFrame:
        """
        Retrieve computed indicator values.

        Returns:
            DataFrame with timestamp and indicator value
        """
        query = """
            SELECT timestamp, value
            FROM indicators
            WHERE instrument = ?
              AND timeframe = ?
              AND indicator_name = ?
            ORDER BY timestamp DESC
        """
        params = [instrument, timeframe, indicator_name]

        if periods:
            query += f" LIMIT {periods}"

        df = self.conn.execute(query, params).fetchdf()
        if not df.empty:
            df = df.sort_values("timestamp")
            df = df.set_index("timestamp")
            df.columns = [indicator_name]
        return df

    # ── Analytics Queries ────────────────────────────────────────

    def query(self, sql: str, params: list | None = None) -> pd.DataFrame:
        """
        Run an ad-hoc SQL query and return results as DataFrame.

        Useful for custom analytics, backtesting queries, etc.

        Args:
            sql: SQL query string
            params: Optional query parameters

        Returns:
            DataFrame with query results
        """
        if params:
            return self.conn.execute(sql, params).fetchdf()
        return self.conn.execute(sql).fetchdf()

    def get_candle_count(
        self,
        instrument: str | None = None,
        timeframe: str | None = None,
    ) -> int:
        """Get total candle count, optionally filtered."""
        query = "SELECT COUNT(*) FROM candles WHERE 1=1"
        params = []

        if instrument:
            query += " AND instrument = ?"
            params.append(instrument)
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)

        result = self.conn.execute(query, params).fetchone()
        return result[0] if result else 0

    def get_instruments(self) -> list[str]:
        """Get list of all instruments stored in DuckDB."""
        result = self.conn.execute(
            "SELECT DISTINCT instrument FROM candles ORDER BY instrument"
        ).fetchall()
        return [row[0] for row in result]

    def get_asset_classes(self) -> list[str]:
        """Get list of all asset classes stored."""
        result = self.conn.execute(
            "SELECT DISTINCT asset_class FROM candles ORDER BY asset_class"
        ).fetchall()
        return [row[0] for row in result]

    # ── Bot State CRUD ──────────────────────────────────────────

    def get_bot_state(self) -> dict | None:
        """Get current bot state."""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM bot_state ORDER BY id ASC LIMIT 1"
            ).fetchdf()
        if row.empty:
            return None
        return row.iloc[0].to_dict()

    def update_bot_state(self, data: dict) -> None:
        """Update bot state fields."""
        with self._lock:
            row = self.conn.execute("SELECT id FROM bot_state ORDER BY id ASC LIMIT 1").fetchone()
            if row is None:
                self.conn.execute("INSERT INTO bot_state (id) VALUES (1)")
                row_id = 1
            else:
                row_id = row[0]

        sets = []
        vals = []
        for k, v in data.items():
            if k == "id":
                continue
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return
        sql = f"UPDATE bot_state SET {', '.join(sets)} WHERE id = ?"
        vals.append(row_id)
        with self._lock:
            self.conn.execute(sql, vals)

    # ── Trade CRUD ──────────────────────────────────────────────

    def create_trade(self, data: dict) -> str:
        """Insert a trade record. Returns the trade ID."""
        import uuid
        trade_id = str(uuid.uuid4())[:16]
        self.conn.execute("""
            INSERT INTO trades
                (id, timestamp, instrument, direction, entry_price,
                 quantity, stop_loss, take_profit, status, strategy_name,
                 asset_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            trade_id,
            data.get("timestamp"),
            data.get("instrument"),
            data.get("direction"),
            data.get("entry_price"),
            data.get("quantity"),
            data.get("stop_loss"),
            data.get("take_profit"),
            data.get("status", "OPEN"),
            data.get("strategy_name"),
            data.get("asset_class", "futures"),
        ])
        return trade_id

    def insert_trade(self, row: dict) -> None:
        """Insert a trade row — accepts any subset of valid trade columns."""
        import json
        # Serialize dict values (e.g. signal_metadata) to JSON strings
        processed = {}
        for k, v in row.items():
            if isinstance(v, dict):
                processed[k] = json.dumps(v)
            else:
                processed[k] = v
        cols = list(processed.keys())
        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        values = [processed[c] for c in cols]
        self.conn.execute(f"INSERT INTO trades ({col_list}) VALUES ({placeholders})", values)

    def list_open_trades(self) -> list[dict]:
        """Return all open trades as list of dicts."""
        df = self.conn.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp").fetchdf()
        if df.empty:
            return []
        return df.to_dict("records")

    def recent_sl_hit(self, instrument: str, within_hours: float = 2.0) -> bool:
        """Return True if this coin had an SL exit within the last `within_hours`."""
        row = self.conn.execute(
            """
            SELECT COUNT(*) FROM trades
            WHERE instrument = ?
              AND status = 'CLOSED'
              AND (exit_reason ILIKE '%SL%' OR exit_reason ILIKE '%STOP%')
              AND exit_timestamp >= now() - INTERVAL (? || ' hours')
            """,
            [instrument, str(within_hours)],
        ).fetchone()
        return bool(row and row[0] > 0)

    def list_all_trades(self) -> list[dict]:
        """Return all trades as list of dicts."""
        df = self.conn.execute("SELECT * FROM trades ORDER BY timestamp").fetchdf()
        if df.empty:
            return []
        return df.to_dict("records")

    def update_trade(self, trade_id: str, data: dict) -> None:
        """Update a trade record."""
        sets = []
        vals = []
        for k, v in data.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return
        vals.append(trade_id)
        sql = f"UPDATE trades SET {', '.join(sets)} WHERE id = ?"
        self.conn.execute(sql, vals)

    def get_open_trades(self) -> list[dict]:
        """Get all open trades."""
        df = self.conn.execute(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY timestamp"
        ).fetchdf()
        if df.empty:
            return []
        return df.to_dict("records")

    def count_open_trades(self) -> int:
        """Count open trades."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'OPEN'"
        ).fetchone()
        return row[0] if row else 0

    # Blob columns excluded from list views (too large for JSON, fetched separately)
    _BLOB_COLS = {"chart_initial_png", "chart_final_png"}

    def get_trades(
        self, limit: int = 50, status: str | None = None, sort_desc: bool = True,
    ) -> list[dict]:
        """Get trades without binary blob columns (charts fetched via /chart endpoint)."""
        # Discover non-blob columns dynamically so new columns are included automatically
        all_cols = [row[1] for row in self.conn.execute("PRAGMA table_info(trades)").fetchall()]
        cols = [c for c in all_cols if c not in self._BLOB_COLS]
        col_sql = ", ".join(cols)
        query = f"SELECT {col_sql} FROM trades"
        params = []
        if status:
            query += " WHERE UPPER(status) = UPPER(?)"
            params.append(status)
        query += f" ORDER BY timestamp {'DESC' if sort_desc else 'ASC'}"
        query += f" LIMIT {limit}"
        df = self.conn.execute(query, params).fetchdf()
        if df.empty:
            return []
        return df.to_dict("records")

    def get_trade(self, trade_id: str) -> dict | None:
        """Get a single trade by ID, excluding raw blob bytes."""
        all_cols = [row[1] for row in self.conn.execute("PRAGMA table_info(trades)").fetchall()]
        cols = [c for c in all_cols if c not in self._BLOB_COLS]
        col_sql = ", ".join(cols)
        df = self.conn.execute(
            f"SELECT {col_sql} FROM trades WHERE id = ?", [trade_id]
        ).fetchdf()
        if df.empty:
            return None
        return df.iloc[0].to_dict()

    def delete_trade(self, trade_id: str) -> None:
        """Hard-delete a trade record and its associated lifecycle events."""
        with self._lock:
            self.conn.execute("DELETE FROM trade_events WHERE trade_id = ?", [trade_id])
            self.conn.execute("DELETE FROM trades WHERE id = ?", [trade_id])

    def get_trade_chart_flags(self, trade_id: str) -> tuple[bool, bool]:
        """Return (has_initial, has_final) booleans for chart blob presence."""
        row = self.conn.execute(
            "SELECT chart_initial_png IS NOT NULL, chart_final_png IS NOT NULL FROM trades WHERE id = ?",
            [trade_id],
        ).fetchone()
        if not row:
            return False, False
        return bool(row[0]), bool(row[1])

    # ── Trade Events ────────────────────────────────────────────

    def insert_trade_event(self, event: dict) -> None:
        """Insert a lifecycle event for a trade."""
        with self._lock:
            self.conn.execute(
                "INSERT INTO trade_events (id, trade_id, event_type, price, pnl_partial, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    event["id"],
                    event["trade_id"],
                    event["event_type"],
                    event.get("price"),
                    event.get("pnl_partial"),
                    event["timestamp"],
                ],
            )

    def list_events_for_trade(self, trade_id: str) -> list[dict]:
        """Return lifecycle events for a trade, ordered by timestamp."""
        with self._lock:
            df = self.conn.execute(
                "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY timestamp ASC",
                [trade_id],
            ).fetchdf()
        if df.empty:
            return []
        return df.to_dict("records")

    # ── Signal CRUD ─────────────────────────────────────────────

    def create_signal(self, data: dict) -> None:
        """Insert a signal record."""
        self.conn.execute("""
            INSERT INTO signals
                (timestamp, instrument, direction, strategy,
                 entry_price, confidence, triggered_level,
                 trend_direction, asset_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            data.get("timestamp"),
            data.get("instrument"),
            data.get("direction"),
            data.get("strategy"),
            data.get("entry_price"),
            data.get("confidence"),
            data.get("triggered_level"),
            data.get("trend_direction"),
            data.get("asset_class", "futures"),
        ])

    def get_signals(self, limit: int = 50) -> list[dict]:
        """Get recent signals."""
        df = self.conn.execute(
            f"SELECT * FROM signals ORDER BY timestamp DESC LIMIT {limit}"
        ).fetchdf()
        if df.empty:
            return []
        return df.to_dict("records")

    # ── Strategy Config CRUD ────────────────────────────────────

    def get_active_strategy(self) -> dict | None:
        """Get the active strategy config."""
        df = self.conn.execute(
            "SELECT * FROM strategy_configs WHERE is_active = TRUE LIMIT 1"
        ).fetchdf()
        if df.empty:
            return None
        row = df.iloc[0].to_dict()
        # Parse JSON params
        if isinstance(row.get("params"), str):
            import json
            row["params"] = json.loads(row["params"])
        return row

    def get_active_strategy_params(self) -> dict:
        """Return the params dict of the active strategy config (live-tunable settings)."""
        import json
        row = self.conn.execute(
            "SELECT params FROM strategy_configs WHERE is_active = TRUE ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return {}
        try:
            return json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
        except Exception:
            return {}

    def update_active_strategy_params(self, updates: dict) -> None:
        """Merge updates into the active strategy params JSON."""
        import json
        current = self.get_active_strategy_params()
        current.update(updates)
        self.conn.execute(
            "UPDATE strategy_configs SET params = ? WHERE is_active = TRUE",
            [json.dumps(current)],
        )

    def get_strategy_configs(self) -> list[dict]:
        """Get all strategy configs."""
        df = self.conn.execute(
            "SELECT * FROM strategy_configs ORDER BY strategy_name"
        ).fetchdf()
        if df.empty:
            return []
        records = df.to_dict("records")
        import json
        for r in records:
            if isinstance(r.get("params"), str):
                r["params"] = json.loads(r["params"])
        return records

    def update_strategy_config(self, strategy_name: str, data: dict) -> None:
        """Update a strategy config."""
        import json
        sets = []
        vals = []
        for k, v in data.items():
            if k == "params" and isinstance(v, dict):
                sets.append(f"{k} = ?")
                vals.append(json.dumps(v))
            else:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        vals.append(strategy_name)
        sql = f"UPDATE strategy_configs SET {', '.join(sets)} WHERE strategy_name = ?"
        self.conn.execute(sql, vals)

    # ── Lifecycle ────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Check if DuckDB is working."""
        try:
            result = self.conn.execute("SELECT 1").fetchone()
            return result is not None and result[0] == 1
        except Exception:
            return False

    def close(self) -> None:
        """Close the DuckDB connection."""
        if getattr(self, "_base_conn", None) is not None:
            self._base_conn.close()
            self._base_conn = None
            logger.info("DuckDB store closed")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
