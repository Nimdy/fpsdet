"""Authenticated external evidence: which registered provider key signed a record. Not whether it is true.

An external record says it comes from a provider. A signature lets fpsdet check that: the provider signs the
exact claim with its private key, and fpsdet verifies it with the public key the operator registered for that
provider. ``verified`` means one thing: the registered key signed this exact claim. It does not mean the claim
is right, and it changes no fusion rule.

The registry is operator configuration: who the operator trusts, by which keys. A record never brings its
own key. A key is ``active``, ``retired`` (it still verifies what it signed; the provider no longer uses it)
or ``revoked`` (nothing it signed is trusted, valid or not).

The algorithm is Ed25519 (RFC 8032), verified with the ``cryptography`` package. That is the one optional
dependency of fpsdet, and only signature checking needs it: ``pip install 'fpsdet[auth]'``. Without it,
everything else works, and a signature is reported as not checked.

What is signed (``fpsdet.external-signature/1``)::

    "fpsdet.external-signature/1" || 0x00 || canonical JSON of the claim

The claim is the record the provider made: an ``fpsdet.external/1`` record, or the provider's own record
that an adapter will map. Canonical JSON is sorted keys, no spaces, UTF-8 (``evidence.canonical_json``).
fpsdet verifies before it maps or normalizes anything, then reads the claim it verified.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from .evidence import canonical_json

SIGNATURE_RECIPE = "fpsdet.external-signature/1"
SIGNED_FORMAT = "fpsdet.external-signed/1"
REGISTRY_FORMAT = "fpsdet.provider-registry/1"

ALGORITHMS = ("ed25519",)
KEY_STATUSES = ("active", "retired", "revoked")

# What fpsdet can say about a record's source.
UNSIGNED = "unsigned"  # no signature: a claim, as before
NOT_CHECKED = "not_checked"  # signed, but the run had no registry to check it with
UNKNOWN_KEY = "unknown_key"  # signed with a key the registry does not have
VERIFIED = "verified"  # the registered, unrevoked key signed this exact claim
INVALID = "invalid"  # the signature does not verify, or names another provider than the claim
REVOKED_KEY = "revoked_key"  # signed with a key the operator revoked; valid or not, it is not trusted
UNSUPPORTED_ALGORITHM = "unsupported_algorithm"
STATUSES = (UNSIGNED, NOT_CHECKED, UNKNOWN_KEY, VERIFIED, INVALID, REVOKED_KEY, UNSUPPORTED_ALGORITHM)
# Never read: a record in one of these states is refused, like a malformed line.
REJECTED = frozenset({INVALID, REVOKED_KEY, UNSUPPORTED_ALGORITHM})
# Read as an unauthenticated claim under the default policy, and never as authenticated.
UNAUTHENTICATED = frozenset({UNSIGNED, NOT_CHECKED, UNKNOWN_KEY})

PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# Fields that would put private material in a registry. Refused by name, so a mistake is caught loudly.
_PRIVATE = frozenset({"private_key", "private", "secret", "seed", "secret_key", "d"})
MAX_REGISTRY_BYTES = 256 * 1024
MAX_KEYS = 1024


class AuthError(ValueError):
    """A registry or signature fpsdet will not read. The message never quotes key or signature bytes."""


class AuthUnavailable(AuthError):
    """Signature checking was asked for, and the ``cryptography`` package is not installed."""


def _ed25519():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError:
        raise AuthUnavailable("checking signatures needs the cryptography package: pip install 'fpsdet[auth]'") from None
    return Ed25519PublicKey, Ed25519PrivateKey, InvalidSignature


def _b64(text, name: str, length: int) -> bytes:
    if not isinstance(text, str):
        raise AuthError(f"{name} must be base64 text")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise AuthError(f"{name} is not base64") from None
    if len(data) != length:
        raise AuthError(f"{name} must be {length} bytes")
    return data


def signed_bytes(claim: Mapping) -> bytes:
    """The exact bytes a provider signs and fpsdet verifies: the recipe, a zero byte, the canonical claim."""
    return SIGNATURE_RECIPE.encode("ascii") + b"\0" + canonical_json(dict(claim)).encode("utf-8")


@dataclass(frozen=True)
class ProviderKey:
    provider: str
    key_id: str
    algorithm: str
    public_key: bytes
    status: str

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "public_key": base64.b64encode(self.public_key).decode("ascii"),
            "status": self.status,
        }


class Registry:
    """The operator's trusted provider keys, by (provider, key id). Public keys only."""

    def __init__(self, keys: list[ProviderKey]):
        self._keys: dict[tuple[str, str], ProviderKey] = {}
        for key in keys:
            if (key.provider, key.key_id) in self._keys:
                raise AuthError(f"{key.provider} lists key {key.key_id} twice")
            self._keys[(key.provider, key.key_id)] = key
        public_key, _private, _invalid = _ed25519()
        self._loaded = {}
        for name, key in self._keys.items():
            try:
                self._loaded[name] = public_key.from_public_bytes(key.public_key)
            except ValueError:
                raise AuthError(f"{key.provider} key {key.key_id} is not an Ed25519 public key") from None

    def get(self, provider: str, key_id: str) -> ProviderKey | None:
        return self._keys.get((provider, key_id))

    def keys(self) -> list[ProviderKey]:
        return [self._keys[name] for name in sorted(self._keys)]

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def digest(self) -> str:
        """``fpsdet.provider-registry/1``: SHA-256 over the recipe, a zero byte, and the canonical JSON of every
        key (provider, key id, algorithm, public key, status), sorted by provider and key id. Order in the file
        does not show; adding, removing or changing a key, or its status, does."""
        body = canonical_json({"keys": [key.to_dict() for key in self.keys()]})
        return "sha256:" + hashlib.sha256(REGISTRY_FORMAT.encode("ascii") + b"\0" + body.encode("utf-8")).hexdigest()

    def summary(self) -> dict:
        return {"recipe": REGISTRY_FORMAT, "digest": self.digest, "keys": len(self)}

    def _verify(self, provider: str, key_id: str, signature: bytes, message: bytes) -> bool:
        _public, _private, invalid = _ed25519()
        try:
            self._loaded[(provider, key_id)].verify(signature, message)
        except invalid:
            return False
        return True

    @classmethod
    def from_dict(cls, obj) -> Registry:
        """A registry, checked strictly: known fields only, no private material, every key well formed."""
        if not isinstance(obj, Mapping) or obj.get("format") != REGISTRY_FORMAT or set(obj) != {"format", "providers"}:
            raise AuthError(f"a registry is a {REGISTRY_FORMAT} object with format and providers only")
        providers = obj["providers"]
        if not isinstance(providers, Mapping):
            raise AuthError("providers must be an object")
        keys = []
        for provider, entry in providers.items():
            if not isinstance(provider, str) or not PROVIDER.fullmatch(provider):
                raise AuthError("a provider must be 1 to 64 lowercase letters, digits, '.', '_' or '-'")
            if not isinstance(entry, Mapping) or set(entry) != {"keys"} or not isinstance(entry["keys"], list) or not entry["keys"]:
                raise AuthError(f"{provider} must list its keys, and only its keys")
            for row in entry["keys"]:
                if not isinstance(row, Mapping):
                    raise AuthError(f"{provider} has a key that is not an object")
                if set(row) & _PRIVATE:
                    raise AuthError(f"{provider} has private key material in the registry; a registry holds public keys only")
                if set(row) != {"key_id", "algorithm", "public_key", "status"}:
                    raise AuthError(f"{provider} has a key without exactly key_id, algorithm, public_key and status")
                if not isinstance(row["key_id"], str) or not KEY_ID.fullmatch(row["key_id"]):
                    raise AuthError(f"{provider} has a key id that is not 1 to 64 letters, digits, '.', '_' or '-'")
                if row["algorithm"] not in ALGORITHMS:
                    raise AuthError(f"{provider} key {row['key_id']} uses an algorithm fpsdet does not verify")
                if row["status"] not in KEY_STATUSES:
                    raise AuthError(f"{provider} key {row['key_id']} status must be one of {', '.join(KEY_STATUSES)}")
                public_key = _b64(row["public_key"], f"{provider} key {row['key_id']} public_key", 32)
                keys.append(ProviderKey(provider, row["key_id"], row["algorithm"], public_key, row["status"]))
        if len(keys) > MAX_KEYS:
            raise AuthError(f"a registry holds at most {MAX_KEYS} keys")
        return cls(keys)


def load_registry(path) -> Registry:
    from pathlib import Path

    from .external import ExternalError, _loads

    target = Path(path)
    try:
        if target.stat().st_size > MAX_REGISTRY_BYTES:
            raise AuthError(f"{target}: a registry is at most {MAX_REGISTRY_BYTES} bytes")
        return Registry.from_dict(_loads(target.read_bytes()))
    except OSError:
        raise AuthError(f"{target}: cannot be read") from None
    except ExternalError as error:
        raise AuthError(f"{target}: {error}") from None
    except AuthUnavailable:
        raise
    except AuthError as error:
        raise AuthError(f"{target}: {error}") from None


@dataclass(frozen=True)
class Signature:
    algorithm: str
    provider: str
    key_id: str
    value: bytes

    def to_dict(self) -> dict:
        return {"algorithm": self.algorithm, "provider": self.provider, "key_id": self.key_id,
                "value": base64.b64encode(self.value).decode("ascii")}


def is_envelope(obj) -> bool:
    return isinstance(obj, Mapping) and obj.get("format") == SIGNED_FORMAT


def parse_envelope(obj) -> tuple[Mapping, Signature]:
    """A signed record, split into the claim and its signature. Only these fields, nothing else: a record
    cannot carry a key, a certificate or anything fpsdet would have to decide whether to trust."""
    if not isinstance(obj, Mapping) or set(obj) != {"format", "claim", "signature"} or obj.get("format") != SIGNED_FORMAT:
        raise AuthError(f"a signed record is a {SIGNED_FORMAT} object with format, claim and signature only")
    claim, signature = obj["claim"], obj["signature"]
    if not isinstance(claim, Mapping):
        raise AuthError("the signed claim must be an object")
    if not isinstance(signature, Mapping) or set(signature) != {"algorithm", "provider", "key_id", "value"}:
        raise AuthError("a signature is exactly algorithm, provider, key_id and value")
    algorithm = signature["algorithm"]
    if not isinstance(algorithm, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", algorithm):
        raise AuthError("the signature algorithm is malformed")
    provider, key_id = signature["provider"], signature["key_id"]
    if not isinstance(provider, str) or not PROVIDER.fullmatch(provider):
        raise AuthError("the signature's provider is malformed")
    if not isinstance(key_id, str) or not KEY_ID.fullmatch(key_id):
        raise AuthError("the signature's key id is malformed")
    return claim, Signature(algorithm, provider, key_id, _b64(signature["value"], "the signature value", 64))


@dataclass(frozen=True)
class Authentication:
    """What fpsdet could establish about who signed a record. ``verified`` means the key signed the claim."""

    status: str
    algorithm: str | None = None
    provider: str | None = None  # the provider the signature names
    key_id: str | None = None
    key_status: str | None = None  # the key's status in the registry, when it has the key
    registry: str | None = None  # the registry digest it was checked against

    @property
    def rank(self) -> int:
        """How much a duplicate of the same claim tells: a verified signature over everything else."""
        return {VERIFIED: 3, UNKNOWN_KEY: 2, NOT_CHECKED: 1}.get(self.status, 0)

    def to_dict(self) -> dict:
        out = {"status": self.status}
        for name in ("algorithm", "provider", "key_id", "key_status", "registry"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


UNSIGNED_RECORD = Authentication(UNSIGNED)


def verify(claim: Mapping, signature: Signature, provider: str, registry: Registry | None) -> Authentication:
    """Check one signature. ``provider`` is the provider the claim will be read as: the claim's own for an
    fpsdet.external/1 record, the adapter's for a mapped one. A signature that names another is invalid."""
    named = dict(algorithm=signature.algorithm, provider=signature.provider, key_id=signature.key_id)
    if signature.algorithm not in ALGORITHMS:
        return Authentication(UNSUPPORTED_ALGORITHM, **named, registry=None if registry is None else registry.digest)
    if signature.provider != provider:
        return Authentication(INVALID, **named, registry=None if registry is None else registry.digest)
    if registry is None:
        return Authentication(NOT_CHECKED, **named)
    key = registry.get(signature.provider, signature.key_id)
    if key is None:
        return Authentication(UNKNOWN_KEY, **named, registry=registry.digest)
    if key.algorithm != signature.algorithm or not registry._verify(key.provider, key.key_id, signature.value, signed_bytes(claim)):
        return Authentication(INVALID, **named, key_status=key.status, registry=registry.digest)
    if key.status == "revoked":
        return Authentication(REVOKED_KEY, **named, key_status=key.status, registry=registry.digest)
    return Authentication(VERIFIED, **named, key_status=key.status, registry=registry.digest)


def sign(claim: Mapping, private_seed: bytes, provider: str, key_id: str) -> dict:
    """For a provider, or a test: a signed record over ``claim`` with a 32-byte Ed25519 private seed. The
    seed is used and dropped; it is never in the output."""
    _public, private_key, _invalid = _ed25519()
    if not isinstance(private_seed, (bytes, bytearray)) or len(private_seed) != 32:
        raise AuthError("an Ed25519 private seed is 32 bytes")
    value = private_key.from_private_bytes(bytes(private_seed)).sign(signed_bytes(claim))
    return {"format": SIGNED_FORMAT, "claim": dict(claim), "signature": Signature("ed25519", provider, key_id, value).to_dict()}


def public_key_for(private_seed: bytes) -> str:
    """The base64 public key a registry lists for a private seed."""
    from cryptography.hazmat.primitives import serialization

    _public, private_key, _invalid = _ed25519()
    raw = private_key.from_private_bytes(bytes(private_seed)).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")
