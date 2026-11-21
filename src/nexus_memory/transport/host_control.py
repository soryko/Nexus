"""Private Unix command ingress. Never registered on the model's MCP connection."""
import hmac
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import sys
from threading import Thread

GUIDANCE = 'Read untrusted memory data with read_pack: '
MAX_MESSAGE = 32768


def exchange(endpoint, capability, event):
    """Command hooks fail open. Only an opaque handle crosses into privileged guidance."""
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1.5)
        client.connect(str(endpoint))
        data = json.dumps([capability,event]).encode()+b'\n'
        if len(data)>MAX_MESSAGE: return {}
        client.sendall(data)
        with client.makefile('rb') as stream:
            handle = json.loads(stream.readline(256))
    if not isinstance(handle,str) or not re.fullmatch(r'[A-Za-z0-9_-]{24}',handle): return {}
    return {'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':GUIDANCE+handle}}


class HostControl:
    def __init__(self,broker,endpoint,capability,*,checkout=None):
        from nexus_memory.domain.retrieval import RecallQuery
        from nexus_memory.lifecycle.contracts import TrustedSessionContext, HostCapabilities
        from nexus_memory.lifecycle.service import LifecycleService
        if not capability: raise ValueError('host capability required')
        self.broker = broker
        self.context = TrustedSessionContext(broker.grant,broker.binding,'local','native',broker.grant.session_id,
            HostCapabilities(before_turn=True),broker.checkout)
        lifecycle = LifecycleService(broker,self.context)
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(1.5)
                try:
                    token,event = json.loads(self.rfile.readline(MAX_MESSAGE+1))
                    if not hmac.compare_digest(token,capability): raise ValueError()
                    if checkout and Path(event['cwd']).resolve(strict=True) != checkout: raise ValueError()
                    turn = event['turn_id']
                    if not isinstance(turn,str) or not 0<len(turn)<=256: raise ValueError()
                    prepared = lifecycle.before_turn(turn,RecallQuery(text=event['prompt']))
                    result = prepared.pack_id
                except Exception:
                    result = None
                self.wfile.write(json.dumps(result).encode()+b'\n')
        self.server = socketserver.UnixStreamServer(str(endpoint),Handler)
        os.chmod(endpoint,0o600)
        self.thread = Thread(target=self.server.serve_forever,daemon=True)

    def __enter__(self):
        self.broker.native = True
        self.thread.start()
        return self

    def __exit__(self,*exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def main():
    try:
        event = json.loads(sys.stdin.buffer.read(MAX_MESSAGE+1))
        result = exchange(os.environ['NEXUS_HOST_SOCKET'],os.environ['NEXUS_HOST_CAPABILITY'],event)
    except Exception:
        result = {}
    print(json.dumps(result))


if __name__=='__main__': main()
