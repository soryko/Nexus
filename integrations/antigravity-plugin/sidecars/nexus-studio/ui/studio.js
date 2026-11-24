// Nexus Studio frontend client. All server data flows over POST + X-Sidecar-Token
// and is rendered strictly via DOM textContent (never innerHTML with dynamic data).

const ACTIVITY_CATEGORIES = [
  ['all', 'All'],
  ['recall', 'Recalls'],
  ['read', 'Reads'],
  ['write', 'Writes'],
  ['error', 'Errors'],
];

const ATLAS_KINDS = ['all', 'decision', 'constraint', 'procedure', 'failure', 'observation'];

const state = {
  workspaceUris: [],
  activeTab: 'activity',
  status: null,
  activityCategory: 'all',
  activityEvents: [],
  overview: { total: 0, counts: [], generation: 0 },
  atlasQuery: '',
  atlasKind: 'all',
  atlasHits: [],
  atlasCursor: null,
  atlasPrevCursors: [],
  selectedMemoryId: null,
  selectedRevisionId: null,
  inspection: null,
  history: null,
  compareRevision: null,
  neighborhood: null,
  related: null,
  headVerdicts: {},
  attachNotice: '',
  pollingTimer: null,
  // Bumped on every workspace change / memory selection; a response captured under an older
  // value belongs to a workspace or selection the user has left and must not be applied.
  workspaceGen: 0,
  selectionGen: 0,
};

function el(tag, opts = {}, ...children) {
  const node = document.createElement(tag);
  if (opts.className) node.className = opts.className;
  if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
  if (opts.title) node.title = opts.title;
  if (opts.type) node.type = opts.type;
  if (opts.disabled !== undefined) node.disabled = Boolean(opts.disabled);
  if (opts.attrs) {
    for (const [k, v] of Object.entries(opts.attrs)) {
      if (v !== undefined && v !== null) node.setAttribute(k, String(v));
    }
  }
  if (opts.onClick) node.addEventListener('click', opts.onClick);
  for (const child of children) {
    if (child !== undefined && child !== null && child !== false) {
      node.append(child);
    }
  }
  return node;
}

function pill(text, tone = '') {
  return el('span', { className: tone ? `nx-pill ${tone}` : 'nx-pill', text });
}

function copyCmdButton(commandText, label = 'Copy CLI') {
  const btn = el('button', { className: 'nx-btn', type: 'button', text: label });
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(commandText);
      btn.textContent = 'Copied';
      setTimeout(() => {
        btn.textContent = label;
      }, 1400);
    } catch {
      btn.textContent = 'Copy manually';
      setTimeout(() => {
        btn.textContent = label;
      }, 1600);
    }
  });
  return btn;
}

function codeBox(cmd) {
  return el(
    'div',
    { className: 'nx-code-box' },
    el('code', { className: 'nx-code-scroll', text: cmd, title: cmd }),
    copyCmdButton(cmd),
  );
}

function shortDate(iso) {
  return typeof iso === 'string' ? iso.replace('T', ' ').slice(0, 19) : '';
}

function shortOid(oid) {
  return typeof oid === 'string' && oid.length > 10 ? oid.slice(0, 10) : (oid || 'none');
}

function firstLine(text) {
  return (String(text || '').split('\n').find((l) => l.trim()) || '(empty)').slice(0, 120);
}

function stateTone(st) {
  if (st === 'bound') return 'ok';
  if (st === 'device_drift' || st === 'unenrolled' || st === 'empty' || st === 'ambiguous') return 'warn';
  return 'danger';
}

function outcomeTone(outcome) {
  if (outcome === 'Matches at HEAD') return 'ok';
  if (outcome === 'Changed at HEAD') return 'warn';
  if (outcome === 'Missing at HEAD') return 'danger';
  return '';
}

function toWorkspaceFileUri(relPath) {
  if (state.workspaceUris.length !== 1 || !relPath) return null;
  let base = state.workspaceUris[0];
  if (!base.startsWith('file://')) {
    const cleanBase = base.replace(/\/+$/, '');
    base = `file://${cleanBase.split('/').map(encodeURIComponent).join('/')}`;
  }
  const cleanRel = relPath
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/');
  return `${base.replace(/\/+$/, '')}/${cleanRel}`;
}

async function apiPost(path, payload = {}) {
  const body = JSON.stringify({
    workspace: state.workspaceUris,
    ...payload,
  });
  const fetcher = window.sidecar?.fetch
    ? window.sidecar.fetch
    : (url, opts) => {
        const headers = new Headers(opts?.headers || {});
        headers.set('Content-Type', 'application/json');
        const tok = new URLSearchParams(window.location.search).get('token');
        if (tok) headers.set('X-Sidecar-Token', tok);
        return fetch(url, { ...opts, headers });
      };
  const res = await fetcher(path, { method: 'POST', body });
  if (!res.ok) {
    return { ok: false, error: { code: `http_${res.status}`, message: `Request failed (${res.status})` } };
  }
  return await res.json();
}

async function refreshStatusAndActivity({ loadAtlasIfBound = false } = {}) {
  const statusRes = await apiPost('/api/status');
  if (statusRes.ok && statusRes.data) {
    state.status = statusRes.data;
    state.overview = statusRes.data.overview || { total: 0, counts: [], generation: 0 };
  }
  if (state.status?.state === 'bound') {
    const cat = state.activityCategory === 'all' ? null : state.activityCategory;
    const actRes = await apiPost('/api/activity', { params: { limit: 60, category: cat } });
    if (actRes.ok && actRes.data) {
      state.activityEvents = actRes.data.events || [];
      if (actRes.data.health) state.status.activity_health = actRes.data.health;
    }
    if (loadAtlasIfBound || (state.activeTab === 'atlas' && state.atlasHits.length === 0)) {
      await loadAtlasList({ resetCursor: true });
    }
  } else {
    state.activityEvents = [];
    state.atlasHits = [];
    state.inspection = null;
  }
  renderAll();
}

async function loadAtlasList({ cursor = null, resetCursor = false } = {}) {
  if (state.status?.state !== 'bound') return;
  const workspaceGen = state.workspaceGen;
  if (resetCursor) state.atlasPrevCursors = [];
  const params = {
    limit: 20,
    query: state.atlasQuery.trim() || null,
    kinds: state.atlasKind === 'all' ? [] : [state.atlasKind],
    cursor,
  };
  const [ovRes, srRes] = await Promise.all([
    apiPost('/api/overview'),
    apiPost('/api/search', { params }),
  ]);
  if (workspaceGen !== state.workspaceGen) return;
  if (ovRes.ok && ovRes.data) state.overview = ovRes.data;
  if (srRes.ok && srRes.data) {
    state.atlasHits = srRes.data.items || [];
    state.atlasCursor = srRes.data.cursor || null;
    if (state.atlasHits.length > 0 && !state.selectedMemoryId) {
      await openMemory(state.atlasHits[0].memory_id, state.atlasHits[0].revision_id, { renderNow: false });
    }
  }
  renderAll();
}

async function openMemory(memoryId, revisionId = null, { renderNow = true } = {}) {
  const selectionGen = ++state.selectionGen;
  state.selectedMemoryId = memoryId;
  state.selectedRevisionId = revisionId;
  state.compareRevision = null;
  state.headVerdicts = {};
  const [inspRes, histRes, nbrRes, relRes] = await Promise.all([
    apiPost('/api/inspect', { params: { memory_id: memoryId, revision_id: revisionId } }),
    apiPost('/api/history', { params: { memory_id: memoryId, limit: 20 } }),
    apiPost('/api/neighborhood', { params: { memory_id: memoryId, revision_id: revisionId, limit: 20 } }),
    apiPost('/api/related', { params: { memory_id: memoryId, limit: 10 } }),
  ]);
  if (selectionGen !== state.selectionGen) return;
  state.inspection = inspRes.ok ? inspRes.data : null;
  state.history = histRes.ok ? histRes.data : null;
  state.neighborhood = nbrRes.ok ? nbrRes.data : null;
  state.related = relRes.ok ? relRes.data : null;
  if (renderNow) renderAll();
}

async function jumpToMemoryFromActivity(memoryId) {
  state.activeTab = 'atlas';
  await openMemory(memoryId, null, { renderNow: false });
  if (state.atlasHits.length === 0) {
    await loadAtlasList({ resetCursor: true });
  } else {
    renderAll();
  }
}

function renderHealthStrip() {
  const strip = document.getElementById('health-strip');
  const st = state.status;
  const scopeState = st?.state || 'loading';
  const health = st?.activity_health || { events_today: 0, errors_24h: 0 };

  const refreshBtn = el('button', {
    className: 'nx-btn',
    type: 'button',
    text: 'Refresh',
    onClick: () => refreshStatusAndActivity({ loadAtlasIfBound: state.activeTab === 'atlas' }),
  });

  const subtitle = st?.project_id
    ? `scope ${st.project_id.slice(0, 8)} · SQLite ${st.sqlite_version || '?'} · ${st.capture_policy === 'explicit' ? 'explicit capture' : 'autonomous capture'}`
    : 'Read-only workbench';

  strip.replaceChildren(
    el(
      'div',
      { className: 'nx-brand-group' },
      el('img', { attrs: { src: '/logo.svg', alt: 'Nexus Logo', width: '24', height: '24' } }),
      el(
        'div',
        {},
        el('div', { className: 'nx-title', text: 'Nexus Studio' }),
        el('div', { className: 'nx-sub', text: subtitle }),
      ),
    ),
    el(
      'div',
      { className: 'nx-metrics-group' },
      pill(scopeState.replace(/_/g, ' '), stateTone(scopeState)),
      pill(`${state.overview?.total ?? 0} memories`),
      pill(`${health.events_today ?? 0} today`),
      pill(`${health.errors_24h ?? 0} errors`, (health.errors_24h ?? 0) > 0 ? 'danger' : ''),
      refreshBtn,
    ),
  );
}

function renderAlertBanner() {
  const slot = document.getElementById('alert-slot');
  const st = state.status;
  if (!st || st.state === 'bound') {
    slot.hidden = true;
    slot.className = 'nx-alert-slot';
    slot.replaceChildren();
    return;
  }

  slot.hidden = false;
  const isDanger = st.state === 'runtime_unavailable' || st.state === 'identity_changed' || st.state === 'invalid';
  slot.className = isDanger ? 'nx-alert-slot danger' : 'nx-alert-slot';

  const children = [
    el('div', {
      className: 'nx-alert-title',
      text:
        st.state === 'device_drift'
          ? `APFS Volume Drift (${st.recorded_device} -> ${st.observed_device}) — Store Locked`
          : st.state === 'runtime_unavailable'
            ? 'Python / SQLite Runtime Unavailable'
            : st.state === 'unenrolled'
              ? 'Workspace Not Enrolled in Nexus Project Scope'
              : `Scope State: ${st.state}`,
    }),
    el('div', { className: 'nx-muted', text: st.message || 'Workspace is unavailable.' }),
  ];

  if (st.recover_command) children.push(codeBox(st.recover_command));
  if (st.state === 'unenrolled' && state.workspaceUris.length === 1) {
    const cleanPath = state.workspaceUris[0].replace(/^file:\/\//, '');
    children.push(codeBox(`python3 -I ~/.gemini/config/plugins/nexus-memory/scripts/launcher.py enroll "${decodeURIComponent(cleanPath)}"`));
  }
  if (st.state === 'runtime_unavailable') {
    children.push(
      el(
        'div',
        { className: 'nx-metrics-group' },
        el('button', {
          className: 'nx-btn primary',
          type: 'button',
          text: 'Retry Runtime Probe',
          onClick: async () => {
            const res = await apiPost('/api/retry-runtime');
            if (res.ok && res.data) state.status = res.data;
            renderAll();
          },
        }),
      ),
    );
  }
  slot.replaceChildren(...children);
}

function renderActivityTab(container) {
  const filterRow = el(
    'div',
    { className: 'nx-chip-group' },
    ...ACTIVITY_CATEGORIES.map(([cat, label]) =>
      el('button', {
        className: 'nx-chip',
        type: 'button',
        text: label,
        attrs: { 'aria-pressed': state.activityCategory === cat ? 'true' : 'false' },
        onClick: async () => {
          state.activityCategory = cat;
          await refreshStatusAndActivity();
        },
      }),
    ),
  );

  container.append(
    el(
      'div',
      { className: 'nx-toolbar' },
      filterRow,
      el('span', { className: 'nx-muted', text: 'Content-free <store>.activity.sqlite3' }),
    ),
  );

  if (state.status?.state !== 'bound') {
    container.append(
      el(
        'div',
        { className: 'nx-card nx-card-pad' },
        el('div', { className: 'nx-muted', text: 'Activity events appear once this workspace is bound and verified.' }),
      ),
    );
    return;
  }

  if (state.activityEvents.length === 0) {
    container.append(
      el(
        'div',
        { className: 'nx-card nx-card-pad' },
        el('div', { className: 'nx-muted', text: 'No activity recorded yet for this filter.' }),
      ),
    );
    return;
  }

  const list = el('div', { className: 'nx-card nx-list' });
  for (const ev of state.activityEvents) {
    const badges = [
      pill(ev.kind, ev.category === 'error' ? 'danger' : ev.category === 'recall' ? 'accent' : ''),
      pill(ev.outcome, ev.category === 'error' ? 'danger' : 'ok'),
    ];
    if (typeof ev.detail?.duration_ms === 'number') {
      badges.push(el('span', { className: 'nx-mono nx-muted', text: `${ev.detail.duration_ms} ms` }));
    }

    const detailNodes = [];
    if (ev.detail?.memory_id) {
      detailNodes.push(
        el('button', {
          className: 'nx-link-btn',
          type: 'button',
          text: `mem:${ev.detail.memory_id.slice(0, 8)}`,
          onClick: () => jumpToMemoryFromActivity(ev.detail.memory_id),
        }),
      );
    }
    if (Array.isArray(ev.detail?.candidate_ids)) {
      for (const cid of ev.detail.candidate_ids) {
        detailNodes.push(
          el('button', {
            className: 'nx-link-btn',
            type: 'button',
            text: `cand:${cid.slice(0, 8)}`,
            onClick: () => jumpToMemoryFromActivity(cid),
          }),
        );
      }
    }
    if (typeof ev.detail?.hit_count === 'number') {
      detailNodes.push(el('span', { className: 'nx-muted', text: `hits=${ev.detail.hit_count}` }));
    }
    if (typeof ev.detail?.suppressed_count === 'number' && ev.detail.suppressed_count > 0) {
      detailNodes.push(el('span', { className: 'nx-muted', text: `suppressed=${ev.detail.suppressed_count}` }));
    }
    if (ev.detail?.error_code) {
      detailNodes.push(el('span', { className: 'nx-mono', text: ev.detail.error_code }));
    }

    list.append(
      el(
        'div',
        { className: 'nx-event-row' },
        el(
          'div',
          { className: 'nx-event-top' },
          el('div', { className: 'nx-metrics-group' }, ...badges),
          el('span', { className: 'nx-mono nx-muted', text: shortDate(ev.ts) }),
        ),
        detailNodes.length > 0 ? el('div', { className: 'nx-metrics-group' }, ...detailNodes) : null,
      ),
    );
  }
  container.append(list);
}

function renderAtlasInspector() {
  const pane = el('div', { className: 'nx-atlas-inspector' });
  const insp = state.inspection;
  if (!insp) {
    pane.append(el('div', { className: 'nx-muted', text: 'Select a memory from the list to inspect its revision text, history, relations, and Git references.' }));
    return pane;
  }

  const attachBtn = el('button', {
    className: 'nx-btn primary',
    type: 'button',
    text: 'Attach to Next Prompt',
    onClick: () => {
      if (window.sidecar?.ui?.addArtifactComment) {
        window.sidecar.ui.addArtifactComment({
          uri: `nexus-memory://${insp.memory_id}`,
          pageTitle: `Nexus Memory (${insp.kind})`,
          selection: insp.content,
          comment: `Pinned Nexus memory ${insp.memory_id} (rev ${insp.revision_id})`,
        });
        state.attachNotice = 'Attached to next prompt';
      } else {
        state.attachNotice = 'Copied citation ID';
        navigator.clipboard?.writeText(insp.memory_id).catch(() => {});
      }
      renderAll();
      setTimeout(() => {
        state.attachNotice = '';
        renderAll();
      }, 1600);
    },
  });

  pane.append(
    el(
      'div',
      { className: 'nx-toolbar' },
      el(
        'div',
        { className: 'nx-metrics-group' },
        pill(insp.kind, 'accent'),
        pill(insp.is_historical ? 'historical revision' : 'HEAD revision', insp.is_historical ? 'warn' : 'ok'),
        el('span', { className: 'nx-mono nx-muted', text: insp.memory_id }),
      ),
      el('div', { className: 'nx-metrics-group' }, attachBtn, state.attachNotice ? pill(state.attachNotice, 'ok') : null),
    ),
    el('div', { className: 'nx-body-pre', text: insp.content || '' }),
  );

  if (Array.isArray(insp.tags) && insp.tags.length > 0) {
    pane.append(el('div', { className: 'nx-metrics-group' }, ...insp.tags.map((t) => pill(`#${t}`))));
  }

  const meta = el('dl', { className: 'nx-kv' });
  meta.append(
    el('dt', { text: 'Revision' }),
    el('dd', { text: insp.revision_id }),
    el('dt', { text: 'Created' }),
    el('dd', { text: shortDate(insp.revision_created_at || insp.created_at) }),
  );
  if (insp.source_uri) meta.append(el('dt', { text: 'Source URI' }), el('dd', { text: insp.source_uri }));
  if (insp.snapshot) meta.append(el('dt', { text: 'Snapshot' }), el('dd', { text: insp.snapshot }));
  pane.append(meta);

  // Verified Git References
  if (Array.isArray(insp.references) && insp.references.length > 0) {
    pane.append(el('div', { className: 'nx-sub', text: 'Git References (on-demand verification at HEAD)' }));
    insp.references.forEach((ref, idx) => {
      const verdict = state.headVerdicts[idx];
      const fileUri = toWorkspaceFileUri(ref.path);
      const checkBtn = el('button', {
        className: 'nx-btn',
        type: 'button',
        text: 'Verify at HEAD',
        onClick: async () => {
          const res = await apiPost('/api/compare-at-head', {
            params: {
              memory_id: insp.memory_id,
              revision_id: insp.revision_id,
              reference_ordinal: idx,
            },
          });
          if (res.ok && res.data) {
            state.headVerdicts[idx] = res.data;
            renderAll();
          }
        },
      });
      const btns = [checkBtn];
      if (fileUri && window.sidecar?.ui?.toggleAuxPane) {
        btns.push(
          el('button', {
            className: 'nx-btn',
            type: 'button',
            text: 'Open File',
            onClick: () => window.sidecar.ui.toggleAuxPane({ tabType: 'fileView', uri: fileUri }),
          }),
        );
      }
      pane.append(
        el(
          'div',
          { className: 'nx-ref-card' },
          el(
            'div',
            { className: 'nx-toolbar' },
            el('span', { className: 'nx-mono', text: `${ref.path} @ ${shortOid(ref.commit_oid)}` }),
            el('div', { className: 'nx-metrics-group' }, ...btns),
          ),
          verdict
            ? el(
                'div',
                { className: 'nx-metrics-group' },
                pill(verdict.outcome, outcomeTone(verdict.outcome)),
                el('span', { className: 'nx-muted', text: verdict.note || '' }),
              )
            : el('div', { className: 'nx-muted', text: `Recorded object: ${shortOid(ref.object_oid)} (${ref.entry_type})` }),
        ),
      );
    });
  }

  // Revision Chain & Diff
  if (state.history?.entries?.length) {
    pane.append(el('div', { className: 'nx-sub', text: `Revision Chain (${state.history.entries.length})` }));
    const chain = el('div', { className: 'nx-list' });
    for (const entry of state.history.entries) {
      const actions = [
        el('button', {
          className: 'nx-btn',
          type: 'button',
          text: entry.revision_id === insp.revision_id ? 'Viewing' : 'Inspect',
          disabled: entry.revision_id === insp.revision_id,
          onClick: () => openMemory(insp.memory_id, entry.revision_id),
        }),
      ];
      if (entry.parent_revision_id) {
        actions.push(
          el('button', {
            className: 'nx-btn',
            type: 'button',
            text: 'Diff parent',
            onClick: async () => {
              const res = await apiPost('/api/compare-revision', {
                params: { memory_id: insp.memory_id, revision_id: entry.revision_id },
              });
              if (res.ok && res.data) {
                state.compareRevision = { ...res.data, revision_id: entry.revision_id };
                renderAll();
              }
            },
          }),
        );
      }
      chain.append(
        el(
          'div',
          { className: 'nx-ref-card' },
          el(
            'div',
            { className: 'nx-toolbar' },
            el(
              'span',
              { className: 'nx-mono', text: `${entry.revision_id.slice(0, 8)} · ${entry.kind} · ${shortDate(entry.created_at)}` },
            ),
            el('div', { className: 'nx-metrics-group' }, ...actions),
          ),
        ),
      );
    }
    pane.append(chain);
  }

  if (state.compareRevision) {
    const cmp = state.compareRevision;
    const diffLines = cmp.lines || cmp.diff_lines || [];
    pane.append(
      el(
        'div',
        { className: 'nx-ref-card' },
        el('div', { className: 'nx-title', text: `Revision Diff (${cmp.state})` }),
        el('div', { className: 'nx-muted', text: `Parent ${(cmp.parent_revision_id || 'none').slice(0, 8)} -> ${(cmp.revision_id || '').slice(0, 8)}` }),
        ...(diffLines.length > 0
          ? diffLines.map((line) => el('div', { className: 'nx-mono', text: line }))
          : [el('div', { className: 'nx-muted', text: '(no text difference between revisions)' })]),
      ),
    );
  }

  return pane;
}

function renderAtlasTab(container) {
  if (state.status?.state !== 'bound') {
    container.append(
      el(
        'div',
        { className: 'nx-card nx-card-pad' },
        el('div', { className: 'nx-muted', text: 'Memory Atlas is locked until this workspace is enrolled and verified.' }),
      ),
    );
    return;
  }

  const searchInput = el('input', {
    className: 'nx-input',
    attrs: { type: 'search', placeholder: 'Search current memories (BM25)...', value: state.atlasQuery },
  });
  searchInput.value = state.atlasQuery;
  searchInput.addEventListener('input', () => {
    state.atlasQuery = searchInput.value;
  });

  const form = el(
    'form',
    { className: 'nx-search-bar' },
    searchInput,
    el('button', { className: 'nx-btn primary', type: 'submit', text: 'Search' }),
  );
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    loadAtlasList({ resetCursor: true });
  });

  const countMap = new Map(state.overview?.counts || []);
  const kindFilter = el(
    'div',
    { className: 'nx-chip-group' },
    ...ATLAS_KINDS.map((k) => {
      const n = k === 'all' ? state.overview?.total : (countMap.get(k) || 0);
      return el('button', {
        className: 'nx-chip',
        type: 'button',
        text: n !== undefined ? `${k} (${n})` : k,
        attrs: { 'aria-pressed': state.atlasKind === k ? 'true' : 'false' },
        onClick: () => {
          state.atlasKind = k;
          loadAtlasList({ resetCursor: true });
        },
      });
    }),
  );

  const listPane = el('div', { className: 'nx-atlas-list' });
  if (state.atlasHits.length === 0) {
    listPane.append(el('div', { className: 'nx-card-pad nx-muted', text: 'No matching active memories found.' }));
  } else {
    for (const hit of state.atlasHits) {
      listPane.append(
        el(
          'button',
          {
            className: 'nx-hit-btn',
            type: 'button',
            attrs: { 'aria-current': hit.memory_id === state.selectedMemoryId ? 'true' : 'false' },
            onClick: () => openMemory(hit.memory_id, hit.revision_id),
          },
          el(
            'div',
            { className: 'nx-metrics-group' },
            pill(hit.kind, 'accent'),
            el('span', { className: 'nx-muted', text: shortDate(hit.created_at).slice(0, 10) }),
          ),
          el('div', { text: firstLine(hit.excerpt) }),
        ),
      );
    }
  }

  container.append(
    form,
    kindFilter,
    el('div', { className: 'nx-atlas-layout' }, listPane, renderAtlasInspector()),
  );
}

function renderSetupTab(container) {
  const st = state.status || {};
  const launcher = '~/.gemini/config/plugins/nexus-memory/scripts/launcher.py';

  const runtimeCard = el(
    'div',
    { className: 'nx-card nx-card-pad' },
    el('div', { className: 'nx-title', text: 'Runtime & Policy Health' }),
    el(
      'dl',
      { className: 'nx-kv' },
      el('dt', { text: 'Scope state' }),
      el('dd', { text: st.state || 'unknown' }),
      el('dt', { text: 'Python version' }),
      el('dd', { text: st.python_version || 'unavailable' }),
      el('dt', { text: 'Linked SQLite' }),
      el('dd', { text: st.sqlite_version || 'unavailable' }),
      el('dt', { text: 'Package version' }),
      el('dd', { text: st.service_version || '0.2.0' }),
      el('dt', { text: 'Capture policy' }),
      el('dd', { text: st.capture_policy === 'explicit' ? 'explicit-only' : 'autonomous + stop-hook nudge (forget explicit-only)' }),
      el('dt', { text: 'UI mode' }),
      el('dd', { text: 'read-only (POST + X-Sidecar-Token)' }),
    ),
    el(
      'div',
      { className: 'nx-metrics-group' },
      el('button', {
        className: 'nx-btn primary',
        type: 'button',
        text: 'Re-verify Runtime',
        onClick: async () => {
          const res = await apiPost('/api/retry-runtime');
          if (res.ok && res.data) state.status = res.data;
          renderAll();
        },
      }),
    ),
  );

  const auditCard = el(
    'div',
    { className: 'nx-card nx-card-pad' },
    el(
      'div',
      { className: 'nx-toolbar' },
      el('div', { className: 'nx-title', text: 'Weekly Nexus Memory Audit' }),
      pill('read-only schedule', 'accent'),
    ),
    el('div', {
      className: 'nx-muted',
      text: 'Bundled schedule sidecar (Mondays 09:00 UTC) checking scope binding and whether cited Git references still match HEAD. Grants only status, search, get, and compare_at_head.',
    }),
  );

  const cliCard = el(
    'div',
    { className: 'nx-card nx-card-pad' },
    el('div', { className: 'nx-title', text: 'Owner Scope Commands' }),
    el('div', { className: 'nx-muted', text: 'Enroll a repository explicitly from its main checkout:' }),
    codeBox(`python3 -I ${launcher} enroll /path/to/repo`),
    el('div', { className: 'nx-muted', text: 'Confirm macOS APFS volume device-only drift:' }),
    codeBox(`python3 -I ${launcher} recover /path/to/repo --expected-project-id <UUID>`),
  );

  container.append(el('div', { className: 'nx-grid-2' }, runtimeCard, auditCard), cliCard);
}

function renderAll() {
  renderHealthStrip();
  renderAlertBanner();

  for (const tabBtn of document.querySelectorAll('.nx-tab')) {
    tabBtn.setAttribute('aria-selected', tabBtn.dataset.tab === state.activeTab ? 'true' : 'false');
  }

  const main = document.getElementById('tab-content');
  main.replaceChildren();
  if (state.activeTab === 'activity') {
    renderActivityTab(main);
  } else if (state.activeTab === 'atlas') {
    renderAtlasTab(main);
  } else {
    renderSetupTab(main);
  }
}

for (const tabBtn of document.querySelectorAll('.nx-tab')) {
  tabBtn.addEventListener('click', async () => {
    const next = tabBtn.dataset.tab;
    if (!next || next === state.activeTab) return;
    state.activeTab = next;
    renderAll();
    if (next === 'atlas' && state.status?.state === 'bound' && state.atlasHits.length === 0) {
      await loadAtlasList({ resetCursor: true });
    }
  });
}

function startPolling() {
  if (state.pollingTimer) clearInterval(state.pollingTimer);
  state.pollingTimer = setInterval(() => {
    if (document.visibilityState === 'visible') {
      refreshStatusAndActivity();
    }
  }, 4000);
}

async function init() {
  renderAll();
  const isStandalone = window.parent === window;
  if (isStandalone) {
    const urlWs = new URLSearchParams(window.location.search).get('workspace');
    if (urlWs) state.workspaceUris = [urlWs];
  }

  if (window.sidecar?.onWorkspaceChange) {
    window.sidecar.onWorkspaceChange(async (uris) => {
      state.workspaceUris = Array.isArray(uris) ? uris : [];
      state.workspaceGen += 1;
      state.selectionGen += 1;
      state.selectedMemoryId = null;
      state.selectedRevisionId = null;
      state.inspection = null;
      state.history = null;
      state.neighborhood = null;
      state.related = null;
      state.compareRevision = null;
      state.headVerdicts = {};
      state.atlasHits = [];
      state.atlasCursor = null;
      state.atlasPrevCursors = [];
      await refreshStatusAndActivity({ loadAtlasIfBound: state.activeTab === 'atlas' });
    });
  }

  if (!isStandalone && window.sidecar?.getWorkspaceUris) {
    const uris = await window.sidecar.getWorkspaceUris();
    if (Array.isArray(uris)) state.workspaceUris = uris;
  }

  await refreshStatusAndActivity({ loadAtlasIfBound: state.activeTab === 'atlas' });
  startPolling();
}

init();
