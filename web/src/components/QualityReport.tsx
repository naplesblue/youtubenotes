import type { QualityIssue } from '../lib/types';

interface Props {
  issues: QualityIssue[];
}

function SeverityBadge({ severity }: { severity: string }) {
  const style = severity === 'error'
    ? { background: 'var(--color-red-dim)', color: 'var(--color-red)' }
    : severity === 'warning'
    ? { background: 'var(--color-amber-dim)', color: 'var(--color-amber)' }
    : { background: 'rgba(107,159,255,0.1)', color: 'var(--color-accent)' };
  return <span style={{ ...style, fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 500 }}>{severity}</span>;
}

export default function QualityReport({ issues }: Props) {
  if (issues.length === 0) {
    return <div style={{ color: 'var(--color-green)', fontSize: 13, padding: '16px 0' }}>所有数据质量检查通过</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {issues.map((issue, i) => (
        <div key={i} className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <SeverityBadge severity={issue.severity} />
            <h3 style={{ fontWeight: 600, fontSize: 14, color: 'var(--color-text)' }}>{issue.title}</h3>
            {issue.count != null && issue.total != null && (
              <span className="font-data" style={{ fontSize: 12, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
                {issue.count}/{issue.total}
                {issue.percentage != null && ` (${issue.percentage}%)`}
              </span>
            )}
          </div>

          {/* missing_price_at_publish */}
          {issue.type === 'missing_price_at_publish' && issue.affected && (
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>分析师</th>
                    <th>日期</th>
                  </tr>
                </thead>
                <tbody>
                  {issue.affected.slice(0, 15).map((a, j) => (
                    <tr key={j}>
                      <td><span className="font-data">{a.ticker}</span></td>
                      <td style={{ color: 'var(--color-text-secondary)' }}>{a.analyst}</td>
                      <td style={{ color: 'var(--color-text-muted)' }}>{a.date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {issue.affected.length > 15 && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 8 }}>...还有 {issue.affected.length - 15} 条</div>
              )}
            </div>
          )}

          {/* ticker_alias_conflict */}
          {issue.type === 'ticker_alias_conflict' && issue.conflicts && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {issue.conflicts.map((c, j) => (
                <div key={j} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  {c.tickers.map(t => (
                    <span key={t} className="font-data" style={{ padding: '2px 8px', borderRadius: 4, background: 'var(--color-amber-dim)', color: 'var(--color-amber)', fontSize: 11 }}>{t}</span>
                  ))}
                  <span style={{ color: 'var(--color-text-muted)' }}>→ {c.description}</span>
                </div>
              ))}
            </div>
          )}

          {/* analyst_alias */}
          {issue.type === 'analyst_alias' && issue.aliases && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {issue.aliases.map((a, j) => (
                <div key={j} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  {a.names.map(n => (
                    <span key={n} style={{ padding: '2px 8px', borderRadius: 4, background: 'rgba(107,159,255,0.1)', color: 'var(--color-accent)', fontSize: 11 }}>{n}</span>
                  ))}
                  <span style={{ color: 'var(--color-text-muted)' }}>← {a.channel}</span>
                </div>
              ))}
            </div>
          )}

          {/* nonstandard_sentiment */}
          {issue.type === 'nonstandard_sentiment' && issue.values && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {Object.entries(issue.values).map(([val, count]) => (
                <span key={val} style={{ padding: '4px 10px', borderRadius: 4, background: 'var(--color-surface-hover)', fontSize: 12 }}>
                  <span className="font-data" style={{ color: 'var(--color-amber)' }}>{val}</span>
                  <span style={{ color: 'var(--color-text-muted)', marginLeft: 4 }}>×{count}</span>
                </span>
              ))}
            </div>
          )}

          {/* channels_without_opinions */}
          {issue.type === 'channels_without_opinions' && issue.channels && (
            <div>
              <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', marginBottom: 8 }}>
                {issue.with_opinions}/{issue.total_channels} 个频道有观点提取
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {issue.channels.map(ch => (
                  <span key={ch} className="pill">{ch}</span>
                ))}
              </div>
            </div>
          )}

          {/* missing_prediction_price */}
          {issue.type === 'missing_prediction_price' && (
            <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
              {issue.count} 条观点缺少预测价位（不含 direction_call 和 reference_only）
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
