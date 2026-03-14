import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';

interface Props {
  distribution: Record<string, number>;
}

const COLORS: Record<string, string> = {
  bullish: '#22c55e',
  bearish: '#ef4444',
  neutral: '#64748b',
};

const LABELS: Record<string, string> = {
  bullish: '看多',
  bearish: '看空',
  neutral: '中性',
};

export default function SentimentChart({ distribution }: Props) {
  const data = Object.entries(distribution).map(([key, value]) => ({
    name: LABELS[key] || key,
    value,
    color: COLORS[key] || '#64748b',
  }));

  if (data.length === 0) {
    return <div className="text-slate-500 text-sm text-center py-4">暂无数据</div>;
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
          label={({ name, value }) => `${name} ${value}`}
        >
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.color} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
        />
      </PieChart>
    </ResponsiveContainer>
  );
}
