"""Credential-field detection: which fill targets must use a secret reference.

The table pins the accepted false positives and false negatives too, so a change in
behaviour shows up as a deliberate edit to this file.
"""

from typing import Any

import pytest

from mendwork.engine.domain.credentials import detect_secret_field, tokenize
from mendwork.engine.domain.fingerprint import Fingerprint
from tests.workflows import (
    document,
    field_fingerprint,
    fill_step,
    input_ref,
    literal,
    problems,
    version,
)


def field(**overrides: object) -> Fingerprint:
    attributes = overrides.pop("attributes", {})
    return Fingerprint.model_validate(
        {
            "tag": "input",
            "structural_path": "form > input",
            "selectors": [{"strategy": "css", "value": "input"}],
            "attributes": attributes,
            **overrides,
        }
    )


SECRET_FIELDS = [
    pytest.param(
        {"attributes": {"type": "password"}}, 'its type is "password"', id="type-password"
    ),
    pytest.param({"attributes": {"type": "PASSWORD"}}, 'its type is "password"', id="type-case"),
    pytest.param(
        {"attributes": {"type": "text", "autocomplete": "current-password"}},
        'its autocomplete is "current-password"',
        id="autocomplete-current",
    ),
    pytest.param(
        {"attributes": {"type": "tel", "autocomplete": "one-time-code"}},
        'its autocomplete is "one-time-code"',
        id="autocomplete-otp",
    ),
    pytest.param(
        {"attributes": {"type": "text", "name": "userPassword"}},
        'its name contains "password"',
        id="camel-case",
    ),
    pytest.param(
        {"attributes": {"id": "login_pwd"}}, 'its id contains "pwd"', id="snake-abbreviation"
    ),
    pytest.param(
        {"attributes": {"data_testid": "api-key-input"}},
        'its data_testid contains "api key"',
        id="phrase-kebab",
    ),
    pytest.param({"attributes": {"name": "APIKey"}}, 'its name contains "apikey"', id="acronym"),
    pytest.param(
        {"attributes": {"type": "number"}, "label_text": "PIN"},
        'its label_text contains "pin"',
        id="numeric-pin",
    ),
    pytest.param(
        {"accessible_name": "One-time code"},
        'its accessible_name contains "one time code"',
        id="one-time-code",
    ),
    pytest.param(
        {"label_text": "Card security code"},
        'its label_text contains "security code"',
        id="card-code",
    ),
    pytest.param(
        {"attributes": {"placeholder": "Recovery code"}},
        'its placeholder contains "recovery code"',
        id="recovery",
    ),
    pytest.param(
        {"attributes": {"aria_label": "Passwort"}},
        'its aria_label contains "passwort"',
        id="german",
    ),
    pytest.param(
        {"label_text": "Mot de passe"}, 'its label_text contains "mot de passe"', id="french"
    ),
    pytest.param(
        {"label_text": "パスワードを入力"}, 'its label_text contains "パスワード"', id="japanese"
    ),
    pytest.param({"attributes": {"name": "cvv2"}}, 'its name contains "cvv"', id="digits-split"),
    pytest.param(
        {"attributes": {"placeholder": "••••••••"}},
        "its placeholder is masked",
        id="masked-placeholder",
    ),
    # Accepted false positives: stricter is the safe side.
    pytest.param(
        {"label_text": "PIN code"}, 'its label_text contains "pin"', id="fp-postal-pin-code"
    ),
    pytest.param(
        {"label_text": "Token amount"}, 'its label_text contains "token"', id="fp-wallet-token"
    ),
]

ORDINARY_FIELDS = [
    pytest.param({"label_text": "Passport number"}, id="passport-is-not-pass"),
    pytest.param({"label_text": "Secretary name"}, id="secretary-is-not-secret"),
    pytest.param({"label_text": "Shipping address"}, id="shipping-contains-pin"),
    pytest.param({"label_text": "Compass bearing"}, id="compass-contains-pass"),
    pytest.param(
        {"attributes": {"type": "email"}, "label_text": "Email for password reset"},
        id="email-type-wins",
    ),
    pytest.param(
        {"attributes": {"type": "date"}, "label_text": "Token expiry"}, id="date-type-wins"
    ),
    pytest.param(
        {"label_text": "Email address", "nearby_text": ["Password"]}, id="nearby-text-ignored"
    ),
    pytest.param({"attributes": {"type": "text", "name": "email"}}, id="plain-email"),
    # Accepted false negatives: invisible to a fingerprint.
    pytest.param({"attributes": {"type": "text", "id": "p"}}, id="fn-toggled-show-password"),
    pytest.param(
        {"attributes": {"type": "text"}, "label_text": "Access phrase"},
        id="fn-css-masked-neutral-label",
    ),
    pytest.param({"label_text": "Salasana"}, id="fn-unlisted-language"),
]


@pytest.mark.parametrize(("overrides", "reason"), SECRET_FIELDS)
def test_secret_fields_are_detected_with_their_reason(
    overrides: dict[str, Any], reason: str
) -> None:
    assert detect_secret_field(field(**overrides)) == reason


@pytest.mark.parametrize("overrides", ORDINARY_FIELDS)
def test_ordinary_fields_are_not_detected(overrides: dict[str, Any]) -> None:
    assert detect_secret_field(field(**overrides)) is None


@pytest.mark.parametrize(
    ("text", "tokens"),
    [
        ("currentPassword", ("current", "password")),
        ("login-pwd_2", ("login", "pwd", "2")),
        ("Mot de passe", ("mot", "de", "passe")),
        ("\uff30\uff29\uff2e", ("pin",)),
    ],
)
def test_tokenize_splits_identifiers_and_labels(text: str, tokens: tuple[str, ...]) -> None:
    assert tokenize(text) == tokens


PASSWORD_TARGET = field_fingerprint(
    accessible_name="Password",
    label_text="Password",
    attributes={"id": "password", "type": "password"},
    selectors=[{"strategy": "label", "value": "Password"}],
)


@pytest.mark.parametrize(
    ("value", "declarations", "kind"),
    [
        (literal("hunter2"), {}, "literal"),
        (input_ref("password"), {"inputs": [{"name": "password", "kind": "text"}]}, "input"),
    ],
)
def test_a_password_field_rejects_literals_and_inputs(
    value: dict[str, Any], declarations: dict[str, Any], kind: str
) -> None:
    broken = document(steps=[fill_step(target=PASSWORD_TARGET, value=value)], **declarations)

    assert problems(broken) == [
        (
            "steps[0].value",
            'the target looks like a password or secret field (its type is "password"), so its '
            f"value must be a secret reference such as {{kind: secret, name: ...}}, not a {kind} "
            "value",
        )
    ]


def test_the_error_never_echoes_the_rejected_value() -> None:
    broken = document(steps=[fill_step(target=PASSWORD_TARGET, value=literal("hunter2"))])

    assert "hunter2" not in repr(problems(broken))


def test_a_password_field_accepts_a_secret_reference() -> None:
    accepted = version(
        secrets=["account_password"],
        steps=[
            fill_step(target=PASSWORD_TARGET, value={"kind": "secret", "name": "account_password"})
        ],
    )

    assert accepted.secrets == ("account_password",)
