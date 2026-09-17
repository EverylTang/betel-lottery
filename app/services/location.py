import json
from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class ResolvedLocation:
    province: str | None = None
    city: str | None = None
    district: str | None = None
    address: str | None = None


def _location_coordinates(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    try:
        latitude, longitude = (float(part.strip()) for part in value.split(",", 1))
    except (TypeError, ValueError):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def resolve_location(value: str | None) -> ResolvedLocation | None:
    """Reverse geocode an authorized browser coordinate without blocking a draw on failure."""
    coordinates = _location_coordinates(value)
    api_key = get_settings().tianditu_api_key.strip()
    if not coordinates or not api_key:
        return None
    latitude, longitude = coordinates
    try:
        response = httpx.get(
            "https://api.tianditu.gov.cn/geocoder",
            params={
                "postStr": json.dumps({"lon": longitude, "lat": latitude, "ver": 1}, separators=(",", ":")),
                "type": "geocode",
                "tk": api_key,
            },
            timeout=2.5,
        )
        response.raise_for_status()
        result = response.json().get("result") or {}
        component = result.get("addressComponent") or {}
    except (httpx.HTTPError, ValueError, TypeError):
        return None

    province = str(component.get("province") or "").strip() or None
    city = str(component.get("city") or "").strip() or None
    district = str(component.get("county") or component.get("district") or "").strip() or None
    address = str(result.get("formatted_address") or component.get("address") or "").strip() or None
    if not city and province and province.rstrip("市") in {"北京", "上海", "天津", "重庆"}:
        city = province
    if not any((province, city, district, address)):
        return None
    return ResolvedLocation(province=province, city=city, district=district, address=address)
