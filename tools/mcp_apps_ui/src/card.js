import { App, applyDocumentTheme, applyHostStyleVariables } from "@modelcontextprotocol/ext-apps";
import { badge, cardHeader, codeRow, copyButton, el, outcomeTone, shortDate, shortOid } from "./dom.js";

const root = document.getElementById("app");
const app = new App({ name: "Nexus Memory Card", version: "0.2.0" });

function applyTheme(ctx) {
  if (!ctx) return;
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}

function renderLoading(args) {
  const label = args && args.memory_id ? `Loading memory ${args.memory_id.slice(0, 8)}...` : "Checking Nexus Memory...";
  root.replaceChildren(
    el(
      "div",
      { className: "nx-card" },
      cardHeader("Nexus Memory", label, [badge("loading", "accent")]),
    ),
  );
}

function renderError(message) {
  root.replaceChildren(
    el(
      "div",
      { className: "nx-card" },
      cardHeader("Nexus Memory", "Tool error", [badge("error", "danger")]),
      el(
        "div",
        { className: "nx-body" },
        el(
          "div",
          { className: "nx-banner danger" },
          el("div", { className: "nx-banner-title", text: message || "Operation failed" }),
        ),
      ),
    ),
  );
}

function renderDiagnosticStatus(data) {
  const state = data.scope_state || "unavailable";
  const isDrift = state === "device_drift";
  const isRuntime = state === "runtime_unavailable";
  const tone = isDrift ? "warn" : "danger";
  const titleText = isDrift
    ? "APFS Device Drift Detected"
    : isRuntime
      ? "Python / SQLite Runtime Unavailable"
      : state === "unenrolled"
        ? "Project Not Enrolled"
        : `Scope State: ${state}`;

  const kv = el("dl", { className: "nx-kv" });
  const rows = [
    ["State", state],
    ["Project UUID", data.project_id || "none"],
    ["Recorded device", data.recorded_device != null ? String(data.recorded_device) : "n/a"],
    ["Observed device", data.observed_device != null ? String(data.observed_device) : "n/a"],
    ["Service", data.service_version || "unknown"],
    ["Capture policy", data.capture_policy || "explicit"],
  ];
  for (const [k, v] of rows) {
    kv.append(el("dt", { text: k }), el("dd", { text: v }));
  }

  const bodyChildren = [
    el(
      "div",
      { className: tone === "danger" ? "nx-banner danger" : "nx-banner" },
      el("div", { className: "nx-banner-title", text: titleText }),
      el("div", { className: "nx-muted", text: data.message || "Project storage is locked until verified from your terminal." }),
    ),
    kv,
  ];

  if (data.recover_command) {
    bodyChildren.push(el("div", { className: "nx-muted", text: "Run in your terminal after verifying this is the same checkout:" }));
    bodyChildren.push(codeRow(data.recover_command));
  } else if (data.enroll_command) {
    bodyChildren.push(el("div", { className: "nx-muted", text: "Enroll explicitly from your main checkout terminal:" }));
    bodyChildren.push(codeRow(data.enroll_command));
  }

  if (Array.isArray(data.probes) && data.probes.length > 0) {
    bodyChildren.push(el("div", { className: "nx-muted", text: "Probed interpreters:" }));
    const list = el("div", { className: "nx-ref-list" });
    for (const p of data.probes.slice(0, 6)) {
      list.append(
        el(
          "div",
          { className: "nx-ref-item" },
          el("div", { className: "nx-mono", text: p.executable || "unknown" }),
          el("div", { className: "nx-muted", text: p.reason || `${p.python || "?"} / sqlite ${p.sqlite || "?"}` }),
        ),
      );
    }
    bodyChildren.push(list);
  }

  root.replaceChildren(
    el(
      "div",
      { className: "nx-card" },
      cardHeader("Nexus Memory Status", "Fail-closed guard active", [badge(state, tone)]),
      el("div", { className: "nx-body" }, ...bodyChildren),
    ),
  );
}

function renderBoundStatus(data) {
  const storageOk = data.durable_storage === "available";
  const policyText = `capture: ${data.capture_policy || "explicit"}`;
  const badges = [
    badge(storageOk ? "bound & healthy" : String(data.durable_storage || "unknown"), storageOk ? "ok" : "warn"),
    badge(policyText, "accent"),
  ];

  const stats = el(
    "div",
    { className: "nx-stats" },
    el("div", { className: "nx-stat" }, el("div", { className: "nx-stat-label", text: "Active memories" }), el("div", { className: "nx-stat-value", text: String(data.active_memories ?? 0) })),
    el("div", { className: "nx-stat" }, el("div", { className: "nx-stat-label", text: "Revisions" }), el("div", { className: "nx-stat-value", text: String(data.revisions ?? 0) })),
    el("div", { className: "nx-stat" }, el("div", { className: "nx-stat-label", text: "Forgotten" }), el("div", { className: "nx-stat-value", text: String(data.forgotten_memories ?? 0) })),
    el("div", { className: "nx-stat" }, el("div", { className: "nx-stat-label", text: "Durable seq" }), el("div", { className: "nx-stat-value", text: String(data.latest_durable_seq ?? 0) })),
  );

  const kv = el("dl", { className: "nx-kv" });
  const rows = [
    ["Namespace", data.namespace || "none"],
    ["Lexical index", data.lexical_index || "unknown"],
    ["Git verification", data.verification || "unknown"],
    ["SQLite", data.sqlite_version || "unknown"],
    ["Service", data.service_version || "unknown"],
    ["DB fingerprint", data.database_location_fingerprint || "none"],
  ];
  for (const [k, v] of rows) {
    kv.append(el("dt", { text: k }), el("dd", { text: v }));
  }

  root.replaceChildren(
    el(
      "div",
      { className: "nx-card" },
      cardHeader("Nexus Memory Status", "Verified project store", badges),
      el("div", { className: "nx-body" }, stats, kv),
    ),
  );
}

function renderHeadComparison(data) {
  const tone = outcomeTone(data.outcome);
  const kv = el("dl", { className: "nx-kv" });
  kv.append(
    el("dt", { text: "Recorded commit" }),
    el("dd", { text: shortOid(data.reference_commit_oid) }),
    el("dt", { text: "HEAD commit" }),
    el("dd", { text: shortOid(data.head_commit_oid) }),
    el("dt", { text: "Observation" }),
    el("dd", { text: data.note || "" }),
  );
  root.replaceChildren(
    el(
      "div",
      { className: "nx-card" },
      cardHeader("Git Reference Check at HEAD", "Read-only object OID check", [badge(data.outcome || "Cannot check", tone)]),
      el("div", { className: "nx-body" }, kv),
    ),
  );
}

function renderMemory(data) {
  const caps = app.getHostCapabilities() || {};
  const isCurrent = data.revision_id === data.current_revision_id;
  const headerBadges = [
    badge(data.kind || "observation", "accent"),
    badge(isCurrent ? "current revision" : "historical", isCurrent ? "ok" : "warn"),
  ];

  const bodyChildren = [
    el("div", { className: "nx-content-box", text: data.content || "" }),
  ];

  if (Array.isArray(data.tags) && data.tags.length > 0) {
    bodyChildren.push(el("div", { className: "nx-tags" }, ...data.tags.map((t) => el("span", { className: "nx-tag", text: `#${t}` }))));
  }

  const kv = el("dl", { className: "nx-kv" });
  kv.append(
    el("dt", { text: "Memory ID" }),
    el("dd", { text: data.memory_id || "" }),
    el("dt", { text: "Revision ID" }),
    el("dd", { text: data.revision_id || "" }),
    el("dt", { text: "Created" }),
    el("dd", { text: shortDate(data.created_at) }),
  );
  if (data.source_uri) kv.append(el("dt", { text: "Source" }), el("dd", { text: data.source_uri }));
  if (data.snapshot) kv.append(el("dt", { text: "Snapshot" }), el("dd", { text: data.snapshot }));
  bodyChildren.push(kv);

  if (Array.isArray(data.references) && data.references.length > 0) {
    const refList = el("div", { className: "nx-ref-list" });
    data.references.forEach((ref, idx) => {
      const resultSlot = el("div", { className: "nx-muted" });
      const checkBtn = el("button", { className: "nx-btn", type: "button", text: "Verify at HEAD" });
      if (!caps.serverTools) {
        checkBtn.hidden = true;
      } else {
        checkBtn.addEventListener("click", async () => {
          checkBtn.disabled = true;
          checkBtn.textContent = "Checking...";
          try {
            const res = await app.callServerTool({
              name: "compare_at_head",
              arguments: {
                memory_id: data.memory_id,
                revision_id: data.revision_id,
                reference_ordinal: idx,
              },
            });
            const cmp = res?.structuredContent;
            if (res?.isError || !cmp) {
              resultSlot.textContent = "HEAD check unavailable.";
            } else {
              resultSlot.replaceChildren(
                badge(cmp.outcome, outcomeTone(cmp.outcome)),
                el("span", { text: ` ${cmp.note || ""}` }),
              );
            }
          } catch {
            resultSlot.textContent = "HEAD check failed.";
          } finally {
            checkBtn.disabled = false;
            checkBtn.textContent = "Verify at HEAD";
          }
        });
      }

      refList.append(
        el(
          "div",
          { className: "nx-ref-item" },
          el(
            "div",
            { className: "nx-ref-top" },
            el("span", { className: "nx-mono", text: `${ref.path} @ ${shortOid(ref.commit_oid)}` }),
            el(
              "div",
              { className: "nx-actions" },
              badge(ref.entry_type || "blob"),
              copyButton(ref.path, "Copy path"),
              checkBtn,
            ),
          ),
          resultSlot,
        ),
      );
    });
    bodyChildren.push(el("div", { className: "nx-muted", text: "Git References (verified commit state; historical evidence)" }));
    bodyChildren.push(refList);
  }

  const actions = el("div", { className: "nx-actions" });
  actions.append(copyButton(data.memory_id, "Copy ID"));
  if (caps.updateModelContext) {
    const attachBtn = el("button", { className: "nx-btn primary", type: "button", text: "Attach to Next Prompt" });
    attachBtn.addEventListener("click", async () => {
      attachBtn.disabled = true;
      try {
        await app.updateModelContext({
          content: [
            {
              type: "text",
              text: `Pinned Nexus Memory ${data.memory_id} (rev ${data.revision_id}, ${data.kind}): ${data.content}`,
            },
          ],
        });
        attachBtn.textContent = "Attached";
      } catch {
        attachBtn.textContent = "Attach failed";
      } finally {
        setTimeout(() => {
          attachBtn.disabled = false;
          attachBtn.textContent = "Attach to Next Prompt";
        }, 1600);
      }
    });
    actions.append(attachBtn);
  }
  bodyChildren.push(actions);

  root.replaceChildren(
    el(
      "div",
      { className: "nx-card" },
      cardHeader("Nexus Memory Record", "Historical evidence — verify against current files", headerBadges),
      el("div", { className: "nx-body" }, ...bodyChildren),
    ),
  );
}

function handleResult(result) {
  if (!result) return;
  if (result.isError) {
    const firstText = Array.isArray(result.content) ? result.content.find((c) => c.type === "text")?.text : "";
    renderError(firstText || "Tool returned an error");
    return;
  }
  const sc = result.structuredContent;
  if (!sc || typeof sc !== "object") {
    const text = Array.isArray(result.content) ? result.content.map((c) => c.text || "").join("\n") : "";
    renderError(text || "No structured payload available");
    return;
  }
  if ("scope_state" in sc) {
    renderDiagnosticStatus(sc);
  } else if ("durable_storage" in sc) {
    renderBoundStatus(sc);
  } else if ("outcome" in sc) {
    renderHeadComparison(sc);
  } else if ("memory_id" in sc && "content" in sc) {
    renderMemory(sc);
  } else {
    renderError("Unsupported tool payload");
  }
}

app.ontoolinput = ({ arguments: args }) => renderLoading(args);
app.ontoolresult = (result) => handleResult(result);
app.ontoolcancelled = ({ reason }) => renderError(reason ? `Cancelled: ${reason}` : "Cancelled");
app.onhostcontextchanged = (ctx) => applyTheme(ctx);

(async () => {
  try {
    await app.connect();
    applyTheme(app.getHostContext());
  } catch (err) {
    renderError(err instanceof Error ? err.message : "Unable to connect to MCP Apps host");
  }
})();
