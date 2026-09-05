"""Native Windows control panel. Widgets only invoke guarded controller operations."""
import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from .controller import Controller, GuardError, JOG_FEEDS
from . import __version__
from .diagnostics import Reporter
from .protocol import READ_COMMANDS
from .transports import SerialTransport, Simulator, list_ports
from .geometry import Document, Shape, BED_X, BED_Y, shape_bounds, shape_paths
from .materials import MaterialLibrary, MaterialPreset
from .viewport import (
    anchor_point, arrow_target, clamp_to_bed, clamp_zoom, corner_handles, fit_viewport,
    handle_at as handle_hit, inside_bed, snap_value, topmost_at,
    zoom_pan_correction,
)


LOG_FILTERS = ("Warnings and errors", "Errors only", "Information", "USB traffic")


def log_level(kind, message):
    upper = message.upper()
    if kind == "FAULT" or upper.startswith(("ERROR:", "ALARM:")):
        return "error"
    if kind == "REPORT" or any(word in upper for word in ("WARNING", "TIMED OUT", "UNEXPECTED", "MISMATCH")):
        return "warning"
    if kind == "RX" and (message.startswith("[VER:") or message.startswith("[OPT:") or message.startswith("[MSG:")):
        return "info"
    return "traffic"


def filter_log(entries, selected):
    allowed = {
        "Errors only": {"error"},
        "Warnings and errors": {"error", "warning"},
        "Information": {"error", "warning", "info"},
        "USB traffic": {"error", "warning", "info", "traffic"},
    }[selected]
    return tuple((kind, message) for kind, message in entries if log_level(kind, message) in allowed)


class App:
    def __init__(self, root, demo=False, port=None, report=None):
        self.root = root
        self.controller = Controller()
        self.reporter = Reporter(report)
        self.active_port = None
        self.root.title(f"Atomstack — Personal controller {__version__}")
        self.root.geometry("1440x980")
        self.root.minsize(1100, 820)
        self.root.configure(bg="#f5f7fb")
        self.demo = False
        self.log_cache = None
        self.click_target = None
        self.pending_key_target = None
        self.view_zoom = 1.0
        self.machine_object = tk.StringVar()
        self.machine_object_signature = None
        self.port_labels = {}
        self.port = tk.StringVar()
        self.status_text = tk.StringVar(value="Disconnected")
        self.message = tk.StringVar()
        self.firmware = tk.StringVar(value="Not read")
        self.profile = tk.StringVar(value=f"Expected {BED_X:g} × {BED_Y:g} mm · S-max 1000")
        self.homed = tk.StringVar(value="Not homed")
        self.app_xy = tk.StringVar(value="X  —        Y  —")
        self.machine_xy = tk.StringVar(value="Machine X —   Y —")
        self.origin_xy = tk.StringVar(value="Home reference not captured")
        self.mode = tk.StringVar(value="USB · 115200 baud")
        self.jog_distance = tk.StringVar(value="1 mm")
        self.jog_feed = tk.StringVar(value="Normal · 3000")
        self.log_filter = tk.StringVar(value="Warnings and errors")
        self.technical = tk.BooleanVar(value=False)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#f5f7fb", foreground="#172033")
        style.configure("TButton", padding=(12, 8))
        style.map("TButton", background=[("active", "#dce7eb"), ("disabled", "#e8ecee")], foreground=[("disabled", "#667680")])
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Position.TLabel", font=("Consolas", 22, "bold"))
        style.configure("Quiet.TLabel", foreground="#53627a")
        style.configure("Stop.TButton", background="#dc2626", foreground="white", font=("Segoe UI", 11, "bold"))
        style.map("Stop.TButton", background=[("active", "#b91c1c")])
        style.configure("Accent.TButton", background="#155eef", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#0f4fcf"), ("disabled", "#d5dfe2")])
        style.configure("TNotebook", borderwidth=0, tabmargins=(0, 8, 0, 0))
        style.configure("TNotebook.Tab", padding=(18, 9), font=("Segoe UI", 10, "bold"))
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill="both", expand=True)
        # Reserve the recovery message before expandable areas consume height.
        ttk.Label(outer, textvariable=self.message, wraplength=940).pack(side="bottom", anchor="w", fill="x", pady=(10, 0))
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Atomstack", style="Title.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.mode, style="Quiet.TLabel").pack(side="left", padx=20)
        ttk.Button(header, text="STOP / RESET", style="Stop.TButton", command=self.stop).pack(side="right", padx=(14, 0))
        ttk.Label(header, textvariable=self.status_text, style="Section.TLabel").pack(side="right")
        connect = ttk.Frame(outer)
        connect.pack(fill="x", pady=(18, 16))
        self.ports = ttk.Combobox(connect, textvariable=self.port, state="readonly", width=40)
        self.ports.pack(side="left")
        self.refresh_btn = ttk.Button(connect, text="Refresh ports", command=self.refresh)
        self.refresh_btn.pack(side="left", padx=6)
        self.connect_btn = ttk.Button(connect, text="Connect USB", command=self.connect)
        self.connect_btn.pack(side="left")
        self.demo_btn = ttk.Button(connect, text="Open simulator", command=self.simulate)
        self.demo_btn.pack(side="right")
        ttk.Separator(outer).pack(fill="x")
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True, pady=(8, 0))
        self.design_tab = ttk.Frame(self.notebook)
        self.machine_tab = ttk.Frame(self.notebook, padding=(12, 8))
        self.notebook.add(self.design_tab, text="Design & Send")
        self.notebook.add(self.machine_tab, text="Machine & Jog")
        body = ttk.Frame(self.machine_tab)
        body.pack(fill="both", expand=True, pady=(8, 12))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, padding=(0, 0, 24, 0))
        left.grid(row=0, column=0, sticky="nsew")
        ttk.Label(left, text="Position · millimeters", style="Section.TLabel").pack(anchor="w")
        ttk.Label(left, textvariable=self.app_xy, style="Position.TLabel").pack(anchor="w", pady=(8, 3))
        ttk.Label(left, textvariable=self.machine_xy, style="Quiet.TLabel").pack(anchor="w")
        ttk.Label(left, textvariable=self.origin_xy, style="Quiet.TLabel").pack(anchor="w", pady=(3, 12))
        self.canvas = tk.Canvas(left, background="#ffffff", width=1, height=180, highlightthickness=1, highlightbackground="#c4ced3")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw_bed())
        self.canvas.bind("<Button-1>", self.click_bed)
        self.canvas.bind("<MouseWheel>", self.zoom_bed)
        ttk.Label(left, text=("Click to jog · mouse wheel to zoom · arrow keys use selected step\n"
                              f"App envelope: {BED_X:g} × {BED_Y:g} mm · design overlay is read-only"),
                  style="Quiet.TLabel").pack(anchor="w", pady=(6, 0))
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        ttk.Label(right, text="Motion checks", style="Section.TLabel").pack(anchor="w")
        ttk.Label(right, textvariable=self.homed).pack(anchor="w", pady=(8, 6))
        ttk.Label(right, text="Laser is kept off for Home, Jog, and Frame (M5/S0).",
                  style="Quiet.TLabel").pack(anchor="w", pady=(0, 8))
        home_row = ttk.Frame(right)
        home_row.pack(fill="x")
        self.home_btn = ttk.Button(home_row, text="Home machine", command=self.home)
        self.home_btn.pack(side="left")
        self.confirm_btn = ttk.Button(home_row, text="Confirm bottom-left", command=lambda: self.act(self.controller.confirm_home))
        self.confirm_btn.pack(side="left", padx=6)
        ttk.Label(right, text="Jog controls", style="Section.TLabel").pack(anchor="w", pady=(18, 8))
        presets = ttk.Frame(right)
        presets.pack(fill="x", pady=(0, 8))
        ttk.Label(presets, text="Move").pack(side="left")
        self.step_box = ttk.Combobox(presets, textvariable=self.jog_distance, values=("0.1 mm", "1 mm", "5 mm", "10 mm"), state="readonly", width=7)
        self.step_box.pack(side="left", padx=(5, 14))
        ttk.Label(presets, text="Speed").pack(side="left")
        self.feed_box = ttk.Combobox(presets, textvariable=self.jog_feed,
                                     values=("Slow · 300", "Normal · 3000", "Fast · 6000", "Very fast · 12000", "Maximum · 20000"),
                                     state="readonly", width=16)
        self.feed_box.pack(side="left", padx=5)
        ttk.Label(presets, text="mm/min", style="Quiet.TLabel").pack(side="left")
        jog = ttk.Frame(right)
        jog.pack(anchor="w")
        self.jogs = []
        for label, axis, direction, row, col in [("Back  Y+", "Y", 1, 0, 1), ("Left  X−", "X", -1, 1, 0),
                                                ("Right  X+", "X", 1, 1, 2), ("Front  Y−", "Y", -1, 2, 1)]:
            button = ttk.Button(jog, text=label, width=10, command=lambda a=axis, d=direction: self.act(lambda: self.controller.jog(a, d, self.selected_distance(), self.selected_feed())))
            button.grid(row=row, column=col, padx=3, pady=3)
            self.jogs.append((button, axis, direction))
        target_header = ttk.Frame(right)
        target_header.pack(fill="x", pady=(14, 5))
        ttk.Label(target_header, text="Design targets", style="Section.TLabel").pack(side="left")
        ttk.Button(target_header, text="Reset zoom", command=self.reset_zoom).pack(side="right")
        self.machine_object_box = ttk.Combobox(right, textvariable=self.machine_object, state="readonly", width=42)
        self.machine_object_box.pack(fill="x", pady=(0, 5))
        self.machine_object_box.bind("<<ComboboxSelected>>", lambda event: self.draw_bed())
        anchors = ttk.Frame(right)
        anchors.pack(fill="x")
        self.object_target_buttons = []
        for text, anchor in (("BL", "BL"), ("BR", "BR"), ("Center", "C"), ("TL", "TL"), ("TR", "TR")):
            button = ttk.Button(anchors, text=text, width=6, command=lambda a=anchor: self.jog_object(a))
            button.pack(side="left", padx=(0, 3))
            self.object_target_buttons.append(button)
        ttk.Button(right, text="STOP / RESET   ·   Esc", style="Stop.TButton", command=self.stop).pack(fill="x", pady=(14, 5))
        ttk.Label(right, text="Stops motion and clears the home reference.\nUse the hardware switch if USB cannot respond.", style="Quiet.TLabel").pack(anchor="w")
        ttk.Separator(self.machine_tab).pack(fill="x")
        activity = ttk.Frame(self.machine_tab)
        activity.pack(fill="x", pady=(12, 6))
        ttk.Label(activity, text="Activity log", style="Section.TLabel").pack(side="left")
        self.log_filter_box = ttk.Combobox(activity, textvariable=self.log_filter, values=LOG_FILTERS, state="readonly", width=20)
        self.log_filter_box.pack(side="left", padx=10)
        self.log_filter_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_log())
        ttk.Checkbutton(activity, text="Technical details", variable=self.technical, command=self.toggle_details).pack(side="right")
        self.technical_frame = ttk.Frame(self.machine_tab)
        details = ttk.Frame(self.technical_frame)
        details.pack(fill="x", pady=(0, 6))
        ttk.Label(details, textvariable=self.firmware, style="Section.TLabel").pack(side="left")
        ttk.Label(details, textvariable=self.profile, style="Quiet.TLabel").pack(side="right")
        queries = ttk.Frame(self.technical_frame)
        queries.pack(fill="x", pady=(0, 6))
        ttk.Label(queries, text="Read diagnostics").pack(side="left", padx=(0, 8))
        self.query_buttons = []
        for command in READ_COMMANDS:
            button = ttk.Button(queries, text=command, width=4, command=lambda c=command: self.act(lambda: self.controller.query(c)))
            button.pack(side="left", padx=2)
            self.query_buttons.append(button)
        ttk.Button(queries, text="Save session log", command=self.save_log).pack(side="right")
        log_frame = ttk.Frame(self.machine_tab)
        log_frame.pack(fill="x")
        self.log = tk.Text(log_frame, height=6, font=("Consolas", 10), background="#ffffff", foreground="#243742", relief="flat", wrap="none", state="disabled")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="x", expand=True)
        self.geometry = GeometryWindow(self.design_tab, self.controller, embedded=True)
        self.root.bind("<Escape>", lambda e: self.stop())
        for key in ("Left", "Right", "Up", "Down"):
            self.root.bind(f"<{key}>", lambda event, direction=key: self.handle_arrow(direction, event))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        if demo:
            self.simulate()
        elif port:
            label = next((label for label, device in self.port_labels.items() if device == port), None)
            if label:
                self.port.set(label)
                self.connect()
            else:
                self.controller.message = f"Requested port {port} was not detected. No connection opened."
        self.pump()

    def act(self, action):
        try:
            action()
        except (GuardError, OSError, ValueError) as exc:
            self.controller.message = str(exc)

    def selected_distance(self):
        return float(self.jog_distance.get().split()[0])

    def selected_feed(self):
        return int(self.jog_feed.get().rsplit("·", 1)[-1].strip())

    def toggle_details(self):
        if self.technical.get():
            self.technical_frame.pack(fill="x", before=self.log.master, pady=(0, 4))
        else:
            self.technical_frame.pack_forget()

    def refresh_log(self):
        self.log_cache = None

    def refresh(self):
        try:
            ports = list_ports()
            self.port_labels = {f"{p.device}  —  {p.description}": p.device for p in ports}
            self.ports["values"] = list(self.port_labels)
            usb = [label for label, p in zip(self.port_labels, ports) if p.vid is not None]
            if self.port.get() not in self.port_labels:
                self.port.set(usb[0] if len(usb) == 1 else next(iter(self.port_labels)) if len(ports) == 1 else "")
            if not ports:
                self.controller.message = "No COM ports detected. Connect USB and refresh, or use the simulator."
        except Exception as exc:
            self.controller.message = "Port scan failed: " + str(exc)

    def connect(self):
        if self.controller.connected:
            self.controller.disconnect()
            return
        port = self.port_labels.get(self.port.get())
        if not port:
            self.controller.message = "Select a COM port first. Port detection does not identify the machine."
            return
        try:
            self.controller.attach(SerialTransport(port))
            self.active_port = port
            self.demo = False
            self.mode.set(f"{port} · USB · 115200 baud")
        except Exception as exc:
            self.controller.message = f"Cannot open {port}: {exc}. Close other serial applications and retry."

    def simulate(self):
        if self.controller.connected:
            return
        self.controller.attach(Simulator(), settle=0)
        self.active_port = "simulator"
        self.demo = True
        self.mode.set("SIMULATOR · no hardware connected")

    def home(self):
        if messagebox.askokcancel("Home machine", "This moves both axes using the firmware's existing homing speeds.\n\nThe controller reports the laser off. Make sure the travel path is clear.\n\nAfter homing, check that the head is physically at bottom-left."):
            self.act(self.controller.home)

    def open_geometry(self):
        self.notebook.select(self.design_tab)

    def stop(self):
        self.controller.stop()

    def close(self):
        self.controller.disconnect()
        self.write_report(force=True)
        self.root.destroy()

    def write_report(self, force=False):
        try:
            self.reporter.write(self.controller, self.active_port, force=force)
        except OSError as exc:
            self.controller.log.append(("REPORT", f"Could not write diagnostic report: {exc}"))
            self.reporter.path = None  # Logging failure must not disrupt the serial event loop.

    def save_log(self):
        filename = filedialog.asksaveasfilename(title="Save session log", defaultextension=".txt", initialfile="atomstack-session.txt", filetypes=[("Text log", "*.txt")])
        if filename:
            try:
                Path(filename).write_text("\n".join(f"{a}  {b}" for a, b in self.controller.log), encoding="utf-8")
                self.controller.message = "Session log saved. It may contain controller network details."
            except OSError as exc:
                self.controller.message = "Could not save log: " + str(exc)

    @staticmethod
    def enabled(widget, enabled):
        widget.state(["!disabled"] if enabled else ["disabled"])

    def pump(self):
        c = self.controller
        c.tick()
        self.message.set(c.message)
        self.status_text.set(f"Connected · {c.status.state}" if c.connected and c.ready and c.status else "Connecting…" if c.connected else "Disconnected")
        self.firmware.set(c.firmware)
        # Show the machine's own numbers once verified, never the app's constants.
        travel = c.travel
        if c.profile_ok and travel:
            self.profile.set(f"Live profile verified · {travel[0]:g} × {travel[1]:g} mm"
                             f" · S{c.settings.get(30, 0):g}")
        else:
            self.profile.set(f"Expected {BED_X:g} × {BED_Y:g} mm · S-max 1000")
        self.homed.set("Home: " + c.home_state)
        xy = c.app_position
        self.app_xy.set(f"X {xy[0]:7.3f}    Y {xy[1]:7.3f}" if xy else "X  —        Y  —")
        machine = c.status.machine if c.status else None
        self.machine_xy.set(f"Machine X {machine[0]:.3f}   Y {machine[1]:.3f}" if machine else "Machine X —   Y —")
        self.origin_xy.set(f"Home reference: X {c.origin[0]:.3f}   Y {c.origin[1]:.3f}" if c.origin else "Home reference not captured")
        self.connect_btn.configure(text="Disconnect" if c.connected else "Connect USB")
        self.enabled(self.demo_btn, not c.connected)
        self.enabled(self.refresh_btn, not c.connected)
        self.ports.configure(state="disabled" if c.connected else "readonly")
        try:
            c._guard(allow_status_poll=True)
            home_ready = True
        except GuardError:
            home_ready = False
        self.enabled(self.home_btn, home_ready)
        self.enabled(self.confirm_btn, c.phase == "home-confirm")
        for button, axis, direction in self.jogs:
            target = list(xy) if xy else None
            if target:
                target[0 if axis == "X" else 1] += direction * self.selected_distance()
            self.enabled(button, home_ready and c.home_state == "Confirmed" and c._inside(target))
        for button in self.query_buttons:
            stable_poll = c.pending is None or c._status_poll_pending()
            self.enabled(button, c.connected and c.ready and c.phase == "idle" and stable_poll and not c.queue)
        lines = filter_log(c.log, self.log_filter.get())
        if lines != self.log_cache:
            at_end = self.log_cache is None or self.log.yview()[1] >= 0.98
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            content = "\n".join(f"{a.upper()}  {b}" for a, b in lines)
            if not content:
                content = "No warnings or errors. Choose another log level to see more activity."
            self.log.insert("end", content)
            if at_end:
                self.log.see("end")
            self.log.configure(state="disabled")
            self.log_cache = lines
        self.draw_bed()
        self.refresh_machine_objects()
        self.dispatch_key_target()
        self.write_report()
        self.root.after(10 if c.phase.startswith("job") and not c.job_paused else 50, self.pump)

    def draw_bed(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        view = self.machine_bed_transform()
        x0, y0, scale = view
        x1, y1 = view.to_canvas(BED_X, BED_Y)
        for x in range(0, int(BED_X) + 1, 50):
            px = view.to_canvas(x, 0)[0]
            canvas.create_line(px, y0, px, y1, fill="#e1e7ea")
        for y in range(0, int(BED_Y) + 1, 50):
            py = view.to_canvas(0, y)[1]
            canvas.create_line(x0, py, x1, py, fill="#e1e7ea")
        canvas.create_rectangle(x0, y1, x1, y0, outline="#718793")
        canvas.create_text(x0, y0 + 14, text="0, 0", anchor="w", fill="#405864", font=("Segoe UI", 9))
        canvas.create_text(x1, y1 - 10, text=f"{BED_X:g}, {BED_Y:g}", anchor="e", fill="#405864", font=("Segoe UI", 9))
        workspace = getattr(self, "geometry", None)
        selected = self.selected_machine_object_index()
        if workspace:
            for index, shape in enumerate(workspace.document.shapes):
                color, line_width = ("#65767e", 2) if index == selected else ("#aeb8bd", 1)
                for path in shape_paths(shape):
                    coords = [coordinate for point in path for coordinate in view.to_canvas(*point)]
                    if len(coords) >= 4:
                        canvas.create_line(*coords, fill=color, width=line_width)
        xy = self.controller.app_position
        if xy:
            x, y = view.to_canvas(*xy)
            canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#155eef", outline="white", width=2)
            canvas.create_line(x - 10, y, x + 10, y, fill="#155eef")
            canvas.create_line(x, y - 10, x, y + 10, fill="#155eef")
            if self.click_target and not self.controller._near(xy, self.click_target):
                tx, ty = view.to_canvas(*self.click_target)
                canvas.create_oval(tx - 7, ty - 7, tx + 7, ty + 7, outline="#d66a1f", width=2, dash=(3, 2))
                canvas.create_text(tx, ty - 12, text=f"{self.click_target[0]:.1f}, {self.click_target[1]:.1f}",
                                   anchor="s", fill="#b34f12", font=("Segoe UI", 9))
        else:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="Position appears after\na confirmed home", fill="#53627a", font=("Segoe UI", 12), justify="center")

    def click_bed(self, event):
        x, y = self.machine_bed_transform().to_bed(event.x, event.y)
        if not inside_bed(x, y):
            self.controller.message = "Click inside the outlined machine bed."
            return
        target = (round(x, 1), round(y, 1))
        try:
            self.controller.jog_to(*target, self.selected_feed())
            self.click_target = target
        except GuardError as exc:
            self.controller.message = str(exc)

    def machine_bed_transform(self):
        return fit_viewport(self.canvas.winfo_width(), self.canvas.winfo_height(),
                            80, 50, self.view_zoom)

    def zoom_bed(self, event):
        self.view_zoom = clamp_zoom(self.view_zoom, 1.2 if event.delta > 0 else 1/1.2, 1.0, 5.0)
        self.draw_bed()
        return "break"

    def reset_zoom(self):
        self.view_zoom = 1.0
        self.draw_bed()

    def arrow_jog(self, direction, event=None):
        if event is not None and event.widget.winfo_class() in ("TEntry", "Entry", "TCombobox", "Text"):
            return
        c = self.controller
        if c.home_state != "Confirmed" or c.app_position is None:
            c.message = "Home and confirm bottom-left before using arrow keys."
            return "break"
        if self.pending_key_target is not None:
            base = self.pending_key_target
        elif c.target is not None and c.origin is not None and c.phase.startswith("jog"):
            base = (c.target[0] - c.origin[0], c.target[1] - c.origin[1])
        else:
            base = c.app_position
        target = arrow_target(base, direction, self.selected_distance())
        self.pending_key_target = target
        self.click_target = target
        c.message = f"Arrow target buffered: X {target[0]:.1f}, Y {target[1]:.1f}."
        self.draw_bed()
        return "break"

    def handle_arrow(self, direction, event):
        if self.notebook.select() == str(self.design_tab):
            return self.geometry.nudge(direction, event)
        return self.arrow_jog(direction, event)

    def dispatch_key_target(self):
        c = self.controller
        if self.pending_key_target is None or c.phase != "idle" or c.deferred_motion:
            return
        target = self.pending_key_target
        self.pending_key_target = None
        try:
            c.jog_to(*target, self.selected_feed())
        except GuardError as exc:
            c.message = str(exc)

    def refresh_machine_objects(self):
        workspace = getattr(self, "geometry", None)
        if not workspace:
            return
        signature = tuple(shape.label for shape in workspace.document.shapes)
        if signature == self.machine_object_signature:
            return
        self.machine_object_signature = signature
        values = tuple(f"{index + 1}. {label}" for index, label in enumerate(signature))
        self.machine_object_box.configure(values=values)
        if self.machine_object.get() not in values:
            self.machine_object.set(values[0] if values else "")
        for button in self.object_target_buttons:
            self.enabled(button, bool(values))
        self.draw_bed()

    def selected_machine_object_index(self):
        value = self.machine_object.get()
        try:
            return int(value.split(".", 1)[0]) - 1
        except (ValueError, IndexError):
            return None

    def jog_object(self, anchor):
        index = self.selected_machine_object_index()
        workspace = getattr(self, "geometry", None)
        if workspace is None or index is None or not 0 <= index < len(workspace.document.shapes):
            self.controller.message = "Choose a design object first."
            return
        shape = workspace.document.shapes[index]
        target = anchor_point(shape_bounds(shape), anchor)
        try:
            self.controller.jog_to(*target, self.selected_feed())
            self.click_target = target
        except GuardError as exc:
            self.controller.message = str(exc)


class GeometryWindow:
    """Geometry editor; only the guarded frame action can request head motion."""
    def __init__(self, parent, controller, embedded=False):
        if embedded:
            self.window = ttk.Frame(parent)
            self.window.pack(fill="both", expand=True)
        else:
            self.window = tk.Toplevel(parent)
            self.window.title("Geometry workspace · frame and send")
            self.window.geometry("1100x900")
            self.window.minsize(1000, 820)
        self.document = Document()
        self.controller = controller
        self.materials = MaterialLibrary()
        self.selected = None
        self.selection = set()
        self.tool = tk.StringVar(value="select")
        self.message = tk.StringVar(value="Select, move, and resize objects. Frame checks placement with the laser off.")
        self.drag_start = None
        self.preview_item = None
        self.interaction = None
        self.undo_stack = []
        self.redo_stack = []
        self.view_zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom_text = tk.StringVar(value="100%")
        self.snap_enabled = tk.BooleanVar(value=True)
        self.grid_size = tk.StringVar(value="1 mm")
        self.design_path = None
        self.fields = {name: tk.StringVar(value=value) for name, value in {
            "x": "10", "y": "10", "width": "40", "height": "30",
            "speed": "1000", "power": "300", "passes": "1", "text": "ATOMSTACK", "rotation": "0"}.items()}
        self.mirror_x = tk.BooleanVar(value=False)
        self.mirror_y = tk.BooleanVar(value=False)
        self.font_family = tk.StringVar(value="Arial")
        self.frame_speed = tk.StringVar(value="6000")
        self.material_name = tk.StringVar()
        outer = ttk.Frame(self.window, padding=12 if embedded else 20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Design workspace", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"{BED_X:g} × {BED_Y:g} mm", style="Quiet.TLabel").pack(side="right")
        ttk.Button(header, text="Save design", command=self.save_design).pack(side="right", padx=(4, 12))
        ttk.Button(header, text="Open", command=self.open_design).pack(side="right", padx=4)
        ttk.Button(header, text="New", command=self.new_design).pack(side="right", padx=4)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(12, 8))
        for text, value in (("Select / move", "select"), ("Rectangle", "rectangle"), ("Ellipse", "circle"), ("Line", "line"), ("Text", "text")):
            ttk.Radiobutton(toolbar, text=text, variable=self.tool, value=value).pack(side="left", padx=(0, 10))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5)
        self.undo_button = ttk.Button(toolbar, text="Undo", command=self.undo)
        self.undo_button.pack(side="left", padx=(5, 3))
        self.redo_button = ttk.Button(toolbar, text="Redo", command=self.redo)
        self.redo_button.pack(side="left", padx=3)
        ttk.Button(toolbar, text="Duplicate", command=self.duplicate).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Delete", command=self.delete).pack(side="left", padx=3)
        transform_menu = tk.Menu(self.window, tearoff=False)
        transform_menu.add_command(label="Rotate left 90°", command=lambda: self.transform_selection("rotate_left"))
        transform_menu.add_command(label="Rotate right 90°", command=lambda: self.transform_selection("rotate_right"))
        transform_menu.add_separator()
        transform_menu.add_command(label="Flip horizontally", command=lambda: self.transform_selection("flip_horizontal"))
        transform_menu.add_command(label="Flip vertically", command=lambda: self.transform_selection("flip_vertical"))
        ttk.Menubutton(toolbar, text="Transform", menu=transform_menu).pack(side="left", padx=3)

        view_bar = ttk.Frame(outer)
        view_bar.pack(fill="x", pady=(0, 8))
        ttk.Button(view_bar, text="Zoom in", command=lambda: self.zoom_by(1.25)).pack(side="left")
        ttk.Button(view_bar, text="Zoom out", command=lambda: self.zoom_by(0.8)).pack(side="left", padx=4)
        ttk.Button(view_bar, text="Fit bed", command=self.fit_view).pack(side="left")
        ttk.Label(view_bar, textvariable=self.zoom_text, width=6, anchor="center").pack(side="left", padx=(4, 12))
        ttk.Checkbutton(view_bar, text="Snap to grid", variable=self.snap_enabled).pack(side="left")
        ttk.Combobox(view_bar, textvariable=self.grid_size, values=("0.1 mm", "0.5 mm", "1 mm", "5 mm", "10 mm"), state="readonly", width=7).pack(side="left", padx=5)
        align_menu = tk.Menu(self.window, tearoff=False)
        for text, action in (("Left edge", "left"), ("Horizontal center", "center_x"), ("Right edge", "right"),
                             ("Bottom edge", "bottom"), ("Vertical center", "center_y"), ("Top edge", "top")):
            align_menu.add_command(label=text, command=lambda a=action: self.align_to_bed(a))
        align_menu.add_separator()
        align_menu.add_command(label="Distribute horizontally", command=lambda: self.distribute("horizontal"))
        align_menu.add_command(label="Distribute vertically", command=lambda: self.distribute("vertical"))
        align_button = ttk.Menubutton(view_bar, text="Align to bed", menu=align_menu)
        align_button.pack(side="left", padx=(6, 3))
        layer_menu = tk.Menu(self.window, tearoff=False)
        layer_menu.add_command(label="Bring forward", command=lambda: self.reorder(1))
        layer_menu.add_command(label="Send backward", command=lambda: self.reorder(-1))
        layer_button = ttk.Menubutton(view_bar, text="Layer", menu=layer_menu)
        layer_button.pack(side="left", padx=3)
        ttk.Label(view_bar, text="Wheel: zoom  ·  middle-drag: pan  ·  arrows: nudge", style="Quiet.TLabel").pack(side="left", padx=8)
        action_bar = ttk.Frame(outer)
        action_bar.pack(fill="x", pady=(0, 10))
        ttk.Button(action_bar, text="Create burn-test grid…", command=self.open_burn_test).pack(side="left")
        self.send_button = ttk.Button(action_bar, text="Send job to machine", style="Accent.TButton", command=self.send_job)
        self.send_button.pack(side="right")
        self.pause_button = ttk.Button(action_bar, text="Pause", command=lambda: self.act(self.controller.pause_job))
        self.pause_button.pack(side="right", padx=(8, 0))
        self.resume_button = ttk.Button(action_bar, text="Resume", command=lambda: self.act(self.controller.resume_job))
        self.resume_button.pack(side="right", padx=(8, 0))
        self.frame_button = ttk.Button(action_bar, text="Frame outline · laser off", command=self.frame)
        self.frame_button.pack(side="right", padx=8)
        self.preview_button = ttk.Button(action_bar, text="Preview job", command=self.open_preview)
        self.preview_button.pack(side="right", padx=(0, 4))
        ttk.Label(action_bar, text="Frame speed").pack(side="right", padx=(8, 3))
        ttk.Combobox(action_bar, textvariable=self.frame_speed, values=tuple(map(str, JOG_FEEDS)), state="readonly", width=7).pack(side="right")
        library_bar = ttk.Frame(outer)
        library_bar.pack(fill="x", pady=(0, 12))
        ttk.Label(library_bar, text="Material library").pack(side="left", padx=(0, 5))
        self.material_combo = ttk.Combobox(library_bar, textvariable=self.material_name, state="readonly", width=24)
        self.material_combo.pack(side="left")
        self.material_apply = ttk.Button(library_bar, text="Apply", command=self.apply_material)
        self.material_apply.pack(side="left", padx=5)
        ttk.Button(library_bar, text="Save current…", command=self.save_material).pack(side="left")
        self.material_delete = ttk.Button(library_bar, text="Delete", command=self.delete_material)
        self.material_delete.pack(side="left", padx=5)
        self.refresh_materials()
        content = ttk.Frame(outer)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=0)
        content.rowconfigure(0, weight=1)
        self.bed = tk.Canvas(content, background="#eef3f6", highlightthickness=1, highlightbackground="#aebdc4")
        self.bed.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        self.bed.bind("<Configure>", lambda e: self.draw())
        self.bed.bind("<ButtonPress-1>", self.press)
        self.bed.bind("<B1-Motion>", self.drag)
        self.bed.bind("<ButtonRelease-1>", self.release)
        self.bed.bind("<MouseWheel>", self.mousewheel)
        self.bed.bind("<ButtonPress-2>", self.pan_press)
        self.bed.bind("<B2-Motion>", self.pan_drag)
        self.bed.bind("<ButtonRelease-2>", self.pan_release)
        side = ttk.Frame(content, width=270)
        side.grid(row=0, column=1, sticky="ns")
        ttk.Label(side, text="Shapes", style="Section.TLabel").pack(anchor="w")
        list_frame = ttk.Frame(side)
        list_frame.pack(fill="x", pady=(6, 8))
        self.listbox = tk.Listbox(list_frame, width=38, height=4, exportselection=False,
                                  selectmode="extended", font=("Segoe UI", 9))
        self.listbox.pack(side="left", fill="x", expand=True)
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        list_scroll.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=list_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self.select)
        self.selection_status = tk.StringVar(value="No selection")
        ttk.Label(side, textvariable=self.selection_status, style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        labels = {"text": "Text", "x": "Left edge X · mm", "y": "Bottom edge Y · mm", "width": "Width · mm", "height": "Height · mm", "rotation": "Rotation · degrees",
                  "speed": "Cut speed · mm/min", "power": "Power · 0–1000", "passes": "Number of passes"}
        self.field_entries = {}
        for name, label in labels.items():
            row = ttk.Frame(side)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=17).pack(side="left")
            entry = ttk.Entry(row, textvariable=self.fields[name], width=12)
            entry.pack(side="right")
            self.field_entries[name] = entry
        font_row = ttk.Frame(side)
        font_row.pack(fill="x", pady=1)
        ttk.Label(font_row, text="Text font", width=17).pack(side="left")
        self.font_combo = ttk.Combobox(font_row, textvariable=self.font_family, values=("Arial", "Segoe UI", "Consolas"), state="readonly", width=12)
        self.font_combo.pack(side="right")
        mirror_row = ttk.Frame(side)
        mirror_row.pack(fill="x", pady=(2, 0))
        self.mirror_x_button = ttk.Checkbutton(mirror_row, text="Flip H", variable=self.mirror_x)
        self.mirror_x_button.pack(side="left")
        self.mirror_y_button = ttk.Checkbutton(mirror_row, text="Flip V", variable=self.mirror_y)
        self.mirror_y_button.pack(side="left", padx=8)
        value_actions = ttk.Frame(side)
        value_actions.pack(fill="x", pady=(6, 4))
        self.add_value_button = ttk.Button(value_actions, text="Add new", command=self.add_from_values)
        self.add_value_button.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.apply_value_button = ttk.Button(value_actions, text="Apply values", command=self.apply)
        self.apply_value_button.pack(side="left", fill="x", expand=True, padx=(2, 0))
        ttk.Label(side, text=("Frame keeps the laser off.\n"
                              f"X 0–{BED_X:g} · Y 0–{BED_Y:g} mm · S 0–1000"),
                  style="Quiet.TLabel", justify="left").pack(anchor="w")
        self.frame_status = tk.StringVar(value="Add geometry to enable Frame.")
        ttk.Label(outer, textvariable=self.frame_status, style="Quiet.TLabel", wraplength=980).pack(fill="x", pady=(10, 0))
        ttk.Label(outer, textvariable=self.message, wraplength=980).pack(fill="x", pady=(12, 0))
        self.window.bind_all("<Control-z>", self.undo_shortcut)
        self.window.bind_all("<Control-y>", self.redo_shortcut)
        self.window.bind_all("<Control-Shift-Z>", self.redo_shortcut)
        self.window.bind_all("<Control-d>", self.duplicate_shortcut)
        self.window.bind_all("<Delete>", self.delete_shortcut)
        self.window.bind_all("<Control-plus>", lambda event: self.zoom_shortcut(1.25))
        self.window.bind_all("<Control-equal>", lambda event: self.zoom_shortcut(1.25))
        self.window.bind_all("<Control-minus>", lambda event: self.zoom_shortcut(0.8))
        self.window.bind_all("<Control-Key-0>", lambda event: self.fit_shortcut())
        self.window.bind_all("<Control-s>", lambda event: self.save_shortcut())
        self.window.bind_all("<Control-o>", lambda event: self.open_shortcut())
        self.window.bind_all("<Control-n>", lambda event: self.new_shortcut())
        self.window.bind_all("<Control-a>", self.select_all_shortcut)
        self.refresh_frame_controls()

    def values(self, kind=None):
        fallback = self.document.shapes[self.selected].kind if self.selected is not None else "rectangle"
        return Shape(kind or (self.tool.get() if self.tool.get() != "select" else fallback),
                     float(self.fields["x"].get()), float(self.fields["y"].get()),
                     float(self.fields["width"].get()), float(self.fields["height"].get()),
                     int(self.fields["speed"].get()), int(self.fields["power"].get()), int(self.fields["passes"].get()),
                     self.fields["text"].get(), self.font_family.get(),
                     rotation=float(self.fields["rotation"].get()),
                     mirror_x=self.mirror_x.get(), mirror_y=self.mirror_y.get())

    def act(self, operation):
        try:
            operation()
        except (ValueError, IndexError, GuardError) as exc:
            self.message.set(str(exc))

    def selected_indices(self):
        valid = {index for index in self.selection if 0 <= index < len(self.document.shapes)}
        if self.selected is not None and 0 <= self.selected < len(self.document.shapes):
            if valid and self.selected not in valid:
                valid = {self.selected}
            else:
                valid.add(self.selected)
        self.selection = valid
        return tuple(sorted(valid))

    def set_selection(self, indices, primary=None):
        self.selection = {index for index in indices if 0 <= index < len(self.document.shapes)}
        if primary in self.selection:
            self.selected = primary
        else:
            self.selected = max(self.selection) if self.selection else None

    def select_all_shortcut(self, event=None):
        if not self.design_shortcut_allowed(event):
            return
        self.set_selection(range(len(self.document.shapes)))
        self.refresh(f"Selected {len(self.selection)} objects." if self.selection else "The design is empty.")
        return "break"

    def snapshot(self):
        return tuple(self.document.shapes)

    def checkpoint(self):
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()

    def restore(self, snapshot, message):
        self.document.shapes = list(snapshot)
        self.set_selection(self.selection)
        if not self.selection and self.document.shapes:
            self.set_selection((min(self.selected or 0, len(self.document.shapes)-1),))
        self.refresh(message)

    def undo(self):
        if not self.undo_stack:
            self.message.set("Nothing to undo.")
            return
        self.redo_stack.append(self.snapshot())
        self.restore(self.undo_stack.pop(), "Undid the last design change.")

    def redo(self):
        if not self.redo_stack:
            self.message.set("Nothing to redo.")
            return
        self.undo_stack.append(self.snapshot())
        self.restore(self.redo_stack.pop(), "Redid the design change.")

    def design_shortcut_allowed(self, event=None):
        if not self.window.winfo_ismapped():
            return False
        if event is not None and event.widget.winfo_class() in ("TEntry", "Entry", "TCombobox", "Text"):
            return False
        return True

    def undo_shortcut(self, event=None):
        if self.design_shortcut_allowed(event):
            self.undo()
            return "break"

    def redo_shortcut(self, event=None):
        if self.design_shortcut_allowed(event):
            self.redo()
            return "break"

    def duplicate_shortcut(self, event=None):
        if self.design_shortcut_allowed(event):
            self.duplicate()
            return "break"

    def zoom_shortcut(self, factor):
        if self.window.winfo_ismapped():
            self.zoom_by(factor)
            return "break"

    def fit_shortcut(self):
        if self.window.winfo_ismapped():
            self.fit_view()
            return "break"

    def save_shortcut(self):
        if self.window.winfo_ismapped():
            self.save_design()
            return "break"

    def open_shortcut(self):
        if self.window.winfo_ismapped():
            self.open_design()
            return "break"

    def new_shortcut(self):
        if self.window.winfo_ismapped():
            self.new_design()
            return "break"

    def new_design(self):
        if self.document.shapes and not messagebox.askyesno("New design", "Clear the current design? You can still use Undo afterward.", parent=self.window):
            return
        self.checkpoint()
        self.document.shapes = []
        self.set_selection(())
        self.design_path = None
        self.fit_view()
        self.refresh("New empty design.")

    def open_design(self):
        path = filedialog.askopenfilename(parent=self.window, title="Open Atomstack design",
                                          filetypes=(("Atomstack design", "*.atomdesign"), ("JSON files", "*.json")))
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("format") != "atomstack-design" or not isinstance(data.get("shapes"), list):
                raise ValueError("This is not an Atomstack design file.")
            shapes = [Shape(**item).validated() for item in data["shapes"]]
            self.checkpoint()
            self.document.shapes = shapes
            self.set_selection((0,) if shapes else ())
            self.design_path = Path(path)
            self.fit_view()
            self.refresh(f"Opened {self.design_path.name}.")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.message.set(f"Could not open design: {exc}")

    def save_design(self):
        path = self.design_path
        if path is None:
            chosen = filedialog.asksaveasfilename(parent=self.window, title="Save Atomstack design",
                                                   defaultextension=".atomdesign",
                                                   filetypes=(("Atomstack design", "*.atomdesign"),))
            if not chosen:
                return
            path = Path(chosen)
        try:
            payload = {"format": "atomstack-design", "version": 1,
                       "bed": {"width": BED_X, "height": BED_Y},
                       "shapes": [shape.__dict__ for shape in self.document.shapes]}
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.design_path = path
            self.message.set(f"Saved {path.name}.")
        except OSError as exc:
            self.message.set(f"Could not save design: {exc}")

    def delete_shortcut(self, event=None):
        if self.design_shortcut_allowed(event):
            self.delete()
            return "break"

    def add_from_values(self):
        def add():
            kind = self.tool.get() if self.tool.get() != "select" else None
            candidate = self.values(kind).validated()
            self.checkpoint()
            self.selected = self.document.add(candidate)
            self.set_selection((self.selected,))
            self.refresh("Geometry added. Frame it first, then send the job when placement is correct.")
        self.act(add)

    def apply(self):
        def update():
            if self.selected is None:
                raise ValueError("Select a shape first.")
            candidate = self.values().validated()
            self.checkpoint()
            indices = self.selected_indices()
            if len(indices) > 1:
                for index in indices:
                    self.document.update(index, speed=candidate.speed, power=candidate.power, passes=candidate.passes)
                self.refresh(f"Updated process settings for {len(indices)} objects.")
            else:
                self.document.update(self.selected, **candidate.__dict__)
                self.refresh("Selected geometry updated.")
        self.act(update)

    def delete(self):
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select a shape first.")
            return
        self.checkpoint()
        for index in reversed(indices):
            self.document.delete(index)
        self.set_selection(())
        self.refresh(f"Deleted {len(indices)} object{'s' if len(indices) != 1 else ''}.")

    def duplicate(self):
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select a shape first.")
            return
        shapes = [self.document.shapes[index] for index in indices]
        bounds = [shape_bounds(shape) for shape in shapes]
        right = max(value[2] for value in bounds)
        top = max(value[3] for value in bounds)
        dx = min(5.0, BED_X-right)
        dy = min(5.0, BED_Y-top)
        if dx <= 0 and dy <= 0:
            self.message.set("There is no room to offset a duplicate inside the bed.")
            return
        self.checkpoint()
        copies = [self.document.add(Shape(**{**shape.__dict__, "x": shape.x+max(0, dx), "y": shape.y+max(0, dy)})) for shape in shapes]
        self.set_selection(copies, copies[-1])
        self.refresh(f"Duplicated {len(copies)} object{'s' if len(copies) != 1 else ''} with a 5 mm offset.")

    def transform_selection(self, action):
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select one or more objects first.")
            return
        shapes = {index: self.document.shapes[index] for index in indices}
        bounds = [shape_bounds(shape) for shape in shapes.values()]
        center_x = (min(b[0] for b in bounds)+max(b[2] for b in bounds))/2
        center_y = (min(b[1] for b in bounds)+max(b[3] for b in bounds))/2
        candidates = {}
        for index, shape in shapes.items():
            shape_center_x, shape_center_y = shape.x+shape.width/2, shape.y+shape.height/2
            offset_x, offset_y = shape_center_x-center_x, shape_center_y-center_y
            values = dict(shape.__dict__)
            if action in ("rotate_left", "rotate_right"):
                angle = 90 if action == "rotate_left" else -90
                if angle == 90:
                    new_center_x, new_center_y = center_x-offset_y, center_y+offset_x
                else:
                    new_center_x, new_center_y = center_x+offset_y, center_y-offset_x
                values.update(x=new_center_x-shape.width/2, y=new_center_y-shape.height/2,
                              rotation=(shape.rotation+angle) % 360)
            elif action == "flip_horizontal":
                new_center_x = 2*center_x-shape_center_x
                values.update(x=new_center_x-shape.width/2, rotation=(-shape.rotation) % 360,
                              mirror_x=not shape.mirror_x)
            elif action == "flip_vertical":
                new_center_y = 2*center_y-shape_center_y
                values.update(y=new_center_y-shape.height/2, rotation=(-shape.rotation) % 360,
                              mirror_y=not shape.mirror_y)
            candidates[index] = Shape(**values)
        candidate_bounds = [shape_bounds(shape) for shape in candidates.values()]
        left, bottom = min(b[0] for b in candidate_bounds), min(b[1] for b in candidate_bounds)
        right, top = max(b[2] for b in candidate_bounds), max(b[3] for b in candidate_bounds)
        if right-left > BED_X+1e-7 or top-bottom > BED_Y+1e-7:
            self.message.set("The transformed selection is larger than the machine bed.")
            return
        dx = -left if left < 0 else BED_X-right if right > BED_X else 0
        dy = -bottom if bottom < 0 else BED_Y-top if top > BED_Y else 0
        try:
            candidates = {index: Shape(**{**shape.__dict__, "x": shape.x+dx, "y": shape.y+dy}).validated()
                          for index, shape in candidates.items()}
        except ValueError as exc:
            self.message.set(str(exc))
            return
        self.checkpoint()
        for index, shape in candidates.items():
            self.document.shapes[index] = shape
        self.load_selected_fields()
        label = {"rotate_left":"Rotated left 90°", "rotate_right":"Rotated right 90°",
                 "flip_horizontal":"Flipped horizontally", "flip_vertical":"Flipped vertically"}[action]
        self.refresh(f"{label}: {len(indices)} object{'s' if len(indices) != 1 else ''}.")

    def align_to_bed(self, alignment):
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select a shape first.")
            return
        shapes = [self.document.shapes[index] for index in indices]
        bounds = [shape_bounds(shape) for shape in shapes]
        left, right = min(b[0] for b in bounds), max(b[2] for b in bounds)
        bottom, top = min(b[1] for b in bounds), max(b[3] for b in bounds)
        dx = dy = 0.0
        if alignment == "left": dx = -left
        elif alignment == "center_x": dx = (BED_X-(right-left))/2-left
        elif alignment == "right": dx = BED_X-right
        elif alignment == "bottom": dy = -bottom
        elif alignment == "center_y": dy = (BED_Y-(top-bottom))/2-bottom
        elif alignment == "top": dy = BED_Y-top
        self.checkpoint()
        for index in indices:
            shape = self.document.shapes[index]
            self.document.update(index, x=shape.x+dx, y=shape.y+dy)
        self.load_selected_fields()
        self.refresh(f"Aligned {len(indices)} object{'s' if len(indices) != 1 else ''} to {alignment.replace('_', ' ')}.")

    def distribute(self, axis):
        indices = self.selected_indices()
        if len(indices) < 3:
            self.message.set("Select at least three objects to distribute them.")
            return
        key = (lambda index: self.document.shapes[index].x+self.document.shapes[index].width/2) if axis == "horizontal" else (lambda index: self.document.shapes[index].y+self.document.shapes[index].height/2)
        ordered = sorted(indices, key=key)
        first, last = key(ordered[0]), key(ordered[-1])
        spacing = (last-first)/(len(ordered)-1)
        updates = {}
        for position, index in enumerate(ordered[1:-1], 1):
            shape = self.document.shapes[index]
            center = first+spacing*position
            value = center-shape.width/2 if axis == "horizontal" else center-shape.height/2
            changes = {"x": value} if axis == "horizontal" else {"y": value}
            try:
                Shape(**{**shape.__dict__, **changes}).validated()
            except ValueError:
                self.message.set(f"Cannot distribute by centers: {shape.label} would leave the bed.")
                return
            updates[index] = changes
        self.checkpoint()
        for index, changes in updates.items():
            self.document.update(index, **changes)
        self.load_selected_fields()
        self.refresh(f"Distributed {len(indices)} objects {axis}ly.")

    def reorder(self, direction):
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select a shape first.")
            return
        selected = set(indices)
        scan = range(len(self.document.shapes)-2, -1, -1) if direction > 0 else range(1, len(self.document.shapes))
        movable = any(index in selected and index+direction not in selected for index in scan)
        if not movable:
            self.message.set("The selected object is already at that layer limit.")
            return
        self.checkpoint()
        for index in scan:
            other = index+direction
            if index in selected and other not in selected:
                self.document.shapes[index], self.document.shapes[other] = self.document.shapes[other], self.document.shapes[index]
                selected.remove(index); selected.add(other)
                if self.selected == index:
                    self.selected = other
        self.set_selection(selected, self.selected)
        self.refresh(f"Changed the stacking order for {len(indices)} object{'s' if len(indices) != 1 else ''}.")

    def refresh_materials(self):
        names = self.materials.names
        self.material_combo.configure(values=names)
        if self.material_name.get() not in names:
            self.material_name.set(names[0] if names else "")
        state = "normal" if names else "disabled"
        self.material_apply.configure(state=state)
        self.material_delete.configure(state=state)

    def apply_material(self):
        def apply():
            preset = self.materials.get(self.material_name.get())
            self.fields["speed"].set(str(preset.speed))
            self.fields["power"].set(str(preset.power))
            self.fields["passes"].set(str(preset.passes))
            if self.selected is not None:
                self.apply()
            self.message.set(f"Applied material preset: {preset.name}.")
        self.act(apply)

    def save_material(self):
        def save():
            name = simpledialog.askstring("Save material", "Material and operation name:", parent=self.window)
            if not name:
                return
            preset = MaterialPreset(name, int(self.fields["speed"].get()),
                                    int(self.fields["power"].get()), int(self.fields["passes"].get()))
            self.materials.save(preset)
            self.refresh_materials()
            self.material_name.set(preset.name.strip())
            self.message.set(f"Saved material preset: {preset.name.strip()}.")
        self.act(save)

    def delete_material(self):
        def delete():
            name = self.material_name.get()
            self.materials.delete(name)
            self.refresh_materials()
            self.message.set(f"Deleted material preset: {name}.")
        self.act(delete)

    def open_burn_test(self):
        BurnTestWindow(self)

    def open_preview(self):
        def open_window():
            self.preview_window = JobPreviewWindow(self)
        self.act(open_window)

    def add_burn_test_values(self, x, y, width, height, columns, rows,
                             min_speed, max_speed, min_power, max_power):
        if not 1 <= columns <= 10 or not 1 <= rows <= 10:
            raise ValueError("Rows and columns must be between 1 and 10.")
        speeds = [round(min_speed + (max_speed-min_speed)*i/max(1, columns-1)) for i in range(columns)]
        powers = [round(min_power + (max_power-min_power)*i/max(1, rows-1)) for i in range(rows)]
        self.checkpoint()
        indices = self.document.add_burn_test(x, y, width, height, speeds, powers)
        self.set_selection(indices, indices[0])
        self.refresh(f"Added {len(indices)} burn-test cells. Labels show speed F and power S; Frame before sending.")

    def send_job(self):
        def send():
            if self.controller.origin is None:
                raise GuardError("Home and confirm bottom-left before sending a job.")
            code = self.document.gcode(self.controller.origin)
            highest = max(shape.power for shape in self.document.shapes)
            if not messagebox.askokcancel("Send job to Atomstack",
                    f"This will fire the laser and run {len(self.document.shapes)} geometry item(s).\n\nMaximum power: S{highest}\n\nConfirm the material is secured, ventilation is on, and you are watching the machine.",
                    parent=self.window):
                return
            self.controller.run_job(code.splitlines())
            self.message.set("Job started. Keep watching the machine; STOP / RESET remains available in the main window.")
        self.act(send)

    def frame(self):
        def start():
            points = self.document.frame_points()
            self.controller.frame(points, int(self.frame_speed.get()))
            self.message.set("Frame started. The laser remains off; watch the head trace the outline.")
        self.act(start)

    def refresh_frame_controls(self):
        if not self.window.winfo_exists():
            return
        self.update_frame_controls()
        self.window.after(250, self.refresh_frame_controls)

    def update_frame_controls(self):
        if not self.document.shapes:
            ready, reason = False, "Add geometry to enable Frame."
        else:
            ready, reason = self.controller.frame_readiness()
        self.frame_button.configure(state="normal" if ready else "disabled")
        self.preview_button.configure(state="normal" if self.document.shapes else "disabled")
        job_running = self.controller.phase.startswith("job")
        self.send_button.configure(state="normal" if ready and self.document.shapes else "disabled")
        self.pause_button.configure(state="normal" if job_running and not self.controller.job_paused else "disabled")
        self.resume_button.configure(state="normal" if job_running and self.controller.job_paused else "disabled")
        self.frame_status.set(self.controller.message if job_running else reason)

    def refresh(self, message=None):
        self.listbox.delete(0, "end")
        for shape in self.document.shapes:
            self.listbox.insert("end", shape.label)
        indices = self.selected_indices()
        for index in indices:
            self.listbox.selection_set(index)
        if self.selected is not None and self.selected < len(self.document.shapes):
            self.listbox.see(self.selected)
            self.load_selected_fields()
        self.update_selection_ui(indices)
        if message:
            self.message.set(message)
        self.undo_button.configure(state="normal" if self.undo_stack else "disabled")
        self.redo_button.configure(state="normal" if self.redo_stack else "disabled")
        self.draw()
        self.update_frame_controls()

    def update_selection_ui(self, indices=None):
        indices = tuple(indices if indices is not None else self.selected_indices())
        self.selection_status.set("No selection" if not indices else
                                  "Selected geometry" if len(indices) == 1 else f"{len(indices)} objects · process settings")
        multiple = len(indices) > 1
        for name in ("text", "x", "y", "width", "height", "rotation"):
            self.field_entries[name].configure(state="disabled" if multiple else "normal")
        self.font_combo.configure(state="disabled" if multiple else "readonly")
        self.mirror_x_button.configure(state="disabled" if multiple else "normal")
        self.mirror_y_button.configure(state="disabled" if multiple else "normal")
        self.add_value_button.configure(state="disabled" if multiple else "normal")
        self.apply_value_button.configure(text=f"Apply process to {len(indices)}" if multiple else "Apply values")

    def select(self, _event=None):
        selection = self.listbox.curselection()
        if not selection:
            self.set_selection(())
            self.update_selection_ui(())
            self.draw()
            return
        primary = self.selected if self.selected in selection else selection[-1]
        self.set_selection(selection, primary)
        self.load_selected_fields()
        self.update_selection_ui(selection)
        self.draw()

    def load_selected_fields(self):
        if self.selected is None or not 0 <= self.selected < len(self.document.shapes):
            return
        shape = self.document.shapes[self.selected]
        for name in self.fields:
            value = getattr(shape, name)
            self.fields[name].set(f"{value:g}" if isinstance(value, (int, float)) else value)
        self.font_family.set(shape.font_family)
        self.mirror_x.set(shape.mirror_x)
        self.mirror_y.set(shape.mirror_y)

    def transform(self):
        return fit_viewport(max(100, self.bed.winfo_width()), max(100, self.bed.winfo_height()),
                            64, 50, self.view_zoom, self.pan_x, self.pan_y)

    def to_bed(self, event, clamp=True):
        x, y = self.transform().to_bed(event.x, event.y)
        return clamp_to_bed(x, y) if clamp else (x, y)

    def grid_step(self):
        try:
            return float(self.grid_size.get().split()[0])
        except ValueError:
            return 1.0

    def snap(self, value):
        return snap_value(value, self.grid_step(), self.snap_enabled.get())

    def zoom_by(self, factor, center=None):
        old = self.view_zoom
        zoom = clamp_zoom(old, factor, 0.5, 8.0)
        if center and zoom != old:
            # Sample the cursor point under the OLD zoom before changing it,
            # otherwise both samples agree and the correction is always zero.
            before = self.to_bed(center, clamp=False)
            self.view_zoom = zoom
            after = self.to_bed(center, clamp=False)
            dx, dy = zoom_pan_correction(before, after, self.transform().scale)
            self.pan_x += dx
            self.pan_y += dy
        else:
            self.view_zoom = zoom
        self.zoom_text.set(f"{self.view_zoom * 100:.0f}%")
        self.draw()

    def mousewheel(self, event):
        self.zoom_by(1.2 if event.delta > 0 else 1 / 1.2, event)
        return "break"

    def fit_view(self):
        self.view_zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.zoom_text.set("100%")
        self.draw()

    def pan_press(self, event):
        self.interaction = {"mode": "pan", "start": (event.x, event.y), "pan": (self.pan_x, self.pan_y)}
        self.bed.configure(cursor="fleur")

    def pan_drag(self, event):
        if not self.interaction or self.interaction.get("mode") != "pan":
            return
        sx, sy = self.interaction["start"]
        px, py = self.interaction["pan"]
        self.pan_x, self.pan_y = px + event.x - sx, py + event.y - sy
        self.draw()

    def pan_release(self, _event=None):
        if self.interaction and self.interaction.get("mode") == "pan":
            self.interaction = None
        self.bed.configure(cursor="")

    def shape_at(self, event):
        x, y = self.to_bed(event, clamp=False)
        bounds = [shape_bounds(shape) for shape in self.document.shapes]
        return topmost_at(x, y, bounds, self.transform().millimetres(7))

    def handle_at(self, event):
        if len(self.selected_indices()) != 1 or self.selected is None or self.selected >= len(self.document.shapes):
            return None
        x, y = self.to_bed(event, clamp=False)
        shape = self.document.shapes[self.selected]
        if shape.rotation % 360:
            return None
        handles = corner_handles(shape.x, shape.y, shape.width, shape.height)
        return handle_hit(x, y, handles, self.transform().millimetres(9))

    def begin_design_change(self):
        if self.interaction and not self.interaction.get("changed"):
            self.undo_stack.append(self.interaction["snapshot"])
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
            self.interaction["changed"] = True

    def press(self, event):
        if self.tool.get() == "select":
            handle = self.handle_at(event)
            index = self.selected if handle else self.shape_at(event)
            if index is None:
                self.set_selection(())
                self.refresh("Click an object to select it, or choose a drawing tool.")
                return
            ctrl = bool(getattr(event, "state", 0) & 0x0004)
            if ctrl and not handle:
                updated = set(self.selected_indices())
                if index in updated:
                    updated.remove(index)
                else:
                    updated.add(index)
                self.set_selection(updated, index if index in updated else None)
                self.refresh(f"Selected {len(updated)} objects." if updated else "Selection cleared.")
                return
            if index not in self.selected_indices():
                self.set_selection((index,), index)
            else:
                self.selected = index
            indices = self.selected_indices()
            originals = {item: self.document.shapes[item] for item in indices}
            self.load_selected_fields()
            self.interaction = {"mode": "resize" if handle else "move", "handle": handle,
                                "start": self.to_bed(event), "shape": self.document.shapes[index],
                                "indices": indices, "shapes": originals,
                                "snapshot": self.snapshot(), "changed": False}
            self.draw()
            return
        self.drag_start = self.to_bed(event)

    def drag(self, event):
        if self.interaction and self.interaction.get("mode") in ("move", "resize"):
            current = self.to_bed(event)
            original = self.interaction["shape"]
            if self.interaction["mode"] == "move":
                dx, dy = current[0] - self.interaction["start"][0], current[1] - self.interaction["start"][1]
                originals = tuple(self.interaction["shapes"].values())
                bounds = [shape_bounds(shape) for shape in originals]
                left, right = min(b[0] for b in bounds), max(b[2] for b in bounds)
                bottom, top = min(b[1] for b in bounds), max(b[3] for b in bounds)
                dx = max(-left, min(BED_X-right, dx))
                dy = max(-bottom, min(BED_Y-top, dy))
                if self.snap_enabled.get():
                    dx = self.snap(original.x+dx)-original.x
                    dy = self.snap(original.y+dy)-original.y
                    dx = max(-left, min(BED_X-right, dx))
                    dy = max(-bottom, min(BED_Y-top, dy))
                candidates = {index: {"x": shape.x+dx, "y": shape.y+dy}
                              for index, shape in self.interaction["shapes"].items()}
            else:
                opposite = {"BL": (original.x+original.width, original.y+original.height),
                            "BR": (original.x, original.y+original.height),
                            "TL": (original.x+original.width, original.y),
                            "TR": (original.x, original.y)}[self.interaction["handle"]]
                cx, cy = self.snap(current[0]), self.snap(current[1])
                candidate = {"x": min(cx, opposite[0]), "y": min(cy, opposite[1]),
                             "width": abs(cx-opposite[0]), "height": abs(cy-opposite[1])}
                if original.kind != "line":
                    candidate["width"] = max(0.1, candidate["width"])
                    candidate["height"] = max(0.1, candidate["height"])
                candidates = {self.selected: candidate}
            self.begin_design_change()
            try:
                for index, changes in candidates.items():
                    self.document.update(index, **changes)
                self.load_selected_fields()
                self.draw()
            except ValueError:
                pass
            return
        if not self.drag_start:
            return
        x0, y0, scale = self.transform()
        x1, y1 = self.drag_start
        x2, y2 = self.to_bed(event)
        coords = (x0 + min(x1, x2) * scale, y0 - max(y1, y2) * scale,
                  x0 + max(x1, x2) * scale, y0 - min(y1, y2) * scale)
        if self.preview_item:
            self.bed.delete(self.preview_item)
        if self.tool.get() == "circle":
            self.preview_item = self.bed.create_oval(*coords, outline="#155eef", dash=(4, 2), width=2)
        elif self.tool.get() == "line":
            self.preview_item = self.bed.create_line(coords[0], coords[3], coords[2], coords[1], fill="#155eef", dash=(4, 2), width=2)
        else:
            self.preview_item = self.bed.create_rectangle(*coords, outline="#155eef", dash=(4, 2), width=2)

    def release(self, event):
        if self.interaction and self.interaction.get("mode") in ("move", "resize"):
            changed = self.interaction.get("changed")
            self.interaction = None
            self.refresh("Object updated." if changed else "Object selected.")
            return
        if not self.drag_start:
            return
        start, end = self.drag_start, self.to_bed(event)
        self.drag_start = None
        x, y = min(start[0], end[0]), min(start[1], end[1])
        width, height = abs(end[0] - start[0]), abs(end[1] - start[1])
        for name, value in (("x", x), ("y", y), ("width", width), ("height", height)):
            self.fields[name].set(f"{value:.3f}")
        self.add_from_values()

    def nudge(self, direction, event=None):
        if event is not None and event.widget.winfo_class() in ("TEntry", "Entry", "TCombobox", "Text"):
            return
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select an object before using the arrow keys.")
            return "break"
        shapes = [self.document.shapes[index] for index in indices]
        step = self.grid_step() if self.snap_enabled.get() else 0.1
        if event is not None and event.state & 0x0001:
            step *= 10
        dx, dy = {"Left": (-step, 0), "Right": (step, 0), "Up": (0, step), "Down": (0, -step)}[direction]
        bounds = [shape_bounds(shape) for shape in shapes]
        left, right = min(b[0] for b in bounds), max(b[2] for b in bounds)
        bottom, top = min(b[1] for b in bounds), max(b[3] for b in bounds)
        dx = max(-left, min(BED_X-right, dx))
        dy = max(-bottom, min(BED_Y-top, dy))
        if dx or dy:
            self.checkpoint()
            for index in indices:
                shape = self.document.shapes[index]
                self.document.update(index, x=shape.x+dx, y=shape.y+dy)
            self.load_selected_fields()
            self.refresh(f"Moved {len(indices)} object{'s' if len(indices) != 1 else ''} by X {dx:g}, Y {dy:g}.")
        return "break"

    def draw(self):
        self.bed.delete("all")
        x0, y0, scale = self.transform()
        x1, y1 = x0 + BED_X * scale, y0 - BED_Y * scale
        self.bed.create_rectangle(x0, y1, x1, y0, fill="white", outline="#718793")
        for x in range(0, 366, 50):
            self.bed.create_line(x0 + x * scale, y0, x0 + x * scale, y1, fill="#e1e7ea")
        for y in range(0, 306, 50):
            self.bed.create_line(x0, y0 - y * scale, x1, y0 - y * scale, fill="#e1e7ea")
        self.bed.create_rectangle(x0, y1, x1, y0, outline="#718793")
        self.bed.create_text(x0, y0 + 13, text="0, 0", anchor="w", fill="#405864", font=("Segoe UI", 9))
        self.bed.create_text(x1, y1 - 9, text=f"{BED_X:g}, {BED_Y:g}", anchor="e", fill="#405864", font=("Segoe UI", 9))
        selected_indices = set(self.selected_indices())
        for index, shape in enumerate(self.document.shapes):
            color, width = ("#155eef", 3) if index in selected_indices else ("#405864", 2)
            for path in shape_paths(shape):
                coords = [coordinate for point in path for coordinate in (x0+point[0]*scale, y0-point[1]*scale)]
                if len(coords) >= 4:
                    self.bed.create_line(*coords, fill=color, width=width)
            bound_left, bound_bottom, bound_right, bound_top = shape_bounds(shape)
            left, right = x0+bound_left*scale, x0+bound_right*scale
            bottom, top = y0-bound_bottom*scale, y0-bound_top*scale
            if shape.note:
                self.bed.create_text((left+right)/2, (top+bottom)/2,
                                     text=shape.note.replace(" · ", "\n"), justify="center",
                                     fill="#20333d", font=("Segoe UI", 7))
            if index == self.selected and len(selected_indices) == 1:
                for hx, hy in ((left, bottom), (right, bottom), (left, top), (right, top)):
                    self.bed.create_rectangle(hx-5, hy-5, hx+5, hy+5, fill="white", outline="#155eef", width=2)
        if len(selected_indices) > 1:
            shapes = [self.document.shapes[index] for index in selected_indices]
            bounds = [shape_bounds(shape) for shape in shapes]
            left = x0+min(bound[0] for bound in bounds)*scale
            right = x0+max(bound[2] for bound in bounds)*scale
            bottom = y0-min(bound[1] for bound in bounds)*scale
            top = y0-max(bound[3] for bound in bounds)*scale
            self.bed.create_rectangle(left, top, right, bottom, outline="#155eef", width=2, dash=(6, 3))
        if self.document.shapes:
            points = self.document.frame_points()
            coords = []
            for x, y in points:
                coords.extend((x0 + x * scale, y0 - y * scale))
            self.bed.create_line(*coords, fill="#d66a1f", width=2, dash=(7, 4))


class JobPreviewWindow:
    """Read-only visualization of the exact geometry order sent to the controller."""
    def __init__(self, editor):
        self.editor = editor
        self.segments = editor.document.preview_segments()
        self.metrics = editor.document.job_metrics()
        self.window = tk.Toplevel(editor.window)
        self.window.title("Job preview · no machine movement")
        self.window.geometry("1050x780")
        self.window.minsize(800, 600)
        self.progress = tk.DoubleVar(value=100)
        self.playing = False
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Job preview", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="READ ONLY · NO MACHINE COMMANDS", style="Quiet.TLabel").pack(side="right")
        metrics = self.metrics
        summary = (f"{len(editor.document.shapes)} objects   ·   {metrics['segments']} moves   ·   "
                   f"Laser path {metrics['burn_distance']:.1f} mm   ·   Travel {metrics['rapid_distance']:.1f} mm   ·   "
                   f"Estimated {self.duration(metrics['estimated_seconds'])}   ·   Max S{metrics['max_power']}")
        ttk.Label(outer, text=summary, style="Section.TLabel").pack(fill="x", pady=(0, 12))
        self.canvas = tk.Canvas(outer, background="#eef3f6", highlightthickness=1, highlightbackground="#aebdc4")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(12, 0))
        self.play_button = ttk.Button(controls, text="Play from start", command=self.play)
        self.play_button.pack(side="left")
        ttk.Button(controls, text="Show complete", command=lambda: self.progress.set(100)).pack(side="left", padx=6)
        ttk.Label(controls, text="Execution order").pack(side="left", padx=(16, 6))
        ttk.Scale(controls, from_=0, to=100, variable=self.progress, command=lambda value: self.draw()).pack(side="left", fill="x", expand=True)
        self.percent = ttk.Label(controls, text="100%", width=5, anchor="e")
        self.percent.pack(side="left", padx=(6, 0))
        legend = ttk.Frame(outer)
        legend.pack(fill="x", pady=(8, 0))
        ttk.Label(legend, text="Dashed gray: laser-off travel", style="Quiet.TLabel").pack(side="left")
        ttk.Label(legend, text="Blue → red: increasing laser power", style="Quiet.TLabel").pack(side="left", padx=20)
        ttk.Label(legend, text="Estimate excludes controller and material delays", style="Quiet.TLabel").pack(side="right")
        self.progress.trace_add("write", lambda *_: self.draw())

    @staticmethod
    def duration(seconds):
        if seconds < 60:
            return f"{seconds:.0f} sec"
        minutes, remaining = divmod(round(seconds), 60)
        return f"{minutes} min {remaining:02d} sec"

    @staticmethod
    def power_color(power):
        ratio = max(0.0, min(1.0, power / 1000))
        low, high = (21, 94, 239), (220, 38, 38)
        rgb = tuple(round(a + (b-a)*ratio) for a, b in zip(low, high))
        return "#" + "".join(f"{value:02x}" for value in rgb)

    def transform(self):
        width, height = max(200, self.canvas.winfo_width()), max(200, self.canvas.winfo_height())
        scale = min((width-70)/BED_X, (height-60)/BED_Y)
        return (width-BED_X*scale)/2, (height+BED_Y*scale)/2, scale

    def draw(self):
        if not self.window.winfo_exists():
            return
        self.canvas.delete("all")
        x0, y0, scale = self.transform()
        x1, y1 = x0+BED_X*scale, y0-BED_Y*scale
        self.canvas.create_rectangle(x0, y1, x1, y0, fill="white", outline="#718793")
        for x in range(0, 366, 50):
            self.canvas.create_line(x0+x*scale, y0, x0+x*scale, y1, fill="#e1e7ea")
        for y in range(0, 306, 50):
            self.canvas.create_line(x0, y0-y*scale, x1, y0-y*scale, fill="#e1e7ea")
        cutoff = round(len(self.segments) * self.progress.get() / 100)
        for index, (kind, first, second, _feed, power, _shape, _pass) in enumerate(self.segments):
            coords = (x0+first[0]*scale, y0-first[1]*scale, x0+second[0]*scale, y0-second[1]*scale)
            complete = index < cutoff
            if kind == "rapid":
                self.canvas.create_line(*coords, fill="#8a96a8" if complete else "#e3e7ec", dash=(5, 4), width=1)
            else:
                self.canvas.create_line(*coords, fill=self.power_color(power) if complete else "#d9e0e7", width=2 if complete else 1)
        if cutoff:
            point = self.segments[min(cutoff, len(self.segments))-1][2]
            px, py = x0+point[0]*scale, y0-point[1]*scale
            self.canvas.create_oval(px-5, py-5, px+5, py+5, fill="#155eef", outline="white", width=2)
        self.percent.configure(text=f"{self.progress.get():.0f}%")

    def play(self):
        self.progress.set(0)
        self.playing = True
        self.play_button.configure(state="disabled")
        self.advance()

    def advance(self):
        if not self.playing or not self.window.winfo_exists():
            return
        value = self.progress.get() + 1
        self.progress.set(min(100, value))
        if value >= 100:
            self.playing = False
            self.play_button.configure(state="normal")
        else:
            self.window.after(30, self.advance)


class BurnTestWindow:
    def __init__(self, editor):
        self.editor = editor
        self.window = tk.Toplevel(editor.window)
        self.window.title("Create burn-test grid")
        self.window.resizable(False, False)
        self.values = {name: tk.StringVar(value=value) for name, value in {
            "x":"10", "y":"10", "width":"15", "height":"10", "columns":"4", "rows":"4",
            "min_speed":"1000", "max_speed":"6000", "min_power":"100", "max_power":"500"}.items()}
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Burn-test grid", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        labels = (("x", "Left X · mm"), ("y", "Bottom Y · mm"), ("width", "Cell width · mm"),
                  ("height", "Cell height · mm"), ("columns", "Speed columns"), ("rows", "Power rows"),
                  ("min_speed", "Minimum speed"), ("max_speed", "Maximum speed"),
                  ("min_power", "Minimum power"), ("max_power", "Maximum power"))
        for row, (name, label) in enumerate(labels, 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 18))
            ttk.Entry(frame, textvariable=self.values[name], width=12).grid(row=row, column=1, sticky="e")
        self.message = tk.StringVar(value="Creates test cells that can be framed and sent directly over USB.")
        ttk.Label(frame, textvariable=self.message, style="Quiet.TLabel", wraplength=300).grid(row=11, column=0, columnspan=2, sticky="w", pady=(10, 8))
        ttk.Button(frame, text="Add test grid", command=self.add).grid(row=12, column=0, columnspan=2, sticky="ew")

    def add(self):
        try:
            number = lambda name: float(self.values[name].get())
            integer = lambda name: int(self.values[name].get())
            self.editor.add_burn_test_values(number("x"), number("y"), number("width"), number("height"),
                                             integer("columns"), integer("rows"), integer("min_speed"),
                                             integer("max_speed"), integer("min_power"), integer("max_power"))
            self.window.destroy()
        except (ValueError, IndexError) as exc:
            self.message.set(str(exc))


def main():
    parser = argparse.ArgumentParser(description="Personal Atomstack USB controller")
    parser.add_argument("--demo", action="store_true", help="Open simulator; no hardware is accessed")
    parser.add_argument("--port", help="Connect the explicitly selected port on launch; e.g. COM3")
    parser.add_argument("--report", help="Write a live JSON diagnostic snapshot to this file")
    parser.add_argument("--verify-features", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.verify_features:
        report = Path(args.verify_features)
        document = Document()
        document.add(Shape("text", 10, 10, 70, 15, 900, 250, 1, "ATOM 10", "Arial"))
        document.add_burn_test(10, 30, 10, 8, (300, 600), (100, 300))
        code = document.gcode((-288, -301))
        material_path = report.with_name("packaged-material-test.json")
        library = MaterialLibrary(material_path)
        library.save(MaterialPreset("Package test", 900, 250, 1))
        reloaded = MaterialLibrary(material_path).get("Package test")
        material_path.unlink(missing_ok=True)
        report.write_text(json.dumps({"result":"PASS", "vector_lines":code.count("G53 G1"),
                                      "test_cells":4, "material_power":reloaded.power}), encoding="utf-8")
        return
    if args.demo and args.port:
        parser.error("--demo and --port cannot be combined")
    root = tk.Tk()
    App(root, args.demo, args.port, args.report)
    root.mainloop()
