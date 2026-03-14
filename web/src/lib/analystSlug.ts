export function toAnalystSlug(analyst: string): string {
  if (!analyst.includes('/')) {
    return analyst;
  }

  return analyst
    .toLowerCase()
    .replace(/\//g, ' ')
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}
