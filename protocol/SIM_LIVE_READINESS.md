# SIM → LIVE readiness (Week 7)

## Confirmed

- Operator EMS UI binds only to `/api/v1/ems/*` REST + `/api/v1/ems/ws`.
- `SKID2_DATA_SOURCE=sim|live` (and the on-screen Flip SIM|LIVE control) changes
  Device Manager source selection with **no UI code changes**.
- Config write-back is app-owned for the web/sim phase; audit rows record
  who / when / old → new.
- Disconnected / LIVE-without-PLC behavior forces stale quality and display `0`.

## Remaining for PLC phase (out of this roadmap)

- Real Modbus/Ethernet adapters for each LIVE host.
- Siemens/EVCS confirm of Appendix A INT32 word order and exact holdings before LIVE DC600.
- Vendor-final Stabiliti register maps replacing provisional SIM contracts.
- Decide whether the PLC owns setpoints (app requests) before energization.

## Production checklist

- [ ] TLS terminator in front of backend/panel
- [ ] Rotate `SKID2_AUTH_SECRET` and DB credentials
- [ ] Disable simulation (`SKID2_SIMULATION_ENABLED=false`)
- [ ] Private network path to field devices (no public Modbus)
- [ ] Offsite backup restore test
- [ ] Fault-matrix CI green (`tests/ems/test_fault_matrix.py`)
