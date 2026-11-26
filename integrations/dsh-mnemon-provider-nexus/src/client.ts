import {spawn} from 'node:child_process'
import {randomUUID} from 'node:crypto'
import {isAbsolute} from 'node:path'
export interface Binding { executable:string; args:string[]; journalDir?:string; atlasOrigin?:string }
const MAX=1024*1024
export class Client {
  private child
  private buffer=Buffer.alloc(0)
  private pending=new Map<string,(error:Error|null,value?:unknown)=>void>()
  closed=false
  readonly ready:Promise<unknown>
  constructor(binding:Binding,startup=5000,private timeout=10000){
    if(!isAbsolute(binding.executable))throw Error('absolute executable required')
    this.child=spawn(binding.executable,binding.args,{shell:false,stdio:['pipe','pipe','ignore'],env:{PATH:process.env.PATH,LANG:'C.UTF-8'}})
    this.child.on('error',()=>this.dispose());this.child.on('exit',()=>this.dispose());this.child.stdin.on('error',()=>this.dispose())
    this.child.stdout.on('data',(chunk:Buffer)=>{try{this.receive(chunk)}catch{this.dispose()}})
    this.ready=this.send('capabilities',{},undefined,undefined,startup)
  }
  private receive(chunk:Buffer){
    this.buffer=Buffer.concat([this.buffer,chunk]);let end:number
    while((end=this.buffer.indexOf(10))!==-1){
      if(end>=MAX)throw Error();const r=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(this.buffer.subarray(0,end)))
      this.buffer=this.buffer.subarray(end+1);const finish=this.pending.get(r.id)
      if(!finish||r.v!==1||typeof r.ok!=='boolean'||(r.ok?!('result' in r)||'error' in r:typeof r.error?.code!=='string'||'result' in r))throw Error()
      finish(r.ok?null:new Error(/^[a-z_]{1,48}$/.test(r.error.code)?r.error.code:'bridge_error'),r.result)
    }
    if(this.buffer.length>=MAX)throw Error()
  }
  call(method:string,params:object,key?:string,signal?:AbortSignal){return this.send(method,params,key,signal,this.timeout)}
  private async send(method:string,params:object,key:string|undefined,signal:AbortSignal|undefined,ms:number):Promise<any>{
    if(this.closed||signal?.aborted||this.pending.size>=16)throw Error('bridge unavailable')
    const id=randomUUID();const line=JSON.stringify({v:1,id,method,params,operation_key:key})+'\n'
    if(Buffer.byteLength(line)>MAX)throw Error('request too large')
    return new Promise((resolve,reject)=>{
      const abort=()=>this.dispose();const timer=setTimeout(abort,ms)
      signal?.addEventListener('abort',abort,{once:true})
      this.pending.set(id,(error,value)=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);this.pending.delete(id);if(error)reject(error);else resolve(value)})
      this.child.stdin.write(line)
    })
  }
  dispose(){if(this.closed)return;this.closed=true;this.child.kill('SIGKILL');for(const finish of this.pending.values())finish(Error('bridge outcome unknown'))}
}
