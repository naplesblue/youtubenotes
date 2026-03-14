import { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';

interface Props {
  distribution: Record<string, number>;
}

const LABELS: Record<string, string> = {
  bullish: '看多',
  bearish: '看空',
  neutral: '中性',
};

function useThemeColors() {
  const defaults = {
    bg: '#111318', surface: '#191c24', border: '#262a36',
    textMuted: '#565a6e', green: '#4ade80', red: '#f87171',
  };
  const get = () => {
    if (typeof window === 'undefined') return defaults;
    const s = getComputedStyle(document.documentElement);
    return {
      bg: s.getPropertyValue('--color-bg').trim() || defaults.bg,
      surface: s.getPropertyValue('--color-surface').trim() || defaults.surface,
      border: s.getPropertyValue('--color-border').trim() || defaults.border,
      textMuted: s.getPropertyValue('--color-text-muted').trim() || defaults.textMuted,
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

export default function SentimentChart({ distribution }: Props) {
  const c = useThemeColors();

  const colorMap: Record<string, string> = {
    bullish: c.green,
    bearish: c.red,
    neutral: c.textMuted,
  };

  const data = Object.entries(distribution).map(([key, value]) => ({
    name: LABELS[key] || key,
    value,
    color: colorMap[key] || c.textMuted,
  }));

  if (data.length === 0) {
    return <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: '16px 0' }}>暂无数据</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={50}
          outerRadius={80}
          paddingAngle={3}
          dataKey="value"
          stroke={c.bg}
          strokeWidth={2}
          label={({ name, value }) => `${name} ${value}`}
        >
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            background: c.surface,
            border: `1px solid ${c.border}`,
            borderRadius: 6,
            fontSize: 12,
            fontFamily: "'JetBrains Mono', monospace",
          }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
