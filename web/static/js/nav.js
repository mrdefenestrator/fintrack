// Group-tab memory for the two-tier navigation (see base.html nav comment).
//
// The Finances / Spending group tabs are plain full-page links whose
// server-rendered hrefs point at each group's default landing page
// (Finances -> Accounts, Spending -> Transactions). This script remembers
// the last-visited sub-tab per group for the session (sessionStorage, keyed
// by snapshot filename + group) and rewrites the group-tab hrefs to it, so
// clicking a group tab returns to where you last were within that group.
//
// The `edit` query param is deliberately stripped when storing and re-applied
// from the server-rendered href when rewriting, so edit-mode carry-through
// stays exactly the server's decision (finances links carry edit=1 while in
// edit mode; spending links never do).
(function () {
    var nav = document.querySelector('[data-nav]');
    if (!nav || !window.sessionStorage) return;

    var file = nav.getAttribute('data-nav-file') || '';
    var group = nav.getAttribute('data-nav-active-group');

    function key(g) {
        return 'fintrack.nav.' + file + '.' + g;
    }

    // (a) Record the current page as this group's last-visited sub-tab.
    if (group) {
        try {
            var params = new URLSearchParams(window.location.search);
            params.delete('edit');
            var qs = params.toString();
            sessionStorage.setItem(
                key(group),
                window.location.pathname + (qs ? '?' + qs : '')
            );
        } catch (e) {
            /* storage unavailable (private mode etc.) — keep defaults */
        }
    }

    // (b) Rewrite each group tab's href to the stored last sub-tab.
    nav.querySelectorAll('a[data-nav-group]').forEach(function (a) {
        var g = a.getAttribute('data-nav-group');
        var stored;
        try {
            stored = sessionStorage.getItem(key(g));
        } catch (e) {
            return;
        }
        if (!stored) return;
        var editOn = new URLSearchParams(a.search).get('edit') === '1';
        var url = stored;
        if (editOn) {
            url += (url.indexOf('?') === -1 ? '?' : '&') + 'edit=1';
        }
        a.setAttribute('href', url);
    });
})();
