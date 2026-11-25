import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from app.providers.base import Point
from app.providers.mock import MockProvider
from scripts.verify_amap import ORIGINS, parse_results


def test_mock_cli_reports_reverse_direction_and_missing_point():
    run = subprocess.run(
        [sys.executable, "-m", "app.cli"], check=True, capture_output=True, text=True,
    )
    data = json.loads(run.stdout)
    assert data["project"] == "TISO"
    assert data["simulated"] is True
    assert data["coordinate_system"] == "GCJ-02"
    assert data["direction"] == "origins_to_destination"
    samples = {sample["point_id"]: sample for sample in data["samples"]}
    assert samples["destination"]["coordinates"] == [121.4737, 31.2304]
    assert samples["destination"]["duration_s"] == 0
    assert samples["east"]["duration_s"] > samples["west"]["duration_s"] > 0
    assert samples["north"]["status"] == "unreachable"
    assert samples["north"]["duration_s"] is None


def test_mock_distinguishes_failure_and_rejects_departure_time():
    origin = Point("failed", 121.48, 31.23)
    destination = Point("destination", 121.47, 31.23)
    provider = MockProvider(failed_ids=frozenset({origin.id}))
    result, = asyncio.run(provider.travel_time_matrix([origin], destination, "driving"))
    assert result.status == "error" and result.duration_s is None
    with pytest.raises(ValueError, match="no departure time"):
        asyncio.run(provider.travel_time_matrix(
            [origin], destination, "driving", datetime.now(UTC),
        ))


def test_amap_matches_out_of_order_results_and_reports_missing_or_invalid():
    payload = {"status": "1", "results": [
        {"origin_id": "3", "dest_id": "1", "duration": "NaN", "distance": "100"},
        {"origin_id": "1", "dest_id": "1", "duration": "30", "distance": "100"},
    ]}
    results = parse_results(payload, ORIGINS)
    assert [result["point_id"] for result in results] == [point.id for point in ORIGINS]
    assert results[0]["duration_s"] == 30
    assert results[1]["provider_error"] == "missing_result"
    assert results[2]["provider_error"] == "invalid_or_failed_result"


def test_amap_rejects_business_error():
    with pytest.raises(ValueError, match="10003"):
        parse_results({"status": "0", "infocode": "10003"}, ORIGINS)


@pytest.mark.parametrize(("args", "status"), [([], 0), (["--live"], 2)])
def test_amap_requires_live_opt_in_and_budget(args, status):
    run = subprocess.run(
        [sys.executable, "-m", "scripts.verify_amap", *args],
        capture_output=True, text=True,
    )
    assert run.returncode == status
    if not args:
        assert "No Amap requests sent" in run.stdout
    else:
        assert "requires --max-requests" in run.stderr
