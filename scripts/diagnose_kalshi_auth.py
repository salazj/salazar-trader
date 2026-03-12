#!/usr/bin/env python3
"""Diagnose Kalshi API authentication issues step by step."""

import base64
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


def main():
    print("=" * 60)
    print("Kalshi Auth Diagnostic")
    print("=" * 60)

    # Step 1: Check env vars
    api_key = os.environ.get("KALSHI_API_KEY", "")
    private_key_raw = os.environ.get("KALSHI_PRIVATE_KEY", "")
    private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
    base_url = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
    demo_mode = os.environ.get("KALSHI_DEMO_MODE", "true").lower() == "true"

    print(f"\n[1] Environment Variables")
    print(f"  KALSHI_API_KEY:          {'SET (' + api_key[:8] + '...)' if api_key else 'MISSING'}")
    print(f"  KALSHI_PRIVATE_KEY:      {'SET (' + str(len(private_key_raw)) + ' chars)' if private_key_raw else 'MISSING'}")
    print(f"  KALSHI_PRIVATE_KEY_PATH: {private_key_path or 'EMPTY'}")
    print(f"  KALSHI_BASE_URL:         {base_url}")
    print(f"  KALSHI_DEMO_MODE:        {demo_mode}")

    if not api_key:
        print("\n  FATAL: No API key set. Cannot authenticate.")
        return

    # Step 2: Parse private key
    print(f"\n[2] Private Key Parsing")
    pem_content = ""
    if private_key_raw:
        pem_content = private_key_raw
        print(f"  Source: inline KALSHI_PRIVATE_KEY env var")
        print(f"  Raw length: {len(pem_content)} chars")
        has_literal_newline = "\\n" in pem_content
        print(f"  Contains literal backslash-n: {has_literal_newline}")
        print(f"  Starts with: {repr(pem_content[:40])}")

        # Do the same replacement as auth.py
        pem_content = pem_content.replace("\\n", "\n").strip()
        print(f"  After \\\\n -> newline replacement: {len(pem_content)} chars")
        lines = pem_content.split("\n")
        print(f"  Line count after split: {len(lines)}")
        print(f"  First line: {lines[0]}")
        print(f"  Last line:  {lines[-1]}")

        if not lines[0].startswith("-----BEGIN"):
            print("  WARNING: First line doesn't start with -----BEGIN")
        if not lines[-1].startswith("-----END"):
            print("  WARNING: Last line doesn't start with -----END")

    elif private_key_path:
        from pathlib import Path
        key_path = Path(private_key_path).expanduser().resolve()
        print(f"  Source: file at {key_path}")
        if key_path.exists():
            pem_content = key_path.read_text()
            print(f"  File exists, {len(pem_content)} chars")
        else:
            print(f"  FATAL: File not found!")
            return
    else:
        print("  FATAL: No private key configured")
        return

    # Step 3: Load the key with cryptography
    print(f"\n[3] Key Loading")
    try:
        from cryptography.hazmat.primitives import serialization
        pem_bytes = pem_content.encode("utf-8")
        key = serialization.load_pem_private_key(pem_bytes, password=None)
        print(f"  Key type: {type(key).__name__}")
        print(f"  Key size: {key.key_size} bits")

        from cryptography.hazmat.primitives.asymmetric import rsa
        if not isinstance(key, rsa.RSAPrivateKey):
            print(f"  FATAL: Not an RSA key!")
            return
        print(f"  RSA key loaded successfully")
    except Exception as e:
        print(f"  FATAL: Failed to load key: {e}")
        return

    # Step 4: Generate a test signature
    print(f"\n[4] Signature Generation")
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    timestamp_ms = str(int(time.time() * 1000))
    method = "GET"
    path = "/trade-api/v2/portfolio/balance"
    message = timestamp_ms + method + path

    print(f"  Timestamp: {timestamp_ms}")
    print(f"  Message:   {message[:60]}...")

    try:
        signature = key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        sig_b64 = base64.b64encode(signature).decode("utf-8")
        print(f"  Signature: {sig_b64[:40]}... ({len(sig_b64)} chars)")
        print(f"  Signature generation: OK")
    except Exception as e:
        print(f"  FATAL: Signing failed: {e}")
        return

    # Step 5: Make a real HTTP request
    print(f"\n[5] Live API Request")
    effective_base = "https://demo-api.kalshi.co/trade-api/v2" if demo_mode else base_url
    print(f"  Base URL: {effective_base}")

    import httpx

    def make_signed_request(k, api_k, target_base, endpoint, query=""):
        ts = str(int(time.time() * 1000))
        sp = "/trade-api/v2" + endpoint
        m = ts + "GET" + sp
        s = k.sign(
            m.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        h = {
            "KALSHI-ACCESS-KEY": api_k,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(s).decode("utf-8"),
            "Accept": "application/json",
        }
        full_url = target_base + endpoint + (("?" + query) if query else "")
        print(f"  Request: GET {full_url}")
        print(f"  Sign path: {sp}")
        print(f"  Headers: KEY={api_k[:12]}... TS={ts}")
        r = httpx.get(full_url, headers=h, timeout=15)
        print(f"  Status:  {r.status_code}")
        print(f"  Body:    {r.text[:300]}")
        return r.status_code

    # Test 1: GET /portfolio/balance
    print(f"\n  --- Test 1: /portfolio/balance ---")
    try:
        make_signed_request(key, api_key, effective_base, "/portfolio/balance")
    except Exception as e:
        print(f"  ERROR: {e}")

    # Test 2: GET /markets (the failing endpoint)
    print(f"\n  --- Test 2: /markets?limit=100&status=open ---")
    try:
        make_signed_request(key, api_key, effective_base, "/markets", "limit=100&status=open")
    except Exception as e:
        print(f"  ERROR: {e}")

    # Test 3: Try the OTHER production URL
    if not demo_mode:
        alt_base = "https://api.elections.kalshi.com/trade-api/v2"
        print(f"\n  --- Test 3: Alternate URL {alt_base} ---")
        try:
            make_signed_request(key, api_key, alt_base, "/markets", "limit=2&status=open")
        except Exception as e:
            print(f"  ERROR: {e}")

    # Test 4: Try with the .pem file directly (if it exists)
    pem_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kalshi_private.pem")
    if os.path.exists(pem_file):
        print(f"\n  --- Test 4: kalshi_private.pem file ---")
        try:
            file_key = serialization.load_pem_private_key(
                open(pem_file, "rb").read(), password=None
            )
            print(f"  File key type: {type(file_key).__name__}, size: {file_key.key_size}")

            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            pub1 = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            pub2 = file_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            keys_match = pub1 == pub2
            print(f"  Keys match inline .env: {keys_match}")

            if not keys_match:
                print(f"  WARNING: .pem file has DIFFERENT key than .env inline!")
                print(f"  Trying request with FILE key instead...")
                make_signed_request(file_key, api_key, effective_base, "/markets", "limit=2&status=open")
        except Exception as e:
            print(f"  Error with .pem file: {e}")

    print(f"\n{'=' * 60}")
    print("Diagnostic complete.")


if __name__ == "__main__":
    main()
