import {
  ComposedChart, Line, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import type { Ticker } from '../lib/types';

interface Props {
  ticker: Ticker;
}

export default function PriceChart({ ticker }: Props) {
  if (!ticker.price_data || ticker.price_data.length === 0) {
    return <div className="text-slate-500 text-sm py-8 text-center">暂无行情数据</div>;
  }

  const chartData = ticker.price_data
    .map(p => ({
      date: p.date,
      close: Number(p.close),
    }))
    .filter(d => Number.isFinite(d.close))
    .sort((a, b) => a.date.localeCompare(b.date));

  if (chartData.length === 0) {
    return <div className="text-slate-500 text-sm py-8 text-center">暂无有效行情数据</div>;
  }

  // Scatter data for opinion markers — map to nearest trading day
  const tradingDates = chartData.map(p => p.date);
  const priceByDate = new Map(chartData.map(p => [p.date, p.close]));

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
      const date = nearestTradingDate(m.date);
      if (!date) return null;
      const rawPrice = priceByDate.get(date)
        ?? (m.price_at_publish != null ? Number(m.price_at_publish) : null)
        ?? (m.price != null ? Number(m.price) : null);
      if (rawPrice == null || !Number.isFinite(rawPrice)) return null;
      return {
        date,
        markerPrice: rawPrice,
        analyst: m.analyst,
        sentiment: m.sentiment,
        type: m.type,
        direction: m.direction,
        targetPrice: m.target_price,
      };
    })
    .filter((d): d is {
      date: string;
      markerPrice: number;
      analyst: string;
      sentiment: string;
      type: string;
      direction: string;
      targetPrice: number | null;
    } => d !== null)
    .sort((a, b) => a.date.localeCompare(b.date));

  const allPrices = chartData.map(d => d.close).filter(v => Number.isFinite(v));
  const minPrice = Math.min(...allPrices) * 0.95;
  const maxPrice = Math.max(...allPrices) * 1.05;

  return (
    <ResponsiveContainer width="100%" height={350}>
      <ComposedChart data={chartData} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis
          dataKey="date"
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          interval="preserveStartEnd"
          tickFormatter={(v: string) => (typeof v === 'string' && v.length >= 10 ? v.slice(5) : String(v))}
        />
        <YAxis
          domain={[minPrice, maxPrice]}
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          tickFormatter={(v: number) => `$${v.toFixed(0)}`}
        />
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
          labelStyle={{ color: '#94a3b8' }}
          labelFormatter={(label: string) => (typeof label === 'string' ? label : String(label))}
          formatter={(value: number | string) => {
            const n = Number(value);
            return Number.isFinite(n) ? [`$${n.toFixed(2)}`, '收盘价'] : [String(value), '收盘价'];
          }}
        />
        <Line
          type="monotone"
          dataKey="close"
          stroke="#3b82f6"
          strokeWidth={2}
          dot={false}
          connectNulls
          isAnimationActive={false}
          activeDot={{ r: 4, fill: '#3b82f6' }}
        />
        {scatterData.length > 0 && (
          <Scatter
            data={scatterData}
            dataKey="markerPrice"
            fill="#f59e0b"
            isAnimationActive={false}
            shape={(props: any) => {
              const { cx, cy, payload } = props;
              const color = payload.sentiment?.includes('bullish') ? '#22c55e'
                : payload.sentiment?.includes('bearish') ? '#ef4444' : '#64748b';
              return (
                <circle cx={cx} cy={cy} r={6} fill={color} stroke="#0f172a" strokeWidth={2} opacity={0.9} />
              );
            }}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
