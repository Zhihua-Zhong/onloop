/** A time window in words: "the last 7 days", never "the last 168 hours". */
export function windowText(hours: number): string {
  return hours % 24 === 0 && hours > 24 ? `the last ${hours / 24} days` : `the last ${hours} hours`;
}

/** The window as drawn: when it starts where the data does, later than asked, say so rather than "the last 7 days". */
export function windowSpanText(hours: number, from: string, to: string): string {
  const span = (Date.parse(to) - Date.parse(from)) / 3_600_000;
  if (!(span < hours - 1)) return windowText(hours);
  const h = Math.max(1, Math.round(span));
  return h >= 48 ? `the ${Math.round(h / 24)} days since the data began` : `the ${h} hours since the data began`;
}
