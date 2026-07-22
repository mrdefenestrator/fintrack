import os
import tempfile
from pathlib import Path

from flask import Blueprint, current_app, g, render_template, request
from sqlalchemy.exc import IntegrityError

from fintrack.core.types import ACCOUNT_TYPE_VALUES
from fintrack.ledger.classifier import classify_and_cache
from fintrack.ledger.importer import run_import
from fintrack.ledger.importer.ofx import extract_ofx_metadata
from fintrack.ledger.repository.accounts import add_account, list_accounts
from web.routes.common import snapshot_scoped
from fintrack.ledger.repository.imports import (
    confirm_import,
    get_staging_imports,
    get_staging_transactions,
    reject_import,
)

bp = snapshot_scoped(Blueprint("imports", __name__, url_prefix="/s/<filename>"))


@bp.route("/import")
def index():
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        staging = get_staging_imports(conn, g.snapshot_id)
        accounts = list_accounts(conn, g.snapshot_id)

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
        staging = get_staging_imports(conn, g.snapshot_id)
        accounts = list_accounts(conn, g.snapshot_id)

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


def _match_account(accounts: list[dict], meta) -> int | None:
    """Match OFX metadata to exactly one existing account, conservatively.

    A candidate must have a case-insensitively matching institution and an
    exactly matching account_type. If the OFX exposes an account number, the
    candidates are further narrowed to accounts whose name contains its last
    4 digits (users fold partials into names by convention, e.g. "...7890").
    Only returns a match when exactly one confident candidate remains —
    ambiguous or empty results return None so the caller falls back to
    whatever the user had already selected.
    """
    if not meta or not meta.get("institution"):
        return None
    institution = meta["institution"].strip().lower()
    account_type = meta.get("account_type")
    candidates = [
        a
        for a in accounts
        if (a.get("institution") or "").strip().lower() == institution
        and a.get("account_type") == account_type
    ]
    if not candidates:
        return None
    last4 = (meta.get("last4") or "").strip()
    if last4:
        narrowed = [a for a in candidates if last4 in (a.get("name") or "")]
        if narrowed:
            candidates = narrowed
    if len(candidates) == 1:
        return candidates[0]["id"]
    return None


@bp.route("/import/detect-account", methods=["POST"])
def detect_account():
    file = request.files.get("files")
    # The currently selected account, if any, so a low-confidence (or failed)
    # match never clobbers a choice the user already made.
    prev_account_id = request.form.get("account_id", type=int)
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
        accounts = list_accounts(conn, g.snapshot_id)

    matched_id = _match_account(accounts, meta)
    selected_account_id = matched_id if matched_id is not None else prev_account_id

    return render_template(
        "partials/account_panel.html",
        accounts=accounts,
        meta=meta,
        selected_account_id=selected_account_id,
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
        staging = get_staging_imports(conn, g.snapshot_id)
        accounts = list_accounts(conn, g.snapshot_id)
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
        staging = get_staging_imports(conn, g.snapshot_id)
        accounts = list_accounts(conn, g.snapshot_id)
    return render_template(
        "partials/import_content.html",
        active_tab="import",
        staging=staging,
        accounts=accounts,
    )


# Single source of truth for valid account types: fintrack.core.types.
VALID_ACCOUNT_TYPES = set(ACCOUNT_TYPE_VALUES)


@bp.route("/import/accounts", methods=["POST"])
def create_account():
    """Create an account from the import page's account panel."""
    name = request.form.get("acct_name", "").strip()
    institution = request.form.get("acct_institution", "").strip()
    account_type = request.form.get("acct_type", "checking")
    if account_type not in VALID_ACCOUNT_TYPES:
        account_type = "checking"

    engine = current_app.config["engine"]

    with engine.connect() as conn:
        if not name or not institution:
            accounts = list_accounts(conn, g.snapshot_id)
            return render_template(
                "partials/account_panel.html",
                accounts=accounts,
                meta=None,
                selected_account_id=None,
                show_create=True,
                error="Name and institution are required.",
            )

        try:
            new_id = add_account(
                conn,
                name=name,
                institution=institution,
                account_type=account_type,
                snapshot_id=g.snapshot_id,
            )
        except IntegrityError:
            conn.rollback()
            accounts = list_accounts(conn, g.snapshot_id)
            return render_template(
                "partials/account_panel.html",
                accounts=accounts,
                meta=None,
                selected_account_id=None,
                show_create=True,
                error=(
                    f'An account named "{name}" already exists for '
                    f'institution "{institution}".'
                ),
            )

        accounts = list_accounts(conn, g.snapshot_id)
        return render_template(
            "partials/account_panel.html",
            accounts=accounts,
            meta=None,
            selected_account_id=new_id,
            show_create=False,
            error=None,
        )
