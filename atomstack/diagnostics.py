"""Shared inspectable snapshots for native UI and terminal diagnostics."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from . import __version__


def snapshot(controller, port):
    c = controller
    return {
        "version": __version__, "pid": os.getpid(), "port": port,
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "connected": c.connected, "ready": c.ready, "phase": c.phase,
        "state": c.status.state if c.status else None,
        "machine_position": c.status.machine if c.status else None,
        "status_age_seconds": round(c.clock() - c.status_at, 3) if c.status else None,
        "power": c.status.power if c.status else None, "laser_off_verified": c.laser_off,
        "firmware": c.firmware, "profile_verified": c.profile_ok,
        "home_state": c.home_state, "origin": c.origin,
        "physical_laser_disconnected": c.physical_laser_off,
        "pending": c.pending.text if c.pending else None,
        "rx_count": c.rx_count, "tx_count": c.tx_count, "message": c.message,
        "log_tail": [[kind, "[network details omitted]" if text.startswith("[MSG:Mode=") else text]
                     for kind, text in list(c.log)[-100:]],
    }


class Reporter:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.last = -float("inf")

    def write(self, controller, port, force=False):
        if not self.path or (not force and time.monotonic() - self.last < 1):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(snapshot(controller, port), indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.last = time.monotonic()
