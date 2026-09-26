# PROJECT_STRUCTURE

> **Последнее обновление: сентябрь 2026.** ADR-26 завершён — `build_mvp_graph()` удалён, единственный граф — `core_v3`. Удалены: `optimization_stub.py`, `implementation_plan_v2.md`, `console_tz/mockups/`, `baseline_kpis.py`, `test_step1_mvp.py`, `supervisor/tools.py`. Все тесты на `build_core_graph()`.

Repository directory structure and file map for AI agents and engineering teams.

## Root Files
- AGENTS.md: System prompt, expert analyst role, domain rules, and workflow tasks.
- README.md: Overview of the MES/APC project, service endpoints, and launch instructions.
- main.py: FastAPI приложение. `graph_mode` в payload принимается, но все режимы направляются в `build_core_graph()` (legacy/shadow удалены).
- Dockerfile: Container build based on `python:3.12-slim`.
- docker-compose.yml: Orchestration for the API/console service and automated test services. The Streamlit UI (`streamlit_app.py`, `src/ui/app.py`) was removed; the operator console at `/console` is now the sole web UI (see `agents/console_tz/`).
- requirements.txt: Python package dependencies.
- start.sh: Quick launch script for Docker services.

## Directories

### src/
Main source code for the MES/APC system.

- **src/agents/**: Decision-making logic, optimization, and multi-agent coordination (LangGraph).
  - `contracts.py`: Pydantic v2 frozen dataclass contracts (§4.1–§4.7 implementation_plan_v3.md) for immutable data flows (`Measurement`, `DataAssessment`, `QualityEstimate`, `PlantEstimate`, `ConstraintSpec`, `ConstraintCertificate`, `ArbitrationDecision`, `DecisionTrace`).
  - `registry.py`: Unified constraint registry (T0–T3, 34 specs) with complete provenance and dependency tracking (`REGISTRY`, `validate_registry_provenance()`).
  - `policy.py`: Policy configuration, automation thresholds, safety alphas, and policy store (`PolicyConfig`, `AutomationThresholds`, `PolicyStore`).
  - `estimation.py`: Technological state estimation and LIMS calibration (`StateEstimator`, `HistoryBuffer`, `KalmanState`).
  - `uncertainty.py`: Uncertainty quantification, chance constraints, normal distribution quantiles (`chance_effective()`, `z_from_alpha()`, `SensitivityModel`).
  - `state.py`: Core state and re-exports for backward compatibility.
  - `state_legacy.py`: Preserved legacy models (`MasGraphState`, `ControlCandidate`, `SafetyAuditReport`, `FinalRecommendation`) under Strangler pattern.
  - `limits.py`: Technological boundaries with source attribution (`NORM` vs `ASSUMPTION`) and historical violation frequencies.
  - `constraints.py`: Trajectory constraint assessment (`assess_limit()`) and statistical uncertainty offsets (`stat_offset()`).
  - `economics.py`: Incremental steady-state economic margin model relative to hold ($\Delta \text{Margin}_{\text{hold}}$), gross and net hourly operating margin calculations, and physical fuel gas consumption ($LHV$, $\text{MW}\cdot\text{h}$, $\text{nm}^3/\text{h}$).
  - `optimization.py`: Dynamic rollout optimization agent (`RolloutOptimizationAgent`) and graph node with runtime economic pricing injection. Наследие MVP-графа (`RolloutOptimizationAgent`); в `build_core_graph()` не вызывается. Используется в `test_step7_rollout.py`, `test_step7_audit_arbitration.py`. Подлежит удалению вместе с этими тестами.
  - `tanks.py`: Component storage tank models with ideal mixing and stock tracking.
  - `decision_log.py`: Structured JSONL decision audit logger for operational tracking.
  - `auditors.py`: Parallel safety and quality auditors. `ReliabilityAgent` monitors equipment limits (`AVT_T55`, `HT_P8`, `AVT_P52`, `AVT_F31`, `HT_GOR`, `FEED_TO_AVT`). `QualityAgent` performs statistical checks on sulfur ($\hat S + z \sigma_S \le 10.0$ ppm), flash, T95, and downstream recipe feasibility. Наследие MVP-графа. Заменён `reliability.py` и `quality.py` в `core_v3`. Используется в `test_step7_audit_arbitration.py`. Подлежит удалению.
  - `reliability.py`: Dedicated ReliabilityAgent implementation (§5.4) verifying T1 equipment constraints (`FURNACE.COT_MAX`, `RX.DP_MAX`, etc.), furnace preconditions (`FURNACE.F31_MIN`, `COL.P52_MAX`), COT policy bands, and local candidate repair.
  - `quality.py`: Dedicated QualityAgent implementation (§5.5) verifying T2 quality constraints (`GODT.S_MAX`, `PRODUCT.FLASH_MIN`, etc.), chance constraints with measurement/prediction uncertainty, and stabilizing furnace move integration.
  - `furnace_ensemble.py`: Monte Carlo ensemble of 40 scenarios (seed=42) for robust stochastic evaluation of furnace COT and F31 transitions under uncertainty.
  - `supply.py`: Dedicated SupplyAgent implementation (§5.6) verifying T3 operational constraints on intermediate feed buffer inventory and long-run ratio management with candidate repair.
  - `twin_view.py`: TwinView adapter providing a uniform interface over digital twin predictions for constraint evaluation.
  - `context.py`: Context factory for assembling plant state, estimates, and data assessment into standard agent evaluation context.
  - `pareto.py`: Pareto analysis of admissible candidates (graph node between auditors and arbitration): fast non-dominated sorting (Deb 2002) over net margin, sulfur giveaway (minimax boundary) and catalyst WABT; vetoed candidates excluded before ranking; nearest trade-off alternatives (safer sulfur / gentler catalyst) with transition price; XAI summary and Plotly figures (3D, 2D projection, parallel coordinates).
  - `scenarios.py`: Input states of the 4 mandatory TZ demo scenarios (§7.2) shared by replay, tests and UI.
  - `generator.py`: Deterministic candidate generator (`CandidateGenerator`, `hold`, `signature_of`) generating HOLD, LOCAL, GLOBAL, REPAIR, and REFINE candidate sets.
  - `global_search.py`: Global continuous optimizer (`GlobalSearchAgent`) using multi-start SLSQP with COBYLA fallback over 16 Sobol points, solving steady-state continuous optimum (`solve`) and finding nearest feasible points (`nearest_feasible`).
  - `repair.py`: Candidate repair engine (`CandidateRepairer`) using linear sensitivity gradients and QP projections for local and joint agent constraint repair.
  - `negotiation.py`: Round-based agent negotiation protocol and nodes (`node_propose`, `node_predict`, `node_reliability`, `node_quality`, `node_supply`, `node_blending`, `node_coordinate`, `calculate_merit`).
  - `arbitration.py`: Two-stage lexicographic arbitration engine (`arbitrate_lexicographic`, `decide`) strictly enforcing Safety T0/T1 > Quality T2 > Supply/Ops T3 > Economics, generating recovery plans and Pareto-filtered trade-offs.
  - `recovery.py`: Multi-step incident recovery planner (`RecoveryPlanner`, `RecoveryPlan`) generating monotone safe trajectories when hold is in violation.
  - `decision_store.py`: SQLite and JSONL persistence backend (`DecisionStore`, `save_decision_trace`) for immutable execution traces.
  - `graph.py`: «Детерминированный граф переговоров v3 (`build_core_graph`, `get_graph`). `build_mvp_graph()` удалён (ADR-26 завершён). Единственный режим исполнения — `core_v3`.»
  - `blending.py`: 3-component tank blending LP optimizer with inventory constraints, `build_blend_problem()`, `solve_elastic()` hierarchical slack relaxation (`INFEASIBLE_ELASTIC`), `godt_prices()` finite-difference shadow pricing, `certify_blend()`, and backward-compatible `solve_recipe()`.
  - `lims.py`: LIMS delay compensator (retrospective error, exponential bias decay $T_{1/2} = 12$ h, first-order filter $\tau = 30$ min, defaults aligned with ADR-12 constants $z = 2$, $\sigma_{S0} = 0.83$ ppm) and `lims_age_from_state()` helper.
  - `data_guard.py`: Telemetry data validation (stuck sensors, missing values, critical tag clamping, confidence scoring, `assess_data`, degradation ladder).
  - `anti_windup.py`: Anti-windup protection for control setpoints.

- **src/twin/**: Grey-box physical digital twin of the complete processing train.
  - `tags.py`: Canonical tag registry (`TagSpec`) with `AVT_*` and `HT_*` prefixes, aliases, and nominal operating points.
  - `params.py`: Dataclass parameter hierarchy with provenance metadata (`NORM`, `REGISTRY`, `DATA`, `ASSUMPTION`), modern 2024–2026 economic benchmarks, and JSON loader.
  - `feed_link.py`: Dynamic inter-unit transfer link (delay $\theta = 10$ min, mixing $\tau_{\text{mix}} = 120$ min for T95, sulfur, density).
  - `kinetics.py`: Two-lump pseudo-first-order hydrodesulfurization kinetics (easy + refractory sulfur) and reactor pressure drop.
  - `stabilizer.py`: Stripper column model for flash point prediction as a function of feed rate, column pressure, and stripping gas.
  - `product.py`: Hydrotreated product property calculation (density, T95, CFPP, cetane number).
  - `chain.py`: Full chain digital twin (`FullChainTwin`) combining static unit models, FOPDT dynamics, and measurement bias assimilation.
  - `session.py`: Session store for digital twins (`TwinSessionStore`) preserving inertia between control cycles.
  - `plant.py`: `PlantSimulator` — dynamically consistent plant emulator (separate twin instance) for closed-loop scenario replay and tests.
  - `fopdt.py`: First Order Plus Dead Time dynamic filters (`FirstOrderDeadTime`).
  - `vak.py`: 17 official Virtual Analyzer of Quality (VAK) regression models matching `new_data/формулы_ВАК.xlsx`.

- **src/safety_kernel/**: Independent Fail-Closed Safety Kernel (§5.11 implementation_plan_v3.md).
  - `kernel.py`: Zero-dependency safety kernel (`SafetyKernel`, `verify`) validating all T0 hard bounds, rate of change, actuator limits, and data degradation permits before publication.
  - `rules.py`: Deterministic safety rules and invariant checks.

- **src/xai/**: Explainable AI decision card and narrative generator.
  - `card.py`: Deterministic 7-block XAI decision card generator (§5 ТЗ) providing comprehensive justification, Provenance traceability, constraint margins, alternative ranking, and operator advisory.
  - `narrative.py`: Deterministic operator explanations in Russian based on physics, dynamic predictions, rejected alternatives, confidence level, and model assumptions.

- **src/console/**: Operator console backend (`/api/console`), serving `static/console/` (HTML/JS/ECharts, no build step). Replaces the removed Streamlit UI as the sole operator/engineering web interface. See `agents/console_tz/` for the spec and `STATUS.md` for what has shipped.

- **src/supervisor/**: Asynchronous LLM Supervisor on OpenAI API for local Qwen 27B/32B (Stage P4, Gate G4).
  - `cassettes.py`: Deterministic cassette storage (`CassetteStore`), request fingerprinting (`calculate_fingerprint` via SHA-256), cassette persistence in `data/llm_cassettes/`.
  - `llm_client.py`: OpenAI-compatible client (`ReplayingOpenAIClient`) supporting `REPLAY_STRICT`, `LIVE_RECORD`, `OFF` modes, Qwen 27B/32B, and robust regex JSON extraction (`extract_json_payload`).
  - `evidence.py`: Deterministic evidence pack generation (`EvidencePackage`, `EvidenceRef`, `build_evidence_package`), canonical hashing, token bounding (< 4–5k tokens).
  - `prompts/`: Versioned Russian system prompts for roles (`diagnostics.v1.md`, `policy.v1.md`, `briefing.v1.md`, `operator_qa.v1.md`).
  - `agents.py`: Specialized role agents (`DiagnosticsAgent`, `PolicyAdvisor`, `BriefingAgent`, `OperatorQAAgent`) and structured Pydantic v2 schemas (`DiagnosticReport`, `PolicyProposal`, `ShiftBriefing`, `OperatorAnswer`).
  - `validation.py`: Verifiers for outputs (`GroundingChecker` with 1% numeric tolerance, `PolicyValidator` enforcing `POLICY_WHITELIST` tighten-only rule and blocking prompt-injections).
  - `shadow.py`: Offline replay engine (`ShadowReplayRunner`, `ShadowReport`) executing core scenarios S1–S4 to verify safety non-degradation before human approval.
  - `store.py`: Persistence backend (`SupervisorStore`, SQLite and JSONL) managing findings, shift briefings, and change requests lifecycle.
  - `graph.py`: LangGraph StateGraph orchestrating role routing, automated verification, shadow replay, human approval interruption, and publication.
  - `triggers.py`: Event trigger detection (`TriggerManager`) and cooldown timers (`KERNEL_OVERRIDE`, `REPEATED_REFUSAL`, `LIMS_PAK_CONFLICT`, `CALIBRATION_DRIFT`, `OPERATOR_REJECTIONS`, etc.).
  - `service.py`: Background runtime supervisor service (`SupervisorService`).

### artifacts/
Project architecture, design specifications, and hackathon requirements.
- `System_Design.md`: Detailed system design document (process math, agent protocols, architecture).
- `whats_done.md`: MVP implementation report and test status.
- `roadmap.md`: Development roadmap and team task backlog.
- `tz_neftecode_full.md`: Complete hackathon technical assignment.

### initial_data/ & new_data/
Raw source datasets, engineering diagrams, and official registers.
- `new_data/теги АВТ_24-2000.xlsx`: Official tag register (replaces KIP sheet of `Теги_хакатон.xlsx`).
- `new_data/формулы_ВАК.xlsx`: Official VAK equations with worked example calculations.
- `new_data/Ustanovka_AVT_merged (1).pdf`: Official manipulated variables, laboratory checks, and product specs.
- `initial_data/242000_tags.csv`, `avt_tags.csv`: Process telemetry logs.
- `initial_data/Выгрузка ПАК 01.01.2023 - н.в_.xlsx`: On-stream analyzer data.
- `initial_data/ЛИМСы 01.01.2023 - н.в_ (2).xlsx`: Laboratory quality measurements.
- `initial_data/Теги_хакатон.xlsx`: Original hackathon tag registry.
- `initial_data/ТЗ_нефтекод.docx`: Original specification document.

### scripts/
Offline calibration, scenario replay, and validation tools.
- `scripts/calibrate_twin.py`: Offline grey-box model calibration on historical train/test split.
- `scripts/replay_scenarios.py`: Full closed-loop replay harness executing scenarios S1–S8 on `PlantSimulator` v2, saving `data/processed/final_kpis.json` and printing §15 KPI comparison table.
- `scripts/demo_v3.py`: Interactive console demonstration for the hackathon jury covering the 5 key stories from TZ §6 (S1 normal, S2 quality risk, S3 data degradation, S4 MAS consensus & kernel, S5 envelope recovery + LLM supervisor).
<<<<<<< HEAD
- `scripts/estimate_quality_uncertainty.py`: TZ-method estimation of quality uncertainty (sulfur, T95, flash), furnace-to-draw response and giveaway boundary -> `data/processed/quality_uncertainty.json`.
=======
- `notebooks/01_model_evaluation.ipynb` §4.2: reproducible estimation of quality uncertainty (sulfur, T95, flash, D15, CFPP) -> `data/processed/quality_uncertainty.json`. Replaces the former `scripts/estimate_quality_uncertainty.py`, which was referenced but absent from the repository.
>>>>>>> d8990c58252fe6b9c42dc256151cbe43333a4fcd

### config/
Committed configuration and calibrated parameter stores.
- `config/twin_params.json`: Calibrated digital twin parameters (DATA provenance).
- `config/policy/`: Versioned policy configurations (`policy_v1.json`).

### tests/
Pytest test suite covering all modules:
- `tests/unit/`: Comprehensive unit tests for P1–P2 modules (`test_agents_reliability.py`, `test_agents_quality.py`, `test_agents_supply.py`, `test_blending_v2.py`, `test_data_guard_v2.py`, `test_estimation.py`, `test_registry.py`, `test_uncertainty.py`).
- `tests/audit/test_audit_regressions.py`: Formal regression suite E1–E12 for audit findings (12/12 PASSED, 100% green; all defects resolved in v3 core graph).
- `tests/test_architecture.py`: AST validation of architectural isolation (no LLM in agents, safety kernel independence).
- `tests/perf/test_cycle_budget.py`: Cycle budget profiling (p50 ~ 0.74s, p95 ~ 0.76s <= 2.0s) and hard 10.0s watchdog timer trigger test (`REFUSAL_TIMEOUT`).
- Legacy integration tests: tags, official VAK, kinetics, stabilizer, chain twin, rollout optimizer, audit/arbitration, blending, graph e2e (steps 1–7).
- `test_step8_pareto.py`: Pareto core vs brute force and ZDT1, Safety Ladder, sulfur 2σ and giveaway axis, trade-off alternatives, graph/API/decision-log integration, Plotly figures, latency.
- `test_step9_tz_compliance.py`: ADR-12 constants vs estimation report, furnace warning-zone veto, commercial-fuel T95 check, furnace twin/economics, TZ scenarios 1/3/4 (scenario 1 on `PlantSimulator`).
- `tests/llm_eval/`: Evaluation suite for asynchronous LLM Supervisor (Gate G4, 21 tests, 100% green):
  - `test_grounding_checker.py`: Fact verification, EvidenceRef addresses, and 1% numeric tolerance checks.
  - `test_policy_validator_injections.py`: Enforcement of POLICY_WHITELIST, tighten-only rule, and adversarial prompt-injection blocking.
  - `test_supervisor_cassettes_replay.py`: Deterministic replay across all roles in REPLAY_STRICT, missing cassette handling, and OFF mode.
  - `test_shadow_replay.py`: Simulation of proposed policy patches across scenarios S1–S4 confirming non-degradation of safety.

### agents/
Instructions, prompts, domain knowledge, and architectural plans for AI agents.
- `DOMAIN_KNOWLEDGE.md`: Engineering handbook covering ESD limits, Euro-5 norms, blending math, and LIMS models.
- `ASSUMPTIONS.md`: Comprehensive engineering register of all model and equipment assumptions (ASSUMPTION provenance), rationale, and boundary rule mapping.
- `implementation_plan_v2.md`: Historical v2 architectural plan, empirical audit, and low-level task specifications (current decisions: `ASSUMPTIONS.md`).
- `PROJECT_STRUCTURE.md`: This directory map.

