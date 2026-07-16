"""Edit mode toggle route."""

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from flask import Blueprint, redirect, request, url_for

from .common import get_default_filename

edit_mode_bp = Blueprint("edit_mode", __name__, url_prefix="/edit-mode")


@edit_mode_bp.route("/toggle", methods=["POST"])
def toggle():
    """Toggle edit mode by manipulating ?edit=1 on the Referer URL."""
    referrer = request.referrer or ""
    parsed = urlparse(referrer)

    # Reject off-site referrers
    if parsed.netloc and parsed.netloc != request.host:
        return redirect(
            url_for("accounts.accounts_view", filename=get_default_filename())
        )

    params = parse_qs(parsed.query, keep_blank_values=True)
    if "edit" in params:
        params.pop("edit")  # was editing → lock
    else:
        params["edit"] = ["1"]  # was locked → edit

    new_query = urlencode({k: v[0] for k, v in params.items()})

    # Reconstruct URL (use path only if netloc absent to avoid open-redirect)
    if parsed.netloc:
        new_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, "")
        )
    else:
        new_url = parsed.path + ("?" + new_query if new_query else "")

    return redirect(
        new_url or url_for("accounts.accounts_view", filename=get_default_filename())
    )
