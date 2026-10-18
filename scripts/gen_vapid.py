"""VAPID 鍵ペアを生成して表示する (初回のみ実行)。"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(
    serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
)
print("VAPID_PUBLIC_KEY =", b64url(public))
print("VAPID_PRIVATE_KEY =", b64url(private))
