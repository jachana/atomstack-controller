"""Atomic project/recovery storage with independent files for concurrent sessions."""
import json
import os
from pathlib import Path
import uuid


def atomic_json(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(data,handle,ensure_ascii=False,indent=2)
            handle.flush(); os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ProjectStore:
    def __init__(self, directory=None):
        self.directory=Path(directory or os.environ.get('ATOMSTACK_STATE_DIR') or
                            Path(os.environ.get('APPDATA',Path.home()))/'AtomstackController'/'designs')
        self.recovery=self.directory/('recovery-'+uuid.uuid4().hex+'.json')
        self.adopted=None

    def remember(self,path):
        paths=[str(Path(path).resolve())]+[p for p in self.recent() if p!=str(Path(path).resolve())]
        atomic_json(self.directory/'recent.json',paths[:10])

    def recent(self):
        try:
            data=json.loads((self.directory/'recent.json').read_text(encoding='utf-8'))
            return [p for p in data if isinstance(p,str)][:10] if isinstance(data,list) else []
        except (OSError,ValueError): return []

    def autosave(self, payload, path):
        atomic_json(self.recovery,{'document':payload,'path':str(path) if path else None})

    def candidates(self):
        found=[]
        for path in self.directory.glob('recovery-*.json'):
            if path==self.recovery: continue
            try: found.append((path.stat().st_mtime,path))
            except OSError: continue  # Another session cleared it mid-scan.
        return [path for _,path in sorted(found,reverse=True)]

    def clear(self):
        self.recovery.unlink(missing_ok=True)
        if self.adopted:
            self.adopted.unlink(missing_ok=True); self.adopted=None
