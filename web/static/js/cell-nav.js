/**
 * Spreadsheet keyboard navigation for editable sheet grids.
 *
 *   Tab / Shift+Tab   -> next / previous editable cell (right / left, wrapping
 *                        onto the adjacent row)
 *   Enter / Shift+Enter -> down / up the same column
 *   Escape             -> cancel the current edit
 *
 * Each move first saves the cell being edited, then, on the resulting swap,
 * opens the next editable cell (the cell-edit input autofocuses).
 *
 * Tables opt in by adding `data-cell-nav` on the container element (the
 * <table> or a wrapper).  The attribute's value names the htmx swap target id
 * (e.g. "holdings-table" or "budget-tbody") for tables that swap their whole
 * tbody.  For per-row tables (merchants), set data-cell-nav-swap="outerHTML"
 * and saves will use the field's own hx-target.
 *
 * Editable display cells: any td with hx-get containing "/cell" or "/edit".
 * Holdings detail cells (.holding-detail-edit) are also supported.
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

    function findContainer(el) {
        return el && el.closest("[data-cell-nav]");
    }

    function swapTarget(container) {
        var id = container.getAttribute("data-cell-nav");
        return id ? "#" + id : null;
    }

    function swapMode(container) {
        return container.getAttribute("data-cell-nav-swap") || "innerHTML";
    }

    function editableCells(row) {
        return Array.prototype.slice.call(
            row.querySelectorAll(
                ':scope > td[hx-get*="/cell"], :scope > td[hx-get*="/edit"]'
            )
        );
    }

    function cellKey(el, row) {
        var td = el && el.closest("td");
        return td ? "c" + Array.prototype.indexOf.call(row.children, td) : null;
    }

    function dataRows(container) {
        var rows = Array.prototype.slice.call(
            container.querySelectorAll("tr[data-reorder-index]")
        );
        if (!rows.length) {
            rows = Array.prototype.slice.call(
                container.querySelectorAll("tbody > tr[id]")
            ).filter(function (tr) {
                return !tr.classList.contains("total-row") &&
                    !tr.hasAttribute("data-add-row") &&
                    tr.id.indexOf("error") === -1;
            });
        }
        return rows;
    }

    function firstEditable(row, fromEnd) {
        var cells = editableCells(row);
        return cells.length ? cells[fromEnd ? cells.length - 1 : 0] : null;
    }

    function findNext(container, rowId, key, dir) {
        var row = document.getElementById(rowId);
        if (!row) return null;

        if (dir === "right" || dir === "left") {
            var step = dir === "right" ? 1 : -1;
            var cells = editableCells(row);
            var current = cells.findIndex(function (cell) {
                return cellKey(cell, row) === key;
            });
            if (current >= 0 && cells[current + step]) return cells[current + step];
            var rows = dataRows(container);
            var next = rows[rows.indexOf(row) + step];
            return next ? firstEditable(next, dir === "left") : null;
        }

        var all = dataRows(container);
        var pos = all.indexOf(row);
        var stp = dir === "down" ? 1 : -1;
        for (var r = pos + stp; r >= 0 && r < all.length; r += stp) {
            var candidates = editableCells(all[r]);
            var match = candidates.find(function (cell) {
                return cellKey(cell, all[r]) === key;
            });
            if (match) return match;
        }
        return null;
    }

    document.addEventListener(
        "keydown",
        function (e) {
            if (!isEditField(e.target)) return;
            var container = findContainer(e.target);
            if (!container) return;

            var mode = swapMode(container);
            var target = swapTarget(container);

            if (e.key === "Escape") {
                e.preventDefault();
                e.stopImmediatePropagation();
                var el = e.target;
                if (el.tagName === "SELECT") {
                    for (var oi = 0; oi < el.options.length; oi++) {
                        el.options[oi].selected = el.options[oi].defaultSelected;
                    }
                } else {
                    el.value = el.defaultValue;
                }
                var cancelForm = el.closest("form");
                var displayUrl = cancelForm && cancelForm.getAttribute("hx-get");
                if (displayUrl) {
                    var cancelTarget = target;
                    var cancelSwap = mode;
                    if (mode === "outerHTML") {
                        var tr = el.closest("tr");
                        cancelTarget = tr ? "#" + tr.id : target;
                        cancelSwap = "outerHTML";
                    }
                    htmx.ajax("GET", displayUrl, {
                        target: cancelTarget,
                        swap: cancelSwap,
                    });
                }
                return;
            }

            var dir = null;
            if (e.key === "Tab") dir = e.shiftKey ? "left" : "right";
            else if (e.key === "Enter") dir = e.shiftKey ? "up" : "down";
            else return;

            var field = e.target;
            var td = field.closest("td");
            var tr = field.closest("tr");
            var url = field.getAttribute("hx-post");
            var form = field.closest("form");
            if (!td || !tr || !tr.id || !url || !form) return;

            e.preventDefault();
            e.stopImmediatePropagation();
            pending = {
                containerId: container.id || container.getAttribute("data-cell-nav"),
                rowId: tr.id,
                key: cellKey(field, tr),
                dir: dir,
                mode: mode,
            };

            var vals = {};
            for (var fi = 0; fi < form.elements.length; fi++) {
                var inp = form.elements[fi];
                if (inp.name && inp.type !== "submit" && inp.type !== "button") {
                    if (inp.type === "checkbox" || inp.type === "radio") {
                        if (inp.checked) vals[inp.name] = inp.value || "on";
                    } else {
                        vals[inp.name] = inp.value;
                    }
                }
            }

            var saveTarget = target;
            if (mode === "outerHTML") {
                saveTarget = "#" + tr.id;
            }
            htmx.ajax("POST", url, {
                target: saveTarget,
                swap: mode,
                values: vals,
            });
        },
        true
    );

    document.addEventListener("htmx:afterSwap", function () {
        var nav = pending;
        pending = null;
        if (!nav) return;
        var container =
            document.getElementById(nav.containerId) ||
            document.querySelector('[data-cell-nav="' + nav.containerId + '"]');
        if (!container) return;
        var next = findNext(container, nav.rowId, nav.key, nav.dir);
        if (!next) return;
        var url = next.getAttribute("hx-get");
        var nextTarget = swapTarget(container);
        var nextSwap = nav.mode;
        if (nav.mode === "outerHTML") {
            var nextTr = next.closest("tr");
            nextTarget = nextTr ? "#" + nextTr.id : nextTarget;
        }
        setTimeout(function () {
            htmx.ajax("GET", url, { target: nextTarget, swap: nextSwap });
        }, 0);
    });
})();
