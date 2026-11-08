"""Run a registered research job only while its extract remains authorized.

Use an operator-controlled libpq service (PGSERVICE) with verified TLS. No DSN,
password, participant measurements or decryption key belongs in command arguments.
"""

import argparse
import os
import signal
import subprocess
import uuid

import psycopg


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
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not os.environ.get("PGSERVICE"):
        parser.error("Supply a command after -- and an operator-configured PGSERVICE")

    def allowed():
        try:
            with psycopg.connect(
                service=os.environ["PGSERVICE"], sslmode="verify-full", sslrootcert="system",
                connect_timeout=5, options="-c statement_timeout=5000"
            ) as db:
                return db.execute(
                    "select qs_governance.check_extract_use(%s)", (args.extract,)
                ).fetchone()[0] is True
        except Exception:
            return False

    code = run_guarded(command, allowed)
    if code == 2:
        print("Research stopped: extract access is paused, withdrawn or unavailable. "
              "Check the research operations report for copy-deletion tasks.")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
