from ipv.ai_commentary import Draft, Section, check_sentence, render, template_draft

FACTS = {"asof": "30 Sep 2026", "exceptions": "96", "breach_adj_usd": "-USD 6.38m", "x1.desk": "Credit"}


def test_placeholders_are_accepted():
    assert check_sentence("There are {exceptions} exceptions at {asof}.", FACTS) is None


def test_model_written_numbers_are_rejected():
    assert "number" in check_sentence("Exceptions rose by 12 this month.", FACTS)
    assert "number" in check_sentence("The reserve is USD 6.4m.", FACTS)


def test_unknown_keys_are_rejected():
    assert "unknown" in check_sentence("AVA was {ava_total}.", FACTS)


def test_identifiers_with_digits_are_allowed():
    assert check_sentence("X1 sits on the {x1.desk} desk at Level 3 under IFRS 13.", FACTS) is None
    assert check_sentence("X9 is new.", FACTS) is not None  # only X1..X5 exist


def test_render_substitutes_and_drops_bad_sentences():
    d = Draft(headline="IPV {asof}", sections=[Section(title="S", sentences=[
        "Reserve is {breach_adj_usd}.", "Reserve is about 6 million."])], questions_for_desks=[])
    md, rejected = render(d, FACTS)
    assert "-USD 6.38m" in md and "about 6 million" not in md
    assert len(rejected) == 1


def test_template_passes_its_own_validator(tmp_path):
    from ipv import pipeline
    f = pipeline.run(out_dir=tmp_path, n_positions=300).facts
    from ipv.facts import public
    _, rejected = render(template_draft(public(f)), public(f))
    assert rejected == []


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text, stop="end_turn"):
        self.content, self.stop_reason, self.model = [_Block(text)], stop, "claude-opus-5"


def _fake_client(resp, calls):
    class Messages:
        def create(self, **kw):
            calls.append(kw)
            return resp

    class Beta:
        messages = Messages()

    class Client:
        beta = Beta()

    return lambda *a, **k: Client()


def _live(monkeypatch, tmp_path, resp):
    import anthropic
    from ipv import ai_commentary
    calls = []
    monkeypatch.setattr(anthropic, "Anthropic", _fake_client(resp, calls))
    monkeypatch.setattr(ai_commentary, "_has_credentials", lambda: True)
    facts = dict(FACTS, positions="1,200", coverage_pct="97.2%")
    return ai_commentary.generate(facts, tmp_path, use_ai=True), calls


def test_live_path_validates_and_fills(monkeypatch, tmp_path):
    import json
    draft = {"headline": "IPV close {asof}", "sections": [{"title": "Summary", "sentences": [
        "Coverage was {coverage_pct} across {positions} positions.",
        "Exceptions rose by 13 versus last month."]}], "questions_for_desks": []}
    c, calls = _live(monkeypatch, tmp_path, _Resp(json.dumps(draft)))
    assert c.source == "claude"
    assert "97.2% across 1,200 positions" in c.markdown and "rose by 13" not in c.markdown
    assert len(c.rejected) == 1
    # Only display strings are sent; aliases and numeric twins stay local.
    sent = calls[0]["messages"][0]["content"]
    assert "_aliases" not in sent and "_num" not in sent
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    audit = (tmp_path / "ai_audit.jsonl").read_text()
    assert "rose by 13" in audit


def test_refusal_falls_back_to_template(monkeypatch, tmp_path):
    c, _ = _live(monkeypatch, tmp_path, _Resp("", stop="refusal"))
    assert c.source == "template" and "declined" in c.note
