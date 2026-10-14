# Carbon DC Skid 2 PMS/EMS

Private implementation and verification repository for the Carbon DC Skid 2 Power Management
System (PMS) and Energy Management System (EMS).

The project is building a software-based digital twin of the Skid 2 power system, deterministic
reference control logic, an industrial backend and historian, an operator panel, and the artifacts
needed to transfer approved supervisory behavior to an Allen-Bradley CompactLogix 5380 PLC.

The software can be developed and tested without physical equipment. The completed production
system will run on Carbon DC-controlled on-site infrastructure and communicate with the real PLC
and plant devices. Cloud services are optional support systems and are never part of the fast or
safety-critical control path.

## Current implementation status

The repository currently provides the software foundation—not the completed plant controller:

- a reproducible Docker Compose development environment;
- one version 1 FastAPI backend with health, current-state, simulation, and EMS APIs;
- TimescaleDB/PostgreSQL persistence with Alembic migrations (telemetry, config, audit, events, auth);
- React/TypeScript operator EMS (login, Overview, BESS, converters, solar, EV charge, Stabiliti, historian)
  plus the development Simulator Lab;
- WebSocket live telemetry fan-out and SIM|LIVE source switching with no UI change on flip;
- app-owned configuration write-back with audit rows (BMS/MPPT/charger/Stabiliti/supervisor);
- charger SOC availability gates (18/10/8% / recover 20%, 10 kWh cap);
- a localhost-only Modbus TCP gateway for the three candidate simulator device routes;
- seven deterministic logical device simulators with normal test values, guided fault tests,
  recovery history, exact replay, and downloadable reports;
- a shared DC-bus energy balance, bounded voltage response, and per-device power accounting;
- explicit live/stale data-quality behavior and historian correlation;
- a versioned protocol schema, validators, codecs, candidate device maps, deterministic generators,
  review artifacts, and drift tests;
- Python, panel, database, container, secret, walking-skeleton, and simulator acceptance gates.

The following major capabilities are still future work:

- vendor-final DC600 and Stabiliti Modbus maps (provisional SIM contracts documented under protocol/);
- detailed coupled PV, SOC, thermal, auxiliary-load, and EV-session models;
- deterministic Python PMS/EMS reference control and scenario suite;
- production CompactLogix Structured Text and Python-to-PLC parity testing;
- production TLS/ops hardening beyond the compose.prod.yaml overlay;
- confirmed real-device communication adapters, HIL, FAT, SAT, and on-site commissioning.

Candidate protocol files and synthetic telemetry are not evidence that real equipment interfaces or
production control behavior have been approved.

## System being controlled

The intended system contains:

| Equipment | Project role |
| --- | --- |
| `CNVTR-2` | Client-confirmed PV MPPT converter and current 750 VDC bus-forming converter |
| `CNVTR-1` | BESS-side DC/DC converter |
| `BA1-1` | Battery Energy Storage System (BESS) and Battery Management System (BMS) |
| `ST-4`, `ST-5`, `ST-6` | CE+T Stabiliti units supplying/grid-forming 480 VAC auxiliary and site loads |
| `TB1` | Shared nominal 750 VDC bus |
| `DC600` | EV charging controller coordinating demand, availability, and downstream Satellites |
| CompactLogix 5380 | Intended production supervisory PLC |

The SRC-033 client clarification establishes the converter numbering above. Do not reverse the
`CNVTR-1` and `CNVTR-2` roles based on the superseded Rev 5.2 equipment-number wording.

Conceptually:

```text
PV ───────────────→ CNVTR-2 (MPPT / DC-bus forming) ──→ 750 VDC bus
                                      ↑                         │
BESS BA1-1 ──→ CNVTR-1 (BESS DC/DC) ─┘                         ├─→ DC600 → Satellites → EVs
                                                                └─→ ST-4/5/6 → 480 VAC loads
```

Exact installed models, firmware, communication endpoints, converter sequences, DC600 handshake,
Stabiliti interface, timing, protection values, and command acknowledgement remain subject to
controlled engineering and vendor confirmation.

## Control and safety boundary

Responsibility is deliberately separated:

- Converter firmware and protection hardware own fast voltage/current regulation and local trips.
- The BMS owns battery protection and dynamic charge/discharge limits.
- The CompactLogix PLC will own production supervisory states, permissives, interlocks, bounded
  commands, communication quality, and approved fault responses.
- Python is an executable reference and test oracle; it does not control the commissioned plant.
- The backend, database, and React panel are supervisory. Their loss must not prevent safe local PLC
  operation.
- Simulation fault injection must be unavailable in production.

This repository does not constitute an approved safety case, protection study, energization
procedure, or authorization to command field equipment.

## Technology stack

| Area | Technology |
| --- | --- |
| Python services | Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic |
| Database/historian | PostgreSQL 17 with TimescaleDB |
| Operator panel | React 19, TypeScript, Vite |
| Containers | Docker Engine and Docker Compose |
| Python quality | pytest, coverage, Ruff, mypy |
| Panel quality | Vitest, Testing Library, ESLint, Prettier, TypeScript |
| Protocol source | Versioned JSON-compatible YAML, deterministic Python generation |
| CI | GitHub Actions |
| Production PLC target | Allen-Bradley CompactLogix 5380, Studio 5000 Structured Text |
| PLC emulation/parity target | Logix Echo |

Native Python and Node installations are optional for ordinary development because the supported
workflow runs inside containers.

## Repository layout

```text
.
├── src/skid2/
│   ├── application/       # use cases, runtime orchestration, and ports
│   ├── backend/           # FastAPI HTTP boundary and response schemas
│   ├── contracts/         # typed domain values and clock abstractions
│   ├── persistence/       # database metadata and telemetry repositories
│   ├── protocol/          # schema loading, validation, codecs, import, and generation
│   ├── reference/         # future deterministic reference controller
│   └── sim/               # deterministic device models and shared fault-test rules
├── panel/                 # React/TypeScript operator panel
├── protocol/
│   ├── schema/            # versioned protocol schema
│   ├── devices/           # candidate BESS and converter definitions
│   ├── blocked/           # explicit missing-interface manifests
│   ├── reference/         # quarantined legacy/reference contracts
│   └── golden-vectors.json
├── generated/protocol/    # deterministic generated review/code artifacts
├── migrations/            # Alembic database migrations
├── tests/                 # Python unit, contract, boundary, and integration tests
├── scripts/               # quality, protocol, secret, reset, and E2E scripts
├── evidence/              # retained phase-gate evidence
├── compose.yaml           # local multi-container topology
├── Dockerfile             # Python quality/build/runtime stages
└── Makefile               # supported developer commands
```

The manually maintained protocol definitions live in `protocol/`. Never hand-edit
`generated/protocol/`; change the controlled input and regenerate it.

## Prerequisites

Install:

- Git;
- Docker Engine;
- Docker Compose v2;
- GNU Make.

Recommended minimum for local Docker development:

- 4 CPU cores;
- 8 GB RAM;
- 10 GB free disk space.

Ensure ports `1502`, `5173`, `8000`, and `5432` are available, or change their host-side values in
`.env`.

## First-time setup

Clone the private Carbon DC repository and enter it:

```bash
git clone https://github.com/Carbon-DC/skid2-pms.git
cd skid2-pms
```

Create local development configuration:

```bash
cp .env.example .env
```

The values in `.env.example` are safe local examples. Do not commit `.env`, real passwords,
private keys, tokens, certificates, or production endpoints.

Build and start the complete current stack:

```bash
make up
```

`make up` starts three long-running containers:

1. `db` runs TimescaleDB/PostgreSQL;
2. `backend` applies migrations and serves every `/api/v1` endpoint, including simulation;
3. `frontend` serves the React application and proxies `/api` to `backend`.

The stack waits for each service health check. There is no separate simulation backend.

Confirm service status:

```bash
make status
```

Open the UI:

- Local Operator EMS: <http://127.0.0.1:5173/ems-ui/>
- Local panel (sign in `admin` / `admin`): <http://127.0.0.1:5173>
- Cloudflare: <https://carbondcai-ems.pages.dev/>
- Netlify: <https://carbon-dc-ems-ui.netlify.app/>
- Netlify demo: <https://carbon-dc-ems-ui-demo.netlify.app/>

## Local endpoints

| Endpoint | Purpose |
| --- | --- |
| <http://127.0.0.1:5173> | Simulator Lab development panel |
| <http://127.0.0.1:5173/ems-ui/> | Operator EMS UI (HTML) |
| <http://127.0.0.1:8000/api/v1/health/live> | FastAPI process liveness |
| <http://127.0.0.1:8000/api/v1/health/ready> | Database, TimescaleDB, and migration readiness |
| <http://127.0.0.1:8000/api/v1/current-state> | Typed current synthetic state |
| <http://127.0.0.1:8000/api/v1/health/components> | Component-level health |
| <http://127.0.0.1:8000/api/v1/simulation> | Simulation and device status |
| <http://127.0.0.1:8000/api/v1/simulation/faults> | Supported fault tests and current tests |
| <http://127.0.0.1:8000/api/v1/simulation/report> | Downloadable simulator test report |
| `127.0.0.1:1502` | Simulator-only Modbus TCP gateway |
| `127.0.0.1:5432` | Local PostgreSQL/TimescaleDB port |

The development frontend proxies all `/api/v1` requests to the single backend container.

### Modbus TCP simulator connection

The backend runs a real Modbus TCP listener when the Simulator Lab stack is enabled. Local Compose
binds the host port only to `127.0.0.1`; staging publishes it as described below.
Use these simulator-only routes in a standard Modbus TCP client:

| Simulated device | Device ID | Read function |
| --- | ---: | ---: |
| `BA1-1` | 1 | 4 (input registers) |
| `CNVTR-1` | 2 | 3 (holding registers) |
| `CNVTR-2` | 3 | 3 (holding registers) |

To verify the connection manually:

1. Run `make up` and open the Simulator Lab.
2. Confirm the Modbus TCP section says **Ready for connections** at `127.0.0.1:1502`.
3. Connect a Modbus TCP client to that address using device ID `1`.
4. Select **Refresh connection** and confirm the client count changes.
5. Close and reconnect the client, refreshing after each change.
6. Request an unknown address and confirm the client receives a Modbus exception while the backend
   remains available.
7. Run `make down` and confirm the port closes.

The generated register bank supplies addresses, scaling, byte order, and word order. It contains
candidate data, not a production-approved equipment interface. `BA1-1`, `CNVTR-1`, and `CNVTR-2`
serve explicit Simulator Lab values through these read-only routes. DC600 and the three Stabiliti
units remain internal software simulations until their vendor protocol details are supplied.

### Modbus connections on staging

Staging runs monitoring, configuration and simulation in one backend process, matching the
single-owner database contract. All HTTP and WebSocket API traffic goes to that backend.
The `simulator` Docker hostname remains an alias for it, so existing saved connections keep working.
Deployment removes the retired separate simulator container without removing database volumes.

For **Battery monitoring** on the deployed website, propose and apply a SIM connection to
`simulator`, port `1502`, unit ID `1`. `127.0.0.1:1502` also works inside the combined backend.
Settings remain subject to the existing save, approve and apply workflow.

For **Simply Modbus or another desktop client**, use the staging server's public IP/DNS name,
TCP port `1502` and the device IDs in the table above. No SSH tunnel is required. In the server's
`.env.staging`, set `SKID2_MODBUS_PUBLIC_HOST` to that IP/DNS name so Simulator Lab displays it.
For the current server this is `SKID2_MODBUS_PUBLIC_HOST=54.183.235.18`.

Staging publishes `0.0.0.0:1502` by default. Allow inbound TCP `1502` in the cloud security group
and any host firewall **only from approved client public IPs**; this raw simulator protocol does
not use the website's sign-in. The API remains bound to server localhost. Local development port
bindings are unchanged. If `SKID2_MODBUS_HOST_PORT` is overridden, desktop clients must use that
published port; the website's backend connection still uses the internal port `1502`.

## Plant Lab

Open the Simulator Lab and select **Plant Lab**. Set `SKID2_SIMULATION_PROFILE=stage0` and
`SKID2_SIMULATION_SEED=0` in an existing local `.env`; both are in `.env.example` for new checkouts.
The seed must be an explicit whole number from 0 to 2147483647. Production rejects simulation.

The backend owns one clock for all seven devices and the plant. **Start** advances it automatically;
**Pause** stops it; **Next step** accepts one whole frame. Opening another browser does not create
another clock. The browser refreshes the committed state once per second.

Applied plant settings and accepted device test inputs are saved automatically in PostgreSQL.
Unsubmitted form edits are not saved. Device inputs remain marked as pending until the next step.

**Save run** stores one complete checkpoint, including simulation time, device and battery state,
fault progress, activity, and replay history. After a server restart, that checkpoint restores
**paused**, using its own settings and step duration. Select **Continue** to advance it. Progress
after the last manual save is lost on restart; there are no automatic checkpoints.

**New run** starts at step zero with the latest saved settings. **Reset run** rewinds the current
experiment, requeues its accepted inputs, and clears progress and fault tests. Neither action nor
applying another profile deletes the saved checkpoint. **Delete saved run** removes only that
checkpoint; settings and the working run remain intact.

Storage uses the existing `app_config` table (`simulation.settings` and `simulation.checkpoint`).
Run the application's existing migrations before startup. An unavailable database or incompatible
checkpoint leaves the simulator unavailable with an explanatory message; it does not erase saved
data or silently substitute a fresh experiment. Model changes that alter continuation semantics
must increment the checkpoint engine version in `SimulationService`.

**Repeat steps** checks device and plant traces before replacing the active run.

The **Basic simulation** profile (`stage0`, revision `stage0-3`) uses model version `5`, starts at `2026-08-01T10:00:00Z`, and records
`SKID2_SIMULATION_STEP_SECONDS` (default 1 second; provisional software range 0.001–60 seconds).
The recorded seed is reserved for later stochastic models; this model uses no randomness.

Each frame feeds the last committed shared outputs into the existing device models, in order:
`ba1-1`, `cnvtr-1`, `cnvtr-2`, `dc600`, `st-4`, `st-5`, `st-6`. Their new device results then enter
the pure plant step. Validation precedes publication of either side. A rejected plant frame pauses
the run, preserves the previous state and pending work, and reports a bounded failure code.
A simulated device fault retains the existing fault and unavailable-device behavior.

The typed plant snapshot contains bus, solar, battery, loads, converter, temperature, and power
ledger summaries, plus seven device ports. Bus energy, voltage and power accounting are calculated.
Selected solar and battery setups add power, energy and loss calculations. Detailed load,
converter and thermal dynamics remain incomplete. Reported device
test values keep their quality and remain separate from calculated power. Missing, blocked, stale, invalid, and communication-error values
remain distinct from numeric zero. Capability evidence, sources, assumptions, review status,
profile restrictions, and calibration fields travel with the configuration, API, trace and report.

The bus uses provisional settings: 750 V initial voltage, 1 F effective capacitance, zero bus loss,
and a mathematical range of 0–1500 V. These are simulation assumptions, not approved protection
settings. Power is in kW, energy in kWh, and time in seconds. Positive converter power supplies the
bus; negative battery-converter power charges the battery. Battery transfer is counted at CNVTR-1
once. CNVTR-2 supplies the solar contribution. DC600 accepted demand and the explicit Stabiliti
**Power needed from DC bus (kW)** entries consume bus power; existing actual-power readings are not
substituted. At a shared limit, each source or load receives the same fraction of its accepted power.

`Energy = capacitance × voltage² / 7,200,000`. Each calculated frame accounts for supplied power,
served load, modeled loss, and the energy change within 0.000001 kW. Unused supply and unmet demand
remain visible. Missing or bad required readings pause this calculation: voltage and energy retain
their last sample time and become stale. Device frames continue, skipped steps are counted, and
recovery resumes from the held energy without reconstructing the skipped interval. The API exposes
the latest 120 voltage-history samples; reports and replay retain the full trace.

To see a known response with one-second steps:

1. Reset and pause the run. In **Devices**, set each Stabiliti bus-load entry to `0` and apply it.
2. Set DC600 demand to `0`, available power to `100`, keep both freshness switches on, and apply.
3. Take one step. The bus stays at 750 V.
4. Enable CNVTR-2 and its solar-available switch. Set requested power to `10`, available power to
   `100`, apply, and take a step. Voltage rises to about 763.217 V.
5. Set DC600 demand to `20`, apply, and take a step. Voltage returns to 750 V.
6. In **Plant Lab**, inspect **Power by device** and **View voltage readings**. Save run, change
   inputs, then restore or repeat the steps to compare the same trace.

See the [bus delivery record](evidence/phase-5/f02-record.md) for equations, boundary conventions,
known responses, acceptance results, and remaining equipment review.

To try battery storage, pause the run, choose a **Battery setup**, then **Save for review**. An administrator approves and applies the proposal in **Settings history**.
The choices are explicitly assumed usable capacities of 2,700, 3,500 and 3,850 kWh; installed
capacity remains unresolved. Each starts half full, with assumed 95% charge/discharge efficiency
and 100 kW bus-facing power limits. **Not included** preserves the basic simulation behavior.
For charging, select **Passing cloud**, set DC600 demand to zero, and enter a negative requested
power such as `-40` in the battery converter controls. For discharging, select **Basic simulation**
with a battery setup, request `40`, and set DC600 demand to `100` with `100` available power.

**Battery storage** shows stored energy, battery level, direction, power, limits, losses and why
power is limited. The chart has a keyboard-accessible readings table. Battery level is derived
from stored energy; the BMS **Reported battery level** remains a separate test input.
The API retains the latest 120 battery samples; reports and replay retain the full trace.

For actual bus power `P` and elapsed hours `h`, discharge changes stored energy by
`-P × h / discharge_efficiency`; charge changes it by `-P × h × charge_efficiency`.
The loss satisfies `P + loss + energy_change / h = 0` and appears once in the battery ledger.
Energy bounds constrain power before bus allocation. Bad required readings hold energy stale
without backfilling missed time. Idle self-consumption and self-discharge are not modeled.
See the [battery trace and equations](tests/fixtures/battery-trace.md) for assumptions and checks.

The existing `/api/v1/simulation` API now also provides:

| Route | Purpose |
| --- | --- |
| `GET /snapshot` | Read current coupled state |
| `POST /snapshot` | Permanently save the complete checkpoint |
| `DELETE /snapshot` | Delete only the saved checkpoint |
| `POST /new` | Start a paused run from the latest saved settings |
| `POST /restore` | Restore the saved checkpoint while paused |
| `GET /profiles` | List supported profile IDs |
| `POST /profile` | Apply `{ "profile_id": "stage0", "seed": 0, "battery_profile": "battery_3500" }` while paused; omit or use `null` for no battery model |
| `GET /report` | Download report schema 2 with original device fields and added plant evidence |

The state response includes `plant`, `plant_trace_hash`, `snapshot_saved`, `saved_run`,
`restored_run`, and `failure`. Each device also exposes `applied_inputs` and `inputs_pending`.
Checkpoint storage uses the existing application tables and runtime dependencies.
See [F01 evidence and remaining review requirements](evidence/phase-5/f01-record.md).

## Normal development loop

Use Linear as the planning system and GitHub as the source/review system:

1. Select a Ready Linear ticket.
2. Create a short-lived branch.
3. Implement the smallest complete change.
4. Run the relevant automated checks.
5. Push the branch and open a pull request.
6. Review test results and generated drift.
7. Merge only after required checks and review pass.
8. Update the Linear ticket and record remaining blockers honestly.

Do not report synthetic simulation, candidate protocol data, or a green UI as production hardware
acceptance.

## Quality and tests

Run Python formatting, linting, typing, tests, and coverage:

```bash
make python-check
```

Run panel formatting, linting, typing, tests, coverage, and production build:

```bash
make panel-check
```

Run the complete simulator acceptance gate, including all seven devices, every supported fault
type, recovery, coupled snapshots and replay, backend stepping, production lockout, the frontend
proxy, and live Modbus reads:

```bash
make simulation-gate ENV_FILE=.env.example
```

CI runs formatting, lint, type checks, protocol drift validation, and production image builds.
Python tests, panel tests, browser tests, and coverage thresholds run locally only; they are not
part of the CI pipeline.

Run the browser checks locally with Node 24.18.0 from `panel`:

```bash
npm ci
npx --no-install playwright install --with-deps chromium
npm run test:visual
```

The browser suite covers desktop/tablet, light/dark, keyboard focus, and offline/recovery states.
Screenshot baselines are reviewed files in `panel/e2e/simulator.spec.ts-snapshots`. Failed browser
traces are retained in `panel/test-results`. `make panel-check` does not run browser tests.
For the optional live browser case, point `SKID2_PANEL_API_PROXY` at an isolated acceptance backend
and set `SKID2_LIVE_API=true`; run with `--workers=1` because the two viewport cases share that run.

Validate protocol inputs, codecs, generated artifacts, production blockers, and deterministic
drift:

```bash
make protocol-check
```

Run the read-only repository secret scan after the Python quality image has been built:

```bash
make secret-check
```

Validate the selected Compose configuration:

```bash
make compose-config
```

Build production-target container images:

```bash
make images
```

### End-to-end walking-skeleton verification

Run the isolated software-only Phase 2 proof:

```bash
make walking-skeleton-check
```

This test creates an isolated Compose project and proves:

- database migration and repeatability;
- one synthetic telemetry value through source, persistence, API, and panel proxy;
- historian persistence across service restart;
- live-to-stale and stale-to-live transitions;
- explicit source identity and data freshness;
- migration downgrade/rebuild;
- cleanup of the isolated test resources.

It does not prove device simulators, PMS dispatch, real PLC behavior, real communications,
electrical stability, protection coordination, or commissioning readiness.

## Protocol workflow

Protocol definitions have explicit lifecycle states:

- `candidate`: reviewable but not approved for production;
- `reference`: retained for comparison and prohibited from production use;
- `blocked`: required interface is missing and no points may be invented;
- `production`: must pass every applicability, review, endpoint, timing, command, and safety gate.

Useful commands:

```bash
make protocol-check

docker run --rm skid2-python-quality:local \
  python -m skid2.protocol validate protocol/devices/*.yaml

docker run --rm skid2-python-quality:local \
  python -m skid2.protocol generate

docker run --rm skid2-python-quality:local \
  python -m skid2.protocol check-drift
```

When changing a protocol point:

1. verify the authorized vendor source, model, firmware, and map applicability;
2. update only the manually maintained protocol input;
3. preserve display notation separately from zero-based wire offsets;
4. validate access, scaling, units, word/byte order, timing, quality, and command behavior;
5. regenerate all outputs;
6. inspect the diff and run `make protocol-check`;
7. obtain the required engineering/vendor review before production promotion.

## Database and migrations

The Docker runtime uses `entrypoint.sh`. Its `serve` command runs `alembic upgrade head`
before starting the backend; a migration failure prevents startup. The simulator listener starts
inside the same backend process after the database is ready.

Staging deployment builds the backend and frontend from the selected commit,
waits for the database, and runs migrations in a temporary backend container before recreating
the application services. If migrations fail, deployment stops before replacing those services.
The backend's subsequent startup migration check is a no-op when the database is already current.

Apply migrations manually:

```bash
make migrate
```

The equivalent command on the staging server is:

```bash
docker compose -f compose.yaml -f compose.staging.yaml --env-file .env.staging run --rm --no-deps backend migrate
```

This uses the backend image's Alembic configuration and the server's database credentials.
The database must already be running and healthy when using `--no-deps`.

Show the installed database revision:

```bash
make db-revision
```

Follow service logs:

```bash
make logs
```

Stop the stack without deleting persistent database data:

```bash
make down
```

Reset local development data only:

```bash
CONFIRM_RESET=skid2-local make reset-dev-data
```

The reset command refuses to run unless `.env` explicitly selects
`SKID2_ENVIRONMENT=development`.

## Environment configuration

All application settings use the `SKID2_` prefix.

| Variable | Purpose |
| --- | --- |
| `SKID2_ENVIRONMENT` | `development`, `test`, or `production` |
| `SKID2_COMPOSE_PROJECT` | Isolates Compose resources by project name |
| `SKID2_SERVICE_NAME` | Service identity used in diagnostics |
| `SKID2_SERVICE_HOST` / `SKID2_SERVICE_PORT` | Python service bind settings |
| `SKID2_LOG_LEVEL` | Python log level |
| `SKID2_DATABASE_URL` | PostgreSQL connection string |
| `SKID2_SYNTHETIC_ENABLED` | Enables the current synthetic source outside production |
| `SKID2_SYNTHETIC_INTERVAL_SECONDS` | Synthetic measurement interval |
| `SKID2_FRESHNESS_THRESHOLD_SECONDS` | Age after which the value becomes stale |
| `SKID2_SYNTHETIC_MAX_SAMPLES` | Optional deterministic sample limit for tests |
| `SKID2_SIMULATION_ENABLED` | Enables development/test simulation APIs and UI |
| `SKID2_SIMULATION_PROFILE` | Repository-owned profile (`stage0`) |
| `SKID2_SIMULATION_SEED` | Required explicit integer seed when simulation is enabled |
| `SKID2_SIMULATION_STEP_SECONDS` | Shared fixed step and backend tick interval |
| `SKID2_MODBUS_ENABLED` | Enables the simulator-only Modbus TCP gateway |
| `SKID2_MODBUS_BIND_HOST` | Listener address inside the running backend |
| `SKID2_MODBUS_CLIENT_HOST` | Safe address shown to Simulator Lab users |
| `SKID2_MODBUS_PORT` | Modbus TCP port inside the backend container |
| `SKID2_MODBUS_BA1_ID` / `SKID2_MODBUS_CNVTR1_ID` / `SKID2_MODBUS_CNVTR2_ID` | Simulator device IDs |
| `SKID2_MODBUS_BANK_PATH` | Generated simulator register-bank path |
| `SKID2_MODBUS_HOST_PORT` | Published Modbus TCP port; defaults to 1502 |
| `SKID2_MODBUS_PUBLIC_HOST` | Staging server IP/DNS shown in Simulator Lab for desktop clients |
| `SKID2_MODBUS_PUBLISH_HOST` | Staging Modbus binding; defaults to 0.0.0.0 (local Compose stays localhost-only) |
| `SKID2_DB_HOST_PORT` | Host-side database port |
| `SKID2_API_HOST_PORT` | Host-side FastAPI port |
| `SKID2_PANEL_HOST_PORT` | Host-side Vite port |

Production settings reject the published local database credential, synthetic telemetry, simulation
controls, and the simulator Modbus gateway.

## Troubleshooting

Inspect current containers:

```bash
docker compose --env-file .env ps
```

Inspect recent logs:

```bash
docker compose --env-file .env logs --tail=200
```

Rebuild after dependency or Dockerfile changes:

```bash
docker compose --env-file .env up --build --detach --wait
```

If a host port is busy, change the corresponding `SKID2_*_HOST_PORT` value in `.env`.

Do not delete volumes to solve an unknown database problem. Inspect logs and migration status
first. Use the guarded reset command only when disposable local development data may be removed.


## License

Copyright © 2026 Carbon DC. All rights reserved.

## Reviewed settings and local verification

Connection settings and simulation setup now follow **Save for review → Approve settings →
Apply settings**. Open **Settings history** to compare proposed values, see who approved them,
inspect unsuccessful attempts, or restore an earlier approved version. Saving a proposal does
not change the current connection or run. Operators may propose simulation settings;
administrators approve and apply changes. A separate second approver is not required by this
implementation.

“Applied” means installed in the application. Battery communication and measured values are
reported independently in Battery monitoring. Simulation activation requires a paused run and
starts a new paused run. It retains the manually saved checkpoint. A restored checkpoint can
use an older setup; Settings history explicitly distinguishes that run from the last committed
settings. Select **New run** to use the committed setup, or restore an approved version through
Settings history.

The additive migration `20261004_0004` imports existing connection/setup values with explicit
import provenance, preserves checkpoints, and does not invent previous approvals. Run one
backend process per database; a database advisory lock prevents multiple runtime owners.
Interrupted configuration actions are reconciled on startup against the last committed settings.
A change whose final database commit cannot be confirmed remains pending until restart.

Local verification (no test runners were added to CI):

```sh
make walking-skeleton-unit-check
# This must point to a disposable database named *_test or skid2_closeout*.
# Integration checks clear application and telemetry data in that database.
export SKID2_TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/skid2_test'
make configuration-check
```

`walking-skeleton-check` and `simulator-lab-check` now run the real database/session/Modbus
integration harness instead of referencing a missing test file. The harness covers migrations,
immutable values, approval, conflicts, failed application/final commit, timeout audit,
checkpoint preservation and an actual backend process restart.

For an already running local simulated battery connection, `python -m skid2.verification`
correlates the authenticated monitoring API with the exact committed historian sample.
Set `SKID2_VERIFY_USERNAME` and `SKID2_VERIFY_PASSWORD` in the invoking environment; do not put
credentials in command-line arguments. The original synthetic endpoint remains supported with
`--api-url http://127.0.0.1:8000/api/v1/current-state`.

The current architecture, limitations and verification record are in
[Configuration lifecycle and closeout record](CONFIGURATION.md).
