"""Byte transport boundary. A future Wi-Fi adapter only implements this protocol."""
from typing import Protocol
from collections import deque
import re

from .protocol import parse_setting


class Transport(Protocol):
    def read(self) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


def list_ports():
    from serial.tools import list_ports as ports
    return sorted(ports.comports(), key=lambda p: (p.vid is None, p.device))


class SerialTransport:
    def __init__(self, port: str):
        import serial
        # Do not intentionally toggle reset lines. Some USB bridges still reset on open.
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=0.5)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    def read(self):
        return self.serial.read(min(self.serial.in_waiting, 16384))

    def write(self, data):
        if self.serial.write(data) != len(data):
            raise OSError("Incomplete USB write")

    def close(self):
        self.serial.close()


class Simulator:
    """Synthetic GRBL controller. Never opens a port.

    This models the *machine*, not the app. It deliberately accepts commands the
    controller would refuse, because a simulator stricter than the real firmware
    hides exactly the bugs it exists to catch: if the simulator rejects an unsafe
    jog, the test passes whether or not the controller guard is present. Bounds,
    feed limits and command whitelisting belong in the controller, and the tests
    have to be able to see when they go missing.

    Behaviour follows the observed fixture. Settings come from the real ``$$``
    dump, spindle speed clamps to ``$30`` the way GRBL does rather than erroring,
    and soft limits stay off because the fixture reports ``$20=0``, so the
    firmware accepts moves past the table edge.

    ``error:20`` is reserved for genuinely unrecognised commands, which is what
    GRBL uses it for.
    """

    JOG_RELATIVE = re.compile(r"\$J=G21 G91 ([XYZ])(-?\d+(?:\.\d+)?) F(\d+(?:\.\d+)?)")
    JOG_ABSOLUTE = re.compile(
        r"\$J=G21 G90 G53 X(-?\d+(?:\.\d+)?) Y(-?\d+(?:\.\d+)?) F(\d+(?:\.\d+)?)")
    MOVE = re.compile(
        r"G53 G([01]) X(-?\d+(?:\.\d+)?) Y(-?\d+(?:\.\d+)?)(?: F(\d+(?:\.\d+)?))?")
    SPINDLE = re.compile(r"M([34]) S(\d+)")

    def __init__(self):
        from pathlib import Path
        self.fixture = (Path(__file__).parent / "fixtures" / "observed.txt").read_text()
        self.settings = {}
        for line in self.fixture.splitlines():
            parsed = parse_setting(line.strip())
            if parsed:
                self.settings[parsed[0]] = parsed[1]
        self.rx = deque()
        self.writes = []
        self.home = [-288.0, -301.0]
        self.position = list(self.home)
        self.power = 0
        self.closed = False

    @property
    def max_power(self):
        return self.settings.get(30, 1000)

    @property
    def soft_limits(self):
        return bool(self.settings.get(20, 0))

    def read(self):
        return self.rx.popleft() if self.rx else b""

    def status_line(self):
        return ("<Idle|MPos:{:.3f},{:.3f},0.000|FS:0,{}|APP:51|USB:0>\n"
                .format(self.position[0], self.position[1], self.power))

    def travel_error(self, x, y):
        """GRBL error:15 when soft limits are on and a target leaves the table.

        The fixture reports ``$20=0``, so this returns None and the machine
        accepts the move. That is deliberate: the controller is what must refuse
        it, and a test can only prove that if the simulator does not.
        """
        if not self.soft_limits:
            return None
        span_x = self.settings.get(130, 365.0)
        span_y = self.settings.get(131, 305.0)
        if not (self.home[0] <= x <= self.home[0] + span_x
                and self.home[1] <= y <= self.home[1] + span_y):
            return "error:15\n"
        return None

    def write(self, data):
        if self.closed:
            raise OSError("Simulator disconnected")
        self.writes.append(data)
        response = self.respond(data, data.decode("ascii", errors="replace").strip())
        if response:
            self.rx.append(response.encode())

    def respond(self, data, command):
        if data == b"?":
            return self.status_line()
        if data == b"\x18":
            self.position = list(self.home)
            self.power = 0
            return "Grbl 1.1h ['$' for help]\n"
        if data in (b"\x85", b"!", b"~"):
            return ""
        if command == "$I":
            return "[VER:V1.055.Oct 13 2023:]\n[OPT:HLSW,512,2048]\nok\n"
        if command == "$$":
            settings = "\n".join(x for x in self.fixture.splitlines() if x.startswith("$"))
            return settings + "\nok\n"
        if command == "$#":
            return "[G54:0.000,0.000,0.000]\n[G92:0.000,0.000,0.000]\n[TLO:0.000]\nok\n"
        if command == "$G":
            spindle = "M4 S{}".format(self.power) if self.power else "M5 S0"
            return "[GC:G0 G54 G17 G21 G90 G94 {} M9 T0 F0]\nok\n".format(spindle)
        if command == "$H":
            self.position = list(self.home)
            self.power = 0
            return "<Home|MPos:{:g},{:g},0|FS:0,0>\nok\n".format(*self.home)
        if command in ("M5", "S0", "M5 S0"):
            self.power = 0
            return "ok\n"
        if command in ("G21", "G90", "G91", "G54", "G94"):
            return "ok\n"

        spindle = self.SPINDLE.fullmatch(command)
        if spindle:
            # GRBL clamps to $30 rather than refusing an over-range speed.
            self.power = min(int(spindle[2]), int(self.max_power))
            return "ok\n"

        move = self.MOVE.fullmatch(command)
        if move:
            return self.go(float(move[2]), float(move[3]))

        absolute = self.JOG_ABSOLUTE.fullmatch(command)
        if absolute:
            return self.go(float(absolute[1]), float(absolute[2]))

        relative = self.JOG_RELATIVE.fullmatch(command)
        if relative:
            axis, delta = relative[1], float(relative[2])
            if axis == "Z":
                return "ok\n"
            target = list(self.position)
            target[0 if axis == "X" else 1] += delta
            return self.go(*target)

        return "error:20\n"

    def go(self, x, y):
        error = self.travel_error(x, y)
        if error:
            return error
        self.position = [x, y]
        return "ok\n"

    def close(self):
        self.closed = True
