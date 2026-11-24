// Test-only DSH startup overlay. Create a synthetic root chat without contacting a model.
import {appendFileSync,existsSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
export const name='inspector-test-fixture';
export const inject=['agents','tools'];
export function apply(ctx) {
  const timer=setInterval(async()=>{if(!existsSync(join(process.env.HOME,'start-seed')))return;clearInterval(timer);try{await seed(ctx)}catch(error){console.error('Fixture seed failed:',error)}},100);
  ctx.effect(()=>()=>clearInterval(timer));
}
async function seed(ctx) {
  const dir=process.env.HOME;
  if(process.env.DSH_HOME!==join(dir,'dsh') || process.cwd()!==dir || !dir.includes('nx-inspector-')) throw Error('fixture requires disposable home');
  const {agent}=await ctx.agents.create({sessionId:'session-inspector-rehearsal',meta:{cwd:dir}});
  const s=agent.session;
  const msg=(text,source)=>({id:randomUUID(),role:'user',source,content:[{type:'text',text}]});
  s.append('turn/start',{turn:1});
  const {seq}=s.append('user/message',msg('Rehearsal: save the amber checkpoint.',{kind:'user'}),{surfaceOp:'append'});
  const key='dsh-memory:'+createHash('sha256').update(`${s.id}:${seq}`).digest('hex');
  const call=async(name,args)=>{
    const r=await ctx.tools.execute({agent,signal:AbortSignal.timeout(15000),calls:[{type:'tool-call',id:randomUUID(),name:'mcp__nexus__'+name,arguments:JSON.stringify(args)}]});
    const out=r.results[0]?.result;if(out?.isError!==false||!out.value?.structuredContent)throw Error('failed synthetic '+name+': '+JSON.stringify(out?.content));return out.value.structuredContent;
  };
  const original='  Amber checkpoint: café\n\t<saved literally>\n';
  const saved=await call('record',{content:original,source_uri:`dsh-session:${s.id}/event/${seq}`,idempotency_key:key});
  const {seq:receiptSeq}=s.append('agent/inbox/spliced',{target:'next-turn',start:0,inserted:[msg('Synthetic capture confirmed.',{kind:'nexus-memory',form:'notice',operation:'capture',outcome:'success',key,memoryId:saved.memory_id,revisionId:saved.revision_id})]});
  s.append('agent/inbox/spliced',{target:'next-turn',start:0,removedCount:1,inserted:[]});
  s.append('turn/end',{turn:1,reason:{kind:'completed'}});
  s.append('session/title',{title:'Amber checkpoint rehearsal',messageSeqs:[seq],source:'fallback'});
  const newer=await call('revise',{memory_id:saved.memory_id,expected_revision_id:saved.revision_id,content:'Different current text.',idempotency_key:'synthetic-revision'});
  // Observer only: no RPC stub or replacement tool. Record qualified calls after seeding.
  ctx.on('tools/post-execute',async(exec,result,next)=>{
    if(exec.name.startsWith('mcp__nexus__'))appendFileSync(join(dir,'observed.jsonl'),JSON.stringify({name:exec.name,arguments:exec.arguments,isError:result.isError})+'\n');
    return next();
  });
  writeFileSync(join(dir,'seed.json'),JSON.stringify({original,originSeq:seq,receiptSeq,session:s.id,memoryId:saved.memory_id,savedRevision:saved.revision_id,currentRevision:newer.revision_id}));
}
