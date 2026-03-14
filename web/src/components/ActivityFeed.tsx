import { useState } from 'react';
import type { ActivityEvent, Video, Opinion } from '../lib/types';
import { toAnalystSlug } from '../lib/analystSlug';
import { typeLabels, directionLabels } from '../lib/colors';

interface Props {
  activity: ActivityEvent[];
  videos: Video[];
  opinions: Opinion[];
}

export default function ActivityFeed({ activity, videos, opinions }: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const videoMap = new Map(videos.map(v => [v.video_id, v]));

  // For opinion events, find matching opinions by ticker+analyst+date
  const findOpinion = (evt: ActivityEvent): Opinion | undefined => {
    if (evt.type !== 'opinion') return undefined;
    return opinions.find(o =>
      o.ticker === evt.ticker &&
      o.analyst === evt.analyst &&
      o.published_date === evt.date
    );
  };

  const toggle = (i: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  return (
    <div style={{ maxHeight: 560, overflowY: 'auto' }}>
      {activity.map((event, i) => {
        const isOpen = expanded.has(i);
        const video = event.video_id ? videoMap.get(event.video_id) : undefined;
        const opinion = findOpinion(event);

        return (
          <div key={i}
            style={{
              borderBottom: i < activity.length - 1 ? '1px solid var(--color-border)' : 'none',
            }}>
            {/* Main row — clickable */}
            <div
              onClick={() => toggle(i)}
              className="feed-row"
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 12,
                padding: '12px 20px', cursor: 'pointer',
                transition: 'background 0.15s',
              }}
            >
              {/* Date */}
              <span className="font-data" style={{ fontSize: 11, width: 56, flexShrink: 0, paddingTop: 2, color: 'var(--color-text-muted)' }}>
                {event.date.slice(5)}
              </span>

              {/* Dot */}
              <span style={{
                width: 6, height: 6, borderRadius: '50%', marginTop: 7, flexShrink: 0,
                background: event.type === 'video' ? 'var(--color-accent)' : 'var(--color-amber)',
              }} />

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                {/* Channel — links to blogger page */}
                <a
                  href={`/bloggers/${event.blogger_slug || toAnalystSlug(event.analyst || event.channel)}/`}
                  className="data-link"
                  style={{ fontSize: 12, color: 'var(--color-text-muted)' }}
                  onClick={e => e.stopPropagation()}
                >
                  {event.channel}
                </a>

                {event.type === 'video' ? (
                  <a href={`/videos/${event.video_id}/`}
                    className="data-link"
                    style={{ fontSize: 13, marginTop: 2, color: 'var(--color-text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}
                    onClick={e => e.stopPropagation()}>
                    {event.title}
                  </a>
                ) : (
                  <div style={{ marginTop: 2, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className={`badge ${
                      event.sentiment?.includes('bullish') ? 'badge-green' :
                      event.sentiment?.includes('bearish') ? 'badge-red' : 'badge-muted'
                    }`}>{event.ticker}</span>
                    {event.analyst && (
                      <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{event.analyst}</span>
                    )}
                  </div>
                )}

                {event.tickers && event.tickers.length > 0 && (
                  <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
                    {event.tickers.slice(0, 5).map((t: any) => {
                      const sym = typeof t === 'string' ? t : t.ticker;
                      return (
                        <a key={sym} href={`/tickers/${sym}/`} className="pill" onClick={e => e.stopPropagation()}>{sym}</a>
                      );
                    })}
                    {event.tickers.length > 5 && (
                      <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>+{event.tickers.length - 5}</span>
                    )}
                  </div>
                )}
              </div>

              {/* Expand indicator */}
              <span style={{
                fontSize: 10, color: 'var(--color-text-muted)', paddingTop: 4, flexShrink: 0,
                transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s',
              }}>▶</span>
            </div>

            {/* Expanded detail */}
            {isOpen && (
              <div style={{
                padding: '0 20px 12px 88px',
                fontSize: 12, color: 'var(--color-text-secondary)',
                lineHeight: 1.6,
              }}>
                {event.type === 'video' && video && (
                  <div>
                    {video.key_points.length > 0 && (
                      <ul style={{ margin: '0 0 8px 0', paddingLeft: 16 }}>
                        {video.key_points.slice(0, 5).map((kp, j) => (
                          <li key={j} style={{ marginBottom: 2 }}>{kp}</li>
                        ))}
                      </ul>
                    )}
                    {video.mentioned_tickers.length > 0 && (
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                        <span style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>提及标的:</span>
                        {video.mentioned_tickers.map((t: any) => {
                          const sym = typeof t === 'string' ? t : t.ticker;
                          return (
                            <a key={sym} href={`/tickers/${sym}/`} className="pill" style={{ fontSize: 10 }}>{sym}</a>
                          );
                        })}
                      </div>
                    )}
                    {video.youtube_url && (
                      <div style={{ display: 'flex', gap: 12, marginTop: 4 }}>
                        <a href={`/videos/${video.video_id}/`}
                           className="data-link" style={{ fontSize: 11 }}>
                          查看简报 →
                        </a>
                        <a href={video.youtube_url} target="_blank" rel="noopener noreferrer"
                           className="data-link" style={{ fontSize: 11 }}>
                          YouTube ↗
                        </a>
                      </div>
                    )}
                    {!video.youtube_url && (
                      <a href={`/videos/${video.video_id}/`}
                         className="data-link" style={{ fontSize: 11, marginTop: 4, display: 'inline-block' }}>
                        查看简报 →
                      </a>
                    )}
                  </div>
                )}

                {event.type === 'video' && !video && (
                  <div style={{ color: 'var(--color-text-muted)', fontStyle: 'italic' }}>暂无视频详情</div>
                )}

                {event.type === 'opinion' && opinion && (
                  <div>
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 6 }}>
                      <span>类型: {typeLabels[opinion.prediction.type] || opinion.prediction.type}</span>
                      <span>方向: {directionLabels[opinion.prediction.direction] || opinion.prediction.direction}</span>
                      {opinion.prediction.price != null && (
                        <span>入场: <span className="font-data">${opinion.prediction.price.toFixed(1)}</span></span>
                      )}
                      {opinion.prediction.target_price != null && (
                        <span>目标: <span className="font-data">${opinion.prediction.target_price.toFixed(1)}</span></span>
                      )}
                      {opinion.prediction.stop_loss != null && (
                        <span>止损: <span className="font-data">${opinion.prediction.stop_loss.toFixed(1)}</span></span>
                      )}
                    </div>
                    {opinion.prediction.context && (
                      <div style={{ color: 'var(--color-text-muted)', fontSize: 12, lineHeight: 1.5 }}>
                        {opinion.prediction.context}
                      </div>
                    )}
                    <div style={{ marginTop: 6, display: 'flex', gap: 12 }}>
                      <a href={`/tickers/${opinion.ticker}/`} className="data-link" style={{ fontSize: 11 }}>
                        查看 {opinion.ticker} 详情 →
                      </a>
                    </div>
                  </div>
                )}

                {event.type === 'opinion' && !opinion && (
                  <div style={{ color: 'var(--color-text-muted)', fontStyle: 'italic' }}>暂无观点详情</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
