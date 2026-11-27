import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from .providers.amap import AmapProvider
from .schemas import Destination

router = APIRouter()


class AmapSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    js_key: str = ""
    js_security_code: SecretStr = SecretStr("")
    web_service_key: SecretStr = SecretStr("")

    @field_validator("js_key", "js_security_code", "web_service_key", mode="before")
    @classmethod
    def clean_key(cls, value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"(?:[A-Za-z0-9_-]{8,128})?", value.strip()):
            raise ValueError("Invalid Amap credential")
        return value.strip()

    def public(self, has_routing: bool) -> dict:
        return {
            "js_key": self.js_key, "routing_configured": has_routing,
            "web_service_saved": bool(self.web_service_key.get_secret_value()),
        }


class CredentialReveal(BaseModel):
    field: Literal["js_security_code", "web_service_key"]


def settings_path() -> Path | None:
    path = os.getenv("TISO_SETTINGS_FILE")
    return Path(path) if path else None


def read_settings() -> AmapSettings:
    path = settings_path()
    if path:
        try:
            return AmapSettings.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            pass
        except ValueError:
            raise RuntimeError("Invalid TISO settings file") from None
    return AmapSettings()


def routing_key(settings: AmapSettings) -> str:
    return settings.web_service_key.get_secret_value() or os.getenv("AMAP_WEB_SERVICE_KEY", "").strip()


def store_settings(settings: AmapSettings) -> None:
    path = settings_path()
    if path is None:
        return
    with NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        try:
            file.write(json.dumps({
                "js_key": settings.js_key,
                "js_security_code": settings.js_security_code.get_secret_value(),
                "web_service_key": settings.web_service_key.get_secret_value(),
            }).encode())
            file.flush()
            os.fsync(file.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def local_origin(request: Request) -> str:
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "高德本地设置仅可从本机回环地址使用")
    return f"{request.url.scheme}://{request.url.netloc}"


@router.get("/api/v1/settings/amap")
async def get_settings(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.amap_settings.public(request.app.state.jobs.amap is not None)


@router.post("/api/v1/settings/amap/reveal", include_in_schema=False)
async def reveal_credential(selection: CredentialReveal, request: Request, response: Response):
    if (request.headers.get("origin") != local_origin(request)
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        raise HTTPException(403, "请从本机 TISO 页面查看密钥")
    settings = request.app.state.amap_settings
    credential = getattr(settings, selection.field).get_secret_value()
    response.headers["Cache-Control"] = "no-store"
    return {"value": credential}


@router.post("/api/v1/settings/amap")
async def update_settings(settings: AmapSettings, request: Request, response: Response):
    if request.headers.get("origin") != local_origin(request):
        raise HTTPException(403, "请从 TISO 页面保存高德设置")
    state = request.app.state
    if state.jobs.active and not state.jobs.active.done():
        raise HTTPException(409, "请等待当前分析结束后再修改高德设置")
    previous = state.amap_settings
    if settings.js_key and not settings.js_security_code.get_secret_value() and previous.js_key == settings.js_key:
        settings.js_security_code = previous.js_security_code
    if bool(settings.js_key) != bool(settings.js_security_code.get_secret_value()):
        raise HTTPException(422, "请同时填写 Web 端 JS Key 和安全密钥")
    if "web_service_key" not in settings.model_fields_set:
        settings.web_service_key = previous.web_service_key
    try:
        store_settings(settings)
    except OSError:
        raise HTTPException(500, "无法保存高德设置") from None
    if routing_key(settings) != routing_key(previous):
        key = routing_key(settings)
        state.jobs.amap = AmapProvider(key, state.amap_client) if key else None
    state.amap_settings = settings
    response.headers["Cache-Control"] = "no-store"
    return settings.public(state.jobs.amap is not None)


@router.get("/_AMapService/{path:path}")
async def proxy_amap(path: str, request: Request):
    origin = local_origin(request)
    if (request.headers.get("sec-fetch-site") != "same-origin"
            or request.headers.get("origin", origin) != origin
            or request.headers.get("sec-fetch-mode") == "navigate"):
        raise HTTPException(403, "高德代理仅接受本页面请求")
    if not re.fullmatch(r"v[345]/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", path):
        raise HTTPException(404, "高德接口不可用")
    state = request.app.state
    settings = state.amap_settings
    security_code = settings.js_security_code.get_secret_value()
    if not settings.js_key or not security_code:
        raise HTTPException(404, "高德底图尚未配置")
    if any(key != settings.js_key for key in request.query_params.getlist("key")):
        raise HTTPException(403, "高德 JS Key 与本机配置不符")
    host = "webapi.amap.com" if path == "v4/map/styles" else "restapi.amap.com"
    params = [(name, value) for name, value in request.query_params.multi_items() if name.lower() != "jscode"]
    params.append(("jscode", security_code))
    try:
        upstream = await state.amap_client.get(
            f"https://{host}/{path}", params=params, headers={"Referer": origin + "/"}, timeout=10,
        )
    except httpx.RequestError:
        raise HTTPException(502, "无法连接高德服务") from None
    if upstream.is_redirect:
        raise HTTPException(502, "高德服务返回了重定向")
    content_type = upstream.headers.get("content-type", "application/octet-stream")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "text/html":
        raise HTTPException(502, "高德服务返回了异常响应")
    content = upstream.content.replace(security_code.encode(), b"[redacted]")
    callbacks = request.query_params.getlist("callback")
    # AMap location plugins can send script requests whose JSONP responses are labeled application/json.
    if (path in {"v3/place/text", "v3/geocode/regeo"} and media_type == "application/json" and len(callbacks) == 1
            and request.headers.get("sec-fetch-dest") == "script"):
        callback = callbacks[0].encode()
        body = content.strip().removesuffix(b";").rstrip()
        if (len(callback) <= 128 and re.fullmatch(rb"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", callback)
                and body.startswith(callback + b"(") and body.endswith(b")")):
            try:
                data = json.loads(body[len(callback) + 1:-1])
            except (ValueError, RecursionError):
                raise HTTPException(502, "高德服务返回了无效的地点搜索响应") from None
            content = callback + b"(" + json.dumps(data, ensure_ascii=True).encode() + b");"
            content_type = "application/javascript; charset=utf-8"
    return Response(content, status_code=upstream.status_code, headers={
        "Content-Type": content_type, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/api/v1/places/reverse")
async def reverse_place(point: Destination, request: Request):
    if (request.headers.get("origin") != local_origin(request)
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        raise HTTPException(403, "请从本机 TISO 页面识别地点")
    state = request.app.state
    key = routing_key(state.amap_settings)
    if not key:
        raise HTTPException(503, "请先配置高德 Web 服务 Key")
    radius = 100
    try:
        response = await state.amap_client.get(
            "https://restapi.amap.com/v3/geocode/regeo",
            params={"key": key, "location": f"{point.lng:.6f},{point.lat:.6f}",
                    "radius": radius, "extensions": "all"},
            timeout=3,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError, RecursionError):
        raise HTTPException(502, "高德地点识别暂时不可用") from None
    if not isinstance(data, dict) or data.get("status") != "1" or not isinstance(data.get("regeocode"), dict):
        raise HTTPException(502, "高德地点识别暂时不可用")
    result = data["regeocode"]
    address = result.get("formatted_address")
    name = address.strip() if isinstance(address, str) else ""
    nearest = radius + 1
    pois = result.get("pois")
    for poi in pois if isinstance(pois, list) else []:
        if not isinstance(poi, dict) or not isinstance(poi.get("name"), str) or not poi["name"].strip():
            continue
        try:
            distance = float(poi.get("distance"))
        except (TypeError, ValueError):
            continue
        if 0 <= distance <= radius and distance < nearest:
            name = poi["name"].strip()
            nearest = distance
    return {"name": name[:256]}
