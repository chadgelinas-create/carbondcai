# Provisional SIM-only Modbus maps (Week 3)
#
# DC600 and Stabiliti ST-4/5/6 remain blocked for production promotion
# (see protocol/blocked/). For EMS demo readiness, the Device Manager
# publishes engineering values from the logical simulators using the
# logical_id contracts already emitted by:
#
#   - src/skid2/sim/dc600.py
#   - src/skid2/sim/stabiliti.py
#
# DC600 SIM candidate is protocol/devices/dc600-appendix-a.candidate.yaml
# (Appendix A expanded holdings, INT32 big-endian as stated). LIVE still
# needs Siemens/EVCS confirm of exact addresses and word order (OD-004).
# Stabiliti ST-4/5/6 remain blocked for production promotion.
#
#   - SIM path: Device Manager ← simulator values ← (optional) Modbus gateway
#   - LIVE path: Device Manager returns stale/0 until PLC adapters exist
#   - UI does not change when SKID2_DATA_SOURCE flips sim|live
#
# Reconciliation checklist (BESS / converters):
#   [x] BA1 cluster SOC 0x3007 present in ba1-bess-v1.18.candidate.yaml
#   [x] CNVTR-1 / CNVTR-2 Sinexcel v126 candidates in protocol/devices/
#   [ ] Confirm converter maps are vendor-final vs placeholder (OD-003)
#   [x] DC600 Appendix A candidate (SIM). LIVE Siemens confirm still open (OD-004)
#   [ ] Stabiliti Rev 15 EDS/AOP (OD-005) — external dependency
