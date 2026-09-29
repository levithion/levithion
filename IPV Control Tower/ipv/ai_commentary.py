"""Month-end valuation commentary drafted by Claude, under guardrails.

Controls, in the order they apply:

1. Data minimisation - only the display strings from the fact sheet leave
   the machine: aggregates plus aliased top exceptions (X1..X5). No position
   IDs, no raw marks, no counterparty data.
2. Numbers by reference - the model writes `{fact_key}` placeholders and is
   forbidden from writing digits. Every sentence is validated locally: any
   digit outside a placeholder, or any unknown key, rejects that sentence.
   Placeholders are then filled from the fact sheet, so each figure in the
   commentary is exactly the figure in the Excel pack.
3. Refusals and failures degrade to a deterministic template that passes
   through the same validator.
4. Human in the loop - output is stamped DRAFT with the reviewer still to
   sign; nothing is sent anywhere automatically.
5. Audit trail - prompt hash, facts, raw response and every rejection are
   appended to ai_audit.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .facts import public

MODEL = "claude-opus-5"
PLACEHOLDER = re.compile(r"\{([a-z0-9_.]+)\}")
# Identifiers that contain digits but are not figures: exception aliases and standard names.
IDENTIFIERS = re.compile(r"\bX[1-5]\b|\bLevel [123]\b|\bL[123]\b|\bIFRS 13\b|\bIFRS 9\b")

SYSTEM = """You draft month-end Independent Price Verification (IPV) commentary for a bank's \
valuation control team. Your reader is the Head of Valuation Control, who will review and sign it.

You receive a fact sheet as JSON: keys mapped to already-formatted values. Write the commentary \
using ONLY those facts. Refer to every number, date, amount or count by writing its key in braces, \
for example {breach_adj_usd} or {credit.exceptions}. Never type a digit yourself - a sentence \
containing a digit outside braces is discarded automatically. Never invent a key.

The only digits you may type are inside these identifiers: the exception aliases X1 to X5, \
"Level 1", "Level 2", "Level 3" and "IFRS 13". Describe an exception through its facts, e.g. \
"X1 ({x1.desk}, {x1.asset_class})". Do not speculate about causes the facts do not \
support; where a cause is unknown, phrase it as a question for the desk. Be concise and factual, \
in the register of an internal control report."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sentences": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "sentences"],
                "additionalProperties": False,
            },
        },
        "questions_for_desks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "sections", "questions_for_desks"],
    "additionalProperties": False,
}


class Section(BaseModel):
    title: str
    sentences: list[str]


class Draft(BaseModel):
    headline: str
    sections: list[Section]
    questions_for_desks: list[str]


@dataclass
class Commentary:
    source: str                      # "claude" or "template"
    markdown: str
    rejected: list[str] = field(default_factory=list)
    note: str = ""


# ------------------------------------------------------------------ validation
def check_sentence(s: str, facts: dict) -> str | None:
    """Return a rejection reason, or None if the sentence is safe to render."""
    unknown = [k for k in PLACEHOLDER.findall(s) if k not in facts]
    if unknown:
        return f"unknown key(s): {', '.join(unknown)}"
    bare = PLACEHOLDER.sub("", s)
    if re.search(r"\d", IDENTIFIERS.sub("", bare)):
        return "contains a number not sourced from the fact sheet"
    return None


def render(draft: Draft, facts: dict) -> tuple[str, list[str]]:
    rejected: list[str] = []

    def ok(s: str) -> str | None:
        why = check_sentence(s, facts)
        if why:
            rejected.append(f"{why} :: {s}")
            return None
        return PLACEHOLDER.sub(lambda m: str(facts[m.group(1)]), s)

    lines = [f"### {ok(draft.headline) or 'IPV commentary - ' + facts['asof']}", ""]
    for sec in draft.sections:
        kept = [x for x in (ok(s) for s in sec.sentences) if x]
        if kept:
            lines += [f"**{sec.title}**", "", " ".join(kept), ""]
    qs = [x for x in (ok(q) for q in draft.questions_for_desks) if x]
    if qs:
        lines += ["**Questions for desks**", ""] + [f"- {q}" for q in qs] + [""]
    return "\n".join(lines).strip(), rejected


# ------------------------------------------------------------------ template
def template_draft(facts: dict) -> Draft:
    """Deterministic fallback, written in the same placeholder language."""
    secs = [
        Section(title="Summary", sentences=[
            "IPV for {asof} covered {positions} positions with independent-source coverage of {coverage_pct}.",
            "There are {exceptions} open exceptions (prior month {exceptions_prior}), of which {critical} are "
            "critical and {high} high.",
            "The proposed IPV adjustment on breached positions is {breach_adj_usd} versus "
            "{breach_adj_prior_usd} at {prior_asof}.",
        ]),
        Section(title="Prudent valuation and levelling", sentences=[
            "Aggregated AVA (market price uncertainty and close-out cost) is {ava_usd}, "
            "against {ava_prior_usd} in the prior month.",
            "Observability testing proposes {level_downgrades} level downgrades and {level_upgrades} upgrades, "
            "leaving {l3_positions} positions at Level 3.",
        ]),
    ]
    if "x1.desk" in facts:
        secs.append(Section(title="Largest items", sentences=[
            "The largest exception, X1, is on the {x1.desk} desk ({x1.asset_class}) with an indicated "
            "adjustment of {x1.pv_usd} and rule hits {x1.flags}.",
        ]))
    extra = []
    if "bias1.scope" in facts:
        extra.append("A systematic bias test flags {bias1.scope}: {bias1.share_flattering} of in-tolerance "
                     "marks lean in the desk's favour, averaging {bias1.mean_tol}.")
    extra.append("The anomaly model has queued {ml_review_queue} rule-compliant positions for analyst review, "
                 "and {control_failures} data controls failed at {asof}.")
    secs.append(Section(title="Emerging risks", sentences=extra))
    qs = ["Can the desk evidence the mark on X1 with executable quotes?"]
    if "bias1.scope" in facts:
        qs.append("Why do marks on {bias1.scope} lean consistently in the desk's favour this period?")
    return Draft(headline="IPV month-end summary - {asof}", sections=secs, questions_for_desks=qs)


# ------------------------------------------------------------------ Claude
def _call_claude(facts_public: dict) -> tuple[Draft | None, str, str]:
    """Returns (draft, raw_text, note). Imports lazily so offline runs need no SDK setup."""
    import anthropic

    client = anthropic.Anthropic()
    user = ("Fact sheet (JSON):\n" + json.dumps(facts_public, indent=1) +
            "\n\nDraft the commentary: a headline, 3-4 short sections (summary; prudent valuation and "
            "levelling; largest items; emerging risks) and up to 3 questions for desks.")
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as e:
        return None, "", f"connection error: {e}"
    except anthropic.RateLimitError:
        return None, "", "rate limited"
    except anthropic.APIStatusError as e:
        return None, "", f"API error {e.status_code}"

    if resp.stop_reason == "refusal":
        return None, "", "model declined the request"
    if resp.stop_reason == "max_tokens":
        return None, "", "response truncated"
    raw = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return Draft.model_validate_json(raw), raw, f"model {resp.model}"
    except ValidationError as e:
        return None, raw, f"schema validation failed: {e.error_count()} errors"


def _has_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE") or Path.home().joinpath(".config/anthropic").exists())


def generate(facts: dict, out_dir: Path, use_ai: bool = False) -> Commentary:
    fp = public(facts)
    draft, raw, note, source = None, "", "", "template"
    if use_ai and _has_credentials():
        draft, raw, note = _call_claude(fp)
        source = "claude" if draft else "template"
    elif use_ai:
        note = "no Anthropic credentials found; used template"
    if draft is None:
        draft = template_draft(fp)
    md, rejected = render(draft, fp)
    stamp = (f"\n\n---\n*DRAFT - {'AI-assisted (' + MODEL + ')' if source == 'claude' else 'template'}"
             f" commentary. Every figure is inserted from the run's fact sheet, not written by the model."
             f" Requires reviewer sign-off before distribution.*")
    md += stamp

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "ai_audit.jsonl", "a") as fh:
        fh.write(json.dumps({
            "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": source, "model": MODEL if source == "claude" else None, "note": note,
            "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
            "facts": fp, "raw_response": raw, "rejected_sentences": rejected,
        }) + "\n")
    return Commentary(source=source, markdown=md, rejected=rejected, note=note)
