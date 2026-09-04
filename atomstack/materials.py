"""Small local material-preset store; no machine commands or recommendations."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class MaterialPreset:
    name: str
    speed: int
    power: int
    passes: int

    def validated(self):
        name = self.name.strip()
        if not name or len(name) > 80 or not all(character.isprintable() for character in name):
            raise ValueError("Material name must contain 1–80 characters.")
        if not 60 <= self.speed <= 20000:
            raise ValueError("Speed must be between 60 and 20,000 mm/min.")
        if not 0 <= self.power <= 1000:
            raise ValueError("Power must be between 0 and 1000.")
        if not 1 <= self.passes <= 20:
            raise ValueError("Passes must be between 1 and 20.")
        return MaterialPreset(name, self.speed, self.power, self.passes)


def default_material_path():
    base = Path(os.environ.get("APPDATA", Path.home())) / "AtomstackController"
    return base / "materials.json"


class MaterialLibrary:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_material_path()
        self._items = {}
        self.load_error = None
        self._load()

    @property
    def names(self):
        return tuple(sorted(self._items, key=str.casefold))

    def get(self, name):
        if name not in self._items:
            raise ValueError("Choose a saved material first.")
        return self._items[name]

    def save(self, preset):
        preset = preset.validated()
        self._items[preset.name] = preset
        self._write()

    def delete(self, name):
        if name not in self._items:
            raise ValueError("Choose a saved material first.")
        del self._items[name]
        self._write()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for item in data.get("materials", []):
                preset = MaterialPreset(**item).validated()
                self._items[preset.name] = preset
        except Exception as exc:
            self._items = {}
            self.load_error = str(exc)

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"materials": [asdict(self._items[name]) for name in self.names]},
                                        indent=2), encoding="utf-8")
        temporary.replace(self.path)
