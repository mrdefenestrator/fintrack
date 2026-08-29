/**
 * Drag-to-reorder rows on the editable Finances sheets (QA item 3).
 *
 * Any <tbody data-reorder-url="..."> (present only in edit mode) becomes
 * drag-sortable via SortableJS. Rows are dragged by the `.drag-handle` in the
 * actions cell; only rows carrying `data-reorder-index` are draggable, so the
 * add-row and total-row stay put.
 *
 * On drop, the new order of the rows' `data-reorder-index` values (a
 * permutation of their current 0-based positions) is POSTed to the reorder
 * endpoint, which rewrites sort_order. The server returns 204 and the DOM is
 * already in the new order, so we just renumber `data-reorder-index` 0..n-1 to
 * keep it in sync for any subsequent drag (no page reload needed).
 *
 * Dragging is disabled while a column sort is active (the handle is also hidden
 * via CSS) so manual order and column sorting don't fight.
 */
(function () {
    "use strict";
    if (typeof Sortable === "undefined") return;

    // Dragging is only valid when the rows shown are the full, canonical order.
    // A column sort (data-sort-dir) or an active filter (data-reorder-locked,
    // set server-side when some rows are hidden) both make the visible row
    // positions not match the stored order, so reordering must be disabled.
    function reorderDisabled(tbody) {
        // Grouped Holdings sorts per data-tbody (holdings-sort.js sets
        // data-sorted on the sorted group's tbody); a sorted group's DOM order
        // no longer matches its stored order, so its reorder must be off.
        if (tbody.hasAttribute("data-sorted")) return true;
        var table = tbody.closest("table");
        if (!table) return false;
        var dir = table.getAttribute("data-sort-dir");
        return dir === "asc" || dir === "desc" || table.hasAttribute("data-reorder-locked");
    }

    function renumber(tbody) {
        tbody.querySelectorAll("[data-reorder-index]").forEach(function (tr, i) {
            tr.setAttribute("data-reorder-index", i);
        });
    }

    function postOrder(tbody) {
        var url = tbody.getAttribute("data-reorder-url");
        if (!url) return;
        var order = Array.prototype.map.call(
            tbody.querySelectorAll("[data-reorder-index]"),
            function (tr) { return tr.getAttribute("data-reorder-index"); }
        );
        var body = "order=" + encodeURIComponent(order.join(","));
        fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body,
        })
            .then(function (r) {
                if (r.ok) {
                    renumber(tbody);
                } else {
                    window.location.reload();
                }
            })
            .catch(function () { window.location.reload(); });
    }

    function init(tbody) {
        if (tbody._rowReorder) {
            tbody._rowReorder.option("disabled", reorderDisabled(tbody));
            return;
        }
        tbody._rowReorder = Sortable.create(tbody, {
            handle: ".drag-handle",
            draggable: "[data-reorder-index]",
            // Use SortableJS's own pointer-driven dragging instead of native
            // HTML5 drag-and-drop. Native DnD is unreliable on iOS Safari — a
            // touch on the handle is arbitrated between drag and scroll, so
            // reorder "works sometimes, then stops." The fallback path owns the
            // pointer from pointerdown on the handle (paired with
            // `touch-action: none` on .drag-handle in base.html), which is
            // deterministic across desktop and mobile Safari. It's also what
            // lets Playwright drive the drag in WebKit for the e2e test.
            forceFallback: true,
            fallbackClass: "row-reorder-fallback",
            // Ignore a tiny move so a tap on the handle isn't read as a drag.
            fallbackTolerance: 4,
            // Keep dragged rows within the contiguous data block — don't cross a
            // non-draggable row (group header / add-row / subtotal). Matters for
            // the grouped Holdings sheet, where those share the group's tbody.
            onMove: function (evt) {
                return !!(evt.related && evt.related.hasAttribute("data-reorder-index"));
            },
            // No swap animation: animating <tr> transforms makes rows visually
            // "bunch up" / overlap when you drag fast over several rows (the
            // slide of one swap hasn't finished before the next begins). Instant
            // swaps keep the sheet legible while dragging.
            animation: 0,
            disabled: reorderDisabled(tbody),
            ghostClass: "row-reorder-ghost",
            chosenClass: "row-reorder-chosen",
            // Keep drags within the data rows — never drop past the grid filler,
            // add row, or total row (QA item 17).
            onMove: function (evt) {
                var r = evt.related;
                return !(
                    r.classList.contains("sheet-grid-filler") ||
                    r.hasAttribute("data-add-row") ||
                    r.classList.contains("total-row")
                );
            },
            onEnd: function () { postOrder(tbody); },
        });
    }

    function scan() {
        document.querySelectorAll("[data-reorder-url]").forEach(init);
    }

    document.addEventListener("DOMContentLoaded", scan);
    // Cell edits swap the tbody's innerHTML in place (the <tbody> element and
    // its Sortable instance persist); re-scan keeps the disabled state current.
    document.addEventListener("htmx:afterSwap", scan);
    document.addEventListener("htmx:afterSettle", scan);
    // A per-group Holdings sort toggles that group's reorder on/off.
    document.addEventListener("holdings:sorted", scan);

    if (document.readyState !== "loading") scan();
})();
