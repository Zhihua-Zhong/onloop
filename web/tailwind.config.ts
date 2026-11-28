import type { Config } from "tailwindcss";

// Every colour, radius and size comes from the tokens in src/styles/tokens.css. A restyle edits that file (and this mapping only to add a token).
const v = (name: string) => `hsl(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), surface: v("surface"), popover: v("popover"), raised: v("raised"), hover: v("hover"), line: v("line"), input: v("input"),
        fg: v("fg"), muted: v("muted"), accent: v("accent"), ring: v("ring"), sidebar: v("sidebar"),
        primary: { DEFAULT: v("primary"), fg: v("primary-fg") },
        work: v("work"), wait: v("wait"), done: v("done"), danger: v("danger"),
        state: {
          open: v("state-open"), blocked: v("state-blocked"), human: v("state-human"), external: v("state-external"), stall: v("state-stall"),
          done: v("state-done"), void: v("state-void"), idle: v("state-idle"),
        },
        zone: v("zone"),
      },
      borderColor: { DEFAULT: v("line") },
      borderRadius: { node: "var(--radius-node)", zone: "var(--radius-zone)", ui: "var(--radius-ui)", lg: "var(--radius)", md: "calc(var(--radius) - 2px)", sm: "calc(var(--radius) - 4px)" },
      fontFamily: { sans: ["var(--font-sans)"], mono: ["var(--font-mono)"] },
      // F's type scale: 13 px body text in the dense views, 14 px reading text, 28 px for a page's numbers
      fontSize: { xs: ["12px", "16px"], sm: ["13px", "20px"], base: ["14px", "22px"], lg: ["16px", "24px"], xl: ["20px", "28px"], "2xl": ["28px", "34px"] },
      // An overlay's content never fades in (0.7.0 plan, section 7): only the dim behind it fades; the content moves in
      // with a transform, and not at all under prefers-reduced-motion (motion-reduce:animate-none where they are used).
      keyframes: {
        dim: { from: { opacity: "0" }, to: { opacity: "1" } },
        rise: { from: { transform: "translateY(6px) scale(.985)" }, to: { transform: "none" } },
        drop: { from: { transform: "translateY(-4px)" }, to: { transform: "none" } },
        "slide-in-left": { from: { transform: "translateX(-100%)" }, to: { transform: "none" } },
        "slide-in-right": { from: { transform: "translateX(100%)" }, to: { transform: "none" } },
        shimmer: { "0%": { opacity: "0.55" }, "50%": { opacity: "1" }, "100%": { opacity: "0.55" } },
      },
      animation: {
        dim: "dim .15s ease-out",
        rise: "rise .16s ease-out",
        drop: "drop .12s ease-out",
        "slide-in-left": "slide-in-left .18s ease-out",
        "slide-in-right": "slide-in-right .18s ease-out",
        shimmer: "shimmer 1.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
