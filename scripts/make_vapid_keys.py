"""Print a fresh pair of notification (VAPID) keys to paste into your host's settings.

  python scripts/make_vapid_keys.py

Set them as VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY. Keep the private one secret.
Without them a hosted server makes temporary keys that are lost on every restart,
and every phone that turned on reminders would stop receiving them.
"""
import sys

sys.path.insert(0, ".")
from app.services.push_service import generate_keys  # noqa: E402

k = generate_keys()
print(f"VAPID_PRIVATE_KEY={k['private']}\nVAPID_PUBLIC_KEY={k['public']}")
