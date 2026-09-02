"""Tests for `token_format.parse_token` -- structural validation of the
`<lookup_id>.<secret>` token format, before any store lookup or Argon2id
work happens.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle

import pytest

from cloudops_guard.ingestion.errors import TokenFormatError
from cloudops_guard.ingestion.token_format import (
    LOOKUP_ID_LENGTH,
    SECRET_LENGTH,
    TOKEN_DELIMITER,
    parse_token,
)

_VALID_LOOKUP_ID = "a" * LOOKUP_ID_LENGTH
_VALID_SECRET = "b" * SECRET_LENGTH
_VALID_TOKEN = f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}{_VALID_SECRET}"


class TestValidToken:
    def test_valid_token_splits_correctly(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        assert parsed.lookup_id == _VALID_LOOKUP_ID
        assert parsed.secret == _VALID_SECRET

    def test_delimiter_is_a_single_dot(self) -> None:
        assert TOKEN_DELIMITER == "."

    @pytest.mark.parametrize(
        "lookup_id",
        [
            "a" * LOOKUP_ID_LENGTH,
            "A" * LOOKUP_ID_LENGTH,
            "0" * LOOKUP_ID_LENGTH,
            ("a-_" * LOOKUP_ID_LENGTH)[:LOOKUP_ID_LENGTH],
        ],
    )
    def test_accepts_every_url_safe_base64_character_class(self, lookup_id: str) -> None:
        token = f"{lookup_id}{TOKEN_DELIMITER}{_VALID_SECRET}"
        parsed = parse_token(token)
        assert parsed.lookup_id == lookup_id


class TestMalformedTokensRejected:
    def test_missing_delimiter_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(_VALID_LOOKUP_ID + _VALID_SECRET)

    def test_extra_delimiter_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}extra{TOKEN_DELIMITER}{_VALID_SECRET}")

    def test_empty_lookup_id_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{TOKEN_DELIMITER}{_VALID_SECRET}")

    def test_empty_secret_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}")

    def test_both_components_empty_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(TOKEN_DELIMITER)

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token("")

    def test_lookup_id_too_short_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID[:-1]}{TOKEN_DELIMITER}{_VALID_SECRET}")

    def test_lookup_id_too_long_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}x{TOKEN_DELIMITER}{_VALID_SECRET}")

    def test_secret_too_short_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}{_VALID_SECRET[:-1]}")

    def test_secret_too_long_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}{_VALID_SECRET}x")

    def test_lookup_id_with_invalid_character_rejected(self) -> None:
        bad_lookup_id = "!" + _VALID_LOOKUP_ID[1:]
        with pytest.raises(TokenFormatError):
            parse_token(f"{bad_lookup_id}{TOKEN_DELIMITER}{_VALID_SECRET}")

    def test_secret_with_invalid_character_rejected(self) -> None:
        bad_secret = "!" + _VALID_SECRET[1:]
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}{bad_secret}")

    def test_secret_with_padding_character_rejected(self) -> None:
        # A real token never has base64 '=' padding (secrets.token_urlsafe
        # strips it) -- a presented token that has it is malformed, never
        # silently stripped back into an accepted value.
        bad_secret = "=" + _VALID_SECRET[1:]
        with pytest.raises(TokenFormatError):
            parse_token(f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}{bad_secret}")

    def test_non_string_input_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(12345)  # type: ignore[arg-type]

    def test_none_input_rejected(self) -> None:
        with pytest.raises(TokenFormatError):
            parse_token(None)  # type: ignore[arg-type]


class TestNoLeakageInErrorMessages:
    @pytest.mark.parametrize(
        "malformed_token",
        [
            "not-a-real-token-at-all",
            f"{_VALID_LOOKUP_ID}{TOKEN_DELIMITER}",
            f"{TOKEN_DELIMITER}{_VALID_SECRET}",
            f"{_VALID_LOOKUP_ID}x{TOKEN_DELIMITER}{_VALID_SECRET}",
        ],
    )
    def test_exception_message_never_contains_the_presented_value(
        self, malformed_token: str
    ) -> None:
        with pytest.raises(TokenFormatError) as excinfo:
            parse_token(malformed_token)
        assert malformed_token not in str(excinfo.value)
        assert malformed_token not in repr(excinfo.value)


class TestParsedTokenRedaction:
    def test_secret_excluded_from_repr(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        assert _VALID_SECRET not in repr(parsed)
        assert _VALID_SECRET not in str(parsed)

    def test_lookup_id_present_in_repr(self) -> None:
        # lookup_id is not secret -- no reason to redact it too.
        parsed = parse_token(_VALID_TOKEN)
        assert _VALID_LOOKUP_ID in repr(parsed)

    def test_secret_still_accessible_via_attribute(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        assert parsed.secret == _VALID_SECRET

    def test_is_immutable(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(AttributeError):
            parsed.secret = "different"  # type: ignore[misc]

    def test_has_exactly_lookup_id_and_secret_attributes(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        assert parsed.lookup_id == _VALID_LOOKUP_ID
        assert parsed.secret == _VALID_SECRET
        with pytest.raises(AttributeError):
            _ = parsed.some_other_field  # type: ignore[attr-defined]


class TestParsedTokenResistsGenericSerialization:
    """Adversarial tests: every generic-serialization pathway that would
    otherwise expose the plaintext secret must fail closed, not merely
    "not be used." A frozen `dataclasses.dataclass` with `field(repr=False)`
    (this project's earlier design) redacts `repr`/`str` correctly but
    still lets `dataclasses.asdict()` reproduce every field's real value
    by walking `__dataclass_fields__` directly, bypassing `repr` entirely
    -- `ParsedToken` is deliberately *not* a dataclass, so that pathway
    does not exist to bypass in the first place.
    """

    def test_is_not_a_dataclass(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        assert dataclasses.is_dataclass(parsed) is False

    def test_dataclasses_asdict_raises(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(TypeError):
            dataclasses.asdict(parsed)  # type: ignore[arg-type]

    def test_vars_raises(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(TypeError):
            vars(parsed)

    def test_has_no_instance_dict(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        assert not hasattr(parsed, "__dict__")

    def test_json_dumps_raises(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(TypeError):
            json.dumps(parsed)

    def test_pickle_dumps_raises(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(TypeError):
            pickle.dumps(parsed)

    def test_deepcopy_raises_rather_than_silently_succeeding(self) -> None:
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(TypeError):
            copy.deepcopy(parsed)

    def test_not_iterable_like_a_tuple_or_namedtuple(self) -> None:
        # A NamedTuple-based design would allow `tuple(parsed)` /
        # `list(parsed)` to reproduce every field, including the secret,
        # via ordinary iteration -- confirm this object supports none of
        # that.
        parsed = parse_token(_VALID_TOKEN)
        with pytest.raises(TypeError):
            iter(parsed)

    def test_no_generic_serialization_pathway_leaks_the_secret(self) -> None:
        # A single, direct proof tying the above together: none of
        # repr/str/json.dumps ever contains the secret, and every
        # structural-extraction attempt (asdict/vars/pickle/deepcopy/
        # iter) raises before it could return anything at all.
        parsed = parse_token(_VALID_TOKEN)
        assert _VALID_SECRET not in repr(parsed)
        assert _VALID_SECRET not in str(parsed)
        for attempt in (
            lambda: dataclasses.asdict(parsed),  # type: ignore[arg-type]
            lambda: vars(parsed),
            lambda: json.dumps(parsed),
            lambda: pickle.dumps(parsed),
            lambda: copy.deepcopy(parsed),
            lambda: iter(parsed),
        ):
            with pytest.raises(TypeError):
                attempt()
