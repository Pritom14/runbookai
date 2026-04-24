import time
from datetime import datetime

import requests


def simulate_high_cpu_temp():
    print("Simulating high CPU temperature sensor...")
    time.sleep(2)
    print("High CPU temperature detected!")

def post_to_webhook(url, data):
    response = requests.post(url, json=data)
    if response.status_code == 200:
        print("Webhook POST successful")
    else:
        print("Webhook POST failed")

if __name__ == "__main__":
    simulate_high_cpu_temp()
    incident_data = {
        "type": "hardware",
        "timestamp": datetime.utcnow().isoformat(),
        "details": "High CPU temperature detected"
    }
    post_to_webhook("http://localhost:8000/webhooks/hardware", incident_data)
    print("SUCCESS")
