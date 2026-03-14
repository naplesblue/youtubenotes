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
        t.ticker.includes(q) || t.company_name.includes(search)
      );
    }
    list.sort((a, b) => {
      if (sortBy === 'opinions') return b.active_opinions - a.active_opinions;
      return b.consensus.weighted_sentiment - a.consensus.weighted_sentiment;
    });
    return list;
  }, [tickers, search, sortBy]);

  return (
    <div>
      <div className="flex gap-3 mb-4 items-center">
        <input
          type="text"
          placeholder="搜索 Ticker..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="px-3 py-2 rounded-lg text-sm border border-slate-700 bg-slate-800 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500 w-48"
        />
        <div className="flex gap-1 text-xs">
          <button
            onClick={() => setSortBy('opinions')}
            className={`px-3 py-1.5 rounded ${sortBy === 'opinions' ? 'bg-blue-500/20 text-blue-400' : 'bg-slate-800 text-slate-400'}`}
          >观点数</button>
          <button
            onClick={() => setSortBy('sentiment')}
            className={`px-3 py-1.5 rounded ${sortBy === 'sentiment' ? 'bg-blue-500/20 text-blue-400' : 'bg-slate-800 text-slate-400'}`}
          >情绪</button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
        {filtered.map(t => {
          const ws = t.consensus.weighted_sentiment;
          const total = t.consensus.bullish_count + t.consensus.bearish_count + t.consensus.neutral_count;
          const bullPct = total > 0 ? (t.consensus.bullish_count / total) * 100 : 0;
          const bearPct = total > 0 ? (t.consensus.bearish_count / total) * 100 : 0;

          const borderColor = ws > 0.3 ? '#22c55e40' : ws < -0.3 ? '#ef444440' : '#33415540';
          const bgGlow = ws > 0.3 ? 'rgba(34,197,94,0.05)' : ws < -0.3 ? 'rgba(239,68,68,0.05)' : 'transparent';

          return (
            <a key={t.ticker} href={`/tickers/${t.ticker}/`}
               className="rounded-xl p-4 border transition-all hover:scale-[1.02] hover:shadow-lg block"
               style={{ background: `linear-gradient(135deg, #1e293b, ${bgGlow})`, borderColor }}>
              <div className="flex items-baseline justify-between mb-2">
                <span className="font-bold text-lg">{t.ticker}</span>
                <span className="text-xs text-slate-500">{t.active_opinions} 条</span>
              </div>
              <div className="text-xs text-slate-400 mb-3 truncate">{t.company_name}</div>

              {/* Sentiment bar */}
              <div className="h-2 rounded-full bg-slate-700 overflow-hidden flex">
                {bullPct > 0 && <div className="h-full bg-green-500" style={{ width: `${bullPct}%` }} />}
                {bearPct > 0 && <div className="h-full bg-red-500" style={{ width: `${bearPct}%` }} />}
              </div>
              <div className="flex justify-between text-xs mt-1">
                <span className="text-green-400">{t.consensus.bullish_count} 多</span>
                <span className="text-slate-500">{t.consensus.neutral_count}</span>
                <span className="text-red-400">{t.consensus.bearish_count} 空</span>
              </div>

              {t.consensus.avg_target_price && (
                <div className="text-xs text-slate-500 mt-2">
                  目标均价 <span className="text-slate-300">${t.consensus.avg_target_price.toFixed(0)}</span>
                </div>
              )}
            </a>
          );
        })}
      </div>
    </div>
  );
}
