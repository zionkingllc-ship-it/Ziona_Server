import random
import re

from core.users.models import User
from core.users.validators import (
    validate_username_format,
    validate_username_not_reserved,
    UsernameValidationError,
)


def check_username_availability(username: str) -> dict:
    """Check if a username is available for use.

    Args:
        username: The username to check.

    Returns:
        Dict with 'available' (bool) and optional 'reason' (str).
    """
    
    try:
        validate_username_format(username)
    except UsernameValidationError as e:
        return {"available": False, "reason": e.message}

   
    try:
        validate_username_not_reserved(username)
    except UsernameValidationError as e:
        return {"available": False, "reason": e.message}

   
    if User.all_objects.filter(username=username).exists():
        return {"available": False, "reason": "This username is already taken"}

    return {"available": True}


def suggest_usernames(base_name: str, count: int = 4) -> list[str]:
    """Generate available username suggestions based on a base name.

    Algorithm:
    1. Clean the base name (alphanumeric + underscores)
    2. Try variations: appended numbers, random suffixes, truncated forms
    3. Return only usernames that are actually available

    Args:
        base_name: Starting name to generate suggestions from.
        count: Number of suggestions to return (default 4).

    Returns:
        List of available username strings.
    """

    clean = re.sub(r"[^a-zA-Z0-9_]", "", base_name.lower().replace(" ", "_"))
    clean = clean[:20]  

    if not clean:
        clean = "user"

    suggestions: list[str] = []
    candidates: list[str] = []

    
    for i in range(1, 100):
        candidates.append(f"{clean}{i}")

    for _ in range(20):
        suffix = random.randint(10, 9999)
        candidates.append(f"{clean}_{suffix}")

    if len(clean) > 5:
        candidates.append(f"{clean[:5]}_{clean[5:]}")
        candidates.append(f"the_{clean}")
        candidates.append(f"{clean}_x")

    
    for candidate in candidates:
        if len(suggestions) >= count:
            break

        if len(candidate) < 3 or len(candidate) > 30:
            continue

        result = check_username_availability(candidate)
        if result["available"]:
            suggestions.append(candidate)

    return suggestions
