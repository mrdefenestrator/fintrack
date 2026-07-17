0. When bringing in the balance from an import for a credit card, we need to resolve how this interacts with the limit and available.
   **FIXED** — resolution: a confirmed statement import now keeps credit-card
   fields consistent. If the statement reports available credit, it's written
   to the account (and fills credit_limit when unset — a user-set limit is
   never overwritten); otherwise available is derived as credit_limit +
   balance. balance stays canonical; non-CC accounts unchanged.
0. Need to remove the code that displays and stores the partial account numbers for accounts.  This can be dropped from the db.  Partial account numbers will be put into name
   **FIXED** — column dropped (migration `b0b3c9940bc5` folds any existing
   partials into the name as " [1234]"); all code/UI references removed.
   NOTE: run `uv run alembic stamp 6a88702b7507 && uv run alembic upgrade head`
   once on your real DB (it predates Alembic tracking).
1. The UNIQ constraint on simply account name makes the accounts annoying to use.  We should be able to have a Wallet from venmo named wallet and a wallet from paypal named wallet without problems.
   **FIXED** — uniqueness is now (snapshot, institution, name): Venmo/Wallet
   and PayPal/Wallet coexist; duplicates within one institution still rejected.
2. We need to gracefully handle save errors and surface these to the user within the accounts spreadsheet.  For example, the UNIQ constraint error printed a traceback in the logs and made the web ui unpresponsive until I changes to a distinct account name.
   **FIXED** — duplicate saves now show an inline red banner in the sheet
   ("An account named ... already exists for institution ...") and the page
   stays responsive. Root cause of the freeze: HTMX 2.x ignores 4xx response
   bodies by default; a beforeSwap hook now swaps non-empty 422 bodies.
3. The account types on the import page for quick account creation should match all the account types in the accounts page.  We should do the same on the account picker on the transactions page, and anywhere else we have a similar input.
   **FIXED** — one canonical ordered list (ACCOUNT_TYPE_OPTIONS in
   fintrack/core/types.py) now drives the import quick-create select, the
   accounts page, and import validation; all 8 types offered everywhere.
4. The account drop down selector should give more than just the account name.  It should definitely have the institution name and maybe the account type.  Just a thought "{Institution} [{Type}] {Name}"
   **FIXED** — import and transactions account pickers now show
   "{Institution} [{Type}] {Name}" via a shared `account_label` Jinja filter
   (degrades to "[{Type}] {Name}" when no institution). The payment/auto
   account-ref selects on accounts/budget already showed institution+name and
   were left as-is.
5. The top controls on the trends page needs to be reworked.  I like the default of trailing 12 months, but we should have a way to page back and forth.  Open to suggestions.
   **FIXED** — trailing-12 default kept; new ◀ / ▶ pager shifts the window a
   month at a time with the current range shown between (e.g. "Aug 2025 – Jul
   2026"), plus a "Latest" reset that appears when paged back. Works with the
   existing period presets, category detail expansion, and browser
   back/forward (hx-push-url); malformed/future `end=` params fall back to
   latest.
6. We have too many many tabs on the top bar.  We need to find a better, more task oriented place to put some pages.  Maybe just iconic buttons for some things like import?  I'm open to a number of ideas.  Maybe a dual tiered tab structure?  We should think about options that support a user's journey and best practices for UX
7. The locked/unlocked control is only relevant for certain pages.  What should we do with it?  Should it only exist on the pages where it's relevant?  I feel we would still want a feeling of continuity between the pages that make use of it.  Or maybe the concept is not ideal in the first place?  It's nice to be able to switch between the modes, it sort of supports the "spreadsheet" ux experience on the accounts / budget / assets pages.
8. Does it make any sense to make the spending categories user defined?  Start with a basic set but allow them to be removed / changed / added to?  What about account types?  Should we do the same?
9. Reserve column on account page needs to be editable for at least checking and savings, probably wallet too?
   **FIXED** — Reserve (minimum_balance) is now inline-editable for checking,
   savings, wallet, and digital_wallet; stays read-only for credit cards,
   loans, gift cards, and other.
10. Is the account auto picker functionality on the import page ever going to work?  Maybe if no matching account is auto picked when the import file is chosen, we just leave the account dropdown alone.  Just in case someone chose the account before providing the file?
    **FIXED** — it now actually matches: institution (case-insensitive) +
    account type, narrowed by the OFX account number's last 4 digits appearing
    in the account name. It only auto-selects on exactly one confident
    candidate; otherwise your existing selection is preserved (it used to be
    unconditionally reset to "Select account...").
11. Search transactions by amount?  Would be helpful in correlating certain transactions like transfers.  Some sort of fuzzy search?
12. Merchants and Transactions lists edit is very clunky / slow.  CLick far right, mouse to far left, make change, save, repeat.  Maybe these should use a display and editing system similar to the "spreadsheet" style we see on the accounts/budget/assts pages?
13. Is there a way to get historical balances out of empower or fidelity for 401k accounts?  This would support the projections view, I think.
14. Do we have a feature for flagging deviations from expectations for spending categories / merchants?  This could be extended into a budget check feature.  Some real design work / collaboration is needed here.
15. In the spreadsheet views, it's hard to see when the sheet is scrolled to the top or bottom.  When the sheet is not at the top, we should have a subtle drop shadow on the sheet under the header row, and the opposite for the bottom.  When not at the bottom, there should be a subtle drop shadow on the sheet above the total row
16. The quick totals in the ui tabs are not always visible, and as we move between tabs, the tab row grows and shrinks vertically depending on which tab is selected (did it come from the finances app or the spending app)

Tracebacks from failed import categorization: **FIXED** — classifier now
chunks merchants into batches of 40, raised max_tokens 1024→4096, detects
truncated (`stop_reason == "max_tokens"`) and malformed JSON responses per
batch, and caches each batch's successes immediately so one bad batch no
longer loses the whole import. Partial failures surface as a "Classified X
of Y" warning.

127.0.0.1 - - [16/Jul/2026 16:54:56] "POST /s/test/import/detect-account HTTP/1.1" 200 -
Classification failed unexpectedly
Traceback (most recent call last):
  File "/Users/mikegauthiere/git/github.com/mrdefenestrator/fintrack/fintrack/ledger/classifier.py", line 104, in classify_and_cache
    classifications = classify_merchants(uncached, category_names)
  File "/Users/mikegauthiere/git/github.com/mrdefenestrator/fintrack/fintrack/ledger/classifier.py", line 62, in classify_merchants
    classifications = json.loads(response.content[0].text)
  File "/Users/mikegauthiere/.local/share/uv/python/cpython-3.14.2-macos-aarch64-none/lib/python3.14/json/__init__.py", line 352, in loads
    return _default_decoder.decode(s)
           ~~~~~~~~~~~~~~~~~~~~~~~^^^
  File "/Users/mikegauthiere/.local/share/uv/python/cpython-3.14.2-macos-aarch64-none/lib/python3.14/json/decoder.py", line 345, in decode
    obj, end = self.raw_decode(s, idx=_w(s, 0).end())
               ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/mikegauthiere/.local/share/uv/python/cpython-3.14.2-macos-aarch64-none/lib/python3.14/json/decoder.py", line 361, in raw_decode
    obj, end = self.scan_once(s, idx)
               ~~~~~~~~~~~~~~^^^^^^^^
json.decoder.JSONDecodeError: Unterminated string starting at: line 1 column 3501 (char 3500)


127.0.0.1 - - [16/Jul/2026 16:47:26] "POST /s/test/import/detect-account HTTP/1.1" 200 -
Classification failed unexpectedly
Traceback (most recent call last):
  File "/Users/mikegauthiere/git/github.com/mrdefenestrator/fintrack/fintrack/ledger/classifier.py", line 104, in classify_and_cache
    classifications = classify_merchants(uncached, category_names)
  File "/Users/mikegauthiere/git/github.com/mrdefenestrator/fintrack/fintrack/ledger/classifier.py", line 62, in classify_merchants
    classifications = json.loads(response.content[0].text)
  File "/Users/mikegauthiere/.local/share/uv/python/cpython-3.14.2-macos-aarch64-none/lib/python3.14/json/__init__.py", line 352, in loads
    return _default_decoder.decode(s)
           ~~~~~~~~~~~~~~~~~~~~~~~^^^
  File "/Users/mikegauthiere/.local/share/uv/python/cpython-3.14.2-macos-aarch64-none/lib/python3.14/json/decoder.py", line 345, in decode
    obj, end = self.raw_decode(s, idx=_w(s, 0).end())
               ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/mikegauthiere/.local/share/uv/python/cpython-3.14.2-macos-aarch64-none/lib/python3.14/json/decoder.py", line 363, in raw_decode
    raise JSONDecodeError("Expecting value", s, err.value) from None
json.decoder.JSONDecodeError: Expecting value: line 1 column 3025 (char 3024)
