/** The write side of the writer host: one bridge session on a root grant, with the provider's
 * operation journal. It mirrors the adapter's remember path (fresh operation key per call, one
 * retry on contention under the same key, read-only reconciliation after an ambiguous reply). */
import {Client,type Binding} from './client.js'
import {Journal,isReceipt} from './journal.js'

export interface RememberParams {content:string; category?:string; importance?:number; tags?:string[]; source?:string}
const FIELDS=new Set(['content','category','importance','tags'])

export function rememberInput(args:unknown):RememberParams {
  if(typeof args!=='object'||args===null||Array.isArray(args))throw Error('invalid arguments')
  const input=args as Record<string,unknown>
  for(const key of Object.keys(input))if(!FIELDS.has(key))throw Error(`unsupported field: ${key}`)
  if(typeof input.content!=='string'||!input.content.trim()||Buffer.byteLength(input.content)>65536)throw Error('invalid content')
  const out:RememberParams={content:input.content}
  if(input.category!==undefined){if(typeof input.category!=='string')throw Error('invalid category');out.category=input.category}
  if(input.importance!==undefined){if(!Number.isInteger(input.importance))throw Error('invalid importance');out.importance=input.importance as number}
  if(input.tags!==undefined){if(!Array.isArray(input.tags)||input.tags.length>20||input.tags.some(t=>typeof t!=='string'))throw Error('invalid tags');out.tags=input.tags as string[]}
  out.source='agent'                       // model-authored; the owner-span check is Phase 4
  return out
}

export class RootWriter {
  private client?:Client
  private journal:Journal
  private recovery?:Promise<unknown>
  private writes=new Set<Promise<unknown>>()
  private disposed=false
  constructor(private binding:Binding&{journalDir:string}){this.journal=new Journal(binding.journalDir)}

  private probe(params:object,key:string,signal:AbortSignal){
    const probe=new Client(this.binding)
    return probe.ready.then(()=>probe.call('reconcile',params,key,signal)).finally(()=>probe.dispose())
  }
  private async session(){
    if(this.disposed)throw Error('disposed')
    if(!this.client||this.client.closed){
      this.client=new Client(this.binding)
      // Settle what an earlier process left unresolved before sending anything new.
      this.recovery??=this.journal.reconcile((p,k)=>this.probe(p,k,AbortSignal.timeout(5000)),AbortSignal.timeout(5000)).catch(()=>undefined)
    }
    const [caps]=await Promise.all([this.client.ready,this.recovery])
    return {client:this.client,caps:caps as any}
  }
  async remember(params:RememberParams,signal?:AbortSignal){
    const write=(async()=>{
      const {client,caps}=await this.session()
      if(caps?.remember!==true)throw Error('read-only grant: this repo is not in maintain mode, or the session is not root')
      signal?.throwIfAborted()
      const key=this.journal.begin(params)
      const send=()=>client.call('remember',params,key,signal)
      try{
        let receipt
        try{receipt=await send()}catch(e){if(!(e instanceof Error)||!['publication_busy','authority_busy'].includes(e.message))throw e;receipt=await send()}
        if(!isReceipt(receipt))throw Error('unknown receipt')
        this.journal.finish(key);return receipt as {memory_id:string;revision_id:string}
      }catch(e){
        // A missing or malformed reply can follow a commit: ask about this one operation only.
        const receipt=await this.journal.recover(key,(p,k)=>this.probe(p,k,AbortSignal.timeout(5000))).catch(()=>undefined)
        if(receipt)return receipt as {memory_id:string;revision_id:string}
        throw e
      }
    })()
    this.writes.add(write);try{return await write}finally{this.writes.delete(write)}
  }
  async dispose(){
    this.disposed=true;this.client?.dispose()
    await Promise.allSettled([...this.writes])
    await this.journal.reconcile((p,k)=>this.probe(p,k,AbortSignal.timeout(5000)),AbortSignal.timeout(5000)).catch(()=>undefined)
  }
}
