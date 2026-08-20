"""Catching a broken VAPID configuration before it silently stops every alert.

Two things could be wrong here and neither announced itself:

  THE PAIR CAN DRIFT. VAPID_PUBLIC_KEY goes to the browser and is baked into
  every subscription it creates; VAPID_PRIVATE_KEY signs every push. Nothing
  ever checked they were the same key pair. Regenerate one and not the other --
  or paste the wrong half into Render -- and Apple answers 403
  VapidPkHashMismatch to every single push, forever. The app boots green, the
  health check passes, the owner sets up a channel that reports success, and
  no alert is ever delivered.

  THE SUBJECT SHIPS AS A PLACEHOLDER. render.yaml deploys
  "mailto:you@example.com" as-is. RFC 8292 requires `sub` to be a mailto: or
  https: URI, and a push service is entitled to reject anything else.

WHY THIS REFUSES TO BOOT, following the precedent _verify_encryption_key set:
a misconfiguration that only shows up on the one screen that matters, days
later, is far more expensive than a deploy that fails immediately and says
why. Refusing to start is loud; delivering nothing is silent. For this product
silence is the failure mode that must never happen.

The one deliberate exception is push being switched off entirely: an empty
VAPID pair is a valid configuration (the owner may only use Telegram or email)
and must not stop the process.
"""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.config import Settings, verify_required_secrets

REAL_SECRETS = {
    "JWT_SECRET": "a" * 50,
    "TV_WEBHOOK_SECRET": "b" * 50,
    # A real Fernet key, because _verify_encryption_key runs first and would
    # otherwise be the thing that failed -- masking whatever this file is
    # actually asserting.
    "SECRET_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pair() -> tuple[str, str]:
    """A matching pair in exactly the encoding scripts/generate_vapid_keys.py
    produces: the raw 32-byte private scalar, and the X9.62 uncompressed
    public point."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public = _b64(
        key.public_key().public_bytes(encoding=Encoding.X962, format=PublicFormat.UncompressedPoint)
    )
    return public, private


def _settings(**overrides) -> Settings:
    return Settings(**{**REAL_SECRETS, **overrides})


# --- the pair ---------------------------------------------------------------


def test_a_matching_pair_boots():
    public, private = _pair()

    verify_required_secrets(_settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY=private))


def test_a_mismatched_pair_refuses_to_start():
    """The failure this file exists for. Every push gets 403 and the owner has
    no way to see why from inside the app."""
    public, _ = _pair()
    _, other_private = _pair()

    with pytest.raises(RuntimeError) as exc:
        verify_required_secrets(_settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY=other_private))

    assert "VAPID" in str(exc.value)


def test_the_mismatch_message_says_which_two_values_disagree():
    """ "VAPID error" sends somebody to read code. Naming both variables and
    the script that regenerates them is the difference between a five-minute
    fix and an afternoon."""
    public, _ = _pair()
    _, other_private = _pair()

    with pytest.raises(RuntimeError) as exc:
        verify_required_secrets(_settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY=other_private))

    message = str(exc.value)
    assert "VAPID_PUBLIC_KEY" in message
    assert "VAPID_PRIVATE_KEY" in message
    assert "generate_vapid_keys" in message


def test_a_private_key_that_is_not_a_key_at_all_is_caught():
    public, _ = _pair()

    with pytest.raises(RuntimeError):
        verify_required_secrets(_settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY="not-a-key"))


def test_a_public_key_that_is_not_a_point_is_caught():
    _, private = _pair()

    with pytest.raises(RuntimeError):
        verify_required_secrets(_settings(VAPID_PUBLIC_KEY="nonsense", VAPID_PRIVATE_KEY=private))


def test_padded_base64url_is_accepted():
    """Some tools emit the '=' padding and some strip it. Rejecting a
    perfectly good key over padding would be a self-inflicted outage."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = base64.urlsafe_b64encode(key.private_numbers().private_value.to_bytes(32, "big"))
    public = base64.urlsafe_b64encode(
        key.public_key().public_bytes(encoding=Encoding.X962, format=PublicFormat.UncompressedPoint)
    )

    verify_required_secrets(
        _settings(VAPID_PUBLIC_KEY=public.decode(), VAPID_PRIVATE_KEY=private.decode())
    )


# --- push switched off entirely ---------------------------------------------


def test_no_vapid_keys_at_all_is_a_valid_configuration():
    """Web push is one channel of four. Somebody using only Telegram and email
    must not be prevented from starting the app."""
    verify_required_secrets(_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY=""))


def test_half_a_pair_is_refused_because_it_can_only_be_a_mistake():
    """One set and one blank is never deliberate, and it produces the same
    silent 403 as a mismatch."""
    public, _ = _pair()

    with pytest.raises(RuntimeError):
        verify_required_secrets(_settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY=""))


# --- the subject ------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    ["mailto:owner@example.com", "https://example.com/contact"],
)
def test_a_valid_subject_is_accepted(subject):
    public, private = _pair()

    verify_required_secrets(
        _settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY=private, VAPID_SUBJECT=subject)
    )


@pytest.mark.parametrize("subject", ["", "owner@example.com", "you@example.com", "not a uri"])
def test_a_subject_that_is_not_a_uri_is_refused(subject):
    """RFC 8292 requires mailto: or https:. A bare address looks right and is
    not, which is exactly the kind of thing that gets pasted in."""
    public, private = _pair()

    with pytest.raises(RuntimeError) as exc:
        verify_required_secrets(
            _settings(VAPID_PUBLIC_KEY=public, VAPID_PRIVATE_KEY=private, VAPID_SUBJECT=subject)
        )

    assert "VAPID_SUBJECT" in str(exc.value)


def test_the_shipped_placeholder_is_refused():
    """render.yaml deploys mailto:you@example.com as-is, and it is nobody's
    address. It has to fail at boot rather than at delivery."""
    public, private = _pair()

    with pytest.raises(RuntimeError):
        verify_required_secrets(
            _settings(
                VAPID_PUBLIC_KEY=public,
                VAPID_PRIVATE_KEY=private,
                VAPID_SUBJECT="mailto:you@example.com",
            )
        )


def test_the_subject_is_only_checked_when_push_is_configured():
    """No keys means no push, so the subject cannot break anything and must not
    stop the app."""
    verify_required_secrets(
        _settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="", VAPID_SUBJECT="you@example.com")
    )
