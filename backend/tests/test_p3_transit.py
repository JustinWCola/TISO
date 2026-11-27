from app.providers.amap_metro import parse_metro
from app.providers.base import Point


def test_live_probe_parser_uses_metro_and_walking_only():
    def plan(seconds, *types):
        return {"cost": {"duration": seconds}, "distance": "900", "segments": [
            {"bus": {"buslines": [{"type": mode}]}} for mode in types
        ]}

    origin = Point("x", 121.47, 31.23)
    metro = plan(600, "地铁线路")
    metro["segments"].append({"walking": {"distance": "100"}})
    options = [plan(200, "普通公交"), plan(250, "地铁线路", "普通公交"),
               {"segments": [{"railway": {"id": "train"}}]}, {"segments": [{"taxi": {"distance": "900"}}]}]
    assert parse_metro({"status": "1", "route": {"transits": options}}, origin).status == "unreachable"
    result = parse_metro({"status": "1", "route": {"transits": [*options, metro]}}, origin)
    assert (result.status, result.duration_s) == ("ok", 600)
