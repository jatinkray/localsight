"""Role-based access control.

Permissions are the atomic unit of authorization. Roles are named bundles of
permissions. Authorization is *always* enforced server-side from the verified
token's permission set — never from any client-supplied value.
"""
from __future__ import annotations

# Atomic permissions (mirror docs/security/rbac.md).
PERMISSIONS: set[str] = {
    "camera:view",
    "camera:configure",
    "video:view",
    "video:export",
    "person:view",
    "person:enroll",
    "person:delete",
    "events:view",
    "events:export",
    "system:configure",
    "audit:view",
    "user:manage",
}

# Role -> permission set.
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "ADMIN": set(PERMISSIONS),  # admin can do everything, incl. user:manage
    "SECURITY_OPERATOR": {
        "camera:view",
        "camera:configure",
        "video:view",
        "video:export",
        "person:view",
        "person:enroll",
        "person:delete",
        "events:view",
        "events:export",
        "system:configure",
        "audit:view",
    },
    "ANALYST": {
        "camera:view",
        "video:view",
        "person:view",
        "events:view",
        "events:export",
        "audit:view",
    },
    "VIEWER": {
        "camera:view",
        "video:view",
        "person:view",
        "events:view",
    },
}

VALID_ROLES = set(ROLE_PERMISSIONS)


def permissions_for_role(role: str) -> set[str]:
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    return set(ROLE_PERMISSIONS[role])


def effective_permissions(roles: list[str]) -> set[str]:
    out: set[str] = set()
    for r in roles:
        out |= permissions_for_role(r)
    return out
