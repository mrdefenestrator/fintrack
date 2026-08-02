/**
 * Per-group column sorting for the grouped Holdings table.
 *
 * Each group is one <tbody data-group="..."> containing a heading, a header row
 * (`.holdings-group-header` with `.sortable-th` cells), the data rows (carrying
 * data-reorder-index), an add-row, and a subtotal. Clicking a header cell sorts
 * only that group's data rows (asc -> desc -> none), re-inserting them ahead of
 * the add-row/subtotal so the header and footer stay put. State is persisted in
 * the URL (sort_<group>_col / sort_<group>_dir) so it survives reloads and tbody
 * swaps. While a group is sorted its tbody gets `data-sorted`, which disables
 * that group's drag-reorder (see row-reorder.js).
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
        return Array.prototype.slice.call(
            groupTbody.querySelectorAll(":scope > tr[data-reorder-index]")
        );
    }

    function rowSortText(row, key) {
        var col = parseInt((key || "").slice(1), 10);
        return row.cells[col] && row.cells[col].textContent;
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
        // Re-insert the (sorted) data rows before the add-row / subtotal so the
        // header and footer rows stay in place.
        var anchor = groupTbody.querySelector(":scope > [data-add-row]")
            || groupTbody.querySelector(":scope > .holdings-subtotal");
        rows.forEach(function (r) { groupTbody.insertBefore(r, anchor); });
        document.dispatchEvent(new CustomEvent("holdings:sorted"));
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
        var headerRow = groupTbody.querySelector(".holdings-group-header");
        if (!headerRow) return;

        // Re-apply a persisted sort (fresh load, or after a tbody swap).
        var saved = readUrl(group);
        if (saved) {
            groupTbody._sortKey = saved.key;
            groupTbody._sortDir = saved.dir;
            apply(groupTbody, saved.key, saved.dir);
            updateIndicators(headerRow, saved.key, saved.dir);
        }

        if (groupTbody._holdingsSort) return;
        groupTbody._holdingsSort = true;
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
        document.querySelectorAll("tbody[data-group]").forEach(init);
    }

    document.addEventListener("DOMContentLoaded", scan);
    document.addEventListener("htmx:afterSwap", function () {
        document.querySelectorAll("tbody[data-group]").forEach(function (t) { delete t._origOrder; });
        scan();
    });
    if (document.readyState !== "loading") scan();
})();
