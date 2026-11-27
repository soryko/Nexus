// Shared by the writer-host boot scripts: boot a disposable DSH profile under the real Loader with
// the receiver stand-in and the writer host, from the directory the script runs in.
import {createRequire} from 'node:module'
import {join} from 'node:path'
import {pathToFileURL} from 'node:url'
import {writeFileSync,readFileSync} from 'node:fs'

export async function bootProfile({extra=[],host:overrides={}}={}){
  const root=process.cwd(), req=createRequire(join(root,'boot.mjs'))
  const fixture=JSON.parse(readFileSync('project.json','utf8')), workspace=fixture.workspace
  const anchor=req.resolve('@deepseek-ai/dsh/package.json'), official=createRequire(anchor)
  const load=name=>import(pathToFileURL(official.resolve(name)))
  const {runProfile}=await load('./lib/profile-boot.js')
  const {loadProfileDirectory}=await load('@deepseek-ai/dsh-app-boot')
  const {createLaunchEnvironmentSnapshot}=await load('@deepseek-ai/dsh-launch-environment')
  const pkg=join(root,'package.json'), manifest=JSON.parse(readFileSync(pkg));manifest.dsh={profile:{bundles:[]}};writeFileSync(pkg,JSON.stringify(manifest))
  const config={instanceId:'nexus-project',workspace,binding:fixture.binding,readBinding:fixture.readBinding,memorySpaces:{dataDir:join(root,'ms-data')},...overrides}
  writeFileSync('stray.mjs','export function apply(){}')
  writeFileSync('cordis.patch.yml',JSON.stringify([{insert:[{id:'receiver',name:pathToFileURL(join(root,'receiver.mjs')).href},
    {id:'nexus-writer',name:'dsh-mnemon-provider-nexus/writer-host',config},...extra]}]))
  const profile=loadProfileDirectory('dsh',root,anchor)
  if(!overrides.workspace)process.chdir(workspace)          // a DSH process serves the workspace it starts in
  const {ctx}=await runProfile({environment:createLaunchEnvironmentSnapshot([{source:'process',values:process.env}]),profile:'fixture',resolvedProfile:{profile,installAnchor:anchor},args:[],patchFiles:[]})
  const {sources,tools,header}=await import(pathToFileURL(join(root,'receiver.mjs')).href)
  return {ctx,sources,tools,header,workspace,fixture,root}
}

export const attempt=async f=>{try{return {ok:true,value:await f()}}catch(e){return {ok:false,error:String(e?.message??e)}}}

/** Open the composed Source as mnemon does for one workspace scope, and expose search and save. */
export async function openSource({sources,tools,header,workspace}){
  const [key]=[...sources.keys()], sourceInstanceKey='source:'+key
  const scope={storage:'custom',workspaceId:workspace,sessionId:'root',agentId:'root'}
  const runtime=sources.get(key).create({sourceInstanceKey,configuration:{}})
  const facts=await runtime.facts({scope})
  const {readGrant:grant}=await runtime.project({scope,sourceInstanceKey,expectedRevision:facts.revision,includeProjection:false,mode:'routed',maxCharacters:1000})
  const route={id:sourceInstanceKey+'/recall',sourceInstanceKey,sourceRouteId:'recall',readGrantId:grant.id}
  const search=(query,ws=workspace)=>attempt(async()=>(await runtime.query({view:{id:'fixture',scope:{...scope,workspaceId:ws}},grant,route,input:{query}})).items.map(i=>i.text))
  const tool=tools.get('nexus_remember')
  const save=(who,content)=>attempt(()=>tool.execute({content,category:'decision'},{agent:{id:who,session:{header:header[who]}}}))
  return {runtime,scope,grant,route,sourceInstanceKey,search,save}
}
