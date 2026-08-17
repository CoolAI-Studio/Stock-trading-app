"""Generate a VAPID key pair for Web Push notifications. Usage:

    python scripts/generate_vapid_keys.py

Paste the printed VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY into .env (locally)
or your host's environment variables (production). The public key is safe
to expose to the frontend; the private key must stay server-side only.
"""

import base64

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid02


def main() -> None:
    vapid = Vapid02()
    vapid.generate_keys()

    private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    private_b64 = base64.urlsafe_b64encode(private_raw).rstrip(b"=").decode()

    public_point = vapid.public_key.public_bytes(
        encoding=Encoding.X962, format=PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(public_point).rstrip(b"=").decode()

    print(f"VAPID_PUBLIC_KEY={public_b64}")
    print(f"VAPID_PRIVATE_KEY={private_b64}")


if __name__ == "__main__":
    main()
