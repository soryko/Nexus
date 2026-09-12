"""Delivered-context classification, three buckets, for the run-4 arms."""
import json, sys
from pathlib import Path
SP=Path(sys.argv[1]); RUN=SP/f"run-{sys.argv[2]}"
corpus=json.loads(Path("/Users/soko/Cerebros/nexus-memory/benchmarks/agent/corpus-dev-a1.json").read_text())
smap={r["memory_id"]: r["corpus_id"] for r in json.loads((RUN/"store-map.json").read_text())}
byid={m["id"]: m for m in corpus["memories"]}

def bucket(cid):
    rel=byid[cid].get("relevance",{}).get(sys.argv[2])
    if rel=="necessary": return "necessary"
    if rel=="support":   return "useful support"
    if rel=="outdated":  return "outdated"
    return "irrelevant"

for arm in ("baseline","nexus","notes"):
    calls=[]
    for l in open(RUN/"arms"/arm/"trace.jsonl"):
        l=l.strip()
        if not l.startswith("{"): continue
        d=json.loads(l)
        if d.get("type")=="assistant":
            for b in d["message"].get("content",[]):
                if b.get("type")=="tool_use": calls.append({"name":b["name"],"input":b.get("input")})
        if d.get("type")=="user":
            c=d.get("message",{}).get("content",[])
            if isinstance(c,list):
                for b in c:
                    if b.get("type")=="tool_result":
                        t=b.get("content")
                        if isinstance(t,list): t=" ".join(x.get("text","") for x in t if isinstance(x,dict))
                        for cc in reversed(calls):
                            if "result" not in cc: cc["result"]=t or ""; break
    blob=" ".join((c.get("result") or "") for c in calls)
    if arm=="notes":
        # the whole rendered file is delivered the moment it is read
        read = any("NOTES-FROM-EARLIER-WORK" in json.dumps(c.get("input",{})) for c in calls)
        delivered = {m["id"] for m in corpus["memories"]} if read else set()
        mode="whole file, on first read"
    elif arm=="nexus":
        delivered={cid for mid,cid in smap.items() if mid in blob}
        mode="per retrieval"
    else:
        delivered=set(); mode="none available"
    counts={}
    for cid in delivered: counts[bucket(cid)]=counts.get(bucket(cid),0)+1
    nbytes=sum(len(byid[c]["content"].encode()) for c in delivered)
    print(f"--- {arm} ({mode}) ---")
    print(f"    memories delivered: {len(delivered)}/13   bytes: {nbytes}")
    for b in ("necessary","useful support","outdated","irrelevant"):
        ids=sorted(c for c in delivered if bucket(c)==b)
        print(f"    {b:16}: {len(ids)}  {ids}")
    need={c for c in byid if byid[c].get('relevance',{}).get(sys.argv[2])=='necessary'}
    # protocol-a1 section 9: a genuinely empty denominator is N/A, never 0.0 -- it must not
    # drag a mean down. d4 declares no necessary memory; the repository carries the evidence.
    cov = "N/A (task declares no necessary memory)" if not need else f"{len(delivered&need)}/{len(need)}"
    print(f"    necessary-fact coverage: {cov}")
