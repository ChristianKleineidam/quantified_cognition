"""Run a registered research job only while its extract remains authorized.

Use an OS-protected authorization token or an operator-controlled libpq service.
No credentials, participant measurements or decryption key belongs in arguments.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from urllib import request
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def configured_authorization(path):
    """This credential can only ask whether a registered extract is usable."""
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(config) != {"service_url", "token_file"}:
        raise ValueError("Unexpected research workspace configuration")
    url = urlsplit(config["service_url"])
    if (
        url.scheme != "https" or url.hostname != "wfjlzecckihsgcvauqwt.supabase.co"
        or url.port not in (None, 443) or url.username or url.password or url.query or url.fragment
        or not re.fullmatch(r"/functions/v1/qs-service(?:-smoke-[0-9a-f]{8})?", url.path)
    ):
        raise ValueError("Unexpected research authorization destination")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from addon.vault import _dpapi

    token = _dpapi(Path(config["token_file"]).read_bytes(), True).decode("utf-8")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise ValueError("Invalid research authorization credential")
    return config["service_url"], token


def allowed_extract(extract, *, config_path=None):
    try:
        if config_path is not None:
            service, token = configured_authorization(config_path)
            headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
            if "-smoke-" in service:
                headers["x-qs-smoke-key"] = token
            req = request.Request(service + "/internal/research-use",
                data=json.dumps({"extract_id": str(uuid.UUID(str(extract)))}).encode(),
                headers=headers, method="POST")
            with request.build_opener(NoRedirect()).open(req, timeout=5) as response:
                raw = response.read(4097)
                if response.status != 200 or len(raw) > 4096:
                    return False
                value = json.loads(raw)
                return (
                    isinstance(value, dict) and set(value) == {"allowed"}
                    and value["allowed"] is True
                )
        settings = {
            "service": os.environ["PGSERVICE"], "sslmode": "verify-full",
            "sslrootcert": "system", "connect_timeout": 5,
            "options": "-c statement_timeout=5000",
        }
        with psycopg.connect(**settings) as db:
            return db.execute(
                "select qs_governance.check_extract_use(%s)", (extract,)
            ).fetchone()[0] is True
    except Exception:
        # Transport errors can contain credentials. Fail closed without logging them.
        return False


def stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW, check=False, timeout=15,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_guarded(command, allowed, *, interval=5):
    """Authorization outages stop work; they never attest to copy deletion."""
    if not allowed():
        return 2
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {
        "start_new_session": True
    }
    process = subprocess.Popen(command, **options)
    try:
        while True:
            try:
                code = process.wait(timeout=interval)
            except subprocess.TimeoutExpired:
                code = None
            if not allowed():
                stop(process)
                return 2
            if code is not None:
                return code
    finally:
        stop(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", type=uuid.UUID, required=True)
    parser.add_argument(
        "--config", type=Path, help="Operator workspace config with an OS-protected token"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not (args.config or os.environ.get("PGSERVICE")):
        parser.error("Supply a command after -- and --config or an operator-configured PGSERVICE")
    code = run_guarded(command, lambda: allowed_extract(args.extract, config_path=args.config))
    if code == 2:
        print("Research stopped: extract access is paused, withdrawn or unavailable. "
              "Check the research operations report for copy-deletion tasks.")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
