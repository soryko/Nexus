import {createRequire} from 'node:module';
import {existsSync,readFileSync,writeFileSync} from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const [checkout,dir,prefix]=process.argv.slice(2);
const url=new URL(readFileSync(path.join(dir,'host.log'),'utf8').match(/dsh web: (http:\/\/127\.0\.0\.1:\d+\/\?token=\S+)/)[1]);
assert.notEqual(url.port,'3080');
const req=createRequire(path.join(checkout,'apps/web/package.json'));
const b=await req('playwright').chromium.launch({channel:'chrome'});
const p=await b.newPage({viewport:{width:1360,height:850},locale:'en-US'});
const errors=new Set();
try {
  await p.route('**/*',route=>new URL(route.request().url()).origin===url.origin?route.continue():route.abort());
  const c=await p.context().newCDPSession(p);
  await c.send('Debugger.enable');await c.send('Debugger.setPauseOnExceptions',{state:'all'});
  c.on('Debugger.paused',async e=>{const msg=e.data?.description?.split('\n')[0];if(msg?.includes('nexusInspector'))errors.add(msg);await c.send('Debugger.resume').catch(()=>{});});
  await p.goto(url.href); p.setDefaultTimeout(12000);
  const failed=p.getByText('Failed to load plugins',{exact:true});
  const cue=p.getByRole('button',{name:'Saved memory',exact:true});
  const title=p.getByText('Amber checkpoint rehearsal',{exact:true}).first();
  const notice=p.getByRole('button',{name:'Continue',exact:true});
  await Promise.race([failed.waitFor(),cue.waitFor(),title.waitFor(),notice.waitFor()]);
  if(await notice.isVisible()){
    await notice.click();
    const key=p.getByRole('button',{name:'Add API Key',exact:true});
    await key.waitFor({timeout:3500}).catch(()=>{});
    if(await key.isVisible()){await key.click();await p.getByRole('button',{name:'Configure later',exact:true}).first().click();}
  }
  const group=p.getByText('Ungrouped',{exact:true});
  if(!await title.isVisible()&&!await cue.isVisible()&&await group.isVisible())await group.click();
  await Promise.race([failed.waitFor(),cue.waitFor(),title.waitFor()]);
  if(await failed.isVisible()) throw Error([...errors].join('; ')||'DSH plugin boot failed');
  const seed=JSON.parse(readFileSync(path.join(dir,'seed.json')));
  if(!await cue.isVisible())await title.click();
  const chat=p.getByText('Rehearsal: save the amber checkpoint.',{exact:true});await chat.waitFor();
  const observed=()=>existsSync(path.join(dir,'observed.jsonl'))?readFileSync(path.join(dir,'observed.jsonl'),'utf8').trim().split('\n').map(line=>JSON.parse(line)):[];
  assert.deepEqual(observed(),[],'no Nexus call on mount');
  p.on('response',async r=>{const req=r.request();if(req.method()==='POST' && (req.postData()||'').includes('nexusInspector')){const body=await r.json().catch(()=>null);if(body?.result?.error)errors.add(body.result.error.message);}});
  await cue.click();const panel=p.getByRole('region',{name:'Saved memory'});
  await panel.getByText('A newer revision exists.',{exact:false}).waitFor();
  assert.equal(await panel.locator('pre').textContent(),seed.original);
  for(const text of [seed.savedRevision,seed.currentRevision,seed.memoryId])await panel.getByText(text,{exact:true}).waitFor();
  assert.notEqual(seed.savedRevision,seed.currentRevision);
  // Desktop acceptance: the read surface is alongside the turn, not another inline expansion.
  const [cueBox, panelBox]=await Promise.all([cue.boundingBox(),panel.boundingBox()]);
  assert.ok(cueBox&&panelBox&&panelBox.x>=cueBox.x+cueBox.width-1,'inspector is beside the originating cue');
  await p.screenshot({path:prefix+'-open.png'});
  await panel.getByRole('button',{name:'Return to turn'}).click();
  await panel.waitFor({state:'hidden'});await cue.waitFor();await p.waitForFunction(()=>document.activeElement?.textContent?.trim()==='Saved memory');assert.ok(await cue.evaluate(el=>el===document.activeElement));await chat.waitFor();
  await p.screenshot({path:prefix+'-return.png'});
  const calls=observed();assert.deepEqual(calls,[{name:'mcp__nexus__get',arguments:{memory_id:seed.memoryId,revision_id:seed.savedRevision},isError:false}]);
  writeFileSync(path.join(dir,'result.json'),JSON.stringify({outcome:'pass',chrome:b.version(),...seed,calls,exactText:true,returnedFocus:true}));
} catch(e){await p.screenshot({path:prefix+'-blocked.png'});writeFileSync(path.join(dir,'result.json'),JSON.stringify({outcome:'blocked',chrome:b.version(),gate:String(e.message).split('\n')[0],caught:[...errors],visible:(await p.locator('body').innerText()).slice(0,3000)}));process.exitCode=1;}finally{await b.close();}
