import {it,expect} from 'vitest'
import {mountMemorySpaceProvider,createMemorySpaceProviderFixture} from 'dsh-mnemon-source-memory-spaces/testing'
import {forAuditedHost} from '../src/index.js'
import {Client} from '../src/client.js'
import {Journal} from '../src/journal.js'
import {mkdtempSync,readdirSync,readFileSync,writeFileSync} from 'node:fs'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
const spawn=(script:string)=>({executable:process.execPath,args:['-e',script]})
it.each([`console.log('not json')`,`process.stdout.write('x'.repeat(1048577))`,`setInterval(()=>{},10000)`])('bounds malformed, oversized or silent startup',async script=>{
 const c=new Client(spawn(script),250,500);try{await expect(c.ready).rejects.toThrow()}finally{c.dispose()}
})
it('bounds pending requests, cancellation, disposal and response deadlines',async()=>{
 const c=new Client(spawn(`process.stdin.once('data',d=>{console.log(JSON.stringify({v:1,id:JSON.parse(d).id,ok:true,result:{}}));process.stdin.resume()})`),1000,250)
 await c.ready;const ac=new AbortController();const pending=Array.from({length:16},()=>c.call('get',{},undefined,ac.signal).catch(e=>e));await expect(c.call('get',{})).rejects.toThrow();ac.abort();expect(await Promise.all(pending)).toHaveLength(16);await expect(c.call('get',{})).rejects.toThrow();c.dispose();await expect(c.call('status',{})).rejects.toThrow()
})
it('keeps timeout-after-commit unknown until read-only recovery; absence never sends',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'nexus-journal-')), journal=new Journal(dir),params={content:' exact 雪 '}
 const key=journal.begin(params);const file=join(dir,'fixture-receipt')
 const c=new Client(spawn(`const fs=require('node:fs');require('node:readline').createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);if(r.method==='remember'){fs.writeFileSync(${JSON.stringify(file)},r.operation_key);return}console.log(JSON.stringify({v:1,id:r.id,ok:true,result:{}}))})`),1000,150)
 await c.ready;await expect(c.call('remember',params,key)).rejects.toThrow('unknown');expect(readFileSync(file,'utf8')).toBe(key)
 const calls:string[]=[];await journal.reconcile(async(p,k)=>{expect(p).toEqual(params);calls.push(k);return {state:'absent_at_snapshot'}},new AbortController().signal)
 expect(readdirSync(dir)).toContain(key);await journal.reconcile(async(_,k)=>{calls.push(k);return {state:'committed',receipt:{memory_id:'m',revision_id:'r'}}},new AbortController().signal)
 expect(calls).toEqual([key,key]);expect(readdirSync(dir)).not.toContain(key)
})
it('keeps unresolved entries and continues checking other original keys after a probe error',async()=>{
 const dir=mkdtempSync(join(tmpdir(),'nexus-scan-')),j=new Journal(dir)
 for(let i=0;i<3;i++)j.begin({content:String(i)})
 const keys=readdirSync(dir),seen:string[]=[]
 await j.reconcile(async(_,k)=>{seen.push(k);if(k===keys[0])throw Error('idempotency_conflict');return {state:'committed',receipt:{memory_id:k===keys[1]?'':'m',revision_id:'r'}}},new AbortController().signal)
 expect(seen).toEqual(keys);expect(readdirSync(dir)).toEqual(keys.slice(0,2))
})
it('keeps an interrupted startup probe pending without breaking the mounted reader',async()=>{
 const root=mkdtempSync(join(tmpdir(),'nexus-restart-')), journalDir=join(root,'journal'), log=join(root,'requests')
 const params={content:' exact 雪 ',tags:['two','one']},key=new Journal(journalDir).begin(params)
 const script=`const fs=require('node:fs');require('node:readline').createInterface({input:process.stdin}).on('line',l=>{
  const r=JSON.parse(l);fs.appendFileSync(${JSON.stringify(log)},l+'\\n');if(r.method==='reconcile'&&!fs.existsSync(${JSON.stringify(join(root,'available'))}))process.exit(0);
  const result=r.method==='reconcile'?{state:'committed',receipt:{memory_id:'m',revision_id:'r'}}:{remember:true};console.log(JSON.stringify({v:1,id:r.id,ok:true,result}))})`
 const provider=forAuditedHost(()=>({source:'project',enabledSources:['project'],receivingProjects:['test'],exposurePinnedUntilDisposal:true}))
 const boot=async()=>{
 const m=await mountMemorySpaceProvider(provider,{config:{bindings:{only:{...spawn(script),journalDir}},audience:'isolated-writer'}})
 try{
  const {body,authority}=createMemorySpaceProviderFixture(m.descriptor,{binding:'only'},{dataDir:root})
  const a=m.createAdapter({memoryBodies:authority,config:{timeoutMs:10000}})
  expect(await a.status!(body)).toMatchObject({healthy:true})
 }finally{await m.dispose()}
 }
 await boot();expect(readdirSync(journalDir)).toEqual([key])
 writeFileSync(join(root,'available'),'');await boot()
 const calls=readFileSync(log,'utf8').trim().split('\n').map(l=>JSON.parse(l)).filter(r=>r.method!=='capabilities')
 expect(calls.map(r=>r.method)).toEqual(['reconcile','status','reconcile','reconcile','status'])
 for(const r of calls.filter(r=>r.method==='reconcile'))expect(r).toMatchObject({params,operation_key:key})
 expect(readdirSync(journalDir)).toEqual([])
})
