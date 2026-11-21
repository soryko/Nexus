import {it} from 'vitest'
import {external} from './external.js'
it('compiles and mounts the external tarball consumer',()=>{
 const f=external();const binding=JSON.parse(f.run(new URL('../../../.venv/bin/python',import.meta.url).pathname.replace('/integrations/.venv/','/.venv/'),['-I',new URL('./grant_fixture.py',import.meta.url).pathname]));f.write('consumer.ts',`import {forAuditedHost} from 'dsh-mnemon-provider-nexus';
// This standalone fixture owns exactly one Source, grant and receiving project.
const provider=forAuditedHost(()=>({source:'only',enabledSources:['only'],receivingProjects:['fixture'],exposurePinnedUntilDisposal:true}));
import {mountMemorySpaceProvider,createMemorySpaceProviderFixture} from 'dsh-mnemon-source-memory-spaces/testing';
const m=await mountMemorySpaceProvider(provider,{config:{bindings:{only:${JSON.stringify(binding)}},audience:'isolated-writer',enabledSources:['only']}});
try {const {body,authority}=createMemorySpaceProviderFixture(m.descriptor,{binding:'only'},{dataDir:'.'});
const a=m.createAdapter({memoryBodies:authority,config:{timeoutMs:10000}}),request={content:'  漢 café  ',category:'fact',source:'external'} as const;
const receipt=await a.remember(body,request);if(!receipt||typeof receipt!=='object'||Array.isArray(receipt)||typeof receipt.memory_id!=='string')throw Error('invalid receipt');
const again=await a.remember(body,request);if(JSON.stringify(again)===JSON.stringify(receipt))throw Error('deduplicated new invocation');
const head=await a.get!(body,receipt.memory_id);if(head?.content!==request.content||head.source!==request.source||head.category!==request.category)throw Error('lossy get');
const page=await a.search(body,{query:'café'});if(page.results.length!==2||page.results.some(r=>r.content!==request.content||r.normalizedScore!==undefined))throw Error('lossy search');
} finally {await m.dispose();}`)
 f.run('pnpm',['exec','tsc','--module','NodeNext','--target','ES2022','--strict','--skipLibCheck','consumer.ts']);f.run(process.execPath,['consumer.js'])
},150000)
