// Native disclosures preserve reading order and keyboard access without a graph canvas.
function node(tag, text) {
  const n = document.createElement(tag); n.textContent = text; return n;
}
function describe(value) {
  if (value == null || value === '') return 'Unknown';
  if (Array.isArray(value)) return value.map(describe).join(', ');
  if (typeof value !== 'object') return String(value);
  return Object.entries(value).map(([key, v]) => `${key.replaceAll('_',' ')}: ${describe(v)}`).join('; ');
}
export function renderPack(target, pack, inspectRevision) {
  target.replaceChildren(node('h2','Supported paths'));
  target.append(node('p',`${pack.bundles.length} paths. ${pack.status.replaceAll('_',' ')}.`));
  if (pack.limitations.length) target.append(node('p',`Truncated or limited: ${pack.limitations.join(', ')}`));
  for (const b of pack.bundles) {
    const details = document.createElement('details');
    details.append(node('summary',`${b.claim.assertion_id} · ${b.contested ? 'Contested' : 'No recorded dispute'}`));
    const path = document.createElement('ol');
    for (const edge of b.path) {
      const li = node('li',`${describe(edge.subject)} — ${edge.step.predicate} (${edge.step.direction}) — ${describe(edge.object)} `);
      const button = node('button',`Inspect revision ${edge.claim.revision_id}`);
      const exact = document.createElement('div');
      button.onclick = () => inspectRevision(edge.claim, exact);
      li.append(button, exact); path.append(li);
    }
    details.append(path, node('p',`Validity: ${describe(b.validity)}. Applicability: ${describe(b.applicability)}.`));
    // Pack groups can belong to different path edges. Only exact assertion groups are alternatives.
    for (const group of b.support_groups) details.append(node('p',`Required together: ${group.join(' AND ')}`));
    for (const citation of b.citations) details.append(node('p',`${citation.evidence_id}: ${describe(citation.source)}; ${describe(citation.span)}`),node('blockquote',citation.text));
    if (!b.citations.length) details.append(node('p','Missing evidence'));
    target.append(details);
  }
}
export function renderRevision(target, result) {
  target.replaceChildren(node('p',describe(result.assertion)), node('p', 'Support: '+(result.support_groups.map(g=>`(${g.join(' AND ')})`).join(' OR ') || 'Missing evidence')),
    node('p',result.sources.length ? result.sources.map(describe).join('\n') : 'Missing evidence'));
}
