
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
import requests
from typing import Any, Dict, List

# Configure logging
logging.basicConfig(filename='demo/chaos/run.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define the ATTACKS list
ATTACKS: List[Dict[str, Any]] = [
    {
        "name": "CPU spike",
        "inject_cmd": "stress-ng --cpu 4 --timeout 30s",
        "alert_payload": {
            "alert_name": "demo-app CPU high",
            "description": "High CPU usage detected",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Process kill",
        "inject_cmd": "pkill -9 nginx",
        "alert_payload": {
            "alert_name": "demo-app service down",
            "description": "Nginx service killed",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "OOM",
        "inject_cmd": "stress-ng --vm 1 --vm-bytes 90% --timeout 20s",
        "alert_payload": {
            "alert_name": "demo-app OOM",
            "description": "Out of memory condition",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Disk fill",
        "inject_cmd": "fallocate -l 50M /var/log/nginx/junk.log",
        "alert_payload": {
            "alert_name": "demo-app disk full",
            "description": "Disk space filled up",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Hardware temp",
        "inject_cmd": "",
        "alert_payload": {
            "alert_name": "CPU Temperature",
            "description": "High CPU temperature",
            "host": "web-01",
            "severity": "critical",
            "url": "/webhooks/hardware",
            "value": "92C"
        }
    },
    {
        "name": "Network latency",
        "inject_cmd": "tc qdisc add dev eth0 root netem delay 800ms",
        "alert_payload": {
            "alert_name": "demo-app high latency",
            "description": "Network latency increased",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Log spam",
        "inject_cmd": "yes ERROR >> /var/log/nginx/error.log & sleep 60; pkill yes",
        "alert_payload": {
            "alert_name": "demo-app log error rate spike",
            "description": "Log error rate spiked",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "File handle exhaustion",
        "inject_cmd": "for i in {1..100}; do exec 200>/tmp/fd$i; done",
        "alert_payload": {
            "alert_name": "demo-app FD exhausted",
            "description": "File descriptor exhaustion",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Zombie processes",
        "inject_cmd": "for i in {1..200}; do (sleep 0; exit) & done",
        "alert_payload": {
            "alert_name": "demo-app zombie processes",
            "description": "Zombie processes detected",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "DNS broken",
        "inject_cmd": "echo 'nameserver 0.0.0.0' > /etc/resolv.conf",
        "alert_payload": {
            "alert_name": "demo-app DNS resolution failing",
            "description": "DNS resolution failing",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Slow disk I/O",
        "inject_cmd": "stress-ng --io 4 --timeout 25s",
        "alert_payload": {
            "alert_name": "demo-app disk I/O degraded",
            "description": "Disk I/O performance degraded",
            "host": "web-01",
            "severity": "critical"
        }
    },
    {
        "name": "Repeat process kill",
        "inject_cmd": "pkill -9 nginx",
        "alert_payload": {
            "alert_name": "demo-app service down",
            "description": "Nginx service killed again",
            "host": "web-01",
            "severity": "critical"
        }
    }
]

def main():
    parser = argparse.ArgumentParser(description="RunbookAI Chaos Attack Orchestrator")
    parser.add_argument('--container', default='demo-app', help='Container name (default: demo-app)')
    parser.add_argument('--only', default=None, help='Run only a specific attack')
    parser.add_argument('--loop', action='store_true', help='Restart attacks after #12')
    parser.add_argument('--shuffle', action='store_true', help='Randomize attack order with --loop')
    parser.add_argument('--duration', type=int, default=None, help='Stop after N minutes')
    parser.add_argument('--server', default='http://localhost:7000', help='RunbookAI server (default: http://localhost:7000)')
    args = parser.parse_args()

    container_name = args.container
    only_attack = args.only
    loop = args.loop
    shuffle = args.shuffle
    duration = args.duration * 60 if args.duration else None
    server_url = args.server

    attacks = ATTACKS
    if only_attack:
        attacks = [attack for attack in attacks if only_attack.lower() in attack['name'].lower()]
    if shuffle:
        import random
        random.shuffle(attacks)

    start_time = time.time()
    while True:
        for attack in attacks:
            if duration and time.time() - start_time > duration:
                break
            logging.info(f"Starting attack: {attack['name']}")
            print(f"Starting attack: {attack['name']}")
            try:
                subprocess.run(f"docker exec {container_name} sh -c '{attack['inject_cmd']}'", shell=True, check=True)
            except subprocess.CalledProcessError as e:
                logging.error(f"Failed to run attack {attack['name']}: {e}")
                print(f"Failed to run attack {attack['name']}: {e}")
            try:
                response = requests.post(f"{server_url}/webhooks/generic", json=attack['alert_payload'])
                if response.status_code != 200:
                    logging.error(f"Failed to send alert for {attack['name']}: {response.status_code} {response.text}")
                    print(f"Failed to send alert for {attack['name']}: {response.status_code} {response.text}")
            except requests.RequestException as e:
                logging.error(f"Failed to send alert for {attack['name']}: {e}")
                print(f"Failed to send alert for {attack['name']}: {e}")

            # Cleanup after attack resolution
            try:
                cleanup_cmds = [
                    "rm -f /var/log/nginx/junk.log",  # Disk fill cleanup
                    "rm -f /tmp/fd*",  # File handle cleanup
                    "echo 'nameserver 8.8.8.8' > /etc/resolv.conf",  # DNS restore
                    "tc qdisc del dev eth0 root 2>/dev/null; true",  # Remove network latency
                ]
                for cleanup_cmd in cleanup_cmds:
                    subprocess.run(f"docker exec {container_name} sh -c '{cleanup_cmd}'", shell=True, check=False)
            except Exception as e:
                logging.debug(f"Cleanup error (non-fatal): {e}")

            time.sleep(60)  # Wait for 60 seconds between attacks
        if not loop:
            break

if __name__ == "__main__":
    main()
