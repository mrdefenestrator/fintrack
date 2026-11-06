/**
 * Per-band column sorting for grouped sheets (Holdings, Budget, Projections —
 * see templates/partials/grouped_sheet.html), and for flat (unbanded) sheets
 * (Transactions, Merchants, Categories, Budget vs Actual), whose one
 * `tbody.sheet-group--flat` is sorted from the header row in the table's
 * <thead>.
 *
 * Each band is one <tbody class="sheet-group" data-group="..."> containing a
 * heading, a header row (`.sheet-group-header` with `.sortable-th` cells), the
 * data rows (carrying data-reorder-index), and an add-row. Clicking a header
 * cell sorts only that band's data rows (asc -> desc -> none), re-inserting them
 * ahead of the add-row so the heading and header stay put. A cell's
 * `data-sort-value` (e.g. Budget's recurrence rank) overrides its text. State is
 * persisted in the URL (sort_<group>_col / sort_<group>_dir) so it survives
 * reloads and table swaps. Data rows are the band's `.sheet-group-row`s (and
 * any carrying data-reorder-index); a `data-detail-row` right after a data row
 * belongs to it and moves with it. While a band is sorted its tbody gets `data-sorted`,
 * which disables that band's drag-reorder (see row-reorder.js).
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

    function dataRows(groupTbody) {
        var rows = Array.prototype.slice.call(
            groupTbody.querySelectorAll(
                ":scope > tr[data-reorder-index], :scope > tr.sheet-group-row"
            )
        );
        rows.forEach(function (r) {
            var next = r.nextElementSibling;
            r._detailRow = next && next.hasAttribute("data-detail-row") ? next : null;
        });
        return rows;
    }

    // The band's own header row, or the shared one in <thead>.
    function headerRowFor(groupTbody) {
        var own = groupTbody.querySelector(":scope > .sheet-group-header");
        if (own) return own;
        // A flat sheet — or a banded one whose bands share one header (Budget) —
        // sorts from the header row in <thead>; with a shared header, one click
        // sorts every band by that column.
        var table = groupTbody.closest("table");
        return table && table.tHead
            ? table.tHead.querySelector(".sheet-group-header")
            : null;
    }

    function rowSortText(row, key) {
        var col = parseInt((key || "").slice(1), 10);
        var cell = row.cells[col];
        if (!cell) return undefined;
        var v = cell.getAttribute("data-sort-value");
        return v !== null ? v : cell.textContent;
    }

    function apply(groupTbody, key, dir) {
        if (!groupTbody._origOrder) groupTbody._origOrder = dataRows(groupTbody);
        var rows = groupTbody._origOrder.slice();
        if (dir === "asc" || dir === "desc") {
            rows.sort(function (a, b) {
                var va = parseSortValue(rowSortText(a, key));
                var vb = parseSortValue(rowSortText(b, key));
                var cmp;
                if (!isNaN(va.num) && !isNaN(vb.num)) cmp = va.num - vb.num;
                else cmp = (va.str || "").localeCompare(vb.str || "", undefined, { numeric: true });
                return dir === "asc" ? cmp : -cmp;
            });
            groupTbody.setAttribute("data-sorted", "1");
        } else {
            groupTbody.removeAttribute("data-sorted");
        }
        // Re-insert the (sorted) data rows before the add-row (or at the end)
        // so the heading and header rows stay in place.
        var anchor = groupTbody.querySelector(":scope > [data-add-row]");
        rows.forEach(function (r) {
            groupTbody.insertBefore(r, anchor);
            if (r._detailRow) groupTbody.insertBefore(r._detailRow, anchor);
        });
        document.dispatchEvent(new CustomEvent("sheet:group-sorted"));
    }

    function updateIndicators(headerRow, key, dir) {
        headerRow.querySelectorAll(".sortable-th").forEach(function (th) {
            var thKey = th.getAttribute("data-sort-key");
            var ind = th.querySelector(".sort-indicator");
            if (ind) ind.textContent = thKey === key && dir !== "none" ? (dir === "asc" ? " ↑" : " ↓") : "";
        });
    }

    function writeUrl(group, key, dir) {
        var url = new URL(window.location);
        if (dir === "none") {
            url.searchParams.delete("sort_" + group + "_col");
            url.searchParams.delete("sort_" + group + "_dir");
        } else {
            url.searchParams.set("sort_" + group + "_col", key);
            url.searchParams.set("sort_" + group + "_dir", dir);
        }
        window.history.replaceState({}, "", url);
    }

    function readUrl(group) {
        var params = new URL(window.location).searchParams;
        var key = params.get("sort_" + group + "_col");
        var dir = params.get("sort_" + group + "_dir");
        if (!/^c\d+$/.test(key || "") || (dir !== "asc" && dir !== "desc")) return null;
        return { key: key, dir: dir };
    }

    function init(groupTbody) {
        var group = groupTbody.getAttribute("data-group");
        var headerRow = headerRowFor(groupTbody);
        if (!headerRow) return;

        // Re-apply a persisted sort (fresh load, or after a tbody swap).
        var saved = readUrl(group);
        if (saved) {
            groupTbody._sortKey = saved.key;
            groupTbody._sortDir = saved.dir;
            apply(groupTbody, saved.key, saved.dir);
            updateIndicators(headerRow, saved.key, saved.dir);
        }

        if (groupTbody._groupSort) return;
        groupTbody._groupSort = true;
        headerRow.querySelectorAll(".sortable-th").forEach(function (th) {
            var key = th.getAttribute("data-sort-key");
            if (!key) return;
            function doSort() {
                var dir;
                if (groupTbody._sortKey !== key) dir = "asc";
                else if (groupTbody._sortDir === "asc") dir = "desc";
                else if (groupTbody._sortDir === "desc") dir = "none";
                else dir = "asc";
                groupTbody._sortKey = key;
                groupTbody._sortDir = dir;
                apply(groupTbody, key, dir);
                updateIndicators(headerRow, key, dir);
                writeUrl(group, key, dir);
            }
            th.addEventListener("click", doSort);
            th.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); doSort(); }
            });
        });
    }

    function scan() {
        document.querySelectorAll("tbody.sheet-group").forEach(init);
    }

    document.addEventListener("DOMContentLoaded", scan);
    document.addEventListener("htmx:afterSwap", function () {
        document.querySelectorAll("tbody.sheet-group").forEach(function (t) { delete t._origOrder; });
        scan();
    });
    if (document.readyState !== "loading") scan();
})();
