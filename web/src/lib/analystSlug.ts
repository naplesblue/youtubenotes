export function toAnalystSlug(analyst: string): string {
  return analyst
    .toLowerCase()
    .trim()
    .replace(/\//g, ' ')
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'unknown';
}
