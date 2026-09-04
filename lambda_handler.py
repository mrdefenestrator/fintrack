"""AWS Lambda handler for PR preview environments.

Translates Lambda Function URL events to WSGI and back, so the Flask app
serves directly with no ASGI adapter layer. On cold start, runs migrations
and seeds the demo households into an ephemeral SQLite database in /tmp.
"""

import io
import os
import subprocess
import sys
from base64 import b64decode, b64encode

# Point the DB at Lambda's writable /tmp
os.environ.setdefault("FINTRACK_DB", "/tmp/fintrack.db")

from web.app import create_app

_app = None
_initialized = False


def _cold_start():
    """Run migrations and seed on first invocation."""
    global _initialized
    if _initialized:
        return
    db_path = os.environ["FINTRACK_DB"]
    if not os.path.exists(db_path):
        # LAMBDA_TASK_ROOT (/var/task) has the app code; ensure subprocesses
        # can import fintrack/ and other project modules.
        env = {
            **os.environ,
            "PYTHONPATH": os.environ.get("LAMBDA_TASK_ROOT", "/var/task"),
        }
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env=env,
        )
        subprocess.run(
            [sys.executable, "scripts/seed_example.py"],
            check=True,
            env=env,
        )
    _initialized = True


def handler(event, context):
    """Lambda entry point — translates Function URL events to WSGI."""
    global _app
    _cold_start()
    if _app is None:
        _app = create_app()

    # Build the WSGI environ from the Lambda Function URL event
    http_ctx = event.get("requestContext", {}).get("http", {})
    headers = event.get("headers", {})

    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        body = b64decode(body)
    elif isinstance(body, str):
        body = body.encode("utf-8")

    query_string = event.get("rawQueryString", "")

    environ = {
        "REQUEST_METHOD": http_ctx.get("method", "GET"),
        "PATH_INFO": http_ctx.get("path", "/"),
        "QUERY_STRING": query_string,
        "SERVER_NAME": headers.get("host", "localhost"),
        "SERVER_PORT": headers.get("x-forwarded-port", "443"),
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": headers.get("x-forwarded-proto", "https"),
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(body)),
        "CONTENT_TYPE": headers.get("content-type", ""),
    }

    # Map HTTP headers to CGI-style HTTP_* keys
    for key, value in headers.items():
        cgi_key = "HTTP_" + key.upper().replace("-", "_")
        if cgi_key not in ("HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"):
            environ[cgi_key] = value

    # Call Flask via WSGI
    response_started = []
    response_body = []

    def start_response(status, response_headers, exc_info=None):
        response_started.append((status, response_headers))

    result = _app.wsgi_app(environ, start_response)
    try:
        for chunk in result:
            response_body.append(chunk)
    finally:
        if hasattr(result, "close"):
            result.close()

    status, response_headers = response_started[0]
    status_code = int(status.split(" ", 1)[0])
    body_bytes = b"".join(response_body)

    # Check if the response is binary
    content_type = ""
    out_headers = {}
    for key, value in response_headers:
        out_headers[key] = value
        if key.lower() == "content-type":
            content_type = value

    is_binary = not content_type.startswith(
        ("text/", "application/json", "application/xml")
    )

    return {
        "statusCode": status_code,
        "headers": out_headers,
        "body": b64encode(body_bytes).decode("ascii")
        if is_binary
        else body_bytes.decode("utf-8"),
        "isBase64Encoded": is_binary,
    }
