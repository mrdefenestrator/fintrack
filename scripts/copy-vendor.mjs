// Copy the vendored browser libraries out of node_modules into
// web/static/dist/, where Flask serves them. Run via `npm run build:js`.
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'web', 'static', 'dist');

const files = {
  'alpinejs/dist/cdn.min.js': 'alpine.min.js',
  'htmx.org/dist/htmx.min.js': 'htmx.min.js',
  'sortablejs/Sortable.min.js': 'Sortable.min.js',
};

mkdirSync(dist, { recursive: true });
for (const [from, to] of Object.entries(files)) {
  copyFileSync(join(root, 'node_modules', from), join(dist, to));
  console.log(`copied ${from} -> web/static/dist/${to}`);
}
