"""SSO service layer for EAOS gateway."""

from eaos.gateway.sso.service import (
    LDAPService,
    OIDCService,
    SAMLService,
    SSOConfig,
    build_saml_request_dict,
    consume_state,
    find_or_create_user,
    get_provider_by_id,
    get_provider_by_key,
    issue_state,
    list_enabled_providers,
)

__all__ = [
    "LDAPService",
    "OIDCService",
    "SAMLService",
    "SSOConfig",
    "build_saml_request_dict",
    "consume_state",
    "find_or_create_user",
    "get_provider_by_id",
    "get_provider_by_key",
    "issue_state",
    "list_enabled_providers",
]
