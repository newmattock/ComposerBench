# Agent production workflow (P0)

ComposerBench now exposes transactional edits and measured revisions through its
Python facade and MCP server. A passing technical check is not a claim that a
piece sounds better.

For LUFS/true-peak diagnostics, loudness-matched listening, non-destructive
resampling and revision audition artifacts, see [Sonic workflow](SONIC_WORKFLOW.md).
Those tools supplement the objective/rollback loop; they do not convert technical
measurements into an automatic artistic-quality score.

## Connect

```python
from composerbench.client import ComposerBench

mb = ComposerBench.connect_magda(cli_path, project_path, serve=True)
# For a new project only:
await mb.initialize()
# Always close a session you opened:
# await mb.close()
```

For MCP, run `python -m composerbench.mcp.server` with:

- `COMPOSERBENCH_ADAPTER=magda_serve` (or `magda` with `COMPOSERBENCH_SERVE=1`)
- `COMPOSERBENCH_CLI` pointing to the installed MAGDA binary
- `COMPOSERBENCH_PROJECT` pointing to the project

`magda` without serve retains the process-per-operation adapter. `fake` is an
emulator: its audio does not model musical edits and cannot validate production
quality. The MCP stdio server keeps one event loop for its entire session.

Persistent transactions require an engine owned by the adapter. An externally
attached `MAGDA_SOCK` session remains usable for inspection, but transactions and
restoration refuse to alter it: ComposerBench cannot guarantee that engine's lifetime
or safely reload its state. Use a dedicated connection without `MAGDA_SOCK` for
agent production transactions.

## Tool sequence

1. `music_capabilities`, `music_project_inspect`, `music_track_inspect`.
2. `music_devices_inspect` / `music_parameters_inspect` for current handles,
   parameter names, units and legal ranges.
3. `music_batch_edit` for deliberate edits, or `music_revision_run` for candidates
   that should only be retained when they improve an explicit objective.
4. `music_render` / `music_critique` for auditable audio and advisory findings.
5. `music_revision_history` for decisions and evidence;
   `music_snapshot_create` / `music_snapshot_restore` for explicit checkpoints.

Other tools: `music_volume_set`, `music_parameter_set`, `music_fx_add`,
`music_roles_list`, `music_role_apply`, plus existing track/clip/note, intent and
plan tools. Every tool publishes an input and output schema, validates its input,
and returns an object in `structuredContent` alongside JSON text. Errors are
reported with `isError=true` and a structured message.

Track, clip and device projections include `stable_id` handles (`mb:...`),
persisted in `.musicbench/handles.json` and included in snapshots. Public facade
and MCP operations accept these handles and resolve them against live state on
each use, including after engine renumbering. Existing numeric `id` fields remain
for compatibility. Use `stable_id` for agent references across reconnects.

Descriptors use track/clip names and device name/order, which P0 edits preserve.
External rename/reorder operations are not coordinated and require re-inspection.
Name references must be unambiguous. A new device must be loaded and inspected
before a parameter-edit batch can validate its parameter metadata.

## Batch example

```python
await mb.batch([
    {"type": "create_track", "name": "lead"},
    {"type": "load_device", "track": "lead", "name": "4osc"},
    {"type": "create_clip", "track": "lead", "name": "phrase",
     "start_beats": 0, "length_beats": 4},
    {"type": "add_notes", "track": "lead", "clip": "phrase",
     "notes": [{"start": 0, "duration": 1, "pitch": 60, "velocity": 90}]}
])
```

The batch schema also supports `set_tempo`, `set_volume`, `add_fx`,
`set_parameter`, `transpose_clip` and `apply_role`. The full batch is checked
before its first edit, including capabilities, track/clip references and note
positions. Failures in execution or saving trigger restoration. Partial semantic
role application is a failure, not success with an unresolved parameter.

## Measured revision example

```python
record = await mb.revise(
    goal="Lower the overall level while keeping the notes unchanged",
    actions=[{"type": "set_volume", "track": "lead", "volume": 0.6}],
    objectives=[{"metric": "rms_dbfs", "direction": "decrease",
                 "min_improvement": 1.0}],
    timeout_seconds=120,
)
```

Each objective names a metric, `increase` / `decrease` / `target`, a positive
`min_improvement` in the metric's units, and optionally a named `section`.
`target` also requires a numeric `target`. At least one objective must improve
and none may regress by its specified threshold. The goal string supplies
context; it is not automatically translated into a musical-quality score.

Metrics: RMS dBFS, sample peak dBFS, crest factor dB, spectral centroid Hz,
low/mid/high spectral proportions, stereo width, and transient density.
Peak and crest objectives currently apply to the whole project. These are
technical or heuristic measurements, **not LUFS, true peak, or listener ratings**.
A gain-only change cannot win a brightness objective merely by being louder.

The loop captures baseline and final candidate renders, full before/after project
projections, applied actions, constraint results, objective evidence and snapshot
ID. Automatic fixes, if enabled, are rendered and measured again; failure of that
render or its checks rejects the revision. Diffs describe the final candidate.
Critic findings are advisory artistic heuristics, separate from technical gates.
The new facade only enforces symbolic energy relations when intent explicitly
sets `constraints.enforce_energy_arc`; direct legacy loops without Intent retain
the original symbolic constraints for compatibility.
With `critic=True` and no explicit objectives, a reduction in critic penalty can
justify acceptance and is labelled `heuristic_critic`.

Existing audio with neither objectives nor enabled critic is restored with
`needs_goal_objectives`. Its candidate render remains available for human review.
Use `batch` to apply an edit deliberately without claiming improvement. Initial
creation can pass technical checks without a baseline and is labelled
`initial_creation`. Non-rendered legacy loops are labelled
`technical_validation_only`; audio objectives cannot pass without rendering.

Records are persisted atomically under `.musicbench/traces/` (or `work_dir/traces`
for direct loops). A new loop with the same sidecar receives the ten most recent
records as proposer context, along with intent, plan, devices, baseline analysis,
capabilities, objectives and current-loop history.

## Safety and analysis contracts

- Transactions serialize on an adapter instance, snapshot the DAW plus artistic
  sidecar state, and restore on exception, rejection, timeout or cancellation.
  Rollback failure is raised explicitly and never reported as successful recovery.
  External edits by another process during a transaction are not coordinated.
- Disk snapshots preserve project, media, aliases, device mappings, intent, plan
  and other sidecar state. Snapshot/render/trace/evaluation history is excluded
  from recursive copying. Restore copies media instead of consuming the snapshot,
  removes state added after the checkpoint and reloads an owned persistent engine.
- Cancelled CLI subprocesses are killed and awaited before restoring files.
- Decoding accepts 8/16/24/32-bit PCM and 32/64-bit IEEE-float RIFF/WAVE, including
  extensible PCM/float format tags. Truncated and nonfinite audio is rejected.
- Analysis reads the actual sample rate and uses all channels for level and
  spectrum; stereo width uses the left/right mid-side ratio. Antiphase audio
  does not disappear from spectral analysis.
- Plans use one-based bars and timeline-absolute quarter-note beats. Shared
  section conversion respects `beats_per_bar`; 3/4 and 4/4 are regression-tested.
- The default rendered sample-peak ceiling is -0.5 dBFS and audibility floor is
  -60 dBFS. Override these deliberately for the project's requirements.

## Verification

Run `.venv/bin/python -m pytest -q` from the ComposerBench directory. The `test_p0_*`
suites cover format/rate/stereo analysis, transaction failures and recovery,
objective comparisons, final-fix validation, MCP schemas and production flows.
Live tests skip if the local MAGDA binary is unavailable. They use temporary
projects, including a persistent-engine restore/render flow and both CLI and
persistent MCP subprocess transports.
