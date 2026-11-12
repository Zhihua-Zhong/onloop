import type { Config } from "tailwindcss";

// Every colour, radius and size comes from the tokens in src/styles/tokens.css. A restyle edits that file (and this mapping only to add a token).
const v = (name: string) => `hsl(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), surface: v("surface"), raised: v("raised"), line: v("line"), fg: v("fg"), muted: v("muted"), accent: v("accent"),
        state: {
          open: v("state-open"), blocked: v("state-blocked"), human: v("state-human"), external: v("state-external"), stall: v("state-stall"),
          done: v("state-done"), void: v("state-void"),
        },
        zone: v("zone"),
      },
      borderRadius: { node: "var(--radius-node)", zone: "var(--radius-zone)", ui: "var(--radius-ui)" },
      fontFamily: { sans: ["var(--font-sans)"], mono: ["var(--font-mono)"] },
    },
  },
  plugins: [],
} satisfies Config;
