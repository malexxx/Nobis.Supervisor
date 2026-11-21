/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./static/index.html",
    "./static/partials/**/*.html",
    "./src/ui/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        gray: {
          850: "#172033",
        },
      },
    },
  },
  plugins: [],
};
