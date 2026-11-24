window.__ModuleLoader__.load({
  id: '@local/dsh-nexus-inspector',
  factory(require) {
    const React = require('react');
    const { createElement: h, useEffect, useRef, useState } = React;
    const slot = 'conversation.chat.turnTail';
    const panelSlot = 'sidebar.right.pane.tab';
    const panelId = '@local/dsh-nexus-inspector';
    const kind = 'nexus-saved-revision';
    const ns = 'nexusReceiptInspector';
    // BEGIN generated descriptor
    function createInspectorApi(z) {
      const pkg = '@local/dsh-nexus-inspector';
      const strict = (symbol, schema, owner = pkg) => ({ mode: 'strict', typeSymbol: `${owner}#${symbol}`, create: () => schema });
      const index = z.int().nonnegative();
      const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9_.:-]+$/);
      const result = z.discriminatedUnion('status', [
        z.strictObject({ status: z.literal('unavailable') }),
        z.strictObject({
          status: z.literal('ok'), seq: index, originSeq: index, turn: z.int().positive(),
          memoryId: id, savedRevisionId: id, currentRevisionId: id, isCurrent: z.boolean(),
          content: z.string().max(65536),
        }),
      ]);
      return { package: pkg, descriptors: [{
        id: `${pkg}#nexusInspector/open`, service: 'nexusInspector', namespace: 'nexusInspector', method: 'open',
        invocation: { kind: 'direct' }, parameters: [
          { name: 'agent', wire: 'agentId', source: 'lookup', lookup: 'agent', codec: strict('SessionId', z.string().min(1), '@deepseek-ai/dsh-session/types') },
          { name: 'seq', wire: 'seq', source: 'json', codec: strict('ReceiptSeq', index) },
        ], cancellation: { parameter: 'signal' }, result: strict('OpenResult', result),
      }] };
    }
    // END generated descriptor
    const api = createInspectorApi(require('zod').z);
    const messages = {
      en: { saved: 'Saved memory', select: 'Choose a receipt', receipt: 'Receipt', loading: 'Reading saved revision…', unavailable: 'This saved revision is unavailable.', retry: 'Try again', back: 'Return to turn', memory: 'Memory', revision: 'Saved revision', current: 'Current revision', same: 'This is the current revision.', changed: 'A newer revision exists. The text below is the saved revision.', home: 'Home not assigned' },
      zh: { saved: '已保存的记忆', select: '选择回执', receipt: '回执', loading: '正在读取已保存的版本…', unavailable: '无法读取此版本。', retry: '重试', back: '返回此轮对话', memory: '记忆', revision: '已保存的版本', current: '当前版本', same: '此版本是当前版本。', changed: '已有更新的版本。以下是当时保存的文本。', home: '未分配归属空间' },
    };
    const control = { font: 'inherit', color: 'inherit', background: 'none', border: 0, cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: '0.2em', padding: '4px 0' };
    const ident = value => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,128}$/.test(value);
    // A tab occurrence and navigation revision identify one explicit open. DSH
    // can remount its body on collapse, docking, or session changes. Share the
    // pending/settled read for that occurrence only; never persist revision text.
    function Reader({ read, retry, t }) {
      const [reply, setReply] = useState(null);
      useEffect(() => {
        let active = true;
        read().then(result => { if (active) setReply(result); });
        return () => { active = false; };
      }, [read]);
      if (reply === null) return h('p', { role: 'status' }, t('loading'));
      if (!reply) return h('div', null, h('p', { role: 'status' }, t('unavailable')), h('button', { type: 'button', style: control, onClick: retry }, t('retry')));
      const isCurrent = reply.currentRevisionId === reply.savedRevisionId;
      const pair = (label, value) => h(React.Fragment, { key: label }, h('dt', null, t(label)), h('dd', { style: { margin: 0, overflowWrap: 'anywhere' } }, value));
      return h(React.Fragment, null,
        h('dl', { style: { display: 'grid', gridTemplateColumns: 'auto minmax(0,1fr)', gap: '2px 12px', fontSize: '12px' } }, pair('memory', reply.memoryId), pair('revision', reply.savedRevisionId), !isCurrent && pair('current', reply.currentRevisionId)),
        h('p', { role: 'status' }, t(isCurrent ? 'same' : 'changed')),
        h('pre', { dir: 'auto', tabIndex: 0, style: { font: 'inherit', lineHeight: 1.55, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '40vh', overflow: 'auto' } }, reply.content),
        h('small', null, t('home')));
    }
    const cueId = (sessionId, turn) => `nexus-receipt-${sessionId}-${turn}`;
    function receiptSeqs(view, turn) {
      const rows = view?.turns?.[turn];
      return Array.isArray(rows) ? [...new Set(rows.map(r => r?.seq).filter(n => Number.isSafeInteger(n) && n >= 0))] : [];
    }
    function Tail({ turn, sessionId, useProjection, useAccess, openPanel, t }) {
      const seqs = receiptSeqs(useProjection('nexusInspectorReceipts'), turn.turn);
      const allowed = useAccess('nexusInspector/open');
      return allowed && seqs.length ? h('button', { id: cueId(sessionId, turn.turn), type: 'button',
        style: { ...control, fontSize: '13px' }, onClick: () => openPanel(turn.turn, seqs[0]) },
        t('saved'), seqs.length > 1 ? ` (${seqs.length})` : '') : null;
    }
    function Panel({ sessionId, useTabInfo, useAccess, useProjection, getRead, t }) {
      const { tab } = useTabInfo();
      const { params, revision } = tab.navigation;
      const turn = params?.turn;
      const seq = params?.seq;
      const seqs = receiptSeqs(useProjection('nexusInspectorReceipts'), turn);
      const allowed = useAccess('nexusInspector/open');
      const origin = params?.sessionId === sessionId && Number.isSafeInteger(turn) && turn > 0;
      const back = useRef(null);
      const close = () => {
        tab.actions.close();
        if (origin) document.getElementById(cueId(sessionId, turn))?.focus();
      };
      useEffect(() => { if (tab.visible) back.current?.focus(); }, [tab.visible, revision]);
      const navigate = seq => tab.actions.openTab(kind, { params: { sessionId, turn, seq } });
      const ready = tab.visible && origin && allowed && seqs.includes(seq) && !tab.signal.aborted;
      return h('section', { 'data-nexus-receipt-panel': '', 'aria-label': t('saved'),
        style: { boxSizing: 'border-box', height: '100%', overflow: 'auto', minWidth: 0, padding: '12px 16px', fontSize: '13px', overflowWrap: 'anywhere' },
        onKeyDown: e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } } },
        h('button', { ref: back, type: 'button', style: control, onClick: close }, t('back')),
        ready && seqs.length > 1 && h('select', { 'aria-label': t('select'), value: seq, style: { marginInlineStart: 12, maxWidth: '100%' },
          onChange: e => navigate(Number(e.target.value)) },
          seqs.map(n => h('option', { key: n, value: n }, `${t('receipt')} #${n}`))),
        ready ? h(Reader, { key: `${sessionId}:${tab.id}:${revision}:${seq}`, read: getRead(tab, turn, seq), retry: () => navigate(seq), t }) : h('p', { role: 'status' }, t('unavailable')));
    }
    return {
      inject: ['remote', 'slots', 'locale'],
      async apply(ctx) {
        const disposeApi = await ctx.remote.$mount(api);
        ctx.effect(() => disposeApi);
        ctx.effect(() => ctx.locale.register(ns, messages));
        await ctx.inject(['remote.nexusInspector', 'slots', 'sidebarRight', 'sidebarRightTabs'], ui => {
          const reads = new WeakMap();
          const t = ui.locale.bind(ns);
          ui.effect(() => ui.sidebarRightTabs.register({ id: panelId, kind, title: () => t('saved') }));
          ui.slots.inject(panelSlot, () => ui.slots.register({ name: panelSlot, key: panelId, locale: ns,
            inject: sessionId => ({ getRead(tab, turn, seq) {
              const revision = tab.navigation.revision;
              const cached = reads.get(tab.signal);
              if (cached?.revision === revision && cached.sessionId === sessionId && cached.seq === seq && cached.turn === turn) return cached.read;
              let promise;
              const read = () => promise ??= Promise.resolve()
                .then(() => !tab.signal.aborted && ui.remote.nexusInspector.open(sessionId, seq))
                .then(result => {
                  const r = result?.ok === true ? result.value : null;
                  return r?.status === 'ok' && r.seq === seq && r.turn === turn && ident(r.memoryId) && ident(r.savedRevisionId) && ident(r.currentRevisionId) && typeof r.content === 'string' ? r : false;
                }, () => false);
              reads.set(tab.signal, { sessionId, revision, seq, turn, read });
              return read;
            } }),
          }, Panel));
          ui.slots.inject(slot, () => ui.slots.register({ name: slot, id: panelId, order: 100, locale: ns,
            inject: sessionId => ({ openPanel(turn, seq) {
              if (ui.sidebarRight.mounted.getSnapshot() === sessionId) ui.sidebarRight.openTab(kind, { params: { sessionId, turn, seq } });
            } }),
          }, Tail));
        });
      },
    };
  },
});
