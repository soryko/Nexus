import {afterEach} from 'vitest'
import {mkdtempSync,writeFileSync,readdirSync,realpathSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {execFileSync} from 'node:child_process'
const roots=new Set<string>()
afterEach(()=>{for(const path of roots){if(realpathSync(path)!==path||!path.startsWith(realpathSync(tmpdir())+'/nexus-packed-'))throw Error('unexpected fixture root');rmSync(path,{recursive:true});roots.delete(path)}})
export function external(loader=false){
 const dir=mkdtempSync(join(tmpdir(),'nexus-packed-'));const env={HOME:dir,PATH:process.env.PATH,DSH_HOME:join(dir,'home'),MNEMON_DATA_DIR:join(dir,'data'),DSH_TELEMETRY_DISABLED:'1'}
 roots.add(realpathSync(dir))
 const run=(exe:string,args:string[],cwd=dir)=>execFileSync(exe,args,{cwd,env,encoding:'utf8',timeout:120000,stdio:'pipe'})
 run('pnpm',['pack','--pack-destination',dir],process.cwd());const pack=readdirSync(dir).find(x=>x.endsWith('.tgz'))!
 writeFileSync(join(dir,'package.json'),JSON.stringify({private:true,type:'module',dependencies:{'dsh-mnemon-provider-nexus':'file:'+pack,'@deepseek-ai/cordis':'4.0.5','dsh-mnemon':'0.5.50','dsh-mnemon-source-memory-spaces':'0.5.31','typescript':'5.9.3',...(loader?{'@deepseek-ai/dsh':'0.2.1-rc.5'}:{})},pnpm:{overrides:{'@deepseek-ai/cordis':'4.0.5'}}}))
 run('pnpm',['install','--ignore-scripts','--store-dir',process.env.NEXUS_FIXTURE_STORE??join(dir,'store')]);return {dir,run,write:(name:string,data:string)=>writeFileSync(join(dir,name),data)}
}
