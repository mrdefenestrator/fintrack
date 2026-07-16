import os
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, render_template, request

from spending.classifier import classify_and_cache
from spending.importer import run_import
from spending.importer.ofx import extract_ofx_metadata
from spending.repository.accounts import list_accounts
from spending.repository.imports import (
    confirm_import,
    get_staging_imports,
    get_staging_transactions,
    reject_import,
)

bp = Blueprint("imports", __name__)


@bp.route("/import")
def index():
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        staging = get_staging_imports(conn)
        accounts = list_accounts(conn)

    template = (
        "partials/import_content.html"
        if request.headers.get("HX-Request")
        else "import.html"
    )
    return render_template(
        template,
        active_tab="import",
        staging=staging,
        accounts=accounts,
    )


@bp.route("/import/upload", methods=["POST"])
def upload():
    files = request.files.getlist("files")
    account_id = request.form.get("account_id", type=int)

    if not files or not account_id:
        return "<p class='text-red-500'>Please select files and an account.</p>", 400

    engine = current_app.config["engine"]
    results = []

    with engine.connect() as conn:
        all_new_merchants = set()

        for f in files:
            if not f.filename:
                continue
            suffix = Path(f.filename).suffix
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp_path = tmp.name
                    f.save(tmp_path)
                result = run_import(conn, tmp_path, account_id)
            finally:
                if tmp_path:
                    os.unlink(tmp_path)
            result["filename"] = f.filename
            results.append(result)
            if not result.get("error"):
                all_new_merchants.update(result.get("new_merchants", []))

        # Classify new merchants
        classified_count = 0
        classify_warning = None
        if all_new_merchants:
            classified_count, classify_warning = classify_and_cache(
                conn, list(all_new_merchants)
            )

        # Re-fetch staging imports
        staging = get_staging_imports(conn)
        accounts = list_accounts(conn)

    template = (
        "partials/import_content.html"
        if request.headers.get("HX-Request")
        else "import.html"
    )
    return render_template(
        template,
        active_tab="import",
        staging=staging,
        accounts=accounts,
        results=results,
        classified_count=classified_count,
        classify_warning=classify_warning,
    )


@bp.route("/import/detect-account", methods=["POST"])
def detect_account():
    file = request.files.get("files")
    meta = None

    if file and file.filename:
        suffix = Path(file.filename).suffix.lower()
        if suffix in (".ofx", ".qfx"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp_path = tmp.name
                file.save(tmp_path)
            try:
                meta = extract_ofx_metadata(tmp_path)
            finally:
                os.unlink(tmp_path)

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        accounts = list_accounts(conn)

    return render_template(
        "partials/account_panel.html",
        accounts=accounts,
        meta=meta,
        selected_account_id=None,
        show_create=(len(accounts) == 0),
        error=None,
    )


@bp.route("/import/<int:import_id>/review")
def review(import_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        txns = get_staging_transactions(conn, import_id)
    return render_template(
        "partials/import_batch.html", transactions=txns, import_id=import_id
    )


@bp.route("/import/<int:import_id>/confirm", methods=["POST"])
def confirm(import_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        confirm_import(conn, import_id)
        staging = get_staging_imports(conn)
        accounts = list_accounts(conn)
    return render_template(
        "partials/import_content.html",
        active_tab="import",
        staging=staging,
        accounts=accounts,
    )


@bp.route("/import/<int:import_id>/reject", methods=["POST"])
def do_reject(import_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        reject_import(conn, import_id)
        staging = get_staging_imports(conn)
        accounts = list_accounts(conn)
    return render_template(
        "partials/import_content.html",
        active_tab="import",
        staging=staging,
        accounts=accounts,
    )
