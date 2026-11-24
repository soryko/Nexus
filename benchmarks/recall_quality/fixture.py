"""Only opens a newly-created scratch database; never uses default store bindings."""
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'src'))
from nexus_memory.domain.errors import MemoryNotFound
from nexus_memory.domain.models import MemoryInput, Scope, SearchQuery
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


class Unsafe(Exception):
    pass


class Fixture:
    def __init__(self, new_directory, records):
        path = Path(new_directory) / 'scratch.sqlite'
        if path.exists():
            raise ValueError('refusing to open an existing store')
        repo = SQLiteRepository(path)
        self.service = MemoryService(repo, Scope('pilot', 'local'))
        foreign = MemoryService(repo, Scope('foreign', 'local'))
        self.ids, self.labels, self.records, self.revisions = {}, {}, records, {}
        self.forbidden = ['OLDORCHID', 'BLUEQUARTZ']
        for rec in records:
            label = rec['id']
            blocked = rec['scope'] != 'pilot' or rec['forgotten']
            svc = foreign if rec['scope'] == 'foreign' else self.service
            self.revisions[label] = []
            for n, text in enumerate(rec['revisions']):
                item = MemoryInput(text, kind=rec['kind'])
                key = f'{label}-{n}'
                prior = (svc.record(item, key) if n == 0 else
                         svc.revise(prior.memory_id, prior.revision_id, item, key))
                self.revisions[label].append(prior.revision_id)
            self.ids[label] = prior.memory_id
            self.labels[prior.memory_id] = label
            if rec['forgotten']:
                svc.forget(prior.memory_id, prior.revision_id, f'{label}-forget')
            if blocked:
                self.forbidden.extend([label, prior.memory_id, *self.revisions[label]])

    def guard(self, value):
        rendered = json.dumps(value).casefold()
        if any(token.casefold() in rendered for token in self.forbidden):
            raise Unsafe('ineligible fixture ID, revision or excerpt exposed')

    def search(self, args):
        page = asdict(self.service.search(SearchQuery(**args)))
        self.guard(page)
        return page

    def exact_controls(self):
        controls = {}
        for rec in self.records:
            label = rec['id']
            blocked = rec['scope'] != 'pilot' or rec['forgotten']
            for n, rev in enumerate([*self.revisions[label], None]):
                try:
                    view = asdict(self.service.get(self.ids[label], rev))
                except MemoryNotFound:
                    if not blocked:
                        raise Unsafe('eligible exact read missing')
                    controls[label, n] = 'not_found'
                else:
                    if blocked or view['content'] != rec['revisions'][n if rev else -1]:
                        raise Unsafe('exact read changed')
                    self.guard(view)
                    controls[label, n] = digest(view)
        return digest(sorted(controls.items()))

    def controls(self, queries):
        return {'explicit': [digest(self.search(args)) for args in queries],
                'exact': self.exact_controls(),
                'generation': self.service.status().latest_durable_seq}
