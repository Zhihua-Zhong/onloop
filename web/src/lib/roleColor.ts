// A role's colour: identity only, never meaning, and always beside the role's name. Picked from a fixed set by the
// name itself, so a role keeps its colour on every page and when other roles come and go. Imports nothing.

const PALETTE = ["#4f6bed", "#0f8a7e", "#c0563a", "#8a5cd6", "#b7862b", "#3d7fb8", "#b6497f", "#5c7a3a"];

export function roleColor(role: string): string {
  let h = 0;
  for (const ch of role) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/** Who asked, in plain words: the role, or "asker unknown" when the ask named none (an adapter's, a work-graph node's). */
export const askerName = (role: string | null | undefined): string => role ?? "asker unknown";
/** An avatar's initial and colour: "?" in a neutral grey for an asker nobody named. */
export const avatarOf = (role: string | null | undefined): { name: string; color: string } =>
  role ? { name: role, color: roleColor(role) } : { name: "?", color: "#8a8f98" };
