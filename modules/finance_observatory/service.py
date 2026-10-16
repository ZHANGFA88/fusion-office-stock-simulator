"""Read-only finance observatory demo service. Never performs provider I/O."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import demo_response

_FIXTURE = Path(__file__).with_name("fixtures") / "demo.json"


def get_observatory_payload() -> dict[str, Any]:
    with _FIXTURE.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    # The public/demo route is deliberately unable to emit LIVE records.
    response = demo_response(payload)
    response["demo"] = True
    return response
