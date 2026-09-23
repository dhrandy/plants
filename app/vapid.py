"""Print a new VAPID key pair for browser push.

    docker run --rm ghcr.io/dhrandy/plants:latest python -m app.vapid
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64url(public), b64url(private)


if __name__ == "__main__":
    public, private = generate()
    print(f"PLANTS_VAPID_PUBLIC_KEY={public}")
    print(f"PLANTS_VAPID_PRIVATE_KEY={private}")
