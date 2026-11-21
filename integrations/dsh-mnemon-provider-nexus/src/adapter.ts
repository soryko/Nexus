import type {MemoryProviderAdapter,MemorySpaceProviderRuntimeContext,MemorySpace,RememberRequest} from 'dsh-mnemon-source-memory-spaces/provider-sdk'
import {Client,type Binding} from './client.js'
import {Journal,isReceipt} from './journal.js'
import {insight,rememberParams} from './codecs.js'
export interface Config {audience?:'single-project'|'isolated-writer'|'mixed'; enabledSources?:string[]; bindings:Record<string,Binding>}
// Only the host child configuration can attest to the profile inventory. Never use body settings.
export const writer=(config:Config)=>(config.audience==='single-project'||config.audience==='isolated-writer') &&
 Array.isArray(config.enabledSources)&&config.enabledSources.length===1&&typeof config.enabledSources[0]==='string'&&!!config.enabledSources[0].trim()&&Object.keys(config.bindings??{}).length===1
export function adapter(ctx:MemorySpaceProviderRuntimeContext,config:Config):MemoryProviderAdapter {
 const authority=ctx.memorySpaces??ctx.memoryBodies
 type Session={client:Client,journal?:Journal,recovery:Promise<unknown>,start:Promise<void>,binding:Binding}
 const sessions=new Map<string,Session>(),writes=new Set<Promise<unknown>>();let disposed=false
 async function recover(binding:Binding,journal:Journal,key?:string){
   const signal=AbortSignal.timeout(5000);let probe:Client|undefined
   const call=async(p:object,k:string)=>{
     signal.throwIfAborted();probe??=new Client(binding);await probe.ready
     return probe.call('reconcile',p,k,signal)
   }
   try{return key===undefined?await journal.reconcile(call,signal):await journal.recover(key,call)}
   finally{probe?.dispose()}
 }
 async function session(body:MemorySpace){
   if(disposed)throw Error('disposed');const {binding}=authority.providerConnection(body.id,ctx.providerInstanceId)
   if(typeof binding!=='string'||!Object.hasOwn(config.bindings,binding))throw Error('unmounted')
   let s=sessions.get(binding)
   if(!s||s.client.closed){const b=config.bindings[binding],journal=s?.journal??(writer(config)?new Journal(b.journalDir??''):undefined),client=new Client(b)
     const recovery=s?.recovery??(journal?recover(b,journal):Promise.resolve())
     const start=Promise.all([client.ready,recovery]).then(()=>{})
     s={client,journal,recovery,start,binding:b};sessions.set(binding,s)
   }
   await s.start;return s
 }
 async function remember(body:MemorySpace,req:RememberRequest,signal?:AbortSignal){
   if(!writer(config))throw Error('read-only audience');const {client,journal,binding}=await session(body)
   const caps=await client.ready
   if(!caps||typeof caps!=='object'||!('remember' in caps)||caps.remember!==true)throw Error('read-only grant')
   const params=rememberParams(body.id,req);signal?.throwIfAborted();const key=journal!.begin(params)
   const send=()=>client.call('remember',params,key,signal)
   try{let receipt
     try{receipt=await send()}catch(e){if(!(e instanceof Error)||!['publication_busy','authority_busy'].includes(e.message))throw e;receipt=await send()}
     if(!isReceipt(receipt))throw Error('unknown receipt')
     journal!.finish(key);return receipt
   }catch(e){
     // Even a missing/malformed reply can follow a commit. Query only this durable record.
     const receipt=await recover(binding,journal!,key).catch(()=>undefined)
     if(receipt)return receipt
     throw e
   }
 }
 const unsupported=async()=>{throw Error('unsupported capability')}
 return {id:ctx.manifest.typeId,scoreSemantics:{kind:'provider-native'},graph:unsupported,list:unsupported,
   async status(body,signal){await (await session(body)).client.call('status',{},undefined,signal);return {healthy:true}},
   async get(body,id,signal){try{const {client,binding}=await session(body);return insight(await client.call('get',{memory_id:id},undefined,signal),binding.atlasOrigin)}catch(e){if(e instanceof Error&&e.message==='not_found')return undefined;throw e}},
   async search(body,req,signal){if(req.category||req.source||req.intent||(req.memoryBodyIds&&!req.memoryBodyIds.includes(body.id)))throw Error('unsupported filter');const {client,binding}=await session(body);const page=await client.call('search',{query:req.query,limit:req.limit??10},undefined,signal);return {results:page.hits.map((hit:any)=>({...insight(hit,binding.atlasOrigin),score:hit.lexical_rank??undefined,matchedVia:'lexical'}))}},
   async remember(body,req,signal){const write=remember(body,req,signal);writes.add(write);try{return await write}finally{writes.delete(write)}},
   async dispose(){disposed=true;for(const s of sessions.values())s.client.dispose();await Promise.allSettled([...writes,...[...sessions.values()].map(s=>s.recovery)]);await Promise.allSettled([...sessions.values()].filter(s=>s.journal).map(s=>recover(s.binding,s.journal!)));sessions.clear()}
 }
}
