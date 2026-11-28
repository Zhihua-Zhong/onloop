// A role's colour: identity only, always beside the role's name, and the same on its avatar, its lane marks and its
// sidebar dot. Each role takes its own slot of a fixed palette in the order of the team's role names (the onloop lead's
// step 4 review: two roles hashed to one purple). `colorFor` is that rule, pure; `useRoleColor` (lib/useRoleColor.ts)
// applies it to the team onloopd reports. A role outside the list falls back to a slot from its name.

export const PALETTE = ["#4f6bed", "#0f8a7e", "#c0563a", "#8a5cd6", "#b7862b", "#64748b", "#b6497f", "#5c7a3a"];
export const UNKNOWN = "#8a8f98";

function hashed(role: string): string {
  let h = 0;
  for (const ch of role) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/** The colour of `role` in a team whose role names are `team`: distinct for up to eight roles, stable while the team is. */
export function colorFor(team: readonly string[], role: string): string {
  const order = [...new Set(team)].sort();
  const i = order.indexOf(role);
  return i >= 0 ? PALETTE[i % PALETTE.length] : hashed(role);
}

/** Before the team is known: a slot from the role's name. */
export function roleColor(role: string): string {
  return hashed(role);
}

/** Who asked, in plain words: the role, or "asker unknown" when the ask named none (an adapter's, a work-graph node's). */
export const askerName = (role: string | null | undefined): string => role ?? "asker unknown";

/** An avatar's initial and colour: "?" in a neutral grey for an asker nobody named. */
export const avatarOf = (role: string | null | undefined, color: (r: string) => string = roleColor): { name: string; color: string } =>
  role ? { name: role, color: color(role) } : { name: "?", color: UNKNOWN };
