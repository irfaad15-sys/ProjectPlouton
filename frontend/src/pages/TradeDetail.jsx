import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft, ArrowUpRight, ArrowDownRight,
  Target, Shield, TrendingUp, BarChart3, DollarSign, Zap,
} from 'lucide-react';
import api from '../lib/api';
import { formatCurrency, formatPnL, formatDateTime } from '../lib/utils';
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/Card';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Skeleton } from '../components/ui/Skeleton';
import FibChart from '../components/FibChart';

function Arrow() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '0 6px', color: '#1e293b', flexShrink: 0 }}>
      →
    </div>
  );
}

function PriceLevel({ label, price, sublabel, color, bg, border, tag, tagColor, dimmed }) {
  return (
    <div style={{
      flex: 1, minWidth: 0,
      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4,
      padding: '14px 10px', borderRadius: 10,
      background: bg, border: `1px solid ${border}`,
      opacity: dimmed ? 0.7 : 1,
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {label}
      </div>
      <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace', color, textAlign: 'center', wordBreak: 'break-all' }}>
        {price != null ? formatCurrency(price) : '—'}
      </div>
      {sublabel && (
        <div style={{ fontSize: 10, color: '#475569', textAlign: 'center', lineHeight: 1.3 }}>
          {sublabel}
        </div>
      )}
      {tag && (
        <span style={{
          fontSize: 9, fontWeight: 800, padding: '2px 7px', borderRadius: 4,
          background: `${tagColor}22`, color: tagColor,
          letterSpacing: '0.1em', textTransform: 'uppercase',
        }}>{tag}</span>
      )}
    </div>
  );
}

function InfoRow({ label, value, icon: Icon, iconColor, mono = true }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
    }}>
      <span style={{ fontSize: 12, color: '#64748b', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        {Icon && <Icon size={13} style={{ color: iconColor }} />}
        {label}
      </span>
      <span style={{ fontSize: 13, color: '#cbd5e1', fontFamily: mono ? 'JetBrains Mono, monospace' : 'inherit' }}>
        {value}
      </span>
    </div>
  );
}

function PnlRow({ label, value, color }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '10px 14px', borderRadius: 8,
      background: `rgba(${color === 'green' ? '16,185,129' : color === 'red' ? '239,68,68' : '59,130,246'},0.06)`,
      border: `1px solid rgba(${color === 'green' ? '16,185,129' : color === 'red' ? '239,68,68' : '59,130,246'},0.12)`,
    }}>
      <span style={{ fontSize: 12, color: '#94a3b8' }}>{label}</span>
      <span style={{ fontSize: 14, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace',
        color: color === 'green' ? '#10b981' : color === 'red' ? '#ef4444' : '#60a5fa' }}>
        {value}
      </span>
    </div>
  );
}

function EventChip({ eventType, price, pnlPartial, timestamp }) {
  const configs = {
    TP1_PARTIAL: { label: 'TP1 Hit', color: '#10b981', bg: 'rgba(16,185,129,0.08)', border: 'rgba(16,185,129,0.2)' },
    SL_MOVED:    { label: 'SL → Breakeven', color: '#60a5fa', bg: 'rgba(96,165,250,0.08)', border: 'rgba(96,165,250,0.2)' },
    TP2_HIT:     { label: 'TP2 Hit', color: '#10b981', bg: 'rgba(16,185,129,0.08)', border: 'rgba(16,185,129,0.2)' },
    SL_HIT:      { label: 'Stopped Out', color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)' },
  };
  const cfg = configs[eventType] || { label: eventType, color: '#94a3b8', bg: 'rgba(148,163,184,0.08)', border: 'rgba(148,163,184,0.2)' };
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.color, marginTop: 5, flexShrink: 0 }} />
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{
            fontSize: 11, fontWeight: 600, color: cfg.color,
            background: cfg.bg, border: `1px solid ${cfg.border}`,
            padding: '2px 8px', borderRadius: 4,
          }}>
            {cfg.label}
            {pnlPartial != null && ` · ${pnlPartial >= 0 ? '+' : ''}$${parseFloat(pnlPartial).toFixed(2)}`}
          </span>
          <span style={{ fontSize: 11, color: '#475569', fontFamily: 'JetBrains Mono, monospace' }}>
            {price != null ? `@ $${parseFloat(price).toFixed(4)}` : ''}
          </span>
        </div>
        <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
          {timestamp ? new Date(timestamp).toLocaleString() : ''}
        </div>
      </div>
    </div>
  );
}

export default function TradeDetail() {
  const { id } = useParams();
  const [trade, setTrade] = useState(null);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState([]);

  useEffect(() => {
    api.getTrade(id)
      .then(t => { setTrade(t); setLoading(false); })
      .catch(() => setLoading(false));
    api.getTradeEvents(id)
      .then(evts => setEvents(evts || []))
      .catch(() => {});
  }, [id]);

  if (loading) return <div style={{ padding: 24 }}><Skeleton className="h-96" /></div>;
  if (!trade || !trade.id) return <p style={{ padding: 24, color: '#94a3b8' }}>Trade not found</p>;

  const pnl = formatPnL(trade.pnl);
  const isClosed  = trade.status === 'CLOSED';
  const isWin     = (trade.pnl || 0) > 0;
  const isBacktest = trade.trade_type === 'backtest';

  // Position math
  const entry       = parseFloat(trade.entry_price || 0);
  const sl          = parseFloat(trade.stop_loss || 0);
  const tp1         = trade.tp1_price ? parseFloat(trade.tp1_price) : null;
  const tp2         = trade.tp2_price ? parseFloat(trade.tp2_price) : null;
  const exitPrice   = trade.exit_price ? parseFloat(trade.exit_price) : null;
  const qty         = parseFloat(trade.quantity || 0);
  const notional    = parseFloat(trade.notional || 0);
  const leverage    = parseFloat(trade.leverage || 1);
  const margin      = parseFloat(trade.initial_margin || 0);
  const liq         = trade.liquidation_price ? parseFloat(trade.liquidation_price) : null;
  const direction   = trade.direction;

  // Risk / reward in USD
  const sign = direction === 'SHORT' ? 1 : -1;
  const riskPerUnit  = Math.abs(entry - sl);
  const riskUsd      = riskPerUnit * qty;
  const rewardTp1Usd = tp1 ? Math.abs(entry - tp1) * qty : null;
  const rewardTp2Usd = tp2 ? Math.abs(entry - tp2) * qty : null;

  const rrTp1 = rewardTp1Usd && riskUsd ? (rewardTp1Usd / riskUsd).toFixed(2) : null;
  const rrTp2 = rewardTp2Usd && riskUsd ? (rewardTp2Usd / riskUsd).toFixed(2) : null;

  // P&L on margin (ROE)
  const pnlUsd     = trade.pnl ? parseFloat(trade.pnl) : null;
  const roePct     = pnlUsd && margin ? ((pnlUsd / margin) * 100).toFixed(1) : null;

  // What TP1/TP2 would have been worth
  const pnlAtTp1 = rewardTp1Usd ? rewardTp1Usd * (direction === 'SHORT' ? 1 : 1) : null;
  const pnlAtTp2 = rewardTp2Usd ? rewardTp2Usd : null;
  const roeAtTp1 = pnlAtTp1 && margin ? ((pnlAtTp1 / margin) * 100).toFixed(1) : null;
  const roeAtTp2 = pnlAtTp2 && margin ? ((pnlAtTp2 / margin) * 100).toFixed(1) : null;

  // Liquidation distance
  const liqDistPct = liq && entry
    ? (Math.abs(entry - liq) / entry * 100).toFixed(1)
    : null;

  // Whether SL was moved to breakeven (sl ≈ entry)
  const isBreakeven = sl > 0 && entry > 0 && Math.abs(entry - sl) / entry < 0.0005;

  // Determine what triggered the exit
  const exitEvent = events.find(e => ['TP1_PARTIAL', 'TP2_HIT', 'SL_HIT'].includes(e.event_type));
  const r = (trade.exit_reason || '').toUpperCase();
  const exitLabel = exitEvent?.event_type === 'TP2_HIT' ? 'TP2 Hit'
    : exitEvent?.event_type === 'TP1_PARTIAL' ? 'TP1 Hit'
    : exitEvent?.event_type === 'SL_HIT' && isBreakeven ? 'Breakeven Exit'
    : exitEvent?.event_type === 'SL_HIT' ? 'Stopped Out'
    : r.includes('TP2') ? 'TP2 Hit'
    : r.includes('TP1') ? 'TP1 Hit'
    : (r.includes('SL') || r.includes('STOP')) && isBreakeven ? 'Breakeven Exit'
    : r.includes('SL') || r.includes('STOP') ? 'Stopped Out'
    : trade.exit_reason || (isClosed ? 'Closed' : null);

  // Price distance helper
  function pct(from, to) {
    if (!from || !to) return null;
    const v = ((to - from) / from * 100);
    return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <Link to="/trades">
          <Button variant="secondary" size="sm"><ArrowLeft size={16} /></Button>
        </Link>
        <div style={{ flex: 1 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: '#f1f5f9', letterSpacing: '-0.02em' }}>
            {trade.instrument} · {trade.direction} · {trade.strategy_name || 'Golden Pocket'}
          </h1>
          <p style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{formatDateTime(trade.timestamp)}</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {isBacktest && (
            <span style={{
              fontSize: '10px', fontWeight: 700, padding: '3px 10px', borderRadius: 6,
              background: 'rgba(251,191,36,0.12)', color: '#fbbf24',
              letterSpacing: '0.08em', textTransform: 'uppercase',
            }}>Backtest</span>
          )}
          <Badge variant={!isClosed ? 'info' : isWin ? 'success' : 'danger'} dot>
            {!isClosed ? 'Open' : isWin ? 'Win' : 'Loss'}
          </Badge>
        </div>
      </div>

      {/* ── Trade Explanation ── */}
      {trade.explanation && (
        <Card>
          <CardHeader>
            <CardTitle icon={BarChart3} iconColor="#a78bfa">What happened in this trade</CardTitle>
          </CardHeader>
          <CardContent style={{ padding: '4px 24px 20px' }}>
            {trade.explanation.split('\n').map((line, i) => (
              <p key={i} style={{
                fontSize: 13.5, lineHeight: 1.6, color: '#cbd5e1',
                margin: '0 0 8px', whiteSpace: 'pre-wrap',
              }}>{line}</p>
            ))}
          </CardContent>
        </Card>
      )}

      {/* ── Price Levels Card ── */}
      <Card>
        <CardContent style={{ padding: '20px 24px' }}>
          <div style={{ display: 'flex', alignItems: 'stretch', gap: 0 }}>

            {/* Entry */}
            <PriceLevel
              label="Entry"
              price={entry}
              sublabel={formatDateTime(trade.timestamp)}
              color={direction === 'LONG' ? '#10b981' : '#ef4444'}
              bg={direction === 'LONG' ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)'}
              border={direction === 'LONG' ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)'}
              tag={direction}
              tagColor={direction === 'LONG' ? '#10b981' : '#ef4444'}
            />

            <Arrow />

            {/* SL */}
            <PriceLevel
              label={isBreakeven ? 'Stop Loss' : 'Stop Loss'}
              price={isBreakeven ? null : sl}
              sublabel={isBreakeven ? 'moved to BE' : pct(entry, sl)}
              color={isBreakeven ? '#60a5fa' : '#ef4444'}
              bg={isBreakeven ? 'rgba(96,165,250,0.05)' : 'rgba(239,68,68,0.05)'}
              border={isBreakeven ? 'rgba(96,165,250,0.15)' : 'rgba(239,68,68,0.15)'}
              tag={isBreakeven ? 'BE' : null}
              tagColor="#60a5fa"
              dimmed
            />

            <Arrow />

            {/* TP1 */}
            {tp1 && (
              <>
                <PriceLevel
                  label="TP1"
                  price={tp1}
                  sublabel={pct(entry, tp1)}
                  color="#10b981"
                  bg={exitPrice && Math.abs(exitPrice - tp1) / tp1 < 0.002 ? 'rgba(16,185,129,0.12)' : 'rgba(16,185,129,0.05)'}
                  border={exitPrice && Math.abs(exitPrice - tp1) / tp1 < 0.002 ? 'rgba(16,185,129,0.35)' : 'rgba(16,185,129,0.12)'}
                  tag={exitPrice && Math.abs(exitPrice - tp1) / tp1 < 0.002 ? 'EXIT' : null}
                  tagColor="#10b981"
                />
                <Arrow />
              </>
            )}

            {/* TP2 */}
            {tp2 && (
              <PriceLevel
                label="TP2"
                price={tp2}
                sublabel={pct(entry, tp2)}
                color="#10b981"
                bg={exitPrice && Math.abs(exitPrice - tp2) / tp2 < 0.002 ? 'rgba(16,185,129,0.12)' : 'rgba(16,185,129,0.05)'}
                border={exitPrice && Math.abs(exitPrice - tp2) / tp2 < 0.002 ? 'rgba(16,185,129,0.35)' : 'rgba(16,185,129,0.12)'}
                tag={exitPrice && Math.abs(exitPrice - tp2) / tp2 < 0.002 ? 'EXIT' : null}
                tagColor="#10b981"
              />
            )}

            {/* Actual exit if it doesn't match TP1/TP2 exactly */}
            {exitPrice && !(!tp1 && !tp2) && !(tp1 && Math.abs(exitPrice - tp1) / tp1 < 0.002) && !(tp2 && Math.abs(exitPrice - tp2) / tp2 < 0.002) && (
              <>
                <Arrow />
                <PriceLevel
                  label="Exit"
                  price={exitPrice}
                  sublabel={exitLabel}
                  color={isWin ? '#10b981' : '#ef4444'}
                  bg={isWin ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)'}
                  border={isWin ? 'rgba(16,185,129,0.25)' : 'rgba(239,68,68,0.25)'}
                  tag="EXIT"
                  tagColor={isWin ? '#10b981' : '#ef4444'}
                />
              </>
            )}

            {/* Open — show current placeholder */}
            {!isClosed && (
              <>
                <Arrow />
                <PriceLevel
                  label="Current"
                  price={null}
                  sublabel="live"
                  color="#3b82f6"
                  bg="rgba(59,130,246,0.05)"
                  border="rgba(59,130,246,0.15)"
                  tag="OPEN"
                  tagColor="#3b82f6"
                />
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ── Interactive TradingView-style Fibonacci chart ── */}
      <Card>
        <CardHeader>
          <CardTitle icon={BarChart3} iconColor="#3b82f6">
            Fibonacci Analysis · {trade.instrument} · {direction}
          </CardTitle>
          <span style={{ fontSize: 11, color: '#475569' }}>Interactive — scroll to zoom, drag to pan</span>
        </CardHeader>
        <CardContent style={{ padding: '0 0 16px' }}>
          <FibChart trade={trade} />
        </CardContent>
      </Card>

      {/* ── P&L Breakdown row ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 14 }}>
        {/* Actual result */}
        <Card style={{ borderColor: isClosed && isWin ? 'rgba(16,185,129,0.2)' : isClosed ? 'rgba(239,68,68,0.2)' : undefined }}>
          <CardHeader>
            <CardTitle icon={DollarSign} iconColor={isClosed ? (isWin ? '#10b981' : '#ef4444') : '#3b82f6'}>
              {isClosed ? 'Realised P&L' : 'Open Position'}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {isClosed && pnlUsd !== null ? (
                <>
                  <PnlRow label="P&L (USD)" value={`${pnlUsd >= 0 ? '+' : ''}$${Math.abs(pnlUsd).toFixed(2)}`} color={isWin ? 'green' : 'red'} />
                  {roePct && <PnlRow label="ROE (on margin)" value={`${pnlUsd >= 0 ? '+' : ''}${roePct}%`} color={isWin ? 'green' : 'red'} />}
                </>
              ) : (
                <PnlRow label="Unrealised P&L" value="— (open)" color="blue" />
              )}
              <InfoRow label="Entry price" value={formatCurrency(entry)} />
              <InfoRow label="Exit price" value={exitPrice ? formatCurrency(exitPrice) : '—'} />
              <InfoRow
                label="Exit reason"
                value={<span style={{ color: exitLabel?.includes('TP') ? '#10b981' : exitLabel?.includes('Breakeven') ? '#60a5fa' : exitLabel?.includes('Stopped') ? '#ef4444' : '#94a3b8' }}>
                  {exitLabel || '—'}
                </span>}
                mono={false}
              />
              <InfoRow label="Confidence" value={`${Number(trade.confidence || 0).toFixed(0)}%`} />
            </div>
          </CardContent>
        </Card>

        {/* TP targets */}
        <Card>
          <CardHeader>
            <CardTitle icon={Target} iconColor="#10b981">Target Scenarios</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {tp1 && (
                <>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', paddingTop: 4 }}>TP1</div>
                  <InfoRow label="Price" value={formatCurrency(tp1)} />
                  {pnlAtTp1 && <InfoRow label="P&L at TP1" value={`+$${pnlAtTp1.toFixed(2)}`} />}
                  {roeAtTp1 && <InfoRow label="ROE at TP1" value={`+${roeAtTp1}%`} />}
                  {rrTp1 && <InfoRow label="R:R" value={`${rrTp1}:1`} />}
                </>
              )}
              {tp2 && (
                <>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.07em', paddingTop: 8 }}>TP2</div>
                  <InfoRow label="Price" value={formatCurrency(tp2)} />
                  {pnlAtTp2 && <InfoRow label="P&L at TP2" value={`+$${pnlAtTp2.toFixed(2)}`} />}
                  {roeAtTp2 && <InfoRow label="ROE at TP2" value={`+${roeAtTp2}%`} />}
                  {rrTp2 && <InfoRow label="R:R" value={`${rrTp2}:1`} />}
                </>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Position sizing */}
        <Card>
          <CardHeader>
            <CardTitle icon={Zap} iconColor="#f59e0b">Position Sizing</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
              <InfoRow label="Leverage" value={`${leverage.toFixed(0)}×`} />
              <InfoRow label="Notional" value={formatCurrency(notional)} />
              <InfoRow label="Margin used" value={formatCurrency(margin)} />
              <InfoRow label="Quantity" value={`${qty.toFixed(4)} ${trade.instrument}`} />
              <InfoRow label="Risk (USD)" value={`$${riskUsd.toFixed(2)}`} />
              {liq && <InfoRow label="Liquidation" value={<span style={{ color: '#ef4444' }}>{formatCurrency(liq)}</span>} />}
              {liqDistPct && <InfoRow label="Liq. distance" value={`${liqDistPct}% from entry`} />}
              <InfoRow label="SL" value={<span style={{ color: '#ef4444' }}>{formatCurrency(sl)}</span>} />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── Trade Events Timeline ── */}
      {events.length > 0 && (
        <Card style={{ marginTop: 16 }}>
          <CardHeader>
            <CardTitle style={{ fontSize: 14, color: '#94a3b8' }}>Trade Events</CardTitle>
          </CardHeader>
          <CardContent>
            {events.map(evt => (
              <EventChip
                key={evt.id}
                eventType={evt.event_type}
                price={evt.price}
                pnlPartial={evt.pnl_partial}
                timestamp={evt.timestamp}
              />
            ))}
          </CardContent>
        </Card>
      )}

      {/* ── Backtest Fibonacci analysis ── */}
      {isBacktest && (trade.swing_high || trade.analysis_notes) && (
        <Card style={{ borderColor: 'rgba(251,191,36,0.15)' }}>
          <CardHeader>
            <CardTitle icon={TrendingUp} iconColor="#fbbf24">Backtest Analysis</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 48px' }}>
              <InfoRow label="Swing High" value={trade.swing_high ? formatCurrency(trade.swing_high) : '—'} />
              <InfoRow label="Swing Low"  value={trade.swing_low  ? formatCurrency(trade.swing_low)  : '—'} />
              <InfoRow label="Zone Upper (50%)"   value={trade.fib_zone_upper ? formatCurrency(trade.fib_zone_upper) : '—'} />
              <InfoRow label="Zone Lower (61.8%)" value={trade.fib_zone_lower ? formatCurrency(trade.fib_zone_lower) : '—'} />
              <InfoRow label="Fib level hit" value={trade.fib_level_triggered ? `${(trade.fib_level_triggered * 100).toFixed(1)}%` : '—'} />
              <InfoRow label="R:R at TP2" value={trade.rr_tp2 ? `${Number(trade.rr_tp2).toFixed(2)}:1` : rrTp2 ? `${rrTp2}:1` : '—'} />
            </div>
            {trade.analysis_notes && (
              <div style={{
                marginTop: 16, padding: '14px 16px', borderRadius: 10,
                background: 'rgba(251,191,36,0.05)', border: '1px solid rgba(251,191,36,0.1)',
                fontSize: 13, color: '#94a3b8', lineHeight: 1.65,
              }}>
                {trade.analysis_notes}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
