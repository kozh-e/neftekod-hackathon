# PROJECT_STRUCTURE

Repository directory structure and file map for AI agents.

## Root Files
- AGENTS.md: System prompt, agent roles, domain rules, and workflow tasks.
- README.md: Overview of the MES/APC project, service endpoints, and launch instructions.
- main.py: FastAPI application with endpoints /api/v1/optimize and /api/v1/health.
- streamlit_app.py: Entry point for the Streamlit dispatcher console.
- Dockerfile: Container build based on python:3.12-slim.
- docker-compose.yml: Orchestration for API, Streamlit UI, and automated test services.
- requirements.txt: Python package dependencies.
- start.sh: Quick launch script for Docker services.

## Directories

### src/
Main source code for the MES/APC system.

- src/agents/: Decision-making logic and multi-agent coordination (LangGraph).
  - state.py: Pydantic v2 schemas and contracts (AgentState, SafetyAuditReport, telemetry).
  - graph.py: LangGraph execution workflow (DataGuard -> Optimization -> Auditors -> Arbitration -> Blending/SafeHold).
  - auditors.py: Parallel safety and quality auditors. ReliabilityAgent checks ESD limits (furnace P-3 T55, reactor R-202 W10, column K-10 P52, flow F31) and computes barrier risk penalty. QualityAgent checks Euro-5 diesel constraints (sulfur <= 9.5 ppm, density 821.25 to 843.75 kg/m3).
  - arbitration.py: Hybrid arbitration node. Stage 1: hard-veto gate. Stage 2: economic clearing (maximizing net utility = margin - penalty). Stage 3: deadband filter (margin threshold 1000 rub/h, step norm 0.05).
  - safe_hold.py: Safe hold state handler (sets delta_u = 0, returns standard dispatch message).
  - blending.py: Optimal in-line blending recipe calculation for diesel components.
  - lims.py: LIMS delay compensator (retrospective error, exponential bias decay T_half = 12h, first-order filter tau = 30m, 95% UCB).
  - data_guard.py: Telemetry data validation (stuck sensors, missing values, outliers).
  - anti_windup.py: Anti-windup protection for control setpoints.
  - optimization_stub.py: Control candidate generation model.

- src/twin/: First-principles physical digital twin.
  - fopdt.py: First Order Plus Dead Time dynamics for ELOU-AVT-6 and 24-2000 hydrotreater units.
  - vak.py: Vacuum distillation and fractionation model.

- src/xai/: Explainable AI narrative generator.
  - narrative.py: Generates deterministic operator explanations in Russian based on physics, chemistry, and economics.

- src/ui/: Operator interface (Human-in-the-Loop).
  - app.py: Streamlit dashboard displaying unit telemetry, arbitration decisions, XAI reports, approval controls, and tag overrides.

### artifacts/
Project architecture, design specifications, and hackathon requirements.
- System_Design.md: Detailed system design document (process math, agent protocols, architecture).
- whats_done.md: MVP implementation report and test status.
- roadmap.md: Development roadmap.
- tz_neftecode_full.md: Complete hackathon technical assignment.

### initial_data/
Raw source datasets, engineering diagrams, and specifications.
- 242000_tags.csv, avt_tags.csv: Process telemetry logs and tag values.
- Выгрузка ПАК 01.01.2023 - н.в_.xlsx: On-stream analyzer data.
- ЛИМСы 01.01.2023 - н.в_ (2).xlsx: Laboratory quality measurements.
- Теги_хакатон.xlsx: Tag registry and descriptions.
- pipeline.drawio, АВТ_схемы/: P&ID flowcharts and engineering schematics.
- ТЗ_нефтекод.docx: Original specification document.

### data/
Runtime and processed datasets.
- data/processed/: Directory for cleaned and normalized telemetry series.

### notebooks/
Jupyter notebooks for exploratory data analysis, twin calibration, and prototyping.

### tests/
Pytest test suite covering all modules:
- test_step1_mvp.py: State schemas and basic graph flow.
- test_step2_twin.py: Digital twin simulation (FOPDT, VAK).
- test_step3_blending.py: Blending recipe optimization.
- test_step4_arbitration.py: Two-stage arbitration, safety vetoes, and deadband filter.
- test_step5_lims.py: LIMS delay compensation and UCB confidence bounds.
- test_step6_xai_ui.py: XAI narrative generator and API endpoints.

### agentic-course/
Theoretical materials and reference documentation on multi-agent architectures and responsible AI.

### agents/
Instructions, prompts, and structure definitions for AI agents.
- PROJECT_STRUCTURE.md: This directory map.
- DOMAIN_KNOWLEDGE.md: Engineering handbook covering ESD limits, Euro-5 norms, blending math, and LIMS models.

### Service Directories (gitignored)
- .agents/: Subagent conversation logs and transcripts.
- .venv/: Local Python virtual environment.
- .claude/, .obsidian/: Editor metadata.
- __pycache__/, .pytest_cache/: Python and pytest bytecode caches.
