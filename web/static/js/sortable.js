/* Client-side table sorting for tables with class="sortable" */
(function () {
    function parseSortValue(text) {
        if (text === undefined || text === null) return { num: NaN, str: '' };
        var s = ('' + text).trim();
        var numStr = s.replace(/\$/g, '').replace(/,/g, '').replace(/%/g, '').trim();
        var negative = numStr.startsWith('(') && numStr.endsWith(')');
        if (negative) numStr = numStr.slice(1, -1).trim();
        var num = parseFloat(numStr);
        if (!isNaN(num) && negative) num = -num;
        return { num: isNaN(num) ? NaN : num, str: s };
    }

    function isTotalRow(tr) {
        var first = tr.cells[0];
        return first && (first.textContent.trim() === 'Total' || /^-+$/.test(first.textContent.trim()));
    }

    function isAddRow(tr) {
        return tr.hasAttribute('data-add-row');
    }

    function getOriginalRowOrder(table) {
        if (table._originalRowOrder) return;
        var tbody = table.querySelector('tbody');
        if (!tbody) return;
        var rows = Array.from(tbody.querySelectorAll('tr'));
        var addRow = null;
        var totalRow = null;

        for (var i = rows.length - 1; i >= 0; i--) {
            if (isAddRow(rows[i])) {
                addRow = rows.splice(i, 1)[0];
                break;
            }
        }

        if (rows.length > 0 && isTotalRow(rows[rows.length - 1])) {
            totalRow = rows.pop();
        }

        table._originalRowOrder = rows;
        table._originalTotalRow = totalRow;
        table._originalAddRow = addRow;
    }

    function restoreOriginalOrder(table) {
        getOriginalRowOrder(table);
        var tbody = table.querySelector('tbody');
        if (!tbody || !table._originalRowOrder) return;
        table._originalRowOrder.forEach(function (r) { tbody.appendChild(r); });
        if (table._originalAddRow) tbody.appendChild(table._originalAddRow);
        if (table._originalTotalRow) tbody.appendChild(table._originalTotalRow);
    }

    function sortTable(table, colIndex, dir) {
        var tbody = table.querySelector('tbody');
        if (!tbody) return;
        getOriginalRowOrder(table);
        var rows = table._originalRowOrder.slice();
        rows.sort(function (a, b) {
            var cellA = a.cells[colIndex];
            var cellB = b.cells[colIndex];
            if (!cellA || !cellB) return 0;
            var va = parseSortValue(cellA.textContent);
            var vb = parseSortValue(cellB.textContent);
            var cmp;
            if (!isNaN(va.num) && !isNaN(vb.num)) {
                cmp = va.num - vb.num;
            } else {
                cmp = (va.str || '').localeCompare(vb.str || '', undefined, { numeric: true });
            }
            return dir === 'asc' ? cmp : -cmp;
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        if (table._originalAddRow) tbody.appendChild(table._originalAddRow);
        if (table._originalTotalRow) tbody.appendChild(table._originalTotalRow);
    }

    function updateIndicators(table, colIndex, dir) {
        var headers = table.querySelectorAll('.sortable-th');
        headers.forEach(function (th, i) {
            var ind = th.querySelector('.sort-indicator');
            if (ind) {
                ind.textContent = (i === colIndex && dir !== 'none') ? (dir === 'asc' ? ' \u2191' : ' \u2193') : '';
            }
        });
    }

    function updateSortUrl(table, colIndex, dir) {
        var url = new URL(window.location);
        if (dir === 'none') {
            url.searchParams.delete('sort_col');
            url.searchParams.delete('sort_dir');
        } else {
            url.searchParams.set('sort_col', colIndex);
            url.searchParams.set('sort_dir', dir);
        }
        window.history.pushState({}, '', url);
    }

    function initSortable() {
        document.querySelectorAll('table.sortable').forEach(function (table) {
            var initialCol = table.getAttribute('data-sort-col');
            var initialDir = table.getAttribute('data-sort-dir');
            if (initialCol && initialDir && initialDir !== 'none') {
                sortTable(table, parseInt(initialCol, 10), initialDir);
                updateIndicators(table, parseInt(initialCol, 10), initialDir);
            }

            table.querySelectorAll('.sortable-th').forEach(function (th) {
                var colIndex = parseInt(th.getAttribute('data-col'), 10);
                if (isNaN(colIndex)) return;
                function doSort() {
                    var currentCol = table.getAttribute('data-sort-col');
                    var currentDir = table.getAttribute('data-sort-dir') || 'none';
                    var dir;
                    if (currentCol !== String(colIndex)) {
                        dir = 'asc';
                    } else if (currentDir === 'asc') {
                        dir = 'desc';
                    } else if (currentDir === 'desc') {
                        dir = 'none';
                    } else {
                        dir = 'asc';
                    }
                    table.setAttribute('data-sort-col', colIndex);
                    table.setAttribute('data-sort-dir', dir);
                    if (dir === 'none') {
                        restoreOriginalOrder(table);
                    } else {
                        sortTable(table, colIndex, dir);
                    }
                    updateIndicators(table, colIndex, dir);
                    updateSortUrl(table, colIndex, dir);
                }
                th.addEventListener('click', doSort);
                th.addEventListener('keydown', function (e) {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        doSort();
                    }
                });
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSortable);
    } else {
        initSortable();
    }
})();
