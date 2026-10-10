// Tailwind v3 config for the compiled stylesheet (web/static/dist/tailwind.css).
// Only class names the scanner can see as complete literal strings make it
// into the CSS — never build them from pieces (e.g. 'bg-' + color).
// Python is scanned because route code returns class strings
// (e.g. web/routes/holdings.py).
/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './web/templates/**/*.html',
    './web/static/js/**/*.js',
    './web/**/*.py',
  ],
};
