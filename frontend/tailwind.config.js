/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
      },
      colors: {
        ink: { DEFAULT: "#171a1f", soft: "#2a2f37" },
        petrol: { 50: "#eef7f6", 100: "#d4ebe8", 500: "#0f766e", 600: "#0c5f59", 700: "#0a4f4a" },
        canvas: "#f5f6f8",
      },
      boxShadow: {
        panel: "0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06)",
        lift: "0 8px 30px rgba(16,24,40,.10)",
      },
    },
  },
  plugins: [],
};
