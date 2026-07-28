/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"DM Sans"', '"Segoe UI"', "sans-serif"],
        serif: ['"Fraunces"', "Georgia", "serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        /* Primary — teal (JobApply design system) */
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
        /* Neutrals — "ink" scale */
        slate: {
          50: "#f6f7f9",
          100: "#eceef2",
          200: "#d5dae3",
          300: "#b0bac9",
          400: "#8594ab",
          500: "#667790",
          600: "#516076",
          700: "#434e60",
          800: "#3a4351",
          900: "#343a46",
          950: "#1a1d24",
        },
        /* App surfaces */
        canvas: "#f6f7f9",
        surface: "#ffffff",
      },
      borderRadius: {
        DEFAULT: "0.5rem",
        lg: "0.625rem",
        xl: "0.75rem",
        "2xl": "1rem",
      },
      boxShadow: {
        xs: "0 1px 2px 0 rgb(26 29 36 / 0.04)",
        card: "0 1px 2px rgb(26 29 36 / 0.04), 0 8px 24px rgb(26 29 36 / 0.06)",
        "card-hover": "0 4px 12px -2px rgb(26 29 36 / 0.08), 0 2px 4px -2px rgb(26 29 36 / 0.04)",
        pop: "0 12px 32px -8px rgb(26 29 36 / 0.16), 0 4px 12px -4px rgb(26 29 36 / 0.08)",
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
      },
      animation: {
        "fade-up": "fade-up .3s ease-out both",
        "slide-in": "slide-in .28s cubic-bezier(.32,.72,.35,1) both",
        "scale-in": "scale-in .22s cubic-bezier(.16,1,.3,1) both",
        "overlay-in": "overlay-in .2s ease-out both",
      },
    },
  },
  plugins: [],
};
