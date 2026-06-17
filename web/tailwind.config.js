/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        korinna: ['"ITC Korinna"', '"Korinna"', '"Crete Round"', 'Georgia', 'serif'],
      },
      colors: {
        jeopardy: {
          board: "#030f7d",
        },
        gold: "#f0c24a",
      },
      boxShadow: {
        "clue-glow":
          "0 1px 0 rgba(0,0,0,0.85), 0 2px 6px rgba(0,0,0,0.55), 0 8px 24px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
