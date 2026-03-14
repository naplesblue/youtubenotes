import { useState, useEffect } from 'react';
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import type { Ticker } from '../lib/types';
import { directionLabels, typeLabels } from '../lib/colors';

function useThemeColors() {
  const defaults = {
    bg: '#111318', surface: '#191c24', border: '#262a36',
    text: '#e2e4e9', textSecondary: '#8b8fa3', textMuted: '#565a6e',
    accent: '#6b9fff', green: '#4ade80', red: '#f87171',
  };
  const get = () => {
    if (typeof window === 'undefined') return defaults;
    const s = getComputedStyle(document.documentElement);
    return {
      bg: s.getPropertyValue('--color-bg').trim() || defaults.bg,
      surface: s.getPropertyValue('--color-surface').trim() || defaults.surface,
      border: s.getPropertyValue('--color-border').trim() || defaults.border,
      text: s.getPropertyValue('--color-text').trim() || defaults.text,
      textSecondary: s.getPropertyValue('--color-text-secondary').trim() || defaults.textSecondary,
      textMuted: s.getPropertyValue('--color-text-muted').trim() || defaults.textMuted,
      accent: s.getPropertyValue('--color-accent').trim() || defaults.accent,
      green: s.getPropertyValue('--color-green').trim() || defaults.green,
      red: s.getPropertyValue('--color-red').trim() || defaults.red,
    };
  };
  const [colors, setColors] = useState(get);
  useEffect(() => {
    setColors(get());
    const obs = new MutationObserver(() => setColors(get()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);
  return colors;
}

interface Props {
  ticker: Ticker;
}

type MarkerPoint = {
  ts: number;
  date: string;
  sourceDate: string;
  markerPrice: number;
  sourcePrice: number | null;
  analyst: string;
  sentiment: string;
  type: string;
  direction: string;
  targetPrice: number | null;
  opinionId?: string;
  videoId?: string;
};

type TooltipMarkerGroup = {
  key: string;
  count: number;
  marker: MarkerPoint;
};

function _formatPrice(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "-";
  if (Math.abs(v) >= 1_000_000_000) return Number(v).toExponential(2);
  return Number(v).toFixed(2);
}

function CustomTooltip({ active, label, payload, groupedByDate }: any) {
  if (!active || !payload || payload.length === 0) return null;

  const closeItem = payload.find((p: any) => p?.dataKey === 'close');
  const close = closeItem != null ? Number(closeItem.value) : NaN;
  const ts = Number(label);
  const date = Number.isFinite(ts)
    ? new Date(ts).toISOString().slice(0, 10)
    : String(label || '');
  const groups: TooltipMarkerGroup[] = groupedByDate?.get(date) || [];
  const visibleGroups = groups.slice(0, 8);
  const dayCount = groups.reduce((acc, g) => acc + g.count, 0);

  return (
    <div style={{
      background: 'var(--color-surface, #191c24)',
      border: '1px solid var(--color-border, #262a36)',
      borderRadius: 6,
      fontSize: 11,
      fontFamily: "'JetBrains Mono', monospace",
      padding: '8px 10px',
      boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
    }}>
      <div style={{ color: 'var(--color-text-muted, #565a6e)', marginBottom: 4 }}>{date}</div>
      {Number.isFinite(close) && (
        <div style={{ color: 'var(--color-accent, #6b9fff)' }}>收盘: ${close.toFixed(2)}</div>
      )}
      {dayCount > 0 && (
        <div style={{ color: 'var(--color-text-secondary, #8b8fa3)', marginTop: 4 }}>观点: {dayCount}</div>
      )}
      {visibleGroups.map((g, idx) => {
        const m = g.marker;
        const dotColor = m.sentiment?.includes('bullish')
          ? 'var(--color-green, #4ade80)'
          : m.sentiment?.includes('bearish')
          ? 'var(--color-red, #f87171)'
          : 'var(--color-text-muted, #565a6e)';
        const dir = directionLabels[m.direction] || m.direction || '';
        const typ = typeLabels[m.type] || m.type || '';
        const displayPrice = m.sourcePrice != null && Number.isFinite(m.sourcePrice)
          ? m.sourcePrice
          : m.markerPrice;
        return (
          <div key={`${g.key}-${idx}`} style={{ color: 'var(--color-text, #e2e4e9)', marginTop: 4 }}>
            <span style={{ color: dotColor, marginRight: 6 }}>●</span>
            {m.analyst} · {dir} · {typ} · ${_formatPrice(displayPrice)}
            {m.targetPrice != null && Number.isFinite(Number(m.targetPrice)) ? ` → ${_formatPrice(Number(m.targetPrice))}` : ''}
            {g.count > 1 ? ` ×${g.count}` : ''}
          </div>
        );
      })}
      {groups.length > visibleGroups.length && (
        <div style={{ color: 'var(--color-text-muted, #565a6e)', marginTop: 4 }}>
          +{groups.length - visibleGroups.length} 更多
        </div>
      )}
    </div>
  );
}

export default function PriceChart({ ticker }: Props) {
  const c = useThemeColors();

  if (!ticker.price_data || ticker.price_data.length === 0) {
    return <div style={{ color: 'var(--color-text-muted, #565a6e)', fontSize: 13, padding: '32px 0', textAlign: 'center' }}>暂无行情数据</div>;
  }

  const chartData = ticker.price_data
    .map(p => ({
      ts: new Date(`${p.date}T00:00:00Z`).getTime(),
      date: p.date,
      close: Number(p.close),
    }))
    .filter(d => Number.isFinite(d.close) && Number.isFinite(d.ts))
    .sort((a, b) => a.ts - b.ts);

  if (chartData.length === 0) {
    return <div style={{ color: 'var(--color-text-muted, #565a6e)', fontSize: 13, padding: '32px 0', textAlign: 'center' }}>暂无有效行情数据</div>;
  }

  const tradingDates = chartData.map(p => p.date);
  const priceByDate = new Map(chartData.map(p => [p.date, p.close]));
  const tsByDate = new Map(chartData.map(p => [p.date, p.ts]));

  const nearestTradingDate = (d: string): string | null => {
    if (!d || tradingDates.length === 0) return null;
    if (priceByDate.has(d)) return d;
    for (let i = tradingDates.length - 1; i >= 0; i--) {
      if (tradingDates[i] <= d) return tradingDates[i];
    }
    return tradingDates[0];
  };

  const scatterData = ticker.opinion_markers
    .map(m => {
      const sourceDate = m.date;
      const date = nearestTradingDate(sourceDate);
      if (!date) return null;
      const rawPrice = priceByDate.get(date)
        ?? (m.price_at_publish != null ? Number(m.price_at_publish) : null)
        ?? (m.price != null ? Number(m.price) : null);
      if (rawPrice == null || !Number.isFinite(rawPrice)) return null;
      const sourcePrice = m.price != null && Number.isFinite(Number(m.price))
        ? Number(m.price)
        : (m.price_at_publish != null && Number.isFinite(Number(m.price_at_publish))
          ? Number(m.price_at_publish) : null);
      const ts = tsByDate.get(date);
      if (ts == null || !Number.isFinite(ts)) return null;
      return { ts, date, sourceDate, markerPrice: rawPrice, sourcePrice,
        analyst: m.analyst, sentiment: m.sentiment, type: m.type,
        direction: m.direction, targetPrice: m.target_price,
        opinionId: m.opinion_id, videoId: m.video_id,
      };
    })
    .filter((d): d is MarkerPoint => d !== null)
    .sort((a, b) => a.ts - b.ts);

  const groupedByDate = scatterData.reduce((acc, m) => {
    const groups = acc.get(m.date) || new Map<string, TooltipMarkerGroup>();
    const key = [m.analyst, m.direction, m.type, m.sourceDate,
      m.sourcePrice == null ? 'na' : _formatPrice(m.sourcePrice),
      m.targetPrice == null ? 'na' : _formatPrice(m.targetPrice),
    ].join('|');
    const existing = groups.get(key);
    if (existing) { existing.count += 1; }
    else { groups.set(key, { key, count: 1, marker: m }); }
    acc.set(m.date, groups);
    return acc;
  }, new Map<string, Map<string, TooltipMarkerGroup>>());

  const groupedByDateList = new Map<string, TooltipMarkerGroup[]>();
  for (const [d, mp] of groupedByDate.entries()) {
    groupedByDateList.set(d, [...mp.values()]);
  }

  const xTicks = (() => {
    if (chartData.length <= 1) return chartData.map(d => d.ts);
    const step = Math.max(1, Math.floor((chartData.length - 1) / 9));
    const ticks: number[] = [];
    for (let i = 0; i < chartData.length; i += step) ticks.push(chartData[i].ts);
    const last = chartData[chartData.length - 1].ts;
    if (ticks[ticks.length - 1] !== last) ticks.push(last);
    return ticks;
  })();

  const allPrices = chartData.map(d => d.close).filter(v => Number.isFinite(v));
  const minPrice = Math.min(...allPrices) * 0.95;
  const maxPrice = Math.max(...allPrices) * 1.05;

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={c.border} strokeOpacity={0.5} />
        <XAxis dataKey="ts" type="number" scale="time"
          domain={[xTicks[0], xTicks[xTicks.length - 1]]} ticks={xTicks}
          tick={{ fill: c.textMuted, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}
          tickLine={false} axisLine={{ stroke: c.border }} minTickGap={24}
          tickFormatter={(v: number) => Number.isFinite(v) ? new Date(v).toISOString().slice(5, 10) : String(v)}
        />
        <YAxis domain={[minPrice, maxPrice]}
          tick={{ fill: c.textMuted, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}
          tickLine={false} axisLine={false}
          tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          width={48}
        />
        <Tooltip content={<CustomTooltip groupedByDate={groupedByDateList} />} />
        <Line type="monotone" dataKey="close" stroke={c.accent} strokeWidth={1.5}
          dot={false} connectNulls isAnimationActive={false}
          activeDot={{ r: 3, fill: c.accent, stroke: c.bg, strokeWidth: 2 }}
        />
        {scatterData.length > 0 && (
          <Scatter data={scatterData} dataKey="markerPrice" fill={c.textMuted}
            isAnimationActive={false}
            shape={(props: any) => {
              const { cx, cy, payload } = props;
              const color = payload.sentiment?.includes('bullish') ? c.green
                : payload.sentiment?.includes('bearish') ? c.red : c.textMuted;
              return <circle cx={cx} cy={cy} r={4} fill={color} stroke={c.bg} strokeWidth={1.5} opacity={0.9} />;
            }}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
