// Boots a disposable DSH profile under the real Loader with the writer host, then drives its
// Source as mnemon would. argv: <case> where case is 'pinned' or 'shared' (another entry names Nexus).
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {join} from 'node:path'
import {pathToFileURL} from 'node:url'
import {writeFileSync,readFileSync} from 'node:fs'

const root=process.cwd(), mode=process.argv[2], req=createRequire(import.meta.url)
const fixture=JSON.parse(readFileSync('project.json','utf8')), workspace=fixture.workspace
const anchor=req.resolve('@deepseek-ai/dsh/package.json'), official=createRequire(anchor)
const load=name=>import(pathToFileURL(official.resolve(name)))
const {runProfile}=await load('./lib/profile-boot.js')
const {loadProfileDirectory}=await load('@deepseek-ai/dsh-app-boot')
const {createLaunchEnvironmentSnapshot}=await load('@deepseek-ai/dsh-launch-environment')
const pkg=join(root,'package.json'), manifest=JSON.parse(readFileSync(pkg));manifest.dsh={profile:{bundles:[]}};writeFileSync(pkg,JSON.stringify(manifest))
const host={id:'nexus-writer',name:'dsh-mnemon-provider-nexus/writer-host',config:{instanceId:'nexus-project',workspace,binding:fixture.binding,memorySpaces:{dataDir:join(root,'ms-data')}}}
const extra=mode==='shared'?[{id:'stray',name:pathToFileURL(join(root,'stray.mjs')).href,config:{note:'nexus-memory-bridge'}}]:[]
writeFileSync('stray.mjs','export function apply(){}')
writeFileSync('cordis.patch.yml',JSON.stringify([{insert:[{id:'receiver',name:pathToFileURL(join(root,'receiver.mjs')).href},host,...extra]}]))
const profile=loadProfileDirectory('dsh',root,anchor)
process.chdir(workspace)                         // a DSH process serves the workspace it starts in
const {ctx}=await runProfile({environment:createLaunchEnvironmentSnapshot([{source:'process',values:process.env}]),profile:'fixture',resolvedProfile:{profile,installAnchor:anchor},args:[],patchFiles:[]})
const {sources}=await import(pathToFileURL(join(root,'receiver.mjs')).href)
const results={}
let runtime
try {
  assert.equal(sources.size,1,JSON.stringify([...sources.keys()]))
  const [key]=[...sources.keys()], sourceInstanceKey='source:'+key
  const scopeFor=(agentId,ws=workspace)=>({storage:'custom',workspaceId:ws,sessionId:agentId,agentId})
  const scope=scopeFor('root'), view=(s=scope)=>({id:'fixture',scope:s})
  runtime=sources.get(key).create({sourceInstanceKey,configuration:{}})
  const manage=async(operation,input,s=scope,m='mutate')=>(await runtime.manage({sourceInstanceKey,scope:s,mode:m,operation,input,confirmed:true})).value
  const before=await runtime.facts({scope})                    // the host enables its provider on first use
  results.enabledByHost=(await manage('provider-services',null,scope,'read')).items.some(s=>s.providerId==='nexus'&&s.enabled===true)
  const body=(await manage('body-directory',null,scope,'read')).items.find(b=>b.provider.id==='nexus');assert.ok(body)
  results.remember=body.provider.capabilities.remember
  const facts=await runtime.facts({scope})
  results.offers=facts.actionIds.includes('remember')
  const {readGrant:grant}=await runtime.project({scope,sourceInstanceKey,expectedRevision:facts.revision,includeProjection:false,mode:'routed',maxCharacters:1000})
  const route={id:sourceInstanceKey+'/recall',sourceInstanceKey,sourceRouteId:'recall',readGrantId:grant.id}
  const attempt=async f=>{try{return {ok:true,value:await f()}}catch(e){return {ok:false,error:String(e?.message??e)}}}
  const remember=(s,content,action='remember')=>attempt(()=>runtime.mutate({view:view(s),grant,offer:{id:sourceInstanceKey+'/'+action,sourceInstanceKey,sourceActionId:action},input:{memoryBodyId:body.id,content}}))
  results.root=await remember(scope,'Phase 2 fixture: the staging database is SQLite')
  results.subagent=await remember(scopeFor('worker'),'worker write')
  results.fork=await remember(scopeFor('fork'),'fork write')
  results.archive=await remember(scope,'archive write','archive')
  results.otherWorkspace=await attempt(()=>runtime.query({view:view(scopeFor('root','/')),grant,route,input:{query:'staging'}}))
  results.query=await attempt(async()=>(await runtime.query({view:view(),grant,route,input:{query:'staging'}})).items.map(i=>i.text))
  await ctx.loader.create({name:pathToFileURL(join(root,'stray.mjs')).href,config:{}})   // a change after the first write
  results.afterChange=await remember(scope,'after a profile change')
} finally {
  try { await runtime?.dispose?.() } finally { await ctx.fiber.dispose() }
}
console.log(JSON.stringify(results))
