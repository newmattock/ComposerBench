# ComposerBench × MAGDA

## Master Build Prompt for an Autonomous AI Music-Making Harness

You are the principal engineer, music-technology architect, agent-systems engineer, and research engineer responsible for building **ComposerBench**.

ComposerBench is an **autonomous AI music composition, production, evaluation, and iteration harness built around the open-source MAGDA DAW ecosystem**.

The core principle is:

> **Do not build another DAW. Build the intelligence layer that allows AI agents to use a real DAW autonomously.**

MAGDA is the workstation.

Tracktion Engine/JUCE are the low-level audio infrastructure beneath MAGDA.

Plugins and built-in devices provide synthesis and effects.

ComposerBench provides:

- autonomous musical reasoning,
- composition planning,
- persistent artistic intent,
- structured project understanding,
- long-horizon memory,
- motif tracking,
- semantic editing,
- rendering,
- listening,
- musical and audio analysis,
- self-critique,
- comparison,
- revision,
- experimentation,
- benchmarking,
- model orchestration,
- and an agent-friendly external control interface.

The ultimate system should let an AI agent work on a song similarly to how a strong coding agent works on a software repository.

The desired loop is:

```text
understand intent
      ↓
inspect project
      ↓
form musical plan
      ↓
make targeted edits
      ↓
validate state
      ↓
render relevant section
      ↓
listen / analyze
      ↓
critique
      ↓
compare against previous version
      ↓
keep or revert
      ↓
update plan
      ↓
repeat
```

This loop is the heart of ComposerBench.

---

# 1. Fundamental Architectural Decision

ComposerBench MUST NOT duplicate capabilities that MAGDA, Tracktion Engine, JUCE, or hosted plugins already provide adequately.

Do not begin by implementing:

- a custom audio engine,
- a custom MIDI engine,
- a new piano roll,
- a new mixer,
- a new plugin host,
- a new automation engine,
- a new waveform editor,
- a new DAW timeline,
- a new audio routing graph,
- a new synth,
- or a new effects rack.

Those belong to the workstation layer.

Instead:

```text
┌─────────────────────────────────────────────┐
│                 ComposerBench                  │
│                                             │
│ artistic intent                             │
│ planning                                    │
│ composition memory                          │
│ musical reasoning                           │
│ autonomous iteration                        │
│ evaluation                                  │
│ motif lineage                               │
│ semantic transformations                    │
│ model orchestration                         │
│ benchmarking                                │
│ multimodal critique                         │
└─────────────────────┬───────────────────────┘
                      │
             structured control API
                      │
┌─────────────────────▼───────────────────────┐
│                   MAGDA                     │
│                                             │
│ project/session state                       │
│ tracks and clips                            │
│ MIDI                                        │
│ audio                                       │
│ arrangement                                 │
│ mixer                                       │
│ automation                                  │
│ racks                                       │
│ devices                                     │
│ plugin hosting                              │
│ rendering                                   │
│ transport                                   │
│ existing DSL / agents                       │
└─────────────────────┬───────────────────────┘
                      │
             Tracktion Engine / JUCE
                      │
             ┌────────┴─────────┐
             │                  │
        Instruments          Effects
       / generators         / processors
```

ComposerBench should preferentially exist **outside MAGDA's core codebase**.

Only modify MAGDA when required capabilities cannot cleanly be accessed externally.

---

# 2. Upstream-First Rule

Treat MAGDA as an active upstream open-source project.

Do not assume the architecture described in this prompt exactly matches the version currently checked out.

Before designing anything:

1. Inspect the current MAGDA repository.
2. Read its README.
3. Read its architecture documentation.
4. Inspect its DSL implementation.
5. Inspect its current agent implementation.
6. Search for remote-control APIs.
7. Search for WebSocket support.
8. Search for MCP support.
9. Search for OSC support.
10. Search for plugin-host abstractions.
11. Search for project serialization.
12. Search for render/export APIs.
13. Search for undo/redo.
14. Search for session/project querying.
15. Search for automation APIs.
16. Search for tests showing intended use.

Search the repository before inventing new abstractions.

Whenever ComposerBench needs capability X:

```text
Does MAGDA already implement X?
         │
    yes ─┴─ no
     │       │
 reuse it    can X be exposed cleanly?
              │
         yes ─┴─ no
          │       │
       adapter   minimal upstream-style
                 MAGDA extension
```

Do not fork simply for convenience.

---

# 3. Repository Strategy

Prefer a structure such as:

```text
workspace/
├── magda-core/
│   └── upstream MAGDA checkout
│
└── composerbench/
    ├── composerbench/
    │   ├── adapter/
    │   ├── client/
    │   ├── agent/
    │   ├── composition/
    │   ├── analysis/
    │   ├── evaluation/
    │   ├── memory/
    │   ├── plugins/
    │   ├── benchmark/
    │   ├── models/
    │   ├── mcp/
    │   └── cli/
    │
    ├── tests/
    ├── examples/
    ├── benchmarks/
    ├── docs/
    └── pyproject.toml
```

ComposerBench should preferably be its own repository.

MAGDA may initially be:

- an adjacent checkout,
- a dependency,
- a submodule,
- or simply an externally running application.

Choose the least coupled solution that works.

---

# 4. Do Not Hard-Fork MAGDA Initially

Do not create a permanent divergent fork before proving it is necessary.

Prefer this hierarchy:

## Level 1

Use MAGDA entirely unmodified.

## Level 2

Add an external ComposerBench adapter.

## Level 3

Add a narrow remote-control or introspection interface to MAGDA.

## Level 4

Contribute generally useful MAGDA improvements upstream.

## Level 5

Maintain ComposerBench-specific patches only if there is no reasonable alternative.

Keep any MAGDA patches:

- small,
- isolated,
- documented,
- tested,
- and potentially upstreamable.

---

# 5. Licensing Constraint

MAGDA and its underlying dependencies have open-source licensing requirements.

Before distributing anything:

- inspect the current MAGDA license,
- inspect the current Tracktion Engine licensing terms,
- inspect JUCE licensing requirements,
- inspect licenses of bundled assets,
- inspect licenses of any plugins shipped with ComposerBench.

Do not assume that being able to build software locally means every distribution model is permitted.

ComposerBench should initially target an **open-source compatible architecture**.

Document licensing boundaries clearly.

---

# 6. First Task: Build and Understand MAGDA

Before writing ComposerBench features, establish that MAGDA works locally.

Perform the actual current upstream setup.

Verify:

```text
MAGDA repository clones correctly
submodules initialize
assets exist
debug build succeeds
tests run
application launches
audio device initializes
MIDI playback works
project can be created
project can be saved
project can be reopened
built-in instrument produces audio
plugin scanning works if supported
render/export works if supported
```

Record exact environment assumptions.

Do not claim integration success merely because compilation succeeds.

Produce audible output.

---

# 7. MAGDA Capability Audit

Create:

```text
docs/MAGDA_CAPABILITY_MAP.md
```

Document at least:

```text
project querying
track querying
clip querying
MIDI querying
audio clip querying
device querying
plugin parameter querying
automation querying
transport querying

track creation
track deletion
clip creation
MIDI editing
audio placement
automation editing
plugin loading
plugin parameter modification
routing modification
rendering

undo
redo
transactions
selection
snapshots
project save
```

For each capability record:

```text
supported
partially supported
internal only
not supported
unknown
```

Also identify its access path:

```text
DSL
C++ API
remote API
WebSocket
MCP
OSC
other
```

This document should drive ComposerBench architecture.

---

# 8. Primary Integration Layer

ComposerBench should communicate with MAGDA using the highest-level structured interface that provides sufficient control.

Preferred order:

```text
existing MAGDA structured remote API
        ↓
existing MAGDA DSL interface
        ↓
existing MCP/WebSocket interface
        ↓
thin remote bridge over MAGDA internal APIs
        ↓
direct C++ integration only if unavoidable
```

Do not use screen automation as the primary interface.

Do not make agents:

- click coordinates,
- visually hunt for buttons,
- drag piano-roll notes,
- manipulate plugin windows,
- or navigate menus via screenshots.

The system should manipulate musical state semantically.

---

# 9. ComposerBench Adapter

Create an adapter abstraction.

Conceptually:

```python
class DawAdapter:
    async def get_project(self): ...
    async def get_tracks(self): ...
    async def get_track(self, track_id): ...
    async def get_clips(self, ...): ...
    async def get_notes(self, ...): ...
    async def get_devices(self, ...): ...
    async def get_parameters(self, ...): ...

    async def create_track(self, ...): ...
    async def create_clip(self, ...): ...
    async def add_notes(self, ...): ...
    async def modify_notes(self, ...): ...
    async def delete_notes(self, ...): ...

    async def load_device(self, ...): ...
    async def set_parameter(self, ...): ...
    async def automate_parameter(self, ...): ...

    async def render(self, ...): ...

    async def undo(self): ...
    async def redo(self): ...
```

Then:

```python
class MagdaAdapter(DawAdapter):
    ...
```

ComposerBench's higher-level reasoning must not depend directly on MAGDA internals.

This allows future adapters for other environments without redesigning the composer.

---

# 10. Adapter Capability Discovery

Because MAGDA evolves, the adapter should expose capabilities.

Example:

```json
{
  "supports": {
    "midi_edit": true,
    "audio_clips": true,
    "plugin_parameters": true,
    "offline_render": true,
    "section_render": false,
    "automation": true,
    "undo": true
  }
}
```

Agents should not attempt unsupported operations.

---

# 11. Stable ComposerBench Entity References

Do not let agents depend entirely on array positions such as:

```text
track 3
plugin 2
clip 7
```

Resolve durable identifiers where possible.

Example:

```text
track:bass
track:drums
clip:verse-bass
device:bass-synth
section:climax
motif:main-theme
```

Map these to MAGDA's underlying identifiers.

Never assume track ordering is stable.

---

# 12. ComposerBench Sidecar State

Not every ComposerBench concept belongs inside the DAW project.

Store agent-specific metadata separately.

Example:

```text
project/
└── .musicbench/
    ├── intent.yaml
    ├── plan.yaml
    ├── motifs.json
    ├── sections.json
    ├── semantic_devices.json
    ├── evaluations/
    ├── renders/
    ├── comparisons/
    ├── traces/
    ├── memory/
    └── snapshots/
```

MAGDA remains authoritative for actual audio-production state.

ComposerBench remains authoritative for:

- intent,
- plans,
- semantic labels,
- musical relationships,
- agent memory,
- evaluation history,
- experimental metadata.

Do not duplicate the whole MAGDA project.

---

# 13. Artistic Intent

Every project should maintain structured intent.

Example:

```yaml
title: untitled

goal:
  "Create something intimate that gradually becomes overwhelming."

emotion:
  opening:
    - fragile
    - lonely

  middle:
    - anxious
    - expanding

  climax:
    - enormous
    - transcendent

constraints:
  tempo_range:
    min: 70
    max: 85

  vocals: false

  duration_minutes:
    min: 4
    max: 7

principles:
  - preserve a recognizable recurring motif
  - introduce additional layers gradually
  - climax must be dynamically and texturally largest section
```

Agents should read intent before every major planning cycle.

---

# 14. Composition Plan

Separate artistic intent from implementation.

Example:

```yaml
sections:

  intro:
    bars: 8
    target_energy: 0.12
    instruments:
      - piano
      - ambience

  development:
    bars: 16
    target_energy: 0.32

  escalation:
    bars: 16
    target_energy: 0.62

  climax:
    bars: 24
    target_energy: 0.95

  outro:
    bars: 8
    target_energy: 0.20
```

Plans should be editable as the agent learns from the composition.

---

# 15. ComposerBench Should Understand Musical Structure

Create semantic first-class concepts above the DAW timeline:

```text
Song
Section
Phrase
Motif
Chord progression
Rhythmic pattern
Instrument role
Energy curve
Tension curve
Transition
```

These may reference underlying MAGDA clips/notes/tracks.

Example:

```json
{
  "section": "climax",
  "bars": [41, 64],
  "tracks": [
    "drums",
    "bass",
    "lead",
    "guitar",
    "pad"
  ]
}
```

---

# 16. Motif Tracking

A motif should be a semantic ComposerBench object.

Example:

```json
{
  "id": "motif:main-theme",
  "source": {
    "track": "track:piano",
    "bars": [3, 4]
  }
}
```

Track appearances.

Example:

```text
main-theme
├── intro-original
├── verse-transposed
├── escalation-fragment
└── climax-octave-doubled
```

Support transformations including:

```text
transpose
invert
augment
diminish
fragment
sequence
reharmonize
change register
change instrumentation
rhythmic variation
ornament
simplify
```

Prefer using MAGDA-native editing operations where possible.

---

# 17. Musical Relationship Graph

Eventually maintain relationships such as:

```text
motif:main-theme
       │
       ├─ transformed_into → climax-theme
       │
       └─ harmonized_by → chorus-harmony

chorus-harmony
       │
       └─ supports → bass-pattern-2
```

This enables targeted regeneration without losing important relationships.

---

# 18. Agent Inspection

An agent should never receive the complete DAW state unless it specifically requests it.

Provide hierarchical inspection.

Example:

```bash
composerbench inspect
```

returns:

```text
Title: Untitled
Tempo: 78 BPM
Length: 64 bars

Sections:
intro         1–8
development   9–24
escalation   25–40
climax       41–56
outro        57–64

Tracks:
piano
pad
bass
drums
lead
texture
```

Then:

```bash
composerbench inspect section climax
```

Then:

```bash
composerbench inspect track bass
```

Then:

```bash
composerbench inspect notes \
  --track bass \
  --bars 41:48
```

Progressively disclose detail.

Optimize for LLM context efficiency.

---

# 19. Query Interface

Create structured queries.

Examples:

```text
tracks
clips in section climax
notes on track bass in bars 20-24
devices on track pad
parameters on device pad-synth
automation on parameter cutoff
```

Return JSON when requested.

```bash
composerbench inspect track bass --json
```

---

# 20. Agent-Facing CLI

Create:

```text
composerbench
```

or a short alias such as:

```text
mbench
```

Commands may include:

```bash
composerbench status

composerbench inspect

composerbench inspect track bass

composerbench inspect section chorus

composerbench inspect device synth

composerbench inspect plugins

composerbench plan show

composerbench plan update

composerbench intent show

composerbench render

composerbench render --section chorus

composerbench analyze

composerbench analyze --section climax

composerbench compare revision-a revision-b

composerbench snapshot before_chorus_edit

composerbench restore before_chorus_edit

composerbench history
```

The CLI is a thin agent interface, not a second DAW.

---

# 21. Machine Output

Every important command should support:

```text
--json
```

Avoid parsing pretty console output.

Errors must also be structured.

Example:

```json
{
  "error": "PLUGIN_NOT_FOUND",
  "requested": "ExampleSynth",
  "available_matches": [
    "ExampleSynth 2"
  ]
}
```

---

# 22. MCP Interface

Expose ComposerBench functionality through MCP.

The MCP server should generally expose **ComposerBench semantic operations**, not every low-level MAGDA internal function individually.

Possible tools:

```text
music_project_inspect
music_section_inspect
music_track_inspect
music_device_inspect

music_notes_add
music_notes_transform
music_notes_delete

music_track_create
music_clip_create

music_plugin_search
music_plugin_load
music_plugin_inspect
music_plugin_set_parameter
music_plugin_automate

music_render
music_analyze
music_compare

music_plan_read
music_plan_update

music_intent_read
music_intent_update

music_snapshot_create
music_snapshot_restore

music_evaluate
```

Keep schemas small and strongly typed.

---

# 23. Preserve MAGDA's DSL

Do not replace MAGDA's native DSL merely because ComposerBench exists.

Use it when appropriate.

ComposerBench may translate semantic actions into DSL operations.

Conceptually:

```text
ComposerBench intent:

"transpose bass one octave down during the climax"

        ↓

structured ComposerBench action

        ↓

MAGDA DSL operation

        ↓

MAGDA project state
```

Treat the DSL as an execution language where it is reliable.

---

# 24. Separate Reasoning from Execution

Never let free-form LLM prose directly mutate DAW state.

Use:

```text
LLM reasoning
     ↓
structured action
     ↓
schema validation
     ↓
ComposerBench adapter
     ↓
MAGDA
```

Not:

```text
LLM prose
     ↓
arbitrary execution
```

---

# 25. Transactions

Agentic editing requires safe experimentation.

Where possible:

```text
snapshot
   ↓
perform multiple changes
   ↓
validate
   ↓
render
   ↓
evaluate
   ↓
keep OR restore
```

If MAGDA exposes transactions, use them.

Otherwise implement ComposerBench-level snapshot/recovery semantics.

---

# 26. Semantic Diff

One of the highest-priority features is **semantic musical diffing**.

Instead of:

```text
project changed
```

tell the agent:

```text
Revision B vs Revision A

CLIMAX

Drums:
  note density +23%
  kick pattern unchanged
  hi-hat subdivision changed 1/8 → 1/16

Bass:
  median pitch -12 semitones
  rhythmic density +8%

Lead:
  12 new notes
  pitch range extended C5 → G6

Pad:
  filter cutoff rose progressively through section

Mix:
  integrated loudness +2.1 LU
```

This gives agents causal understanding.

---

# 27. Render Loop

Rendering should become a first-class agent operation.

Support as many as MAGDA permits:

```text
whole song
section
time range
track
stem
selection
```

If only full renders are initially possible, do not build a new renderer immediately.

First use MAGDA's renderer and optimize later.

Store render metadata:

```json
{
  "revision": 27,
  "range": "climax",
  "start_bar": 41,
  "end_bar": 56,
  "state_hash": "...",
  "file": "..."
}
```

---

# 28. Never Re-Render Unchanged Music Without Need

Compute hashes of relevant project state.

Cache:

- section renders,
- stems,
- analysis,
- comparisons.

If bars 1–32 are unchanged, editing bar 48 should not force unnecessary evaluation of the entire composition.

---

# 29. Symbolic Analysis

Analyze MIDI/project information directly.

Useful metrics:

```text
note density
onset density
pitch range
median register
velocity
polyphony
rhythmic entropy
syncopation
pitch-class distribution
harmonic rhythm
chord changes
repetition
motif recurrence
instrument activity
silence
section contrast
```

These metrics do not need to be perfect musicology.

They need to give agents useful feedback.

---

# 30. Audio Analysis

Analyze rendered audio.

Potential metrics:

```text
LUFS
true peak
RMS
crest factor
dynamic range
spectral centroid
spectral bandwidth
low-frequency energy
high-frequency energy
onset density
stereo width
correlation
frequency masking
section similarity
```

Use MAGDA's built-in analysis where it already exists.

Do not duplicate analysis unnecessarily.

ComposerBench should aggregate and reason over measurements.

---

# 31. Energy Model

Build an approximate section-level `energy` score.

Potential inputs:

```text
note density
velocity
loudness
instrument count
register
drum activity
spectral brightness
distortion
rhythmic subdivision
```

Example:

```text
intro          0.14
verse          0.29
development    0.41
escalation     0.67
climax         0.94
outro          0.18
```

Then constraints can express:

```text
energy(climax) > energy(all_previous_sections)
```

---

# 32. Tension Model

Build a separate heuristic for tension.

Possible inputs:

```text
dissonance
chromaticism
harmonic instability
register extremes
repetition
rhythmic instability
automation
spectral density
dynamic growth
```

Do not conflate loudness with tension.

---

# 33. Constraint System

Allow declarative constraints.

Example:

```yaml
constraints:

  - type: recurring_motif
    motif: main-theme
    minimum_occurrences: 3

  - type: maximum_peak
    value_dbfs: -0.5

  - type: energy_relation
    lhs: climax
    relation: greater_than
    rhs: development

  - type: track_present
    track: drums
    section: climax

  - type: vocals
    allowed: false
```

Validation should return:

```text
PASS motif appears 4 times
PASS climax energy > development
FAIL true peak exceeds target
PASS no vocal tracks detected
```

---

# 34. Plugin Architecture

MAGDA is the plugin host.

ComposerBench is the plugin **reasoning layer**.

Do not write a VST/AU host.

ComposerBench should be able to:

```text
discover plugins
search plugins
load plugins
inspect plugin parameters
set parameters
automate parameters
save semantic knowledge about parameters
```

through MAGDA.

---

# 35. Plugin Discovery

Build an agent-readable catalog.

Example:

```json
{
  "name": "Some Synth",
  "type": "instrument",
  "format": "VST3",
  "manufacturer": "...",
  "parameters": 213
}
```

Allow queries like:

```text
find synths
find reverbs
find compressors
find distortion effects
```

Use deterministic metadata first.

AI classification can augment it later.

---

# 36. Plugin Parameter Introspection

Expose plugin parameters without requiring the plugin GUI.

Example:

```json
{
  "parameter": "Filter Cutoff",
  "normalized_value": 0.42,
  "display_value": "2.4 kHz",
  "automatable": true
}
```

The agent should be able to manipulate plugin state programmatically.

---

# 37. Semantic Plugin Profiles

Raw parameter names are insufficient for high-level musical reasoning.

Create optional semantic profiles.

Example:

```yaml
plugin: example-synth

semantics:

  brightness:
    primary:
      - filter_cutoff

  attack_sharpness:
    primary:
      - amp_attack

  width:
    primary:
      - unison_width

  instability:
    contributors:
      - oscillator_detune
      - lfo_depth
      - filter_mod
```

Then semantic commands become possible:

```text
make pad brighter
make synth wider
soften attack
increase instability
```

---

# 38. Semantic Operations Must Be Explainable

If the agent asks:

```text
make climax brighter
```

ComposerBench might execute:

```text
pad filter cutoff +18%
lead octave layer enabled
high shelf +1.5 dB
```

Return those actual changes.

Never hide meaningful causal changes behind an opaque semantic function.

---

# 39. Plugin Presets

Agents should be able to:

```text
list presets
search presets
load presets
compare presets
```

if MAGDA/plugin APIs permit it.

Do not assume preset formats are uniform across plugins.

---

# 40. Built-In Device Fallback

All benchmark/demo projects should preferably be able to function without proprietary plugins.

Use MAGDA's built-in devices when possible.

Optional third-party plugins can improve production quality but should not be required for basic ComposerBench tests.

---

# 41. Composition Agent

Create a high-level Composer.

Responsibilities:

```text
interpret artistic intent
build arrangement
establish motifs
develop harmony
write melody
write bass
create rhythmic material
choose instrumentation
manage long-term coherence
```

The Composer should not concern itself with API mechanics.

It asks the ComposerBench tool layer to perform actions.

---

# 42. Arranger Agent

Optional specialist.

Responsibilities:

```text
section lengths
instrument entries/exits
layering
transitions
density
macro dynamic arc
contrast
climax construction
```

---

# 43. Sound Designer Agent

Optional specialist.

Responsibilities:

```text
instrument selection
synth parameters
presets
modulation
effects
texture generation
timbral development
```

---

# 44. Mixing Agent

Leverage existing MAGDA analysis where possible.

Responsibilities:

```text
gain balance
frequency masking
stereo width
dynamics
headroom
effects balance
master levels
```

Do not allow it to rewrite composition unless explicitly authorized.

---

# 45. Critic Agent

The critic should not directly edit.

It returns observations.

Example:

```json
{
  "strengths": [
    "main motif remains recognizable",
    "climax is substantially denser"
  ],

  "problems": [
    {
      "severity": 0.82,
      "section": "development",
      "issue": "energy remains static for 12 bars"
    }
  ]
}
```

---

# 46. Director Agent

The Director decides:

```text
continue
accept revision
revert revision
change plan
finish
```

It should evaluate work relative to the original artistic intent rather than maximizing metrics blindly.

---

# 47. Single-Agent First

Do not start with five agents merely because multi-agent architecture sounds impressive.

First implement:

```text
one capable agent
+
good tools
+
good state
+
good feedback
```

Only split roles when measurable benefits appear.

---

# 48. Model-Agnostic Runtime

ComposerBench must not require one AI provider.

Create a model abstraction.

Possible backends:

```text
OpenAI-compatible APIs
OpenRouter
Anthropic
Google
local llama.cpp
local OpenAI-compatible servers
other future models
```

Keep models replaceable.

---

# 49. Model Routing

Different tasks may deserve different models.

Example:

```text
simple deterministic command
        ↓
small/local model

harmonic suggestion
        ↓
music-specialized model

large arrangement decision
        ↓
strong reasoning model

audio critique
        ↓
audio-capable model
```

Do not require routing in v0.

Design so it can be added.

---

# 50. Audio-Capable Models

Eventually allow a multimodal model to receive rendered audio.

Conceptually:

```python
evaluation = critic.listen(
    audio="climax.wav",
    intent=intent,
    previous="climax_previous.wav",
)
```

Possible output:

```text
The new version has a much stronger arrival,
but the snare becomes fatiguing and the lead
loses melodic clarity under the distorted pad.
```

Combine learned critique with deterministic measurements.

---

# 51. Deterministic Metrics vs Artistic Judgment

Keep these distinct.

## Deterministic

```text
tempo
note count
peak level
pitch range
density
plugin values
loudness
section duration
```

## Heuristic

```text
energy
tension
motif similarity
contrast
```

## Subjective

```text
beautiful
boring
melancholic
overwhelming
catchy
emotionally effective
```

Never present subjective judgments as objective measurements.

---

# 52. Revision Loop

Implement the central agent loop.

Pseudo-code:

```python
while not finished:

    intent = read_intent()
    plan = read_plan()
    state = inspect_project()

    critique = evaluate(
        intent=intent,
        plan=plan,
        state=state,
        latest_render=latest_render,
    )

    change = agent.propose_change(
        critique=critique,
        state=state,
    )

    snapshot = create_snapshot()

    execute(change)

    validation = validate()

    if not validation.ok:
        restore(snapshot)
        continue

    preview = render(change.affected_range)

    analysis = analyze(preview)

    comparison = compare(
        before=snapshot,
        after=current_state,
    )

    decision = judge(
        intent,
        critique,
        analysis,
        comparison,
    )

    if decision == "reject":
        restore(snapshot)

    elif decision == "accept":
        record_revision()

    elif decision == "revise_plan":
        update_plan()

    if completion_criteria_met():
        finished = True
```

---

# 53. Evaluation Before Keeping Changes

An autonomous agent should not blindly accumulate edits.

For meaningful edits:

```text
BEFORE
snapshot

CHANGE
modify project

AFTER
render

COMPARE
measure + critique

DECIDE
keep / revert
```

This is analogous to running tests after modifying software.

---

# 54. A/B Comparisons

Support direct comparisons.

Example:

```bash
composerbench compare \
    snapshots/pre-chorus \
    snapshots/post-chorus \
    --section chorus
```

Return:

```text
Symbolic:
density +21%
median register +7 semitones

Audio:
LUFS +1.8
spectral centroid +14%

Critic:
B has clearer forward motion
but less space around the melody
```

---

# 55. Experiment Branches

Eventually support multiple candidate revisions.

Example:

```text
chorus-a
chorus-b
chorus-c
```

Agent can render all three and select one.

Do not mutate the main project three times irreversibly.

---

# 56. Memory

Agent memory should contain useful musical knowledge about the project, not endless conversational transcripts.

Examples:

```text
main motif must remain recognizable

the user preferred revision 14's drums

pad should stay out of first four bars

bass becomes muddy below current octave

plugin preset X worked well for climax
```

Use structured memory.

---

# 57. Working Memory vs Persistent Memory

Separate:

## Working

Current problem.

```text
Fix the transition into the chorus.
```

## Project

Persistent song facts.

```text
The chorus starts bar 33.
```

## Learned

Reusable production knowledge.

```text
This synth's cutoff parameter maps well to perceived brightness.
```

Do not mix these indiscriminately.

---

# 58. History

Record agent operations.

Example:

```text
revision 28

goal:
increase climax impact

changes:
- added octave lead
- increased drum density
- automated pad cutoff

result:
accepted

reason:
energy +0.13
critic preferred version B
```

This is much more useful than a raw chat log.

---

# 59. Trace Every Autonomous Run

Store:

```text
model
model configuration
prompt
project revision
tool calls
tool responses
reasoning summary
render files
metrics
critic output
decision
token usage if available
latency if available
```

This enables research and debugging.

---

# 60. Benchmark Mode

ComposerBench should eventually benchmark music agents.

Tasks should test specific capabilities.

Example:

> Create sixteen bars in A minor with four tracks.

Objective validators can check:

```text
16 bars
A minor
>=4 tracks
```

---

# 61. Editing Benchmark

Provide existing music:

> Increase rhythmic activity in bars 9–16 without changing melody pitches.

Validate:

```text
melody pitch sequence unchanged
onset density increased
bars outside range unchanged
```

This evaluates precision.

---

# 62. Motif Benchmark

Task:

> Introduce a four-note motif in the intro and transform it during the climax.

Measure:

```text
motif exists
motif recurrence exists
second instance differs
recognizable similarity remains
```

---

# 63. Arrangement Benchmark

Task:

> Produce four sections with monotonically increasing energy.

Validate:

```text
E1 < E2 < E3 < E4
```

---

# 64. Production Benchmark

Task:

> Repair the mix so no track masks the lead excessively while preserving arrangement and MIDI.

Validate that:

```text
MIDI unchanged
track structure unchanged
mix measurements improved
```

---

# 65. Long-Horizon Benchmark

Task:

> Produce a coherent 3-minute composition from scratch.

Measure:

```text
constraint satisfaction
tool-call efficiency
edit precision
structural coherence
motif recurrence
human preference
completion rate
```

---

# 66. Human Evaluation

Support blind A/B listening.

Collect dimensions such as:

```text
melody
structure
sound design
mix
emotional impact
coherence
originality
overall preference
```

Never train benchmark scoring solely around easily gamed numerical metrics.

---

# 67. External Generative Audio

Do not make text-to-music generation the core architecture.

However, future tools may provide:

```text
sample generation
texture generation
stem generation
vocals
sound effects
audio continuation
```

Treat resulting files as assets placed into MAGDA.

Example:

```text
generate 12 seconds of distant metallic ambience
       ↓
asset.wav
       ↓
place on texture track
       ↓
process through MAGDA effects
```

---

# 68. AI-Generated DSP

MAGDA may expose programmable DSP mechanisms.

ComposerBench can eventually allow a Sound Designer agent to create effects or devices programmatically.

This should remain sandboxed and explicitly tested.

Do not allow arbitrary untrusted generated native code to become the normal production path.

---

# 69. Security

Treat model output as untrusted.

Never translate free-form model text into arbitrary shell execution.

Validate:

```text
paths
plugin identifiers
track identifiers
parameter ranges
clip boundaries
file operations
DSL operations
remote requests
```

Restrict access to the ComposerBench project and approved asset/plugin locations.

---

# 70. No Pixel Automation Principle

Do not use screenshot-driven plugin manipulation unless no programmatic alternative exists.

Preferred:

```text
plugin parameter API
```

Avoid:

```text
find knob visually
move mouse to knob
drag 38 pixels
```

GUI automation is brittle and destroys agent reproducibility.

---

# 71. GUI Is for Humans

ComposerBench does not need its own major GUI initially.

MAGDA already provides the human visualization.

ComposerBench should prioritize:

```text
API
CLI
MCP
agent loop
analysis
```

A dashboard can come later.

---

# 72. Real-Time Human Collaboration

Eventually support interactions such as:

```text
Human:
Make the drums less busy.

Agent:
adjusts drums

Human listens.

Human:
Keep that, but restore the old kick pattern.

Agent:
performs targeted restoration
```

This requires semantic history.

---

# 73. User Preferences

Eventually allow ComposerBench to learn project-independent preferences when explicitly desired.

Examples:

```text
prefers long developments
prefers sparse intros
likes dramatic climaxes
dislikes excessive cymbals
```

Keep preference learning separate from one project's intent.

---

# 74. Music Theory Utilities

Only implement theory functionality missing from MAGDA and useful to agent reasoning.

Potential helpers:

```text
scale membership
chord detection
chord tones
functional harmony
voice-leading distance
interval analysis
motif similarity
rhythmic similarity
cadence detection
```

Do not create a gigantic theoretical framework before agents actually need it.

---

# 75. Semantic Transformations

ComposerBench may provide high-level transformations:

```text
increase_energy
decrease_energy
increase_tension
release_tension
make_sparser
make_denser
make_brighter
make_darker
make_more_syncopated
simplify
build_to_climax
create_transition
vary_motif
```

But each semantic transformation must ultimately decompose into concrete MAGDA operations.

---

# 76. Example Semantic Transformation

Input:

```text
increase energy of climax by ~20%
```

Possible planning result:

```json
{
  "changes": [
    {
      "type": "velocity_scale",
      "track": "drums",
      "amount": 1.08
    },
    {
      "type": "increase_note_density",
      "track": "hihat",
      "amount": 0.2
    },
    {
      "type": "octave_double",
      "track": "lead"
    },
    {
      "type": "automation",
      "device": "pad",
      "parameter": "filter_cutoff",
      "direction": "increase"
    }
  ]
}
```

Then execute through MAGDA.

---

# 77. Explain Changes

After execution:

```text
Energy adjustment applied:

Drums
+ mean velocity 7%
+ hi-hat onset density 21%

Lead
+ octave doubling bars 45–56

Pad
+ cutoff automation from 31% → 68%

Measured section energy
0.71 → 0.83
```

Agents should understand what semantic operations actually did.

---

# 78. Preserve Constraints During Transformations

A semantic change may include invariants.

Example:

```yaml
operation:
  increase_energy

target:
  section: chorus

preserve:
  - melody_pitches
  - section_length
  - chord_progression
```

Validate invariants after modification.

---

# 79. Partial Regeneration

Support:

```text
rewrite drums only
rewrite bars 17–20 only
reharmonize while preserving melody
change melody rhythm while preserving pitch order
change sound design without changing MIDI
change mix without changing composition
```

This precision is essential.

---

# 80. Definition of "Listen"

An agent "listening" may initially consist of:

```text
symbolic analysis
+
audio measurements
+
optional multimodal audio model
```

Do not pretend deterministic metrics alone equal human listening.

As audio-capable models improve, integrate them cleanly.

---

# 81. Completion Detection

Agents should not endlessly polish.

Possible completion criteria:

```text
all hard constraints pass

no high-severity critic issues

artistic intent judged sufficiently satisfied

no candidate change improves score meaningfully

human requested stop

iteration budget reached
```

---

# 82. Resource Budgets

Autonomous sessions need limits.

Configurable budgets:

```text
maximum tool calls
maximum model tokens
maximum renders
maximum wall-clock effort
maximum candidate branches
maximum expensive audio evaluations
```

Prefer cheap symbolic checks before expensive full renders.

---

# 83. Agent Context Optimization

Never dump:

```text
100,000 MIDI events
2,000 plugin parameters
complete project serialization
```

into an LLM by default.

Summarize first.

Example plugin response:

```text
Serum

Relevant exposed semantic controls:
brightness
attack
decay
width
movement
distortion

Use `inspect_device --all-parameters`
for complete parameter list.
```

---

# 84. Failure Recovery

Agent-facing errors must be actionable.

Bad:

```text
Error -4
```

Good:

```json
{
  "error": "PARAMETER_OUT_OF_RANGE",
  "device": "pad-synth",
  "parameter": "cutoff",
  "received": 1.4,
  "allowed": [0.0, 1.0]
}
```

---

# 85. Connection Recovery

MAGDA may close, crash, restart, or reload a project.

ComposerBench must detect:

```text
connection lost
wrong project loaded
project changed externally
stale entity IDs
render failed
plugin unavailable
```

Recover gracefully.

Do not blindly continue mutating stale state.

---

# 86. Project State Hashing

At minimum record a revision identifier or hash.

Before executing a planned action:

```text
expected revision: 44
current revision: 47
```

If mismatched, re-inspect.

This prevents an agent from editing based on stale state.

---

# 87. Human Edits

Humans may modify the project directly in MAGDA while ComposerBench is running.

Detect state changes.

ComposerBench should treat MAGDA's current state as authoritative.

Refresh semantic indexes as necessary.

---

# 88. Testing Strategy

Create automated tests for:

```text
MAGDA connection
adapter queries
track creation
MIDI edits
device inspection
parameter writes
automation
rendering
snapshot restoration
semantic diff
state hashing
constraint validation
analysis
MCP schemas
CLI JSON output
failure recovery
```

Use real MAGDA integration tests where feasible.

---

# 89. Fake Adapter

Build:

```python
FakeDawAdapter
```

for fast unit tests.

It should emulate enough state for ComposerBench logic.

This keeps agent/planning/evaluation tests from needing the full DAW every time.

---

# 90. Integration Test Projects

Maintain small deterministic projects.

Examples:

```text
empty_project
four_bar_piano
basic_drums
synth_with_automation
two_section_song
plugin_parameter_test
```

---

# 91. Golden Tests

Where sensible, test:

```text
known MIDI state
known semantic analysis
known diff
known constraints
known project inspection output
```

Avoid comparing raw rendered WAV bytes unless deterministic rendering guarantees make that meaningful.

---

# 92. First Vertical Slice

Do not implement everything above.

The first vertical slice should prove:

```text
external AI agent
      ↓
ComposerBench
      ↓
MAGDA
      ↓
actual musical edit
      ↓
actual audio render
      ↓
analysis
      ↓
second edit
```

This is the milestone that matters.

---

# 93. Milestone 0 — MAGDA Audit

Tasks:

- build current MAGDA,
- run tests,
- inspect architecture,
- inspect DSL,
- inspect agent system,
- inspect remote interfaces,
- inspect plugin control,
- inspect rendering,
- document capability map.

Deliverable:

```text
MAGDA_CAPABILITY_MAP.md
```

Do not begin reinventing missing features during the audit.

---

# 94. Milestone 1 — External Control

Establish structured external communication.

Required minimum:

```text
get project status
list tracks
create track
create MIDI clip
add MIDI notes
change track volume
save project
```

Use existing MAGDA interfaces if available.

If missing, build the narrowest clean bridge.

---

# 95. Milestone 2 — ComposerBench Python Client

Create:

```python
client = ComposerBench.connect()

project = await client.project()

tracks = await client.tracks()

await client.create_track("piano")

await client.add_notes(
    track="piano",
    notes=[...],
)
```

Prove end-to-end control.

---

# 96. Milestone 3 — MCP

Expose core operations.

Connect a general-purpose coding agent.

Have it perform:

> Create an eight-bar melody.

No bespoke music model required yet.

---

# 97. Milestone 4 — Plugin Control

Demonstrate:

```text
discover instrument
load instrument
inspect parameters
change parameter
render audio
```

Use a MAGDA built-in instrument first.

Then test at least one external plugin if available.

---

# 98. Milestone 5 — Autonomous Render Loop

Implement:

```text
snapshot
edit
render
analyze
compare
keep/revert
```

Have the agent autonomously improve one four- or eight-bar phrase.

---

# 99. Milestone 6 — Musical Project State

Add:

```text
intent
sections
motifs
plan
constraints
```

Have the agent produce a multi-section piece.

---

# 100. Milestone 7 — Audio Critique

Add deterministic audio analysis.

Then optionally add an audio-capable model.

Compare whether audio feedback improves revisions.

---

# 101. Milestone 8 — Plugin Semantics

Map raw plugin parameters into musical concepts.

Demonstrate:

> Make the pad brighter during the chorus.

without hard-coding that exact plugin command into the agent prompt.

---

# 102. Milestone 9 — Full Autonomous Demo

Create a 30–90 second original track.

Minimum:

```text
4 sections
5+ tracks
drums
bass
harmony
melody
1 recurring motif
1 transformed motif
automation
real synthesis
effects
rendered audio
```

The agent must perform at least several genuine revision cycles.

---

# 103. Demo Trace

Record:

```text
initial intent

plan

initial composition

render 1

critique 1

revision

render 2

comparison

revision

render 3

final decision
```

A single one-shot generation does not count.

---

# 104. Benchmark the Agent Against Itself

For the demo, save:

```text
first render
final render
```

Perform blind comparison later.

The purpose of ComposerBench is not merely generation.

The purpose is **improvement through iteration**.

---

# 105. Example User Experience

Eventually this should work:

```text
User:

Make me a six-minute instrumental piece.

It should begin almost empty, like something barely alive.

Introduce a tiny melody near the beginning.

Keep bringing that melody back, but transform it each time.

The middle should gradually become unstable.

The final version of the melody should feel enormous.

No vocals.
```

The ComposerBench agent should then autonomously:

```text
inspect available instruments

choose tempo/key

create a section plan

create tracks

design motif

write opening

render opening

listen

revise

write development

transform motif

add drums

render transition

fix transition

design climax

automate synths/effects

render climax

detect masking

adjust mix

render entire song

evaluate artistic trajectory

revise weak sections

finalize
```

That is the target behavior.

---

# 106. What ComposerBench Is NOT

ComposerBench is not:

```text
a ChatGPT wrapper around a DAW

a MIDI-file generator

a text-to-music endpoint

a piano-roll replacement

a collection of hard-coded genre templates

a GUI macro recorder

a plugin-host implementation

a one-shot music generator
```

It is an **agentic musical workspace**.

---

# 107. Research Questions

Design the architecture so ComposerBench can eventually answer:

```text
Can models improve their own compositions through repeated listening?

How many revision cycles produce meaningful improvements?

What musical abstractions help agents most?

Does explicit motif memory improve long-term coherence?

Do semantic plugin controls improve sound design?

Does audio feedback improve composition compared with symbolic feedback alone?

Which models are best at composition versus production versus critique?

Do specialized agents outperform one strong generalist?

How much state should an agent inspect at once?

Can an agent learn the semantics of previously unseen plugins?

Can agents preserve artistic intent over hundreds of operations?
```

---

# 108. Main Engineering Metric

When considering a feature, ask:

> Does this increase an AI agent's ability to understand, manipulate, hear, evaluate, or recover the musical project?

Especially prioritize:

```text
observability
control
reversibility
precision
feedback
semantic abstraction
context efficiency
determinism
```

---

# 109. Avoid Premature Complexity

Do NOT begin with:

```text
multi-agent orchestration
vector databases
custom neural music models
custom DAW rendering
complex GUIs
distributed workers
fine-tuning
reinforcement learning
plugin pixel control
```

First get one strong external agent to produce and iteratively improve real music inside MAGDA.

---

# 110. Dogfood Constantly

As soon as the agent can operate MAGDA, give it real tasks.

Examples:

```text
create four bars of piano

add bass

create drums

make drums sparser

rewrite only bar 4

transpose melody without changing rhythm

make chorus louder without changing MIDI

make synth brighter

add reverb

build an eight-bar transition

reuse the opening motif in the ending
```

Observe every failure.

When an agent repeatedly struggles, ask whether it needs:

```text
better tool
better schema
better query
better abstraction
better error
better context
better analysis
```

Improve the harness rather than merely adding more prompt instructions.

---

# 111. Upstream Contribution Strategy

If MAGDA is missing generally useful functionality such as:

```text
structured project querying
stable entity IDs
remote rendering
plugin parameter enumeration
transactional edits
remote API calls
```

implement it in an upstream-friendly manner where appropriate.

Keep ComposerBench-specific artistic reasoning outside MAGDA.

A useful boundary is:

```text
MAGDA:
"What exists and how can it be manipulated?"

ComposerBench:
"What should we do musically, and was it better?"
```

---

# 112. Definition of Success

ComposerBench succeeds when an external model can treat a real DAW project as a manipulable environment.

The model should be able to:

- inspect it,
- understand it,
- change it,
- hear the result,
- reason about the result,
- undo mistakes,
- compare alternatives,
- maintain long-term artistic goals,
- and continue autonomously.

The final conceptual loop is:

```text
        ┌─────────────┐
        │   INTENT    │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │    PLAN     │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │   INSPECT   │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │    EDIT     │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │   RENDER    │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │   LISTEN    │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │  CRITIQUE   │
        └──────┬──────┘
               ↓
        ┌─────────────┐
        │   COMPARE   │
        └──────┬──────┘
               ↓
          KEEP / REVERT
               │
               └──────────────→ repeat
```

---

# 113. Begin Now

Do not merely summarize this prompt.

Do not respond with a hypothetical architecture and stop.

Begin performing the work.

Your first actions should be:

1. Inspect the current workspace.
2. Locate any existing ComposerBench work.
3. Locate or clone the current official MAGDA repository if appropriate.
4. Read MAGDA's current documentation.
5. Search the actual source for DSL, agents, remote APIs, MCP, WebSocket, rendering, devices, plugins, project state, and tests.
6. Build a current capability map.
7. Identify the **smallest path from an external process to a real musical edit inside MAGDA**.
8. Implement that path.
9. Test it.
10. Continue until an external agent can cause MAGDA to produce an audible musical result.

Do not redesign MAGDA before understanding it.

Do not duplicate functionality merely because implementing a new version seems easier.

Do not stop after generating boilerplate.

Do not call mocked tests sufficient proof of DAW integration.

The first real victory is:

> **An external AI agent sends structured actions through ComposerBench, MAGDA performs those actions on a real project, and MAGDA produces audible music.**

The second victory is:

> **The agent renders that music, evaluates it, makes another targeted change, determines whether the change helped, and keeps or reverts it.**

Once those two loops work, we have the foundation.

Everything else builds on them.

## Governing Principle

When uncertain, choose the design that maximizes:

> **agent control + musical semantics + observability + reversibility + real audio feedback + compatibility with upstream MAGDA.**

Do not build another DAW.

**Build the thing that can actually use one.**
