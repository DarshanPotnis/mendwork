"""Naming inputs and secrets after recording, and assembling the workflow from the decisions."""

from datetime import UTC, datetime

import pytest

from mendwork.engine.domain.enums import ActionType, InputKind, RiskLevel, ValueKind
from mendwork.engine.domain.fingerprint import Fingerprint, FingerprintAttributes
from mendwork.engine.domain.identifiers import SLUG_DESCRIPTION, SecretName
from mendwork.engine.domain.recording import (
    DraftStep,
    IgnoredReason,
    InputHint,
    InteractionIgnored,
    LiteralDraft,
    Recording,
    SecretDraft,
)
from mendwork.engine.domain.steps import FillStep, NavigateStep
from mendwork.engine.domain.values import InputValue, LiteralValue, SecretValue
from mendwork.engine.errors import RecordingUnusable
from mendwork.engine.recording.assembly import assemble
from mendwork.engine.recording.naming import (
    Decisions,
    NamingSession,
    default_secret_name,
    input_hint,
)
from tests.fakes.clock import FakeClock
from tests.unit.recording.builders import START_URL, by_test_id

CLOCK = FakeClock(datetime(2026, 9, 12, 10, 30, 15, 123456, tzinfo=UTC))


def field(name: str | None = None, *, label: str | None = None, **attributes: str) -> Fingerprint:
    return Fingerprint(
        tag="input",
        accessible_name=label,
        label_text=label,
        attributes=FingerprintAttributes(name=name, **attributes),
        structural_path="main > form > input",
        selectors=(by_test_id(name or "field"),),
    )


def navigate(
    index: int = 0, url: str = START_URL, hint: InputHint | None = InputHint.START_URL
) -> DraftStep:
    return DraftStep(
        index=index,
        step_id=f"open_{index}",
        action=ActionType.NAVIGATE,
        description=f"NAVIGATE to {url}",
        intent=f"Open {url}",
        risk=RiskLevel.SAFE,
        value=LiteralDraft(value=url, hint=hint),
    )


def fill(index: int, target: Fingerprint, value: LiteralDraft | SecretDraft) -> DraftStep:
    return DraftStep(
        index=index,
        step_id=f"fill_{index}",
        action=ActionType.FILL,
        description="FILL a field",
        intent="Fill a field",
        risk=RiskLevel.CAUTION,
        target=target,
        value=value,
    )


def secret(index: int, name: str = "password") -> DraftStep:
    return fill(
        index,
        field("password", label="Password", type="password"),
        SecretDraft(proposed_name=SecretName(name), reason='its type is "password"'),
    )


def email(index: int, value: str = "ada@example.test") -> DraftStep:
    return fill(
        index,
        field("email", label="Email address", type="email"),
        LiteralDraft(value=value, hint=InputHint.EMAIL),
    )


def test_input_hints_come_from_the_value_or_the_field() -> None:
    assert input_hint(None, " ada@example.test ") is InputHint.EMAIL
    assert input_hint(field(autocomplete="username"), "ada") is InputHint.USERNAME
    assert input_hint(field("userName"), "ada") is InputHint.USERNAME
    assert input_hint(field("city"), "Lisbon") is None
    assert input_hint(None, "Lisbon") is None


def test_default_secret_names_come_from_the_field() -> None:
    assert default_secret_name(field("current-password")) == "current_password"
    assert default_secret_name(field(label="PIN code")) == "pin_code"
    assert default_secret_name(field(label="パスワード")) == "password"
    assert default_secret_name(field("2fa")) == "password_2_fa"


def test_proposals_cover_the_start_url_emails_and_secrets() -> None:
    session = NamingSession([navigate(), email(1), secret(2), email(3)], [])

    assert [(p.default_name, p.kind, p.steps) for p in session.input_proposals] == [
        ("start_url", InputKind.URL, (0,)),
        ("email", InputKind.TEXT, (1, 3)),
    ]
    assert "start URL" in session.input_proposals[0].prompt
    assert "looks like an email" in session.input_proposals[1].prompt
    assert "ada@" not in session.input_proposals[1].prompt
    assert [p.default_description for p in session.input_proposals] == [
        "URL of the page the workflow starts on (recorded at /index.html)",
        "Email address typed into the 'Email address' field",
    ]
    assert session.suggested_input_answer(session.input_proposals[0]) == (
        "start_url: URL of the page the workflow starts on (recorded at /index.html)"
    )
    assert [(p.step, p.default_name) for p in session.secret_proposals] == [(2, "password")]


def test_defaults_are_accepted_when_nobody_answers() -> None:
    session = NamingSession([navigate(), email(1), secret(2)], [])

    decisions = session.decisions()

    assert [(d.name, d.kind, d.value, d.steps, d.description) for d in decisions.inputs] == [
        (
            "start_url",
            InputKind.URL,
            START_URL,
            (0,),
            "URL of the page the workflow starts on (recorded at /index.html)",
        ),
        (
            "email",
            InputKind.TEXT,
            "ada@example.test",
            (1,),
            "Email address typed into the 'Email address' field",
        ),
    ]
    assert [(d.name, d.step) for d in decisions.secrets] == [("password", 2)]


def test_an_answer_may_add_a_description_after_the_name() -> None:
    session = NamingSession([navigate(), email(1), email(3)], [])
    [start, mail] = session.input_proposals

    assert session.name_input(start, "portal_url: sign-in page of the portal") is None
    assert session.name_input(mail, ": the account's email") is None

    assert [(d.name, d.description) for d in session.decisions().inputs] == [
        ("portal_url", "sign-in page of the portal"),
        ("email", "the account's email"),
    ]


def test_a_name_with_an_empty_description_keeps_the_proposed_one() -> None:
    session = NamingSession([navigate()], [])
    [start] = session.input_proposals

    assert session.name_input(start, "portal_url:") is None

    assert session.decisions().inputs[0].description == start.default_description


def test_a_description_the_format_cannot_hold_is_refused() -> None:
    session = NamingSession([navigate()], [])
    [start] = session.input_proposals

    problem = "the description must be one line of at most 256 characters"
    assert session.name_input(start, "portal_url: " + "x" * 300) == problem
    assert session.name_input(start, "portal_url: tab" + chr(7)) == problem
    assert session.decisions().inputs[0].name == "start_url"


def test_a_repeated_name_keeps_the_description_already_chosen() -> None:
    session = NamingSession([navigate(), email(1), email(2, "ada@example.test")], [])
    [_, mail] = session.input_proposals

    assert session.name_input(mail, "login: the account's email") is None
    assert session.name_input(mail, "login") is None

    assert session.decisions().inputs[-1].description == "the account's email"


def test_answers_are_validated_as_they_are_given() -> None:
    session = NamingSession([navigate(), email(1), secret(2)], [])
    [start, mail] = session.input_proposals
    [password] = session.secret_proposals

    assert session.name_secret(password, "Portal Password") == SLUG_DESCRIPTION
    assert session.name_secret(password, "portal_password") is None
    assert (
        session.name_input(mail, "portal_password")
        == "'portal_password' is already the name of a secret"
    )
    assert session.name_input(start, "portal_url") is None
    assert session.name_input(mail, "portal_url") == (
        "'portal_url' is already the name of an input with a different value"
    )
    assert session.name_input(mail, "not a slug") == SLUG_DESCRIPTION
    assert session.name_input(mail, "-") is None
    assert (
        session.name_secret(password, "portal_url")
        == "'portal_url' is already the name of an input"
    )

    decisions = session.decisions()
    assert [d.name for d in decisions.inputs] == ["portal_url"]
    assert [d.name for d in decisions.secrets] == ["portal_password"]


def test_suggestions_avoid_names_already_taken() -> None:
    session = NamingSession([navigate(), email(1), secret(2)], [])
    [start, mail] = session.input_proposals
    [password] = session.secret_proposals
    assert session.name_secret(password, "email") is None

    assert session.suggested_input_name(mail) == "email_2"
    assert session.name_input(start, "") is None
    assert session.suggested_input_name(start) == "start_url"


def test_input_overrides_decide_in_advance() -> None:
    date = fill(2, field("from", label="From", type="date"), LiteralDraft(value="2026-02-10"))
    city = fill(3, field("city", label="City"), LiteralDraft(value="Lisbon"))
    steps = [navigate(), email(1), date, city]

    session = NamingSession(
        steps,
        [
            ("portal_url", START_URL),
            ("from_date", "2026-02-10"),
            ("city", "Lisbon"),
            ("missing", "nothing"),
            ("Bad Name", "Lisbon"),
        ],
    )

    assert [p.default_name for p in session.input_proposals] == ["email"]
    assert session.warnings == [
        "--input missing: no recorded value matched, so it was not used",
        f"--input Bad Name: the name {SLUG_DESCRIPTION}",
    ]
    decisions = session.decisions()
    assert [(d.name, d.kind, d.steps, d.description) for d in decisions.inputs] == [
        (
            "portal_url",
            InputKind.URL,
            (0,),
            "URL of the page the workflow starts on (recorded at /index.html)",
        ),
        ("email", InputKind.TEXT, (1,), "Email address typed into the 'Email address' field"),
        ("from_date", InputKind.DATE, (2,), "Date typed into the 'From' field"),
        ("city", InputKind.TEXT, (3,), "Value typed into the 'City' field"),
    ]


def test_an_override_whose_value_is_not_valid_for_its_kind_is_refused() -> None:
    bad = navigate(url="https://user:pw@portal.example.test/")

    session = NamingSession([bad], [("portal_url", "https://user:pw@portal.example.test/")])

    assert session.warnings[0].startswith("--input portal_url: the value is not a valid url")


def test_assembly_writes_inputs_secrets_and_literals() -> None:
    date = fill(3, field("from", label="From", type="date"), LiteralDraft(value="2026-02-10"))
    recording = Recording(
        start_url=START_URL,
        steps=(navigate(), email(1), secret(2), date),
        notices=(InteractionIgnored(reason=IgnoredReason.FRAME),),
    )
    decisions = NamingSession(recording.steps, []).decisions()

    version = assemble(recording, decisions, workflow_id="download_report", clock=CLOCK)

    assert version.created_at == datetime(2026, 9, 12, 10, 30, 15, tzinfo=UTC)
    assert [(declaration.name, declaration.description) for declaration in version.inputs] == [
        ("start_url", "URL of the page the workflow starts on (recorded at /index.html)"),
        ("email", "Email address typed into the 'Email address' field"),
    ]
    assert version.secrets == ("password",)
    first, mail, password, from_date = version.steps
    assert isinstance(first, NavigateStep)
    assert first.value == InputValue(kind=ValueKind.INPUT, name="start_url")
    assert isinstance(mail, FillStep)
    assert mail.value == InputValue(kind=ValueKind.INPUT, name="email")
    assert isinstance(password, FillStep)
    assert password.value == SecretValue(kind=ValueKind.SECRET, name="password")
    assert isinstance(from_date, FillStep)
    assert from_date.value == LiteralValue(kind=ValueKind.LITERAL, value="2026-02-10")
    assert recording.ignored_count == 1


def test_assembly_refuses_steps_that_do_not_form_a_valid_workflow() -> None:
    recording = Recording(start_url=START_URL, steps=(navigate(), navigate(1)))
    duplicate_ids = Recording(
        start_url=START_URL,
        steps=(navigate(), navigate(1).model_copy(update={"step_id": "open_0"})),
    )

    assemble(recording, Decisions(), workflow_id="demo", clock=CLOCK)
    with pytest.raises(RecordingUnusable) as caught:
        assemble(duplicate_ids, Decisions(), workflow_id="demo", clock=CLOCK)

    assert caught.value.context["reason"] == "invalid_workflow"
