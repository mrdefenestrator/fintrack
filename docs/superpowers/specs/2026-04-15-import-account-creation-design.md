# Import: Inline Account Creation with OFX Auto-detect

**Date:** 2026-04-15  
**Status:** Approved

## Problem

On a fresh database there are no accounts, so the import UI's account `<select>` is empty and the user cannot import any files. Even with a valid OFX/QFX file in hand there is no way to proceed.

## Approach

Detect-on-file-select: when the user picks file(s) in the dropzone, the first file is sent to a new `/import/detect-account` endpoint. The server parses OFX metadata and returns a partial HTML fragment that either shows a pre-filled "Create Account" form (no accounts) or a collapsed toggle to create a new one (accounts exist). A separate `POST /accounts` endpoint creates the account and returns an updated `<select>` with the new account pre-selected.

No DB schema changes required.

## Architecture

### New endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/import/detect-account` | Parse OFX metadata from uploaded file, return `#account-panel` partial |
| POST | `/accounts` | Create account, return updated `<select>` with new account selected |

### Modified files

- `spending/importer/ofx.py` — extract institution, account_type, account_id metadata
- `spending/types.py` — add `AccountMeta` TypedDict
- `web/routes/imports.py` — add `detect_account` route
- `web/routes/` — add `accounts.py` with `POST /accounts` route
- `web/app.py` — register accounts blueprint
- `web/templates/import.html` — add `#account-panel` target div
- `web/templates/partials/account_panel.html` — new partial (create form + select states)
- `web/static/app.js` — POST first file to detect-account on file input change

## OFX Metadata Extraction

`ofx.py` is extended to extract from `ofxparse`:

- `ofx.account.institution.organization` → institution name
- `ofx.account.account_type` → normalized to `checking` / `savings` / `credit`
- `ofx.account.account_id` → last 4 digits used to suggest a name

New TypedDict in `types.py`:

```python
class AccountMeta(TypedDict):
    institution: str        # e.g. "Chase"
    account_type: str       # "checking" | "savings" | "credit"
    suggested_name: str     # e.g. "Chase Credit ...1234"
```

`parse_ofx()` returns `AccountMeta | None` alongside transactions. Returns `None` on any parse failure so the detect endpoint degrades gracefully.

## UI Flow

1. User selects file(s) in the dropzone
2. `app.js` POSTs the first file to `/import/detect-account`
3. Response replaces `#account-panel`, which owns the entire account selection section (the `<select>` lives inside it, not alongside it):
   - **No accounts exist:** create form shown expanded, pre-filled. `<select>` omitted or hidden.
   - **Accounts exist:** `<select>` shown normally, with a collapsed "+ Create new account" toggle below it, pre-filled if OFX data found.
4. User submits create form → `POST /accounts` → `#account-panel` swaps to updated `<select>` (new account pre-selected) with toggle collapsed, form disappears
5. User proceeds with Import as normal

For CSV files (no OFX metadata): same flow, form fields are empty.

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Invalid/corrupt file on detect | `#account-panel` returns empty — user proceeds manually |
| Account name collision (UNIQUE constraint) | Inline validation error in `#account-panel`, no 500 |
| CSV or non-OFX file selected | Create form renders with empty fields |
| Multiple files selected | Only first file sent to detect |
| No file selected yet | `#account-panel` stays empty; existing dropdown works normally |
