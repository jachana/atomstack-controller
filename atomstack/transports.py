"""Byte transport boundary. A future Wi-Fi adapter only implements this protocol."""
from typing import Protocol
from collections import deque
import re


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
    """Synthetic motion/status; never opens a port. Actual settings come from the fixture."""
    def __init__(self):
        from pathlib import Path
        self.fixture = (Path(__file__).parent / "fixtures" / "observed.txt").read_text()
        self.rx = deque()
        self.writes = []
        self.position = [-288.0, -301.0]
        self.power = 0
        self.closed = False

    def read(self):
        return self.rx.popleft() if self.rx else b""

    def write(self, data):
        if self.closed:
            raise OSError("Simulator disconnected")
        self.writes.append(data)
        command = data.decode("ascii", errors="replace").strip()
        response = ""
        if data == b"?":
            response = f"<Idle|MPos:{self.position[0]:.3f},{self.position[1]:.3f},0.000|FS:0,{self.power}|APP:51|USB:0>\n"
        elif command == "$I":
            response = "[VER:V1.055.Oct 13 2023:]\n[OPT:HLSW,512,2048]\nok\n"
        elif command == "$$":
            response = "\n".join(x for x in self.fixture.splitlines() if x.startswith("$")) + "\nok\n"
        elif command == "$#":
            response = "[G54:0.000,0.000,0.000]\n[G92:0.000,0.000,0.000]\n[TLO:0.000]\nok\n"
        elif command == "$G":
            spindle = f"M4 S{self.power}" if self.power else "M5 S0"
            response = f"[GC:G0 G54 G17 G21 G90 G94 {spindle} M9 T0 F0]\nok\n"
        elif command in ("M5 S0", "M5", "S0"):
            self.power = 0
            response = "ok\n"
        elif re.fullmatch(r"M4 S(?:[0-9]|[1-9][0-9]{1,2}|1000)", command):
            self.power = int(command.split("S")[1])
            response = "ok\n"
        elif command in ("G21", "G90"):
            response = "ok\n"
        elif re.fullmatch(r"G53 G[01] X-?\d+(?:\.\d+)? Y-?\d+(?:\.\d+)?(?: F\d+)?", command):
            match = re.search(r"X(-?\d+(?:\.\d+)?) Y(-?\d+(?:\.\d+)?)", command)
            self.position = [float(match[1]), float(match[2])]
            response = "ok\n"
        elif command == "$H":
            self.position = [-288.0, -301.0]
            response = "<Home|MPos:-288,-301,0|FS:0,0>\nok\n"
        elif command.startswith("$J="):
            relative = re.fullmatch(r"\$J=G21 G91 ([XY])(-?(?:0\.100|1\.000|5\.000|10\.000)) F(?:180|300|600|1000|1200|3000|6000|12000|20000)", command)
            absolute = re.fullmatch(r"\$J=G21 G90 G53 X(-?\d+(?:\.\d+)?) Y(-?\d+(?:\.\d+)?) F(?:180|300|600|1000|1200|3000|6000|12000|20000)", command)
            if relative:
                self.position[0 if relative[1] == "X" else 1] += float(relative[2])
                response = "ok\n"
            elif absolute:
                self.position = [float(absolute[1]), float(absolute[2])]
                response = "ok\n"
            else:
                response = "error:20\n"
        elif data == b"\x18":
            response = "Grbl 1.1h ['$' for help]\n"
        elif data in (b"\x85", b"!", b"~"):
            pass
        else:
            response = "error:20\n"
        if response:
            self.rx.append(response.encode())

    def close(self):
        self.closed = True
