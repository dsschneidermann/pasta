"""Id generation - the one impure source of identifiers.

Ids look like the reference server's: a type/kind prefix, then a token built from
a base-36 millisecond timestamp and a short random suffix, e.g.
`architecture:mqtcfkx1-a3f9`. The pure core never calls these directly; it takes
an `id_factory` argument so tests can inject a deterministic counter instead.
"""

from __future__ import annotations

import secrets
import time
from typing import Callable

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(number: int) -> str:
    if number == 0:
        return "0"
    digits: list[str] = []
    while number:
        number, remainder = divmod(number, 36)
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))


def new_token() -> str:
    """A time-ordered, collision-resistant token: `<base36-ms>-<random>`."""
    millis = int(time.time() * 1000)
    suffix = secrets.token_hex(3)  # 6 hex chars
    return f"{_base36(millis)}-{suffix}"


def new_id(prefix: str) -> str:
    """A prefixed id, e.g. `new_id("architecture") -> "architecture:mqtcfkx1-a3f9c1"`."""
    return f"{prefix}:{new_token()}"


def new_revision_token() -> str:
    """A short 6-digit optimistic-concurrency stamp for a page's status, e.g. `042917`."""
    return f"{secrets.randbelow(1_000_000):06d}"


# The type used for injected id factories in the pure core.
IdFactory = Callable[[str], str]

# The type used for injected status-revision factories in the store.
RevisionFactory = Callable[[], str]


def default_id_factory(prefix: str) -> str:
    """Prefixed id when `prefix` is non-empty (pages), else a bare token (list elements)."""
    return new_id(prefix) if prefix else new_token()


def default_revision_factory() -> str:
    return new_revision_token()
