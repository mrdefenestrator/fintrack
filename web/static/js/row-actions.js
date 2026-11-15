/**
 * Row actions on the editable sheets (issue #148): partials/row_actions.html
 * renders each row's [ grip ][ ⋯ ] actions cell; styles in grouped_sheet.css
 * "Row actions". Two parts, independent of each other:
 *
 * 1. Reorder. Any <tbody data-reorder-url="..."> (edit mode only) reorders by
 *    its rows' `.drag-handle` grips — dragged with SortableJS (mouse or
 *    touch), or focused and moved with ↑/↓. Only rows carrying
 *    `data-reorder-index` move, and only among themselves, so band headings,
 *    detail rows, the add row and the total row stay put. After a move the
 *    rows' `data-reorder-index` values (a permutation of their stored
 *    positions) are POSTed to the reorder endpoint, which rewrites sort_order
 *    and answers 204; the DOM is already in the new order, so the indexes are
 *    renumbered 0..n-1 right away and the POSTs are queued, so quick
 *    successive moves each send an order relative to the one before. A failed
 *    POST reloads the page (the server's order wins). Reordering is off while
 *    the rows shown aren't the stored order: a column sort, a sorted band, or
 *    a filter hiding rows (data-reorder-locked); the grip is hidden then too.
 *
 * 2. Menu. ⋯ opens the row's menu, a native popover (top layer, light
 *    dismiss, Esc). This anchors it beside the button — below, or above when
 *    there's no room — closes it when the sheet scrolls or the window resizes
 *    (it would detach from its row), moves focus with ↑/↓/Home/End, and closes
 *    it once an item is chosen (the item's htmx request goes on). Delete's
 *    in-place confirm focuses ✗; cancelling it focuses ⋯ again.
 */
(function () {
    "use strict";

    // ---- Reorder -----------------------------------------------------------

    // Dragging is only valid when the rows shown are the full, canonical order.
    function reorderDisabled(tbody) {
        // Grouped sheets sort per band (group-sort.js sets data-sorted on the
        // sorted band's tbody); its DOM order no longer matches its stored order.
        if (tbody.hasAttribute("data-sorted")) return true;
        var table = tbody.closest("table");
        if (!table) return false;
        var dir = table.getAttribute("data-sort-dir");
        return dir === "asc" || dir === "desc" || table.hasAttribute("data-reorder-locked");
    }

    // A row's detail row (Budget's link suggestions: `data-detail-row` with
    // `data-detail-of` naming the row's id) doesn't move by itself, so a move
    // can leave it behind or land between a row and its detail; put each back
    // right under its row.
    function reattachDetailRows(tbody) {
        tbody.querySelectorAll("tr[data-detail-of]").forEach(function (detail) {
            var owner = document.getElementById(detail.getAttribute("data-detail-of"));
            if (owner && owner.parentNode === tbody) {
                tbody.insertBefore(detail, owner.nextSibling);
            }
        });
    }

    function postOrder(tbody) {
        var url = tbody.getAttribute("data-reorder-url");
        if (!url) return;
        var rows = tbody.querySelectorAll("[data-reorder-index]");
        var order = Array.prototype.map.call(rows, function (tr) {
            return tr.getAttribute("data-reorder-index");
        });
        // The DOM is now the stored order: renumber before the next move reads it.
        rows.forEach(function (tr, i) { tr.setAttribute("data-reorder-index", i); });
        var body = "order=" + encodeURIComponent(order.join(","));
        tbody._reorderQueue = (tbody._reorderQueue || Promise.resolve())
            .then(function () {
                return fetch(url, {
                    method: "POST",
                    headers: { "Content-Type": "application/x-www-form-urlencoded" },
                    body: body,
                });
            })
            .then(function (r) { if (!r.ok) throw new Error(r.status); })
            .catch(function () { window.location.reload(); });
    }

    function initSortable(tbody) {
        if (typeof Sortable === "undefined") return;  // drag only; keys still work
        if (tbody._rowReorder) {
            tbody._rowReorder.option("disabled", reorderDisabled(tbody));
            return;
        }
        tbody._rowReorder = Sortable.create(tbody, {
            handle: ".drag-handle",
            draggable: "[data-reorder-index]",
            // Only ever land among the band's own data rows — never past a band
            // heading, the grid filler, the add row or the total row.
            onMove: function (evt) {
                return !!(evt.related && evt.related.hasAttribute("data-reorder-index"));
            },
            // No swap animation: animating <tr> transforms makes rows "bunch
            // up" when you drag fast over several rows.
            animation: 0,
            disabled: reorderDisabled(tbody),
            ghostClass: "row-reorder-ghost",
            chosenClass: "row-reorder-chosen",
            onEnd: function (evt) {
                reattachDetailRows(tbody);
                if (evt.oldIndex !== evt.newIndex) postOrder(tbody);
            },
        });
    }

    function scan() {
        document.querySelectorAll("[data-reorder-url]").forEach(initSortable);
    }

    // ↑/↓ on a focused grip moves its row one place within its band.
    document.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
        var grip = e.target.closest && e.target.closest(".drag-handle");
        if (!grip) return;
        var tr = grip.closest("tr[data-reorder-index]");
        var tbody = tr && tr.closest("tbody[data-reorder-url]");
        if (!tbody) return;
        e.preventDefault();
        if (reorderDisabled(tbody)) return;
        var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-reorder-index]"));
        var i = rows.indexOf(tr);
        var j = e.key === "ArrowUp" ? i - 1 : i + 1;
        if (j < 0 || j >= rows.length) return;
        if (j < i) tbody.insertBefore(tr, rows[j]);
        else tbody.insertBefore(tr, rows[j].nextSibling);
        reattachDetailRows(tbody);
        grip.focus();
        tr.classList.remove("row-moved");
        void tr.offsetWidth;  // restart the flash
        tr.classList.add("row-moved");
        postOrder(tbody);
    });

    document.addEventListener("DOMContentLoaded", scan);
    // Cell edits swap the tbody's innerHTML in place (the <tbody> element and
    // its Sortable instance persist); re-scan keeps the disabled state current.
    document.addEventListener("htmx:afterSwap", scan);
    document.addEventListener("htmx:afterSettle", scan);
    // A per-band sort on a grouped sheet toggles that band's reorder on/off.
    document.addEventListener("sheet:group-sorted", scan);
    if (document.readyState !== "loading") scan();

    // ---- Menu --------------------------------------------------------------

    var GAP = 4;     // px between ⋯ and its menu
    var MARGIN = 8;  // px the menu keeps from the viewport edges

    function invoker(menu) {
        return document.querySelector('[popovertarget="' + menu.id + '"]');
    }

    function items(menu) {
        return Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
    }

    // Right-align the menu under ⋯ (the column is at the sheet's right edge);
    // flip above when it won't fit below; keep it inside the viewport.
    function place(menu) {
        var btn = invoker(menu);
        if (!btn) return;
        var b = btn.getBoundingClientRect();
        menu.classList.add("row-menu--anchored");
        var w = menu.offsetWidth;
        var h = menu.offsetHeight;
        var vw = document.documentElement.clientWidth;
        var vh = window.innerHeight;
        var left = Math.min(Math.max(b.right - w, MARGIN), vw - w - MARGIN);
        var top = b.bottom + GAP;
        if (top + h > vh - MARGIN && b.top - GAP - h >= MARGIN) top = b.top - GAP - h;
        top = Math.max(MARGIN, Math.min(top, vh - h - MARGIN));
        menu.style.left = Math.max(MARGIN, left) + "px";
        menu.style.top = top + "px";
    }

    function openMenus() {
        return document.querySelectorAll(".row-menu:popover-open");
    }

    function closeAll() {
        openMenus().forEach(function (m) { m.hidePopover(); });
    }

    document.addEventListener("toggle", function (e) {
        var menu = e.target;
        if (!menu.classList || !menu.classList.contains("row-menu")) return;
        var btn = invoker(menu);
        var open = e.newState === "open";
        if (btn) {
            btn.classList.toggle("is-open", open);
            btn.setAttribute("aria-expanded", open ? "true" : "false");
        }
        if (open) {
            place(menu);
            var first = items(menu)[0];
            if (first) first.focus({ preventScroll: true });
        }
    }, true);

    document.addEventListener("keydown", function (e) {
        var menu = e.target.closest && e.target.closest(".row-menu");
        if (!menu) return;
        var list = items(menu);
        var i = list.indexOf(document.activeElement);
        var next = null;
        if (e.key === "ArrowDown") next = list[(i + 1) % list.length];
        else if (e.key === "ArrowUp") next = list[(i - 1 + list.length) % list.length];
        else if (e.key === "Home") next = list[0];
        else if (e.key === "End") next = list[list.length - 1];
        else if (e.key === "Tab") { menu.hidePopover(); return; }
        if (next) {
            e.preventDefault();
            next.focus();
        }
    });

    // Choosing an item closes the menu; its htmx request carries on. Deferred
    // so htmx sees the click first.
    document.addEventListener("click", function (e) {
        var item = e.target.closest && e.target.closest('.row-menu [role="menuitem"]');
        if (!item) return;
        var menu = item.closest(".row-menu");
        setTimeout(function () {
            if (menu.matches(":popover-open")) menu.hidePopover();
        }, 0);
    });

    // Delete's in-place confirm takes focus on ✗, so a stray Enter cancels;
    // cancelling hands focus back to ⋯. Done on htmx:load — after htmx has
    // wired the new buttons — not with autofocus, which can land before the
    // button answers a key.
    document.addEventListener("htmx:load", function (e) {
        var el = e.detail.elt;
        if (!el.classList || !el.classList.contains("row-actions-cell")) return;
        if (el.classList.contains("row-actions-cell--confirm")) {
            var cancel = el.querySelector("[data-row-cancel]");
            if (cancel) cancel.focus();
        } else if (!document.activeElement || document.activeElement === document.body) {
            var btn = el.querySelector(".row-menu-btn");
            if (btn) btn.focus();
        }
    });

    // An open menu would float off its row once the sheet moves under it.
    document.addEventListener("scroll", function (e) {
        if (e.target.closest && e.target.closest(".row-menu")) return;
        closeAll();
    }, true);
    window.addEventListener("resize", closeAll);
    // A swap that replaces the row (or the sheet) takes the menu with it.
    document.addEventListener("htmx:beforeSwap", closeAll);
})();
