// Phase 2 exit criteria (plan §5): simultaneous unrelated sessions stay isolated; session switch,
// old receipt, worker write attempt and missing root behave as specified. The worker case is in
// writer-host.spec.ts (subagent, mnemon's delegated writer, fork). Each scenario boots the writer
// host under the real Loader against a disposable Nexus home; no model is called.
import {expect,it} from 'vitest'
import {copyFileSync,mkdirSync,readFileSync,symlinkSync} from 'node:fs'
import {join} from 'node:path'
import {spawn} from 'node:child_process'
import {external} from './external.js'

const python=new URL('../../../.venv/bin/python',import.meta.url).pathname
const here=(name:string)=>new URL('./'+name,import.meta.url)
const receiver=(workspace:string)=>`export const sources=new Map(),tools=new Map();export const provide=['mnemonMemory','tools'];
export const header={root:{cwd:${JSON.stringify(workspace)}}}
export function apply(ctx){
 ctx.provide('tools',{register(d){tools.set(d.name,d);return ()=>tools.delete(d.name)}})
 ctx.provide('mnemonMemory',{installContributions(c,options){const key=options.instanceId;if(c.sources.length!==1||sources.has(key))throw Error('unexpected sources');sources.set(key,c.sources[0]);return ()=>sources.delete(key)}})}`
const refused=(why:string)=>({ok:false,error:expect.stringContaining(why)})

function project(f:ReturnType<typeof external>,repo='api'){
 return JSON.parse(f.run(python,['-I',here('project_fixture.py').pathname,join(f.dir,'nexus'),'--repo',repo]))
}
/** A profile directory of its own inside the fixture, sharing the installed packages. */
function profileDir(f:ReturnType<typeof external>,name:string,proj:any){
 const dir=join(f.dir,'profiles',name);mkdirSync(dir,{recursive:true})
 copyFileSync(join(f.dir,'package.json'),join(dir,'package.json'));symlinkSync(join(f.dir,'node_modules'),join(dir,'node_modules'))
 for(const file of ['boot-setup.mjs','writer-exit-boot.mjs'])copyFileSync(here(file),join(dir,file))
 copyFileSync(here('writer-exit-boot.mjs'),join(dir,'boot.mjs'))
 f.write(join('profiles',name,'project.json'),JSON.stringify(proj));f.write(join('profiles',name,'receiver.mjs'),receiver(proj.workspace))
 return dir
}
function run(f:ReturnType<typeof external>,dir:string,args:string[]){
 return new Promise<any>((resolve,reject)=>{
  const env={HOME:f.dir,PATH:process.env.PATH,DSH_HOME:join(f.dir,'home'),MNEMON_DATA_DIR:join(f.dir,'data'),DSH_TELEMETRY_DISABLED:'1'}
  const child=spawn(process.execPath,['writer-exit-boot.mjs',...args],{cwd:dir,env,stdio:['ignore','pipe','pipe']})
  let out='',err='';child.stdout.on('data',d=>out+=d);child.stderr.on('data',d=>err+=d)
  child.on('exit',code=>{const last=out.trim().split('\n').pop()??'';try{resolve(JSON.parse(last))}catch{reject(Error(`exit ${code}: ${err.slice(-2000)}`))}})
 })
}
async function scenario(name:string){
 const f=external(true);const dir=profileDir(f,'p',project(f));return run(f,dir,[name])
}

it('keeps two unrelated repos isolated while their sessions save at the same moment',async()=>{
 const f=external(true),api=project(f,'api'),web=project(f,'web')
 const [a,b]=await Promise.all([run(f,profileDir(f,'a',api),['isolated',web.workspace]),run(f,profileDir(f,'b',web),['isolated',api.workspace])])
 for(const [r,own] of [[a,'api'],[b,'web']] as const){
  expect(r.save.ok,r.save.error).toBe(true)
  expect(r.search).toEqual({ok:true,value:[`Decision for ${own}: its staging database stays local`]})
  expect(r.peer).toMatchObject(refused('another workspace'))
 }
 expect(a.save.value.memory_id).not.toBe(b.save.value.memory_id)
},300000)

it('stops saving once the process leaves its workspace, and does not resume on return',async()=>{
 const r=await scenario('switch')
 expect(r.before.ok,r.before.error).toBe(true)
 expect(r.away).toMatchObject(refused('profile changed'))
 expect(r.back).toMatchObject(refused('profile changed'))
},240000)

it('lets an owner disable reach the open session, and re-enable does not revive it',async()=>{
 const r=await scenario('disable')
 expect(r.before.ok,r.before.error).toBe(true)
 expect(r.disabledSave).toMatchObject(refused('grant_revoked'))
 expect(r.enabledSearch).toEqual({ok:true,value:['Decision before the owner disabled the repo']})
 // The open read session delivers nothing once disabled (the Source reports no hits, not the text).
 expect(r.disabledSearch).toEqual({ok:true,value:[]})
 expect(r.reenabledSave).toMatchObject(refused('grant_revoked'))
 expect(r.ownerStatus).toBe('Recall + maintain')
},240000)

it('reconciles an earlier process\'s unresolved save without sending it again',async()=>{
 const r=await scenario('journal')
 expect(r.save.ok,r.save.error).toBe(true)
 expect(r.stale).toEqual({ok:true,value:[]})
 expect(r.fresh).toEqual({ok:true,value:['fresh decision after a restart']})
 expect(r.pending).toBe(1)                                   // kept for inspection, never resubmitted
},240000)

it('boots DSH without Nexus when the workspace is missing',async()=>{
 const r=await scenario('missing')
 expect(r).toMatchObject({sources:0,tool:false})
},240000)
