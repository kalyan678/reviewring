"""Keyed pseudonyms for identifiers that leave the process.

Raw contributor IDs and business data_ids are internal join keys. Anything
exported or logged per node must use ``pseudonym`` instead: HMAC-SHA256 under a
local secret, with the identifier kind mixed in so a reviewer and a business
can never share a pseudonym, and so pseudonyms of one kind cannot be matched
against the other.

The secret is read from ``REVIEWRING_PSEUDONYM_KEY`` (environment or ``.env``).
It is never generated, defaulted, or written by this module: a missing or short
key is an error. Pseudonyms are stable only for a given key; changing the key
changes every pseudonym, and anyone holding the key can test a guessed
identifier against a pseudonym. Keep the key out of version control.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from dotenv import load_dotenv

PSEUDONYM_KEY_ENV_VAR = "REVIEWRING_PSEUDONYM_KEY"
MIN_KEY_LENGTH = 32
PSEUDONYM_HEX_LENGTH = 16

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Identifier kind -> pseudonym prefix. The kind is also the HMAC domain.
DOMAINS = {"reviewer": "R", "business": "B"}


class MissingPseudonymKeyError(Exception):
    """REVIEWRING_PSEUDONYM_KEY is not configured or is too short."""


def _check_key(key: bytes) -> None:
    if len(key) < MIN_KEY_LENGTH:
        raise MissingPseudonymKeyError(
            f"{PSEUDONYM_KEY_ENV_VAR} must be set to a secret of at least "
            f"{MIN_KEY_LENGTH} characters (see .env.example). It is never generated automatically."
        )


def load_pseudonym_key() -> bytes:
    """Return the pseudonym secret from the environment / ``.env``, or fail."""
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.environ.get(PSEUDONYM_KEY_ENV_VAR, "").strip().encode("utf-8")
    _check_key(key)
    return key


def pseudonym(domain: str, raw_id: str, key: bytes) -> str:
    """Deterministic pseudonym for ``raw_id`` within ``domain`` ("reviewer" or "business")."""
    if domain not in DOMAINS:
        # The value is not echoed: swapped arguments would put a raw ID here.
        raise ValueError(f"Unknown pseudonym domain; expected one of {sorted(DOMAINS)}")
    _check_key(key)
    message = b"reviewring/v1/" + domain.encode("ascii") + b"\x00" + raw_id.encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"{DOMAINS[domain]}_{digest[:PSEUDONYM_HEX_LENGTH]}"
