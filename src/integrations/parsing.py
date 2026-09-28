import re
from typing import Any


def parse_coord(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None

    text = str(value).strip().replace(",", ".")
    try:
        numeric = float(text)
        return numeric if -180 <= numeric <= 180 else None
    except Exception:
        pass

    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    if not nums:
        return None

    sign = -1 if any(x in text.upper() for x in ("S", "W")) else 1
    if nums[0] < 0:
        sign = -1
    deg = abs(nums[0])
    minutes = nums[1] if len(nums) > 1 else 0
    seconds = nums[2] if len(nums) > 2 else 0
    numeric = sign * (deg + minutes / 60 + seconds / 3600)
    return numeric if -180 <= numeric <= 180 else None


def pick(row: dict, *names: str) -> str:
    normalized = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        value = normalized.get(name.strip().lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
