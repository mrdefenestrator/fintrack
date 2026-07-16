"""File management blueprint — create, copy, rename, delete snapshots."""

import re

from flask import Blueprint, abort, current_app, redirect, request, url_for

from finances.repository.snapshots import (
    copy_snapshot,
    create_snapshot,
    delete_snapshot,
    get_snapshot_id,
    list_snapshots,
    rename_snapshot,
)

files_bp = Blueprint("files", __name__, url_prefix="/files")


def _sanitize_name(raw: str) -> str:
    """Return a safe snapshot name.

    Raises ValueError if empty or invalid after sanitisation.
    """
    name = raw.strip()
    name = name.replace("\\", "/")
    name = name.rsplit("/", 1)[-1]
    name = re.sub(r"\.(yaml|yml)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^a-zA-Z0-9 _\-.]", "", name).strip()
    if not name:
        raise ValueError("Invalid snapshot name")
    return name


def _navigate_to_snapshot(name: str):
    target = url_for("accounts.accounts_view", filename=name)
    if request.headers.get("HX-Request"):
        resp = current_app.make_response("")
        resp.headers["HX-Redirect"] = target
        return resp
    return redirect(target)


@files_bp.route("/new", methods=["POST"])
def new():
    raw_name = request.form.get("name", "")
    try:
        name = _sanitize_name(raw_name)
    except ValueError:
        abort(400, description="Invalid snapshot name")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        if get_snapshot_id(conn, name) is not None:
            abort(409, description=f"Snapshot already exists: {name}")
        create_snapshot(conn, name)
    return _navigate_to_snapshot(name)


@files_bp.route("/copy", methods=["POST"])
def copy():
    source = request.form.get("source", "").strip()
    raw_name = request.form.get("name", "")
    try:
        dest_name = _sanitize_name(raw_name)
    except ValueError:
        abort(400, description="Invalid snapshot name")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        from_id = get_snapshot_id(conn, source)
        if from_id is None:
            abort(404, description=f"Source snapshot not found: {source}")
        if get_snapshot_id(conn, dest_name) is not None:
            abort(409, description=f"Snapshot already exists: {dest_name}")
        copy_snapshot(conn, from_id, dest_name)
    return _navigate_to_snapshot(dest_name)


@files_bp.route("/rename", methods=["POST"])
def rename():
    old_name = request.form.get("old_name", "").strip()
    raw_new = request.form.get("new_name", "")
    try:
        new_name = _sanitize_name(raw_new)
    except ValueError:
        abort(400, description="Invalid snapshot name")

    engine = current_app.config["engine"]
    with engine.connect() as conn:
        snap_id = get_snapshot_id(conn, old_name)
        if snap_id is None:
            abort(404, description=f"Snapshot not found: {old_name}")
        if get_snapshot_id(conn, new_name) is not None:
            abort(409, description=f"Snapshot already exists: {new_name}")
        rename_snapshot(conn, snap_id, new_name)
    return _navigate_to_snapshot(new_name)


@files_bp.route("/delete", methods=["POST"])
def delete():
    name = request.form.get("name", "").strip()
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        snap_id = get_snapshot_id(conn, name)
        if snap_id is None:
            abort(404, description=f"Snapshot not found: {name}")
        delete_snapshot(conn, snap_id)
        remaining = list_snapshots(conn)

    if remaining:
        return _navigate_to_snapshot(remaining[0])

    target = url_for("status.status_view")
    if request.headers.get("HX-Request"):
        resp = current_app.make_response("")
        resp.headers["HX-Redirect"] = target
        return resp
    return redirect(target)
