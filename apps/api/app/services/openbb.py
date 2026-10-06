import httpx

from app.config import get_settings


async def openbb_is_ready() -> bool:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.openbb_url}/openapi.json")
            return response.is_success
    except httpx.HTTPError:
        return False


async def get_openbb_coverage() -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{settings.openbb_url}/openapi.json")
        response.raise_for_status()
        schema = response.json()

    paths = schema.get("paths", {})
    return {
        "title": schema.get("info", {}).get("title", "OpenBB API"),
        "route_count": len(paths),
        "sample_routes": sorted(paths.keys())[:12],
    }
