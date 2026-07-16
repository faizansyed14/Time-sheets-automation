/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter"', "system-ui", "-apple-system", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
          950: "#1e1b4b",
        },
      },
      boxShadow: {
        // Subtle, layered, enterprise depth — no heavy drop shadows.
        xs: "0 1px 2px 0 rgb(15 23 42 / 0.05)",
        card: "0 1px 2px -1px rgb(15 23 42 / 0.06), 0 1px 3px 0 rgb(15 23 42 / 0.05)",
        "card-hover": "0 2px 4px -2px rgb(15 23 42 / 0.08), 0 4px 12px -4px rgb(15 23 42 / 0.08)",
        pop: "0 8px 24px -8px rgb(15 23 42 / 0.18), 0 16px 40px -12px rgb(15 23 42 / 0.16)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "slide-in": {
          "0%": { transform: "translateX(100%)" },
          "100%": { transform: "translateX(0)" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "translateY(8px) scale(.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "overlay-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        // Premium / chat motion
        "blink": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0" },
        },
        "typing-dot": {
          "0%, 60%, 100%": { transform: "translateY(0)", opacity: ".4" },
          "30%": { transform: "translateY(-4px)", opacity: "1" },
        },
        "shimmer": {
          "100%": { transform: "translateX(100%)" },
        },
        "gradient-pan": {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
        "pop-in": {
          "0%": { opacity: "0", transform: "scale(.92)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        "bubble-in": {
          "0%": { opacity: "0", transform: "translateY(8px) scale(.98)" },
          "100%": { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "review-pop": {
          "0%": { opacity: "0", transform: "translate(10px, -6px) scale(0.92)" },
          "70%": { transform: "translate(-2px, 2px) scale(1.03)" },
          "100%": { opacity: "1", transform: "translate(0, 0) scale(1)" },
        },
        "review-float": {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-4px)" },
        },
      },
      animation: {
        "fade-up": "fade-up .25s ease-out both",
        "slide-in": "slide-in .25s cubic-bezier(.32,.72,.35,1) both",
        "scale-in": "scale-in .2s cubic-bezier(.16,1,.3,1) both",
        "overlay-in": "overlay-in .2s ease-out both",
        "blink": "blink 1s step-end infinite",
        "shimmer": "shimmer 1.6s infinite",
        "gradient-pan": "gradient-pan 6s ease infinite",
        "pop-in": "pop-in .22s cubic-bezier(.16,1,.3,1) both",
        "bubble-in": "bubble-in .28s cubic-bezier(.16,1,.3,1) both",
        "review-pop": "review-pop .45s cubic-bezier(.34,1.56,.64,1) both",
        "review-float": "review-float 3s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
