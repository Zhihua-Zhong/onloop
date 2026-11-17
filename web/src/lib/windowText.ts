/** A time window in words: "the last 7 days", never "the last 168 hours". */
export function windowText(hours: number): string {
  return hours % 24 === 0 && hours > 24 ? `the last ${hours / 24} days` : `the last ${hours} hours`;
}
