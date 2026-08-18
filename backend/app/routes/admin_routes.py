import os

from fastapi import APIRouter, Request
from altcha import create_challenge_v1

from ..celery_worker import warm_target_cache
from ..utils.auth_tools import _is_internal_ip
from ..utils.sea_tools import get_target_files

router = APIRouter()


@router.get("/")
def read_root() -> dict:
    return {"msg": "Welcome to SEA", "version": 1}


@router.get("/captcha/challenge", include_in_schema=False)
def get_captcha_challenge():
    """Generate a new ALTCHA proof-of-work challenge."""
    hmac_secret = os.getenv("ALTCHA_HMAC_SECRET")
    if not hmac_secret:
        return {"disabled": True}
    return create_challenge_v1(hmac_key=hmac_secret, max_number=100000).to_dict()


@router.post("/warm_cache")
def warm_cache():
    """Trigger Redis cache warming for target files."""
    task = warm_target_cache.delay()
    return {"message": "Cache warming started", "task_id": task.id}


@router.get("/is_internal")
def is_internal(request: Request):
    """Return whether the client IP is from an internal subnet."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    return {"internal": _is_internal_ip(client_ip), "ip": client_ip, "headers": dict(request.headers)}


@router.get("/cache_info")
def cache_info():
    """Get information about the Redis cache."""
    import redis as redis_module

    broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6380/0")
    host = broker_url.split("://")[1].split(":")[0]
    port = int(broker_url.split(":")[2].split("/")[0])
    r = redis_module.Redis(host=host, port=port, db=2)

    cached_keys = r.keys("target:*")
    total_targets = len(get_target_files())
    return {
        "cached_targets": len(cached_keys),
        "total_targets": total_targets,
        "cache_hit_rate": f"{(len(cached_keys) / total_targets * 100):.1f}%" if total_targets > 0 else "N/A",
    }
