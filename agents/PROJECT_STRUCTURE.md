# PROJECT_STRUCTURE

Repository directory structure and file map for AI agents and engineering teams.

## Root Files
- AGENTS.md: System prompt, expert analyst role, domain rules, and workflow tasks.
- README.md: Overview of the MES/APC project, service endpoints, and launch instructions.
- main.py: FastAPI application with endpoints `/api/v1/optimize` (with session support) and `/api/v1/health`.
- streamlit_app.py: Entry point for the Streamlit dispatcher console.
- Dockerfile: Container build based on `python:3.12-slim`.
- docker-compose.yml: Orchestration for API, Streamlit UI, and automated test services.
- requirements.txt: Python package dependencies.
- start.sh: Quick launch script for Docker services.

## Directories

### src/
Main source code for the MES/APC system.

- **src/agents/**: Decision-making logic, optimization, and multi-agent coordination (LangGraph).
  - `state.py`: Pydantic v2 schemas, candidate models (`ControlCandidate`), audit reports, and graph state (`MasGraphState`).
  - `limits.py`: Technological boundaries with source attribution (`NORM` vs `ASSUMPTION`) and historical violation frequencies.
  - `constraints.py`: Trajectory constraint assessment (`assess_limit()`) and statistical uncertainty offsets (`stat_offset()`).
  - `economics.py`: Incremental steady-state economic margin model relative to hold ($\Delta \text{Margin}_{\text{hold}}$), gross and net hourly operating margin calculations, and physical fuel gas consumption ($LHV$, $\text{MW}\cdot\text{h}$, $\text{nm}^3/\text{h}$).
  - `optimization.py`: Dynamic rollout optimization agent (`RolloutOptimizationAgent`) and graph node with runtime economic pricing injection.
  - `tanks.py`: Component storage tank models with ideal mixing and stock tracking.
  - `decision_log.py`: Structured JSONL decision audit logger for operational tracking.
  - `auditors.py`: Parallel safety and quality auditors. `ReliabilityAgent` monitors equipment limits (`AVT_T55`, `HT_P8`, `AVT_P52`, `AVT_F31`, `HT_GOR`, `FEED_TO_AVT`). `QualityAgent` performs statistical checks on sulfur ($\hat S + z \sigma_S \le 10.0$ ppm), flash, T95, and downstream recipe feasibility.
  - `arbitration.py`: Two-stage hybrid arbitration node with corrective clearing (`SUCCESS_CORRECTIVE`), normalized step norm, dynamic deadband configuration, and alternative evaluation.
  - `safe_hold.py`: Safe hold state handler (sets $\Delta \mathbf{u} = \mathbf{0}$, returns formal dispatch explanation).
  - `blending.py`: 3-component tank blending LP optimizer (hydrotreated diesel, kerosene, gasoil) with additives A & B, stock limits, and mass-based sulfur blending. Backward-compatible `solve_recipe()` wrapper.
  - `lims.py`: LIMS delay compensator (retrospective error, exponential bias decay $T_{1/2} = 12$ h, first-order filter $\tau = 30$ min, calibrated $\sigma_{S0} = 1.19$ ppm).
  - `data_guard.py`: Telemetry data validation (stuck sensors, missing values, critical tag clamping, confidence scoring).
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
  - `fopdt.py`: First Order Plus Dead Time dynamic filters (`FirstOrderDeadTime`).
  - `vak.py`: 17 official Virtual Analyzer of Quality (VAK) regression models matching `new_data/формулы_ВАК.xlsx`.

- **src/xai/**: Explainable AI narrative generator.
  - `narrative.py`: Generates deterministic operator explanations in Russian based on physics, dynamic predictions, rejected alternatives, confidence level, and model assumptions.

- **src/ui/**: Operator interface (Human-in-the-Loop).
  - `app.py`: Streamlit dashboard displaying official unit telemetry, arbitration decisions, dynamic forecast charts, recipe cards, alternative tables, interactive market pricing & tariff control panel, and approval controls.

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
- `scripts/replay_scenarios.py`: Scenario replay harness through LangGraph.

### config/
Committed configuration and calibrated parameter stores.
- `config/twin_params.json`: Calibrated digital twin parameters (DATA provenance).

### tests/
Pytest test suite covering all modules:
- Unit tests: tags, official VAK, kinetics, stabilizer, chain twin, rollout optimizer, audit/arbitration, blending, graph e2e.

### agents/
Instructions, prompts, domain knowledge, and architectural plans for AI agents.
- `DOMAIN_KNOWLEDGE.md`: Engineering handbook covering ESD limits, Euro-5 norms, blending math, and LIMS models.
- `ASSUMPTIONS.md`: Comprehensive engineering register of all model and equipment assumptions (ASSUMPTION provenance), rationale, and boundary rule mapping.
- `implementation_plan_v2.md`: Comprehensive v2 architectural plan, empirical audit, and low-level task specifications.
- `PROJECT_STRUCTURE.md`: This directory map.
