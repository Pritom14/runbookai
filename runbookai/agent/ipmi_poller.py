
import asyncio
import logging
import os
import signal
from subprocess import run, PIPE

import httpx

logger = logging.getLogger("runbookai.agent.ipmi_poller")

IPMI_POLL_INTERVAL = int(os.getenv("IPMI_POLL_INTERVAL", "60"))
IPMI_HOST = os.getenv("IPMI_HOST", "localhost")
IPMI_USER = os.getenv("IPMI_USER", "admin")
IPMI_PASSWORD = os.getenv("IPMI_PASSWORD", "password")


async def poll_ipmi_sensors():
    while True:
        try:
            result = run(
                ["ipmitool", "-I", "lanplus", "-H", IPMI_HOST, "-U", IPMI_USER, "-P", IPMI_PASSWORD, "sdr", "elist"],
                stdout=PIPE, stderr=PIPE, text=True, check=True,
            )
            sensor_data = parse_sensor_output(result.stdout)
            check_thresholds(sensor_data)
            logger.info(f"Sensor readings: {sensor_data}")
        except Exception as e:
            logger.error(f"Error polling IPMI sensors: {e}")
        await asyncio.sleep(IPMI_POLL_INTERVAL)


def parse_sensor_output(output):
    readings = {}
    for line in output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[0]:
            name = parts[0].strip()
            value = parts[1].strip()
            unit = parts[2].strip()
            readings[name] = {"value": value, "unit": unit}
    return readings


def check_thresholds(sensor_data):
    for name, data in sensor_data.items():
        if "temp" in name.lower() and data["unit"] == "C":
            try:
                if float(data["value"]) > 80:
                    alert(f"High temperature: {data['value']}C", sensor_data)
            except ValueError:
                pass
        elif "fan" in name.lower() and data["unit"] == "RPM":
            try:
                if float(data["value"]) < 500:
                    alert(f"Low fan speed: {data['value']} RPM", sensor_data)
            except ValueError:
                pass


def alert(description, sensor_data=None):
    alert_data = {
        "title": "IPMI Alert",
        "description": description,
        "source": "hardware",
        "severity": "critical",
        "metadata": sensor_data or {},
    }
    asyncio.create_task(send_alert(alert_data))


async def send_alert(alert_data):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post("http://localhost:7000/webhooks/generic", json=alert_data)
            response.raise_for_status()
            logger.info(f"Alert sent: {alert_data}")
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")


async def main():
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, loop.stop)
    loop.add_signal_handler(signal.SIGINT, loop.stop)
    await poll_ipmi_sensors()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
