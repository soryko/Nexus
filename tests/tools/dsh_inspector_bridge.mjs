// Test-only DSH session/gateway shim. The Python test dispatches the actual MCP get.
import { createInterface } from 'node:readline/promises';
import { createHash } from 'node:crypto';
import { openReceipt } from '../../integrations/dsh-inspector/host.js';
const lines = createInterface({input:process.stdin})[Symbol.asyncIterator]();
const next = async () => JSON.parse((await lines.next()).value);
const send = obj => process.stdout.write(JSON.stringify(obj)+'\n');
const {memory_id:memoryId,revision_id:revisionId}=await next();
const id='disposable-inspector-session', key='dsh-memory:'+createHash('sha256').update(id+':1').digest('hex');
const events=[['turn/start',{turn:1}],['user/message',{role:'user',source:{kind:'user'}}],['turn/end',{turn:1,reason:{kind:'completed'}}],['agent/inbox/spliced',{inserted:[{role:'user',source:{kind:'nexus-memory',form:'notice',operation:'capture',outcome:'success',memoryId,revisionId,key,budgetTurn:1}}]}]].map(([type,data],seq)=>({seq,type,data}));
const agent={id, session:{id,header:{id,isSeeded:false},inheritedEventCount:0,isOwnSeq:i=>!!events[i],eventAt:i=>events[i]}};
const ctx={tools:{executable:n=>n==='mcp__nexus__get'},agents:{get:k=>k===id?agent:undefined,withInitiator:(_,fn)=>fn()}};
send(await openReceipt(ctx,agent,3,new AbortController().signal,async ({calls:[call]})=>{send({name:call.name,args:JSON.parse(call.arguments)});const value=await next();return {results:[{callId:call.id,result:{isError:false,value:{structuredContent:value}}}]}}));
