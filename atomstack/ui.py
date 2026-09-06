"""Native Windows control panel. Widgets only invoke guarded controller operations."""
import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from .controller import Controller, GuardError, JOG_FEEDS
from . import __version__
from .diagnostics import Reporter
from .projects import ProjectStore, atomic_json
from dataclasses import replace
from .protocol import READ_COMMANDS
from .transports import SerialTransport, Simulator, list_ports
from .geometry import Document, Shape, BED_X, BED_Y, shape_bounds, shape_paths
from .vector_text import ALIGNMENTS
from .materials import MaterialLibrary, MaterialPreset
from .viewport import (
    anchor_point, arrow_target, clamp_to_bed, clamp_zoom, corner_handles, fit_viewport,
    handle_at as handle_hit, inside_bed, snap_value, topmost_at,
    zoom_pan_correction, ROTATE_HANDLE, resize_from_handle, rotated_handles,
    rotation_from_pointer, transformed_point,
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
        self.alignment_path = ProjectStore().directory / "beam-alignment.json"
        self.alignment_error = None
        try:
            offset = json.loads(self.alignment_path.read_text(encoding="utf-8"))["offset_x"] if self.alignment_path.exists() else -12.5
            self.controller.set_beam_offset(offset)
        except (OSError, ValueError, KeyError, TypeError, GuardError) as exc:
            self.alignment_error = str(exc)
        self.controller.alignment_valid = self.alignment_error is None
        self.reporter = Reporter(report)
        self.active_port = None
        self.root.title(f"Atomstack — Personal controller {__version__}")
        self.root.geometry("1440x980")
        self.root.minsize(1100, 820)
        self.root.configure(bg="#25272b")
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
        style.configure(".", font=("Segoe UI", 9), background="#25272b", foreground="#e4e7eb")
        style.configure("TButton", padding=(7, 4), background="#363940", foreground="#e4e7eb")
        style.map("TButton", background=[("active", "#49515e"), ("disabled", "#2c2e33")], foreground=[("disabled", "#9399a3")])
        style.configure("Title.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Position.TLabel", font=("Consolas", 22, "bold"))
        style.configure("Quiet.TLabel", foreground="#b9c0cb")
        style.configure("TEntry", fieldbackground="#363940", foreground="#f1f3f5", insertcolor="white")
        style.configure("TCombobox", fieldbackground="#363940", background="#363940", foreground="#f1f3f5", arrowcolor="#f1f3f5")
        style.map("TCombobox", fieldbackground=[("readonly", "#363940")], foreground=[("readonly", "#f1f3f5")])
        style.configure("Treeview", background="#292c31", fieldbackground="#292c31", foreground="#e4e7eb", rowheight=24)
        style.configure("Treeview.Heading", background="#363940", foreground="#e4e7eb")
        style.map("TNotebook.Tab", background=[("selected", "#414752")], foreground=[("selected", "white")])
        self.root.option_add("*Menu.background", "#292c31")
        self.root.option_add("*Menu.foreground", "#e4e7eb")
        self.root.option_add("*Listbox.background", "#292c31")
        self.root.option_add("*Listbox.foreground", "#e4e7eb")
        style.configure("Stop.TButton", background="#dc2626", foreground="white", font=("Segoe UI", 11, "bold"))
        style.map("Stop.TButton", background=[("active", "#b91c1c")])
        style.configure("Accent.TButton", background="#155eef", foreground="white", font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#0f4fcf"), ("disabled", "#d5dfe2")])
        style.configure("TNotebook", borderwidth=0, tabmargins=(0, 8, 0, 0))
        style.configure("TNotebook.Tab", padding=(12, 5), font=("Segoe UI", 10, "bold"))
        outer = ttk.Frame(root, padding=8)
        outer.pack(fill="both", expand=True)
        # Reserve the recovery message before expandable areas consume height.
        ttk.Label(outer, textvariable=self.message, wraplength=940).pack(side="bottom", anchor="w", fill="x", pady=(10, 0))
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Atomstack", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Beam alignment", command=self.open_alignment).pack(side="left", padx=8)
        ttk.Label(header, textvariable=self.mode, style="Quiet.TLabel").pack(side="left", padx=20)
        ttk.Button(header, text="STOP / RESET", style="Stop.TButton", command=self.stop).pack(side="right", padx=(14, 0))
        ttk.Label(header, textvariable=self.status_text, style="Section.TLabel").pack(side="right")
        connect = ttk.Frame(outer)
        connect.pack(fill="x", pady=(18, 16))
        self.ports = ttk.Combobox(connect, textvariable=self.port, state="readonly", width=40)
        self.ports.pack(side="left")
        self.refresh_btn = ttk.Button(connect, text="Refresh ports", command=self.refresh)
        self.refresh_btn.pack(side="left", padx=6)
        self.connect_btn = ttk.Button(connect, text="Connect & home", command=self.connect)
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
        # Connecting homes and takes its own endpoint as bottom-left, so the
        # confirmation only appears for a session that asks to do it by hand.
        if not self.controller.auto_home:
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

    def open_alignment(self):
        window = tk.Toplevel(self.root)
        window.title("Beam alignment")
        window.transient(self.root)
        panel = ttk.Frame(window, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Positioning mark → cutting beam", style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Your estimate: cutting beam 12.5 mm LEFT of the mark.\nNegative = left · positive = right · 0 = no correction.\nJog and Frame follow the positioning mark.\nCutting shifts the head in the opposite direction.\nDesign coordinates stay unchanged.", wraplength=440).pack(anchor="w", pady=12)
        value = tk.StringVar(value=str(self.controller.beam_offset_x))
        ttk.Label(panel, text="Cutting beam X offset · mm").pack(anchor="w")
        ttk.Entry(panel, textvariable=value, width=16).pack(anchor="w", pady=6)
        feedback = tk.StringVar(value="12.5 mm is an estimate; refine it from your alignment measurement.")
        ttk.Label(panel, textvariable=feedback, wraplength=440).pack(anchor="w", pady=8)
        def save():
            previous = self.controller.beam_offset_x
            try:
                self.controller.set_beam_offset(float(value.get()))
                atomic_json(self.alignment_path, {"offset_x": self.controller.beam_offset_x})
            except (ValueError, OSError, GuardError) as exc:
                self.controller.beam_offset_x = previous
                feedback.set(str(exc))
                return
            self.alignment_error = None
            self.controller.alignment_valid = True
            feedback.set(f"Saved. Head compensation X {-self.controller.beam_offset_x:+g} mm during cutting.")
        ttk.Button(panel, text="Save alignment", command=save).pack(anchor="e", pady=(8, 0))
        return window

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
        if not self.geometry.confirm_discard():
            return
        self.geometry.project_store.clear()
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
        self.connect_btn.configure(text="Disconnect" if c.connected else "Connect & home")
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


class MultiLineField:
    """A Text widget with a StringVar's interface, so ``fields`` stays uniform."""

    def __init__(self, parent, width=12, height=3):
        self.widget = tk.Text(parent, width=width, height=height, wrap="word",
                              font=("Segoe UI", 9), undo=True)

    def get(self):
        return self.widget.get("1.0", "end-1c")

    def set(self, value):
        if self.get() == value:
            return
        self.widget.delete("1.0", "end")
        self.widget.insert("1.0", value)

    def pack(self, **options):
        self.widget.pack(**options)
        return self

    def __getattr__(self, name):
        # configure, bind, cget and the rest belong to the widget itself.
        return getattr(self.widget, name)


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
        self.project_store = ProjectStore()
        self.saved_payload = self.document.to_payload()
        self.document_status = tk.StringVar(value="Untitled · saved")
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
        self.keep_ratio = tk.BooleanVar(value=False)
        self.snap_enabled = tk.BooleanVar(value=True)
        self.grid_size = tk.StringVar(value="1 mm")
        self.design_path = None
        self.preview_window = None
        self.layer_window = None
        self.fields = {name: tk.StringVar(value=value) for name, value in {
            "x": "10", "y": "10", "width": "40", "height": "30",
            "speed": "1000", "power": "300", "passes": "1", "text": "ATOMSTACK", "rotation": "0"}.items()}
        self.mirror_x = tk.BooleanVar(value=False)
        self.mirror_y = tk.BooleanVar(value=False)
        self.bounds_text = tk.StringVar(value="Bounds · no selection")
        self.font_family = tk.StringVar(value="Arial")
        self.frame_speed = tk.StringVar(value="6000")
        self.material_name = tk.StringVar()
        outer = ttk.Frame(self.window, padding=4 if embedded else 8)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Design", style="Section.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.document_status, width=28, style="Quiet.TLabel").pack(side="right")
        ttk.Button(header, text="Save design", command=self.save_design).pack(side="right", padx=(4, 12))
        ttk.Button(header, text="Open", command=self.open_design).pack(side="right", padx=4)
        ttk.Button(header, text="New", command=self.new_design).pack(side="right", padx=4)
        file_menu = tk.Menu(self.window, tearoff=False)
        file_menu.add_command(label="Import SVG…", command=self.import_svg)
        file_menu.add_command(label="Import DXF…", command=self.import_dxf)
        file_menu.add_command(label="Import image…", command=self.import_image)
        file_menu.add_command(label="Save as…", command=self.save_as)
        file_menu.add_command(label="Recent designs…", command=self.open_recent)
        file_menu.add_command(label="Recover autosave…", command=self.recover_design)
        ttk.Menubutton(header, text="File", menu=file_menu).pack(side="right", padx=4)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(4, 4))
        content = ttk.Frame(outer)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)
        tool_rail = ttk.Frame(content, padding=(2, 8))
        tool_rail.grid(row=0, column=0, sticky="ns")
        dock = ttk.Frame(content, width=320)
        dock.grid(row=0, column=2, sticky="nsew")
        for text, value in (("Select / move", "select"), ("Pan", "pan"), ("Rectangle", "rectangle"), ("Ellipse", "circle"), ("Line", "line"), ("Text", "text")):
            ttk.Radiobutton(tool_rail, text=text, variable=self.tool, value=value).pack(anchor="w", pady=8)
        ttk.Separator(tool_rail).pack(fill="x", pady=8)
        ttk.Checkbutton(tool_rail, text="Keep ratio", variable=self.keep_ratio).pack(anchor="w", pady=4)
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

        for label, name in (("X", "x"), ("Y", "y"), ("W", "width"), ("H", "height"), ("Angle", "rotation")):
            ttk.Label(toolbar, text=label).pack(side="left", padx=(6, 2))
            ttk.Entry(toolbar, textvariable=self.fields[name], width=5).pack(side="left")
        ttk.Button(toolbar, text="Apply", command=self.apply).pack(side="left", padx=5)

        ttk.Button(toolbar, text="Place job…", command=self.open_placement).pack(side="left", padx=3)
        view_bar = ttk.Frame(outer)
        view_bar.pack(fill="x", pady=(0, 4))
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
        layer_button = ttk.Menubutton(view_bar, text="Stack order", menu=layer_menu)
        layer_button.pack(side="left", padx=3)
        ttk.Button(view_bar, text="Cut layers…", command=self.open_layers).pack(side="left", padx=3)
        production = tk.Menu(self.window, tearoff=False)
        production.add_command(label="Array copies…", command=self.create_array)
        production.add_command(label="Offset outline…", command=self.create_offset)
        production.add_command(label="Weld selection", command=self.weld_selection)
        production.add_separator()
        self.optimise_order = tk.BooleanVar(value=self.document.optimise_order)
        production.add_checkbutton(label="Shorten travel within each layer",
                                   variable=self.optimise_order, command=self.set_cut_order)
        production.add_command(label="Align left edges", command=lambda: self.align_selection("left"))
        production.add_command(label="Align horizontal centers", command=lambda: self.align_selection("center_x"))
        production.add_command(label="Align bottom edges", command=lambda: self.align_selection("bottom"))
        production.add_command(label="Align vertical centers", command=lambda: self.align_selection("center_y"))
        ttk.Menubutton(view_bar, text="Arrange", menu=production).pack(side="left", padx=3)
        action_bar = ttk.LabelFrame(dock, text="Laser", padding=8)
        action_bar.pack(side="bottom", fill="x", pady=(6, 0))
        action_bar.columnconfigure((0, 1), weight=1)
        self.send_button = ttk.Button(action_bar, text="Start job", style="Accent.TButton", command=self.send_job)
        self.send_button.grid(row=0, column=0, sticky="ew", padx=2, pady=3)
        ttk.Button(action_bar, text="STOP / RESET", style="Stop.TButton", command=self.controller.stop).grid(row=0, column=1, sticky="ew", padx=2, pady=3)
        self.pause_button = ttk.Button(action_bar, text="Pause", command=lambda: self.act(self.controller.pause_job))
        self.pause_button.grid(row=1, column=0, sticky="ew", padx=2, pady=3)
        self.resume_button = ttk.Button(action_bar, text="Resume", command=lambda: self.act(self.controller.resume_job))
        self.resume_button.grid(row=1, column=1, sticky="ew", padx=2, pady=3)
        self.frame_button = ttk.Button(action_bar, text="Frame · laser off", command=self.frame)
        self.frame_button.grid(row=2, column=0, sticky="ew", padx=2, pady=3)
        self.preview_button = ttk.Button(action_bar, text="Preview", command=self.open_preview)
        self.preview_button.grid(row=2, column=1, sticky="ew", padx=2, pady=3)
        ttk.Label(action_bar, text="Frame mm/min").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(action_bar, textvariable=self.frame_speed, values=tuple(map(str, JOG_FEEDS)), state="readonly", width=9).grid(row=3, column=1, sticky="ew")
        ttk.Button(action_bar, text="Create material test…", command=self.open_burn_test).grid(row=4, column=0, columnspan=2, sticky="ew", pady=4)
        library_bar = ttk.Frame(dock)
        library_bar.pack(side="bottom", fill="x", pady=6)
        ttk.Label(library_bar, text="Materials").pack(anchor="w")
        self.material_combo = ttk.Combobox(library_bar, textvariable=self.material_name, state="readonly", width=12)
        self.material_combo.pack(side="left")
        self.material_apply = ttk.Button(library_bar, text="Apply", command=self.apply_material)
        self.material_apply.pack(side="left", padx=5)
        ttk.Button(library_bar, text="Save current…", command=self.save_material).pack(side="left")
        self.material_delete = ttk.Button(library_bar, text="Delete", command=self.delete_material)
        self.material_delete.pack(side="left", padx=5)
        self.refresh_materials()
        self.workspace_content = content
        content.pack(fill="both", expand=True)
        self.bed = tk.Canvas(content, background="#eef3f6", highlightthickness=1, highlightbackground="#aebdc4")
        self.bed.grid(row=0, column=1, sticky="nsew", padx=(4, 8))
        self.bed.bind("<Configure>", lambda e: self.draw())
        self.bed.bind("<ButtonPress-1>", self.press)
        self.bed.bind("<B1-Motion>", self.drag)
        self.bed.bind("<ButtonRelease-1>", self.release)
        self.bed.bind("<MouseWheel>", self.mousewheel)
        self.bed.bind("<ButtonPress-2>", self.pan_press)
        self.bed.bind("<B2-Motion>", self.pan_drag)
        self.bed.bind("<ButtonRelease-2>", self.pan_release)
        self.inspector = ttk.Notebook(dock, width=310)
        self.inspector.pack(fill="both", expand=True)
        object_panel = ttk.Frame(self.inspector, width=290)
        self.inspector.add(object_panel, text="Objects")
        object_footer = ttk.Frame(object_panel)
        object_footer.pack(side="bottom", fill="x")
        object_body = ttk.Frame(object_panel)
        object_body.pack(fill="both", expand=True)
        self.object_scroll = tk.Canvas(object_body, width=285, highlightthickness=0, background="#25272b")
        object_bar = ttk.Scrollbar(object_body, orient="vertical", command=self.object_scroll.yview)
        object_bar.pack(side="right", fill="y")
        self.object_scroll.pack(side="left", fill="both", expand=True)
        self.object_scroll.configure(yscrollcommand=object_bar.set)
        side = ttk.Frame(self.object_scroll, padding=4)
        object_item = self.object_scroll.create_window((0,0), window=side, anchor="nw")
        side.bind("<Configure>", lambda e: self.object_scroll.configure(scrollregion=self.object_scroll.bbox("all")))
        self.object_scroll.bind("<Configure>", lambda e: self.object_scroll.itemconfigure(object_item, width=e.width))
        self.layer_panel = ttk.Frame(self.inspector)
        self.inspector.add(self.layer_panel, text="Cut layers")
        self.move_panel = ttk.Frame(self.inspector, padding=8)
        self.inspector.add(self.move_panel, text="Move")
        move_canvas = tk.Canvas(self.move_panel, highlightthickness=0, width=285, background="#25272b")
        move_scroll = ttk.Scrollbar(self.move_panel, orient="vertical", command=move_canvas.yview)
        move_scroll.pack(side="right", fill="y")
        move_canvas.pack(side="left", fill="both", expand=True)
        move_canvas.configure(yscrollcommand=move_scroll.set)
        move_body = ttk.Frame(move_canvas)
        move_item = move_canvas.create_window((0, 0), window=move_body, anchor="nw")
        move_body.bind("<Configure>", lambda e: move_canvas.configure(scrollregion=move_canvas.bbox("all")))
        move_canvas.bind("<Configure>", lambda e: move_canvas.itemconfigure(move_item, width=e.width))
        self.move_position = tk.StringVar(value="Home required")
        ttk.Label(move_body, textvariable=self.move_position, style="Section.TLabel").pack(anchor="w", pady=6)
        ttk.Button(move_body, text="Home machine", command=lambda: self.act(self.controller.home)).pack(fill="x", pady=3)
        if not self.controller.auto_home:
            ttk.Button(move_body, text="Confirm bottom-left",
                       command=lambda: self.act(self.controller.confirm_home)).pack(fill="x", pady=3)
        self.dock_step = tk.StringVar(value="1")
        self.dock_feed = tk.StringVar(value="3000")
        for label, variable, values in (("Step · mm", self.dock_step, ("0.1", "1", "5", "10")), ("Speed · mm/min", self.dock_feed, tuple(map(str, JOG_FEEDS)))):
            row = ttk.Frame(move_body); row.pack(fill="x", pady=4)
            ttk.Label(row, text=label).pack(side="left")
            ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=9).pack(side="right")
        pad = ttk.Frame(move_body); pad.pack(pady=8)
        for label, axis, direction, row, column in (("Y+", "Y", 1, 0, 1), ("X−", "X", -1, 1, 0), ("X+", "X", 1, 1, 2), ("Y−", "Y", -1, 2, 1)):
            ttk.Button(pad, text=label, width=5, command=lambda a=axis,d=direction: self.act(lambda: self.controller.jog(a, d, float(self.dock_step.get()), int(self.dock_feed.get())))).grid(row=row,column=column,padx=2,pady=2)
        ttk.Label(move_body, text="Jog follows the positioning mark. Laser off.\nUse Machine & Jog for keyboard / mouse targets.", wraplength=290, style="Quiet.TLabel").pack(anchor="w")
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
        labels = {"text": "Text", "x": "Object origin X · mm", "y": "Object origin Y · mm", "width": "Width · mm", "height": "Height · mm", "rotation": "Rotation · degrees",
                  "speed": "Cut speed · mm/min", "power": "Power · 0–1000", "passes": "Number of passes"}
        self.field_entries = {}
        for name, label in labels.items():
            row = ttk.Frame(side)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=17).pack(side="left")
            if name == "text":
                # Text can span lines, so it needs a box rather than one row.
                entry = MultiLineField(row).pack(side="right")
                self.fields[name] = entry
            else:
                entry = ttk.Entry(row, textvariable=self.fields[name], width=12)
                entry.pack(side="right")
            self.field_entries[name] = entry
        font_row = ttk.Frame(side)
        font_row.pack(fill="x", pady=1)
        ttk.Label(font_row, text="Text font", width=17).pack(side="left")
        self.font_combo = ttk.Combobox(font_row, textvariable=self.font_family, values=("Arial", "Segoe UI", "Consolas"), state="readonly", width=12)
        self.font_combo.pack(side="right")
        align_row = ttk.Frame(side)
        align_row.pack(fill="x", pady=1)
        ttk.Label(align_row, text="Text align", width=17).pack(side="left")
        self.text_align = tk.StringVar(value="left")
        ttk.Combobox(align_row, textvariable=self.text_align, values=ALIGNMENTS,
                     state="readonly", width=12).pack(side="right")
        for label, variable, default in (("Line spacing · em", "line_spacing", "1.2"),
                                         ("Letter spacing · em", "letter_spacing", "0.0")):
            row = ttk.Frame(side)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=17).pack(side="left")
            self.fields[variable] = tk.StringVar(value=default)
            ttk.Entry(row, textvariable=self.fields[variable], width=12).pack(side="right")
        mirror_row = ttk.Frame(side)
        mirror_row.pack(fill="x", pady=(2, 0))
        self.mirror_x_button = ttk.Checkbutton(mirror_row, text="Flip H", variable=self.mirror_x)
        self.mirror_x_button.pack(side="left")
        self.mirror_y_button = ttk.Checkbutton(mirror_row, text="Flip V", variable=self.mirror_y)
        self.mirror_y_button.pack(side="left", padx=8)
        ttk.Label(side, textvariable=self.bounds_text, style="Quiet.TLabel", wraplength=265,
                  justify="left").pack(anchor="w", pady=(4, 0))
        value_actions = ttk.Frame(object_footer)
        value_actions.pack(fill="x", pady=(6, 4))
        self.add_value_button = ttk.Button(value_actions, text="Add new", command=self.add_from_values)
        self.add_value_button.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.apply_value_button = ttk.Button(value_actions, text="Apply values", command=self.apply)
        self.apply_value_button.pack(side="left", fill="x", expand=True, padx=(2, 0))
        ttk.Label(side, text=("Frame keeps the laser off.\n"
                              f"X 0–{BED_X:g} · Y 0–{BED_Y:g} mm · S 0–1000"),
                  style="Quiet.TLabel", justify="left").pack(anchor="w")
        self.frame_status = tk.StringVar(value="Add geometry to enable Frame.")
        self.frame_status_label = ttk.Label(outer, textvariable=self.frame_status, style="Quiet.TLabel", wraplength=980)
        self.frame_status_label.pack(side="bottom", fill="x", pady=(3, 0))
        ttk.Label(outer, textvariable=self.message, wraplength=980).pack(side="bottom", fill="x", pady=(3, 0))
        content.pack_forget()
        content.pack(fill="both", expand=True)
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
        from .layer_ui import LayerWindow
        self.layer_window = LayerWindow(self, parent=self.layer_panel)
        self.refresh_frame_controls()
        self.window.after(10000, self.autosave_tick)
        from .dropfiles import enable as enable_drops
        self.drops_enabled = enable_drops(self.window, self.accept_dropped)
        self.window.after(1200, self.offer_recovery)

    def dirty(self):
        return self.document.to_payload() != self.saved_payload

    def update_document_status(self):
        name = self.design_path.name if self.design_path else "Untitled"
        if len(name)>20: name=name[:17]+"…"
        self.document_status.set(name + (" · unsaved changes" if self.dirty() else " · saved"))

    def autosave_tick(self):
        if not self.window.winfo_exists():
            return
        try:
            if self.dirty():
                self.project_store.autosave(self.document.to_payload(), self.design_path)
        except (OSError, ValueError) as exc:
            self.message.set(f"Autosave failed: {exc}. Save your design manually.")
        self.update_document_status()
        self.window.after(10000, self.autosave_tick)

    def confirm_discard(self):
        if not self.dirty():
            return True
        answer = messagebox.askyesnocancel("Unsaved design", "Save your changes before continuing?", parent=self.window)
        if answer is None:
            return False
        if answer:
            self.save_design()
            return not self.dirty()
        return True

    def offer_recovery(self):
        if self.project_store.candidates() and not self.document.shapes:
            if messagebox.askyesno("Recover design", "An autosaved design is available. Recover it now?", parent=self.window):
                self.recover_design()

    def recover_design(self):
        candidates = self.project_store.candidates()
        if not candidates:
            self.message.set("No recovery files available.")
            return
        if not self.confirm_discard():
            return
        try:
            recovery = json.loads(candidates[0].read_text(encoding="utf-8"))
            document = Document.from_payload(recovery["document"])
            self.checkpoint()
            self.document = document
            self.design_path = Path(recovery["path"]) if recovery.get("path") else None
            self.project_store.adopted = candidates[0]
            self.saved_payload = None
            self.set_selection(())
            self.refresh("Recovered autosave. Save the design to keep it.")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.message.set(f"Could not recover design: {exc}")

    def save_as(self):
        previous = self.design_path
        self.design_path = None
        self.save_design()
        if self.design_path is None:
            self.design_path = previous
        self.update_document_status()

    def open_recent(self):
        paths = self.project_store.recent()
        if not paths:
            self.message.set("No recent designs yet.")
            return
        window = tk.Toplevel(self.window)
        window.title("Recent designs")
        choices = tk.Listbox(window, width=90, height=min(10, len(paths)), exportselection=False)
        choices.pack(fill="both", expand=True, padx=12, pady=12)
        for path in paths:
            choices.insert("end", path)
        def open_choice():
            if choices.curselection():
                path = paths[choices.curselection()[0]]
                window.destroy()
                self.open_design(path)
        ttk.Button(window, text="Open selected", command=open_choice).pack(pady=(0,12))
        choices.bind("<Double-Button-1>", lambda event: open_choice())

    def import_svg(self):
        self.import_vectors("SVG", (("SVG vectors", "*.svg"),))

    def import_dxf(self):
        self.import_vectors("DXF", (("DXF drawings", "*.dxf"),))

    def accept_dropped(self, paths):
        """Open what was dropped on the window, by what it is.

        One file is what an operator means; the rest are ignored rather than
        opened at once, because opening four designs would discard three.
        """
        from .dropfiles import classify
        for kind, path in classify(paths):
            if kind == "image":
                ImageImportWindow(self, Path(path))
            elif kind in ("svg", "dxf"):
                self.import_vectors(kind.upper(), path=str(path))
            elif kind == "design":
                self.open_design(str(path))
            else:
                self.message.set(f"{Path(path).name} is not an image, drawing or design.")
                continue
            return
        self.window.focus_force()

    def import_image(self):
        path = filedialog.askopenfilename(
            parent=self.window, title="Import image",
            filetypes=(("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff"),))
        if path:
            ImageImportWindow(self, Path(path))

    def place_imported(self, shapes, description):
        """Shared tail of every importer: add, select, frame the view, report."""
        self.checkpoint()
        first = len(self.document.shapes)
        self.document.shapes.extend(shapes)
        self.set_selection(range(first, first + len(shapes)))
        self.fit_view()
        self.refresh(description)

    def import_vectors(self, kind, filetypes=(), path=None):
        """Both importers return bed-millimetre shapes, so the rest is shared.

        ``path`` skips the dialog, for a file that arrived by being dropped.
        """
        path = path or filedialog.askopenfilename(parent=self.window,
                                                  title=f"Import {kind}", filetypes=filetypes)
        if not path:
            return
        if kind == "SVG":
            from .svg_import import import_svg as read
            placed = "at their SVG size"
        else:
            from .dxf_import import import_dxf as read
            placed = "from the drawing's lower-left corner"
        def load():
            shapes = read(Path(path))
            self.checkpoint()
            first = len(self.document.shapes)
            self.document.shapes.extend(shapes)
            self.set_selection(range(first, first+len(shapes)))
            self.fit_view()
            self.refresh(f"Imported {len(shapes)} vector objects {placed}. Curves use 0.05 mm tolerance.")
        try:
            self.act(load)
        except OSError as exc:
            self.message.set(f"Could not read {kind}: {exc}")

    def create_array(self):
        from .production import array_copies
        if not self.selected_indices():
            self.message.set("Select objects to duplicate.")
            return
        columns = simpledialog.askinteger("Array", "Columns (including original):", initialvalue=3, minvalue=1, maxvalue=30, parent=self.window)
        if columns is None: return
        rows = simpledialog.askinteger("Array", "Rows (including original):", initialvalue=2, minvalue=1, maxvalue=30, parent=self.window)
        if rows is None: return
        gap = simpledialog.askfloat("Array", "Gap between copies in mm:", initialvalue=2, minvalue=0, maxvalue=100, parent=self.window)
        if gap is None: return
        def apply_array():
            copies = array_copies([self.document.shapes[i] for i in self.selected_indices()], columns, rows, gap, gap)
            self.checkpoint()
            start = len(self.document.shapes)
            self.document.shapes.extend(copies)
            self.set_selection(range(start, start+len(copies)))
            self.refresh(f"Added {len(copies)} array objects.")
        self.act(apply_array)

    def cutting_reach(self):
        """The X span the cutting beam can be placed at on this machine."""
        from .placement import reachable_x
        try:
            return reachable_x(self.controller.beam_offset_x)
        except ValueError:
            return None

    def set_cut_order(self):
        """Ordering changes the path, so it is an edit like any other."""
        self.checkpoint()
        self.document.optimise_order = self.optimise_order.get()
        self.invalidate_preview()
        saved = self.document.job_metrics()["rapid_distance"] if self.document.output_shapes() else 0
        self.refresh("Cut order shortened within each layer · travel now "
                     f"{saved:.0f} mm." if self.optimise_order.get() else
                     "Cut order follows the design list.")

    def weld_selection(self):
        from .production import weld_shapes

        def merge():
            indices = self.selected_indices()
            if len(indices) < 2:
                raise ValueError("Select two or more overlapping objects to weld.")
            welded = weld_shapes([self.document.shapes[index] for index in indices])
            self.checkpoint()
            for index in sorted(indices, reverse=True):
                self.document.delete(index)
            position = self.document.add(welded)
            self.set_selection((position,), position)
            self.refresh(f"Welded {len(indices)} objects into one outline.")
        self.act(merge)

    def create_offset(self):
        from .production import offset_shape
        indices = self.selected_indices()
        if not indices:
            self.message.set("Select closed outlines to offset.")
            return
        distance = simpledialog.askfloat("Offset outline", "Distance in mm (positive outward, negative inward):", initialvalue=1, parent=self.window)
        if distance is None: return
        def apply_offset():
            copies = [offset_shape(self.document.shapes[i], distance) for i in indices]
            self.checkpoint()
            start = len(self.document.shapes)
            self.document.shapes.extend(copies)
            self.set_selection(range(start, start+len(copies)))
            self.refresh("Added offset outlines. Originals remain in the design.")
        self.act(apply_offset)

    def align_selection(self, alignment):
        indices = self.selected_indices()
        if len(indices) < 2:
            self.message.set("Select at least two objects; the primary object is the alignment reference.")
            return
        def align():
            ref = shape_bounds(self.document.shapes[self.selected])
            position = lambda b: b[0] if alignment=="left" else b[1] if alignment=="bottom" else (b[0]+b[2])/2 if alignment=="center_x" else (b[1]+b[3])/2
            changes = []
            for index in indices:
                shape = self.document.shapes[index]
                delta = position(ref)-position(shape_bounds(shape))
                changes.append((index, replace(shape, x=shape.x+delta if alignment in ("left","center_x") else shape.x,
                                                y=shape.y+delta if alignment in ("bottom","center_y") else shape.y).validated()))
            self.checkpoint()
            for index, shape in changes: self.document.shapes[index] = shape
            self.refresh("Aligned selection to the primary object.")
        self.act(align)

    def values(self, kind=None):
        fallback = self.document.shapes[self.selected].kind if self.selected is not None else "rectangle"
        return Shape(kind or (self.tool.get() if self.tool.get() != "select" else fallback),
                     float(self.fields["x"].get()), float(self.fields["y"].get()),
                     float(self.fields["width"].get()), float(self.fields["height"].get()),
                     int(self.fields["speed"].get()), int(self.fields["power"].get()), int(self.fields["passes"].get()),
                     self.fields["text"].get(), self.font_family.get(),
                     rotation=float(self.fields["rotation"].get()),
                     line_spacing=float(self.fields["line_spacing"].get()),
                     letter_spacing=float(self.fields["letter_spacing"].get()),
                     text_align=self.text_align.get(),
                     mirror_x=self.mirror_x.get(), mirror_y=self.mirror_y.get(),
                     paths=self.document.shapes[self.selected].paths if self.selected is not None else (),
                     mode=self.document.shapes[self.selected].mode if self.selected is not None else "line",
                     interval=self.document.shapes[self.selected].interval if self.selected is not None else 0.2)

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
        # Cut order belongs here too: it changes the path the head takes, so it
        # is an edit, and Undo has to put it back like any other.
        return (tuple(self.document.shapes), str(self.design_path) if self.design_path else None,
                tuple(self.document.layers), self.document.optimise_order)

    def invalidate_preview(self):
        preview = self.preview_window
        self.preview_window = None
        if preview is not None:
            preview.close()

    def checkpoint(self):
        self.invalidate_preview()
        self.undo_stack.append(self.snapshot())
        self.undo_stack = self.undo_stack[-100:]
        self.redo_stack.clear()

    def restore(self, snapshot, message):
        self.invalidate_preview()
        shapes, path, layers, optimise_order = snapshot
        self.document.layers = list(layers)
        self.document.shapes = list(shapes)
        self.document.optimise_order = optimise_order
        self.design_path = Path(path) if path else None
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
        if not self.confirm_discard():
            return
        self.checkpoint()
        self.document.shapes = []
        self.document.layers = []
        self.set_selection(())
        self.design_path = None
        self.saved_payload = self.document.to_payload()
        self.project_store.clear()
        self.fit_view()
        self.refresh("New empty design.")

    def open_design(self, path=None):
        if not self.confirm_discard():
            return
        path = path or filedialog.askopenfilename(parent=self.window, title="Open Atomstack design",
                                          filetypes=(("Atomstack design", "*.atomdesign"), ("JSON files", "*.json")))
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            loaded = Document.from_payload(data)
            shapes = loaded.shapes
            self.checkpoint()
            self.document.shapes = shapes
            self.document.layers = loaded.layers
            self.set_selection((0,) if shapes else ())
            self.design_path = Path(path)
            self.saved_payload = self.document.to_payload()
            self.project_store.clear()
            self.project_store.remember(path)
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
            payload = self.document.to_payload()
            # Write beside the design and rename over it, the way the material
            # library does: an interrupted save must not truncate the old file.
            atomic_json(path, payload)
            self.design_path = path
            self.saved_payload = payload
            self.project_store.remember(path)
            self.project_store.clear()
            self.update_document_status()
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
            if self.keep_ratio.get() and len(self.selected_indices()) == 1:
                from .viewport import proportional_size
                current = self.document.shapes[self.selected]
                width, height = proportional_size(current.width, current.height, candidate.width, candidate.height)
                candidate = replace(candidate, width=width, height=height).validated()
            if any(self.document.shapes[i].layer for i in self.selected_indices()):
                current = self.document.shapes[self.selected]
                candidate = Shape(**{**candidate.__dict__, "speed": current.speed, "power": current.power, "passes": current.passes, "layer": current.layer})
                if len(self.selected_indices()) > 1:
                    raise ValueError("Use Cut layers to change shared process settings.")
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

    def open_layers(self):
        from .layer_ui import LayerWindow
        if self.layer_window is not None and self.layer_window.window.winfo_exists():
            self.inspector.select(self.layer_panel)
        else:
            self.layer_window = LayerWindow(self)

    def open_burn_test(self):
        BurnTestWindow(self)

    def open_placement(self):
        from .placement import ANCHORS, place_shapes
        window = tk.Toplevel(self.window)
        window.title("Place job")
        window.transient(self.window.winfo_toplevel())
        panel = ttk.Frame(window, padding=18); panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Place enabled output on the material", style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Moves all enabled objects together. Undo restores their position.\nCoordinates refer to the positioning mark; cutting includes beam alignment.", wraplength=430).pack(anchor="w", pady=8)
        anchor = tk.StringVar(value="Bottom-left")
        ttk.Combobox(panel, textvariable=anchor, values=tuple(ANCHORS), state="readonly").pack(fill="x", pady=4)
        x, y = tk.StringVar(value="20"), tk.StringVar(value="20")
        for label, value in (("Target X · mm", x), ("Target Y · mm", y)):
            row=ttk.Frame(panel); row.pack(fill="x", pady=4)
            ttk.Label(row,text=label).pack(side="left")
            ttk.Entry(row,textvariable=value,width=12).pack(side="right")
        feedback=tk.StringVar(value="Placement changes the design only; no machine movement.")
        ttk.Label(panel,textvariable=feedback,wraplength=430).pack(anchor="w",pady=8)
        def current():
            try:
                self.controller._guard(require_home=True, allow_status_poll=True)
                position=self.controller.app_position
                x.set(f"{position[0]:.3f}"); y.set(f"{position[1]:.3f}")
            except GuardError as exc: feedback.set(str(exc))
        def apply():
            try:
                output=self.document.output_shapes()
                originals=[self.document.shapes[i] for i,_ in output]
                placed=place_shapes(originals,anchor.get(),(float(x.get()),float(y.get())),self.controller.beam_offset_x)
                self.checkpoint()
                for (index,_),shape in zip(output,placed): self.document.shapes[index]=shape
                self.set_selection([i for i,_ in output],output[0][0])
                self.refresh("Job placed. Preview and Frame before cutting.")
                window.destroy()
            except (ValueError, GuardError) as exc: feedback.set(str(exc))
        ttk.Button(panel,text="Use current positioning mark",command=current).pack(fill="x",pady=4)
        ttk.Button(panel,text="Place job",command=apply).pack(fill="x",pady=4)
        return window

    def open_preview(self):
        def open_window():
            self.invalidate_preview()
            self.preview_window = JobPreviewWindow(self)
        self.act(open_window)

    def add_test_card(self, card, x, y, width, height, columns, rows,
                      min_speed, max_speed, min_power, max_power, gap, passes, interval):
        """Build a cut or engraving card and place it as one undoable change."""
        from .testcards import card_size, cut_card, engraving_card, fits_bed
        if not 1 <= columns <= 10 or not 1 <= rows <= 10:
            raise ValueError("Rows and columns must be between 1 and 10.")
        steps = lambda low, high, count: [round(low + (high-low)*i/max(1, count-1))
                                          for i in range(count)]
        speeds = steps(min_speed, max_speed, columns)
        powers = steps(min_power, max_power, rows)
        maker = cut_card if card == "cut" else engraving_card
        extra = {"passes": passes} if card == "cut" else {"interval": interval}
        shapes = maker(x, y, speeds, powers, cell_width=width, cell_height=height,
                       gap=gap, **extra)
        self.checkpoint()
        first = len(self.document.shapes)
        for shape in shapes:
            self.document.add(shape)
        self.set_selection(range(first, len(self.document.shapes)))
        span = card_size(speeds, powers, width, height, gap)
        cells = columns * rows
        note = ""
        if not fits_bed(x, y, speeds, powers, width, height, gap, self.cutting_reach()):
            low, high = self.cutting_reach() or (0, BED_X)
            note = (f" It reaches past what the beam can cut (X {low:g}-{high:g} mm); "
                    "move it in before sending.")
        self.refresh(f"Added a {card} test card: {cells} cells, "
                     f"{span[0]:.0f} x {span[1]:.0f} mm, labelled with its speeds and powers." + note)

    def add_burn_test_values(self, x, y, width, height, columns, rows,
                             min_speed, max_speed, min_power, max_power, gap=2, passes=1, mode="line", interval=0.2):
        if not 1 <= columns <= 10 or not 1 <= rows <= 10:
            raise ValueError("Rows and columns must be between 1 and 10.")
        speeds = [round(min_speed + (max_speed-min_speed)*i/max(1, columns-1)) for i in range(columns)]
        powers = [round(min_power + (max_power-min_power)*i/max(1, rows-1)) for i in range(rows)]
        pending = Document()
        pending.add_burn_test(x, y, width, height, speeds, powers, gap, passes, mode, interval)
        self.checkpoint()
        start = len(self.document.shapes)
        self.document.shapes.extend(pending.shapes)
        indices = list(range(start, len(self.document.shapes)))
        self.set_selection(indices, indices[0])
        self.refresh(f"Added {len(indices)} burn-test cells. Labels show speed F and power S; Frame before sending.")

    def send_job(self):
        def send():
            if self.controller.origin is None:
                raise GuardError("Home and confirm bottom-left before sending a job.")
            code = self.controller.prepare_job(self.document)
            output = self.document.output_shapes()
            highest = max(shape.power for _, shape in output)
            if not messagebox.askokcancel("Send job to Atomstack",
                    f"This will fire the laser and run {len(output)} geometry item(s).\n\nMaximum power: S{highest}\nBeam offset X: {self.controller.beam_offset_x:+g} mm (cut minus mark)\n\nConfirm the material is secured, ventilation is on, and you are watching the machine.",
                    parent=self.window):
                return
            self.controller.run_job(code.splitlines())
            self.message.set("Job started. Keep watching the machine; STOP / RESET remains available in the main window.")
        self.act(send)

    def frame(self):
        def start():
            self.controller.prepare_job(self.document)  # Check compensated cut travel before framing.
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
        position = self.controller.app_position
        self.move_position.set(f"X {position[0]:.3f}   Y {position[1]:.3f}" if position else "Home required")
        signature=(position,self.controller.beam_offset_x)
        if signature != getattr(self,"beam_display_signature",None) and not self.interaction and not self.drag_start:
            self.beam_display_signature=signature
            self.draw()
        has_output = bool(self.document.output_shapes())
        offbed = self.document.offbed(self.cutting_reach()) if has_output else ()
        if not has_output:
            ready, reason = False, "Enable layer output or add geometry to enable Frame."
        elif offbed:
            # Off-bed geometry is allowed to sit in the design; it just cannot go
            # to the machine, so say which object and why rather than greying out.
            names = ", ".join(str(index + 1) for index, _ in offbed[:4])
            ready = False
            reason = (f"Object {names} {'is' if len(offbed) == 1 else 'are'} outside the bed. "
                      "Scale or move it in to enable Frame and Send.")
        else:
            ready, reason = self.controller.frame_readiness()
        self.frame_button.configure(state="normal" if ready else "disabled")
        self.preview_button.configure(state="normal" if has_output else "disabled")
        job_running = self.controller.phase.startswith("job")
        self.send_button.configure(state="normal" if ready and has_output else "disabled")
        self.pause_button.configure(state="normal" if job_running and not self.controller.job_paused else "disabled")
        self.resume_button.configure(state="normal" if job_running and self.controller.job_paused else "disabled")
        self.frame_status.set((self.controller.message if job_running else reason) + f" · Cut beam X {self.controller.beam_offset_x:+g} mm from mark")

    def refresh(self, message=None):
        if self.optimise_order.get() != self.document.optimise_order:
            self.optimise_order.set(self.document.optimise_order)
        self.listbox.delete(0, "end")
        for shape in self.document.shapes:
            layer = next((l for l in self.document.layers if l.name == shape.layer), None)
            prefix = f"[{layer.name}{' · off' if not layer.enabled else ''}] " if layer else ""
            self.listbox.insert("end", prefix + shape.label)
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
        self.update_document_status()
        if self.layer_window is not None and self.layer_window.window.winfo_exists():
            self.layer_window.refresh()

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
        assigned = any(self.document.shapes[i].layer for i in indices)
        for name in ("speed", "power", "passes"):
            self.field_entries[name].configure(state="disabled" if assigned else "normal")
        if assigned:
            self.selection_status.set("Layer settings · edit in Cut layers")
        self.update_bounds_text(indices)

    def update_bounds_text(self, indices=None):
        indices = tuple(indices if indices is not None else self.selected_indices())
        if not indices:
            self.bounds_text.set("Bounds · no selection")
            return
        bounds = [shape_bounds(self.document.shapes[index]) for index in indices]
        left = min(bound[0] for bound in bounds)
        bottom = min(bound[1] for bound in bounds)
        right = max(bound[2] for bound in bounds)
        top = max(bound[3] for bound in bounds)
        prefix = "Combined bounds" if len(indices) > 1 else "Transformed bounds"
        self.bounds_text.set(f"{prefix} · L {left:g} · B {bottom:g} · R {right:g} · T {top:g}")

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
        layer = next((l for l in self.document.layers if l.name == shape.layer), None)
        for name in self.fields:
            value = getattr(layer if layer and name in ("speed", "power", "passes") else shape, name)
            self.fields[name].set(f"{value:g}" if isinstance(value, (int, float)) else value)
        self.font_family.set(shape.font_family)
        self.text_align.set(shape.text_align)
        self.mirror_x.set(shape.mirror_x)
        self.mirror_y.set(shape.mirror_y)
        self.update_bounds_text()

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
        """Frame the bed, widening only far enough to show geometry beyond it.

        An import larger than the machine has to be visible before it can be
        scaled to fit, so the view shrinks to include it. Geometry inside the
        bed keeps the familiar 100% view.
        """
        self.pan_x = self.pan_y = 0.0
        zoom = 1.0
        if self.document.shapes:
            bounds = [shape_bounds(shape) for shape in self.document.shapes]
            left, bottom = min(0.0, *(b[0] for b in bounds)), min(0.0, *(b[1] for b in bounds))
            right, top = max(BED_X, *(b[2] for b in bounds)), max(BED_Y, *(b[3] for b in bounds))
            zoom = clamp_zoom(1.0, min(BED_X / (right - left), BED_Y / (top - bottom)), 0.5, 1.0)
        self.view_zoom = zoom
        self.zoom_text.set(f"{self.view_zoom * 100:.0f}%")
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
        return handle_hit(x, y, self.shape_handles(shape), self.transform().millimetres(9))

    def shape_handles(self, shape):
        """Handle positions in bed millimetres, following any rotation applied.

        The grip sits a constant distance from the edge on screen, so it stays
        reachable whatever the zoom.
        """
        return rotated_handles((shape.x, shape.y, shape.width, shape.height), shape.rotation,
                               shape.mirror_x, shape.mirror_y,
                               rotation_gap=self.transform().millimetres(22))

    def begin_design_change(self):
        if self.interaction and not self.interaction.get("changed"):
            self.invalidate_preview()
            self.undo_stack.append(self.interaction["snapshot"])
            self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
            self.interaction["changed"] = True

    def press(self, event):
        if self.tool.get() == "pan":
            self.pan_press(event)
            return
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
            mode = "rotate" if handle == ROTATE_HANDLE else "resize" if handle else "move"
            self.interaction = {"mode": mode, "handle": handle,
                                "start": self.to_bed(event), "shape": self.document.shapes[index],
                                "indices": indices, "shapes": originals,
                                "snapshot": self.snapshot(), "changed": False}
            self.draw()
            return
        self.drag_start = self.to_bed(event)

    def drag(self, event):
        if self.interaction and self.interaction.get("mode") == "pan":
            self.pan_drag(event)
            return
        if self.interaction and self.interaction.get("mode") in ("move", "resize", "rotate"):
            current = self.to_bed(event)
            original = self.interaction["shape"]
            if self.interaction["mode"] == "move":
                dx, dy = current[0] - self.interaction["start"][0], current[1] - self.interaction["start"][1]
                originals = tuple(self.interaction["shapes"].values())
                bounds = [shape_bounds(shape) for shape in originals]
                left, right = min(b[0] for b in bounds), max(b[2] for b in bounds)
                bottom, top = min(b[1] for b in bounds), max(b[3] for b in bounds)
                # Only clamp on an axis the selection actually fits, or an
                # oversized import could never be dragged back onto the bed.
                if right - left <= BED_X:
                    dx = max(-left, min(BED_X-right, dx))
                if top - bottom <= BED_Y:
                    dy = max(-bottom, min(BED_Y-top, dy))
                if self.snap_enabled.get():
                    dx = self.snap(original.x+dx)-original.x
                    dy = self.snap(original.y+dy)-original.y
                    if right - left <= BED_X:
                        dx = max(-left, min(BED_X-right, dx))
                    if top - bottom <= BED_Y:
                        dy = max(-bottom, min(BED_Y-top, dy))
                candidates = {index: {"x": shape.x+dx, "y": shape.y+dy}
                              for index, shape in self.interaction["shapes"].items()}
            elif self.interaction["mode"] == "rotate":
                # Snapping means exact angles here, not grid millimetres.
                angle = rotation_from_pointer(current, (original.x, original.y, original.width, original.height),
                                              original.mirror_y, step=15 if self.snap_enabled.get() else 0)
                candidates = {self.selected: {"rotation": angle}}
            else:
                # The opposite corner anchors the drag, so a rotated object
                # grows along its own axes rather than the bed's.
                box = (original.x, original.y, original.width, original.height)
                candidate = resize_from_handle(self.interaction["handle"],
                                               (self.snap(current[0]), self.snap(current[1])),
                                               box, original.rotation, original.mirror_x, original.mirror_y,
                                               minimum=0.0 if original.kind == "line" else 0.1,
                                               keep_ratio=self.keep_ratio.get())
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
        if self.interaction and self.interaction.get("mode") == "pan":
            self.pan_release(event)
            return
        if self.interaction and self.interaction.get("mode") in ("move", "resize", "rotate"):
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

    def draw_raster(self, shape, x0, y0, scale):
        """Show the picture on the bed, not just the box it occupies.

        Placing an engraving means seeing it. If the preview cannot be built for
        any reason the outline still draws, so the object never disappears.
        """
        try:
            from PIL import Image, ImageTk
            from .raster import decode
            width = max(1, int(shape.width * scale))
            height = max(1, int(shape.height * scale))
            if width * height > 4_000_000:
                return
            grey = decode(shape.image)
            picture = Image.fromarray((grey * 255).astype("uint8"), mode="L")
            picture = picture.resize((width, height), Image.BILINEAR)
            if shape.mirror_x:
                picture = picture.transpose(Image.FLIP_LEFT_RIGHT)
            if shape.mirror_y:
                picture = picture.transpose(Image.FLIP_TOP_BOTTOM)
            if shape.rotation % 360:
                picture = picture.rotate(shape.rotation, expand=True,
                                         resample=Image.BILINEAR, fillcolor=255)
            photo = ImageTk.PhotoImage(picture)
            self.raster_images.append(photo)
            centre = (shape.x + shape.width / 2, shape.y + shape.height / 2)
            self.bed.create_image(x0 + centre[0] * scale, y0 - centre[1] * scale,
                                  image=photo, tags=("raster-preview",))
        except Exception:
            pass

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
        for value in range(0, int(BED_X)+1, 20):
            px = x0 + value*scale
            if 20 < px < self.bed.winfo_width()-15:
                self.bed.create_text(px, max(10, y1-12), text=str(value), fill="#405864", font=("Segoe UI", 8))
        for value in range(0, int(BED_Y)+1, 20):
            py = y0 - value*scale
            if 15 < py < self.bed.winfo_height()-15:
                self.bed.create_text(max(18, x0-8), py, text=str(value), anchor="e", fill="#405864", font=("Segoe UI", 8))
        self.bed.create_text(x0, y0 + 13, text="0, 0", anchor="w", fill="#405864", font=("Segoe UI", 9))
        self.bed.create_text(x1, y0 + 13, text="mm", anchor="e", fill="#405864", font=("Segoe UI", 9))
        from .placement import reachable_x
        low, high = reachable_x(self.controller.beam_offset_x)
        for left, right in ((0, low), (high, BED_X)):
            if right > left:
                self.bed.create_rectangle(x0+left*scale,y1,x0+right*scale,y0,fill="#ffe4d6",stipple="gray50",outline="#c87948",tags=("unreachable",))
        if high < BED_X or low > 0:
            self.bed.create_text(x0+8,y1+12,anchor="w",text=f"Cutting reach X {low:g}–{high:g} mm · shaded strip unreachable",fill="#94431f",tags=("reach-label",))
        position=self.controller.app_position
        if position:
            for px, color, label in ((position[0], "#1464d2", "Mark"), (position[0]+self.controller.beam_offset_x, "#c04422", "Cut")):
                cx,cy=x0+px*scale,y0-position[1]*scale
                self.bed.create_oval(cx-4,cy-4,cx+4,cy+4,outline=color,width=2,tags=("beam-position",))
                self.bed.create_text(cx,cy-12,text=label,fill=color,tags=("beam-position",))
        selected_indices = set(self.selected_indices())
        self.raster_images = []          # Tk drops an image it holds no reference to.
        for index, shape in enumerate(self.document.shapes):
            if shape.kind == "raster":
                self.draw_raster(shape, x0, y0, scale)
            layer = next((l for l in self.document.layers if l.name == shape.layer), None)
            color, width = ("#155eef", 3) if index in selected_indices else ("#aab3bf" if layer and not layer.enabled else "#405864", 2)
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
                handles = self.shape_handles(shape)
                grip = handles.pop(ROTATE_HANDLE, None)
                if grip:
                    # A stalk from the middle of the top edge, wherever rotation
                    # and mirroring have put that edge.
                    edge = transformed_point((shape.x, shape.y, shape.width, shape.height),
                                             shape.rotation, shape.mirror_x, shape.mirror_y,
                                             (0, shape.height/2))
                    gx, gy = x0+grip[0]*scale, y0-grip[1]*scale
                    self.bed.create_line(x0+edge[0]*scale, y0-edge[1]*scale, gx, gy,
                                         fill="#155eef", width=1, dash=(3, 2))
                    self.bed.create_oval(gx-5, gy-5, gx+5, gy+5, fill="white", outline="#155eef", width=2,
                                         tags=("rotate-handle",))
                for hx, hy in ((x0+point[0]*scale, y0-point[1]*scale) for point in handles.values()):
                    self.bed.create_rectangle(hx-5, hy-5, hx+5, hy+5, fill="white", outline="#155eef", width=2,
                                              tags=("resize-handle",))
        if len(selected_indices) > 1:
            shapes = [self.document.shapes[index] for index in selected_indices]
            bounds = [shape_bounds(shape) for shape in shapes]
            left = x0+min(bound[0] for bound in bounds)*scale
            right = x0+max(bound[2] for bound in bounds)*scale
            bottom = y0-min(bound[1] for bound in bounds)*scale
            top = y0-max(bound[3] for bound in bounds)*scale
            self.bed.create_rectangle(left, top, right, bottom, outline="#155eef", width=2, dash=(6, 3))
        if self.selected is not None and self.interaction and self.interaction.get("mode") == "move":
            primary = shape_bounds(self.document.shapes[self.selected])
            tolerance = 2/scale
            for index, shape in enumerate(self.document.shapes):
                if index in selected_indices: continue
                bounds = shape_bounds(shape)
                for a in (primary[0], (primary[0]+primary[2])/2, primary[2]):
                    if any(abs(a-b)<tolerance for b in (bounds[0], (bounds[0]+bounds[2])/2, bounds[2])):
                        self.bed.create_line(x0+a*scale, y1, x0+a*scale, y0, fill="#b34fb8", dash=(3,3), tags=("alignment-guide",))
                for a in (primary[1], (primary[1]+primary[3])/2, primary[3]):
                    if any(abs(a-b)<tolerance for b in (bounds[1], (bounds[1]+bounds[3])/2, bounds[3])):
                        self.bed.create_line(x0, y0-a*scale, x1, y0-a*scale, fill="#b34fb8", dash=(3,3), tags=("alignment-guide",))
        if self.document.output_shapes():
            try:
                points = self.document.frame_points()
            except ValueError:
                points = ()  # Off the bed: there is no outline to trace yet.
            coords = []
            for x, y in points:
                coords.extend((x0 + x * scale, y0 - y * scale))
            if coords:
                self.bed.create_line(*coords, fill="#d66a1f", width=2, dash=(7, 4))


class ImageImportWindow:
    """Choose how a picture becomes outlines, and see the count before adding.

    The settings are the ones that change what gets cut: how the light and dark
    are separated, how much detail survives, and how big the result is. Tracing
    runs on demand rather than on every keystroke, because a large photograph
    takes long enough that doing it per character would feel broken.
    """

    def __init__(self, editor, path):
        self.editor = editor
        self.path = path
        self.shapes = ()
        self.window = tk.Toplevel(editor.window)
        self.window.title(f"Import image · {path.name}")
        self.window.transient(editor.window)
        outer = ttk.Frame(self.window, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Import image as outlines", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text=path.name, style="Quiet.TLabel").pack(anchor="w", pady=(0, 10))

        self.mode = tk.StringVar(value="outline")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Trace", width=20).pack(side="left")
        ttk.Radiobutton(row, text="Light and dark", variable=self.mode,
                        value="outline").pack(side="left")
        ttk.Radiobutton(row, text="Edges", variable=self.mode,
                        value="edges").pack(side="left", padx=8)
        ttk.Radiobutton(row, text="Engrave", variable=self.mode,
                        value="engrave").pack(side="left", padx=8)

        self.fields = {}
        for label, name, value in (("Width · mm", "width", "100"),
                                   ("Engrave lines · mm", "interval", "0.2"),
                                   ("Threshold · blank = auto", "level", ""),
                                   ("Blur · pixels", "blur", "0"),
                                   ("Brightness · -1 to 1", "brightness", "0"),
                                   ("Contrast · 0 to 10", "contrast", "1"),
                                   ("Simplify · mm", "tolerance", "0.1"),
                                   ("Ignore below · mm2", "min_area", "1")):
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=20).pack(side="left")
            self.fields[name] = tk.StringVar(value=value)
            ttk.Entry(row, textvariable=self.fields[name], width=10).pack(side="right")
        self.invert = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, text="Invert (cut the light areas instead)",
                        variable=self.invert).pack(anchor="w", pady=(4, 0))

        self.preview = tk.Canvas(outer, width=300, height=200, highlightthickness=1,
                                 highlightbackground="#aebdc4", background="#eef3f6")
        self.preview.pack(pady=(10, 4))
        self.preview_image = None      # Tk drops an image nothing holds.
        self.status = tk.StringVar(value="Showing the picture. Trace to see what will be cut.")
        ttk.Label(outer, textvariable=self.status, style="Quiet.TLabel",
                  wraplength=320, justify="left").pack(anchor="w", pady=(10, 6))
        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        ttk.Button(actions, text="Trace", command=self.trace).pack(side="left")
        self.add_button = ttk.Button(actions, text="Add to design", command=self.add,
                                     state="disabled")
        self.add_button.pack(side="left", padx=6)
        ttk.Button(actions, text="Cancel", command=self.window.destroy).pack(side="right")
        self.mode.trace_add("write", lambda *_: self.show_source())
        self.show_source()

    def draw_preview(self, picture):
        """Fit a greyscale preview into the pane."""
        from PIL import Image, ImageTk
        width, height = int(self.preview["width"]), int(self.preview["height"])
        fitted = picture.copy()
        fitted.thumbnail((width - 4, height - 4), Image.LANCZOS)
        self.preview_image = ImageTk.PhotoImage(fitted)
        self.preview.delete("all")
        self.preview.create_image(width // 2, height // 2, image=self.preview_image)

    def show_source(self):
        """What the file looks like, before any of the settings are applied."""
        try:
            from PIL import Image, ImageOps
            with Image.open(self.path) as opened:
                self.draw_preview(ImageOps.exif_transpose(opened).convert("L"))
        except Exception as exc:
            self.preview.delete("all")
            self.preview.create_text(150, 100, text=str(exc), width=280)

    def show_result(self):
        """What the machine would actually do with the current settings."""
        from PIL import Image, ImageDraw
        from .raster import decode
        shape = self.shapes[0]
        scale = 300 / max(shape.width, 1e-6)
        canvas = Image.new("L", (300, max(1, int(shape.height * scale))), 255)
        if shape.kind == "raster":
            grid = decode(shape.image)
            canvas = Image.fromarray((grid * 255).astype("uint8"), mode="L")
        else:
            pen = ImageDraw.Draw(canvas)
            for path in shape.paths:
                points = [(x * shape.width * scale,
                           (1 - y) * shape.height * scale) for x, y in path]
                if len(points) > 1:
                    pen.line(points, fill=0, width=1)
        self.draw_preview(canvas)

    def values(self):
        numbers = {}
        for name, variable in self.fields.items():
            text = variable.get().strip()
            if name == "level" and not text:
                numbers[name] = None
                continue
            numbers[name] = float(text)
        return numbers

    def trace(self):
        from .imaging import engraving_shape, image_shapes
        try:
            values = self.values()
            if self.mode.get() == "engrave":
                shape = engraving_shape(
                    self.path, width_mm=values["width"], interval=values["interval"],
                    brightness=values["brightness"], contrast=values["contrast"],
                    invert=self.invert.get(),
                    speed=int(self.editor.fields["speed"].get()),
                    power=int(self.editor.fields["power"].get()))
                self.shapes = [shape]
                from .raster import decode, engraving_metrics, quantise, scan_runs, RASTER_LEVELS
                grid = decode(shape.image)
                rows = scan_runs(quantise(grid, RASTER_LEVELS, 0, shape.power),
                                 (shape.x, shape.y, shape.width, shape.height), shape.interval)
                metrics = engraving_metrics(rows, shape.speed)
                self.status.set(
                    f"{metrics['rows']} lines · {metrics['runs']} moves · "
                    f"{shape.width:.0f} x {shape.height:.0f} mm · sweeps "
                    f"{metrics['burn_distance']/1000:.1f} m, about "
                    f"{metrics['seconds']/60:.0f} min of engraving.")
                self.show_result()
                self.add_button.configure(state="normal")
                return
            self.shapes = image_shapes(
                self.path, width_mm=values["width"], mode=self.mode.get(),
                level=values["level"], blur_radius=values["blur"],
                brightness=values["brightness"], contrast=values["contrast"],
                invert=self.invert.get(), tolerance_mm=values["tolerance"],
                min_area_mm=values["min_area"])
            outlines = sum(len(shape.paths) for shape in self.shapes)
            points = sum(len(path) for shape in self.shapes for path in shape.paths)
            left, bottom, right, top = shape_bounds(self.shapes[0])
            self.status.set(f"{outlines} outlines · {points} points · "
                            f"{right-left:.1f} x {top-bottom:.1f} mm. "
                            "Add it, or change the settings and trace again.")
            self.show_result()
            self.add_button.configure(state="normal")
        except (ValueError, OSError) as exc:
            self.shapes = ()
            self.add_button.configure(state="disabled")
            self.status.set(str(exc))

    def add(self):
        if not self.shapes:
            return
        if self.shapes[0].kind == "raster":
            self.editor.place_imported(
                self.shapes, f"Placed an engraving of {self.path.name}.")
            self.window.destroy()
            return
        outlines = sum(len(shape.paths) for shape in self.shapes)
        self.editor.place_imported(
            self.shapes, f"Imported {outlines} outlines from {self.path.name}.")
        self.window.destroy()


class JobPreviewWindow:
    """Read-only visualization of the exact geometry order sent to the controller."""
    def __init__(self, editor):
        self.editor = editor
        # Rapids run at the machine's own rate, not at any speed the app chose.
        rapid = editor.controller.max_xy_feed or None
        self.segments = editor.document.preview_segments(rapid_feed=rapid)
        self.metrics = editor.document.job_metrics(rapid_feed=rapid)
        self.window = tk.Toplevel(editor.window)
        self.window.title("Job preview · no machine movement")
        self.window.geometry("1050x780")
        self.window.minsize(800, 600)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.progress = tk.DoubleVar(value=100)
        self.playing = False
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Job preview", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="READ ONLY · NO MACHINE COMMANDS", style="Quiet.TLabel").pack(side="right")
        metrics = self.metrics
        summary = (f"{len(editor.document.output_shapes())} output objects   ·   {metrics['segments']} moves   ·   "
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

    def close(self):
        self.playing = False
        if self.editor.preview_window is self:
            self.editor.preview_window = None
        if self.window.winfo_exists():
            self.window.destroy()

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
            "min_speed":"1000", "max_speed":"6000", "min_power":"100", "max_power":"500",
            "gap":"2", "passes":"1", "interval":"0.2"}.items()}
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Burn-test grid", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        labels = (("x", "Left X · mm"), ("y", "Bottom Y · mm"), ("width", "Cell width · mm"),
                  ("height", "Cell height · mm"), ("columns", "Speed columns"), ("rows", "Power rows"),
                  ("min_speed", "Minimum speed"), ("max_speed", "Maximum speed"),
                  ("min_power", "Minimum power"), ("max_power", "Maximum power"),
                  ("gap", "Cell gap · mm"), ("passes", "Passes per cell"), ("interval", "Fill spacing · mm"))
        for row, (name, label) in enumerate(labels, 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 18))
            ttk.Entry(frame, textvariable=self.values[name], width=12).grid(row=row, column=1, sticky="e")
        self.mode = tk.StringVar(value="line")
        ttk.Label(frame, text="Test mode").grid(row=14, column=0, sticky="w")
        ttk.Combobox(frame, textvariable=self.mode, values=("line", "fill"), state="readonly", width=10).grid(row=14, column=1)
        self.card = tk.StringVar(value="cut")
        ttk.Label(frame, text="Card type").grid(row=15, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(frame, textvariable=self.card, values=("cut", "engrave"),
                     state="readonly", width=10).grid(row=15, column=1, pady=(6, 0))
        self.message = tk.StringVar(value="Cells carry their own settings, and the speeds and powers are cut onto the card so it can still be read once it is off the machine.")
        ttk.Label(frame, textvariable=self.message, style="Quiet.TLabel", wraplength=300).grid(row=16, column=0, columnspan=2, sticky="w", pady=(10, 8))
        ttk.Button(frame, text="Add test card", command=self.add).grid(row=17, column=0, columnspan=2, sticky="ew")

    def add(self):
        try:
            number = lambda name: float(self.values[name].get())
            integer = lambda name: int(self.values[name].get())
            self.editor.add_test_card(
                self.card.get(), number("x"), number("y"), number("width"), number("height"),
                integer("columns"), integer("rows"), integer("min_speed"), integer("max_speed"),
                integer("min_power"), integer("max_power"), number("gap"), integer("passes"),
                number("interval"))
            self.window.destroy()
        except (ValueError, IndexError) as exc:
            self.message.set(str(exc))


def capture_import_previews(folder, image=None):
    """Photograph the import dialog in each mode, from the running app.

    Evidence has to come from the thing being claimed. The app cannot be driven
    from outside without sending input to whatever window happens to be in
    front, so it takes its own picture instead, the way this project already
    generates its preview artifacts.
    """
    import os
    import tempfile
    import time
    from PIL import Image, ImageDraw, ImageGrab

    folder.mkdir(parents=True, exist_ok=True)
    # Run against its own state directory. Otherwise this sees the operator's
    # autosaved designs, offers to recover one, and waits forever on a dialog
    # nobody is there to answer; it would also leave its own behind.
    state = tempfile.mkdtemp(prefix="atomstack-verify-")
    os.environ["ATOMSTACK_STATE_DIR"] = state
    source = Path(image) if image else folder / "verify-source.png"
    if not image:
        drawn = Image.new("L", (320, 240), 250)
        pen = ImageDraw.Draw(drawn)
        pen.ellipse((60, 40, 260, 200), fill=60)
        pen.ellipse((120, 90, 200, 150), fill=245)
        drawn.save(source)

    root = tk.Tk()
    app = App(root, demo=True)
    written = []

    def capture_each():
        for mode, fields in (("outline", {"width": "80", "blur": "2",
                                          "tolerance": "0.4", "min_area": "6"}),
                             ("edges", {"width": "80", "level": "0.12", "blur": "3",
                                        "tolerance": "0.3", "min_area": "4"}),
                             ("engrave", {"width": "80", "interval": "0.25"})):
            window = ImageImportWindow(app.geometry, source)
            window.mode.set(mode)
            for name, value in fields.items():
                window.fields[name].set(value)
            window.trace()
            window.window.lift()
            window.window.attributes("-topmost", True)
            root.update_idletasks()
            root.update()
            time.sleep(0.8)
            root.update()
            widget = window.window
            box = (widget.winfo_rootx(), widget.winfo_rooty(),
                   widget.winfo_rootx() + widget.winfo_width(),
                   widget.winfo_rooty() + widget.winfo_height())
            target = folder / f"import-{mode}.png"
            ImageGrab.grab(bbox=box).save(target)
            written.append((mode, str(target), window.status.get()))
            window.window.destroy()
            root.update()

    def cards():
        for card in ("cut", "engrave"):
            app.geometry.document.shapes.clear()
            app.geometry.add_test_card(card, 20, 20, 14, 14, 4, 4, 600, 6000, 150, 900,
                                       4, 1, 0.3)
            app.geometry.set_selection(())
            app.geometry.fit_view()
            # The main window has to be in front, or this photographs whatever
            # is: ImageGrab takes the screen, not the window's own pixels.
            root.lift()
            root.attributes("-topmost", True)
            root.update_idletasks()
            root.update()
            time.sleep(0.9)
            root.update()
            box = (root.winfo_rootx(), root.winfo_rooty(),
                   root.winfo_rootx() + root.winfo_width(),
                   root.winfo_rooty() + root.winfo_height())
            ImageGrab.grab(bbox=box).save(folder / f"card-{card}.png")
            written.append((f"{card} card", str(folder / f"card-{card}.png"),
                            app.geometry.message.get()))

    def everything():
        try:
            capture_each()
            cards()
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            root.quit()

    root.after(700, everything)
    root.mainloop()
    root.destroy()
    (folder / "import-previews.json").write_text(
        json.dumps([{"mode": m, "file": f, "status": s} for m, f, s in written], indent=2),
        encoding="utf-8")
    for mode, target, status in written:
        print(f"{mode}: {target}")
        print(f"  {status}")


def main():
    parser = argparse.ArgumentParser(description="Personal Atomstack USB controller")
    parser.add_argument("--demo", action="store_true", help="Open simulator; no hardware is accessed")
    parser.add_argument("--port", help="Connect the explicitly selected port on launch; e.g. COM3")
    parser.add_argument("--report", help="Write a live JSON diagnostic snapshot to this file")
    parser.add_argument("--open", metavar="FILE",
                        help="Open an image, drawing or design on start, as Explorer would")
    parser.add_argument("--verify-features", help=argparse.SUPPRESS)
    parser.add_argument("--verify-images", help=argparse.SUPPRESS)
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
        # Imaging pulls in Pillow and numpy, which the bundle has to carry.
        # A packaged app that cannot import them fails here rather than in
        # front of an operator with a photograph.
        from .imaging import engraving_shape, image_shapes
        picture_path = report.with_name("packaged-image-test.png")
        from PIL import Image, ImageDraw
        picture = Image.new("L", (240, 160), 255)
        ImageDraw.Draw(picture).ellipse((40, 30, 200, 130), fill=0)
        picture.save(picture_path)
        traced, = image_shapes(picture_path, width_mm=60, tolerance_mm=0.2)
        engraving = engraving_shape(picture_path, width_mm=60, interval=0.5,
                                    speed=3000, power=400)
        engraved = Document()
        engraved.add(engraving)
        raster_code = engraved.gcode((-288, -301))
        picture_path.unlink(missing_ok=True)
        report.write_text(json.dumps({"result":"PASS", "vector_lines":code.count("G53 G1"),
                                      "test_cells":4, "material_power":reloaded.power,
                                      "traced_outlines":len(traced.paths),
                                      "engraved_marks":raster_code.count(" S"),
                                      "engraving_mm":round(engraving.width, 1)}),
                          encoding="utf-8")
        return
    if args.verify_images:
        capture_import_previews(Path(args.verify_images), args.open)
        return
    if args.demo and args.port:
        parser.error("--demo and --port cannot be combined")
    root = tk.Tk()
    app = App(root, args.demo, args.port, args.report)
    if args.open:
        # Same route a dropped file takes, so "Open with" and a drop agree.
        root.after(400, lambda: app.geometry.accept_dropped([args.open]))
    root.mainloop()
