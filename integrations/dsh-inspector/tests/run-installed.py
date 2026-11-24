"""Run from Nexus repo with its venv: DSH_CHECKOUT=... .venv/bin/python integrations/dsh-inspector/tests/run-installed.py"""
import hashlib, json, os, re, signal, socket, subprocess as sp, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DSH = Path(os.environ['DSH_CHECKOUT']).resolve()
CLI = str(DSH / 'apps/cli/lib/bin.js')
PREFIX = ROOT / 'docs/measurements/dsh-inspector-side-panel-installed'

def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

with tempfile.TemporaryDirectory(prefix='nx-inspector-') as tmp:
    d = Path(tmp).resolve()
    env = dict(PATH=os.environ['PATH'], HOME=str(d), DSH_HOME=str(d/'dsh'), LANG='en_US.UTF-8', npm_config_fetch_retries='0')
    def run(args, **kw):
        return sp.run(args, cwd=d, env=env, check=True, text=True, timeout=120, **kw)
    pkg = HERE.parent
    run(['npm','pack','--ignore-scripts','--pack-destination',str(d),str(pkg)],capture_output=True)
    archive, = d.glob('*.tgz')
    run(['node',CLI,'plugin','--profile','web','add',str(archive)])
    profile=d/'dsh/profiles/web'
    installed=profile/'node_modules/@local/dsh-nexus-inspector'
    hashes={name:digest(installed/name) for name in ['host.js','client.js','projection.js','descriptor.js']}
    assert hashes=={name:digest(pkg/name) for name in hashes}
    cfg=run(['node',CLI,'web','--dump-config'],capture_output=True).stdout
    assert '- id: nexus-receipt-inspector' in cfg
    (d/'installed-fixture.mjs').write_bytes((HERE/'installed-fixture.mjs').read_bytes())
    (d/'package.json').write_text('{"name":"inspector-fixture-only","private":true}')
    patch=d/'fixture.json'
    mcp=dict(serverName='nexus', transport='stdio',command=sys.executable, args=['-B','-m','nexus_memory','--db',str(d/'fixture.db'),'--namespace','disposable-test','--actor','synthetic','--no-instructions'],cwd=str(d),env=dict(HOME=str(d),PYTHONPATH=str(ROOT/'src')),failOnStartupError=True)
    patch.write_text(json.dumps([{'insert':[{'id':'fixture-mcp','name':'@deepseek-ai/dsh-mcp-client','config':mcp},{'id':'fixture-session','name':str(d/'installed-fixture.mjs')}]}]))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    assert port != 3080
    log=d/'host.log'
    with log.open('w') as f:
        host=sp.Popen(['node',CLI,'web','--patch',str(patch),'--host','127.0.0.1','--port',str(port),'--no-open'], cwd=d,env=env,stdout=f,stderr=f,start_new_session=True)
    try:
        deadline=time.monotonic()+45
        while time.monotonic()<deadline and host.poll() is None and 'dsh web: http://' not in log.read_text(): time.sleep(.1)
        if 'dsh web: http://' not in log.read_text(): raise RuntimeError('Host startup: '+re.sub(r'token=\S+', 'token=[redacted]',log.read_text()[-2500:]))
        (d/'start-seed').touch()
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and not (d/'seed.json').exists() and 'Fixture seed failed:' not in log.read_text(): time.sleep(.1)
        browser=sp.run(['node',str(HERE/'installed-browser.mjs'),str(DSH),str(d),str(PREFIX)],cwd=d,env=env,timeout=90)
        evidence=json.loads((d/'result.json').read_text());evidence.update(loopbackPort=port,installedSha256=hashes,archiveSha256=digest(archive),dshCommit=run(['git','-C',str(DSH),'rev-parse','HEAD'],capture_output=True).stdout.strip(),fixtureSeeded=(d/'seed.json').is_file())
        if evidence['outcome']=='blocked' and (d/'observed.jsonl').exists(): print('Observed', (d/'observed.jsonl').read_text())
        if evidence['outcome']=='blocked': print(re.sub(r'token=\S+', 'token=[redacted]',log.read_text()[-4000:]))
    finally:
        try: os.killpg(host.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        try: host.wait(timeout=8)
        except sp.TimeoutExpired: os.killpg(host.pid,signal.SIGKILL);host.wait()
    with socket.socket() as sock: assert sock.connect_ex(('127.0.0.1',port))!=0
    evidence['listenerClosed']=True
    PREFIX.with_suffix('.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n');print(json.dumps(evidence,ensure_ascii=False));sys.exit(browser.returncode)
