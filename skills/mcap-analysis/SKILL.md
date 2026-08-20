---
name: mcap-analysis
description: Perform token-efficient, read-only APA MCAP root-cause analysis by locating the abnormal event first, then tracing only the directly related topics backward through producer-consumer boundaries.
---

# MCAP Analysis

Use a time-first, backward-slice investigation. Keep all work read-only. Do not build a global causal
map, enumerate subsystem-wide hypotheses, decode broad payloads, or inspect source before an abnormal
event window and observable output have been identified.

## 1. Inventory Once

Run `scripts/mcap_inventory.py <recording> --top 40` once per recording and reuse its result. Use the
optional routing hints in the installed pack registry (`references/<pack>_topic_registry.yaml`) to choose candidate patterns and fields;
confirm exact names from the recording/schema. Add
`--focus-topic` for known APA state/output topics or `--focus-regex` for the relevant topic family so
low-frequency diagnostic topics are not lost from the top-N list. Record readability, coverage, primary
clock, ordinary topic counts, focus topics, and missing critical signals. Inventory is metadata, not
evidence of a cause. Do not repeat it in manager and specialist stages.

If the user supplied a symptom but not entry/window/depth, proceed without another question:

```text
Entry: auto
Window: locate from the symptom, initially no wider than needed
Depth: quick, or standard when the user explicitly requests root cause
```

Ask the user only when no recorded signal can represent the reported symptom or several materially
different symptom interpretations would change the investigation.

## 2. Locate the Event Before Explaining It

First identify an observable symptom anchor. Prefer, in order:

1. a user-provided trigger time or event ID;
2. a terminal or safety state transition;
3. the reported abnormal output, such as trajectory loss, stop reason, direction, gear, command, or
   success/failure result;
4. a low-volume state or diagnostic signal near the visible symptom;
5. a field-only scan of candidate output/state topics when no direct anchor is known;
6. a derived metric such as trajectory count, minimum clearance, stop distance, or tracking error when
   the symptom has no explicit error/state topic.

Do not read source or construct mechanism hypotheses during event localization. Compare only the
minimum fields needed to identify:

```text
primary_clock | anchor_topic.field | last_good | first_bad | symptom_time |
initial_window | confidence | missing_clock_or_signal
```

Use one primary clock. Check publish/header time only for the selected anchor and later causal topics;
do not align every topic in the recording. Use `scripts/mcap_event_locator.py` with an explicit predicate
(`--condition gt|gte|lt|lte|eq|ne|regex` + `--threshold/--value/--pattern`,
`--debounce-count`, `--min-duration-ms`) instead of asking the model to read a broad payload dump.
State the abnormality as a computable condition (e.g. `mirror_fold_dist lt 0.15`); reserve
`--condition changed --tolerance N` for enum/state fields, never as the default for numeric signals.
The result carries `status` (`transition_found`/`all_good`/`bad_from_start`/...), `last_good`,
`first_bad`, `bad_duration_ms`, and `missing_or_null_count`; treat missing samples as an
observability signal, not as normal. If no explicit anchor is found, perform one
state/output scan and one derived-metric scan. If both fail, stop and report the minimum user input or
missing topic required.

## 3. Start From the First Bad Observable Output

Treat the anchor as the current consumer output, not automatically as the root cause. Determine its
exact recorded identity, validity, frame, unit, mode, gear, and evaluation time. Then resolve only its
direct producer and direct material inputs.

Create a compact trace packet. Use `scripts/mcap_slice.py` to read all selected topics in one narrow
window and project only the requested fields:

```text
hop | window | consumer_output | last_good/first_bad | direct_input_topics(max 3) |
one discriminating check | result | next_hop_or_boundary
```

Keep the initial window around the last-good/first-bad boundary. Expand it only to reach the preceding
relevant state update or the producer sample actually consumed.

## 4. Trace Backward One Dependency Hop at a Time

At each hop:

1. Compare the current output at the last-good and first-bad evaluations.
2. Start with at most three direct inputs that can change that output. If source, schema, or recorded
   evidence proves another determinant is material, add only one input at a time and record why it was
   added. Do not use a hard three-input limit when it would hide a real dependency.
3. Check identity, validity, freshness, frame, unit, reference point, mode, gear, configuration, and
   producer/consumer ordering only for those signals.
4. If an input diverged earlier, make that input the next current output and move one hop upstream.
5. If direct inputs remain valid and expected while the output diverges, stop backward expansion and
   place the fault boundary inside the current transform, state, policy, or configuration.
6. If an input is unrecorded, distinguish an observability gap from a normal input. Read the smallest
   source/config chain needed to identify what evidence is missing.

Do not inspect sibling topics, adjacent modules, full schemas, or source-wide call chains unless the
current hop produces concrete contradictory evidence. A second domain is allowed only after a named
producer-consumer boundary is identified.

## 5. Analyze the Located Boundary

Only after the boundary is located, maintain one to three credible mechanism-level hypotheses for that
boundary. Examples include stale or mismatched input, transform/frame/unit defect, state/reset mismatch,
policy/configuration behavior, numerical/discretization effect, fallback/default path, timing/order,
tracking, or runtime-version mismatch.

Use a compact ledger:

```text
hypothesis | predicted boundary observation | support | contradiction |
next discriminating check | status
```

Trace only material symbols through source and runtime configuration. Verify producer, consumer,
update cadence, state/reset lifecycle, branch predicate, default/fallback, and recording-time binary or
configuration provenance when relevant. Test one counterfactual or invariant before calling the
mechanism confirmed.

Classify causal roles separately:

- initiating cause;
- necessary condition;
- amplifier;
- downstream consequence;
- observability gap.

## 6. Conditional Checks

Use a technique only when the active hop requires it:

- Intermittent/stateful input: reconstruct `input -> gate/state/timer -> output` per evaluation; use
  `scripts/mcap_frame_correlation.py` when frame identity or freshness distinguishes new input from
  repeated use. Always pass an explicit clock pair (`--source-clock/--target-clock`, default log_time)
  and `--max-age-ms` from the interface contract; `stale_matches` means the consumer reused an expired
  frame and must not be read as "upstream input was normal". `--source-id-field` is optional; missing
  IDs are counted in `quality.source_id_missing` instead of aborting the analysis.
- Geometry/numerics: verify frames, reference point, footprint/inflation, sampling, interpolation,
  resolution, index conversion, and threshold comparison.
- Perception/association: verify existence, identity continuity, class, geometry, validity, freshness,
  and the representation consumed downstream.
- Planning/decision: separate candidate generation, feasibility, ranking, selection, replanning, gate,
  fallback, and published output.
- Control: align planned, commanded, and measured behavior with signs, units, gear, latency, and
  saturation.
- Version/configuration: separate inspected commit, checkout configuration, packaged binary, vehicle
  variant, and recording-time parameters.

## 7. Depth and Stop Conditions

- `quick`: inventory once, locate the event, and complete one backward hop. Return the current fault
  boundary and the next evidence question.
- `standard`: continue bounded hops until the first observed boundary is located, then run one mechanism
  check and one downstream propagation check.
- `deep`: use only when requested or when credible mechanisms remain unresolved; add disconfirming and
  counterfactual checks, provenance, and change-coverage assessment.

Stop when the requested profile is satisfied. Do not continue merely to complete a subsystem survey.
Do not call a root cause confirmed unless the first causal divergence, mechanism, downstream propagation,
and contradiction of credible alternatives are evidenced. Otherwise report a likely boundary/cause and
the minimum next evidence.

## 8. Evidence and Output Rules

- Return slices as `time | topic | field | value | frame/unit | validity | clock`.
- Prefer `mcap_event_locator.py` for the temporal packet and `mcap_slice.py` for each hop. Their output
  is compact evidence, not a root-cause conclusion.
- All scripts share `mcap_common.py` (one `dotted_get`, one streaming reader) and accept
  `--max-records/--max-bytes/--timeout-secs`. A bounded read reports `"status":"partial"` with the
  limit that was hit; narrow the window or raise the limit explicitly — never treat a partial read
  as complete evidence. Topics json-encoded in the recording are decoded in-process (no `mcap` CLI
  needed); other encodings stream through `mcap cat` line by line without buffering the full output.
- Regression tests live in `tests/run_tests.py` (self-building fixture); run them after changing any
  script.
- Every script emits a `timing` object (`total_secs`, `read_secs`, `analyze_secs`; manifest adds
  `hash_secs`). Carry these into the report's final timing breakdown table
  (`step | tool/agent | wall_secs | notes`); they are the measurement basis for speed optimization.
  Do not re-run a script just to measure it.
- Start every case with `scripts/mcap_case_manifest.py <recording> --case-id <id>`; its JSON
  (recording sha256, time range, tool/registry versions, optional source commit) is the provenance
  header of the report. Two analyses of the same case must reference the same manifest hash.
- the installed pack registry (`references/<pack>_topic_registry.yaml`) v2 carries per-interface contracts: `max_age_ms` feeds
  `--max-age-ms`, `clock.preferred` feeds `--source-clock/--target-clock/--clock`, and
  `predicate_hint` is the default abnormality definition for `mcap_event_locator.py`. Entries with
  `confirmed: false` are seeds — verify against the recording, and report registry corrections as a
  follow-up, never edit the registry mid-case.

## Read-only enforcement (deployment)

Prompts alone do not guarantee read-only behavior. Deploy with runtime enforcement:
- Mount the source repository read-only (or a read-only checkout owned by another user).
- Give agents a separate scratch directory for analysis products; nothing else is writable.
- Keep `sandbox_mode = "read-only"` on every agent; a write attempt is a case-level failure,
  not a footnote in the report.
- A slice may report `frame`, `unit`, or `validity` as unknown/null when the recording does not expose
  them; do not infer those values from the topic name.
- Pass compact trace packets between manager and specialists; never pass raw payloads, full trajectories,
  full schemas, full topic inventories, or large source excerpts.
- Reuse extracted facts across hops. Do not make another agent rediscover inventory, clocks, or the event
  window.
- Treat absence from an unrecorded or filtered topic as unknown, not negative evidence.
- Separate observed facts, source-confirmed behavior, causal inference, hypothesis, and unknowns.
- For a proposed change, first establish whether the changed branch lies on the traced causal path; then
  report direct/conditional/no effect, unchanged mechanisms, regression risk, and missing validation.
- Never prescribe an unvalidated calibration value or claim vehicle safety, production readiness, or a
  verified fix from MCAP and source analysis alone.


## Orchestration rules (manager) — moved from agent config, single source of truth

Workflow:
0. Run `mcap_case_manifest.py <recording> --case-id <id> [--repo <src>]` once and keep its JSON as the case provenance header; include it in the final report. Same portable path convention as the other scripts.
1. Run the installed `mcap-analysis/scripts/mcap_inventory.py` once with relevant `--focus-topic` or `--focus-regex` values. In a shell, the portable path is `${CODEX_HOME:-$HOME/.codex}/skills/mcap-analysis/scripts/mcap_inventory.py`. Reuse the compact result; never ask a specialist to inventory the same recording again.
2. Use the installed `mcap-analysis/scripts/mcap_event_locator.py` on candidate output/state fields. In a shell, the portable path is `${CODEX_HOME:-$HOME/.codex}/skills/mcap-analysis/scripts/mcap_event_locator.py`. Always state the abnormality as an explicit predicate: `--condition gt|gte|lt|lte|eq|ne|regex` with `--threshold/--value/--pattern`, plus `--debounce-count` and `--min-duration-ms` for noisy signals; reserve `--condition changed --tolerance N` for enum/state fields. Read `status`, `last_good`, `first_bad`, and `missing_or_null_count` from the result; `missing_or_null_count > 0` is an observability signal, not proof of normality. Use the optional `references/{{PACK}}_topic_registry.yaml` routing hints, but confirm exact topic/field names from inventory/schema. Use user time/event ID first; otherwise scan low-volume terminal/state/output fields, then one derived-metric scan when the symptom has no explicit state/error anchor.
3. Before reading source or forming mechanism hypotheses, produce a temporal packet:
   - primary clock;
   - anchor topic.field;
   - last-good, first-bad, and visible-symptom times;
   - smallest initial window and confidence;
   - unavailable clock or signal.
4. If no anchor is found, perform one derived-metric scan for trajectory count, clearance, stop distance, or tracking error. If still absent, stop with the minimum missing user input or recorded signal. Do not compensate with a repository-wide search.
5. Treat the first-bad anchor as the current consumer output. Use `mcap_slice.py` to extract the narrow window for the output and direct inputs in one call; if its final status line reports `"status":"partial"`, narrow the window or raise limits explicitly instead of ignoring the truncation. When judging whether an upstream input was live, use `mcap_frame_correlation.py` with an explicit clock (`--source-clock/--target-clock`) and `--max-age-ms` from the registry or interface contract; treat `stale_matches` as reused expired input, never as fresh normal input. Start with at most three material inputs; add one only when evidence proves it is determinant, recording the reason. The first specialist question must distinguish whether an input was already bad or the current module first produced the divergence.
5b. Timer/duration-gated boundaries (a condition must hold continuously for N ms before acting): the PRIME suspect is flapping — a gating input repeatedly entering/leaving its threshold resets the timer. For EVERY gating input, count transitions over the window with `mcap_event_locator.py --condition changed --debounce-count 1` (NO debounce: debounce exists to hide noise, but here the flapping IS the evidence). Many transitions on one input = timer-reset mechanism; name that input's producer as the next hop.
5c. Unrecorded direct input: do NOT stop at "observability gap". First find what the field is derived from (registry `derived_from`/`upstream`, or source code) and test the recorded upstream signals as indirect evidence — e.g. a derived clearance field traces back to the obstacle topic; obstacle jitter recorded upstream is valid proxy evidence for the derived field flapping. Declare the gap only when neither direct nor upstream proxy evidence exists, and say which proxies you checked.
6. Depth profiles are also LATENCY budgets (specialist spawns are the most expensive step):
   - `quick`: do EVERYTHING solo — manifest, inventory, event location, one backward hop, report. Spawn NO specialist. Total LLM turns for the case: entry + this manager only.
   - `standard`: continue hops solo while the trace stays inside one domain; spawn a specialist ONLY when the trace crosses a named producer-consumer domain boundary. Typical case: zero or one spawn.
   - `deep`: full specialist chain per domain, plus disconfirming checks, only on explicit user request.
7. When you do spawn (standard: cross-domain boundary; deep: per domain), launch one specialist at a time. Pass only the temporal packet, current hop, selected topics/fields, and one evidence question. Do not pass raw payloads, full schemas, full trajectories, full inventory, or unrelated history. Chain consecutive scripts in one turn — run manifest + inventory + event location back-to-back without pausing to narrate between them.
8. A specialist must return a trace packet:
   `hop | window | consumer output | last-good/first-bad | direct inputs(max 3) | check | result | next hop or boundary | duration_secs`.
   `duration_secs` is the specialist's wall-clock time for that hop (its own measurement).
9. If an input diverged earlier, make it the next current output and route upstream. Spawn a different/second specialist only after a named producer-consumer boundary crosses domains. Do not start multiple broad domain reviews.
10. Only after the boundary is located, maintain one to three credible mechanism hypotheses for that boundary. Request the smallest source/config trace and one disconfirming or counterfactual check.
11. Synthesize without rereading all evidence. Separate first observed divergence, first causal divergence, initiating cause, necessary conditions, amplifiers, consequences, and observability gaps.
12. For a proposed change, first verify that its branch lies on the traced path; separate direct, conditional, and no effect, unchanged mechanisms, regression risk, and missing validation.
13. Call a cause confirmed only when the causal divergence, source/config mechanism, downstream propagation, and contradiction of credible alternatives are evidenced. Otherwise report the current boundary and minimum next evidence.

Routing packet — pass EXACTLY this JSON object to a specialist, no free-form prose around the fields; a specialist must reject a packet with missing fields:
```json
{
  "case_id": "...",
  "recording": "...",
  "hop": 1,
  "clock": "log_time|publish_time|header_time",
  "anchor": {"topic": "...", "field": "...", "last_good": 0.0, "first_bad": 0.0},
  "window_secs": [0.0, 0.0],
  "current_output": {"topic": "...", "field": "..."},
  "inputs": [{"topic": "...", "field": "...", "max_age_ms": 100}],
  "specialist": "{{PACK}}_<domain>_analysis",
  "question": "one discriminating evidence question",
  "stop_condition": "...",
  "started_at_monotonic_hint": null
}
```
`inputs` holds at most three entries. Specialists return the trace packet plus `next_hop` (same JSON shape) or `boundary`.

Token and scope budget:
- Inventory and time-location summary: at most 10 bullets or one compact table.
- One specialist active at a time; one dependency hop per packet. Spawning is the dominant latency cost: never spawn for a hop you can complete solo with the scripts, and never spawn in `quick`.
- Never repeat stable inventory, clock, event-window, or already extracted values.
- Do not invoke `mcap cat` separately for each selected field when `mcap_slice.py` can combine the topic reads.
- No global causal map, subsystem survey, or hypothesis ledger before the event and boundary are located.
- Keep the final synthesis concise and evidence-calibrated. Never claim production safety or a validated fix.

Visualization staging (on by default):
- Staging root: `~/mcap-reports` (MCAP_REPORT_DIR overrides; =off disables). `<root>/<case_id>/`
  is the ONLY writable location. Sandbox-denied writes degrade to text-only (say so, not an error).
- Save each script's stdout verbatim as it runs: 01_manifest / 02_inventory / 03_locator /
  04_slice_hop<N> / 05_corr_hop<N> .json.
- At synthesis stage your findings as: 06_trace.json (hops), 07_conclusion.json (REQUIRES
  root_cause — one decisive verdict sentence, never "cannot confirm"; uncertainty goes in
  confidence + upgrade_evidence; plus temporal_packet, causal_chain, recommendations,
  gating_checks, timing_breakdown), 08_code_refs.json (verbatim snippets), 09_timeline.json
  (events each REQUIRING topic; role/caused_by mark the causal flow).
- Then run `mcap_report.py --case-dir ...` (renders the canonical report; exit 2 = defects
  listed in its output — fix the JSONs and rerun) and `mcap_plot.py --case-dir ...` (HTML).
  Deliver report.md verbatim; never hand-write a substitute report or HTML.

Timing discipline:
- Every script result carries a `timing` object (`total_secs` / `read_secs` / `analyze_secs`); copy it into your working notes per step, never re-run a script just to re-measure.
- The final report MUST end with a timing breakdown table: `step | tool/agent | wall_secs | notes` covering manifest, inventory, event location, each hop (specialist duration_secs), and synthesis, plus the case total. This is the data source for pipeline speed optimization; missing rows mean the step is unaccounted, not free.

Routing hints:
{{ROUTING_HINTS}}

Invoked directly (not via {{PACK}}_problem_analysis)? You still owe the full report contract:
deliver mcap_report.py's report.md; every evidence row carries topic:field.

Version-alignment: the recording reflects the code that PRODUCED it. If traced files changed
after the recording date (check read-only `git log`), analyze the RECORDING-ERA code via
`git show <commit>:<file>`, state that commit, and report a version mismatch rather than
"no cause found" from post-fix code. Old recordings never validate new code — fixes need a
new recording from the fixed binary.

Field-resolution: protobuf decodes to camelCase (scripts auto-try snake<->camel). All-null
fields mean a wrong path — inspect one raw message, fix the path, rerun. NEVER abandon the
standard scripts for hand-rolled decoding or hand-written HTML; staging + mcap_report/plot
are the only sanctioned pipeline.

