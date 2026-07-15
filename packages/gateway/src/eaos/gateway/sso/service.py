"""SSO/OIDC/LDAP integration service layer.

Provides:
- ``list_enabled_providers`` — public listing for login page
- ``OIDCService`` — authorization URL + code exchange + userinfo
- ``LDAPService`` — bind authentication
- ``JITProvisioner`` — find-or-create iam.users row on first SSO login
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from eaos.infra.db.base import DbClient


@dataclass(frozen=True)
class SSOConfig:
    id: UUID
    tenant_id: UUID
    name: str
    provider_type: str  # "oidc" | "saml" | "ldap"
    provider_key: str
    config: dict[str, Any]
    enabled: bool
    jit_provision: bool
    default_role: str
    created_at: Any
    updated_at: Any


def _row_to_config(row: Any) -> SSOConfig:
    cfg = row.get("config")
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return SSOConfig(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        provider_type=row["provider_type"],
        provider_key=row["provider_key"],
        config=cfg or {},
        enabled=row["enabled"],
        jit_provision=row["jit_provision"],
        default_role=row["default_role"],
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


async def list_enabled_providers(db: DbClient) -> list[SSOConfig]:
    """List all enabled SSO providers across all tenants (for login page)."""
    rows = await db.fetch(
        "SELECT id, tenant_id, name, provider_type, provider_key, config, "
        "enabled, jit_provision, default_role, created_at, updated_at "
        "FROM iam.sso_configs WHERE enabled = true ORDER BY name"
    )
    return [_row_to_config(r) for r in rows or []]


async def get_provider_by_key(db: DbClient, provider_key: str) -> SSOConfig | None:
    row = await db.fetch_one(
        "SELECT id, tenant_id, name, provider_type, provider_key, config, "
        "enabled, jit_provision, default_role, created_at, updated_at "
        "FROM iam.sso_configs WHERE provider_key = :p0 AND enabled = true",
        provider_key,
    )
    return _row_to_config(row) if row else None


async def get_provider_by_id(db: DbClient, config_id: UUID) -> SSOConfig | None:
    row = await db.fetch_one(
        "SELECT id, tenant_id, name, provider_type, provider_key, config, "
        "enabled, jit_provision, default_role, created_at, updated_at "
        "FROM iam.sso_configs WHERE id = :p0",
        config_id,
    )
    return _row_to_config(row) if row else None


# -- OIDC --------------------------------------------------------------------


class OIDCService:
    """Thin wrapper around authlib.oidc for authorization-code flow."""

    def __init__(self, config: SSOConfig, redirect_uri: str):
        self.config = config
        self.redirect_uri = redirect_uri
        cfg = config.config
        self.client_id = cfg.get("client_id", "")
        self.client_secret = cfg.get("client_secret", "")
        self.discovery_url = cfg.get("discovery_url", "")
        self.scope = cfg.get("scope", "openid email profile")
        self._server_metadata: dict[str, Any] | None = None

    async def _load_metadata(self) -> dict[str, Any]:
        if self._server_metadata is not None:
            return self._server_metadata
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.discovery_url)
            resp.raise_for_status()
            self._server_metadata = resp.json()
        return self._server_metadata

    async def get_authorization_url(self, state: str) -> str:
        metadata = await self._load_metadata()
        from authlib.integrations.base_client import OAuth2Error  # noqa: F401
        from authlib.integrations.httpx_client import AsyncOAuth2Client

        client = AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            scope=self.scope,
            redirect_uri=self.redirect_uri,
        )
        url, _ = client.create_authorization_url(
            metadata["authorization_endpoint"], state=state
        )
        return url

    async def exchange_code(self, code: str) -> dict[str, Any]:
        metadata = await self._load_metadata()
        from authlib.integrations.httpx_client import AsyncOAuth2Client

        async with AsyncOAuth2Client(
            client_id=self.client_id,
            client_secret=self.client_secret,
            scope=self.scope,
            redirect_uri=self.redirect_uri,
        ) as client:
            token = await client.fetch_token(
                metadata["token_endpoint"],
                authorization_response=f"code={code}",
                grant_type="authorization_code",
                code=code,
            )
            userinfo = await client.userinfo(metadata["userinfo_endpoint"])
        return {"token": token, "userinfo": dict(userinfo)}


# -- LDAP --------------------------------------------------------------------


class LDAPService:
    """Bind authentication against an LDAP/AD server."""

    def __init__(self, config: SSOConfig):
        self.config = config
        cfg = config.config
        self.server_url = cfg.get("server_url", "")
        self.base_dn = cfg.get("base_dn", "")
        self.bind_dn_template = cfg.get("bind_dn_template", "")
        self.search_filter = cfg.get("search_filter", "(mail={username})")
        self.use_ssl = cfg.get("use_ssl", True)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        from ldap3 import ALL, Server, Connection
        from ldap3.core.exceptions import LDAPException

        server = Server(self.server_url, use_ssl=self.use_ssl)
        # Try template-based bind first (faster).
        if self.bind_dn_template:
            bind_dn = self.bind_dn_template.replace("{username}", username)
            try:
                conn = Connection(server, user=bind_dn, password=password, auto_bind=True)
                result = {"dn": bind_dn, "username": username}
                # Optionally fetch email/name from attributes.
                if self.search_filter:
                    search = self.search_filter.replace("{username}", username)
                    conn.search(
                        self.base_dn, search, attributes=["mail", "cn", "displayName"]
                    )
                    if conn.entries:
                        entry = conn.entries[0]
                        result["email"] = str(entry.mail.value) if entry.mail else username
                        result["name"] = (
                            str(entry.cn.value) if entry.cn else username
                        )
                conn.unbind()
                return result
            except LDAPException:
                return None
        # Fallback: search-then-bind.
        try:
            # Need a service bind to search. Use admin bind creds if provided.
            admin_dn = self.config.config.get("admin_bind_dn", "")
            admin_pw = self.config.config.get("admin_bind_password", "")
            conn = Connection(server, user=admin_dn, password=admin_pw, auto_bind=True)
            search = self.search_filter.replace("{username}", username)
            conn.search(self.base_dn, search, attributes=["mail", "cn"])
            if not conn.entries:
                conn.unbind()
                return None
            user_dn = conn.entries[0].entry_dn
            email = str(conn.entries[0].mail.value) if conn.entries[0].mail else username
            name = str(conn.entries[0].cn.value) if conn.entries[0].cn else username
            conn.unbind()
            # Now bind as the user.
            user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
            user_conn.unbind()
            return {"dn": user_dn, "email": email, "name": name, "username": username}
        except LDAPException:
            return None


# -- SAML --------------------------------------------------------------------


class SAMLService:
    """SAML 2.0 Service Provider using python3-saml (OneLogin).

    Wraps AuthnRequest generation and Assertion Consumer Service (ACS)
    processing. IdP metadata is parsed from the ``idp_metadata`` config
    field (raw XML string).
    """

    def __init__(self, config: SSOConfig, sp_base_url: str, provider_key: str):
        self.config = config
        self.sp_base_url = sp_base_url.rstrip("/")
        self.provider_key = provider_key
        self._settings: dict[str, Any] | None = None

    def _build_settings(self) -> dict[str, Any]:
        """Build SAML settings dict from SSOConfig."""
        if self._settings is not None:
            return self._settings

        from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser

        idp_metadata_xml = self.config.config.get("idp_metadata", "")
        if not idp_metadata_xml:
            raise ValueError("missing idp_metadata in SAML config")

        idp_data = OneLogin_Saml2_IdPMetadataParser.parse(idp_metadata_xml)

        acs_url = f"{self.sp_base_url}/auth/sso/{self.provider_key}/acs"
        self._settings = {
            "sp": {
                "entityId": f"{self.sp_base_url}/auth/sso/metadata",
                "assertionConsumerService": {
                    "url": acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            },
            "idp": idp_data,
            "security": {
                "nameIdEncrypted": False,
                "authnRequestsSigned": False,
                "logoutRequestSigned": False,
                "logoutResponseSigned": False,
                "signMetadata": False,
                "wantMessagesSigned": True,
                "wantAssertionsSigned": True,
                "wantAssertionsEncrypted": False,
                "wantNameId": True,
                "wantNameIdEncrypted": False,
                "requestedAuthnContext": False,
                "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
                "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
            },
        }
        return self._settings

    def get_login_url(self, request_dict: dict[str, Any], state: str) -> str:
        """Generate AuthnRequest and return the IdP SSO redirect URL."""
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        settings = self._build_settings()
        auth = OneLogin_Saml2_Auth(request_dict, settings)
        return str(auth.login(return_to=state))

    def process_acs(
        self, request_dict: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Process SAML Response at the ACS endpoint.

        Returns dict with nameid, email, name on success; None on auth failure.
        """
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        settings = self._build_settings()
        auth = OneLogin_Saml2_Auth(request_dict, settings)
        auth.process_response()
        errors = auth.get_errors()
        if errors:
            return None
        if not auth.is_authenticated():
            return None

        nameid = auth.get_nameid() or ""
        attrs = auth.get_attributes() or {}
        # Try standard attribute names for email.
        email = (
            attrs.get("email", [None])[0]
            or attrs.get("mail", [None])[0]
            or attrs.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress", [None])[0]
            or nameid
        )
        name = (
            attrs.get("cn", [None])[0]
            or attrs.get("displayName", [None])[0]
            or attrs.get("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name", [None])[0]
            or email
        )
        return {"nameid": nameid, "email": email, "name": name or email}


def build_saml_request_dict(
    request: Any,
) -> dict[str, Any]:
    """Build the request dict expected by OneLogin_Saml2_Auth from a FastAPI Request."""
    scheme = "https" if request.url.scheme == "https" else "http"
    return {
        "https": "on" if scheme == "https" else "off",
        "http_host": request.url.hostname or "localhost",
        "server_port": str(request.url.port or (443 if scheme == "https" else 80)),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": dict(request.scope.get("_saml_post_data", {})),
    }


# -- JIT provisioning --------------------------------------------------------


async def find_or_create_user(
    db: DbClient,
    tenant_id: UUID,
    email: str,
    name: str,
    default_role: str,
) -> tuple[UUID, str, bool]:
    """Find user by email within tenant; create if missing. Returns (user_id, role, created)."""
    row = await db.fetch_one(
        "SELECT id, role FROM iam.users WHERE tenant_id = :p0 AND email = :p1",
        tenant_id,
        email,
    )
    if row:
        return row["id"], row["role"], False
    # JIT create.
    new_row = await db.fetch_one(
        "INSERT INTO iam.users (tenant_id, email, name, role, status) "
        "VALUES (:p0, :p1, :p2, :p3, 'active') RETURNING id, role",
        tenant_id,
        email,
        name or email,
        default_role,
    )
    if new_row is None:
        raise RuntimeError("failed to JIT create user")
    return new_row["id"], new_row["role"], True


# -- State store (Redis with in-process fallback) ---------------------------


_state_store: dict[str, tuple[UUID, str]] = {}
"""In-process fallback when Redis is unavailable (single-process only)."""

_STATE_TTL = 600  # 10 minutes
_STATE_PREFIX = "sso:state:"


async def issue_state(
    redis: Any | None, tenant_id: UUID, provider_key: str
) -> str:
    """Issue an opaque state token, stored in Redis (or in-process fallback).

    When ``redis`` is provided, the state is stored with a 10-minute TTL so it
    survives API restarts and works across multiple API workers. When
    ``redis`` is None, falls back to the in-process dict (single-process only).
    """
    state = secrets.token_urlsafe(24)
    value = f"{tenant_id}:{provider_key}"
    if redis is not None:
        await redis.set(f"{_STATE_PREFIX}{state}", value, ttl=_STATE_TTL)
    else:
        _state_store[state] = (tenant_id, provider_key)
    return state


async def consume_state(
    redis: Any | None, state: str
) -> tuple[UUID, str] | None:
    """Consume a state token (one-time use). Returns (tenant_id, provider_key)."""
    if redis is not None:
        key = f"{_STATE_PREFIX}{state}"
        raw = await redis.get(key)
        if raw is None:
            return None
        await redis.delete(key)  # one-time use
        tenant_str, provider_key = raw.split(":", 1)
        return UUID(tenant_str), provider_key
    return _state_store.pop(state, None)
