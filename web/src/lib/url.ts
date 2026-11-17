/** A ref is a link only when it is a web address. A path or a code is not: as a link it opens "not found". */
export function isUrl(s: string | null | undefined): s is string {
  return !!s && /^https?:\/\//.test(s);
}
