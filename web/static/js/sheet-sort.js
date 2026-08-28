/**
 * Column sorting for the generic sheet renderer (partials/sheet_table.html).
 *
 * Works for both shapes the renderer emits:
 *   - Flat tables: the sortable header cells live in a <thead>; the sort scope
 *     is the table's single <tbody data-group>.
 *   - Grouped tables: each group is a <tbody data-group> whose header row is
 *     `.sheet-group-header` (inside the tbody); each group sorts independently.
 *
 * Clicking a header cell cycles asc -> desc -> none over that scope's data rows
 * (the <tr> carrying an id), re-inserting them ahead of the add-row so the
 * header/footer stay put. State is persisted in the URL, keyed by the table's
 * cell-nav id + the group token, so it survives reloads and the whole-tbody
 * HTMX swaps that inline editing performs — this is what makes those swaps safe
 * for tables that would otherwise lose a client-side sort. A sorted scope gets
 * `data-sorted`, which disables that scope's drag-reorder (row-reorder.js).
 *
 * Disjoint from holdings-sort.js: that binds `.holdings-group-header`; this
 * binds `.sheet-group-header` and flat <thead> cells, so both can coexist while
 * Holdings has not yet been migrated onto this renderer.
 */
(function () {
    "use strict";

    function parseSortValue(text) {
        if (text === undefined || text === null) return { num: NaN, str: "" };
        var s = ("" + text).trim();
        var n = s.replace(/\$/g, "").replace(/,/g, "").replace(/%/g, "").trim();
        var neg = n.startsWith("(") && n.endsWith(")");
        if (neg) n = n.slice(1, -1).trim();
        var num = parseFloat(n);
        if (!isNaN(num) && neg) num = -num;
        return { num: isNaN(num) ? NaN : num, str: s };
    }

    function dataRows(tbody) {
        return Array.prototype.slice.call(
            tbody.querySelectorAll(":scope > tr[id]")
        );
    }

    function rowSortText(row, key) {
        var col = parseInt((key || "").slice(1), 10);
        var cell = row.cells[col];
        if (!cell) return "";
        var sv = cell.getAttribute("data-sort-value");
        return sv !== null ? sv : cell.textContent;
    }

    function apply(tbody, key, dir) {
        if (!tbody._origOrder) tbody._origOrder = dataRows(tbody);
        var rows = tbody._origOrder.slice();
        if (dir === "asc" || dir === "desc") {
            rows.sort(function (a, b) {
                var va = parseSortValue(rowSortText(a, key));
                var vb = parseSortValue(rowSortText(b, key));
                var cmp;
                if (!isNaN(va.num) && !isNaN(vb.num)) cmp = va.num - vb.num;
                else cmp = (va.str || "").localeCompare(vb.str || "", undefined, { numeric: true });
                return dir === "asc" ? cmp : -cmp;
            });
            tbody.setAttribute("data-sorted", "1");
        } else {
            tbody.removeAttribute("data-sorted");
        }
        var anchor = tbody.querySelector(":scope > [data-add-row]");
        rows.forEach(function (r) { tbody.insertBefore(r, anchor); });
        document.dispatchEvent(new CustomEvent("holdings:sorted"));
    }

    function updateIndicators(ths, key, dir) {
        ths.forEach(function (th) {
            var thKey = th.getAttribute("data-sort-key");
            var ind = th.querySelector(".sort-indicator");
            if (ind) ind.textContent = thKey === key && dir !== "none" ? (dir === "asc" ? " ↑" : " ↓") : "";
        });
    }

    function token(tableId, group) {
        return (tableId || "sheet") + "_" + (group || "_");
    }

    function writeUrl(tok, key, dir) {
        var url = new URL(window.location);
        if (dir === "none") {
            url.searchParams.delete("sort_" + tok + "_col");
            url.searchParams.delete("sort_" + tok + "_dir");
        } else {
            url.searchParams.set("sort_" + tok + "_col", key);
            url.searchParams.set("sort_" + tok + "_dir", dir);
        }
        window.history.replaceState({}, "", url);
    }

    function readUrl(tok) {
        var params = new URL(window.location).searchParams;
        var key = params.get("sort_" + tok + "_col");
        var dir = params.get("sort_" + tok + "_dir");
        if (!/^c\d+$/.test(key || "") || (dir !== "asc" && dir !== "desc")) return null;
        return { key: key, dir: dir };
    }

    /** Wire one sort unit: a set of header cells sorting a scope tbody. */
    function wire(tableId, tbody, ths) {
        var group = tbody.getAttribute("data-group");
        var tok = token(tableId, group);

        var saved = readUrl(tok);
        if (saved) {
            tbody._sortKey = saved.key;
            tbody._sortDir = saved.dir;
            apply(tbody, saved.key, saved.dir);
            updateIndicators(ths, saved.key, saved.dir);
        }

        if (tbody._sheetSort) return;
        tbody._sheetSort = true;
        ths.forEach(function (th) {
            var key = th.getAttribute("data-sort-key");
            if (!key || th.hasAttribute("data-sort-disabled")) return;
            function doSort() {
                var dir;
                if (tbody._sortKey !== key) dir = "asc";
                else if (tbody._sortDir === "asc") dir = "desc";
                else if (tbody._sortDir === "desc") dir = "none";
                else dir = "asc";
                tbody._sortKey = key;
                tbody._sortDir = dir;
                apply(tbody, key, dir);
                updateIndicators(ths, key, dir);
                writeUrl(tok, key, dir);
            }
            th.addEventListener("click", doSort);
            th.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); doSort(); }
            });
        });
    }

    function ths(el, selector) {
        return Array.prototype.slice.call(el.querySelectorAll(selector));
    }

    function scan() {
        document.querySelectorAll("table[data-sheet]").forEach(function (table) {
            var tableId = table.id || table.getAttribute("data-cell-nav") || "sheet";
            // Grouped: header rows inside each group tbody.
            var groupBodies = table.querySelectorAll(":scope > tbody:has(> .sheet-group-header)");
            if (groupBodies.length) {
                groupBodies.forEach(function (tbody) {
                    wire(tableId, tbody, ths(tbody, ":scope > .sheet-group-header .sortable-th"));
                });
                return;
            }
            // Flat: a shared <thead>; scope is the single data tbody.
            var head = table.querySelector(":scope > thead");
            var body = table.querySelector(":scope > tbody[data-group]");
            if (head && body) {
                wire(tableId, body, ths(head, ".sortable-th"));
            }
        });
    }

    document.addEventListener("DOMContentLoaded", scan);
    document.addEventListener("htmx:afterSwap", function () {
        document.querySelectorAll("table[data-sheet] tbody[data-group]").forEach(function (t) {
            delete t._origOrder;
            delete t._sheetSort;
        });
        scan();
    });
    if (document.readyState !== "loading") scan();
})();
