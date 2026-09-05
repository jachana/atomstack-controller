"""Named process layers; changes go through the editor's history and preview gates."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk
from .geometry import CutLayer


class LayerWindow:
    def __init__(self, editor):
        self.editor = editor
        self.window = tk.Toplevel(editor.window)
        self.window.title("Cut layers · execution order")
        self.window.geometry("760x530")
        self.window.minsize(700, 500)
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Cut layers", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Run top to bottom. Unassigned objects run last with individual settings.",
                  style="Quiet.TLabel").pack(anchor="w", pady=(4, 14))
        self.table = ttk.Treeview(outer, columns=("name", "speed", "power", "passes", "output", "objects"),
                                  show="headings", selectmode="browse", height=7)
        for key, label, width in (("name", "Layer", 210), ("speed", "mm/min", 95),
                                  ("power", "Power / 1000", 100), ("passes", "Passes", 60),
                                  ("output", "Output", 70), ("objects", "Objects", 65)):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key == "name")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.load)
        self.fields = {key: tk.StringVar(value=value) for key, value in
                       (("name", "Engrave"), ("speed", "3000"), ("power", "200"), ("passes", "1"))}
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(14, 8))
        for key, label, width in (("name", "Name", 22), ("speed", "Speed · mm/min", 11),
                                  ("power", "Power · 0–1000", 12), ("passes", "Passes", 6)):
            group = ttk.Frame(row)
            group.pack(side="left", padx=(0, 10))
            ttk.Label(group, text=label).pack(anchor="w")
            ttk.Entry(group, textvariable=self.fields[key], width=width).pack()
        self.enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Output on", variable=self.enabled).pack(side="left", pady=(18, 0))
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=4)
        for label, action in (("Add layer", self.add), ("Save changes", self.save),
                               ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1))):
            ttk.Button(actions, text=label, command=lambda f=action: self.act(f)).pack(side="left", padx=(0, 6))
        assignment = ttk.Frame(outer)
        assignment.pack(fill="x", pady=(8, 0))
        ttk.Button(assignment, text="Assign selected objects", command=lambda: self.act(self.assign)).pack(side="left", padx=(0, 6))
        ttk.Button(assignment, text="Use individual settings", command=lambda: self.act(self.detach)).pack(side="left")
        self.message = tk.StringVar(value="Select objects in the design, choose a layer here, then assign them.")
        ttk.Label(outer, textvariable=self.message, wraplength=700, style="Quiet.TLabel").pack(fill="x", pady=(12, 0))
        self.refresh()

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
                        int(self.fields["power"].get()), int(self.fields["passes"].get()), self.enabled.get()).validated()

    def refresh(self):
        selected = self.table.selection()
        name = self.table.item(selected[0], "values")[0] if selected else None
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
        name = self.editor.document.layers[i].name
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
            values = {key: getattr(layer, key) for key in ("speed", "power", "passes")} if layer else {}
            self.editor.document.update(index, layer="", **values)
        self.changed(f"{len(indices)} objects now use individual settings and output is on.")
