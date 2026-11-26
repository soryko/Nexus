import { renderPack, renderRevision } from '/paths.js';
const $ = id => document.getElementById(id);

export async function startSpaces() {
  const initial = await fetch('/api/v2/spaces');
  if (initial.status === 404) return false; // legacy bound project
  if (!initial.ok) throw Error('session');
  document.querySelector('main').hidden = true;
  $('back').hidden = true; $('space-atlas').hidden = false;
  let generation = 0, controller, approval, edits = 0, packSequence = 0, refreshSequence = 0, csrf;
  const choice = $('space-choice'), status = $('status');
  function invalidate() {
    edits++; approval = null; $('preview').textContent = ''; $('publish').disabled = true;
  }
  function clear() {
    generation++; controller?.abort(); controller = new AbortController();
    window.getSelection()?.removeAllRanges();
    $('path-details').replaceChildren(); $('pack-find').reset(); $('command-form').reset(); invalidate();
    status.textContent = choice.value ? `Active space: ${choice.selectedOptions[0].textContent}` : 'No permitted space.';
  }
  function options(data, id) {
    choice.replaceChildren(); csrf = data.csrf;
    for (const s of data.spaces) choice.add(new Option(`${s.label} (${s.mode.replaceAll('_',' ')}${s.default ? ', default destination' : ''})`,s.id));
    if (data.spaces.some(s=>s.id===id)) choice.value=id;
    if (!id || choice.value !== id) clear();
    else status.textContent = `Active space: ${choice.selectedOptions[0].textContent}`;
    $('owner-controls').hidden = !csrf || !choice.value;
  }
  function revoked() {
    options({spaces:[]},null); status.textContent='Access changed. Refresh to reload permitted spaces.';
  }
  async function request(url, init, apply, current=()=>true) {
    const stamp = generation;
    try {
      const r = await fetch(url,{...init,signal:controller.signal});
      const data = await r.json();
      if (generation !== stamp) return;
      if (!r.ok && data.error === 'scope_lost') { revoked(); return; }
      if (!current()) return;
      if (!r.ok) {
        if (init.method === 'POST') invalidate();
        switch (data.error) {
          case 'approval_invalid': status.textContent='Approval expired or changed. Preview again to publish your draft.'; break;
          case 'revision_conflict': case 'resolution_conflict': status.textContent='Revision conflict. Inspect the current revision, update your draft and preview again.'; break;
          case 'idempotency_conflict': status.textContent='Operation key conflict. Check the previous operation before choosing a new key.'; break;
          case 'stale_pack': status.textContent='This pack is stale. Request a new pack.'; break;
          case 'forbidden': status.textContent='Request denied. Check your permissions before trying again.'; break;
          case 'invalid_request': status.textContent='Invalid request. Check the command and preview again.'; break;
          default: status.textContent=init.method === 'POST' ? 'Request unavailable. Check the current revision, then preview again with the same operation key.' : 'Request unavailable. Please try again.';
        }
        return;
      }
      apply(data);
    } catch { if (generation===stamp && current()) status.textContent='Request unavailable. Refresh to try again.'; }
  }
  choice.onchange = clear;
  options(await initial.json());
  function refresh() {
    const id=choice.value, seq=++refreshSequence;
    request('/api/v2/spaces',{},data=>options(data,id),()=>seq===refreshSequence);
  }
  $('refresh').onclick=refresh;
  window.addEventListener('pageshow',e=>{if(e.persisted) refresh();});
  window.addEventListener('pagehide',revoked);
  $('pack-find').onsubmit=e=>{
    e.preventDefault(); const seq=++packSequence;
    const scope='?space='+encodeURIComponent(choice.value), target=$('path-details'); target.replaceChildren();
    request('/api/v2/packs/'+encodeURIComponent($('pack-id').value)+scope,{},p=>renderPack(target,p,(claim,out)=>{
      request(`/api/v2/assertions/${encodeURIComponent(claim.assertion_id)}/revisions/${encodeURIComponent(claim.revision_id)}${scope}`,{},r=>renderRevision(out,r),()=>seq===packSequence);
    }),()=>seq===packSequence);
  };
  function command(action) {
    let draft; try { draft=JSON.parse($('command-draft').value); } catch { status.textContent='Enter a JSON command before previewing.'; return; }
    const stamp=edits;
    const body={family:$('command-family').value,space:choice.value,operation_key:$('operation-key').value,request:draft};
    if(action!=='preview') body.approval=approval;
    request('/api/v2/owner/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Atlas-CSRF':csrf},body:JSON.stringify(body)},data=>{
      if(action==='preview') { approval=data.approval; $('preview').textContent=JSON.stringify(data.command,null,2); $('publish').disabled=false; }
      else { invalidate(); status.textContent='Revision published.'; }
    },()=>stamp===edits);
  }
  $('command-form').oninput=invalidate;
  $('command-form').onsubmit=e=>{e.preventDefault(); invalidate(); command('preview');};
  $('publish').onclick=()=>{ $('publish').disabled=true; command($('command-family').value); };
  return true;
}
