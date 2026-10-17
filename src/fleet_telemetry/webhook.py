"""Deliver alert JSON to an HTTP webhook."""

from __future__ import annotations

import json
import logging
import urllib.request

from fleet_telemetry.alerts import Alert

logger = logging.getLogger(__name__)


class HttpWebhook:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout

    def send(self, alert: Alert) -> None:
        body = json.dumps(alert.to_dict()).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            response.read()


class NullWebhook:
    def send(self, alert: Alert) -> None:
        logger.info("webhook disabled; dropped %s for %s", alert.alert_type.value, alert.robot_id)


def webhook_from_url(url: str, timeout: float = 5.0) -> HttpWebhook | NullWebhook:
    if url.strip() == "":
        return NullWebhook()
    return HttpWebhook(url, timeout=timeout)
