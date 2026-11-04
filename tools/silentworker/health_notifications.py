"""Preserve the existing channel while reporting new QC faults during other faults."""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

OLD = """  triggers:
  - trigger: state
    entity_id:
    - binary_sensor.silentworker_health_problem
    to:
    - 'on'
  conditions: []
"""
NEW = """  triggers:
  - trigger: state
    entity_id:
    - binary_sensor.silentworker_health_problem
  conditions:
  - condition: template
    value_template: >-
      {% set qc_current = trigger.to_state.attributes.failed_checks | default([])
         | select('match', '^quantified-cognition-') | sort | list %}
      {% set qc_previous = (trigger.from_state.attributes.failed_checks | default([])
         if trigger.from_state is not none else [])
         | select('match', '^quantified-cognition-') | sort | list %}
      {{ trigger.to_state.state == 'on' and
         (trigger.from_state is none or trigger.from_state.state != 'on' or
          (qc_current != qc_previous and qc_current | count > 0)) }}
"""


def patch(text):
    blocks = re.split(r"(?m)(?=^- id:)", text)
    matches = [
        index
        for index, block in enumerate(blocks)
        if "  alias: silentworker health problem notification\n" in block
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one existing health notification rule")
    index = matches[0]
    if NEW in blocks[index]:
        return text, False
    if OLD not in blocks[index]:
        raise ValueError("Health rule differs from the reviewed configuration")
    blocks[index] = blocks[index].replace(OLD, NEW, 1)
    return "".join(blocks), True


def validate():
    result = subprocess.run(
        [
            "docker",
            "exec",
            "homeassistant",
            "python",
            "-m",
            "homeassistant",
            "--script",
            "check_config",
            "--config",
            "/config",
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError("Home Assistant configuration check failed; details withheld")


def main():
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "silentworker":
        raise SystemExit("Run this reviewed integration as root on silentworker")
    path = Path("/opt/homeassistant/config/automations.yaml")
    original = path.read_text()
    updated, changed = patch(original)
    if not changed:
        print("Existing notification channel already reports new Quantified Cognition failures")
        return
    validate()
    saved = Path("/etc/quantified-cognition-ops/home-assistant-automations-before.yaml")
    if not saved.exists():
        shutil.copyfile(path, saved)
        saved.chmod(0o600)
    if path.read_text() != original:
        raise RuntimeError("Automation changed during review")
    try:
        path.write_text(updated)
        validate()
    except BaseException:
        if path.read_text() == updated:
            path.write_text(original)
        raise
    subprocess.run(
        ["docker", "restart", "homeassistant"], capture_output=True, check=True, timeout=90
    )
    print("Updated only the existing health trigger/condition; notification target unchanged")


if __name__ == "__main__":
    main()
