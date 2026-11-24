# DSH capture pilot: fresh-model draft signal (2026-11-19)

**DeepSeek produced a useful, supported lesson on 5 of 6 eligible synthetic tasks.** It proposed a lesson on 1 of 4 controls; the blinded assessor rejected it on both usefulness and support. This shows draft signal with optional lesson guidance, not prompt-free capture or a production false-positive rate.

## Method

This is the draft-signal exercise from the [Track 1 benchmark](<../../benchmarks/dsh_capture/README.md>), not the recall-on/off application exercise. Six eligible tasks use the task question and event from [facts.json](<../../benchmarks/dsh_capture/facts.json>). Four controls use the event strings and kinds from [draft_rubric.py](<../../benchmarks/dsh_capture/draft_rubric.py>) and the ordinary task questions below. The markers are invented and are not Nexus defaults. No control containing an authored wrong-value draft was fed to the generator; this run does not measure resistance to disputed or negated values, forged citations, or cross-scope events.

Coeus 1.1.0, standalone `coeus_ask`, explicit native route `deepseek/deepseek-v4-pro` (no finer provider build ID available), temperature requested 0, max output 240 tokens, reasoning requested and reported off (no reasoning content). All 20 generation requests succeeded on the first attempt with the requested route, no absent replies and no reported cache-hit tokens. Each call supplied just its constructed prompt, with no model tools, retrieval, memory, project instructions, prior conversation, answer key, or case label. No Nexus connection or write, profile change, or model turn in DSH Host was part of this run.

Each pair got identical task and evidence. **T** (task-only) used the base prompt; **D** added the optional guidance below. Receipt ran T first; the remaining pairs were shuffled with five D-first and four T-first. The table is in execution order, with its first-arm column giving the within-pair order. No explicit Remember prompt was used.

~~~text
Scenario: a fictional software rehearsal. Give a concise answer to the task using only the event below. Do not invent facts.
Task: {question}
Same-turn event [11] ({event kind}, committed): {event text}
~~~

D appended one newline and this guidance:

~~~text
After answering the task, you may optionally add one brief reusable lesson when the event establishes something new, durable, concrete and actionable. Include a natural symptom or trigger when accurate and cite the source event ID in the form “Lesson [ID]: …”. Omit the lesson if no such takeaway is warranted.
~~~

The four control questions were: routine check, “Did the requested pytest check pass?”; routine progress, “Give a one-sentence status update on the Atlas page review.”; plan only, “What, if anything, was decided about the pager?”; recalled only, “Can I treat the special lane as confirmed for this rehearsal?”

A separate fresh request on the same model (same settings except max output 1,400) assessed shuffled opaque rows. It received only each raw lesson line (or null) and the event ID, kind, committed flag, and text—not the task answer, case name, eligibility label, or curated lesson. The assessor judged two axes separately: usefulness (new, durable, concrete, actionable under a recognizable trigger), and support (a valid citation of an eligible committed event entailing the condition, action, and any claimed result or reason). Event kinds and contents were necessarily visible. The assessor consumed 832 input / 440 output tokens and took 3,352 ms wall time, excluded from pair deltas. “Blind” here means blind to fixture labels, not that the model could not infer obvious control types; the assessor used the generator's model and was neither human nor independently replicated.

## Results

Timing is elapsed wall time at the Coeus tool caller (milliseconds), not DSH Host latency. I/O counts are provider-reported prompt/completion tokens. “—” means no proposal, not a negative score on either axis. A draft is ready for review in this benchmark only if it is both useful and supported.

| Case | Class | First arm | Blind row | Useful / supported | T ms; I/O | D ms; I/O | D−T ms |
|---|---|---|---|---|---:|---:|---:|
| receipt | Eligible | T | R7 | Yes / yes | 1,188; 86/6 | 1,160; 148/43 | −28 |
| recalled-only | Control | D | R8 | — | 1,068; 65/27 | 1,187; 127/27 | +119 |
| plan-only | Control | T | R3 | — | 1,202; 66/23 | 853; 128/19 | −349 |
| refs | Eligible | D | R4 | — | 1,275; 83/4 | 1,147; 145/5 | −128 |
| routine-progress | Control | T | R2 | No / no | 1,643; 65/13 | 1,347; 127/44 | −296 |
| routine-check | Control | T | R1 | — | 646; 58/2 | 1,078; 120/13 | +432 |
| checkpoint | Eligible | D | R6 | Yes / yes | 1,183; 85/26 | 1,234; 147/43 | +51 |
| atlas | Eligible | D | R9 | Yes / yes | 949; 84/7 | 1,214; 146/36 | +265 |
| cursor | Eligible | T | R5 | Yes / yes | 1,410; 86/10 | 1,376; 148/44 | −34 |
| scope | Eligible | D | R10 | Yes / yes | 1,025; 87/9 | 1,287; 149/33 | +262 |

- **Omissions:** 4/10 D calls had no proposal. The eligible omission was `refs`: the model picked the right bucket but didn't draft the reusable routing decision. The other three omissions were controls. T had no lesson in any reply.
- **False positives vs. support:** 1/4 controls attempted a draft; 0/4 passed both axes. The one unsupported proposal (1/6 emitted) took a single Atlas page inspection and turned it into a general guarantee about committed bounded reads. Citation [11] was valid for that event; it did not entail the rule. The assessor's reason called it a restatement of the event type; the single instance also doesn't establish the broader conclusion. For the other five proposals the assessor cited the specific symptom and the documented decision, verification or constraint.
- **Ordinary task answers:** Both arms named the assigned marker in all six eligible tasks. The intentionally opaque `checkpoint` task is not evidence of retrieval or recognition of a paraphrase: both arms were given the WAL event directly. On routine progress, T overclaimed that review was complete and D inferred it was in progress; the event establishes only that the page was inspected. Lesson scoring did not credit or penalize these separate task-answer claims.
- **Paired cost (D minus T):** +62 input tokens on every pair; median +20.5 output tokens (mean +18; range −4 to +37); median +11.5 ms wall time (mean +29.4 ms; range −349 to +432 ms). The wall-time differences change sign and are noisy; one run per pair is not a reliable estimate of a latency effect.

## Exact generation replies

Each line is a JSON object with the task-only (T) and optional-draft (D) reply strings, including line breaks.

~~~jsonl
{"case":"receipt","T":"Juniper-43.","D":"Lane: juniper-43.  \nLesson [11]: If the ordinary lane emits duplicate banners, select the explicitly designated alternate lane (e.g., juniper-43) for the violet receipt banner check."}
{"case":"recalled-only","T":"No. The event only says older recalled text *suggests* using a special lane; it does not confirm the special lane.","D":"No. The event only says older recalled text “suggests” using a special lane; it does not confirm the special lane."}
{"case":"plan-only","T":"Nothing was decided about the pager; the event only proposes that tomorrow they could try adding a different pager.","D":"Nothing was decided; the assistant only proposed that tomorrow they could try adding a different pager."}
{"case":"refs","T":"Copper-59","D":"Copper-59."}
{"case":"routine-progress","T":"The Atlas page review is complete, with the existing page inspected.","D":"The Atlas page review is in progress, with the existing page having been inspected.  \nLesson [11]: When a bounded-file-read event is committed, it confirms that the specified file or page was successfully accessed and reviewed."}
{"case":"routine-check","T":"Yes.","D":"Yes. The requested pytest check passed (exit code 0)."}
{"case":"checkpoint","T":"Frozen drumbeat follows a restart → use the route where kestrel-38 isolated the blocked writer from the read probe.","D":"Route: kestrel-38.\n\nLesson [11]: If a durability rehearsal shows the SQLite write-ahead log stalled after recovery, isolate the blocked writer from the read probe using kestrel-38."}
{"case":"atlas","T":"review label: heron-71","D":"Review label: heron-71\n\nLesson [11]: If Atlas timeline tiles show stripes after refresh, the approved review label for renderer trace routing is heron-71."}
{"case":"cursor","T":"The rehearsal marker is **larch-86**.","D":"Marker: larch-86.  \nLesson [11]: If cursor pagination turns amber on page two, check the marker that exercised page two (e.g., larch-86) rather than the default marker."}
{"case":"scope","T":"The enrollment lane is basalt-24.","D":"Basalt-24.  \n\nLesson [11]: A magenta workspace fingerprint during enrollment indicates the replaced scope simulation, and its only enrollment lane is basalt-24."}
~~~

These counts describe ten selected, short synthetic tasks at one setting with one label-blind model judgment. They establish neither organic durable reuse nor a safe automatic-save policy. Nothing here tests private proposal ingress, human approval, checkout routing, restart behavior, or real recall.
