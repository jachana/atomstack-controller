"""Single-threaded session state machine; transport and UI contain no motion policy.

Only one request is in flight. Status queries complete on a status report;
newline commands complete on an acknowledgement.
No stored coordinate is ever trusted across a connection or reset.
"""
from collections import deque
from dataclasses import dataclass
import math
import re
import time

from .protocol import EXPECTED, READ_COMMANDS, parse_setting, parse_status

JOG_FEEDS = (180, 300, 600, 1000, 1200, 3000, 6000, 12000, 20000)
PLANNER_BLOCKS = 16  # GRBL 1.1 BLOCK_BUFFER_SIZE; how far ahead an ack can be held.


class GuardError(RuntimeError):
    pass


@dataclass
class Command:
    text: str
    tag: str
    timeout: float = 5.0


class Controller:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.transport = None
        self.log = deque(maxlen=800)
        self.rx_count = 0
        self.tx_count = 0
        self._clear()
        self.message = "Connect USB or open the simulator. Motion is locked."

    def _clear(self):
        self.queue = deque()
        self.pending = None
        self.sent_at = 0.0
        self.buffer = b""
        self.settings = {}
        self.firmware = "Not read"
        self.parameters = {}
        self.modal = ""
        self.status = None
        self.status_at = -math.inf
        self.origin = None
        self.home_state = "Not homed"
        self.target = None
        self.last_position = None
        self.ready = False
        self.laser_off = False
        self.physical_laser_off = False  # Optional user fact; never required by a motion guard.
        self.phase = "disconnected"
        # Set here as well as in attach(): tick() reads it whenever the phase is
        # "settling", and a cleared session must never leave it undefined.
        self.settle_until = math.inf
        self.motion_deadline = math.inf
        self.last_poll = -math.inf
        self.last_tick = None
        self.deferred_motion = None
        self.frame_waypoints = deque()
        self.frame_feed = None
        self.job_commands = deque()
        self.job_durations = deque()
        self.job_window = deque(maxlen=PLANNER_BLOCKS)
        self.job_total = 0
        self.job_done = 0
        self.job_paused = False
        self.job_expected_power = 0
        self.job_final_target = None

    @property
    def connected(self):
        return self.transport is not None

    @property
    def app_position(self):
        if self.origin is None or self.status is None or self.status.machine is None:
            return None
        return tuple(a - b for a, b in zip(self.status.machine, self.origin))

    @property
    def profile_ok(self):
        return all(self.settings.get(k) == v for k, v in EXPECTED.items())

    @property
    def max_xy_feed(self):
        return int(min(self.settings.get(110, 0), self.settings.get(111, 0)))

    @property
    def bounds_text(self):
        """Bed limits for an error message, in the machine's own numbers."""
        travel = self.travel
        if travel is None:
            return "the machine travel limits, which have not been read yet"
        return f"0-{travel[0]:g} X / 0-{travel[1]:g} Y mm"

    @property
    def travel(self):
        """Bed span in millimetres, taken from the live $130/$131.

        None until both have been read, so bounds checks fail closed rather than
        falling back to a constant that the machine has not confirmed.
        """
        span_x, span_y = self.settings.get(130), self.settings.get(131)
        if span_x is None or span_y is None or span_x <= 0 or span_y <= 0:
            return None
        return (span_x, span_y)

    def attach(self, transport, settle=2.0):
        if self.connected:
            self.disconnect()
        self._clear()
        self.transport = transport
        self.phase = "settling"
        self.settle_until = self.clock() + settle
        self.message = "Waiting for USB startup; no motion commands sent."

    def _write(self, data):
        if not self.connected:
            raise GuardError("Connect first.")
        self.transport.write(data)
        self.tx_count += 1
        self.log.append(("TX", f"{data!r}  [hex: {data.hex(' ')}]"))

    def _enqueue(self, text, tag=None, timeout=5.0):
        self.queue.append(Command(text, tag or text, timeout))

    def _invalidate(self):
        self.origin = None
        self.home_state = "Not homed"
        self.ready = False
        self.laser_off = False
        self.target = None

    def disconnect(self):
        transport = self.transport
        if transport:
            try:
                # Reset is needed to abort homing; jog cancel alone cannot abort $H.
                if self.phase.startswith("home"):
                    transport.write(b"\x18")
                elif self.phase.startswith(("jog", "frame", "job")):
                    # Jog cancel aborts both ordinary jogs and the absolute
                    # jog segments used for the frame outline.
                    transport.write(b"!")
                    transport.write(b"\x85")
                    if self.phase.startswith("job"):
                        transport.write(b"\x18")
            except Exception:
                pass
            finally:
                try:
                    transport.close()
                except Exception:
                    pass
        self.transport = None
        self._clear()
        self.message = "Disconnected. Home reference cleared."

    def fault(self, reason):
        # Unexpected external motion may be reported while our phase is idle.
        # A fault must request a stop even when this sender did not start it.
        self.stop()
        self.message = reason + " Reconnect and home again."
        self.log.append(("FAULT", self.message))

    def stop(self):
        if self.connected:
            # Attempt each independently: a failed hold write must not skip reset.
            for command in (b"!", b"\x85", b"\x18"):
                try:
                    self._write(command)
                except Exception:
                    pass
        self.phase = "stopping"
        self.disconnect()
        self.message = "Stop requested; session closed. Check the machine, then reconnect."

    def set_physical_laser_off(self, checked):
        """Retained for old reports/clients; physical disconnection is optional."""
        self.physical_laser_off = bool(checked)

    def query(self, command):
        if command not in READ_COMMANDS:
            raise GuardError("Only the five diagnostic commands are available.")
        if not self.connected or not self.ready or self.phase != "idle" or self.pending or self.queue:
            raise GuardError("Wait for diagnostics or motion to finish.")
        self._enqueue(command)

    def _status_poll_pending(self):
        return self.pending is not None and self.pending.text == "?" and not self.queue

    def _awaiting_position(self):
        """True when the next report is what a motion request should wait for.

        Either a poll is already in flight, or nothing has been heard recently
        enough to act on — after a modal dialog stopped the tick loop, say.
        """
        return self._status_poll_pending() or self.status is None or self.clock() - self.status_at > 1.5

    def _guard(self, require_home=False, allow_status_poll=False, allow_stale=False):
        pending_blocks = self.pending is not None and not (allow_status_poll and self._status_poll_pending())
        if not self.connected or not self.ready or self.phase != "idle" or pending_blocks or self.queue:
            raise GuardError("Wait for the connection and diagnostics to become ready.")
        if not self.profile_ok:
            raise GuardError("Live settings do not match the observed machine profile.")
        if not self.laser_off:
            raise GuardError("The controller must report M5 S0 before motion.")
        # allow_stale checks a request that is about to wait for a fresh report;
        # it runs again in full once that report arrives.
        if not self.status or (not allow_stale and self.clock() - self.status_at > 1.5):
            raise GuardError("Position is stale. Wait for a fresh status report.")
        allowed_states = ("Idle",) if require_home else ("Idle", "Alarm")
        if self.status.state not in allowed_states or self.status.machine is None:
            raise GuardError("A fresh Idle report with machine X/Y is required.")
        if self.status.power != 0:
            raise GuardError("A status report confirming zero laser power is required.")
        if require_home and (self.origin is None or self.home_state != "Confirmed"):
            raise GuardError("Home, then confirm the head is physically at bottom-left.")

    def home(self):
        if self._awaiting_position():
            self._guard(allow_status_poll=True, allow_stale=True)
            if self.deferred_motion:
                raise GuardError("A motion request is already waiting for the current position.")
            self.deferred_motion = ("home",)
            self.message = "Home queued behind the current position update."
            return
        self._guard()
        self.origin = None
        self.home_state = "Homing"
        self.phase = "home-command"
        self.motion_deadline = self.clock() + 95
        self._enqueue("$H", "home", 90)
        self.message = "Homing using the firmware's existing homing speeds."

    def confirm_home(self):
        if self.phase != "home-confirm" or self.home_state != "Awaiting confirmation":
            raise GuardError("First complete a home cycle in this session.")
        if (not self.laser_off or not self.status
                or self.status.state != "Idle" or self.status.machine is None
                or self.clock() - self.status_at > 1.5 or self.status.power != 0):
            raise GuardError("Fresh Idle coordinates and laser-off confirmation are required.")
        self.origin = self.status.machine
        self.last_position = self.status.machine
        self.home_state = "Confirmed"
        self.phase = "idle"
        self.message = "Bottom-left is app (0, 0). Test one positive axis step at a time."

    def jog(self, axis, direction, distance=1.0, feed=300):
        if axis not in ("X", "Y") or direction not in (-1, 1):
            raise GuardError("Only single-axis jogs are permitted.")
        if distance not in (0.1, 1.0, 5.0, 10.0):
            raise GuardError("Jog distance must be 0.1, 1, 5, or 10 mm.")
        if feed not in JOG_FEEDS or feed > self.max_xy_feed:
            raise GuardError("Jog speed must be one of the guarded presets.")
        if self._awaiting_position():
            self._guard(require_home=True, allow_status_poll=True, allow_stale=True)
            if self.deferred_motion:
                raise GuardError("A motion request is already waiting for the current position.")
            self.deferred_motion = ("jog", axis, direction, distance, feed)
            self.message = "Jog queued behind the current position update."
            return
        self._guard(require_home=True)
        app = self.app_position
        index = 0 if axis == "X" else 1
        target = list(app)
        delta = direction * distance
        target[index] += delta
        if not self._inside(app) or not self._inside(target):
            raise GuardError(f"Jog blocked: target is outside {self.bounds_text}.")
        machine_target = list(self.status.machine)
        machine_target[index] += delta
        self.target = tuple(machine_target)
        self.phase = "jog-command"
        self.motion_deadline = self.clock() + distance / feed * 60 + 5
        self._enqueue(f"$J=G21 G91 {axis}{delta:.3f} F{feed}", "jog")
        direction_name = {("X", 1): "right", ("X", -1): "left", ("Y", 1): "back", ("Y", -1): "front"}[(axis, direction)]
        self.message = f"Moving {distance:g} mm {direction_name} at {feed} mm/min."

    def jog_to(self, x, y, feed=3000):
        try:
            point = (float(x), float(y))
        except (TypeError, ValueError):
            raise GuardError("Click-to-jog requires numeric X/Y coordinates.")
        if not all(math.isfinite(value) for value in point) or not self._inside(point):
            raise GuardError(f"Click-to-jog target is outside {self.bounds_text}.")
        if feed not in JOG_FEEDS or feed > self.max_xy_feed:
            raise GuardError("Click-to-jog speed exceeds the live machine limit.")
        if self._awaiting_position():
            self._guard(require_home=True, allow_status_poll=True, allow_stale=True)
            if self.deferred_motion:
                raise GuardError("A motion request is already waiting for the current position.")
            self.deferred_motion = ("jog-to", point[0], point[1], feed)
            self.message = "Click-to-jog queued behind the current position update."
            return
        self._guard(require_home=True)
        self.target = (self.origin[0] + point[0], self.origin[1] + point[1])
        distance = math.dist(self.status.machine, self.target)
        if distance < 0.05:
            self.target = None
            self.message = f"Already at X {point[0]:.1f}, Y {point[1]:.1f}."
            return
        self.phase = "jog-command"
        self.motion_deadline = self.clock() + distance / feed * 60 + 5
        self._enqueue(f"$J=G21 G90 G53 X{self.target[0]:.3f} Y{self.target[1]:.3f} F{feed}", "jog")
        self.message = f"Moving to X {point[0]:.1f}, Y {point[1]:.1f} at {feed} mm/min · laser off."

    def frame(self, app_points, feed=600):
        points = tuple(tuple(float(value) for value in point) for point in app_points)
        if len(points) < 2 or any(len(point) != 2 or not all(math.isfinite(v) for v in point) for point in points):
            raise GuardError("Frame requires a finite outline with at least two points.")
        if feed not in JOG_FEEDS or feed > self.max_xy_feed:
            raise GuardError("Frame speed must be one of the guarded presets.")
        if any(not self._inside(point) for point in points):
            raise GuardError("Frame blocked: its outline exceeds the app travel bounds.")
        if self._awaiting_position():
            self._guard(require_home=True, allow_status_poll=True, allow_stale=True)
            if self.deferred_motion:
                raise GuardError("A motion request is already waiting for the current position.")
            self.deferred_motion = ("frame", points, feed)
            self.message = "Frame queued behind the current position update."
            return
        self._guard(require_home=True)
        self.frame_waypoints = deque((self.origin[0] + x, self.origin[1] + y) for x, y in points)
        self.frame_feed = feed
        self._start_frame_segment()

    def frame_readiness(self):
        """Return a short UI-facing reason without changing controller state."""
        try:
            self._guard(require_home=True, allow_status_poll=True)
        except GuardError as exc:
            return False, str(exc)
        if self.deferred_motion:
            return False, "Another motion request is waiting."
        return True, "Ready to trace the orange outline with the laser off."

    def _start_frame_segment(self):
        if not self.frame_waypoints:
            self.phase = "idle"
            self.target = None
            self.motion_deadline = math.inf
            self.message = "Frame complete. Laser remained off. Check the head path before running a job."
            return
        self.target = self.frame_waypoints.popleft()
        current = self.status.machine
        travel = self.travel
        longest = math.dist((0, 0), travel) if travel else 500
        distance = math.dist(current, self.target) if current else longest
        self.phase = "frame-command"
        self.motion_deadline = self.clock() + distance / self.frame_feed * 60 + 5
        self._enqueue(f"$J=G21 G90 G53 X{self.target[0]:.3f} Y{self.target[1]:.3f} F{self.frame_feed}", "frame")
        self.message = f"Framing with laser off · {len(self.frame_waypoints) + 1} points remaining."

    def run_job(self, lines):
        lines = tuple(lines)
        # Validating the job against a slightly old position is fine: if it has
        # to wait for a fresh report, the whole request runs again on arrival.
        self._guard(require_home=True, allow_status_poll=True, allow_stale=True)
        commands = []
        durations = []
        final_target = None
        position = self.status.machine
        for raw in lines:
            command = raw.strip()
            if not command or command.startswith(";"):
                continue
            move_target, seconds = self._validate_job_command(command, position)
            if move_target is not None:
                final_target = position = move_target
            commands.append(command)
            durations.append(seconds)
        if not commands or commands[-2:] != ["M5", "S0"]:
            raise GuardError("Generated job must end with M5 and S0.")
        if self._awaiting_position():
            if self.deferred_motion:
                raise GuardError("A motion request is already waiting for the current position.")
            self.deferred_motion = ("job", lines)
            self.message = "Job queued behind the current position update."
            return
        self.job_commands = deque(commands)
        self.job_durations = deque(durations)
        self.job_window = deque(maxlen=PLANNER_BLOCKS)
        self.job_total = len(commands)
        self.job_done = 0
        self.job_paused = False
        self.job_expected_power = 0
        self.job_final_target = final_target
        self.phase = "job-command"
        # The job is only late once the motion it commands could not have run.
        self.motion_deadline = self.clock() + sum(durations) + 120
        self._start_job_command()

    def _validate_job_command(self, command, position=None):
        """Check one generated command and say how long its motion takes."""
        if command in ("G21", "G90", "M5", "S0"):
            return None, 0.0
        power = re.fullmatch(r"M4 S(\d+)", command)
        if power and 0 <= int(power[1]) <= self.settings.get(30, 0):
            return None, 0.0
        move = re.fullmatch(r"G53 G([01]) X(-?\d+(?:\.\d+)?) Y(-?\d+(?:\.\d+)?)(?: F(\d+))?", command)
        if not move:
            raise GuardError(f"Generated job contains an unsupported command: {command}")
        machine = (float(move[2]), float(move[3]))
        app = (machine[0] - self.origin[0], machine[1] - self.origin[1])
        if not self._inside(app):
            raise GuardError("Generated job contains a move outside the app bounds.")
        if move[1] == "1" and (not move[4] or not 60 <= int(move[4]) <= self.max_xy_feed):
            raise GuardError("Generated job feed exceeds the live machine limit.")
        feed = int(move[4]) if move[4] else self.max_xy_feed
        return machine, self._move_seconds(math.dist(position, machine), feed) if position else 0.0

    def _move_seconds(self, distance, feed):
        """An upper bound on one block: full speed, plus one accelerate and stop.

        GRBL carries speed across a corner, so a job of short segments takes
        less than this. A watchdog wants the pessimistic number: a job that
        finishes early is not a fault, and one cut short mid-burn is.
        """
        if not feed:
            return 0.0
        speed = feed / 60.0
        acceleration = min(self.settings.get(120, 0) or math.inf,
                           self.settings.get(121, 0) or math.inf)
        return distance / speed + (speed / acceleration if math.isfinite(acceleration) else 0.0)

    def _start_job_command(self):
        if self.job_paused:
            return
        if not self.job_commands:
            self.phase = "job-verify-modal"
            self._enqueue("$G", "job-modal")
            return
        command = self.job_commands.popleft()
        self.job_window.append(self.job_durations.popleft() if self.job_durations else 0.0)
        if command.startswith("M4 S"):
            self.job_expected_power = int(command.split("S", 1)[1])
        elif command in ("M5", "S0"):
            self.job_expected_power = 0
        self.phase = "job-command"
        # GRBL acknowledges a move when it is parsed into the planner, so on a
        # full buffer this acknowledgement waits for a block to finish. The
        # longest block it could be waiting on is one of the last few sent.
        self._enqueue(command, "job", 10 + max(self.job_window, default=0.0))
        self.message = f"Sending job · {self.job_done}/{self.job_total} commands · power {self.job_expected_power}."

    def pause_job(self):
        if not self.connected or not self.phase.startswith("job") or self.job_paused:
            raise GuardError("No running job is available to pause.")
        self._write(b"!")
        self.job_paused = True
        self.message = "Job paused. Resume or press STOP / RESET."

    def resume_job(self):
        if not self.connected or not self.phase.startswith("job") or not self.job_paused:
            raise GuardError("No paused job is available to resume.")
        self._write(b"~")
        self.job_paused = False
        # The held time is not evidence of a stuck machine: give the in-flight
        # command and the job as a whole a fresh window from the resume.
        now = self.clock()
        self.sent_at = now
        # What is left to send, plus what the planner is still holding.
        self.motion_deadline = now + sum(self.job_durations) + sum(self.job_window) + 120
        # An in-flight command still owes its acknowledgement, and that
        # acknowledgement is what sends the next one.
        if not self.pending and not self.queue:
            self._start_job_command()

    def _inside(self, xy):
        """Bounds check against the machine's reported travel, never a constant.

        The profile guard already requires $130=365 and $131=305, so this agrees
        with the old hardcoded limits on the observed machine. Reading them live
        means the two can never quietly disagree.
        """
        travel = self.travel
        if travel is None or xy is None:
            return False
        return 0 <= xy[0] <= travel[0] and 0 <= xy[1] <= travel[1]

    @staticmethod
    def _near(a, b):
        return a is not None and b is not None and all(abs(x - y) < 0.05 for x, y in zip(a, b))

    def tick(self):
        if not self.connected:
            return
        try:
            data = self.transport.read()
            self.buffer += data
            if len(self.buffer) > 65536:
                raise ValueError("USB report exceeded the input limit")
            while b"\n" in self.buffer and self.connected:
                line, self.buffer = self.buffer.split(b"\n", 1)
                self.receive(line.decode("ascii", errors="strict").strip())
            if not self.connected:
                return
            now = self.clock()
            if self.phase == "settling":
                if now < self.settle_until:
                    return
                self.phase = "initializing"
                # Explicitly disarm; verify via $G and FS, without changing firmware settings.
                for cmd in ("$I", "?", "$$", "$#", "$G"):
                    self._enqueue(cmd, "init-modal" if cmd == "$G" else cmd)
            # Every deadline below measures the machine. A modal dialog stops
            # this loop for as long as the operator reads it, and time nobody
            # spent watching is not evidence that the machine went silent.
            gap = now - self.last_tick if self.last_tick is not None else 0.0
            self.last_tick = now
            stalled = gap > 0.5  # Longer than a poll cycle: ticking really stopped.
            if stalled:
                self.sent_at += gap
                for name in ("motion_deadline", "settle_until"):
                    deadline = getattr(self, name)
                    if math.isfinite(deadline):
                        setattr(self, name, deadline + gap)
            # A held machine stops acknowledging the move it did not finish, so
            # an operator pause must not be read as a silent controller.
            # resume_job() restarts both clocks.
            if not self.job_paused:
                if self.pending and now - self.sent_at > self.pending.timeout:
                    self.fault(f"Timed out waiting for {self.pending.text}.")
                    return
                if now > self.motion_deadline:
                    self.fault("Motion completion could not be verified.")
                    return
            # status_at is deliberately not shifted: the position really is old,
            # so motion stays blocked until the next report arrives.
            if (not stalled and self.origin is not None
                    and self.phase in ("idle", "home-confirm") and now - self.status_at > 1.5):
                self.fault("Position updates stopped; home reference discarded.")
                return
            # V1.055 corrupts the next command when '?' overlaps a line command.
            # Serialize bare status queries too, completing them on '<...>'.
            # No empty G-code lines or extra acknowledgements are generated.
            if not self.pending and not self.queue and now - self.last_poll >= 0.4:
                self._enqueue("?", "status")
            if not self.pending and self.queue:
                self.pending = self.queue.popleft()
                self.sent_at = now
                if self.pending.text == "?":
                    self.last_poll = now
                wire = b"?" if self.pending.text == "?" else (self.pending.text + "\n").encode("ascii")
                self._write(wire)
        except Exception as exc:
            self.fault(f"Connection/report error: {exc}.")

    def receive(self, line):
        if not line:
            return
        self.log.append(("RX", line))
        self.rx_count += 1
        if self.phase == "settling":
            return  # Startup noise belongs to no transaction.
        if line.startswith("Grbl") or line.startswith("[MSG:Reset"):
            self.fault("Controller restarted; previous coordinates are no longer trusted.")
            return
        if line.startswith(("ALARM:", "error:")):
            context = f"awaiting {self.pending.text!r}" if self.pending else "no line command pending"
            explanation = " Standard GRBL meaning: expected a command letter." if line == "error:1" else ""
            self.fault(f"Controller reported {line} ({context}; phase {self.phase}).{explanation}")
            return
        if line == "ok":
            if self.pending is None or self.pending.text == "?":
                self.fault("Unexpected acknowledgement; command ordering is uncertain.")
                return
            tag = self.pending.tag
            self.pending = None
            if tag == "init-modal":
                # GRBL rejects ordinary G-code while homing-locked. In Alarm,
                # require a reported M5 S0 and physical disconnection before $H.
                if self.status and (self.status.state == "Alarm" or
                                    (self.laser_off and self.status.power == 0)):
                    self._finish_initialization()
                else:
                    self._enqueue("M5 S0")
                    self._enqueue("$G", "init-last")
            elif tag == "init-last":
                self._finish_initialization()
            elif tag == "home":
                self.phase = "home-barrier"
                self._enqueue("$G", "home-barrier")
            elif tag == "home-barrier":
                self.phase = "home-status"
                self._enqueue("?", "status")
            elif tag == "jog":
                self.phase = "jog-status"
                self._enqueue("?", "status")
            elif tag == "frame":
                self.phase = "frame-status"
                self._enqueue("?", "status")
            elif tag == "job":
                self.job_done += 1
                self._start_job_command()
            elif tag == "job-modal":
                self.phase = "job-status"
                self._enqueue("?", "status")
            return
        if line.startswith("<"):
            report = parse_status(line)
            if self.pending and self.pending.text == "?":
                self.pending = None
            self.status = report
            self.status_at = self.clock()
            if report.state == "Alarm" and self.origin is None and self.phase in ("initializing", "idle"):
                self.home_state = "Homing required (Alarm)"
            elif report.state.split(":")[0] in ("Alarm", "Door", "Sleep", "Check") or (report.state.startswith("Hold") and not self.job_paused):
                self.fault("Machine entered " + report.state + ".")
                return
            accessories = report.fields.get("A", "")
            expected_job_power = self.job_expected_power if self.phase.startswith("job") else 0
            unexpected_active = ((report.power is not None and report.power > expected_job_power)
                                 or ("S" in accessories and expected_job_power == 0) or "C" in accessories)
            if unexpected_active:
                self.stop()
                self.message = "Unexpected active laser report. Stop requested; check the machine."
                return
            if self.phase == "home-status" and report.state == "Idle" and report.machine is not None:
                self.phase = "home-confirm"
                self.home_state = "Awaiting confirmation"
                self.last_position = report.machine
                self.motion_deadline = math.inf
                self.message = "Home completed. Confirm the head is physically at bottom-left."
            elif self.phase == "home-confirm" and (report.state != "Idle" or not self._near(report.machine, self.last_position)):
                self.fault("Position changed before home confirmation.")
                return
            elif self.phase == "jog-status" and report.state == "Idle":
                if not self._near(report.machine, self.target):
                    self.fault("Jog endpoint differs from the requested target.")
                    return
                self.last_position = report.machine
                self.phase = "idle"
                self.target = None
                self.motion_deadline = math.inf
                self.message = "Step complete. Check the physical direction before another step."
            elif self.phase == "frame-status" and report.state == "Idle":
                if not self._near(report.machine, self.target):
                    self.fault("Frame endpoint differs from the expected corner.")
                    return
                self.last_position = report.machine
                self._start_frame_segment()
            elif self.phase == "job-status" and report.state == "Idle":
                if report.power != 0:
                    self.fault("Job ended without a zero-power report.")
                    return
                if self.job_final_target is not None and not self._near(report.machine, self.job_final_target):
                    self.fault("Job endpoint differs from the final generated position.")
                    return
                self.last_position = report.machine
                self.phase = "idle"
                self.motion_deadline = math.inf
                self.job_expected_power = 0
                self.message = "Job complete. Controller reports Idle with zero laser power."
            elif self.origin is not None and self.phase == "idle":
                if report.state != "Idle" or not self._near(report.machine, self.last_position):
                    self.fault("Unexpected movement or missing machine position.")
                    return
            if self.origin is not None and report.machine is not None and not self._inside(self.app_position):
                self.fault("Reported position is outside the app travel bounds.")
                return
            if self.connected and self.deferred_motion and self.pending is None:
                motion = self.deferred_motion
                self.deferred_motion = None
                try:
                    if motion[0] == "home":
                        self.home()
                    elif motion[0] == "frame":
                        self.frame(*motion[1:])
                    elif motion[0] == "job":
                        self.run_job(motion[1])
                    elif motion[0] == "jog-to":
                        self.jog_to(*motion[1:])
                    else:
                        self.jog(*motion[1:])
                except GuardError as exc:
                    self.message = f"Queued motion cancelled: {exc}"
            return
        setting = parse_setting(line)
        if setting:
            self.settings[setting[0]] = setting[1]
            if self.ready and not self.profile_ok:
                self.fault("Machine settings changed from the verified profile.")
            return
        if line.startswith("[VER:") and line.endswith("]"):
            self.firmware = line[5:-1].rstrip(":")
        elif line.startswith("[GC:") and line.endswith("]"):
            self.modal = line[4:-1]
            words = self.modal.split()
            spindle = [w for w in words if w.startswith("S")]
            self.laser_off = "M5" in words and bool(spindle) and float(spindle[-1][1:]) == 0 and not any(w in words for w in ("M3", "M4"))
            if not self.laser_off and self.phase != "initializing":
                self.stop()
                self.message = "Parser did not confirm M5 S0. Check the machine before reconnecting."
        elif line.startswith("[") and line.endswith("]") and ":" in line:
            key, value = line[1:-1].split(":", 1)
            self.parameters[key] = value

    def _finish_initialization(self):
        self.ready = self.profile_ok and self.laser_off and self.firmware != "Not read"
        self.phase = "idle"
        self.message = ("Connected. Diagnostics verified. Home the machine before motion."
                        if self.ready else "Diagnostics incomplete or profile mismatch. Motion locked; inspect the log.")
