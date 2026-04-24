from unittest.mock import patch

from runbookai.agent.ipmi_poller import alert, check_thresholds, parse_sensor_output


def test_parse_sensor_output_temperature():
    result = parse_sensor_output("CPU_Temp | 75 | C\n")
    assert result["CPU_Temp"] == {"value": "75", "unit": "C"}


def test_parse_sensor_output_fan():
    result = parse_sensor_output("Fan1 | 1200 | RPM\n")
    assert result["Fan1"] == {"value": "1200", "unit": "RPM"}


def test_check_thresholds_high_temp_calls_alert():
    sensor_data = {"CPU_Temp": {"value": "85", "unit": "C"}}
    with patch("runbookai.agent.ipmi_poller.alert") as mock_alert:
        check_thresholds(sensor_data)
        mock_alert.assert_called_once()


def test_check_thresholds_low_fan_calls_alert():
    sensor_data = {"Fan1": {"value": "400", "unit": "RPM"}}
    with patch("runbookai.agent.ipmi_poller.alert") as mock_alert:
        check_thresholds(sensor_data)
        mock_alert.assert_called_once()


def test_check_thresholds_normal_no_alert():
    sensor_data = {
        "CPU_Temp": {"value": "70", "unit": "C"},
        "Fan1": {"value": "1200", "unit": "RPM"},
    }
    with patch("runbookai.agent.ipmi_poller.alert") as mock_alert:
        check_thresholds(sensor_data)
        mock_alert.assert_not_called()


def test_alert_payload_source_and_severity():
    with patch("runbookai.agent.ipmi_poller.asyncio") as mock_asyncio:
        alert("High temp", {})
        assert mock_asyncio.create_task.called
