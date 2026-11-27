// Boots a disposable DSH profile under the real Loader with the writer host, then drives its
// Source as mnemon would and its save tool as the agent loop would.
// argv: <case>, 'pinned' or 'shared' (another loader entry names Nexus).
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
const host={id:'nexus-writer',name:'dsh-mnemon-provider-nexus/writer-host',config:{instanceId:'nexus-project',workspace,binding:fixture.binding,readBinding:fixture.readBinding,memorySpaces:{dataDir:join(root,'ms-data')}}}
const extra=mode==='shared'?[{id:'stray',name:pathToFileURL(join(root,'stray.mjs')).href,config:{note:'nexus-memory-bridge'}}]:[]
writeFileSync('stray.mjs','export function apply(){}')
writeFileSync('cordis.patch.yml',JSON.stringify([{insert:[{id:'receiver',name:pathToFileURL(join(root,'receiver.mjs')).href},host,...extra]}]))
const profile=loadProfileDirectory('dsh',root,anchor)
process.chdir(workspace)                         // a DSH process serves the workspace it starts in
const {ctx}=await runProfile({environment:createLaunchEnvironmentSnapshot([{source:'process',values:process.env}]),profile:'fixture',resolvedProfile:{profile,installAnchor:anchor},args:[],patchFiles:[]})
const {sources,tools,header}=await import(pathToFileURL(join(root,'receiver.mjs')).href)
const results={}
let runtime
try {
  assert.equal(sources.size,1,JSON.stringify([...sources.keys()]))
  const [key]=[...sources.keys()], sourceInstanceKey='source:'+key
  const scope={storage:'custom',workspaceId:workspace,sessionId:'root',agentId:'root'}, view=(s=scope)=>({id:'fixture',scope:s})
  runtime=sources.get(key).create({sourceInstanceKey,configuration:{}})
  const manage=async(operation,input,s=scope,m='mutate')=>(await runtime.manage({sourceInstanceKey,scope:s,mode:m,operation,input,confirmed:true})).value
  const facts=await runtime.facts({scope})                    // the host enables its provider on first use
  results.enabledByHost=(await manage('provider-services',null,scope,'read')).items.some(s=>s.providerId==='nexus'&&s.enabled===true)
  const body=(await manage('body-directory',null,scope,'read')).items.find(b=>b.provider.id==='nexus');assert.ok(body)
  results.sourceRemember=body.provider.capabilities.remember
  const {readGrant:grant}=await runtime.project({scope,sourceInstanceKey,expectedRevision:facts.revision,includeProjection:false,mode:'routed',maxCharacters:1000})
  const route={id:sourceInstanceKey+'/recall',sourceInstanceKey,sourceRouteId:'recall',readGrantId:grant.id}
  const attempt=async f=>{try{return {ok:true,value:await f()}}catch(e){return {ok:false,error:String(e?.message??e)}}}
  // mnemon's path, even when the request names the root agent (as the delegated child's did).
  results.viaSource=await attempt(()=>runtime.mutate({view:view(),grant,offer:{id:sourceInstanceKey+'/remember',sourceInstanceKey,sourceActionId:'remember'},input:{memoryBodyId:body.id,content:'via mnemon'}}))
  const tool=tools.get('nexus_remember');assert.ok(tool,'nexus_remember is registered')
  const save=(who,content)=>attempt(()=>tool.execute({content,category:'decision'},{agent:{id:who,session:{header:header[who]}}}))
  results.root=await save('root','Phase 2 fixture: the staging database is SQLite')
  for(const who of ['worker','delegated','fork','elsewhere'])results[who]=await save(who,who+' write')
  results.otherWorkspace=await attempt(()=>runtime.query({view:view({...scope,workspaceId:'/'}),grant,route,input:{query:'staging'}}))
  results.query=await attempt(async()=>(await runtime.query({view:view(),grant,route,input:{query:'staging'}})).items.map(i=>i.text))
  await ctx.loader.create({name:pathToFileURL(join(root,'stray.mjs')).href,config:{}})   // a change after the first save
  results.afterChange=await save('root','after a profile change')
} finally {
  try { await runtime?.dispose?.() } finally { await ctx.fiber.dispose() }
}
console.log(JSON.stringify(results))
