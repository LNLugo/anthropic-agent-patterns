"""Personal data redaction at intake (s5): the stand-in for a PII detection service.

What is redacted in the indexed text: consumer names and contact details, employee names
and employee numbers. What is kept: the names of the people who approved a document,
because they are part of the controlled record. No rule here targets an "Approved by"
line, and a test checks every approver name survives; a detector that finds names
anywhere would need an explicit exception for them. Originals stay in the source system.

The rules know the forms the sources use: a "Name:" line in a complaint's Consumer
section, an "operator <name> (E123456)" mention, phone numbers and personal email
addresses. Role mailboxes (qa@, quality@) are not personal and are kept. A name written
in free text, outside those forms, is not caught: that is the limit of rules, and it is
measured, not hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parse import Line, Parsed

EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+)@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
ROLE_MAILBOXES = {"qa", "quality", "info", "orders", "supplier-quality", "complaints"}
PHONE = re.compile(r"\+\d{1,3}(?: \d{2,4}){2,4}\b")
EMPLOYEE_ID = re.compile(r"\bE\d{6}\b")
OPERATOR = re.compile(r"\b(operator )([A-Z][a-z]+ [A-Z][a-z]+)(?= \()")


@dataclass(frozen=True)
class Finding:
    kind: str
    text: str
    section: str


def _redact(text: str, section: str, found: list[Finding]) -> str:
    if section == "Consumer" and text.startswith("Name: "):
        found.append(Finding("consumer_name", text[6:], section))
        return "Name: [REDACTED:NAME]"

    def email(m):
        if m.group(1).lower() in ROLE_MAILBOXES:
            return m.group(0)
        found.append(Finding("email", m.group(0), section))
        return "[REDACTED:EMAIL]"

    def tag(kind, label):
        def f(m):
            found.append(Finding(kind, m.group(0), section))
            return f"[REDACTED:{label}]"
        return f

    def operator(m):
        found.append(Finding("employee_name", m.group(2), section))
        return m.group(1) + "[REDACTED:NAME]"

    text = EMAIL.sub(email, text)
    text = PHONE.sub(tag("phone", "PHONE"), text)
    text = OPERATOR.sub(operator, text)
    text = EMPLOYEE_ID.sub(tag("employee_id", "EMPLOYEE_ID"), text)
    return text


def redact(doc: Parsed) -> tuple[Parsed, list[Finding]]:
    """The document with personal data replaced in every line, and what was replaced."""
    found: list[Finding] = []
    header = [Line(_redact(x.text, "Header", found), x.page, x.confidence, x.region) for x in doc.header]
    sections = []
    for s in doc.sections:
        s2 = type(s)(s.number, s.heading, [Line(_redact(x.text, s.heading, found), x.page, x.confidence, x.region)
                                           for x in s.lines], s.tables)
        sections.append(s2)
    return Parsed(doc.fmt, doc.kind_line, doc.title, header, sections, doc.attachments), found
