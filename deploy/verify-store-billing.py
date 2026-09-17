"""Confirm store-policy helpers and public return page on the live host."""

from wellnav.auth import is_public_path
from wellnav.billing import is_store_client

assert is_store_client("Mozilla/5.0 WellNavigation/1.0 (iOS; store)")
assert is_store_client("Mozilla/5.0 WellNavigation/1.0 (Android; store)")
assert not is_store_client("Mozilla/5.0 (iPhone) Safari/604.1")
assert is_public_path("/billing/success")
assert is_public_path("/billing/webhook")
assert not is_public_path("/billing")
print("store_helpers_ok")
