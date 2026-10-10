# Local Front-End Assets — Design Spec

**Date:** 2026-10-10
**Status:** Approved
**Issue:** #69

## Problem

`web/templates/base.html` loads two front-end dependencies from CDNs:

- Tailwind's **play CDN** (`https://cdn.tailwindcss.com`). It compiles CSS in
  the browser on every page load, and Tailwind says it isn't meant for production.
- **Alpine** from `cdn.jsdelivr.net` at an unpinned `alpinejs@3.x.x`.

If either host is unreachable, pages render unstyled and Alpine controls stop
working. This is the cause of the misleading e2e failures in network-restricted
sandboxes (see the CLAUDE.md testing gotcha).

The two other front-end libraries are committed as files: htmx 2.0.4
(`web/static/htmx.min.js`) and Sortable 1.15.6
(`web/static/js/vendor/Sortable.min.js`). Nothing tracks their versions, so
they never get updated.

## Goals

1. The app works with no outbound network access, and `base.html` references
   no third-party hosts.
2. All four front-end libraries (Tailwind, Alpine, htmx, Sortable) are pinned in
   one manifest, and Dependabot proposes their upgrades.
3. Node is needed **only at build time**. Neither runtime image (Docker or
   Lambda) runs Node.
4. Adding Node doesn't noticeably slow builds. Dependency installs are cached
   between builds in CI and Docker (see "Build-time budget").

## Non-goals

- Upgrading to Tailwind v4. Its CSS-based configuration and changed defaults
  are a separate migration; this spec stays on v3.
- Bundling, minifying, or transpiling the app's own JS in `web/static/js/`. It
  stays hand-written and is served as-is.
- A Content-Security-Policy header. Removing third-party hosts makes a strict
  CSP possible, but adding one is follow-up work.
- Visual changes of any kind. After the switch, every page should render the
  same as before.

## Approach

Add a minimal `package.json` at the repo root that lists the four libraries as
`devDependencies`. npm scripts compile Tailwind and copy the three JS libraries
out of `node_modules/` into a **gitignored** output directory,
`web/static/dist/`. Flask serves that directory like any other static path.

Nothing generated is committed. Every path that runs the app (local
`mise run serve`, CI, both Dockerfiles) builds the assets itself. A
Dependabot bump then reaches the app on its own: the PR changes
`package-lock.json`, CI rebuilds the assets from the new lockfile, and e2e
tests the result.

### Why not commit the built output?

With committed CSS and JS, a Dependabot PR would update the lockfile without
rebuilding the assets. A CI check for stale assets would then fail on every
such PR. Fixing that automatically requires pushing to Dependabot branches,
which needs a personal access token because Dependabot's workflow token is
read-only. Building on demand avoids that problem entirely.

## Package manifest

`package.json` (new, `"private": true`):

| Package | Version range | Notes |
|---------|---------------|-------|
| `tailwindcss` | `~3.4.x` (latest 3.x when implemented) | v3 only. Dependabot ignores majors (see below) |
| `alpinejs` | `^3.x` (latest 3.x when implemented) | Replaces the unpinned CDN `3.x.x` |
| `htmx.org` | `2.0.4` | Same version as the committed file, so the switch changes no behavior |
| `sortablejs` | `1.15.6` | Same version as the committed file |

`package-lock.json` is committed. Every automated install uses `npm ci`, which
fails if the lockfile and `package.json` disagree.

npm scripts:

```jsonc
{
  "scripts": {
    "build": "npm run build:css && npm run build:js",
    "build:css": "tailwindcss -c tailwind.config.js -i web/static/src/tailwind.css -o web/static/dist/tailwind.css --minify",
    "build:js": "node scripts/copy-vendor.mjs",
    "watch:css": "tailwindcss -c tailwind.config.js -i web/static/src/tailwind.css -o web/static/dist/tailwind.css --watch"
  }
}
```

`scripts/copy-vendor.mjs` is a short script with no dependencies. It copies
these files and creates `web/static/dist/` first:

| From `node_modules/` | To `web/static/dist/` |
|---|---|
| `alpinejs/dist/cdn.min.js` | `alpine.min.js` |
| `htmx.org/dist/htmx.min.js` | `htmx.min.js` |
| `sortablejs/Sortable.min.js` | `Sortable.min.js` |

It's a Node script rather than shell `cp` so it behaves the same on macOS,
Linux and in Docker.

## Tailwind configuration

`tailwind.config.js` (new):

```js
module.exports = {
  darkMode: 'class',
  content: [
    './web/templates/**/*.html',
    './web/static/js/**/*.js',
    './web/**/*.py',
  ],
};
```

`web/static/src/tailwind.css` (new) contains just the three
`@tailwind base; @tailwind components; @tailwind utilities;` directives.

**Python sources must be in `content`.** Route code returns Tailwind class
names as Python strings, for example `web/routes/holdings.py:303`
(`"text-gray-300 dark:text-gray-600"`) and `:413`, `:415` and `:455`
(`text-red-600 …`, `text-amber-600 …`). The globs proposed in #69 cover only
templates and JS, so these classes would silently drop out of the compiled CSS.

Before switching, search for class names **built from pieces**, such as
`'bg-' + color` in JS or `'text-' ~ x` in Jinja, because Tailwind's scanner
can't see them. Rewrite any found as complete literal class strings. A first
pass found none, but the implementation must repeat that check.

The play CDN's preflight and its default theme match the v3 compiler, so no
`theme` overrides are needed.

## `base.html` changes

- Remove the play-CDN `<script>` and the inline `tailwind.config = …` script.
- Remove the jsdelivr Alpine `<script>`. Load
  `url_for('static', filename='dist/alpine.min.js')` with `defer`, in the same
  position.
- Point htmx and Sortable at `dist/htmx.min.js` and `dist/Sortable.min.js`.
  Delete the committed `web/static/htmx.min.js` and
  `web/static/js/vendor/Sortable.min.js`.
- Add `<link rel="stylesheet" href="{{ url_for('static', filename='dist/tailwind.css') }}">`
  **after** the inline `<style>` block (just before `</head>`). The play CDN
  inserts its generated styles after the inline `<style>`, and the custom CSS
  depends on that cascade order. The comment at `base.html:144-149` explains
  this for the nav/scrim selectors. Putting the link in the same place keeps
  that order. Update the comment to describe the compiled stylesheet instead
  of the CDN.
- The dark-mode script that runs before first paint stays as it is.

### Missing-asset guard

If `web/static/dist/tailwind.css` doesn't exist (for example, someone runs
`uv run python -m web.app` without building), the app should say so instead of
quietly serving unstyled pages. At app creation, log one clear warning naming
the missing file and the command to fix it (`mise run assets`). It's a warning
rather than an error so CLI-only and test setups that never render pages
aren't blocked.

## Tooling

### mise

`mise.toml`:

```toml
[tools]
uv = "latest"
node = "22"          # LTS; build-time only

[tasks.setup]
run = ["uv sync", "npm ci", "npm run build"]

[tasks.assets]
run = "npm run build"
description = "Build front-end assets into web/static/dist/"

[tasks.assets-watch]
run = "npm run watch:css"
description = "Rebuild Tailwind CSS on template changes"

[tasks.serve]
depends = ["assets"]

[tasks.test-e2e]
depends = ["assets"]
```

`assets` runs only `npm run build` (about 1–2 s), not `npm ci`, so it's cheap
enough to run before every `serve` and `test-e2e`. `setup` runs `npm ci` once
per lockfile change, the same way it runs `uv sync`.

### .gitignore / .dockerignore

- `.gitignore`: add `node_modules/` and `web/static/dist/`.
- `.dockerignore`: add `node_modules/` and `web/static/dist/`. Images build
  their own assets and must not copy a developer's local build.

### Docker (`Dockerfile`, `Dockerfile.lambda`)

Add an asset-builder stage to both:

```dockerfile
FROM --platform=$BUILDPLATFORM node:22-slim AS assets
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY tailwind.config.js ./
COPY scripts/copy-vendor.mjs ./scripts/
COPY web/ ./web/
RUN npm run build
```

Then, in the final stage, after `COPY web/ ./web/`:

```dockerfile
COPY --from=assets /app/web/static/dist/ ./web/static/dist/
```

Details:

- **`--platform=$BUILDPLATFORM`**: the output is plain CSS and JS, the same on
  every architecture. The publish job builds `linux/amd64,linux/arm64` under
  QEMU, so without this flag the arm64 build would run `npm ci` under
  emulation, which is much slower. With it, the stage runs natively once and
  both architectures reuse it.
- **Layer order**: `npm ci` sits right after copying the manifest, so its layer
  is rebuilt only when `package*.json` changes. Template edits invalidate only
  the `npm run build` layer (about 1–2 s).
- **Caching**: the publish job already uses `cache-from/cache-to: type=gha,
  mode=max`, which keeps builder stages too. The `--mount=type=cache` covers
  local rebuilds after the lockfile changes.
- No Node in either runtime image.

## CI (`.github/workflows/ci.yml`)

- `jdx/mise-action` installs Node from `mise.toml`, and already caches mise
  tool installs between runs.
- Add an `actions/cache` step for `~/.npm`, keyed on
  `hashFiles('package-lock.json')`.
- Add `npm ci` and `mise run assets` steps before the unit tests. The unit
  tests don't need the assets, but building early gives a broken Tailwind
  config a fast failure.

## PR preview (`.github/workflows/pr-preview.yml`)

Today the preview job runs a plain `docker build -f Dockerfile.lambda` on a
fresh runner with no layer cache, so every layer is rebuilt on every push.
Without a change, the new asset stage would add a cold `npm ci` (estimated
10–20 s) to every preview deploy, on top of the cold `uv pip install` it
already pays.

Switch the "Build and push Lambda image" step to `docker/setup-buildx-action`
plus `docker/build-push-action`, with a **registry cache stored in the
preview ECR repository**:

```yaml
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4

      - name: Compute image URI
        id: image
        run: echo "uri=${{ steps.ecr.outputs.registry }}/$ECR_REPOSITORY:pr-${{ github.event.number }}-${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"

      - name: Build and push Lambda image
        uses: docker/build-push-action@v7
        with:
          context: .
          file: Dockerfile.lambda
          platforms: linux/amd64
          push: true
          tags: ${{ steps.image.outputs.uri }}
          cache-from: type=registry,ref=<registry>/<repo>:buildcache
          cache-to: type=registry,ref=<registry>/<repo>:buildcache,mode=max,image-manifest=true,oci-mediatypes=true
          provenance: false
          sbom: false
```

- **Why a registry cache instead of `type=gha`:** GitHub only lets a PR read
  caches from its own branch and from `main`, and `main` never builds
  `Dockerfile.lambda`. With `type=gha`, every PR's first deploy would be cold.
  The ECR `buildcache` tag is shared by all PRs, so a first deploy is warm
  whenever its lockfiles match what's cached. The OIDC role can already push
  to this repository, so no new credentials are needed.
- **`provenance: false` / `sbom: false` are required.** By default buildx
  attaches attestations, which turns the pushed image into a multi-entry
  manifest index. Lambda rejects that format.
- **`image-manifest=true,oci-mediatypes=true`** are required for ECR to
  accept a registry cache.
- **Keep the image URI available to the deploy step.** The deploy step reads
  the image URI from a step output. It now comes from the "Compute image URI"
  step instead of the build step.
- **ECR lifecycle:** keep the `buildcache` tag when configuring a lifecycle
  policy for the preview repository. Note this in
  `docs/aws-preview-setup.md`.

The Python dependency layer is cached too, so preview deploys should end up
faster than today, not just break even.

## Dependency upgrades (Dependabot)

Add to `.github/dependabot.yml`:

```yaml
  # Front-end build dependencies (package.json + package-lock.json):
  # Tailwind, Alpine, htmx, Sortable.
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      frontend:
        patterns: ["*"]
        update-types: ["minor", "patch"]
    ignore:
      # Tailwind v4 is a config-model migration, not a bump. See Non-goals.
      - dependency-name: "tailwindcss"
        update-types: ["version-update:semver-major"]
```

- Grouping minor and patch updates into one PR keeps the volume to about one
  front-end PR a week at most. Majors for Alpine, htmx and Sortable still
  arrive as separate PRs so they get individual attention.
- The existing `docker` ecosystem will also start tracking `node:22-slim`.
  Add an ignore rule for `node` majors so Docker's Node version doesn't drift
  away from the `node = "22"` pin in `mise.toml`. Moving to the next Node LTS
  is a deliberate change to both files.

**How an upgrade flows:** Dependabot opens a PR that changes
`package.json` and `package-lock.json`. CI runs `npm ci` and the asset build,
then the full e2e suite runs against the rebuilt assets. If it's green,
merge. Nothing needs regenerating by hand.

**Manual upgrade** (e.g. a major): `npm install <pkg>@<version>`, then
`mise run assets`, then `mise run test`, then commit `package*.json`.

## Testing

### Offline enforcement in e2e

Each e2e suite has a `flask_server` fixture (`tests/e2e/ledger/conftest.py`,
`tests/e2e/networth/conftest.py`). Add a shared, autouse fixture that installs
`context.route("**/*", …)`. It aborts every request whose host isn't the local
test server and **fails the test**, naming the URL it blocked. This makes
"works offline" a permanent, tested rule instead of a one-time check. It also
ensures a future CDN `<script>` tag can't slip back in.

### Visual parity check (one-time, during implementation)

The e2e suite checks behavior, not appearance. Before merging:

- Take Playwright full-page screenshots of the Holdings, Budget, Transactions,
  Trends, Projections and Import pages, in light and dark mode, at desktop and
  phone widths.
- Take one set on `main` (CDN) and one on the branch (compiled), and compare
  them pixel by pixel. Any difference is either a class missing from `content`
  or a cascade-order regression, and must be fixed before merging.
- The screenshots are a review aid only. They aren't committed or added to CI.

### Existing suites

`mise run test` must pass with the network blocked. That's the acceptance test
for #69 and also the condition that unblocks #70's panel-discoverability item.

## Build-time budget

Targets, measured during implementation and recorded in the PR:

| Path | Cold (no cache) | Warm (lockfile unchanged) |
|------|-----------------|---------------------------|
| CI `ci` job, added time | ≤ ~30 s | ≤ ~10 s |
| Docker publish (both arches), added time | ≤ ~30 s | ~0 s for `npm ci`, plus ~2 s for the CSS build |
| Local `mise run serve` startup | n/a | ≤ ~2 s added |

If a measurement comes in well over these targets, stop and reconsider before
merging. One fallback is replacing `npm ci` for Tailwind with the standalone
binary.

## Documentation updates

- **CLAUDE.md:**
  - Tech stack: add "Node 22 (build-time only) for front-end assets".
  - Common commands: add `mise run assets` and `mise run assets-watch`.
  - Remove the CDN testing gotcha.
  - Add a code-style rule: Tailwind class names must be literal strings in
    templates, JS or Python, because the compiled CSS includes only classes the
    scanner can see.
- **README.md:** add Node to the setup prerequisites. mise installs it.
- **AGENTS.md:** mirror any CLAUDE.md content it duplicates.

## Rollout

One PR, built in this order so the visual check compares like with like:

1. Add `package.json` and the lockfile, the Tailwind config and input CSS,
   `copy-vendor.mjs`, the mise tasks, and the ignore-file entries.
2. Switch `base.html`, delete the two committed vendor files, add the
   missing-asset guard.
3. Run the content audit for built-up class names and the screenshot
   comparison; fix any differences.
4. Add the e2e offline fixture.
5. Update both Dockerfiles, CI, the PR-preview workflow and Dependabot. Measure build times.
6. Update the docs.

## Decisions taken (flag if you disagree)

- **htmx and Sortable move to npm too.** They could stay committed, but moving
  them puts all four libraries under one manifest and one upgrade process.
- **Node 22 LTS**, pinned to the major version in both `mise.toml` and the
  Docker stage.
- **Output in `web/static/dist/`**, gitignored. Source CSS goes in
  `web/static/src/`.
- **A missing build logs a warning rather than raising an error.**
