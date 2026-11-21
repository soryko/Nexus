import {createHash,randomUUID} from 'node:crypto'
import {closeSync,fsyncSync,lstatSync,mkdirSync,openSync,readFileSync,readdirSync,unlinkSync,writeFileSync} from 'node:fs'
import {isAbsolute,join} from 'node:path'
function digest(params:object){return createHash('sha256').update(JSON.stringify(['remember',Object.entries(params).sort(([a],[b])=>a<b?-1:a>b?1:0)])).digest('hex')}
export function isReceipt(value:any){return typeof value?.memory_id==='string' && !!value.memory_id.trim() && typeof value.revision_id==='string' && !!value.revision_id.trim()}
export class Journal {
 constructor(private dir:string){if(!isAbsolute(dir))throw Error('absolute journal required');mkdirSync(dir,{recursive:true,mode:0o700});const s=lstatSync(dir);if(!s.isDirectory()||s.uid!==process.getuid?.()||(s.mode&0o077))throw Error('private journal required')}
 private sync(){const fd=openSync(this.dir,'r');try{fsyncSync(fd)}finally{closeSync(fd)}}
 begin(params:object){const key=randomUUID();const fd=openSync(join(this.dir,key),'wx',0o600);try{writeFileSync(fd,JSON.stringify({params,digest:digest(params)}));fsyncSync(fd)}finally{closeSync(fd)}this.sync();return key}
 finish(key:string){unlinkSync(join(this.dir,key));this.sync()}
 async reconcile(call:(params:object,key:string)=>Promise<any>,signal:AbortSignal){
   for(const key of readdirSync(this.dir)){
     if(signal.aborted)break
     if(!/^[0-9a-f-]{36}$/.test(key))continue
     try{await this.recover(key,call)}catch{
       // Leave the entry for later inspection. One unresolved key must not block the rest.
     }
   }
 }
 async recover(key:string,call:(params:object,key:string)=>Promise<any>){const {params,digest:stored}=JSON.parse(readFileSync(join(this.dir,key),'utf8'));if(digest(params)!==stored)throw Error('invalid journal digest');const result=await call(params,key);if(result?.state==='committed'&&isReceipt(result.receipt)){this.finish(key);return result.receipt}}
}
