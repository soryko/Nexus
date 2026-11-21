import {it} from 'vitest'
import {mkdirSync,readFileSync} from 'node:fs'
import {join} from 'node:path'
import {execFileSync} from 'node:child_process'
import {external} from './external.js'

it('co-mounts two Sources with identical child and alias IDs under the real Loader',()=>{
 const f=external(true)
 const entries=['work','other'].map(id=>{
  const dir=join(f.dir,id);mkdirSync(dir)
  const binding=JSON.parse(f.run(new URL('../../../.venv/bin/python',import.meta.url).pathname,['-I',new URL('./grant_fixture.py',import.meta.url).pathname],dir))
  const response=execFileSync(binding.executable,binding.args,{cwd:dir,env:{HOME:dir,PATH:process.env.PATH},encoding:'utf8',input:JSON.stringify({v:1,id:'seed',method:'remember',operation_key:'isolated-seed',params:{content:'Loader fixture '+id}})+'\n'})
  if(!JSON.parse(response).ok)throw Error(response)
  return {id,name:'dsh-mnemon-source-memory-spaces',config:{dataDir:join(dir,'data'),providers:[{use:'dsh-mnemon-provider-nexus',instanceId:'same-child',config:{audience:'isolated-writer',enabledSources:[id],bindings:{only:binding}}}]}}
 })
 f.write('sources.json',JSON.stringify(entries))
 f.write('receiver.mjs',`export const sources=new Map();export const provide=['mnemonMemory'];export function apply(ctx){ctx.provide('mnemonMemory',{installContributions(c,options){const key=options.instanceId;if(c.sources.length!==1||sources.has(key))throw Error('unexpected sources');sources.set(key,c.sources[0]);return ()=>sources.delete(key)}})}`)
 f.write('boot.mjs',readFileSync(new URL('./loader-operations.mjs',import.meta.url),'utf8'))
 f.run(process.execPath,['boot.mjs'])
},150000)
