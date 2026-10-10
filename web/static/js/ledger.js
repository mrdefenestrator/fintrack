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

// In-memory copies of the files picked in the import dropzone. iPadOS Safari
// hands the page File objects backed by the Files app / iCloud that can become
// unreadable after their first read (detect-account) or after a short while,
// so the later upload went out without its file body and the server answered
// 400 (#74). Reading the bytes once, at pick time, and sending these copies
// sidesteps that. `input` ties the copies to the current #file-input, since
// htmx swaps replace it.
let importFiles = { input: null, files: null, reading: false };

function showImportError(message) {
    const box = document.getElementById('import-error');
    if (!box) return;
    box.textContent = message || '';
    box.classList.toggle('hidden', !message);
}

function updateImportButton() {
    const btn = document.getElementById('import-submit');
    if (!btn) return;
    const fileInput = document.getElementById('file-input');
    const accountSelect = document.querySelector('select[name="account_id"]');
    const hasFile = !!fileInput && importFiles.input === fileInput &&
        !importFiles.reading && !!importFiles.files && importFiles.files.length > 0;
    const hasAccount = accountSelect && accountSelect.value !== '';
    btn.disabled = !(hasFile && hasAccount);
}

// The file input has no `accept` filter (iOS greys out .ofx/.qfx with one),
// so unsupported picks are caught here instead.
const IMPORT_EXTENSIONS = ['.ofx', '.qfx', '.csv'];

function bufferImportFiles(fileInput, fileList) {
    const picked = Array.from(fileList);
    const unsupported = picked.filter(f =>
        !IMPORT_EXTENSIONS.some(ext => f.name.toLowerCase().endsWith(ext)));
    if (unsupported.length > 0) {
        importFiles = { input: fileInput, files: null, reading: false };
        updateImportButton();
        showImportError('Unsupported file type: ' +
            unsupported.map(f => f.name).join(', ') + '. Choose OFX, QFX, or CSV files.');
        return;
    }
    importFiles = { input: fileInput, files: null, reading: picked.length > 0 };
    showImportError('');
    updateImportButton();
    if (picked.length === 0) return;
    Promise.all(picked.map(f => f.arrayBuffer().then(buf =>
        new File([buf], f.name, { type: f.type, lastModified: f.lastModified }))))
        .then(copies => {
            if (importFiles.input !== fileInput) return;  // superseded
            importFiles = { input: fileInput, files: copies, reading: false };
            updateImportButton();
            detectAccount(copies[0]);
        })
        .catch(err => {
            if (importFiles.input !== fileInput) return;
            console.error('Could not read selected file(s):', err);
            importFiles = { input: fileInput, files: null, reading: false };
            updateImportButton();
            showImportError("Couldn't read the selected file. Please select it again " +
                '(if it lives in iCloud Drive, make sure it has finished downloading).');
        });
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
    // afterSettle fires for every htmx swap (e.g. each staging batch's review
    // load), so only wire a given dropzone once.
    if (dropzone.dataset.initialized) return;
    dropzone.dataset.initialized = 'true';

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
        bufferImportFiles(fileInput, fileInput.files);
    });

    fileInput.addEventListener('change', function () {
        updateFileList();
        bufferImportFiles(fileInput, fileInput.files);
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

function isImportUpload(evt) {
    return evt.detail.requestConfig
        ? evt.detail.requestConfig.path.endsWith('/import/upload')
        : (evt.detail.path || '').endsWith('/import/upload');
}

// Send the in-memory copies instead of the live input's File objects.
document.addEventListener('htmx:configRequest', function (e) {
    if (!isImportUpload(e)) return;
    if (importFiles.input !== document.getElementById('file-input') || !importFiles.files) return;
    e.detail.formData.delete('files');
    importFiles.files.forEach(f => e.detail.formData.append('files', f));
    showImportError('');
});

// htmx drops 4xx/5xx bodies and network failures silently; tell the user.
// (422s carry a re-rendered page with its own message and are swapped in by
// base.html, so they're skipped here.)
document.addEventListener('htmx:responseError', function (e) {
    if (!isImportUpload(e)) return;
    const status = e.detail.xhr.status;
    if (status === 422) return;
    showImportError(status === 413
        ? 'Import failed: the file is too large.'
        : `Import failed (server error ${status}). Please try again.`);
});
document.addEventListener('htmx:sendError', function (e) {
    if (!isImportUpload(e)) return;
    showImportError('Import failed: could not reach the server. Check your connection and try again.');
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
