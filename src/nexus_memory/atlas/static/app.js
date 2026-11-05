// Task 4 shell: exchange the launch secret, then show the counted overview as text.
const status = document.getElementById("status");
const groups = document.getElementById("groups");

async function signIn() {
  const match = /^#k=([\w-]+)$/.exec(location.hash);
  if (!match) return;
  history.replaceState(null, "", location.pathname);   // the secret leaves the address bar at once
  const reply = await fetch("/session", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key: match[1] }),
  });
  if (!reply.ok) throw new Error("sign-in refused");
}

async function main() {
  await signIn();
  const reply = await fetch("/api/overview");
  if (!reply.ok) { status.textContent = "Not signed in or the project binding changed. Relaunch Atlas."; return; }
  const overview = await reply.json();
  status.textContent = `${overview.total} active memories`;
  for (const [kind, count] of overview.counts) {
    const item = document.createElement("li");
    item.textContent = `${kind}: ${count}`;
    groups.append(item);
  }
}

main().catch(() => { status.textContent = "Atlas could not start. Relaunch it."; });
