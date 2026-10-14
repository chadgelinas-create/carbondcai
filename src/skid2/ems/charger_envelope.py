"""PMS → EVCS Available-Power envelope (Appendix A holdings, SIM codec).

One aggregate demand in (40001 INT32 W), one Available Power out (40101 INT32 W).
INT32 is two holdings, MSB big-endian as stated. System status 0-5 is 40103.
Siemens/EVCS still confirms exact addresses and word order before LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass

from skid2.ems.config import ConfigService

BUS_CAP_KW = 600.0
RAMP_KW_S = 20.0
FAULT_DELAY_S = 30.0
BUS_MIN_V = 712.5
BUS_MAX_V = 787.5
NORMAL_SOC = 0.186
RESERVE_STATUS_SOC = 0.075
RESERVE_FLOOR_SOC = 0.043
TIME_UNLIMITED_MIN = 999.0
PMS_LABELS: tuple[str, ...] = (
    "normal",
    "derated_pv",
    "derated_bess",
    "derated_both",
    "bess_reserve",
    "fault",
)


@dataclass(frozen=True, slots=True)
class EnvelopeDecision:
    available_kw: float
    target_kw: float
    status: int
    status_label: str
    time_remaining_min: float
    pv_kw: float
    bess_kw: float
    soc: float


class ChargerEnvelope:
    """Ramp Available Power only after fault recovery; otherwise publish the target."""

    def __init__(self) -> None:
        self._published = 0.0
        self._faulted = False
        self._clear_wait = 0.0

    def compute(
        self,
        *,
        pv_kw: float,
        soc: float,
        bess_limit_kw: float,
        bus_v: float,
        comms_ok: bool,
        charger_fault: bool,
        dt_seconds: float = 0.0,
        config: ConfigService | None = None,
    ) -> EnvelopeDecision:
        cap = _cfg(config, "supervisor", "bus_power_cap", BUS_CAP_KW)
        delay = _cfg(config, "supervisor", "fault_recovery_s", FAULT_DELAY_S)
        ramp = _cfg(config, "supervisor", "available_ramp_kw_s", RAMP_KW_S)
        pv = max(0.0, pv_kw)
        bus_ok = BUS_MIN_V <= bus_v <= BUS_MAX_V
        in_fault = (not comms_ok) or charger_fault or not bus_ok
        reserve = _cfg(config, "supervisor", "bess_reserve_soc", RESERVE_STATUS_SOC)
        normal = _cfg(config, "supervisor", "bess_normal_soc", NORMAL_SOC)
        floor = _cfg(config, "supervisor", "bess_reserve_floor", RESERVE_FLOOR_SOC)
        if soc <= reserve:
            bess = 0.0
        else:
            bess = max(0.0, bess_limit_kw)
            if soc < normal:
                span = max(normal - reserve, 1e-6)
                bess *= (soc - reserve) / span
        if in_fault:
            target = 0.0
            status = 5
        elif soc <= reserve:
            target = min(cap, pv)
            status = 4
            if pv <= 0 and soc <= floor:
                target = 0.0
        else:
            target = min(cap, pv + bess)
            pv_low = pv < cap * 0.2
            bess_low = soc < normal
            if pv_low and bess_low:
                status = 3
            elif bess_low:
                status = 2
            elif pv_low:
                status = 1
            else:
                status = 0
        published = self._apply(target, in_fault, dt_seconds, delay, ramp)
        holding = self._faulted and published <= 0.0
        code = 5 if holding else status
        return EnvelopeDecision(
            available_kw=published,
            target_kw=max(0.0, target),
            status=code,
            status_label=PMS_LABELS[code],
            time_remaining_min=TIME_UNLIMITED_MIN if published > 0 else 0.0,
            pv_kw=pv,
            bess_kw=bess,
            soc=soc,
        )

    def _apply(
        self, target: float, in_fault: bool, dt_seconds: float, delay: float, ramp: float
    ) -> float:
        if in_fault:
            self._faulted = True
            self._clear_wait = 0.0
            self._published = 0.0
            return 0.0
        if not self._faulted:
            self._published = target
            return self._published
        if dt_seconds <= 0:
            return 0.0 if self._clear_wait < delay else self._published
        self._clear_wait += dt_seconds
        if self._clear_wait < delay:
            self._published = 0.0
            return 0.0
        self._published = min(target, self._published + ramp * dt_seconds)
        if self._published + 1e-9 >= target:
            self._faulted = False
            self._clear_wait = 0.0
        return self._published


def _cfg(config: ConfigService | None, group_id: str, key: str, default: float) -> float:
    if config is None:
        return default
    try:
        return float(config.numeric(group_id, key))
    except (KeyError, TypeError, ValueError):
        return default
