"""Provision one fresh test-only grant in the external consumer directory."""
import json
import sys
from pathlib import Path

from nexus_memory.domain.spaces import OperationRef
from nexus_memory.spaces.catalog import Catalog
from nexus_memory.storage.sqlite import SQLiteRepository
from nexus_memory.transport.harnesses.generic import register

root = Path.cwd()
catalog = Catalog(root / 'catalog')
binding = catalog.create_space('fixture', 'research', principal_id='fixture', operation=OperationRef('fixture', 'space'))
grant = register(catalog, 'fixture', binding.space_id, stream='fixture', receiving_context='only', mode='read_write')
_, path = catalog._resolve(grant, None, False)
SQLiteRepository(path, owner_schema6=True)
ticket = root / 'ticket'
ticket.touch(mode=0o600, exist_ok=False)
ticket.write_text(json.dumps({'grant_id':grant.grant_id}))
print(json.dumps({'executable':str(Path(sys.executable).with_name('nexus-memory-bridge')),
                  'args':['--catalog',str(catalog.path),'--grant-file',str(ticket)],'journalDir':str(root/'journal')}))
