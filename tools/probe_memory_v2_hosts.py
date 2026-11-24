"""macOS-only, no inference: native CLI health and Codex app-server tool call."""
import select, subprocess, json, tempfile, asyncio, time
from pathlib import Path
from check_memory_v2 import Fixture,scenario,BIN,QUERY

def probe(f,ticket,verify,observations):
    # Deny network and Keychain IPC even if a client attempts automatic auth.
    prefix=['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)(deny mach-lookup (global-name "com.apple.securityd") (global-name "com.apple.security.agent"))']
    args=f.server_args(ticket)
    cfg=f.root/'codex'; cfg.mkdir()
    env=dict(f.env,CODEX_HOME=str(cfg),CLAUDE_CONFIG_DIR=str(f.root/'claude'),CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    def cmd(*args):
        return subprocess.run([*prefix,*map(str,args)],env=env,cwd=f.git,text=True,capture_output=True,timeout=30)
    add=cmd('claude','mcp','add','--scope','local','fixture','--',BIN/'nexus-memory',*args)
    observations['Claude Code'] = dict(version=cmd('claude','--version').stdout.strip(),
        recall='pending: no no-model tool-call path exercised', health='not connected')
    if add.returncode==0:
        get=cmd('claude','mcp','get','fixture')
        if get.returncode==0 and 'Connected' in get.stdout:
            observations['Claude Code']['health']='connected'
    observations['Codex'] = dict(version=cmd('codex','--version').stdout.strip(), recall='pending')
    (cfg/'config.toml').write_text('cli_auth_credentials_store="file"\nmodel_provider="fixture"\nmodel="fixture"\n[model_providers.fixture]\nname="Disabled"\nbase_url="http://127.0.0.1:1/v1"\n[mcp_servers.fixture]\ncommand='+json.dumps(str(BIN/'nexus-memory'))+'\nargs='+json.dumps(args)+'\n')
    with subprocess.Popen([*prefix,'codex','app-server','--stdio'],cwd=f.git,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0) as proc:
        try:
            def send(obj): proc.stdin.write(json.dumps(obj).encode()+b'\n')
            def rpc(id,method,params):
                send(dict(id=id,method=method,params=params)); deadline=time.monotonic()+30
                while select.select([proc.stdout],[],[],max(0,deadline-time.monotonic()))[0]:
                    line=proc.stdout.readline()
                    if not line: raise ValueError('host exited')
                    out=json.loads(line)
                    if out.get('id')==id:
                        if 'error' in out: raise ValueError(method+': '+str(out['error']['code']))
                        return out['result']
                raise TimeoutError(method)
            rpc(1,'initialize',dict(clientInfo=dict(name='fixture',version='1'),capabilities=dict(experimentalApi=True))); send(dict(method='initialized'))
            th=rpc(2,'thread/start',dict(cwd=str(f.git),ephemeral=True,sandbox='read-only',approvalPolicy='never'))['thread']['id']
            res=rpc(3,'mcpServer/tool/call',dict(threadId=th,server='fixture',tool='recall',arguments=QUERY))
            pack=json.loads(res['content'][0]['text'])
            observations['Codex'].update(retrieved_refs=verify(pack),recall='verified: app-server MCP tool call')
        finally: proc.kill()


def main():
    observations={}
    report=dict(kind='fresh_host_probe',observations=observations,model_turns=0)
    try:
        with tempfile.TemporaryDirectory(prefix='nexus-host-') as d:
            f=Fixture(Path(d))
            result=asyncio.run(scenario(f,'sha1',lambda *args: probe(*args,observations)))
            report.update(R=result['R'],exact_receipt=result['exact_receipt'],
                          curation_operation_id=result['curation_receipt']['operation_id'])
    except Exception as exc:
        report['probe_error_type']=type(exc).__name__
    print(json.dumps(report,indent=2))
    return int('probe_error_type' in report)


if __name__=='__main__':
    raise SystemExit(main())
