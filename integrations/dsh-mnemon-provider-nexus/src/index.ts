import {defineMemorySpaceProvider, type MemorySpaceProviderHost} from 'dsh-mnemon-source-memory-spaces/provider-sdk'
import {adapter,writer,type Config} from './adapter.js'
import {manifest} from './descriptor.js'

/** Programmatic composition only. The embedding owner must obtain this snapshot
 * from the complete, enforced host generation for this exact bound host, and
 * drain its child before changing exposure. Unknown or mutable exposure is not
 * attestable. Never build this callback from profile/child or View inputs. */
export interface PinnedAudience {
  source: string
  enabledSources: readonly string[]
  receivingProjects: readonly string[]
  exposurePinnedUntilDisposal: true
}
export function forAuditedHost(inspect: (host: MemorySpaceProviderHost) => PinnedAudience | undefined) {
  return defineMemorySpaceProvider<Config>({id:'nexus',apply(ctx,host,config){
    const bindings=structuredClone(config?.bindings ?? {})
    let options: Config={bindings}
    if(config?.audience==='single-project'||config?.audience==='isolated-writer'){
      const proof=inspect(host)
      if(proof?.exposurePinnedUntilDisposal===true && typeof proof.source==='string' && proof.source.trim() &&
        Array.isArray(proof.enabledSources) && proof.enabledSources.length===1 && proof.enabledSources[0]===proof.source &&
        Array.isArray(proof.receivingProjects) && proof.receivingProjects.length===1 &&
        typeof proof.receivingProjects[0]==='string' && proof.receivingProjects[0].trim() && Object.keys(bindings).length===1){
        options={bindings,audience:config.audience,enabledSources:[proof.source]}
      }
    }
    // One child is one grant boundary: even a persisted body.connection is
    // merged by the SDK and must not be able to select a sibling grant.
    if(Object.keys(bindings).length>1) throw Error('configure one grant binding per Nexus child')
    host.install(ctx,{manifest:manifest(writer(options)),create:context=>({
      ...adapter(context,options),
      async discover({binding}) {
        if(typeof binding!=='string'||!Object.hasOwn(bindings,binding)) throw Error('unmounted binding')
        return [{externalId:binding,name:'Nexus memory',description:'Owner-bound Nexus memory space',connection:{}}]
      },
    })})
  }})
}
// The pinned declarative SDK does not expose complete profile/context inventory.
export default forAuditedHost(()=>undefined)
