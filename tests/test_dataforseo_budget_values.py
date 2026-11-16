"""Invalid arithmetic inputs must not approve spending or poison its ledger."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dataforseo_costs.py"
ENDPOINT = "serp_organic_live_advanced"


def run(config_dir, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False,
        env={**os.environ, "CLAUDE_SEO_CONFIG_DIR": str(config_dir)},
    )


def assert_error(result):
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", ["estimate", "check"])
@pytest.mark.parametrize("count", ["0", "-10"])
def test_nonpositive_call_counts_never_produce_approval(tmp_path, command, count):
    assert_error(run(tmp_path, command, ENDPOINT, f"--count={count}"))


@pytest.mark.parametrize("cost", ["nan", "inf", "-1"])
def test_invalid_logged_cost_does_not_create_or_modify_ledger(tmp_path, cost):
    assert run(tmp_path, "log", ENDPOINT, "0.25").returncode == 0
    ledger = tmp_path / "dataforseo-ledger.json"
    before = ledger.read_bytes()
    assert_error(run(tmp_path, "log", ENDPOINT, "--", cost))
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("field", ["threshold", "daily-limit"])
@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_invalid_budget_settings_are_not_written(tmp_path, field, value):
    assert_error(run(tmp_path, "config", f"--{field}={value}"))
    assert not (tmp_path / "dataforseo-costs.json").exists()


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -1, True, "1", None])
def test_invalid_existing_ledger_costs_fail_closed(tmp_path, cost):
    path = tmp_path / "dataforseo-ledger.json"
    path.write_text(json.dumps({"entries": [{
        "timestamp": datetime.now().isoformat(), "endpoint": ENDPOINT, "cost": cost,
    }]}), encoding="utf-8")
    before = path.read_bytes()
    assert_error(run(tmp_path, "check", ENDPOINT))
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["threshold", "daily_limit"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_existing_config_cannot_approve_a_call(tmp_path, field, value):
    path = tmp_path / "dataforseo-costs.json"
    path.write_text(json.dumps({field: value}), encoding="utf-8")
    before = path.read_bytes()
    assert_error(run(tmp_path, "check", ENDPOINT))
    assert path.read_bytes() == before


def test_zero_cost_and_zero_budget_remain_valid(tmp_path):
    assert run(tmp_path, "log", ENDPOINT, "0").returncode == 0
    configured = run(tmp_path, "config", "--threshold=0", "--daily-limit=0")
    assert configured.returncode == 0
    checked = run(tmp_path, "check", ENDPOINT)
    assert checked.returncode == 0
    assert json.loads(checked.stdout)["status"] == "blocked"


def test_normal_budget_arithmetic_and_approval_are_unchanged(tmp_path):
    assert run(tmp_path, "log", ENDPOINT, "0.25").returncode == 0
    checked = json.loads(run(tmp_path, "check", ENDPOINT, "--count=2").stdout)
    assert checked["status"] == "approved"
    assert checked["total_cost_usd"] == 0.004
    assert checked["today_spend_usd"] == 0.25
