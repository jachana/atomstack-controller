"""GRBL report parsing. Unknown vendor fields are retained, never interpreted as commands."""
from dataclasses import dataclass, field
import math
import re


def numbers(value: str) -> tuple[float, ...]:
    result = tuple(float(x) for x in value.split(","))
    if not result or not all(math.isfinite(x) for x in result):
        raise ValueError("Non-finite or missing coordinates")
    return result


@dataclass(frozen=True)
class Status:
    state: str
    machine: tuple[float, float] | None
    work: tuple[float, float] | None
    power: float | None
    fields: dict[str, str] = field(default_factory=dict)


def parse_status(line: str) -> Status:
    if not line.startswith("<") or not line.endswith(">"):
        raise ValueError("Incomplete status report")
    parts = line[1:-1].split("|")
    fields = {}
    for part in parts[1:]:
        if ":" in part:
            key, value = part.split(":", 1)
            if key in fields:
                raise ValueError("Duplicate status field")
            fields[key] = value
    def xy(key):
        values = numbers(fields[key])
        if len(values) < 2:
            raise ValueError("X and Y required")
        return values[:2]
    machine = xy("MPos") if "MPos" in fields else None
    work = xy("WPos") if "WPos" in fields else None
    # Never reuse a cached WCO: it may have changed in another sender.
    if machine is None and work is not None and "WCO" in fields:
        offset = xy("WCO")
        machine = (work[0] + offset[0], work[1] + offset[1])
    power = None
    if "FS" in fields:
        fs = numbers(fields["FS"])
        if len(fs) != 2:
            raise ValueError("Invalid feed/spindle report")
        power = fs[1]
    return Status(parts[0], machine, work, power, fields)


def parse_setting(line: str):
    match = re.fullmatch(r"\$(\d+)=([-+]?\d+(?:\.\d+)?)", line)
    return (int(match[1]), float(match[2])) if match else None


EXPECTED = {13: 0, 20: 0, 21: 1, 22: 1, 23: 3, 30: 1000, 32: 1, 130: 365, 131: 305}
READ_COMMANDS = ("$I", "$$", "?", "$#", "$G")
