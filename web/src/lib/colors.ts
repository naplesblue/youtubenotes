export const sentimentColors = {
  bullish: { bg: '#22c55e', text: '#bbf7d0', badge: 'bg-green-500/20 text-green-400' },
  bearish: { bg: '#ef4444', text: '#fecaca', badge: 'bg-red-500/20 text-red-400' },
  neutral: { bg: '#64748b', text: '#cbd5e1', badge: 'bg-slate-500/20 text-slate-400' },
} as const;

export function getSentimentColor(sentiment: string) {
  const key = sentiment.includes('bullish') ? 'bullish'
    : sentiment.includes('bearish') ? 'bearish' : 'neutral';
  return sentimentColors[key] || sentimentColors.neutral;
}

export const directionLabels: Record<string, string> = {
  long: '做多', short: '做空', hold: '观望',
};

export const typeLabels: Record<string, string> = {
  target_price: '目标价', entry_zone: '入场区', support: '支撑位',
  resistance: '阻力位', direction_call: '方向判断', reference_only: '仅参考', stop_loss: '止损',
};

export const confidenceLabels: Record<string, string> = {
  high: '高', medium: '中', low: '低',
};

export const horizonLabels: Record<string, string> = {
  short_term: '短线', medium_term: '中期', long_term: '长期',
};
