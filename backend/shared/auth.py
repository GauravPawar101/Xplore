"""
Clerk JWT verification - optional auth dependency for FastAPI.
If CLERK_JWKS_URL is set, verifies Bearer token and returns Clerk user id (sub).
"""

import logging
from typing import Any, Optional

from shared.config import CLERK_JWKS_URL
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger("ezdocs.auth")
_security = HTTPBearer(auto_error=False)
_jwks_client: Optional[Any] = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is not None:
        return _jwks_client
    if not CLERK_JWKS_URL:
        return None
    try:
        import jwt
        from jwt import PyJWKClient
        _jwks_client = PyJWKClient(CLERK_JWKS_URL)
        return _jwks_client
    except Exception as e:
        log.warning("Clerk JWKS client init failed: %s", e)
        return None


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> Optional[str]:
    if not CLERK_JWKS_URL:
        return None
    if not credentials or not credentials.credentials:
        return None
    client = _get_jwks_client()
    if not client:
        return None
    try:
        import jwt
        signing_key = client.get_signing_key_from_jwt(credentials.credentials)
        payload = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_iss": False},
        )
        return payload.get("sub") or None
    except Exception as e:
        log.debug("Clerk JWT verification failed: %s", e)
        return None


async def get_current_user(
    user_id: Optional[str] = Depends(get_current_user_optional),
) -> str:
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id
