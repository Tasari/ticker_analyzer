from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

ACCESS_CONFIG_PATH = Path(__file__).resolve().parents[1] / "site_access.json"
AUTHENTICATED_STATE_KEY = "_site_access_authenticated"
ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 310_000
MINIMUM_PASSWORD_LENGTH = 12
DISCLAIMER_ACKNOWLEDGED_STATE_KEY = "_site_access_disclaimer_acknowledged"

DISCLAIMER_TEXT = """
**Important — read before continuing**

This is a private, experimental research and analytics tool. It is not intended for public
distribution and does not provide investment, financial, legal, tax, or other professional advice.

- Scores, rankings, labels such as “Buy” or “Strong Buy”, charts, estimates, and other outputs are
  automated model results for informational and educational use. They are not personalized
  recommendations, offers, solicitations, or instructions to buy, sell, or hold any instrument.
- The application does not consider your objectives, financial situation, knowledge, experience,
  risk tolerance, investment horizon, or ability to bear losses.
- Data may be incomplete, delayed, inaccurate, or unavailable. Calculations may contain errors and
  no output is guaranteed to be correct, current, or suitable for any purpose.
- Past performance and simulated or estimated returns do not predict future results. Investing
  involves risk, including the possible loss of all invested capital.

Independently verify all information and seek appropriately licensed professional advice where
needed. You remain solely responsible for your decisions and for the consequences of using this tool.
"""


class AccessConfigError(ValueError):
    """Raised when the local site-access configuration is missing or invalid."""


@dataclass(frozen=True)
class AccessConfig:
    iterations: int
    salt: bytes
    password_hash: bytes


def create_access_config(
    password: str,
    *,
    iterations: int = DEFAULT_ITERATIONS,
    salt: bytes | None = None,
) -> dict[str, Any]:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at least {MINIMUM_PASSWORD_LENGTH} characters.")
    if iterations < 100_000:
        raise ValueError("PBKDF2 iterations must be at least 100000.")
    actual_salt = salt or os.urandom(16)
    digest = _derive_password_hash(password, actual_salt, iterations)
    return {
        "version": 1,
        "algorithm": ALGORITHM,
        "iterations": iterations,
        "salt": base64.b64encode(actual_salt).decode("ascii"),
        "password_hash": base64.b64encode(digest).decode("ascii"),
    }


def write_access_config(password: str, path: Path = ACCESS_CONFIG_PATH) -> None:
    payload = create_access_config(password)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_access_config(path: Path = ACCESS_CONFIG_PATH) -> AccessConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AccessConfigError("Site access configuration could not be loaded.") from exc
    if payload.get("version") != 1 or payload.get("algorithm") != ALGORITHM:
        raise AccessConfigError("Unsupported site access configuration.")
    try:
        iterations = int(payload["iterations"])
        salt = base64.b64decode(payload["salt"], validate=True)
        password_hash = base64.b64decode(payload["password_hash"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise AccessConfigError("Invalid site access configuration.") from exc
    if iterations < 100_000 or len(salt) < 16 or len(password_hash) != hashlib.sha256().digest_size:
        raise AccessConfigError("Unsafe site access configuration.")
    return AccessConfig(iterations=iterations, salt=salt, password_hash=password_hash)


def verify_password(password: str, config: AccessConfig) -> bool:
    candidate = _derive_password_hash(password, config.salt, config.iterations)
    return hmac.compare_digest(candidate, config.password_hash)


def render_access_gate(state: MutableMapping[str, Any] | None = None) -> bool:
    session = st.session_state if state is None else state
    if session.get(AUTHENTICATED_STATE_KEY) is True:
        return True

    st.title("Stock Analyzer")
    st.caption("This application is private. Enter the password to continue.")
    st.warning(DISCLAIMER_TEXT, icon="⚠️")
    acknowledged = st.checkbox(
        "I have read and understand the disclaimer.",
        key=DISCLAIMER_ACKNOWLEDGED_STATE_KEY,
    )
    try:
        config = load_access_config()
    except AccessConfigError:
        st.error("Site access is not configured correctly. Contact the application owner.")
        return False

    with st.form("site_access_form"):
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Unlock", disabled=not acknowledged)
    if not submitted:
        return False
    if not verify_password(password, config):
        st.error("Incorrect password.")
        return False
    session[AUTHENTICATED_STATE_KEY] = True
    st.rerun()
    return True


def render_logout_control(state: MutableMapping[str, Any] | None = None) -> None:
    session = st.session_state if state is None else state
    if st.sidebar.button("Lock app", key="lock_site_access", use_container_width=True):
        session.pop(AUTHENTICATED_STATE_KEY, None)
        st.rerun()


def render_runtime_disclaimer() -> None:
    with st.sidebar.expander("Important disclaimer"):
        st.caption(
            "Automated research tool only — not investment advice or a recommendation. "
            "Data and calculations may be incomplete or wrong. Investing can result in total loss."
        )


def _derive_password_hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
