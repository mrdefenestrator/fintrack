/**
 * Global header keyboard shortcuts.
 *
 *   ⌘E / Ctrl+E  -> toggle edit mode (submits the header lock/unlock form).
 *                   Only acts on pages that honor edit mode, i.e. where the
 *                   toggle form is actually rendered.
 *   ⌘I / Ctrl+I  -> open the Import page (follows the header Import link).
 *
 * We preventDefault so the browser's own ⌘E/⌘I (e.g. Safari's "Use Selection
 * for Find" / "Email This Page") don't also fire. Both intentionally work even
 * while a cell editor is focused, so you can flip modes without clicking out.
 */
(function () {
    "use strict";

    document.addEventListener("keydown", function (e) {
        // Require exactly the platform command modifier (⌘ on mac, Ctrl
        // elsewhere) with no Alt/Shift, so we never shadow richer chords.
        if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;

        var key = e.key.toLowerCase();

        if (key === "e") {
            var form = document.querySelector("[data-edit-toggle]");
            if (!form) return; // page doesn't honor edit mode
            e.preventDefault();
            if (form.requestSubmit) form.requestSubmit();
            else form.submit();
        } else if (key === "i") {
            var link = document.querySelector("[data-import-link]");
            if (!link) return;
            e.preventDefault();
            window.location.href = link.href;
        }
    });
})();
