/**
 * Per-group column sorting for the grouped Holdings table.
 *
 * The sheet is one <table> with several <tbody>s. A header <tbody> carries
 * data-sort-target="<id of the group's data tbody>"; clicking a `.sortable-th`
 * in it sorts that data tbody's rows (asc -> desc -> none), independently of the
 * other group. While a group is sorted its data tbody gets `data-sorted`, which
 * disables drag-reorder for that group (see row-reorder.js) so the two don't
 * fight. This is deliberately separate from the shared sortable.js, which only
 * handles single-tbody tables.
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

    // Data rows only (skip an empty-state row, which is a single colspan cell).
    function dataRows(tbody) {
        return Array.prototype.filter.call(tbody.children, function (tr) {
            return tr.hasAttribute("data-reorder-index") || (tr.cells && tr.cells.length > 1);
        });
    }

    function apply(dataTbody, col, dir) {
        if (!dataTbody._origOrder) dataTbody._origOrder = dataRows(dataTbody);
        var rows = dataTbody._origOrder.slice();
        if (dir === "asc" || dir === "desc") {
            rows.sort(function (a, b) {
                var va = parseSortValue(a.cells[col] && a.cells[col].textContent);
                var vb = parseSortValue(b.cells[col] && b.cells[col].textContent);
                var cmp;
                if (!isNaN(va.num) && !isNaN(vb.num)) cmp = va.num - vb.num;
                else cmp = (va.str || "").localeCompare(vb.str || "", undefined, { numeric: true });
                return dir === "asc" ? cmp : -cmp;
            });
            dataTbody.setAttribute("data-sorted", "1");
        } else {
            dataTbody.removeAttribute("data-sorted");
        }
        rows.forEach(function (r) { dataTbody.appendChild(r); });
        // Let row-reorder.js refresh its disabled state for this group.
        document.dispatchEvent(new CustomEvent("holdings:sorted"));
    }

    function updateIndicators(headerTbody, col, dir) {
        headerTbody.querySelectorAll(".sortable-th").forEach(function (th, i) {
            var ind = th.querySelector(".sort-indicator");
            if (ind) ind.textContent = i === col && dir !== "none" ? (dir === "asc" ? " ↑" : " ↓") : "";
        });
    }

    // Sort state is persisted per group in the URL as sort_<group>_col /
    // sort_<group>_dir, so it survives reloads and tbody swaps (cell edits).
    function writeUrl(group, col, dir) {
        var url = new URL(window.location);
        if (dir === "none") {
            url.searchParams.delete("sort_" + group + "_col");
            url.searchParams.delete("sort_" + group + "_dir");
        } else {
            url.searchParams.set("sort_" + group + "_col", col);
            url.searchParams.set("sort_" + group + "_dir", dir);
        }
        window.history.replaceState({}, "", url);
    }

    function readUrl(group) {
        var params = new URL(window.location).searchParams;
        var col = parseInt(params.get("sort_" + group + "_col"), 10);
        var dir = params.get("sort_" + group + "_dir");
        if (isNaN(col) || (dir !== "asc" && dir !== "desc")) return null;
        return { col: col, dir: dir };
    }

    function init(headerTbody) {
        var group = headerTbody.getAttribute("data-group");
        var dataTbody = document.getElementById(headerTbody.getAttribute("data-sort-target"));
        if (!dataTbody) return;

        // Re-apply a persisted sort (fresh load, or after a tbody swap replaced
        // these elements).
        var saved = readUrl(group);
        if (saved) {
            headerTbody._sortCol = saved.col;
            headerTbody._sortDir = saved.dir;
            apply(dataTbody, saved.col, saved.dir);
            updateIndicators(headerTbody, saved.col, saved.dir);
        }

        if (headerTbody._holdingsSort) return;
        headerTbody._holdingsSort = true;
        headerTbody.querySelectorAll(".sortable-th").forEach(function (th) {
            var col = parseInt(th.getAttribute("data-col"), 10);
            if (isNaN(col)) return;
            function doSort() {
                var dir;
                if (headerTbody._sortCol !== col) dir = "asc";
                else if (headerTbody._sortDir === "asc") dir = "desc";
                else if (headerTbody._sortDir === "desc") dir = "none";
                else dir = "asc";
                headerTbody._sortCol = col;
                headerTbody._sortDir = dir;
                apply(dataTbody, col, dir);
                updateIndicators(headerTbody, col, dir);
                writeUrl(group, col, dir);
            }
            th.addEventListener("click", doSort);
            th.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); doSort(); }
            });
        });
    }

    function scan() {
        document.querySelectorAll("[data-sort-target]").forEach(init);
    }

    document.addEventListener("DOMContentLoaded", scan);
    // Cell edits swap the whole table body; the tbodies are new elements, so drop
    // any cached original order and re-wire.
    document.addEventListener("htmx:afterSwap", scan);
    if (document.readyState !== "loading") scan();
})();
