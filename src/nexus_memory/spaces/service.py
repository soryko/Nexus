"""Trusted host-side service selection. No publication or mutation entry point in A03."""
from __future__ import annotations

from nexus_memory.domain.errors import InvalidInput
from nexus_memory.domain.spaces import GrantContext
from nexus_memory.memory import MemoryService
from nexus_memory.storage import SQLiteRepository
from .catalog import Catalog


class BoundSpace:
    __slots__ = ('_binding', '_service', '_validate')
    graph_repository = None

    def __init__(self, *args, **kwargs):
        raise InvalidInput('catalog resolution required')

    @classmethod
    def _resolve(cls, catalog, grant, alias, write):
        binding, locator = catalog._resolve(grant, alias, write)

        def validate():
            catalog._resolve(grant, alias, write)

        class MountedReader(SQLiteRepository):
            SCHEMA_VERSION = _target_schema_version = SQLiteRepository.OWNER_SCHEMA_VERSION

            def _connect_read_only(self):
                validate()
                return super()._connect_read_only()

        bound = object.__new__(cls)
        bound._binding = binding
        bound._service = MemoryService(MountedReader.open_readonly(locator), binding.scope)
        bound._validate = validate
        return bound

    @property
    def binding(self):
        self._validate()
        return self._binding

    @property
    def memory_service(self):
        self._validate()
        return self._service


class MountedMemoryService:
    def __init__(self, catalog: Catalog, grant: GrantContext):
        if not isinstance(catalog,Catalog) or not isinstance(grant,GrantContext):
            raise InvalidInput('catalog grant required')
        self._catalog, self._grant = catalog, grant

    def resolve(self, alias=None, *, write=False) -> BoundSpace:
        """Select the sole mount/default destination. Writes await the A05 fence."""
        return BoundSpace._resolve(self._catalog,self._grant,alias,write)
