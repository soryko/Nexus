import type {Insight,RememberRequest} from 'dsh-mnemon-source-memory-spaces/provider-sdk'
export function rememberParams(body:string,request:RememberRequest){
 const {memoryBodyId,...params}=request
 if(memoryBodyId!==undefined && memoryBodyId!==body)throw Error('body mismatch')
 if(typeof params.content!=='string'||!params.content||Buffer.byteLength(params.content)>65536)throw Error('invalid content')
 return JSON.parse(JSON.stringify(params)) as typeof params
}
export function insight(row:any,origin?:string):Insight {
 if(typeof row?.memory_id!=='string'||typeof row.content!=='string')throw Error('invalid bridge memory')
 let externalUri:string|undefined
 if(origin!==undefined){
   const url=new URL(origin);if(url.origin!==origin||url.protocol!=='http:'||url.hostname!=='127.0.0.1'||!url.port)throw Error('invalid Atlas origin')
   for(const id of [row.memory_id,row.revision_id,row.space_id])if(typeof id!=='string'||!/^[\w:-]{1,128}$/.test(id))throw Error('invalid receipt')
   url.pathname=`/api/v2/memories/${row.memory_id}/revisions/${row.revision_id}`;url.searchParams.set('space',row.space_id);externalUri=url.href
 }
 const meta=row.compat??{}
 return {id:row.memory_id,content:row.content,tags:row.tags,createdAt:row.created_at,externalUri,
   category:meta.category??(row.kind==='decision'?'decision':undefined),importance:meta.importance,entities:meta.entities,source:meta.source}
}
