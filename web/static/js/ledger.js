// Ledger-page behavior (import dropzone, trends drill-down, transactions
// month-state). Pages live under /s/<snapshot>/..., so fetch URLs are built
// from the current path's snapshot prefix.

function snapshotPrefix() {
    // "/s/<snapshot>" from "/s/<snapshot>/<section>..."
    return window.location.pathname.split('/').slice(0, 3).join('/');
}

// Remove one selected value from a multi-select filter chip: uncheck the
// matching hidden checkbox and re-fire its htmx request so the list reloads
// with that value dropped. Used by partials/filter_controls.html chips.
function removeFilter(name, value) {
    // name is a controlled field name; match the value in JS so category names
    // with special characters don't break an attribute selector.
    const boxes = document.querySelectorAll('input[name="' + name + '"]');
    for (const cb of boxes) {
        if (cb.value === value) {
            cb.checked = false;
            // Native change event: triggers both htmx (hx-trigger="change") and
            // a form's onchange="submit()" — so this works for the htmx filter
            // bars and the form-submit Finances sheets alike.
            cb.dispatchEvent(new Event('change', { bubbles: true }));
            break;
        }
    }
}

// Clear a single-select radio filter: select its empty ("Any") option and
// re-fire the request. Used by single-select filter chips.
function clearRadio(name) {
    const any = document.querySelector('input[type="radio"][name="' + name + '"][value=""]');
    if (any) {
        any.checked = true;
        any.dispatchEvent(new Event('change', { bubbles: true }));
    }
}

function updateImportButton() {
    const btn = document.getElementById('import-submit');
    if (!btn) return;
    const fileInput = document.getElementById('file-input');
    const accountSelect = document.querySelector('select[name="account_id"]');
    const hasFile = fileInput && fileInput.files.length > 0;
    const hasAccount = accountSelect && accountSelect.value !== '';
    btn.disabled = !(hasFile && hasAccount);
}

function detectAccount(file) {
    const formData = new FormData();
    formData.append('files', file);
    // Include whatever account the user has already picked so the server
    // can preserve it when auto-detection doesn't find a confident match.
    const accountSelect = document.querySelector('select[name="account_id"]');
    if (accountSelect && accountSelect.value !== '') {
        formData.append('account_id', accountSelect.value);
    }
    fetch(snapshotPrefix() + '/import/detect-account', { method: 'POST', body: formData })
        .then(r => {
            if (!r.ok) throw new Error(`detect-account failed: ${r.status}`);
            return r.text();
        })
        .then(html => {
            const panel = document.getElementById('account-panel');
            if (panel) {
                panel.outerHTML = html;
                htmx.process(document.body);
                updateImportButton();
            }
        })
        .catch(err => {
            console.error('Account detection failed, continuing without pre-fill:', err);
        });
}

function initDropzone() {
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('file-input');
    const fileList = document.getElementById('file-list');

    if (!dropzone || !fileInput) return;

    dropzone.addEventListener('click', () => fileInput.click());

    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('border-blue-400', 'bg-blue-50');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('border-blue-400', 'bg-blue-50');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('border-blue-400', 'bg-blue-50');
        fileInput.files = e.dataTransfer.files;
        updateFileList();
        if (fileInput.files.length > 0) {
            detectAccount(fileInput.files[0]);
        }
    });

    fileInput.addEventListener('change', function () {
        updateFileList();
        updateImportButton();
        if (fileInput.files.length > 0) {
            detectAccount(fileInput.files[0]);
        }
    });

    updateImportButton();

    function updateFileList() {
        const files = fileInput.files;
        if (files.length === 0) {
            fileList.innerHTML = '';
            return;
        }
        const names = Array.from(files).map(f => f.name).join(', ');
        fileList.textContent = `Selected: ${names}`;
    }
}

document.addEventListener('DOMContentLoaded', initDropzone);

// Re-init after HTMX swaps content (DOMContentLoaded only fires once)
document.addEventListener('htmx:afterSettle', initDropzone);
document.addEventListener('htmx:afterSettle', updateImportButton);

// Account select may be replaced by HTMX — use delegation
document.addEventListener('change', function (e) {
    if (e.target.name === 'account_id') updateImportButton();
});

function toggleTrendDetail(rowId, category, period, end) {
    const detailRow = document.getElementById('trend-detail-' + rowId);
    const arrow = document.getElementById('arrow-' + rowId);
    if (!detailRow) return;

    if (detailRow.classList.contains('hidden')) {
        detailRow.classList.remove('hidden');
        if (arrow) { arrow.textContent = '▼'; arrow.classList.replace('text-gray-300', 'text-gray-500'); }
        if (!detailRow.dataset.loaded) {
            detailRow.dataset.loaded = 'true';
            const cell = detailRow.querySelector('td');
            let url = snapshotPrefix() + '/trends/detail?category=' + encodeURIComponent(category) + '&period=' + encodeURIComponent(period);
            if (end) url += '&end=' + encodeURIComponent(end);
            fetch(url)
                .then(r => r.text())
                .then(html => { cell.innerHTML = html; })
                .catch(() => { cell.innerHTML = '<div class="p-4 text-xs text-red-400">Failed to load detail.</div>'; });
        }
    } else {
        detailRow.classList.add('hidden');
        if (arrow) { arrow.textContent = '▶'; arrow.classList.replace('text-gray-500', 'text-gray-300'); }
    }
}

document.addEventListener('htmx:configRequest', function (e) {
    if (!e.detail.path.endsWith('/transactions')) return;
    const state = document.getElementById('month-state');
    if (!state) return;
    if (!e.detail.parameters.get('year')) e.detail.parameters.set('year', state.dataset.year);
    if (!e.detail.parameters.get('month')) e.detail.parameters.set('month', state.dataset.month);
});
