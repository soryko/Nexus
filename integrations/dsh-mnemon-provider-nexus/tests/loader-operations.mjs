import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {join} from 'node:path'
import {pathToFileURL} from 'node:url'
import {writeFileSync,readFileSync} from 'node:fs'

const root=process.cwd(), pkg=join(root,'package.json'), req=createRequire(import.meta.url)
const anchor=req.resolve('@deepseek-ai/dsh/package.json'), official=createRequire(anchor)
const load=name=>import(pathToFileURL(official.resolve(name)))
const {runProfile}=await load('./lib/profile-boot.js')
const {loadProfileDirectory}=await load('@deepseek-ai/dsh-app-boot')
const {createLaunchEnvironmentSnapshot}=await load('@deepseek-ai/dsh-launch-environment')
const manifest=JSON.parse(readFileSync(pkg));manifest.dsh={profile:{bundles:[]}};writeFileSync(pkg,JSON.stringify(manifest))
const entries=JSON.parse(readFileSync('sources.json'))
writeFileSync('cordis.patch.yml',JSON.stringify([{insert:[{id:'receiver',name:pathToFileURL(join(root,'receiver.mjs')).href},...entries]}]))
const profile=loadProfileDirectory('dsh',root,anchor)
const {ctx}=await runProfile({environment:createLaunchEnvironmentSnapshot([{source:'process',values:process.env}]),profile:'fixture',resolvedProfile:{profile,installAnchor:anchor},args:[],patchFiles:[]})
const {sources}=await import('./receiver.mjs'), runtimes=[]
try {
 assert.equal(sources.size,2)
 const mounted=[]
 for(const entry of entries){
  const key=[...sources.keys()].find(k=>k===entry.id || k.endsWith(':'+entry.id));assert.ok(key,JSON.stringify([...sources.keys()]))
  const sourceInstanceKey='source:'+key,scope={storage:'custom',workspaceId:root},view={id:'fixture',scope}
  const runtime=sources.get(key).create({sourceInstanceKey,configuration:{dataDir:entry.config.dataDir}});runtimes.push(runtime)
  const manage=async(mode,operation,input)=>(await runtime.manage({sourceInstanceKey,scope,mode,operation,input,confirmed:true})).value
  await manage('mutate','provider-service-update',{providerId:'same-child',enabled:true,settings:{binding:'only'}})
  const directory=await manage('read','body-directory',null)
  const body=directory.items.find(b=>b.provider.id==='same-child');assert.ok(body)
  assert.equal(body.provider.capabilities.remember,false)
  // 0.5.31 splits even a body-update's connection into service and memory
  // settings. 'service' scope by itself is NOT an owner isolation boundary.
  const changeBinding=binding=>manage('mutate','body-update',{memoryBodyId:body.id,connection:{binding}})
  await changeBinding('alien')
  const services=await manage('read','provider-services',null)
  assert.equal(services.items.find(s=>s.providerId==='same-child').settings.binding,'alien')
  const reconnect=()=>manage('read','body-reconnect',{memoryBodyId:body.id})
  await assert.rejects(reconnect,/unmounted/)
  await changeBinding('only')
  assert.equal((await reconnect()).healthy,true)
  const facts=await runtime.facts({scope});assert.ok(!facts.actionIds.includes('remember'))
  const {readGrant:grant}=await runtime.project({scope,sourceInstanceKey,expectedRevision:facts.revision,includeProjection:false,mode:'routed',maxCharacters:1000})
  const route={id:sourceInstanceKey+'/recall',sourceInstanceKey,sourceRouteId:'recall',readGrantId:grant.id}
  const query=async()=> (await runtime.query({view,grant,route,input:{query:'fixture'}})).items.map(i=>i.text)
  assert.deepEqual(await query(),['Loader fixture '+entry.id])
  const mutate=g=>runtime.mutate({view,grant:g,offer:{id:sourceInstanceKey+'/remember',sourceInstanceKey,sourceActionId:'remember'},input:{memoryBodyId:body.id,content:'unauthorized'}})
  await assert.rejects(()=>mutate(grant),/remember|read-only|writ/i)
  mounted.push({grant,query,mutate})
 }
 await assert.rejects(()=>mounted[1].mutate(mounted[0].grant),/grant|Source/i)
 assert.deepEqual(await mounted[0].query(),['Loader fixture work'])
}finally{try{for(const runtime of runtimes) await runtime.dispose?.()}finally{await ctx.fiber.dispose()}}
assert.equal(sources.size,0)
