import {MEMORY_SPACE_PROVIDER_API_VERSION,type MemorySpaceProviderManifest} from 'dsh-mnemon-source-memory-spaces/provider-sdk'
export function manifest(writable:boolean):MemorySpaceProviderManifest {return {
 apiVersion:MEMORY_SPACE_PROVIDER_API_VERSION,kind:'provider',typeId:'nexus',packageName:'dsh-mnemon-provider-nexus',version:'0.1.0',label:'Nexus memory',icon:{kind:'glyph',value:'N'},summary:'Read grant-bound Nexus memories and optionally record exact content.',origin:'third-party',locality:'local',workspaceBinding:'provider-global',
 capabilities:{search:true,browse:false,graph:false,entities:false,related:false,remember:writable,link:false,forget:false,writeMode:'exact',deletionMode:'unsupported'},
 fields:[{key:'binding',label:'Owner binding',scope:'service',input:'text',required:true}],secrets:[],scoreSemantics:'provider-native'
}}
