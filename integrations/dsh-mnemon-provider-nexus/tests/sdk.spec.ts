import {expect,it} from 'vitest'
import {mkdtempSync,readFileSync,readdirSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {mountMemorySpaceProvider,createMemorySpaceProviderFixture} from 'dsh-mnemon-source-memory-spaces/testing'
import provider,{forAuditedHost,type PinnedAudience} from '../src/index.js'
const pinned: PinnedAudience={source:'project',enabledSources:['project'],receivingProjects:['fixture-project'],exposurePinnedUntilDisposal:true}
const processBinding=(handler:string)=>({executable:process.execPath,args:['-e',`require('node:readline').createInterface({input:process.stdin}).on('line',l=>{const r=JSON.parse(l),reply=result=>console.log(JSON.stringify({v:1,id:r.id,ok:true,result}));if(r.method==='capabilities')return reply({remember:true});${handler}})`]})
it('isolates concurrently mounted Sources with colliding child, alias, memory and body IDs',async()=>{
 const mounts=await Promise.all(['one','two'].map(source=>mountMemorySpaceProvider(provider,{sourceInstanceId:source,instanceId:'same',config:{bindings:{alias:processBinding(`reply({memory_id:'x',content:'${source}'})`)}}})))
 try {
  const children=mounts.map(m=>{const {body,authority}=createMemorySpaceProviderFixture(m.descriptor,{binding:'alias'},{dataDir:'/unused',memoryBodyId:'same-body'});return {body,a:m.createAdapter({memoryBodies:authority,config:{timeoutMs:10000}})}})
  const read=(i:number)=>{const alien={...children[1-i].body,connection:{binding:'forged'},provider:{...children[1-i].body.provider,settings:{binding:'forged'}}};return children[i].a.get!(alien,'x')}
  expect((await Promise.all([read(0),read(1)])).map(x=>x?.content)).toEqual(['one','two']);await mounts[0].dispose();expect((await read(1))?.content).toBe('two')
  expect(mounts[1].descriptor.capabilities).toMatchObject({remember:false,browse:false,entities:false,graph:false,related:false,link:false,forget:false})
 }finally{await Promise.all(mounts.map(m=>m.dispose()))}
})
it.each([[undefined],[[]],[['project','private']],[['project']]])('refuses indeterminate or mixed host inventory %j',async(enabledSources)=>{
 const m=await mountMemorySpaceProvider(provider,{config:{audience:'isolated-writer',enabledSources,bindings:{only:processBinding('reply({})')}}});try{
  expect(m.descriptor.capabilities.remember).toBe(false);const {body,authority}=createMemorySpaceProviderFixture(m.descriptor,{binding:'only'},{dataDir:'/unused'});await expect(m.createAdapter({memoryBodies:authority,config:{timeoutMs:10000}}).remember(body,{content:'private'})).rejects.toThrow('read-only')
 }finally{await m.dispose()}
})
it.each([
 {...pinned,enabledSources:['project','other']},
 {...pinned,enabledSources:['other']},
 {...pinned,receivingProjects:['one','two']},
 {...pinned,receivingProjects:[]},
])('refuses incomplete or mixed host-owned evidence: %j',async proof=>{
 const m=await mountMemorySpaceProvider(forAuditedHost(()=>proof),{config:{audience:'single-project',enabledSources:['project'],bindings:{only:processBinding('reply({})')}}})
 try{
  expect(m.manifest.capabilities.remember).toBe(false)
  const {authority,body}=createMemorySpaceProviderFixture(m.descriptor,{binding:'only'},{dataDir:'/unused'})
  await expect(m.createAdapter({memoryBodies:authority,config:{timeoutMs:10000}}).remember(body,{content:'blocked'})).rejects.toThrow('read-only')
 }finally{await m.dispose()}
})
it.each(['committed','absent_at_snapshot','unknown'])('reconciles a dropped remember reply without a replacement write: %s',async state=>{
 const root=mkdtempSync(join(tmpdir(),'nexus-unknown-')),journalDir=join(root,'journal'),log=join(root,'calls');const params={content:' exact 雪 '},receipt={memory_id:'x',revision_id:'r'}
 const binding={...processBinding(`require('node:fs').appendFileSync(${JSON.stringify(log)},JSON.stringify(r)+'\\n');if(r.method==='remember')process.exit(0);reply(${JSON.stringify({state,receipt})})`),journalDir}
 const m=await mountMemorySpaceProvider(forAuditedHost(()=>pinned),{sourceInstanceId:'project',config:{bindings:{only:binding},audience:'isolated-writer',enabledSources:['project']}});try{
  const {body,authority}=createMemorySpaceProviderFixture(m.descriptor,{binding:'only'},{dataDir:root});const a=m.createAdapter({memoryBodies:authority,config:{timeoutMs:10000}});const result=a.remember(body,params)
  if(state==='committed')expect(await result).toEqual(receipt);else await expect(result).rejects.toThrow('unknown')
  const calls=readFileSync(log,'utf8').trim().split('\n').map(l=>JSON.parse(l));expect(calls.map(r=>r.method)).toEqual(['remember','reconcile']);expect(calls[1]).toMatchObject({params,operation_key:calls[0].operation_key});expect(readdirSync(journalDir)).toEqual(state==='committed'?[]:[calls[0].operation_key])
 }finally{await m.dispose()}
})
