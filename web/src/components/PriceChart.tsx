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

  // Merge price data with opinion markers
  const markersByDate = new Map<string, typeof ticker.opinion_markers>();
  for (const m of ticker.opinion_markers) {
    if (!markersByDate.has(m.date)) markersByDate.set(m.date, []);
    markersByDate.get(m.date)!.push(m);
  }

  const chartData = ticker.price_data.map(p => ({
    date: p.date,
    close: p.close,
    label: p.date.slice(5),
  }));

  // Scatter data for opinion markers — map to nearest trading day
  const tradingDates = new Set(ticker.price_data.map(p => p.date));
  const priceByDate = new Map(ticker.price_data.map(p => [p.date, p.close]));

  const scatterData = ticker.opinion_markers
    .filter(m => {
      // Find nearest trading day
      return priceByDate.has(m.date) || tradingDates.size > 0;
    })
    .map(m => {
      const price = priceByDate.get(m.date) ?? m.price_at_publish ?? m.price;
      if (price == null) return null;
      return {
        date: m.date,
        markerPrice: price,
        label: m.date.slice(5),
        analyst: m.analyst,
        sentiment: m.sentiment,
        type: m.type,
        direction: m.direction,
        targetPrice: m.target_price,
      };
    })
    .filter(Boolean);

  const allPrices = chartData.map(d => d.close).filter(Boolean);
  const minPrice = Math.min(...allPrices) * 0.95;
  const maxPrice = Math.max(...allPrices) * 1.05;

  return (
    <ResponsiveContainer width="100%" height={350}>
      <ComposedChart data={chartData} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
        <XAxis
          dataKey="label"
          tick={{ fill: '#64748b', fontSize: 11 }}
          tickLine={false}
          interval="preserveStartEnd"
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
          formatter={(value: number) => [`$${value.toFixed(2)}`, '收盘价']}
        />
        <Line
          type="monotone"
          dataKey="close"
          stroke="#3b82f6"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: '#3b82f6' }}
        />
        {scatterData.length > 0 && (
          <Scatter
            data={scatterData}
            dataKey="markerPrice"
            fill="#f59e0b"
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
