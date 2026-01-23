"""
Slack Webhook Alerts

Send notifications to Slack channels for job failures and important events.
"""

import logging
from functools import lru_cache
from typing import Optional

import requests

from core.config import Settings, get_settings
from core.context import RequestContext
from core.result import JobResult, ResultStatus

logger = logging.getLogger(__name__)


class SlackAlerter:
    """
    Slack webhook alerter for job notifications.

    Usage:
        alerter = SlackAlerter(webhook_url, channel)
        alerter.alert_job_failed(ctx, "clean_old_orders", "Connection timeout")
        alerter.alert_job_completed(ctx, job_result)
    """

    def __init__(
        self,
        webhook_url: str,
        channel: str = "#sentinel-alerts",
        enabled: bool = True,
    ):
        self.webhook_url = webhook_url
        self.channel = channel
        self.enabled = enabled and bool(webhook_url)

    def _send(self, payload: dict) -> bool:
        """Send payload to Slack webhook."""
        if not self.enabled:
            logger.debug("Slack alerts disabled, skipping")
            return True

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False

    def _build_blocks(
        self,
        title: str,
        color: str,
        fields: list[dict],
        footer: Optional[str] = None,
    ) -> dict:
        """Build Slack message payload with blocks."""
        attachment = {
            "color": color,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": title, "emoji": True}
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*{f['title']}*\n{f['value']}"}
                        for f in fields
                    ]
                }
            ]
        }

        if footer:
            attachment["blocks"].append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": footer}]
            })

        return {"channel": self.channel, "attachments": [attachment]}

    def alert_job_failed(
        self,
        ctx: RequestContext,
        error: str,
        additional_info: Optional[dict] = None,
    ) -> bool:
        """
        Send alert for job failure.

        Args:
            ctx: Request context
            error: Error message
            additional_info: Optional extra data to include

        Returns:
            True if sent successfully
        """
        fields = [
            {"title": "Job", "value": ctx.job_name},
            {"title": "Error", "value": error[:200]},
            {"title": "Triggered By", "value": ctx.triggered_by},
            {"title": "Dry Run", "value": "Yes" if ctx.dry_run else "No"},
        ]

        if additional_info:
            for key, value in list(additional_info.items())[:2]:
                fields.append({"title": key, "value": str(value)[:100]})

        payload = self._build_blocks(
            title=f":x: Job Failed: {ctx.job_name}",
            color="#dc3545",  # Red
            fields=fields,
            footer=f"Request ID: {ctx.request_id}",
        )

        return self._send(payload)

    def alert_job_completed(
        self,
        ctx: RequestContext,
        result: JobResult,
    ) -> bool:
        """
        Send alert for job completion (success or partial).

        Args:
            ctx: Request context
            result: Job result

        Returns:
            True if sent successfully
        """
        # Determine status emoji and color
        if result.status == ResultStatus.SUCCESS:
            emoji = ":white_check_mark:"
            color = "#28a745"  # Green
        elif result.status == ResultStatus.PARTIAL:
            emoji = ":warning:"
            color = "#ffc107"  # Yellow
        elif result.status == ResultStatus.DRY_RUN:
            emoji = ":eyes:"
            color = "#17a2b8"  # Blue
        else:
            emoji = ":x:"
            color = "#dc3545"  # Red

        fields = [
            {"title": "Status", "value": result.status.value},
            {"title": "Records Checked", "value": str(result.records_checked)},
            {"title": "Records Updated", "value": str(result.records_updated)},
            {"title": "Errors", "value": str(len(result.errors))},
        ]

        if result.duration_seconds:
            fields.append({
                "title": "Duration",
                "value": f"{result.duration_seconds:.1f}s"
            })

        payload = self._build_blocks(
            title=f"{emoji} Job {result.status.value.title()}: {ctx.job_name}",
            color=color,
            fields=fields,
            footer=f"Request ID: {ctx.request_id}",
        )

        return self._send(payload)

    def alert_custom(
        self,
        title: str,
        message: str,
        color: str = "#17a2b8",
        fields: Optional[list[dict]] = None,
    ) -> bool:
        """
        Send a custom alert.

        Args:
            title: Alert title
            message: Alert message
            color: Attachment color (hex)
            fields: Optional list of {"title": str, "value": str}

        Returns:
            True if sent successfully
        """
        payload_fields = [{"title": "Message", "value": message}]
        if fields:
            payload_fields.extend(fields)

        payload = self._build_blocks(
            title=title,
            color=color,
            fields=payload_fields,
        )

        return self._send(payload)


class NoOpAlerter(SlackAlerter):
    """No-op alerter for testing/development."""

    def __init__(self):
        super().__init__(webhook_url="", enabled=False)
        logger.info("Using NoOp alerter (no Slack notifications)")

    def _send(self, payload: dict) -> bool:
        logger.info(f"[NOOP ALERT] {payload.get('attachments', [{}])[0].get('blocks', [{}])[0]}")
        return True


@lru_cache(maxsize=1)
def get_alerter(settings: Optional[Settings] = None) -> SlackAlerter:
    """
    Get or create a cached Slack alerter instance.

    Args:
        settings: Optional settings (uses get_settings() if not provided)

    Returns:
        SlackAlerter or NoOpAlerter if not configured
    """
    if settings is None:
        settings = get_settings()

    if not settings.slack_webhook_url:
        return NoOpAlerter()

    return SlackAlerter(
        webhook_url=settings.slack_webhook_url,
        channel=settings.slack_channel,
    )


def send_alert(
    title: str,
    message: str,
    color: str = "#17a2b8",
    fields: Optional[list[dict]] = None,
) -> bool:
    """
    Convenience function to send a custom alert.

    Args:
        title: Alert title
        message: Alert message
        color: Attachment color (hex)
        fields: Optional list of {"title": str, "value": str}

    Returns:
        True if sent successfully
    """
    alerter = get_alerter()
    return alerter.alert_custom(title, message, color, fields)
