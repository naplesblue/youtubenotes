export interface DashboardData {
  generated_at: string;
  stats: Stats;
  data_quality: QualityIssue[];
  bloggers: Blogger[];
  tickers: Ticker[];
  opinions: Opinion[];
  videos: Video[];
  activity: ActivityEvent[];
}

export interface Stats {
  total_videos: number;
  total_channels: number;
  total_opinions: number;
  verifiable_opinions: number;
  verified_count: number;
  pending_count: number;
  total_tickers: number;
  total_bloggers: number;
  date_range: { start: string | null; end: string | null };
}

export interface QualityIssue {
  type: string;
  title: string;
  severity: 'warning' | 'info' | 'error';
  count?: number;
  total?: number;
  percentage?: number;
  affected?: { opinion_id: string; ticker: string; analyst: string; date: string }[];
  conflicts?: { tickers: string[]; description: string }[];
  aliases?: { names: string[]; channel: string }[];
  values?: Record<string, number>;
  channels?: string[];
  with_opinions?: number;
  total_channels?: number;
}

export interface Prediction {
  type: string;
  direction: string;
  price: number | null;
  target_price: number | null;
  stop_loss: number | null;
  confidence: string;
  conviction: string;
  horizon: string;
  context: string;
}

export interface Verification {
  status: string;
  snapshots: Record<string, { price: number | null; return_pct: number | null; result: string; regime: string | null }>;
  last_verified: string;
}

export interface Opinion {
  opinion_id: string;
  video_id: string;
  channel: string;
  analyst: string;
  blogger_slug?: string;
  published_date: string;
  ticker: string;
  company_name: string;
  sentiment: string;
  prediction: Prediction;
  price_at_publish: number | null;
  verification: Verification;
}

export interface Blogger {
  slug: string;
  alias_slugs?: string[];
  channel: string;
  analyst: string;
  total_opinions: number;
  verified_opinions: number;
  win_rate: Record<string, number | null>;
  avg_return: Record<string, number | null>;
  credibility_score: number | null;
  sample_sufficient: boolean;
  top_tickers: { ticker: string; count: number }[];
  sentiment_distribution: Record<string, number>;
  daily_activity: Record<string, number>;
  opinions: Opinion[];
}

export interface Ticker {
  ticker: string;
  company_name: string;
  active_opinions: number;
  consensus: {
    bullish_count: number;
    bearish_count: number;
    neutral_count: number;
    weighted_sentiment: number;
    avg_target_price: number | null;
    avg_support_price: number | null;
  };
  top_analysts: {
    analyst: string;
    channel: string;
    blogger_slug?: string;
    sentiment: string;
    win_rate_90d: number | null;
  }[];
  price_data: { date: string; open: number; high: number; low: number; close: number }[];
  opinion_markers: {
    opinion_id?: string;
    video_id?: string;
    date: string;
    analyst: string;
    blogger_slug?: string;
    sentiment: string;
    type: string;
    direction: string;
    price: number | null;
    target_price: number | null;
    confidence: string;
    price_at_publish: number | null;
  }[];
}

export interface Video {
  video_id: string;
  title: string;
  channel: string;
  host: string;
  date: string;
  youtube_url: string;
  mentioned_tickers: string[];
  key_points: string[];
  summary: string;
}

export interface ActivityEvent {
  type: 'video' | 'opinion';
  date: string;
  channel: string;
  title?: string;
  video_id?: string;
  tickers?: (string | { ticker: string; company_name?: string; sentiment?: string; analyst?: string; price_levels?: any[] })[];
  analyst?: string;
  blogger_slug?: string;
  ticker?: string;
  sentiment?: string;
  prediction_type?: string;
}
