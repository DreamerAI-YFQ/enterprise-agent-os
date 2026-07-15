/**
 * Format an ISO timestamp as a relative time string (Chinese).
 *   < 1 min  → "刚刚"
 *   < 1 hour → "N 分钟前"
 *   < 1 day  → "N 小时前"
 *   < 7 days → "N 天前"
 *   else     → locale date
 */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;

  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return new Date(then).toLocaleDateString();
}
