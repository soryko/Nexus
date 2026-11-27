import {expect,it} from 'vitest'
import {mkdirSync,readFileSync} from 'node:fs'
import {join} from 'node:path'
import {external} from './external.js'

const python=new URL('../../../.venv/bin/python',import.meta.url).pathname
// The receiver stands in for dsh-mnemon's memory service and DSH's tool registry. Agents are the
// session headers DSH gives: the root, a subagent, mnemon's delegated writer (the child that
// performed the first real save), a fork, and a root session of another workspace.
const receiver=(workspace:string)=>`export const sources=new Map(),tools=new Map();export const provide=['mnemonMemory','tools'];
const ws=${JSON.stringify(workspace)}
export const header={root:{cwd:ws},worker:{cwd:ws,origin:'subagent',parentSession:'root',delegationDepth:1},
 delegated:{cwd:ws,origin:'subagent',parentSession:'root',isSeeded:false,delegationDepth:1},fork:{cwd:ws,parentSession:'root',isSeeded:true},elsewhere:{cwd:'/'}}
export function apply(ctx){
 ctx.provide('tools',{register(d){if(tools.has(d.name))throw Error('duplicate tool');tools.set(d.name,d);return ()=>tools.delete(d.name)}})
 ctx.provide('mnemonMemory',{installContributions(c,options){const key=options.instanceId;if(c.sources.length!==1||sources.has(key))throw Error('unexpected sources');sources.set(key,c.sources[0]);return ()=>sources.delete(key)}})}`

function boot(mode:'pinned'|'shared',ceiling:'maintain'|'recall'='maintain'){
 const f=external(true),dir=join(f.dir,'nexus');mkdirSync(dir)
 const project=JSON.parse(f.run(python,['-I',new URL('./project_fixture.py',import.meta.url).pathname,dir,...(ceiling==='recall'?['recall']:[])]))
 f.write('project.json',JSON.stringify(project));f.write('receiver.mjs',receiver(project.workspace))
 f.write('boot.mjs',readFileSync(new URL('./writer-host-boot.mjs',import.meta.url),'utf8'))
 const out=f.run(process.execPath,['boot.mjs',mode]).trim().split('\n')
 return JSON.parse(out[out.length-1])
}
const refused=(why:string)=>({ok:false,error:expect.stringContaining(why)})

it('saves only through the tool, from the root session of its one workspace',()=>{
 const r=boot('pinned')
 expect(r.enabledByHost).toBe(true)
 expect(r.sourceRemember).toBe(false)                         // mnemon sees a read-only Source
 expect(r.viaSource).toMatchObject(refused('nexus_remember'))
 expect(r.root.ok,r.root.error).toBe(true)
 expect(r.root.value).toMatchObject({memory_id:expect.any(String),revision_id:expect.any(String)})
 for(const who of ['worker','delegated','fork','elsewhere'])expect(r[who]).toMatchObject(refused('root session'))
 expect(r.query).toEqual({ok:true,value:['Phase 2 fixture: the staging database is SQLite']})
 expect(r.otherWorkspace).toMatchObject(refused('another workspace'))
 expect(r.afterChange).toMatchObject(refused('profile changed'))
},240000)

it('refuses to save when another loader entry names Nexus',()=>{
 const r=boot('shared')
 expect(r.root).toMatchObject(refused('profile changed'))
},240000)

it('refuses to save on a repo in recall-only mode even when the host vouches for it',()=>{
 const r=boot('pinned','recall')
 expect(r.root).toMatchObject(refused('read-only grant'))
 expect(r.query).toEqual({ok:true,value:[]})
},240000)
