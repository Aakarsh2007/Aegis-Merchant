"""The render boundary: can anything other than template+slots get out?

The tests that matter here are the adversarial ones. A renderer that fills
slots correctly is easy; the question is what it does with a customer named
``{link}``, a template body containing ``{x.__class__}``, a slot value with a
newline in it, and a body that names a field nobody meant to send.

Refusal is the expected outcome in most of this file. A partially-rendered
message is worse than none: visibly broken to the customer, and it still
spends a contact from a budget of two.
"""

from __future__ import annotations

import pytest

from app.db.enums import Channel, MessageClass
from app.db.models import MessageTemplate
from app.guardrails.consent import (
    ALLOWED_SLOTS,
    CHANNEL_BODY_LIMIT,
    RenderRefusal,
    extract_slots,
    render,
    select_template,
)

FULL_SLOTS = {
    "first_name": "Ananya",
    "amount": "Rs 4,299",
    "cause": "UPI timeout",
    "validity": "30",
    "link": "https://rzp.io/i/abc123",
}


def _template(
    body: str = (
        "Hi {first_name}, aapka {amount} ka payment {cause} ki wajah se complete "
        "nahi hua. Yeh fresh link {validity} minutes tak valid hai: {link}"
    ),
    *,
    channel: Channel = Channel.WHATSAPP,
    message_class: MessageClass = MessageClass.TRANSACTIONAL,
    approved: bool = True,
    dlt: str | None = "DLT_UTIL_RETRY_0001",
    language: str = "hinglish",
) -> MessageTemplate:
    return MessageTemplate(
        id="tpl_test",
        merchant_id="mch_test",
        channel=channel,
        message_class=message_class,
        dlt_template_id=dlt,
        language=language,
        body_with_slots=body,
        approved=approved,
    )


def _render(template: MessageTemplate, slots: dict[str, str], **kw: bool):  # type: ignore[no-untyped-def]
    return render(
        template,
        slots=slots,
        marketing_consent=kw.get("marketing_consent", True),
        transactional_consent=kw.get("transactional_consent", True),
    )


# ===========================================================================
class TestHappyPath:
    def test_renders_every_slot(self) -> None:
        msg = _render(_template(), FULL_SLOTS)
        assert msg.body == (
            "Hi Ananya, aapka Rs 4,299 ka payment UPI timeout ki wajah se complete "
            "nahi hua. Yeh fresh link 30 minutes tak valid hai: https://rzp.io/i/abc123"
        )
        assert msg.dlt_template_id == "DLT_UTIL_RETRY_0001"
        assert msg.slots == FULL_SLOTS

    def test_extract_slots_is_ordered_and_deduplicated(self) -> None:
        assert extract_slots("{a} {b} {a} {c}") == ("a", "b", "c")

    def test_slots_are_recorded_for_the_audit_block(self) -> None:
        """An auditor must be able to re-render from the template and these
        values and get identical bytes."""
        msg = _render(_template(), FULL_SLOTS)
        assert (
            render(
                _template(),
                slots=msg.slots,
                marketing_consent=True,
                transactional_consent=True,
            ).body
            == msg.body
        )


# ===========================================================================
class TestInjection:
    """Values are data. Nothing in a slot may become structure."""

    def test_a_customer_named_like_a_slot_is_not_expanded(self) -> None:
        """The attack: name yourself "{link}" and see if the renderer hands
        you the payment URL in the greeting. Values are substituted in one
        pass and never re-scanned, so it cannot."""
        msg = _render(_template(), {**FULL_SLOTS, "first_name": "{link}"})
        assert msg.body.startswith("Hi {link}, aapka")
        assert msg.body.count("https://rzp.io/i/abc123") == 1

    def test_format_string_expressions_are_not_slots(self) -> None:
        """`str.format` would walk this and reach process globals. The slot
        pattern is a bare identifier, so it never matches — and the leftover
        brace then trips the unresolved-placeholder check."""
        tpl = _template("Hi {x.__class__.__init__.__globals__}")
        with pytest.raises(RenderRefusal) as exc:
            _render(tpl, {})
        assert exc.value.code == "unresolved_placeholder"

    def test_index_and_conversion_syntax_are_not_slots(self) -> None:
        for body in ("{a[0]}", "{a!r}", "{a:>10}"):
            with pytest.raises(RenderRefusal):
                _render(_template(body), {})

    def test_a_value_containing_braces_survives_literally(self) -> None:
        msg = _render(_template("Hi {first_name}"), {"first_name": "a{b}c"})
        assert msg.body == "Hi a{b}c"

    @pytest.mark.parametrize("bad", ["a\x00b", "a\x1bb", "a\x7fb"])
    def test_control_characters_are_refused(self, bad: str) -> None:
        with pytest.raises(RenderRefusal) as exc:
            _render(_template("Hi {first_name}"), {"first_name": bad})
        assert exc.value.code == "control_characters"

    def test_newlines_are_refused_off_email(self) -> None:
        """A CR/LF through a slot is how a header gets forged on paths that
        have headers, and how an SMS becomes two messages."""
        with pytest.raises(RenderRefusal) as exc:
            _render(_template("Hi {first_name}"), {"first_name": "Ananya\r\nBcc: x@y.z"})
        assert exc.value.code == "newline_in_slot"

    def test_newlines_are_allowed_on_email(self) -> None:
        tpl = _template("Dear {first_name}", channel=Channel.EMAIL, dlt=None)
        assert "\n" in _render(tpl, {"first_name": "A\nB"}).body


# ===========================================================================
class TestRefusals:
    def test_a_missing_slot_refuses_rather_than_sending_a_broken_message(self) -> None:
        """ "Hi {first_name}, your Rs 4,299 payment" must never go out. It is
        visibly broken AND it spends a contact."""
        slots = {k: v for k, v in FULL_SLOTS.items() if k != "first_name"}
        with pytest.raises(RenderRefusal) as exc:
            _render(_template(), slots)
        assert exc.value.code == "missing_slots"
        assert "first_name" in exc.value.detail

    def test_an_unused_value_refuses(self) -> None:
        """The caller believes something is in the message that is not. That
        disagreement is worth surfacing, not silently dropping."""
        with pytest.raises(RenderRefusal) as exc:
            _render(_template(), {**FULL_SLOTS, "discount": "10"})
        assert exc.value.code == "unused_slots"

    def test_a_slot_outside_the_allowlist_refuses(self) -> None:
        """A template naming {ltv} would put data in front of a customer that
        nobody intended to send."""
        with pytest.raises(RenderRefusal) as exc:
            _render(_template("Hi {ltv}"), {"ltv": "48000"})
        assert exc.value.code == "forbidden_slots"

    def test_an_unapproved_template_refuses(self) -> None:
        with pytest.raises(RenderRefusal) as exc:
            _render(_template(approved=False), FULL_SLOTS)
        assert exc.value.code == "template_not_approved"

    def test_a_missing_dlt_id_refuses_on_carrier_channels(self) -> None:
        for channel in (Channel.SMS, Channel.WHATSAPP):
            with pytest.raises(RenderRefusal) as exc:
                _render(
                    _template("Hi {first_name}", channel=channel, dlt=None), {"first_name": "A"}
                )
            assert exc.value.code == "missing_dlt_id"

    def test_email_does_not_need_a_dlt_id(self) -> None:
        tpl = _template("Hi {first_name}", channel=Channel.EMAIL, dlt=None)
        assert _render(tpl, {"first_name": "A"}).body == "Hi A"

    def test_an_sms_over_160_characters_refuses(self) -> None:
        """Over the limit it silently becomes multipart: costs more, can
        arrive out of order."""
        tpl = _template("{first_name}", channel=Channel.SMS, dlt="DLT_X")
        with pytest.raises(RenderRefusal) as exc:
            _render(tpl, {"first_name": "x" * (CHANNEL_BODY_LIMIT[Channel.SMS] + 1)})
        assert exc.value.code == "body_too_long"

    def test_a_malformed_body_refuses(self) -> None:
        with pytest.raises(RenderRefusal) as exc:
            _render(_template("Hi {first_name"), {})
        assert exc.value.code == "unresolved_placeholder"


# ===========================================================================
class TestConsentAtTheRenderBoundary:
    """S-08 already decided. This is the last gate before bytes leave, and it
    re-checks against the template's OWN class rather than a label carried
    alongside it."""

    def test_marketing_template_without_marketing_consent_refuses(self) -> None:
        tpl = _template(
            "Hi {first_name}, {discount}% off: {link}", message_class=MessageClass.MARKETING
        )
        with pytest.raises(RenderRefusal) as exc:
            _render(
                tpl,
                {"first_name": "A", "discount": "10", "link": "u"},
                marketing_consent=False,
            )
        assert exc.value.code == "marketing_without_consent"

    def test_marketing_template_with_consent_renders(self) -> None:
        tpl = _template(
            "Hi {first_name}, {discount}% off: {link}", message_class=MessageClass.MARKETING
        )
        msg = _render(
            tpl, {"first_name": "A", "discount": "10", "link": "u"}, marketing_consent=True
        )
        assert msg.message_class is MessageClass.MARKETING

    def test_no_transactional_consent_refuses_even_a_utility_message(self) -> None:
        with pytest.raises(RenderRefusal) as exc:
            _render(_template(), FULL_SLOTS, transactional_consent=False)
        assert exc.value.code == "no_transactional_consent"


# ===========================================================================
class TestTemplateSelection:
    def test_never_falls_back_across_message_class(self) -> None:
        """The load-bearing one. Substituting a marketing template when the
        transactional one is missing would send a promotional message to
        someone who consented only to service messages — precisely the
        violation the class distinction exists to prevent."""
        marketing_only = [_template(message_class=MessageClass.MARKETING)]
        with pytest.raises(RenderRefusal) as exc:
            select_template(
                marketing_only,
                channel=Channel.WHATSAPP,
                message_class=MessageClass.TRANSACTIONAL,
            )
        assert exc.value.code == "no_approved_template"

    def test_language_does_fall_back(self) -> None:
        """Sending Hinglish to someone who wanted Tamil is a quality problem,
        not a compliance one, so it degrades instead of refusing."""
        got = select_template(
            [_template(language="hinglish")],
            channel=Channel.WHATSAPP,
            message_class=MessageClass.TRANSACTIONAL,
            language="tamil",
        )
        assert got.language == "hinglish"

    def test_prefers_the_requested_language(self) -> None:
        got = select_template(
            [_template(language="hinglish"), _template(language="english")],
            channel=Channel.WHATSAPP,
            message_class=MessageClass.TRANSACTIONAL,
            language="english",
        )
        assert got.language == "english"

    def test_unapproved_templates_are_not_selectable(self) -> None:
        with pytest.raises(RenderRefusal):
            select_template(
                [_template(approved=False)],
                channel=Channel.WHATSAPP,
                message_class=MessageClass.TRANSACTIONAL,
            )

    def test_never_falls_back_across_channel(self) -> None:
        with pytest.raises(RenderRefusal):
            select_template(
                [_template(channel=Channel.EMAIL, dlt=None)],
                channel=Channel.SMS,
                message_class=MessageClass.TRANSACTIONAL,
            )


class TestTheSeededTemplatesAreRenderable:
    """The templates the demo actually ships must pass their own gate. A
    template that cannot render is a runtime failure in front of a judge."""

    @pytest.mark.asyncio
    async def test_every_seeded_template_renders(self) -> None:
        from app.db.seed import _build_templates

        values = {
            "first_name": "Ananya",
            "amount": "Rs 4,299",
            "cause": "UPI timeout",
            "link": "https://rzp.io/i/abc",
            "validity": "30",
            "discount": "10",
            "invoice_id": "INV-2026-0042",
            "due_date": "1 Sep 2026",
            "merchant_name": "GlowKart",
        }
        for tpl in _build_templates():
            needed = {k: values[k] for k in extract_slots(tpl.body_with_slots)}
            msg = render(
                tpl,
                slots=needed,
                marketing_consent=True,
                transactional_consent=True,
            )
            assert msg.body
            assert "{" not in msg.body

    def test_no_seeded_template_names_a_forbidden_slot(self) -> None:
        from app.db.seed import _build_templates

        for tpl in _build_templates():
            assert set(extract_slots(tpl.body_with_slots)) <= ALLOWED_SLOTS, tpl.id
