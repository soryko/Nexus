// Isolated visual/interaction check using the real client factory and React. No live DSH session.
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const workspace = process.env.DSH_CHECKOUT;
if (!workspace) throw Error('Set DSH_CHECKOUT to a DSH checkout with apps/web dependencies installed');
const req=createRequire(path.resolve(workspace,'apps/web/package.json'));
const { chromium }=req('playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
try {
  const page=await browser.newPage();
  await page.setContent('<div style="display:flex"><main id="chat"></main><aside id="dock" style="flex:1;min-width:0"></aside></div>');
  for(const [pkg,file] of [['react','react.production.min.js'],['react-dom','react-dom.production.min.js']]) {
    const source=path.join(path.dirname(req.resolve(pkg+'/package.json')),'umd',file);
    await page.addScriptTag({content:await readFile(source,'utf8')});
  }
  // Match DSH's frozen platform table: React and real Zod, no relative imports.
  const {build}=createRequire(req.resolve('vite/package.json'))('esbuild');
  const zod=await build({entryPoints:[req.resolve('zod')],bundle:true,write:false,format:'iife',globalName:'Zod'});
  await page.addScriptTag({content:zod.outputFiles[0].text});
  await page.evaluate(()=>{window.__ModuleLoader__={load:({factory})=>window.plugin=factory(n=>{if(n==='react')return window.React;if(n==='zod')return window.Zod;throw Error(n)})}});
  await page.addScriptTag({content:await readFile(new URL('../client.js',import.meta.url),'utf8')});
  await page.evaluate(async()=>{
    const text='  historical text\n<b>not markup</b>\n';window.reads=[];const sid='fixture';
    let agentSchema, seqSchema, resultSchema;
    const remote={
      $mount:async({descriptors:[descriptor]})=>{
        const codecs=[...descriptor.parameters.map(p=>p.codec),descriptor.result];
        if(!codecs.every(c=>c.mode==='strict' && c.typeSymbol))throw Error('Missing strict codec');
        [agentSchema,seqSchema,resultSchema]=codecs.map(c=>c.create());
        if(agentSchema.safeParse(null).success || agentSchema.safeParse('').success || ['3',-1,1.5,Number.MAX_SAFE_INTEGER+1].some(n=>seqSchema.safeParse(n).success))throw Error('Malformed input accepted');
        resultSchema.parse({status:'unavailable'});
        if([{status:'unavailable',content:'private'},{status:'ok'},{status:'other'}].some(r=>resultSchema.safeParse(r).success))throw Error('Malformed output accepted');
        return ()=>{};
      },
      nexusInspector:{open:async(id,seq)=>{
        agentSchema.parse(id); seqSchema.parse(seq);
        window.reads.push([id,seq]);
        if (seq===4 && window.reads.filter(([,n])=>n===4).length===1) return {ok:true,value:{status:'unavailable'}};
        return {ok:true,value:resultSchema.parse({status:'ok',seq,turn:1,originSeq:1,memoryId:'m',savedRevisionId:'r1',currentRevisionId:'r2',isCurrent:false,content:seq===3 ? text : 'another revision'})};
      }}
    };
    const entries = new Map();
    let selected = sid, allowed = true, controller, tab, definition;
    const chat = ReactDOM.createRoot(document.querySelector('#chat'));
    const dock = ReactDOM.createRoot(document.querySelector('#dock'));
    const render = () => {
      const props = { sessionId:selected, t:ctx.t, useAccess:()=>allowed, useProjection:()=>({turns:{1:[{seq:3},{seq:4}]}}) };
      const entry = (name, more) => {
        const [opts,component] = entries.get(name);
        return React.createElement(component,{...opts.inject(selected),...props,...more});
      };
      chat.render(entry('conversation.chat.turnTail',{turn:{turn:1}}));
      dock.render(tab && entry('sidebar.right.pane.tab',{useTabInfo:()=>({tab})}));
    };
    const close = () => { controller.abort(); tab=null; render(); };
    const openTab = (kind,{params}) => {
      if (kind!==definition.kind) throw Error('Unknown tab kind');
      if (!tab) controller=new AbortController();
      tab={id:'pane-tab', visible:true, signal:controller.signal, actions:{close,openTab}, navigation:{params,revision:(tab?.navigation.revision??0)+1}};
      render();
    };
    const ctx={remote,inject:(deps,fn)=>{if(!deps.every(key=>key==='remote.nexusInspector'||key in ctx))throw Error('Missing dependency');return fn(ctx)},
      locale:{register:(ns,msgs)=>{ctx.t=k=>msgs.en[k];return ()=>{}},bind:()=>ctx.t},effect:fn=>fn(),
      slots:{inject:(name,fn)=>fn(),register:(opts,component)=>entries.set(opts.name,[opts,component])},
      sidebarRightTabs:{register:def=>{definition=def; return ()=>{}}}, sidebarRight:{mounted:{getSnapshot:()=>selected},openTab}};
    await window.plugin.apply(ctx);
    if(entries.get('sidebar.right.pane.tab')[0].key!==definition.id || definition.title()!=='Saved memory') throw Error('Missing tab binding');
    window.fixture={session(id){selected=id;render()},access(value){allowed=value;render()},
      visible(value){tab={...tab,visible:value};render()},restore(){openTab(definition.kind,{})}};
    render();
  });
  const cue=page.getByRole('button',{name:'Saved memory (2)',exact:true});
  const panel=page.locator('aside').getByRole('region',{name:'Saved memory',exact:true});
  const reads=()=>page.evaluate(()=>window.reads);
  await cue.waitFor();assert.deepEqual(await reads(),[]);
  await cue.click();await panel.getByText('A newer revision exists.',{exact:false}).waitFor();
  assert.equal(await panel.locator('pre').textContent(),'  historical text\n<b>not markup</b>\n');
  assert.equal(await page.locator('main pre').count(),0);
  await page.evaluate(()=>fixture.visible(false));await panel.locator('pre').waitFor({state:'detached'});
  await page.evaluate(()=>fixture.visible(true));await panel.locator('pre').waitFor();
  assert.deepEqual(await reads(),[['fixture',3]]);
  await page.evaluate(()=>fixture.session('other'));await panel.getByText('This saved revision is unavailable.').waitFor();
  assert.deepEqual(await reads(),[['fixture',3]]);
  await page.evaluate(()=>fixture.session('fixture'));await panel.locator('pre').waitFor();
  await panel.getByRole('combobox').selectOption('4');await panel.getByRole('button',{name:'Try again'}).click();
  await panel.getByText('another revision',{exact:true}).waitFor();
  assert.deepEqual(await reads(),[['fixture',3],['fixture',4],['fixture',4]]);
  await page.setViewportSize({width:360,height:640});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await panel.getByRole('button',{name:'Return to turn'}).click();
  await panel.waitFor({state:'detached'});assert.equal(await cue.evaluate(el=>el===document.activeElement),true);
  await cue.click();await panel.locator('pre').waitFor();await page.keyboard.press('Escape');
  await panel.waitFor({state:'detached'});assert.equal(await cue.evaluate(el=>el===document.activeElement),true);
  await page.evaluate(()=>fixture.restore());await panel.getByText('This saved revision is unavailable.').waitFor();
  await page.evaluate(()=>fixture.access(false));await cue.waitFor({state:'detached'});
  assert.equal((await reads()).length,4);
  console.log('PASS: scoped dock, lazy reads, remount, session mismatch, grouped selection/retry, focus, narrow width and unbound restore.');
}finally{await browser.close()}
