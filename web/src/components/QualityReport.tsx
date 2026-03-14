import type { QualityIssue } from '../lib/types';

interface Props {
  issues: QualityIssue[];
}

function SeverityBadge({ severity }: { severity: string }) {
  const cls = severity === 'error' ? 'bg-red-500/20 text-red-400'
    : severity === 'warning' ? 'bg-amber-500/20 text-amber-400'
    : 'bg-blue-500/20 text-blue-400';
  return <span className={`text-xs px-2 py-0.5 rounded font-medium ${cls}`}>{severity}</span>;
}

export default function QualityReport({ issues }: Props) {
  if (issues.length === 0) {
    return <div className="text-green-400 text-sm py-4">所有数据质量检查通过</div>;
  }

  return (
    <div className="space-y-4">
      {issues.map((issue, i) => (
        <div key={i} className="rounded-xl border border-slate-700/50 p-5" style={{ background: '#1e293b' }}>
          <div className="flex items-center gap-3 mb-3">
            <SeverityBadge severity={issue.severity} />
            <h3 className="font-semibold">{issue.title}</h3>
            {issue.count != null && issue.total != null && (
              <span className="text-sm text-slate-400 ml-auto">
                {issue.count}/{issue.total}
                {issue.percentage != null && ` (${issue.percentage}%)`}
              </span>
            )}
          </div>

          {/* missing_price_at_publish */}
          {issue.type === 'missing_price_at_publish' && issue.affected && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    <th className="text-left py-1 px-2">Ticker</th>
                    <th className="text-left py-1 px-2">分析师</th>
                    <th className="text-left py-1 px-2">日期</th>
                  </tr>
                </thead>
                <tbody>
                  {issue.affected.slice(0, 15).map((a, j) => (
                    <tr key={j} className="border-b border-slate-800/50">
                      <td className="py-1 px-2 font-mono">{a.ticker}</td>
                      <td className="py-1 px-2 text-slate-400">{a.analyst}</td>
                      <td className="py-1 px-2 text-slate-500">{a.date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {issue.affected.length > 15 && (
                <div className="text-xs text-slate-500 mt-2">...还有 {issue.affected.length - 15} 条</div>
              )}
            </div>
          )}

          {/* ticker_alias_conflict */}
          {issue.type === 'ticker_alias_conflict' && issue.conflicts && (
            <div className="space-y-2">
              {issue.conflicts.map((c, j) => (
                <div key={j} className="flex items-center gap-2 text-sm">
                  {c.tickers.map(t => (
                    <span key={t} className="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 font-mono text-xs">{t}</span>
                  ))}
                  <span className="text-slate-500">→ {c.description}</span>
                </div>
              ))}
            </div>
          )}

          {/* analyst_alias */}
          {issue.type === 'analyst_alias' && issue.aliases && (
            <div className="space-y-2">
              {issue.aliases.map((a, j) => (
                <div key={j} className="flex items-center gap-2 text-sm">
                  {a.names.map(n => (
                    <span key={n} className="px-2 py-0.5 rounded bg-blue-500/15 text-blue-400 text-xs">{n}</span>
                  ))}
                  <span className="text-slate-500">← {a.channel}</span>
                </div>
              ))}
            </div>
          )}

          {/* nonstandard_sentiment */}
          {issue.type === 'nonstandard_sentiment' && issue.values && (
            <div className="flex gap-2 flex-wrap">
              {Object.entries(issue.values).map(([val, count]) => (
                <span key={val} className="px-2 py-1 rounded bg-slate-700 text-sm">
                  <span className="text-amber-400 font-mono">{val}</span>
                  <span className="text-slate-500 ml-1">×{count}</span>
                </span>
              ))}
            </div>
          )}

          {/* channels_without_opinions */}
          {issue.type === 'channels_without_opinions' && issue.channels && (
            <div>
              <div className="text-sm text-slate-400 mb-2">
                {issue.with_opinions}/{issue.total_channels} 个频道有观点提取
              </div>
              <div className="flex gap-2 flex-wrap">
                {issue.channels.map(ch => (
                  <span key={ch} className="px-2 py-0.5 rounded bg-slate-700 text-xs text-slate-300">{ch}</span>
                ))}
              </div>
            </div>
          )}

          {/* missing_prediction_price */}
          {issue.type === 'missing_prediction_price' && (
            <div className="text-sm text-slate-400">
              {issue.count} 条观点缺少预测价位（不含 direction_call 和 reference_only）
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
