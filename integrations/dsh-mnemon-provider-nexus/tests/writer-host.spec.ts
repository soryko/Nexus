import {expect,it} from 'vitest'
import {mkdirSync,readFileSync} from 'node:fs'
import {join} from 'node:path'
import {external} from './external.js'

const python=new URL('../../../.venv/bin/python',import.meta.url).pathname
// The receiver stands in for dsh-mnemon's memory service and for DSH's live agent registry:
// a root session, a subagent and a fork, each in the fixture workspace.
const receiver=(workspace:string)=>`export const sources=new Map();export const provide=['mnemonMemory','agents'];
const header={root:{cwd:${JSON.stringify(workspace)}},worker:{cwd:${JSON.stringify(workspace)},origin:'subagent',parentSession:'root',delegationDepth:1},fork:{cwd:${JSON.stringify(workspace)},parentSession:'root',isSeeded:true}}
export function apply(ctx){
 ctx.provide('agents',{get:id=>header[id]?{id,session:{header:header[id]}}:undefined})
 ctx.provide('mnemonMemory',{installContributions(c,options){const key=options.instanceId;if(c.sources.length!==1||sources.has(key))throw Error('unexpected sources');sources.set(key,c.sources[0]);return ()=>sources.delete(key)}})}`

function boot(mode:'pinned'|'shared',ceiling:'maintain'|'recall'='maintain'){
 const f=external(true),dir=join(f.dir,'nexus');mkdirSync(dir)
 const project=JSON.parse(f.run(python,['-I',new URL('./project_fixture.py',import.meta.url).pathname,dir,...(ceiling==='recall'?['recall']:[])]))
 f.write('project.json',JSON.stringify(project));f.write('receiver.mjs',receiver(project.workspace))
 f.write('boot.mjs',readFileSync(new URL('./writer-host-boot.mjs',import.meta.url),'utf8'))
 const out=f.run(process.execPath,['boot.mjs',mode]).trim().split('\n')
 return JSON.parse(out[out.length-1])
}

it('writes from the root session of its one workspace, and nothing else writes',()=>{
 const r=boot('pinned')
 expect(r.enabledByHost).toBe(true)
 expect(r.remember).toBe(true)
 expect(r.offers).toBe(true)
 expect(r.root.ok,r.root.error).toBe(true)
 expect(r.query).toEqual({ok:true,value:['Phase 2 fixture: the staging database is SQLite']})
 for(const refused of [r.subagent,r.fork])expect(refused).toMatchObject({ok:false,error:expect.stringContaining('root session')})
 expect(r.archive).toMatchObject({ok:false,error:expect.stringContaining('only remember')})
 expect(r.otherWorkspace).toMatchObject({ok:false,error:expect.stringContaining('another workspace')})
 expect(r.afterChange).toMatchObject({ok:false,error:expect.stringContaining('profile changed')})
},240000)

it('stays read-only when another loader entry names Nexus',()=>{
 const r=boot('shared')
 expect(r.remember).toBe(false)
 expect(r.root.ok).toBe(false)
},240000)

it('stays read-only on a repo in recall-only mode even when the host vouches for it',()=>{
 const r=boot('pinned','recall')
 expect(r.root.ok).toBe(false)
 expect(r.query).toEqual({ok:true,value:[]})
},240000)
