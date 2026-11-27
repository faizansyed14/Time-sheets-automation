/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Same family as Alpha CV: Inter for the interface (crisp, tabular numerals),
      // Plus Jakarta Sans for titles. `serif` is kept as an alias of the display face so
      // every existing `font-serif` heading picks it up.
      fontFamily: {
        sans: ['"Inter"', "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        display: ['"Plus Jakarta Sans"', '"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ['"Plus Jakarta Sans"', '"Inter"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        /* Brand: the original deep teal-green. 600 = links, accents, focus; 700-800 = primary fills. */
        brand: {
          50: "#ccfbf1",
          100: "#99f6e4",
          200: "#5eead4",
          300: "#2dd4bf",
          400: "#14b8a6",
          500: "#0d9488",
          600: "#0f766e",
          700: "#115e59",
          800: "#134e4a",
          900: "#042f2e",
          950: "#021815",
        },
        /* Neutrals: cool slate */
        slate: {
          50: "#f8fafc",
          100: "#f1f5f9",
          200: "#e2e8f0",
          300: "#cbd5e1",
          400: "#94a3b8",
          500: "#64748b",
          600: "#475569",
          700: "#334155",
          800: "#1e293b",
          900: "#0f172a",
          950: "#020617",
        },
        /* App surfaces */
        canvas: "#f6f7fb",
        surface: "#ffffff",
      },
      borderRadius: {
        DEFAULT: "0.5rem",
        lg: "0.625rem",
        xl: "0.75rem",
        "2xl": "1rem",
      },
      // Depth from hairlines and low, layered shadows - never heavy drop shadows.
      boxShadow: {
        xs: "0 1px 2px 0 rgb(15 23 42 / 0.04)",
        card: "0 1px 2px rgb(15 23 42 / 0.04), 0 0 0 1px rgb(15 23 42 / 0.02)",
        "card-hover": "0 4px 14px -6px rgb(15 23 42 / 0.12), 0 1px 3px rgb(15 23 42 / 0.04)",
        pop: "0 22px 48px -16px rgb(15 23 42 / 0.26), 0 8px 16px rgb(15 23 42 / 0.05), 0 0 0 1px rgb(15 23 42 / 0.04)",
        /* glossy primary button: a hairline of light along the top edge */
        sheen: "inset 0 1px 0 rgb(255 255 255 / 0.14), 0 1px 2px rgb(16 24 40 / 0.16), 0 0 0 1px rgb(4 47 46 / 0.88)",
        glow: "0 0 0 3px rgb(15 118 110 / 0.18)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in": {
          "0%": { transform: "translateX(100%)" },
          "100%": { transform: "translateX(0)" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "translateY(6px) scale(.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "overlay-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "typing-dot": {
          "0%, 60%, 100%": { transform: "translateY(0)", opacity: ".4" },
          "30%": { transform: "translateY(-4px)", opacity: "1" },
        },
        shimmer: {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        "skeleton-shimmer": {
          "100%": { transform: "translateX(100%)" },
        },
        "caret-blink": {
          "0%, 49%": { opacity: "1" },
          "50%, 100%": { opacity: "0" },
        },
        "feature-in": {
          "0%": { opacity: "0", transform: "translateX(-12px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up .3s ease-out both",
        "slide-in": "slide-in .28s cubic-bezier(.32,.72,.35,1) both",
        "scale-in": "scale-in .22s cubic-bezier(.16,1,.3,1) both",
        "overlay-in": "overlay-in .2s ease-out both",
        shimmer: "shimmer 1.6s linear infinite",
        "caret-blink": "caret-blink 1s step-end infinite",
        "feature-in": "feature-in .45s cubic-bezier(.16,1,.3,1) both",
      },
    },
  },
  plugins: [],
};
