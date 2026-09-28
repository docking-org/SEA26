import os
import ipaddress

from fastapi import HTTPException, Request

_DEFAULT_INTERNAL_SUBNETS = [
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
]


def _get_internal_subnets() -> list[ipaddress.ip_network]:
    raw = os.getenv("INTERNAL_SUBNETS")
    entries = [s.strip() for s in raw.split(",")] if raw else _DEFAULT_INTERNAL_SUBNETS
    normalized = [s if "/" in s else s + ("/128" if ":" in s else "/32") for s in entries]
    return [ipaddress.ip_network(s, strict=False) for s in normalized]


def _is_internal_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _get_internal_subnets())
    except ValueError:
        return False


def _require_internal(request: Request):
    client_ip = request.client.host if request.client else ""
    if not _is_internal_ip(client_ip):
        raise HTTPException(status_code=403, detail="Access restricted to internal network")
