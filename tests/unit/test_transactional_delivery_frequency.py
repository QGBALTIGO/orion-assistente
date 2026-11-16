"""Transactional WhatsApp delivery latency: never delay cancellation by a 15s scan."""
from pathlib import Path


def test_appointment_notification_scan_every_five_seconds_only():
    source = (Path(__file__).resolve().parents[2]/"app"/"worker.py").read_text()
    assert "cron(scan_appointment_notifications, minute=set(range(60)), second=set(range(0,60,5)))" in source
    assert "cron(scan_appointment_notifications, minute=set(range(60)), second={0,15,30,45})" not in source
    assert "_job_id='appointment-notice:'" in (Path(__file__).resolve().parents[2]/"app"/"appointment_notifications.py").read_text()
