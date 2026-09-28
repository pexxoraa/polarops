import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException
from js import Object, TextEncoder, Uint8Array, crypto
from pyodide.ffi import to_js as _to_js

from core.config import TOKEN_HOURS


def _js_obj(value):
    return _to_js(value, dict_converter=Object.fromEntries)


async def _pbkdf2_bytes(password: str, salt: bytes, rounds: int) -> bytes:
    encoder = TextEncoder.new()
    key = await crypto.subtle.importKey(
        "raw",
        encoder.encode(password),
        "PBKDF2",
        False,
        _to_js(["deriveBits"]),
    )
    bits = await crypto.subtle.deriveBits(
        _js_obj({
            "name": "PBKDF2",
            "salt": _to_js(salt),
            "iterations": int(rounds),
            "hash": "SHA-256",
        }),
        key,
        256,
    )
    return bytes(Uint8Array.new(bits).to_py())


async def hash_password(password: str) -> str:
    salt = bytes(crypto.getRandomValues(Uint8Array.new(16)).to_py())
    digest = await _pbkdf2_bytes(password, salt, 100_000)
    return (
        "pbkdf2_sha256$100000$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(digest).decode()
    )


async def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = await _pbkdf2_bytes(password, salt, int(rounds))
        return hmac.compare_digest(actual, expected)
    except Exception as exc:
        print(f"AUTH_VERIFY_ERROR {type(exc).__name__}: {exc}")
        return False


def _sign_payload(payload: dict, secret: str) -> str:
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
    return raw + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def _decode_signed_payload(token: str, secret: str) -> dict:
    raw, sig = token.split(".", 1)
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    if not hmac.compare_digest(sig, expected):
        raise ValueError("bad signature")
    payload = json.loads(
        base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    )
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("expired")
    return payload


def make_token(user: dict, secret: str) -> str:
    return _sign_payload({
        "uid": user["id"],
        "oid": user["organization_id"],
        "email": user["email"],
        "role": user["role"],
        "exp": int(time.time() + TOKEN_HOURS * 3600),
    }, secret)


def decode_token(token: str, secret: str) -> dict:
    try:
        return _decode_signed_payload(token, secret)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


def make_ws_ticket(user: dict, expedition_id: int, secret: str, ttl_seconds: int = 60) -> str:
    return _sign_payload({
        "uid": int(user["id"]),
        "oid": int(user["organization_id"]),
        "eid": int(expedition_id),
        "exp": int(time.time() + ttl_seconds),
        "purpose": "expedition_ws",
    }, secret)


def decode_ws_ticket(ticket: str, secret: str) -> dict:
    try:
        payload = _decode_signed_payload(ticket, secret)
        if payload.get("purpose") != "expedition_ws":
            raise ValueError("wrong purpose")
        return payload
    except Exception as exc:
        raise ValueError("Invalid or expired realtime ticket") from exc
