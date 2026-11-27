// Tailwind v3 config for the compiled stylesheet (web/static/dist/tailwind.css).
// Only class names the scanner can see as complete literal strings make it
// into the CSS — never build them from pieces (e.g. 'bg-' + color).
// Python is scanned because route code returns class strings
// (e.g. web/routes/holdings.py).
//
// `phone:` / `wide:` (issue #146): a phone is a narrow screen (below sm) OR a
// short one (a landscape phone: under 500px tall and narrower than lg), which
// width breakpoints alone treat as a desktop. `wide:` is exactly the opposite,
// for mobile-first markup that used to switch at `sm:`. Hand-written CSS
// repeats these queries (base.html, projections.html) — keep them in step.
/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './web/templates/**/*.html',
    './web/static/js/**/*.js',
    './web/**/*.py',
  ],
  theme: {
    extend: {
      screens: {
        phone: { raw: '(max-width: 639px), (max-height: 500px) and (max-width: 1023px)' },
        wide: { raw: '(min-width: 640px) and (min-height: 501px), (min-width: 1024px)' },
      },
    },
  },
};
