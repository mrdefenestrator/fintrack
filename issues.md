1. The UNIQ constraint on simply account name makes the accounts annoying to use.  We should be able to have a Wallet from venmo named wallet and a wallet from paypal named wallet without problems.
2. We need to gracefully handle save errors and surface these to the user within the accounts spreadsheet.  For example, the UNIQ constraint error printed a traceback in the logs and made the web ui unpresponsive until I changes to a distinct account name.
3. The account types on the import page for quick account creation should match all the account types in the accounts page.  We should do the same on the account picker on the transactions page, and anywhere else we have a similar input.
4. The account drop down selector should give more than just the account name.  It should definitely have the institution name and maybe the account type.  Just a thought "{Institution} [{Type}] {Name}"
5. The top controls on the trends page needs to be reworked.  I like the default of trailing 12 months, but we should have a way to page back and forth.  Open to suggestions.
6. We have too many many tabs on the top bar.  We need to find a better, more task oriented place to put some pages.  Maybe just iconic buttons for some things like import?  I'm open to a number of ideas.  Maybe a dual tiered tab structure?  We should think about options that support a user's journey and best practices for UX
7. The locked/unlocked control is only relevant for certain pages.  What should we do with it?  Should it only exist on the pages where it's relevant?  I feel we would still want a feeling of continuity between the pages that make use of it.  Or maybe the concept is not ideal in the first place?  It's nice to be able to switch between the modes, it sort of supports the "spreadsheet" ux experience on the accounts / budget / assets pages.
8. Does it make any sense to make the spending categories user defined?  Start with a basic set but allow them to be removed / changed / added to?  What about account types?  Should we do the same?
9. Reserve column on account page needs to be editable for at least checking and savings, probably wallet too?
10. Is the account auto picker functionality on the import page ever going to work?  Maybe if no matching account is auto picked when the import file is chosen, we just leave the account dropdown alone.  Just in case someone chose the account before providing the file?
11. Search transactions by amount?  Would be helpful in correlating certain transactions like transfers.  Some sort of fuzzy search?
12. Merchants and Transactions lists edit is very clunky / slow.  CLick far right, mouse to far left, make change, save, repeat.  Maybe these should use a display and editing system similar to the "spreadsheet" style we see on the accounts/budget/assts pages?

Tracebacks from failed import categorization:

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
