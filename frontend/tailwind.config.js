/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular"],
      },
      colors: {
        ink: "#05070c",
        panel: "rgba(12, 16, 28, 0.74)",
        line: "rgba(255, 255, 255, 0.12)",
        neon: "#66f2c2",
        cobalt: "#6aa9ff",
        ember: "#ffb86b",
      },
      boxShadow: {
        glow: "0 0 40px rgba(102, 242, 194, 0.18)",
      },
    },
  },
  plugins: [],
};
