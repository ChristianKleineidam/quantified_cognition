"""Install the first research upload alert using Silentworker's existing mobile target."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path

import yaml
from health_notifications import validate

CONFIG = Path("/opt/homeassistant/config")
PACKAGE = """\
command_line:
  - sensor:
      name: Quantified Cognition First Research Uploads
      unique_id: quantified_cognition_first_research_uploads
      command: >-
        cat /config/quantified_cognition_research/first_uploads 2>/dev/null || echo unavailable
      scan_interval: 60

counter:
  quantified_cognition_research_uploads_notified:
    initial: 0
    restore: true

automation:
  - id: quantified_cognition_first_research_upload
    alias: Quantified Cognition first research upload
    mode: single
    triggers:
      - trigger: time_pattern
        minutes: "/1"
    variables:
      current: "{{ states('sensor.quantified_cognition_first_research_uploads') | int(-1) }}"
      notified: >-
        {{ states('counter.quantified_cognition_research_uploads_notified') | int(-1) }}
    conditions:
      - condition: template
        value_template: "{{ notified >= 0 and current > notified }}"
    actions:
      - action: __NOTIFY_ACTION__
        target: __NOTIFY_TARGET__
        data:
          title: Quantified Cognition
          message: >-
            {% set added = current - notified %}
            {{ 'One new participant sent a first research upload.' if added == 1 else
               (added ~ ' new participants sent their first research uploads.') }}
      - action: counter.set_value
        target:
          entity_id: counter.quantified_cognition_research_uploads_notified
        data:
          value: "{{ current }}"
"""


def render(automations):
    blocks = re.split(r"(?m)(?=^- id:)", automations)
    alias = "\n  alias: silentworker health problem notification\n"
    matches = [block for block in blocks if alias in block]
    if len(matches) != 1:
        raise ValueError("Expected exactly one Silentworker health rule")
    [rule] = yaml.safe_load(matches[0])
    [action] = rule["actions"]
    service, target = action["action"], action["target"]
    if service != "notify.send_message" or not target:
        raise ValueError("Unexpected Silentworker notification destination")
    return PACKAGE.replace("__NOTIFY_ACTION__", service).replace(
        "__NOTIFY_TARGET__", json.dumps(target)
    )


def main():
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "silentworker":
        raise SystemExit("Run this reviewed integration as root on silentworker")
    path = CONFIG / "packages" / "quantified_cognition_research.yaml"
    desired = render((CONFIG / "automations.yaml").read_text())
    if path.exists():
        if path.read_text() != desired:
            raise RuntimeError("The research alert package differs from the reviewed configuration")
        print("Research upload alert is already configured")
        return
    validate()
    with path.open("x", encoding="utf-8") as output:
        output.write(desired)
    try:
        validate()
    except BaseException:
        path.unlink()
        raise
    subprocess.run(
        ["docker", "restart", "homeassistant"], capture_output=True, check=True, timeout=90
    )
    print("First research upload alert configured for the existing Silentworker destination")


if __name__ == "__main__":
    main()
