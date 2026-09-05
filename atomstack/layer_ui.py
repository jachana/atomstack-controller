"""Named process layers; changes go through the editor's history and preview gates."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
from .geometry import CutLayer


class LayerWindow:
    def __init__(self, editor, parent=None):
        self.editor = editor
        embedded = parent is not None
        if embedded:
            self.window = ttk.Frame(parent)
            self.window.pack(fill="both", expand=True)
            canvas = tk.Canvas(self.window, highlightthickness=0, width=285, background="#25272b")
            scroll = ttk.Scrollbar(self.window, orient="vertical", command=canvas.yview)
            scroll.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            canvas.configure(yscrollcommand=scroll.set)
            outer = ttk.Frame(canvas, padding=8)
            item = canvas.create_window((0,0), window=outer, anchor="nw")
            outer.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        else:
            self.window = tk.Toplevel(editor.window)
            self.window.title("Cut layers · execution order")
            self.window.geometry("800x660")
            self.window.minsize(740, 620)
            outer = ttk.Frame(self.window, padding=20)
            outer.pack(fill="both", expand=True)
        if not embedded:
            ttk.Label(outer, text="Cut layers", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Top to bottom · unassigned objects last" if embedded else "Run top to bottom. Unassigned objects run last with individual settings.",
                  style="Quiet.TLabel", wraplength=270 if embedded else 740).pack(anchor="w", pady=(4, 14))
        self.table = ttk.Treeview(outer, columns=("name", "speed", "power", "passes", "output", "objects"),
                                  show="headings", selectmode="browse", height=4 if embedded else 7)
        for key, label, width in (("name", "Layer", 210), ("speed", "mm/min", 95),
                                  ("power", "Power / 1000", 100), ("passes", "Passes", 60),
                                  ("output", "Output", 70), ("objects", "Objects", 65)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key == "name")
        if embedded:
            self.table.configure(displaycolumns=("name", "output"))
            self.table.column("name", width=160)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.load)
        self.fields = {key: tk.StringVar(value=value) for key, value in
                       (("name", "Engrave"), ("speed", "3000"), ("power", "200"), ("passes", "1"))}
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(14, 8))
        for key, label, width in (("name", "Name", 22), ("speed", "Speed · mm/min", 11),
                                  ("power", "Power · 0–1000", 12), ("passes", "Passes", 6)):
            group = ttk.Frame(row)
            group.pack(side="top" if embedded else "left", fill="x" if embedded else "none", padx=(0, 10))
            ttk.Label(group, text=label).pack(side="left" if embedded else "top", anchor="w")
            ttk.Entry(group, textvariable=self.fields[key], width=12 if embedded else width).pack(side="right" if embedded else "top")
        self.enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Output on", variable=self.enabled).pack(side="top" if embedded else "left", pady=(8, 0))
        process = ttk.Frame(outer)
        process.pack(fill="x", pady=4)
        self.mode = tk.StringVar(value="line")
        self.interval = tk.StringVar(value="0.2")
        ttk.Label(process, text="Mode").pack(side="left")
        ttk.Combobox(process, textvariable=self.mode, values=("line", "fill"), state="readonly", width=6).pack(side="left", padx=4)
        ttk.Label(process, text="Spacing mm").pack(side="left")
        ttk.Entry(process, textvariable=self.interval, width=5).pack(side="left", padx=4)
        ttk.Label(outer, text="Fill uses alternating scan lines; spacing controls density.",
                  wraplength=270 if embedded else 740, style="Quiet.TLabel").pack(anchor="w")
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=4)
        for n, (label, action) in enumerate((("Add layer", self.add), ("Save changes", self.save),
                               ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1)))):
            button = ttk.Button(actions, text=label, command=lambda f=action: self.act(f))
            if embedded:
                actions.columnconfigure(n%2, weight=1)
                button.grid(row=n//2, column=n%2, sticky="ew", padx=1, pady=1)
            else:
                button.pack(side="left", padx=(0,6))
        assignment = ttk.Frame(outer)
        assignment.pack(fill="x", pady=(8, 0))
        ttk.Button(assignment, text="Assign selected objects", command=lambda: self.act(self.assign)).pack(side="top" if embedded else "left", fill="x" if embedded else "none", padx=(0, 6))
        ttk.Button(assignment, text="Use individual settings", command=lambda: self.act(self.detach)).pack(side="top" if embedded else "left", fill="x" if embedded else "none")
        self.message = tk.StringVar(value="Select objects in the design, choose a layer here, then assign them.")
        ttk.Label(outer, textvariable=self.message, wraplength=270 if embedded else 740, style="Quiet.TLabel").pack(fill="x", pady=(12, 0))
        presets = ttk.Frame(outer)
        presets.pack(fill="x", pady=8)
        self.preset_name = tk.StringVar()
        self.preset_combo = ttk.Combobox(presets, textvariable=self.preset_name, state="readonly", values=editor.materials.names, width=20)
        self.preset_combo.pack(fill="x")
        ttk.Button(presets, text="Load material settings", command=lambda: self.act(self.load_material)).pack(fill="x", pady=3)
        self.refresh()

    def load_material(self):
        preset = self.editor.materials.get(self.preset_name.get())
        for key in ("speed", "power", "passes"):
            self.fields[key].set(str(getattr(preset, key)))
        self.message.set("Material settings loaded. Save changes to apply them to the layer.")

    def act(self, action):
        try:
            action()
        except (ValueError, IndexError) as exc:
            self.message.set(str(exc))

    def selected_name(self):
        selected = self.table.selection()
        if not selected:
            raise ValueError("Choose a layer first.")
        return self.table.item(selected[0], "values")[0]

    def index(self):
        name = self.selected_name()
        return next((i for i, layer in enumerate(self.editor.document.layers) if layer.name == name), -1)

    def candidate(self):
        return CutLayer(self.fields["name"].get().strip(), int(self.fields["speed"].get()),
                        int(self.fields["power"].get()), int(self.fields["passes"].get()), self.enabled.get(),
                        self.mode.get(), float(self.interval.get())).validated()

    def refresh(self):
        selected = self.table.selection()
        name = self.table.item(selected[0], "values")[0] if selected else None
        if hasattr(self, "preset_combo"):
            self.preset_combo.configure(values=self.editor.materials.names)
        self.table.delete(*self.table.get_children())
        for i, layer in enumerate(self.editor.document.layers):
            count = sum(s.layer == layer.name for s in self.editor.document.shapes)
            self.table.insert("", "end", iid=str(i), values=(layer.name, layer.speed, layer.power,
                                                            layer.passes, "On" if layer.enabled else "Off", count))
            if layer.name == name:
                self.table.selection_set(str(i))

    def load(self, event=None):
        i = self.index() if self.table.selection() else -1
        if i < 0:
            return
        layer = self.editor.document.layers[i]
        for key in self.fields:
            self.fields[key].set(str(getattr(layer, key)))
        self.enabled.set(layer.enabled)
        self.mode.set(layer.mode)
        self.interval.set(str(layer.interval))

    def changed(self, message):
        self.editor.refresh(message)
        self.refresh()
        self.message.set(message)

    def add(self):
        layer = self.candidate()
        if any(l.name == layer.name for l in self.editor.document.layers):
            raise ValueError("Choose a unique layer name.")
        self.editor.checkpoint()
        self.editor.document.layers.append(layer)
        self.changed(f"Added {layer.name}. Select objects and assign them to this layer.")
        self.table.selection_set(str(len(self.editor.document.layers)-1))

    def save(self):
        i = self.index()
        if i < 0:
            raise ValueError("Choose a current layer first.")
        layer = self.candidate()
        if any(j != i and l.name == layer.name for j, l in enumerate(self.editor.document.layers)):
            raise ValueError("Choose a unique layer name.")
        old = self.editor.document.layers[i].name
        for shape in self.editor.document.shapes:
            if shape.layer == old:
                replace(shape, mode=layer.mode, interval=layer.interval).validated()
        self.editor.checkpoint()
        self.editor.document.layers[i] = layer
        self.editor.document.shapes = [replace(s, layer=layer.name) if s.layer == old else s
                                       for s in self.editor.document.shapes]
        self.changed(f"Saved {layer.name}; output is {'on' if layer.enabled else 'off'}.")
        self.table.selection_set(str(i))

    def move(self, direction):
        i = self.index()
        j = i + direction
        if i < 0 or not 0 <= j < len(self.editor.document.layers):
            return
        self.editor.checkpoint()
        layers = self.editor.document.layers
        layers[i], layers[j] = layers[j], layers[i]
        self.changed("Updated execution order.")

    def assign(self):
        i = self.index()
        indices = self.editor.selected_indices()
        if i < 0 or not indices:
            raise ValueError("Select design objects and choose a layer first.")
        layer = self.editor.document.layers[i]
        name = layer.name
        for index in indices:
            replace(self.editor.document.shapes[index], mode=layer.mode, interval=layer.interval).validated()
        self.editor.checkpoint()
        for index in indices:
            self.editor.document.update(index, layer=name)
        self.changed(f"Assigned {len(indices)} objects to {name}.")

    def detach(self):
        indices = self.editor.selected_indices()
        if not indices:
            raise ValueError("Select design objects first.")
        # Keep their currently effective settings when removing the assignment.
        layers = {layer.name: layer for layer in self.editor.document.layers}
        self.editor.checkpoint()
        for index in indices:
            shape = self.editor.document.shapes[index]
            layer = layers.get(shape.layer)
            values = {key: getattr(layer, key) for key in ("speed", "power", "passes", "mode", "interval")} if layer else {}
            self.editor.document.update(index, layer="", **values)
        self.changed(f"{len(indices)} objects now use individual settings and output is on.")
