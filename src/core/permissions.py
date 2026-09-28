ROLE_PERMISSIONS: dict[str, set[str]] = {
    "commander": {
        "facilities.read",
        "facilities.sync",
        "facilities.import",
        "environment.read",
        "locations.manage",
        "backup.export",
        "operations.read",
        "operations.manage",
        "science.manage",
        "incident.manage",
        "audit.read",
    },
    "logistics": {
        "facilities.read",
        "facilities.sync",
        "facilities.import",
        "environment.read",
        "locations.manage",
        "operations.read",
        "operations.manage",
        "science.manage",
        "incident.manage",
        "audit.read",
    },
    "field": {
        "facilities.read",
        "environment.read",
        "operations.read",
        "science.manage",
        "audit.read",
    },
}


def has_permission(user: dict, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(str(user.get("role") or ""), set())
