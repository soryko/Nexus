# A01 — shared contract evidence

Based on A00 (`9fea21f`). No v2 profile or persistence is enabled here. The lead observed pytest fail collection on the missing graph module before implementation (exit 2). The initial combined contract/core/transport gate reached 42 passed. This did not close the task: independent review found missing read-side graph fields, scope/time and authorization metadata, timestamp rules, historical index limitations, and an incomplete failure response.

Graph repair: the worker observed nine new tests fail before implementation; the revision/evidence DTOs now preserve recorded assertion time and sequence, evidence origin, and exact captured UTF-8 bytes. UTC validity tests include fractional-second precision.

Retrieval repair: the worker reported five expected red tests and a further failing empty-revision test before implementation. The new tests exercise the byte floor, rationale depth, revision rank, authorization epochs and time cutoffs. Historical index unavailability cannot be labeled complete. The worker reported 19 passing retrieval-edge and shared tests.

Operation failure repair: the worker observed seven failing tests and one pass before implementation, then all eight passed. The model-facing response exposes only the safe code and retryability, not the owner reconciliation reference. Legacy errors remain text-only.

After all three repairs landed, the lead ran `uv run pytest -q`: exit 0, 707 passed, 3 skipped, 34 deselected, 1 expected failure in 96.81 seconds. Skips/deselections/xfail match the A00 baseline; no external host certification is implied.
