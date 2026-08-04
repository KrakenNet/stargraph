# RL toolkit (`stargraph.rl`)

Reinforcement-learning support under the `rl` extra: gymnasium environments and
ONNX policies as graph nodes, an SB3 PPO trainer tool, a **policy admission
gauntlet** (the evaluation harness), and a planner extension point.

```bash
pip install 'stargraph[rl]'   # gymnasium, stable-baselines3, torch, numpy, scipy, onnxruntime
```

`stargraph.rl` imports cleanly *without* the extra (the same lazy-import seam
as `stargraph.ml.export`); only constructing a gym/SB3 surface raises
`RLNodeError` with the install hint. The gauntlet library and the reference
eval graph run on the base install.

The gauntlet is a port of an upstream governed-RL pipeline — the machinery
behind a real admission refusal on the ESA Kelvins collision-avoidance
dataset. The split/PBO/metrics/admission arithmetic is intentionally identical
to the upstream implementation; any adaptation is documented per module
docstring.

## Nodes

### `GymEnvNode` — a registered env as a node

```yaml
nodes:
  - id: env
    kind: "stargraph.rl.envs:GymEnvNode"   # via Python Graph API; see below for CLI note
```

Environments are addressed by **gymnasium registry id** — graphs name an env,
never a class (the same indirection the model registry gives models):

- a plain id resolves through the gymnasium registry (`CartPole-v1`, or
  anything a distribution registered via `gymnasium.register`);
- `"my_pkg.envs:MyEnv-v0"` imports `my_pkg.envs` first (its import side effect
  registers the env), then resolves `MyEnv-v0` — how a package ships a custom
  env without touching Stargraph.

Per `execute`: the first call (and every call after a terminal step) **resets**
the episode; otherwise the env **steps** on `state.<action_field>`. It writes
`observation` / `reward` / `terminated` / `truncated` (field names
configurable), with numpy values converted to plain Python so state stays
JSON-serializable. Construction is eager: a missing extra or unknown id fails
at definition time.

Note: `module:Class` node kinds are constructed zero-arg by the CLI, so
configured nodes like `GymEnvNode(env_id=...)` are used from the Python
`Graph` API or from a zero-arg subclass; the reference eval graph avoids the
issue by passing everything through state.

### `PolicyNode` — exported SB3 policy inference

A thin specialization of [`MLNode`](nodes/ml.md) over the ONNX graphs
published by `stargraph.ml.export.export_sb3_policy`: reads
`state.<observation_field>`, runs the actor deterministically, writes the full
SB3 triple — `action`, `value`, `log_prob` — to state. Session handling
(shared cache, eager warm-up, content-hash gate) is inherited from MLNode
unchanged.

### Trainer tool — `rl:train_ppo`

A `@tool` (entry point `rl_train_ppo`, capability `tools:rl_train_ppo`)
wrapping stable-baselines3 PPO on a registered env id. Writes
`<out_dir>/ppo_policy.zip` + `policy_meta.json`. Hyperparameter defaults
mirror the upstream trainer (`n_steps=512`, `batch_size=128`). `side_effects=write`,
so replays stub it — a replayed run never silently retrains. Serve the result
through the governed path: export to ONNX, run with `PolicyNode`, and gate
with the gauntlet before anything downstream consumes it.

## The admission gauntlet (`stargraph.rl.gauntlet`)

Library surface (math identical to the upstream implementation):

| Piece | What it does |
|---|---|
| `three_way` / `materialize` / `Split` | Deterministic 3-way split, by event AND by time: train < admission < deploy on the timeline; partitions pinned by sha256 manifests. |
| `rollout` / `aggregate` / `evaluate` | One episode per event through a pluggable `EnvFactory`; folds terminal infos into admission metrics (risk rate, total Δv, maneuver/false-maneuver rates). |
| `pareto_beats` | Candidate must be no worse on risk AND fuel, strictly better on one — ties lose. |
| `cscv_pbo` | CSCV probability of backtest overfitting (López de Prado 2015) over a config family's per-event utility streams; fails closed to 1.0. |
| `gate` | The admission verdict: toolchain split (a candidate trained on the gate's own backend is refused unheard), Pareto vs the ops baseline, family PBO ≤ 0.5. |

Postures the port keeps from upstream: **default = refusal**, evaluation failures
**fail closed** (a refusal verdict, never a silent pass), misconfiguration
**fails loud** (`RLNodeError`), and the **generator is never the sole
evaluator** — the gate re-derives its own split and binds the candidate's
recorded train-split sha to it, never to trainer-authored files.

### The reference eval graph

`stargraph.rl.gauntlet.eval_graph_path()` returns a runnable IR graph:
wall → train → gate → shield **stations**, every transition decided by a
Fathom rule pack over mirrored `*_verdict` facts (rules-not-edges — same shape
as `examples/pipeline.yaml`). Refusal is a first-class terminal at every hop.

All pluggable pieces arrive as dotted references in `EvalState`
(`"pkg.mod:attr"`, or a bare `"pkg.mod"` for module-shaped backends):

```bash
stargraph run <eval-graph.yaml> \
  --inputs dataset_path=events.parquet --inputs model_dir=models/candidate \
  --inputs events_loader=my_pkg.data:load_events \
  --inputs expert_cfg_loader=my_pkg.config:load_cfg \
  --inputs env_factory=my_pkg.envs:EventEnv \
  --inputs gate_backend=my_pkg.backends.independent \
  --inputs candidate_loader=my_pkg.policy:Policy.load \
  --inputs baseline_factory=my_pkg.policy:Baseline
```

Stations:

- **`WallStation`** — structural admission of the dataset (refuse > repair);
- **`TrainStation`** — `cached` when `policy_meta.json` exists; else runs the
  dotted `trainer` on ONLY the re-derived train partition;
- **`GateStation`** — the upstream admission run as a node: re-derive
  the split, bind the candidate's train-split sha, gate Pareto + PBO;
- **`ShieldStation`** — wraps an upstream-style deterministic rule evaluator
  (`factory(cfg, backend) -> evaluator.evaluate(rec, ctx)`); it only ever
  emits a verdict, it commands nothing.

The self-contained unit suite (`tests/unit/rl/test_gauntlet_math.py`) pins the
ported split/PBO/metrics/admission arithmetic.

## Planners (`stargraph.planners` entry-point group)

The planning extension point: an entry point names a zero-arg-constructible
class satisfying the `Planner` protocol —

```python
def plan(self, observation, *, k: int, context: Mapping[str, Any]) -> list[CandidatePlan]: ...
```

`CandidatePlan` is `(actions: list[int], score: float, info: dict)`; higher
score = preferred; planners must be deterministic in `(observation, k,
context)`. Register one:

```toml
[project.entry-points."stargraph.planners"]
my-planner = "my_pkg.planning:MyPlanner"
```

`PlannerNode(planner="my-planner", k=3)` runs it over
`state.<observation_field>` and writes ranked plan dicts to
`state.<plans_field>`. World models are a **contract slot**, not a shipped
component: `PlannerNode(rollout_ref="pkg.mod:fn")` re-scores candidates
through any `fn(observation, actions, context) -> float` — a simulator
rollout or a learned dynamics model's rollout, indistinguishably.

### Reference implementation: `mpc-ca-burn`

A convex-MPC-style burn-option planner over the collision-avoidance
problem: enumerate the (burn epoch × direction) option lattice, pick the
fuel-minimal Δv bin whose post-burn operational Pc clears the maneuver
threshold (closed-form Clohessy–Wiltshire response + Foster encounter-plane
Pc, ported from the upstream geometry — not invented here), rank by fuel. When
nothing clears, it returns an explicit hold-out plan with the reason — never
an empty list. It is an offline option study (it sees the full recorded
event); use it for candidate generation, not as a deployable policy.
