# EMS scenario profiles (Week 3 enabler for Weeks 4/6 demos)
#
# The shared simulator already supports:
#   - SKID2_SIMULATION_STEP_SECONDS for accelerated clocks
#   - plant profiles via /api/v1/simulation/profile
#   - fault injection for comms-loss / recovery demos
#
# Recommended demo profiles:
#   sunrise_ev   — PV ramp + EV session + SOC gate crossing
#   soc_drawdown — force BA1 SOC through 18/10/8/20% for charger gates
#
# Drive SOC for charger demos by posting BA1 inputs through the Simulator Lab
# or /api/v1/simulation/devices/ba1-1/inputs with cluster_soc stepped down.
