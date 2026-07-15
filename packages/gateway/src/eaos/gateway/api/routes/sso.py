"""SSO authentication routes — public IdP endpoints + admin config CRUD.

Public:
  - GET  /auth/sso/providers            — list enabled providers (login page)
  - GET  /auth/sso/{provider_key}/login — redirect to IdP authorization URL (OIDC/SAML)
  - GET  /auth/sso/{provider_key}/callback — OIDC code exchange + JIT + JWT
  - POST /auth/sso/{provider_key}/acs   — SAML Assertion Consumer Service
  - POST /auth/sso/ldap/login           — LDAP bind authentication

Admin:
  - GET    /admin/sso-configs
  - POST   /admin/sso-configs
  - GET    /admin/sso-configs/{id}
  - PUT    /admin/sso-configs/{id}
  - DELETE /admin/sso-configs/{id}
  - POST   /admin/sso-configs/{id}/test — validate config (no auth flow)
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID  # noqa: TC003 — Pydantic needs UUID at runtime

from eaos.core.auth import Principal, create_jwt_token  # noqa: TC002
from eaos.gateway.api.deps import get_db, get_principal, get_redis
from eaos.gateway.api.routes.admin import require_admin
from eaos.gateway.sso import (
    LDAPService,
    OIDCService,
    SAMLService,
    build_saml_request_dict,
    consume_state,
    find_or_create_user,
    get_provider_by_id,
    get_provider_by_key,
    issue_state,
    list_enabled_providers,
)
from eaos.infra.db.base import DbClient  # noqa: TC002 — runtime for FastAPI
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: TC002
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

router = APIRouter()


# -- Public: list providers --------------------------------------------------


@router.get("/auth/sso/providers", tags=["sso"])
async def list_providers(
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List enabled SSO providers for the login page."""
    providers = await list_enabled_providers(db)
    return [
        {
            "provider_key": p.provider_key,
            "name": p.name,
            "provider_type": p.provider_type,
        }
        for p in providers
    ]


# -- Public: OIDC login + callback -------------------------------------------


@router.get("/auth/sso/{provider_key}/login", tags=["sso"])
async def sso_login(
    provider_key: str,
    request: Request,
    db: DbClient = Depends(get_db),  # noqa: B008
    redis: Any = Depends(get_redis),  # noqa: B008
) -> RedirectResponse:
    """Redirect to the IdP authorization endpoint (OIDC or SAML)."""
    provider = await get_provider_by_key(db, provider_key)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"SSO provider '{provider_key}' not found")
    base_url = str(request.base_url).rstrip("/")
    state = await issue_state(redis, provider.tenant_id, provider.provider_key)

    if provider.provider_type == "oidc":
        redirect_uri = f"{base_url}/auth/sso/{provider_key}/callback"
        service = OIDCService(provider, redirect_uri)
        try:
            url = await service.get_authorization_url(state)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"failed to contact IdP: {exc}") from exc
        return RedirectResponse(url=url, status_code=302)

    if provider.provider_type == "saml":
        service = SAMLService(provider, base_url, provider_key)
        request_dict = build_saml_request_dict(request)
        try:
            url = service.get_login_url(request_dict, state)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"failed to generate SAML AuthnRequest: {exc}"
            ) from exc
        return RedirectResponse(url=url, status_code=302)

    raise HTTPException(
        status_code=400, detail=f"provider '{provider_key}' type '{provider.provider_type}' does not support login redirect"
    )


@router.get("/auth/sso/{provider_key}/callback", tags=["sso"])
async def oidc_callback(
    provider_key: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: DbClient = Depends(get_db),  # noqa: B008
    redis: Any = Depends(get_redis),  # noqa: B008
) -> dict[str, Any]:
    """Handle OIDC code exchange, JIT provisioning, and issue a JWT."""
    if error:
        raise HTTPException(status_code=400, detail=f"IdP returned error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state parameter")
    state_info = await consume_state(redis, state)
    if state_info is None:
        raise HTTPException(status_code=400, detail="invalid or expired state token")
    tenant_id, expected_key = state_info
    if expected_key != provider_key:
        raise HTTPException(status_code=400, detail="state/provider mismatch")
    provider = await get_provider_by_key(db, provider_key)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider disappeared mid-flow")
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/sso/{provider_key}/callback"
    service = OIDCService(provider, redirect_uri)
    try:
        result = await service.exchange_code(code)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"token exchange failed: {exc}") from exc
    userinfo = result.get("userinfo", {})
    email = userinfo.get("email") or userinfo.get("preferred_username") or userinfo.get("sub")
    name = userinfo.get("name") or email
    if not email:
        raise HTTPException(status_code=400, detail="IdP did not return an email claim")
    user_id, role, _created = await find_or_create_user(
        db, tenant_id, email, name, provider.default_role
    )
    token = create_jwt_token(user_id=user_id, tenant_id=tenant_id, role=role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": str(user_id), "email": email, "name": name, "role": role},
    }


# -- Public: SAML ACS --------------------------------------------------------


@router.post("/auth/sso/{provider_key}/acs", tags=["sso"])
async def saml_acs(
    provider_key: str,
    request: Request,
    db: DbClient = Depends(get_db),  # noqa: B008
    redis: Any = Depends(get_redis),  # noqa: B008
) -> dict[str, Any]:
    """Handle SAML Assertion Consumer Service (ACS).

    Receives the SAML Response from the IdP via HTTP POST binding,
    verifies the assertion, consumes the state token from RelayState,
    performs JIT provisioning, and issues a JWT.
    """
    provider = await get_provider_by_key(db, provider_key)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"SSO provider '{provider_key}' not found")
    if provider.provider_type != "saml":
        raise HTTPException(
            status_code=400,
            detail=f"provider '{provider_key}' is not SAML (got '{provider.provider_type}')",
        )

    form = await request.form()
    saml_response = form.get("SAMLResponse")
    relay_state = form.get("RelayState") or ""
    if not saml_response:
        raise HTTPException(status_code=400, detail="missing SAMLResponse in form data")

    # Consume state token (one-time use) to bind the callback to the original login.
    state_info: tuple[UUID, str] | None = None
    if relay_state:
        state_info = await consume_state(redis, relay_state)
        if state_info is None:
            raise HTTPException(status_code=400, detail="invalid or expired RelayState token")
        tenant_id, expected_key = state_info
        if expected_key != provider_key:
            raise HTTPException(status_code=400, detail="RelayState/provider mismatch")
    else:
        # No RelayState — fall back to the provider's tenant_id (less secure but
        # some IdPs don't echo RelayState back).
        tenant_id = provider.tenant_id

    base_url = str(request.base_url).rstrip("/")
    service = SAMLService(provider, base_url, provider_key)
    # Build the request dict with POST data for ACS processing.
    scheme = "https" if request.url.scheme == "https" else "http"
    request_dict: dict[str, Any] = {
        "https": "on" if scheme == "https" else "off",
        "http_host": request.url.hostname or "localhost",
        "server_port": str(request.url.port or (443 if scheme == "https" else 80)),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": {"SAMLResponse": saml_response, "RelayState": relay_state},
    }
    try:
        result = service.process_acs(request_dict)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"SAML ACS processing failed: {exc}"
        ) from exc
    if result is None:
        raise HTTPException(status_code=401, detail="SAML authentication failed")

    email = result.get("email") or result.get("nameid") or ""
    name = result.get("name") or email
    if not email:
        raise HTTPException(status_code=400, detail="SAML assertion did not contain an email claim")
    user_id, role, _created = await find_or_create_user(
        db, tenant_id, email, name, provider.default_role
    )
    token = create_jwt_token(user_id=user_id, tenant_id=tenant_id, role=role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": str(user_id), "email": email, "name": name, "role": role},
    }


# -- Public: LDAP login ------------------------------------------------------


class LDAPLoginRequest(BaseModel):
    provider_key: str
    username: str
    password: str


@router.post("/auth/sso/ldap/login", tags=["sso"])
async def ldap_login(
    body: LDAPLoginRequest,
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Authenticate against an LDAP server and issue a JWT."""
    provider = await get_provider_by_key(db, body.provider_key)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"SSO provider '{body.provider_key}' not found")
    if provider.provider_type != "ldap":
        raise HTTPException(status_code=400, detail=f"provider '{body.provider_key}' is not LDAP")
    service = LDAPService(provider)
    result = service.authenticate(body.username, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="LDAP authentication failed")
    email = result.get("email") or body.username
    name = result.get("name") or body.username
    user_id, role, _created = await find_or_create_user(
        db, provider.tenant_id, email, name, provider.default_role
    )
    token = create_jwt_token(user_id=user_id, tenant_id=provider.tenant_id, role=role)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"id": str(user_id), "email": email, "name": name, "role": role},
    }


# -- Admin: SSO config CRUD --------------------------------------------------


class SSOConfigCreateRequest(BaseModel):
    name: str
    provider_type: str  # "oidc" | "saml" | "ldap"
    provider_key: str
    config: dict[str, Any] = {}
    enabled: bool = True
    jit_provision: bool = True
    default_role: str = "employee"


class SSOConfigUpdateRequest(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    jit_provision: bool | None = None
    default_role: str | None = None


def _serialize_config(cfg: Any) -> dict[str, Any]:
    return {
        "id": str(cfg.id),
        "tenant_id": str(cfg.tenant_id),
        "name": cfg.name,
        "provider_type": cfg.provider_type,
        "provider_key": cfg.provider_key,
        "config": cfg.config,
        "enabled": cfg.enabled,
        "jit_provision": cfg.jit_provision,
        "default_role": cfg.default_role,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.get("/admin/sso-configs", tags=["admin", "sso"])
async def list_sso_configs(
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all SSO configs for the current tenant."""
    rows = await db.fetch(
        "SELECT id, tenant_id, name, provider_type, provider_key, config, "
        "enabled, jit_provision, default_role, created_at, updated_at "
        "FROM iam.sso_configs WHERE tenant_id = :p0 ORDER BY name",
        principal.tenant_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows or []:
        cfg_data = r.get("config")
        if isinstance(cfg_data, str):
            cfg_data = json.loads(cfg_data)
        out.append(
            {
                "id": str(r["id"]),
                "tenant_id": str(r["tenant_id"]),
                "name": r["name"],
                "provider_type": r["provider_type"],
                "provider_key": r["provider_key"],
                "config": cfg_data or {},
                "enabled": r["enabled"],
                "jit_provision": r["jit_provision"],
                "default_role": r["default_role"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None,
            }
        )
    return out


@router.post("/admin/sso-configs", tags=["admin", "sso"], status_code=201)
async def create_sso_config(
    body: SSOConfigCreateRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Create a new SSO provider configuration."""
    valid_types = {"oidc", "saml", "ldap"}
    if body.provider_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"invalid provider_type. Valid: {', '.join(sorted(valid_types))}",
        )
    valid_roles = {"admin", "manager", "employee", "viewer"}
    if body.default_role not in valid_roles:
        raise HTTPException(
            status_code=400,
            detail=f"invalid default_role. Valid: {', '.join(sorted(valid_roles))}",
        )
    # Check uniqueness within tenant.
    existing = await db.fetch_val(
        "SELECT count(*) FROM iam.sso_configs "
        "WHERE tenant_id = :p0 AND provider_type = :p1 AND provider_key = :p2",
        principal.tenant_id,
        body.provider_type,
        body.provider_key,
    )
    if existing and int(existing) > 0:
        raise HTTPException(
            status_code=409,
            detail=f"{body.provider_type}/{body.provider_key} already exists in this tenant",
        )
    row = await db.fetch_one(
        "INSERT INTO iam.sso_configs "
        "(tenant_id, name, provider_type, provider_key, config, enabled, "
        "jit_provision, default_role) "
        "VALUES (:p0, :p1, :p2, :p3, CAST(:p4 AS jsonb), :p5, :p6, :p7) "
        "RETURNING id, tenant_id, name, provider_type, provider_key, config, "
        "enabled, jit_provision, default_role, created_at, updated_at",
        principal.tenant_id,
        body.name,
        body.provider_type,
        body.provider_key,
        json.dumps(body.config),
        body.enabled,
        body.jit_provision,
        body.default_role,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="failed to create SSO config")
    cfg_data = row.get("config")
    if isinstance(cfg_data, str):
        cfg_data = json.loads(cfg_data)
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "name": row["name"],
        "provider_type": row["provider_type"],
        "provider_key": row["provider_key"],
        "config": cfg_data or {},
        "enabled": row["enabled"],
        "jit_provision": row["jit_provision"],
        "default_role": row["default_role"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@router.get("/admin/sso-configs/{config_id}", tags=["admin", "sso"])
async def get_sso_config(
    config_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    cfg = await get_provider_by_id(db, config_id)
    if cfg is None or cfg.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="SSO config not found")
    return _serialize_config(cfg)


@router.put("/admin/sso-configs/{config_id}", tags=["admin", "sso"])
async def update_sso_config(
    config_id: UUID,
    body: SSOConfigUpdateRequest,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    existing = await get_provider_by_id(db, config_id)
    if existing is None or existing.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="SSO config not found")
    sets: list[str] = []
    params: list[Any] = [config_id]
    if body.name is not None:
        sets.append("name = :p" + str(len(params)))
        params.append(body.name)
    if body.config is not None:
        sets.append("config = CAST(:p" + str(len(params)) + " AS jsonb)")
        params.append(json.dumps(body.config))
    if body.enabled is not None:
        sets.append("enabled = :p" + str(len(params)))
        params.append(body.enabled)
    if body.jit_provision is not None:
        sets.append("jit_provision = :p" + str(len(params)))
        params.append(body.jit_provision)
    if body.default_role is not None:
        valid_roles = {"admin", "manager", "employee", "viewer"}
        if body.default_role not in valid_roles:
            raise HTTPException(
                status_code=400,
                detail=f"invalid default_role. Valid: {', '.join(sorted(valid_roles))}",
            )
        sets.append("default_role = :p" + str(len(params)))
        params.append(body.default_role)
    if not sets:
        return _serialize_config(existing)
    sets.append("updated_at = now()")
    await db.execute(
        f"UPDATE iam.sso_configs SET {', '.join(sets)} WHERE id = :p0",
        *params,
    )
    updated = await get_provider_by_id(db, config_id)
    return _serialize_config(updated) if updated else _serialize_config(existing)


@router.delete("/admin/sso-configs/{config_id}", tags=["admin", "sso"], status_code=204)
async def delete_sso_config(
    config_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> None:
    existing = await get_provider_by_id(db, config_id)
    if existing is None or existing.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="SSO config not found")
    await db.execute("DELETE FROM iam.sso_configs WHERE id = :p0", config_id)


@router.post("/admin/sso-configs/{config_id}/test", tags=["admin", "sso"])
async def test_sso_config(
    config_id: UUID,
    principal: Principal = Depends(require_admin),  # noqa: B008
    db: DbClient = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Validate SSO config without performing a full login flow."""
    cfg = await get_provider_by_id(db, config_id)
    if cfg is None or cfg.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=404, detail="SSO config not found")
    if cfg.provider_type == "oidc":
        import httpx

        discovery = cfg.config.get("discovery_url", "")
        if not discovery:
            return {"ok": False, "detail": "missing discovery_url in config"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(discovery)
                resp.raise_for_status()
                metadata = resp.json()
            required = {"authorization_endpoint", "token_endpoint", "userinfo_endpoint"}
            missing = required - set(metadata.keys())
            if missing:
                return {"ok": False, "detail": f"IdP metadata missing: {','.join(missing)}"}
            return {
                "ok": True,
                "detail": f"OIDC discovery OK: {metadata.get('issuer', discovery)}",
            }
        except Exception as exc:
            return {"ok": False, "detail": f"discovery failed: {exc}"}
    if cfg.provider_type == "ldap":
        from ldap3 import Server
        from ldap3.core.exceptions import LDAPException

        server_url = cfg.config.get("server_url", "")
        if not server_url:
            return {"ok": False, "detail": "missing server_url in config"}
        try:
            server = Server(server_url, use_ssl=cfg.config.get("use_ssl", True))
            from ldap3 import Connection

            conn = Connection(server, auto_bind=False)
            opened = conn.open()
            if not opened:
                return {"ok": False, "detail": f"LDAP open failed: {conn.result}"}
            try:
                conn.unbind()
            except Exception:
                pass
            return {"ok": True, "detail": f"LDAP server reachable: {server_url}"}
        except LDAPException as exc:
            return {"ok": False, "detail": f"LDAP connection failed: {exc}"}
        except Exception as exc:
            return {"ok": False, "detail": f"LDAP connection error: {exc}"}
    if cfg.provider_type == "saml":
        # Validate metadata by parsing it with python3-saml (requires xmlsec1).
        metadata = cfg.config.get("idp_metadata", "")
        if not metadata:
            return {"ok": False, "detail": "missing idp_metadata in config"}
        try:
            from onelogin.saml2.idp_metadata_parser import (
                OneLogin_Saml2_IdPMetadataParser,
            )

            idp_data = OneLogin_Saml2_IdPMetadataParser.parse(metadata)
            entity_id = idp_data.get("entityId", "")
            sso_url = idp_data.get("singleSignOnService", {}).get("url", "")
            slo_url = idp_data.get("singleLogoutService", {}).get("url", "")
            if not entity_id:
                return {"ok": False, "detail": "metadata parsed but no entityId found"}
            details = [f"entityId: {entity_id}"]
            if sso_url:
                details.append(f"SSO URL: {sso_url}")
            if slo_url:
                details.append(f"SLO URL: {slo_url}")
            return {
                "ok": True,
                "detail": f"SAML metadata valid ({'; '.join(details)})",
            }
        except ImportError:
            return {
                "ok": False,
                "detail": "python3-saml not installed (run: uv pip install python3-saml)",
            }
        except Exception as exc:
            return {"ok": False, "detail": f"metadata parse failed: {exc}"}
    return {"ok": False, "detail": f"unknown provider_type: {cfg.provider_type}"}


# Re-export to silence unused import warnings.
_ = get_principal
