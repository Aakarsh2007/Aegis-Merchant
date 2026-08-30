"""The render boundary: template + slots, and no way to say anything else.

This module does **not** decide consent. S-07, S-08 and S-09 already do that
(``guardrails/stopping_rules.py``), and re-deciding it here is exactly the
INC-007 shape — the same judgement in two places, drifting apart until one of
them is wrong. What happens here is enforcement at the *last* boundary, after
the decision is made and immediately before bytes would leave the process.

Why a separate gate at all, if the rules already ran
---------------------------------------------------

Because the rules operate on a *proposal*, and this operates on the *message*.
Between the two sits an LLM that wrote copy. The interesting failure is not
"the agent decided to send a marketing message without consent" — S-08 catches
that. It is "the agent decided to send a transactional message, and the model
wrote ``20% off!`` into it". A message's compliance class is a property of what
it says, not of what we labelled it, and nothing upstream reads the final text.

So the model never writes a message. It fills named slots in a template that a
human registered with the carrier, and this module is what makes that
structural rather than aspirational.

Why not ``str.format``
----------------------

``"{first_name}".format(**slots)`` looks like the obvious implementation and is
the wrong one. ``str.format`` walks attribute and index expressions, so a body
containing ``{x.__class__.__init__.__globals__}`` reads process globals, and a
value that happens to contain braces gets re-examined. Our bodies come from an
``approved`` row rather than from user input, which makes that unlikely rather
than impossible — and "unlikely" is a poor property for the code that renders
every outbound message.

:func:`render` instead extracts the slot names, checks each one against an
allowlist, and substitutes literally. Values are never re-scanned, so a
customer whose name is ``{link}`` gets a message containing the characters
``{link}`` and not their payment URL. There is no expression evaluation to
attack because there is no expression evaluation.

Refusing is the safe direction
------------------------------

Every failure here returns a refusal, never a partial message. A template with
an unfilled slot must not go out as ``Hi {first_name}, your ₹4,299 payment`` —
that is worse than sending nothing, because it is visibly broken to the
customer and still consumes the contact budget the caps are protecting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.db.enums import Channel, MessageClass
from app.db.models import MessageTemplate

__all__ = [
    "ALLOWED_SLOTS",
    "CHANNEL_BODY_LIMIT",
    "DLT_REQUIRED_CHANNELS",
    "RenderRefusal",
    "RenderedMessage",
    "extract_slots",
    "render",
    "select_template",
]

#: Every slot a template may name. An allowlist rather than a denylist: a
#: template that referenced ``{internal_notes}`` or ``{ltv}`` would leak data
#: we never intended to put in front of a customer, and a denylist cannot
#: anticipate the field somebody adds next year.
ALLOWED_SLOTS: Final[frozenset[str]] = frozenset(
    {
        "first_name",
        "amount",
        "cause",
        "link",
        "validity",
        "discount",
        "invoice_id",
        "due_date",
        "merchant_name",
    }
)

#: Indian carriers require a registered DLT template id for commercial traffic
#: on these channels (TRAI TCCCPR). Email is not in scope for DLT.
DLT_REQUIRED_CHANNELS: Final[frozenset[Channel]] = frozenset({Channel.SMS, Channel.WHATSAPP})

#: Practical body limits. SMS is the binding one: a body over 160 GSM-7
#: characters silently becomes multipart, which costs more and can arrive out
#: of order. Email is generous because a formal invoice reminder is long.
CHANNEL_BODY_LIMIT: Final[dict[Channel, int]] = {
    Channel.SMS: 160,
    Channel.WHATSAPP: 1024,
    Channel.EMAIL: 5000,
    Channel.NONE: 0,
}

#: A slot reference. Deliberately narrow — a bare identifier and nothing else,
#: so ``{a.b}``, ``{a[0]}`` and ``{a!r}`` are not slots and never match.
_SLOT_RE: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Any remaining brace after substitution means the body contained something
#: brace-shaped that was not a plain slot.
_ANY_BRACE_RE: Final = re.compile(r"[{}]")

#: Characters that must never reach a message body. Newlines are permitted on
#: email only; control characters never are. A CR or LF injected through a slot
#: is how a header gets forged on the email path.
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class RenderRefusal(Exception):
    """A message could not be rendered safely, so none was produced.

    Carries a machine-readable ``code`` because the caller records the refusal
    on the case and the operator needs to know *which* rule refused, not that
    something did.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RenderedMessage:
    """A message that is safe to hand to a channel adapter."""

    template_id: str
    dlt_template_id: str | None
    channel: Channel
    message_class: MessageClass
    language: str
    body: str
    #: Exactly the slots that were substituted, for the audit block. An
    #: auditor can re-render from the template and these values and get the
    #: same bytes.
    slots: dict[str, str]


def extract_slots(body: str) -> tuple[str, ...]:
    """Slot names in the order they appear, de-duplicated."""
    seen: dict[str, None] = {}
    for match in _SLOT_RE.finditer(body):
        seen.setdefault(match.group(1), None)
    return tuple(seen)


def select_template(
    templates: list[MessageTemplate],
    *,
    channel: Channel,
    message_class: MessageClass,
    language: str = "hinglish",
) -> MessageTemplate:
    """Pick the registered template for this channel and class.

    Never falls back across ``message_class``. Selecting a marketing template
    when a transactional one is missing would send a promotional message to
    someone who consented only to service messages — the precise violation the
    class distinction exists to prevent. A missing transactional template is a
    configuration error and is raised as one.

    Language falls back, because sending Hinglish to someone who wanted English
    is a quality problem, not a compliance one.
    """
    eligible = [
        t
        for t in templates
        if t.channel is channel and t.message_class is message_class and t.approved
    ]
    if not eligible:
        raise RenderRefusal(
            "no_approved_template",
            f"no approved {message_class.value} template for {channel.value}",
        )
    for candidate in eligible:
        if candidate.language == language:
            return candidate
    return eligible[0]


def render(
    template: MessageTemplate,
    *,
    slots: dict[str, str],
    marketing_consent: bool,
    transactional_consent: bool,
) -> RenderedMessage:
    """Fill a template's slots, or refuse.

    The consent arguments are a **second** check, after S-08 has already
    decided. Defence in depth is warranted here specifically because this is
    the last point at which a message can be stopped: everything downstream
    sends bytes.
    """
    # 1. The template itself must be registered and approved. An unapproved
    #    template is a draft, and a draft has not been through DLT.
    if not template.approved:
        raise RenderRefusal(
            "template_not_approved",
            f"template {template.id} is not approved for sending",
        )

    if template.channel in DLT_REQUIRED_CHANNELS and not template.dlt_template_id:
        raise RenderRefusal(
            "missing_dlt_id",
            f"{template.channel.value} requires a registered DLT template id",
        )

    # 2. Consent, re-checked against the template's own class rather than
    #    against a label carried alongside it.
    if template.message_class is MessageClass.MARKETING and not marketing_consent:
        raise RenderRefusal(
            "marketing_without_consent",
            "a marketing template cannot be rendered without marketing consent",
        )
    if not transactional_consent:
        raise RenderRefusal(
            "no_transactional_consent",
            "no consent of any class on record for this customer",
        )

    body = template.body_with_slots
    required = set(extract_slots(body))
    provided = set(slots)

    # 3. Every slot the body names must have a value. A partially-filled
    #    message is worse than no message: visibly broken to the customer, and
    #    it still spends the contact budget the caps exist to protect.
    if missing := sorted(required - provided):
        raise RenderRefusal("missing_slots", f"template needs {missing}")

    # 4. Every value supplied must be used. An unused value means the caller
    #    and the template disagree about what is being sent, and the caller
    #    believes something is in the message that is not.
    if extra := sorted(provided - required):
        raise RenderRefusal("unused_slots", f"values supplied for absent slots: {extra}")

    # 5. Slot names must be on the allowlist, so a template cannot name a
    #    field we never meant to put in front of a customer.
    if forbidden := sorted(required - ALLOWED_SLOTS):
        raise RenderRefusal("forbidden_slots", f"not permitted in a message body: {forbidden}")

    for name, value in slots.items():
        if _CONTROL_RE.search(value):
            raise RenderRefusal("control_characters", f"slot {name!r} contains control characters")
        if template.channel is not Channel.EMAIL and ("\n" in value or "\r" in value):
            raise RenderRefusal("newline_in_slot", f"slot {name!r} contains a line break")

    # 6. Validate the TEMPLATE's structure, before substituting — not the
    #    rendered output.
    #
    #    Checking the output would conflate two unrelated things: a malformed
    #    body such as "Hi {first_name" (our bug, must refuse) and a slot value
    #    that happens to contain a brace, such as a customer who has typed
    #    "{link}" into the name field (their data, must pass through
    #    untouched). Structure is a property of the template; values are
    #    opaque. Removing the valid slots and looking at what is left tests
    #    exactly the first thing and none of the second.
    if _ANY_BRACE_RE.search(_SLOT_RE.sub("", body)):
        raise RenderRefusal(
            "unresolved_placeholder",
            "template body contains a brace that is not a well-formed slot",
        )

    # 7. Substitute literally, one pass, values never re-scanned. A customer
    #    named "{link}" gets those six characters, not their payment URL.
    rendered = _SLOT_RE.sub(lambda m: slots[m.group(1)], body)

    limit = CHANNEL_BODY_LIMIT.get(template.channel, 0)
    if len(rendered) > limit:
        raise RenderRefusal(
            "body_too_long",
            f"{len(rendered)} characters exceeds the {template.channel.value} limit of {limit}",
        )

    return RenderedMessage(
        template_id=template.id,
        dlt_template_id=template.dlt_template_id,
        channel=template.channel,
        message_class=template.message_class,
        language=template.language,
        body=rendered,
        slots=dict(slots),
    )
