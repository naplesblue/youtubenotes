import { useState, useMemo } from 'react';
import type { Ticker } from '../lib/types';

interface Props {
  tickers: Ticker[];
}

export default function TickerGrid({ tickers }: Props) {
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<'opinions' | 'sentiment'>('opinions');

  const filtered = useMemo(() => {
    let list = [...tickers];
    if (search) {
      const q = search.toUpperCase();
      list = list.filter(t =>
        t.ticker.includes(q) || t.company_name.toUpperCase().includes(q)
      );
    }
    list.sort((a, b) => {
      if (sortBy === 'opinions') return b.active_opinions - a.active_opinions;
      return b.consensus.weighted_sentiment - a.consensus.weighted_sentiment;
    });
    return list;
  }, [tickers, search, sortBy]);

  const btnStyle = (active: boolean) => ({
    padding: '4px 12px',
    borderRadius: 4,
    fontSize: 11,
    fontWeight: 500,
    cursor: 'pointer' as const,
    border: 'none',
    background: active ? 'rgba(107,159,255,0.12)' : 'transparent',
    color: active ? 'var(--color-accent)' : 'var(--color-text-muted)',
    transition: 'all 0.15s',
  });

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, alignItems: 'center' }}>
        <input
          type="text"
          placeholder="搜索标的..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{
            padding: '7px 12px',
            borderRadius: 6,
            fontSize: 13,
            border: '1px solid var(--color-border)',
            background: 'var(--color-surface)',
            color: 'var(--color-text)',
            outline: 'none',
            width: 180,
          }}
        />
        <div style={{ display: 'flex', gap: 2 }}>
          <button onClick={() => setSortBy('opinions')} style={btnStyle(sortBy === 'opinions')}>
            按数量
          </button>
          <button onClick={() => setSortBy('sentiment')} style={btnStyle(sortBy === 'sentiment')}>
            按情绪
          </button>
        </div>
        <span style={{ fontSize: 11, color: 'var(--color-text-muted)', marginLeft: 'auto' }}>
          {filtered.length} 个标的
        </span>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
        gap: 10,
      }}>
        {filtered.map(t => {
          const ws = t.consensus.weighted_sentiment;
          const total = t.consensus.bullish_count + t.consensus.bearish_count + t.consensus.neutral_count;
          const bullPct = total > 0 ? (t.consensus.bullish_count / total) * 100 : 0;
          const bearPct = total > 0 ? (t.consensus.bearish_count / total) * 100 : 0;

          return (
            <a key={t.ticker} href={`/tickers/${t.ticker}/`}
               style={{
                 display: 'block',
                 padding: 16,
                 borderRadius: 10,
                 border: '1px solid var(--color-border)',
                 background: 'var(--color-surface)',
                 textDecoration: 'none',
                 color: 'inherit',
                 transition: 'all 0.2s',
               }}
               className="ticker-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                <span className="font-data" style={{ fontSize: 15, fontWeight: 700 }}>{t.ticker}</span>
                <span className="font-data" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                  {t.active_opinions}
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {t.company_name}
              </div>

              {/* Sentiment bar */}
              <div style={{ height: 3, borderRadius: 2, background: 'var(--color-border)', overflow: 'hidden', display: 'flex' }}>
                {bullPct > 0 && <div style={{ height: '100%', width: `${bullPct}%`, background: 'var(--color-green)', opacity: 0.8 }} />}
                {bearPct > 0 && <div style={{ height: '100%', width: `${bearPct}%`, background: 'var(--color-red)', opacity: 0.8 }} />}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, marginTop: 6, color: 'var(--color-text-muted)' }}>
                <span style={{ color: 'var(--color-green)' }}>{t.consensus.bullish_count}</span>
                <span>{t.consensus.neutral_count}</span>
                <span style={{ color: 'var(--color-red)' }}>{t.consensus.bearish_count}</span>
              </div>

              {t.consensus.avg_target_price && (
                <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 8 }}>
                  目标价 <span className="font-data" style={{ color: 'var(--color-text-secondary)' }}>
                    ${t.consensus.avg_target_price.toFixed(0)}
                  </span>
                </div>
              )}
            </a>
          );
        })}
      </div>
    </div>
  );
}
