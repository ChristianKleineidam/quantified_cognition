"""Create a credential-free PostgreSQL cluster for isolated backend tests."""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def cluster(binaries: Path):
    import psycopg

    binaries = binaries.resolve(strict=True)
    for name in ("initdb", "pg_ctl", "postgres"):
        if not (binaries / name).is_file():
            raise RuntimeError(f"Missing native PostgreSQL executable: {name}")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    with tempfile.TemporaryDirectory(prefix="qc-postgres-") as temporary:
        directory = Path(temporary)
        data, log = directory / "data", directory / "server.log"
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        subprocess.run(
            [
                str(binaries / "initdb"),
                "-D",
                str(data),
                "--username=postgres",
                "--auth-local=trust",
                "--auth-host=trust",
                "--encoding=UTF8",
                "--locale=C",
            ],
            env=environment,
            check=True,
            capture_output=True,
            timeout=60,
        )
        options = (
            f"-h 127.0.0.1 -p {port} -k {directory} "
            "-c quantified_cognition.test_cluster=on -c shared_buffers=16MB "
            "-c max_connections=60 -c fsync=off -c log_min_error_statement=panic"
        )
        started = False
        try:
            subprocess.run(
                [
                    str(binaries / "pg_ctl"),
                    "-D",
                    str(data),
                    "-l",
                    str(log),
                    "-o",
                    options,
                    "-w",
                    "start",
                ],
                env=environment,
                check=True,
                capture_output=True,
                timeout=60,
            )
            started = True
            dsn = f"host=127.0.0.1 port={port} user=postgres dbname=postgres sslmode=disable"
            with psycopg.connect(dsn) as connection:
                assert (
                    connection.execute(
                        "select current_setting('quantified_cognition.test_cluster')"
                    ).fetchone()[0]
                    == "on"
                )
            yield dsn
        finally:
            # Stop even if startup timed out after the postmaster was launched.
            if started or (data / "postmaster.pid").exists():
                subprocess.run(
                    [str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                    env=environment,
                    check=True,
                    capture_output=True,
                    timeout=30,
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    with cluster(args.postgres_bin) as dsn:
        environment = {**os.environ, "QC_PG_TEST_DSN": dsn}
        files = ["supabase/tests/test_concurrency.py", "supabase/tests/test_recovery.py"]
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *files, "-q", "-ra", f"--junitxml={args.report}"],
            env=environment,
            check=False,
            timeout=600,
        )
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
