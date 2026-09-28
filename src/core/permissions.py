ROLE_PERMISSIONS: dict[str, set[str]] = {
    "commander": {
        "facilities.read",
        "facilities.sync",
        "facilities.import",
        "environment.read",
        "locations.manage",
        "backup.export",
    },
    "logistics": {
        "facilities.read",
        "facilities.sync",
        "facilities.import",
        "environment.read",
        "locations.manage",
    },
    "field": {
        "facilities.read",
        "environment.read",
    },
}


def has_permission(user: dict, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(str(user.get("role") or ""), set())
