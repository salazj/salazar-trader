"""
Kalshi authentication via RSA-PSS signature.

Signs each API request with the member's RSA private key using PSS padding
with SHA-256.  The resulting signature is sent as three HTTP headers:
  KALSHI-ACCESS-KEY, KALSHI-ACCESS-TIMESTAMP, KALSHI-ACCESS-SIGNATURE

Message format:  ``<timestamp_ms><METHOD><path>``

The private key must be a PEM-encoded RSA key (2048-bit minimum, 4096
recommended).  Generate one with:

    openssl genrsa -out kalshi_private.pem 4096
    openssl rsa -in kalshi_private.pem -pubout -out kalshi_public.pem

Upload the public key to https://kalshi.com/account/api-keys.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.monitoring import get_logger

logger = get_logger(__name__)


class KalshiAuth:
    """Generates signed headers for Kalshi API requests."""

    def __init__(
        self,
        api_key: str,
        private_key_path: str = "",
        *,
        private_key_pem: str = "",
    ) -> None:
        if not api_key:
            raise ValueError("Kalshi API key must not be empty")
        self._api_key = api_key
        self._private_key = self._resolve_private_key(private_key_pem, private_key_path)
        logger.info("kalshi_auth_initialized", key_prefix=api_key[:6] + "…")

    @staticmethod
    def _resolve_private_key(
        pem_content: str, path: str
    ) -> rsa.RSAPrivateKey:
        """Load the RSA key from inline PEM content or a file path."""
        if pem_content:
            pem_content = pem_content.replace("\\n", "\n").strip()
            pem_data = pem_content.encode("utf-8")
            source = "inline env var"
        elif path:
            key_path = Path(path).expanduser().resolve()
            if not key_path.exists():
                raise FileNotFoundError(f"Kalshi private key not found at {key_path}")
            pem_data = key_path.read_bytes()
            source = str(key_path)
        else:
            raise ValueError(
                "Set KALSHI_PRIVATE_KEY (paste PEM content) or "
                "KALSHI_PRIVATE_KEY_PATH (file path) in your .env"
            )

        try:
            key = serialization.load_pem_private_key(pem_data, password=None)
        except Exception as exc:
            raise ValueError(f"Failed to parse private key from {source}: {exc}") from exc

        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError(
                f"Kalshi requires an RSA private key, got {type(key).__name__}"
            )

        key_size = key.key_size
        if key_size < 2048:
            logger.warning("kalshi_weak_rsa_key", bits=key_size)

        return key

    def sign_request(self, method: str, path: str) -> dict[str, str]:
        """Return the three auth headers required by Kalshi.

        Parameters
        ----------
        method : str
            HTTP method (GET, POST, DELETE, …) — uppercased automatically.
        path : str
            The request path (e.g. ``/trade-api/v2/portfolio/orders``).
            Should NOT include query parameters.
        """
        timestamp_ms = str(int(time.time() * 1000))
        message = timestamp_ms + method.upper() + path
        signature = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")
        return {
            "KALSHI-ACCESS-KEY": self._api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": sig_b64,
        }
