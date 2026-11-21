"""Enable native turns only for launcher-coordinated, private command-hook launches."""
from contextlib import nullcontext
import os
from pathlib import Path
from nexus_memory.transport.host_control import HostControl


def ingress(broker, checkout):
    endpoint = os.environ.get('NEXUS_HOST_SOCKET')
    capability = os.environ.get('NEXUS_HOST_CAPABILITY')
    if checkout is None or not endpoint or not capability:
        return nullcontext()
    path = Path(endpoint)
    if not path.is_absolute() or path.parent.stat().st_uid != os.getuid() or path.parent.stat().st_mode & 0o077:
        return nullcontext()
    try:
        return HostControl(broker,path,capability,checkout=checkout)
    except OSError:
        return nullcontext()
