// Phase 2 exit scenarios for the writer host under the real Loader. argv: <scenario> [peer workspace]
//   isolated  two processes in unrelated repos save at the same moment (barrier on ready files)
//   switch    the process leaves its workspace after a save
//   disable   the owner disables the repo, then re-enables it, while the session stays open
//   journal   an unresolved operation left by an earlier process is reconciled, never re-sent
//   missing   the configured workspace does not exist
import {createHash,randomUUID} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import {mkdirSync,readdirSync,writeFileSync} from 'node:fs'
import {basename,dirname,join} from 'node:path'
import {setTimeout as sleep} from 'node:timers/promises'
import {bootProfile,openSource} from './boot-setup.mjs'

const [scenario,peer]=process.argv.slice(2)
const results={scenario}
const booted=await bootProfile(scenario==='missing'?{host:{workspace:join(process.cwd(),'gone','repo')}}:{})
const {ctx,fixture,workspace}=booted
const owner=(...args)=>execFileSync(fixture.launcher[0],[...fixture.launcher.slice(1),...args],{env:{HOME:fixture.home,PATH:process.env.PATH},stdio:'pipe'})
let source
try {
  if(scenario==='missing'){
    results.sources=booted.sources.size
    results.tool=booted.tools.has('nexus_remember')
  } else {
    if(scenario==='journal'){
      // The format the provider's journal writes (src/journal.ts): a pending remember, digest-checked.
      const params={content:'stale journal entry from an earlier process',category:'decision',source:'agent'}
      const digest=createHash('sha256').update(JSON.stringify(['remember',Object.entries(params).sort(([a],[b])=>a<b?-1:a>b?1:0)])).digest('hex')
      const dir=fixture.binding.journalDir;mkdirSync(dir,{recursive:true,mode:0o700})
      writeFileSync(join(dir,randomUUID()),JSON.stringify({params,digest}),{mode:0o600})
    }
    source=await openSource(booted)
    const name=basename(workspace)
    if(scenario==='isolated'){
      writeFileSync(join(process.cwd(),'..','ready-'+name),'')            // barrier: both sessions are open
      for(let i=0;i<1200&&readdirSync(join(process.cwd(),'..')).filter(f=>f.startsWith('ready-')).length<2;i++)await sleep(50)
      results.save=await source.save('root',`Decision for ${name}: its staging database stays local`)
      writeFileSync(join(process.cwd(),'..','saved-'+name),'')            // both saves are in before searching
      for(let i=0;i<1200&&readdirSync(join(process.cwd(),'..')).filter(f=>f.startsWith('saved-')).length<2;i++)await sleep(50)
      results.search=await source.search('staging database')
      results.peer=await source.search('staging database',peer)
    }
    if(scenario==='switch'){
      results.before=await source.save('root','Decision before the switch')
      process.chdir(dirname(workspace))
      results.away=await source.save('root','saved from outside the workspace')
      process.chdir(workspace)
      results.back=await source.save('root','saved after coming back')
    }
    if(scenario==='disable'){
      results.before=await source.save('root','Decision before the owner disabled the repo')
      results.enabledSearch=await source.search('Decision')            // opens the read session first
      owner('disable',workspace)
      results.disabledSave=await source.save('root','saved while disabled')
      results.disabledSearch=await source.search('Decision')
      owner('enable',workspace);owner('mode',workspace,'maintain')
      results.reenabledSave=await source.save('root','saved by the old session after re-enable')
    }
    if(scenario==='journal'){
      results.save=await source.save('root','fresh decision after a restart')
      results.stale=await source.search('stale journal entry')
      results.fresh=await source.search('fresh decision')
      results.pending=readdirSync(fixture.binding.journalDir).length
    }
  }
} finally {
  try { await source?.runtime?.dispose?.() } finally { await ctx.fiber.dispose() }
}
if(scenario==='disable'){
  // A new process after re-enable is a new session with a new grant: it may save again.
  results.ownerStatus=JSON.parse(owner('status',workspace).toString()).mode
}
console.log(JSON.stringify(results))
