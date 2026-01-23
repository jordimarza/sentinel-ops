"""
Sentinel-Ops Alerts Module

Alert notifications via Slack and other channels.
"""

from core.alerts.slack import SlackAlerter, get_alerter, send_alert

__all__ = [
    "SlackAlerter",
    "get_alerter",
    "send_alert",
]
