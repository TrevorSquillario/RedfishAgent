import os
from pathlib import Path
import pytest

from plugins.output.slack.main import SlackOutputPlugin

def test_slack_output_plugin_live(monkeypatch):
    plugin = SlackOutputPlugin()
    # Use the environment-provided webhook URL; plugin reads it in initialize()
    plugin.initialize()
    resp_text = plugin.send("integration test: hello from pytest")

    # Slack incoming webhooks return 'ok' on success
    assert resp_text == "ok"
