/**
 * Spreadsheet keyboard navigation for the Holdings edit grid.
 *
 *   Tab / Shift+Tab   -> next / previous editable cell (right / left, wrapping
 *                        onto the adjacent row)
 *   Enter / Shift+Enter -> down / up the same column
 *
 * Each move first saves the cell being edited (a POST that re-renders the whole
 * table body into #holdings-table), then, on the resulting swap, clicks the next
 * editable cell — which re-enters edit mode there (the cell-edit input autofocuses).
 *
 * We drive the save ourselves (htmx.ajax) and stopImmediatePropagation so the
 * cell's own hx-trigger doesn't also fire (no double save). Editable display
 * cells are the ones carrying an hx-get to the holdings cell-edit route.
 */
(function () {
    "use strict";
    if (typeof htmx === "undefined") return;

    var pending = null;

    function isEditField(el) {
        return (
            el &&
            el.classList &&
            (el.classList.contains("table-cell-input") ||
                el.classList.contains("table-cell-select"))
        );
    }

    function isEditable(td) {
        return td && td.matches && td.matches('td[hx-get*="/holdings/cell/"]');
    }

    function dataRows() {
        return Array.prototype.slice.call(
            document.querySelectorAll("#holdings-table tr[data-reorder-index]")
        );
    }

    function firstEditable(row, fromEnd) {
        var cells = row.children;
        if (fromEnd) {
            for (var i = cells.length - 1; i >= 0; i--) if (isEditable(cells[i])) return cells[i];
        } else {
            for (var j = 0; j < cells.length; j++) if (isEditable(cells[j])) return cells[j];
        }
        return null;
    }

    function findNext(rowId, col, dir) {
        var row = document.getElementById(rowId);
        if (!row) return null;

        if (dir === "right" || dir === "left") {
            var step = dir === "right" ? 1 : -1;
            var cells = row.children;
            for (var i = col + step; i >= 0 && i < cells.length; i += step) {
                if (isEditable(cells[i])) return cells[i];
            }
            // Wrap onto the adjacent data row.
            var rows = dataRows();
            var next = rows[rows.indexOf(row) + step];
            return next ? firstEditable(next, dir === "left") : null;
        }

        // up / down: same column, scanning adjacent data rows.
        var all = dataRows();
        var pos = all.indexOf(row);
        var stp = dir === "down" ? 1 : -1;
        for (var r = pos + stp; r >= 0 && r < all.length; r += stp) {
            var c = all[r].children[col];
            if (isEditable(c)) return c;
        }
        return null;
    }

    document.addEventListener(
        "keydown",
        function (e) {
            if (!isEditField(e.target)) return;
            // This grid navigation is holdings-only: it drives htmx swaps against
            // #holdings-table and would otherwise hijack Enter/Escape (and suppress
            // the native save) on every other sheet that reuses .table-cell-input
            // (transactions, merchants, accounts, budget, assets).
            if (!e.target.closest("#holdings-table")) return;

            var dir = null;
            if (e.key === "Tab") dir = e.shiftKey ? "left" : "right";
            else if (e.key === "Enter") dir = e.shiftKey ? "up" : "down";
            else return;

            var field = e.target;
            var td = field.closest("td");
            var tr = field.closest("tr");
            var url = field.getAttribute("hx-post");
            var form = field.closest("form");
            var fieldName = form && form.querySelector('[name="field"]');
            if (!td || !tr || !tr.id || !url || !fieldName) return;

            // Save this cell ourselves; suppress the cell's own hx-trigger.
            e.preventDefault();
            e.stopImmediatePropagation();
            pending = {
                rowId: tr.id,
                col: Array.prototype.indexOf.call(tr.children, td),
                dir: dir,
            };
            htmx.ajax("POST", url, {
                target: "#holdings-table",
                swap: "innerHTML",
                values: { field: fieldName.value, value: field.value },
            });
        },
        true
    );

    document.addEventListener("htmx:afterSwap", function () {
        var nav = pending;
        pending = null;
        if (!nav) return;
        var next = findNext(nav.rowId, nav.col, nav.dir);
        if (!next) return;
        var url = next.getAttribute("hx-get");
        // Defer past the current swap's settle, then open the next cell for edit
        // (its cell-edit input autofocuses).
        setTimeout(function () {
            htmx.ajax("GET", url, { target: "#holdings-table", swap: "innerHTML" });
        }, 0);
    });
})();
