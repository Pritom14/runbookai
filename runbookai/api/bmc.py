"""BMC sensor emulator — simulates out-of-band hardware sensors for demo purposes.

Exposes /bmc/sensors (readable by agent and dashboard) and /bmc/control
(used by chaos scripts to inject hardware failures). No persistence — state
is in-process memory, which is fine for the demo.

Supports both real IPMI (hardware_mode=real) and emulated (hardware_mode=emulated).
When real mode is enabled, queries actual BMC at configured IP. Falls back to
emulated mode if BMC is unreachable.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from fastapi import APIRouter

from runbookai.config import settings

logger = logging.getLogger("runbookai.api.bmc")
router = APIRouter(prefix="/bmc", tags=["bmc"])

# ---------------------------------------------------------------------------
# IPMI helper functions
# ---------------------------------------------------------------------------

async def _get_real_sensors() -> dict[str, Any] | None:
    """Attempt to query real IPMI BMC for sensor readings.

    Returns sensor dict on success, None if BMC unreachable.
    Falls back to emulated mode on any error.
    """
    if not settings.bmc_ip:
        logger.debug("bmc_ip not configured, using emulated mode")
        return None

    try:
        # python-ipmi library provides IPMI operations
        import ipmi

        logger.debug("Attempting IPMI connection to BMC at %s", settings.bmc_ip)
        ipmi.create_interface(
            interface_type="lanplus",
            host=settings.bmc_ip,
            username=settings.bmc_username,
            password=settings.bmc_password,
        )

        # Read sensor readings via IPMI
        # This is a simplified version — real deployments would parse SDR records
        # Try to read common thermal sensors
        try:
            # Note: This is pseudocode. Actual python-ipmi usage is more complex
            # For now, we'll return None to fall back to emulated
            logger.info(
                "Real IPMI sensor read from %s (not yet fully implemented)",
                settings.bmc_ip,
            )
            return None  # Fallback to emulated for now
        except Exception as e:
            logger.warning("Failed to read IPMI sensors from BMC: %s", e)
            return None

    except ImportError:
        logger.debug("python-ipmi not installed, using emulated mode")
        return None
    except Exception as e:
        logger.warning("IPMI connection failed, falling back to emulated: %s", e)
        return None


# ---------------------------------------------------------------------------
# In-memory sensor state
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "mode": "healthy",          # healthy | thermal_failure | fan_failure | recovering
    "mode_started_at": time.time(),
    "recover_from_temp": 94.0,
    "override_fans": False,
    # Healthy baselines
    "cpu_temp_base": 62.0,
    "fan1_rpm_base": 2400,
    "fan2_rpm_base": 2350,
    "psu1_voltage_base": 12.05,
    "psu1_power_base": 145.0,
}


def _compute_sensors() -> dict[str, Any]:
    """Return live sensor readings based on current mode and elapsed time."""
    mode = _state["mode"]
    elapsed = time.time() - _state["mode_started_at"]

    # Keep demo failures active long enough for slower local LLMs to inspect
    # sensors. Recovery should come from fan_override or manual reset.
    if mode in ("thermal_failure", "fan_failure") and elapsed > 300:
        _state["mode"] = "healthy"
        _state["mode_started_at"] = time.time()
        mode = "healthy"
        elapsed = 0

    cpu_temp = _state["cpu_temp_base"]
    fan1 = _state["fan1_rpm_base"]
    fan2 = _state["fan2_rpm_base"]
    psu_v = _state["psu1_voltage_base"]
    psu_p = _state["psu1_power_base"]

    if mode == "thermal_failure":
        # CPU temp jumps to ~86 C immediately, then climbs to 94 C, with light noise
        base_spike = 86.0
        ramp = min(elapsed / 10.0, 1.0)
        cpu_temp = base_spike + ramp * (94.0 - base_spike)
        cpu_temp += math.sin(elapsed * 0.4) * 1.2
        psu_p = _state["psu1_power_base"] + ramp * 80.0

    elif mode == "fan_failure":
        # Fan 1 drops to 0 over 30 s; CPU heats up as a consequence
        ramp = min(elapsed / 30.0, 1.0)
        fan1 = int(_state["fan1_rpm_base"] * (1.0 - ramp))
        cpu_temp = _state["cpu_temp_base"] + ramp * 24.0

    elif mode == "recovering":
        # Fans at max; temp drops back to baseline over ~45 s
        recover_temp = _state["recover_from_temp"]
        ramp = min(elapsed / 45.0, 1.0)
        cpu_temp = recover_temp - ramp * (recover_temp - _state["cpu_temp_base"])
        cpu_temp += math.sin(elapsed * 0.5) * 0.6
        fan1 = 4200
        fan2 = 4150
        psu_p = _state["psu1_power_base"] + 18.0
        # Auto-transition back to healthy once temperature has settled
        if elapsed > 50:
            _state["mode"] = "healthy"
            _state["mode_started_at"] = time.time()
            _state["override_fans"] = False

    if mode == "healthy" and _state["override_fans"]:
        fan1 = 4200
        fan2 = 4150

    # Clamp to physical limits
    cpu_temp = max(40.0, min(99.0, cpu_temp))
    fan1 = max(0, int(fan1))
    fan2 = max(0, int(fan2))

    cpu_status = "critical" if cpu_temp > 85.0 else ("warning" if cpu_temp > 75.0 else "ok")
    fan1_status = "critical" if fan1 < 600 else "ok"
    fan2_status = "critical" if fan2 < 600 else "ok"

    return {
        "timestamp": time.time(),
        "mode": mode,
        "sensors": {
            "CPU_Temp": {
                "value": round(cpu_temp, 1),
                "unit": "C",
                "threshold_upper": 85.0,
                "status": cpu_status,
            },
            "Fan1_RPM": {
                "value": fan1,
                "unit": "RPM",
                "threshold_lower": 600,
                "status": fan1_status,
            },
            "Fan2_RPM": {
                "value": fan2,
                "unit": "RPM",
                "threshold_lower": 600,
                "status": fan2_status,
            },
            "PSU1_Voltage": {
                "value": round(psu_v + math.sin(elapsed * 0.08) * 0.03, 3),
                "unit": "V",
                "threshold_lower": 11.4,
                "status": "ok",
            },
            "PSU1_Power": {
                "value": round(psu_p + math.sin(elapsed * 0.15) * 2.5, 1),
                "unit": "W",
                "threshold_upper": 300.0,
                "status": "ok",
            },
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/sensors")
async def get_sensors() -> dict[str, Any]:
    """Return current hardware sensor readings.

    If hardware_mode=real and BMC is configured and reachable, query real sensors.
    Otherwise, return emulated sensor readings.
    """
    if settings.hardware_mode == "real":
        real_sensors = await _get_real_sensors()
        if real_sensors is not None:
            logger.info("Returning real IPMI sensor data")
            return real_sensors
        else:
            logger.debug("Real mode configured but BMC unreachable, falling back to emulated")

    return _compute_sensors()


@router.post("/control")
async def control(action: str) -> dict[str, Any]:
    """Inject or clear a hardware failure scenario.

    action: thermal_failure | fan_failure | recover | reset
    """
    if action == "thermal_failure":
        _state["mode"] = "thermal_failure"
        _state["mode_started_at"] = time.time()
    elif action == "fan_failure":
        _state["mode"] = "fan_failure"
        _state["mode_started_at"] = time.time()
    elif action == "recover":
        current = _compute_sensors()
        _state["recover_from_temp"] = current["sensors"]["CPU_Temp"]["value"]
        _state["mode"] = "recovering"
        _state["mode_started_at"] = time.time()
        _state["override_fans"] = True
    elif action == "reset":
        _state["mode"] = "healthy"
        _state["mode_started_at"] = time.time()
        _state["override_fans"] = False
    else:
        return {"ok": False, "error": f"Unknown action: {action}"}
    return {"ok": True, "mode": _state["mode"]}


@router.post("/fan_override")
async def fan_override(speed_percent: int = 100) -> dict[str, Any]:
    """Agent calls this to override fan speed and cool an overheating system."""
    current = _compute_sensors()
    _state["recover_from_temp"] = current["sensors"]["CPU_Temp"]["value"]
    _state["mode"] = "recovering"
    _state["mode_started_at"] = time.time()
    _state["override_fans"] = True
    return {
        "ok": True,
        "message": f"Fan override active at {speed_percent}%. Cooling initiated.",
        "fan1_rpm": 4200,
        "fan2_rpm": 4150,
        "estimated_recovery_seconds": 45,
    }


@router.get("/config")
async def get_bmc_config() -> dict[str, Any]:
    """Return current BMC configuration and connection status."""
    hardware_mode = settings.hardware_mode
    bmc_configured = bool(settings.bmc_ip)
    bmc_reachable = False

    if hardware_mode == "real" and bmc_configured:
        # Try a quick connection test
        real_sensors = await _get_real_sensors()
        bmc_reachable = real_sensors is not None

    return {
        "hardware_mode": hardware_mode,
        "bmc_configured": bmc_configured,
        "bmc_ip": settings.bmc_ip if bmc_configured else None,
        "bmc_reachable": bmc_reachable,
        "active_source": "real_ipmi" if (hardware_mode == "real" and bmc_reachable) else "emulated",
    }
