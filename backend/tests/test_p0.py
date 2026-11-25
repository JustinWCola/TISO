import subprocess
import sys
from datetime import UTC, datetime

import pytest

from app.providers.base import Point, validate_driving_request
from scripts.verify_amap import ORIGINS, parse_results


def test_driving_rejects_departure_time():
    with pytest.raises(ValueError, match="no departure time"):
        validate_driving_request([Point("origin", 121.48, 31.23)], "driving", datetime.now(UTC))


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
