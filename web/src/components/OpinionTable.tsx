import { useState, useMemo } from 'react';
import type { Opinion } from '../lib/types';
import { typeLabels, directionLabels, confidenceLabels } from '../lib/colors';
import { toAnalystSlug } from '../lib/analystSlug';

interface Props {
  opinions: Opinion[];
  showChannel?: boolean;
}

function SentimentBadge({ sentiment }: { sentiment: string }) {
  const cls = sentiment.includes('bullish') ? 'badge-green'
    : sentiment.includes('bearish') ? 'badge-red' : 'badge-muted';
  const label = sentiment.includes('bullish') ? '看多' : sentiment.includes('bearish') ? '看空' : '中性';
  return <span className={`badge ${cls}`}>{label}</span>;
}

type AnalystGroup = {
  analyst: string;
  bloggerSlug: string;
  channel?: string;
  opinions: Opinion[];
  latestSentiment: string;
  latestType: string;
  latestPrice: number | null;
  latestTarget: number | null;
  latestDate: string;
};

export default function OpinionTable({ opinions, showChannel = false }: Props) {
  const [globalFilter, setGlobalFilter] = useState('');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [viewMode, setViewMode] = useState<'grouped' | 'flat'>('grouped');
  const [sortBy, setSortBy] = useState<'date' | 'count'>('date');

  const filtered = useMemo(() => {
    if (!globalFilter) return opinions;
    const q = globalFilter.toLowerCase();
    return opinions.filter(o =>
      o.analyst.toLowerCase().includes(q) ||
      o.ticker.toLowerCase().includes(q) ||
      o.sentiment.toLowerCase().includes(q) ||
      (o.prediction.type && o.prediction.type.toLowerCase().includes(q))
    );
  }, [opinions, globalFilter]);

  const groups = useMemo<AnalystGroup[]>(() => {
    const map = new Map<string, Opinion[]>();
    for (const o of filtered) {
      const key = o.analyst;
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(o);
    }
    const result: AnalystGroup[] = [];
    for (const [analyst, ops] of map.entries()) {
      const sorted = [...ops].sort((a, b) => b.published_date.localeCompare(a.published_date));
      const latest = sorted[0];
      result.push({
        analyst,
        bloggerSlug: latest.blogger_slug || toAnalystSlug(analyst),
        channel: latest.channel,
        opinions: sorted,
        latestSentiment: latest.sentiment,
        latestType: latest.prediction.type,
        latestPrice: latest.prediction.price,
        latestTarget: latest.prediction.target_price,
        latestDate: latest.published_date,
      });
    }
    if (sortBy === 'date') {
      result.sort((a, b) => b.latestDate.localeCompare(a.latestDate));
    } else {
      result.sort((a, b) => b.opinions.length - a.opinions.length);
    }
    return result;
  }, [filtered, sortBy]);

  const toggle = (analyst: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(analyst)) next.delete(analyst);
      else next.add(analyst);
      return next;
    });
  };

  const btnStyle = (active: boolean) => ({
    padding: '3px 10px', borderRadius: 4, fontSize: 11, fontWeight: 500,
    cursor: 'pointer' as const, border: 'none',
    background: active ? 'rgba(107,159,255,0.12)' : 'transparent',
    color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
    transition: 'all 0.15s',
  });

  return (
    <div>
      {/* Controls */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        {opinions.length > 10 && (
          <input type="text" placeholder="筛选..." value={globalFilter}
            onChange={e => setGlobalFilter(e.target.value)}
            style={{
              padding: '5px 12px', borderRadius: 6, fontSize: 12,
              border: '1px solid var(--color-border)', background: 'var(--color-surface)',
              color: 'var(--color-text)', outline: 'none', width: 160,
            }}
          />
        )}
        <div style={{ display: 'flex', gap: 2 }}>
          <button onClick={() => setViewMode('grouped')} style={btnStyle(viewMode === 'grouped')}>按分析师</button>
          <button onClick={() => setViewMode('flat')} style={btnStyle(viewMode === 'flat')}>平铺</button>
        </div>
        {viewMode === 'grouped' && (
          <div style={{ display: 'flex', gap: 2 }}>
            <button onClick={() => setSortBy('date')} style={btnStyle(sortBy === 'date')}>按时间</button>
            <button onClick={() => setSortBy('count')} style={btnStyle(sortBy === 'count')}>按数量</button>
          </div>
        )}
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
          {filtered.length} 条观点
        </span>
      </div>

      {viewMode === 'grouped' ? (
        <GroupedView groups={groups} expanded={expanded} toggle={toggle} showChannel={showChannel} />
      ) : (
        <FlatView opinions={filtered} showChannel={showChannel} />
      )}
    </div>
  );
}

function GroupedView({ groups, expanded, toggle, showChannel }: {
  groups: AnalystGroup[]; expanded: Set<string>; toggle: (a: string) => void; showChannel: boolean;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {groups.map(g => {
        const isOpen = expanded.has(g.analyst);
        return (
          <div key={g.analyst} style={{ borderBottom: '1px solid var(--color-border)' }}>
            {/* Group header */}
            <div onClick={() => toggle(g.analyst)}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
                cursor: 'pointer', transition: 'background 0.15s',
              }}
              onMouseEnter={e => (e.currentTarget.style.background = 'var(--color-surface-hover)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <span style={{
                fontSize: 10, color: 'var(--color-text-muted)', width: 16, textAlign: 'center',
                transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s',
              }}>▶</span>

              {showChannel ? (
                <a href={`/bloggers/${g.bloggerSlug}/`} className="data-link"
                   style={{ fontSize: 13, fontWeight: 500, width: 120, flexShrink: 0 }}
                   onClick={e => e.stopPropagation()}>
                  {g.analyst}
                </a>
              ) : (
                <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--color-text)', width: 120, flexShrink: 0 }}>
                  {g.analyst}
                </span>
              )}

              <SentimentBadge sentiment={g.latestSentiment} />

              <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                {typeLabels[g.latestType] || g.latestType}
              </span>

              {g.latestPrice != null && (
                <span className="font-data" style={{ fontSize: 12 }}>${g.latestPrice.toFixed(1)}</span>
              )}
              {g.latestTarget != null && (
                <span className="font-data" style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                  → ${g.latestTarget.toFixed(1)}
                </span>
              )}

              <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span className="font-data" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                  {g.opinions.length > 1 ? `${g.opinions.length} 条` : ''}
                </span>
                <span className="font-data" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                  {g.latestDate.slice(5)}
                </span>
              </span>
            </div>

            {/* Expanded opinions */}
            {isOpen && (
              <div style={{ paddingLeft: 28, paddingBottom: 8 }}>
                {g.opinions.map((o, i) => (
                  <div key={o.opinion_id || i}
                    style={{
                      display: 'flex', alignItems: 'flex-start', gap: 12, padding: '8px 0',
                      borderTop: i > 0 ? '1px solid var(--color-border)' : 'none',
                      fontSize: 12,
                    }}>
                    <span className="font-data" style={{ fontSize: 11, color: 'var(--color-text-muted)', width: 48, flexShrink: 0 }}>
                      {o.published_date.slice(5)}
                    </span>
                    <SentimentBadge sentiment={o.sentiment} />
                    <span style={{ color: 'var(--color-text-secondary)' }}>
                      {typeLabels[o.prediction.type] || o.prediction.type}
                    </span>
                    <span style={{ color: 'var(--color-text-secondary)' }}>
                      {directionLabels[o.prediction.direction] || o.prediction.direction}
                    </span>
                    {o.prediction.price != null && (
                      <span className="font-data">${o.prediction.price.toFixed(1)}</span>
                    )}
                    {o.prediction.target_price != null && (
                      <span className="font-data" style={{ color: 'var(--color-text-muted)' }}>
                        → ${o.prediction.target_price.toFixed(1)}
                      </span>
                    )}
                    {o.prediction.stop_loss != null && (
                      <span className="font-data" style={{ color: 'var(--color-red)', fontSize: 11 }}>
                        止损 ${o.prediction.stop_loss.toFixed(1)}
                      </span>
                    )}
                    {o.prediction.confidence && (
                      <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        {confidenceLabels[o.prediction.confidence] || o.prediction.confidence}
                      </span>
                    )}
                  </div>
                ))}
                {g.opinions.some(o => o.prediction.context) && (
                  <div style={{ marginTop: 4, padding: '8px 0', borderTop: '1px solid var(--color-border)' }}>
                    {g.opinions.filter(o => o.prediction.context).slice(0, 2).map((o, i) => (
                      <div key={i} style={{ fontSize: 12, color: 'var(--color-text-muted)', lineHeight: 1.5, marginBottom: 4 }}>
                        <span style={{ color: 'var(--color-text-secondary)' }}>{o.published_date.slice(5)}:</span>{' '}
                        {o.prediction.context}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function FlatView({ opinions, showChannel }: { opinions: Opinion[]; showChannel: boolean }) {
  const sorted = useMemo(() =>
    [...opinions].sort((a, b) => b.published_date.localeCompare(a.published_date)),
    [opinions]
  );

  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="data-table">
        <thead>
          <tr>
            {showChannel && <th>分析师</th>}
            <th>方向</th>
            <th>类型</th>
            <th>入场价</th>
            <th>目标价</th>
            <th>止损</th>
            <th>置信度</th>
            <th>日期</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((o, i) => (
            <tr key={o.opinion_id || i}>
              {showChannel && (
                <td>
                  <a href={`/bloggers/${o.blogger_slug || toAnalystSlug(o.analyst)}/`}
                     className="data-link" style={{ fontSize: 13 }}>
                    {o.analyst}
                  </a>
                </td>
              )}
              <td><SentimentBadge sentiment={o.sentiment} /></td>
              <td><span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{typeLabels[o.prediction.type] || o.prediction.type}</span></td>
              <td><span className="font-data">{o.prediction.price != null ? `$${o.prediction.price.toFixed(1)}` : '—'}</span></td>
              <td><span className="font-data">{o.prediction.target_price != null ? `$${o.prediction.target_price.toFixed(1)}` : '—'}</span></td>
              <td><span className="font-data">{o.prediction.stop_loss != null ? `$${o.prediction.stop_loss.toFixed(1)}` : '—'}</span></td>
              <td><span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{confidenceLabels[o.prediction.confidence] || o.prediction.confidence}</span></td>
              <td><span className="font-data" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{o.published_date.slice(5)}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
