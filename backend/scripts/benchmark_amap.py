"""Run an offline driving job through the real provider using simulated HTTP responses."""

import argparse
import asyncio
import json
import math
from hashlib import sha256
from time import perf_counter

import httpx

from app.core.mainland import mainland_boundary
from app.providers.amap import AmapProvider
from app.schemas import Parameters
from app.tasks import Jobs


class MockDistanceService:
    def __init__(self, latency: float) -> None:
        self.latency = latency
        self.batch_sizes: list[int] = []
        self.origins: set[str] = set()
        self.first_request = self.last_response = 0.0
        self.in_flight = self.peak_in_flight = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/distance"
        origins = request.url.params["origins"].split("|")
        lng, lat = map(float, request.url.params["destination"].split(","))
        if not self.batch_sizes:
            self.first_request = perf_counter()
        self.batch_sizes.append(len(origins))
        self.origins.update(origins)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency)
            rows = []
            for index, origin in enumerate(origins, 1):
                x, y = map(float, origin.split(","))
                distance = math.hypot((x - lng) * 95_000, (y - lat) * 111_000)
                rows.append({"origin_id": index, "dest_id": 1, "duration": distance * 0.55, "distance": distance})
            return httpx.Response(200, json={"status": "1", "results": rows})
        finally:
            self.in_flight -= 1
            self.last_response = perf_counter()


async def run(latency: float) -> dict:
    service = MockDistanceService(latency)
    params = Parameters(
        destination={"lng": 121.4737, "lat": 31.2304}, radius_m=6000, max_duration_min=45,
    )
    mainland_boundary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
        provider = AmapProvider("offline-benchmark", client)
        jobs = Jobs(provider)
        started = perf_counter()
        job = jobs.create(params, 0)
        await job.task
        elapsed = perf_counter() - started
    if job.status != "completed" or job.result is None:
        raise RuntimeError(job.error or job.status)
    result = job.result
    http_requests = len(service.batch_sizes)
    routed_points = len(service.origins)
    submitted_points = sum(service.batch_sizes)
    routing_elapsed = service.last_response - service.first_request
    assert (http_requests, routed_points) == (result["request_count"], result["route_points"])
    comparable_samples = sorted(
        (sample["id"], sample["coordinates"], sample["status"], sample["duration_s"])
        for sample in result["samples"]
    )
    comparable_output = json.dumps([comparable_samples, result["bands"], result["contours"]], sort_keys=True)
    return {
        "batch_size": provider.capabilities().max_origins,
        "mock_latency_s": latency,
        "http_requests": http_requests,
        "unique_routed_points": routed_points,
        "submitted_points_including_retries": submitted_points,
        "batch_fill": round(submitted_points / (http_requests * provider.capabilities().max_origins), 4),
        "average_points_per_request": round(submitted_points / http_requests, 2),
        "peak_in_flight": service.peak_in_flight,
        "routing_window_s": round(routing_elapsed, 3),
        "routing_points_per_s": round(routed_points / routing_elapsed, 2),
        "end_to_end_s": round(elapsed, 3),
        "end_to_end_points_per_s": round(routed_points / elapsed, 2),
        "real_amap_quota_used": 0,
        "samples": result["sample_count"],
        "result_sha256": sha256(comparable_output.encode()).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latency", type=float, default=0.7, help="Mock HTTP latency in seconds")
    print(json.dumps(asyncio.run(run(parser.parse_args().latency)), indent=2))
