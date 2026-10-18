from stylemem.config import DEFAULT
from stylemem.engine import MemoryEngine
from stylemem.extract import RuleBasedExtractor, _Labelled
from stylemem.suggest import suggest, wilson_lb

CTX = {"footage_type": "talking_head", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}


def test_three_of_three_is_not_certain():
    c = wilson_lb(1.0, 3, DEFAULT.wilson_z)
    assert 0.5 < c < 0.7
    assert wilson_lb(1.0, 30, DEFAULT.wilson_z) > c         # more evidence -> more confidence
    assert wilson_lb(0.6, 30, DEFAULT.wilson_z) < wilson_lb(0.9, 30, DEFAULT.wilson_z)


def test_abstain_on_thin_evidence(stream):
    s = stream()
    s.session([("transition", CTX, "whip_pan", "accept")])     # one passive accept
    e = MemoryEngine(DEFAULT, extractor=_Labelled(RuleBasedExtractor()))
    e.ingest(s.events)
    r = suggest(e.profile(), CTX, "ava", "private", "transition")
    assert r["abstain"] and r["action"] is None


def test_abstain_on_unseen_context(train_engine):
    r = suggest(train_engine.profile(), {"footage_type": "drone", "prev_edit": "aerial_reveal", "format": "4:5"},
                "ava", "private", "transition")
    assert r["abstain"] and "unseen context" in r["why"][-1]


def test_abstain_for_unknown_actor(train_engine):
    r = suggest(train_engine.profile(), CTX, "cara", "private", "transition")
    assert r["abstain"]


def test_answers_when_confident(train_engine):
    r = suggest(train_engine.profile(), {"format": "9:16", "section": "hook", "footage_type": "talking_head"},
                "ava", "private", "caption_style")
    assert not r["abstain"] and r["action"] == "word_pop" and r["confidence"] >= DEFAULT.abstain_below
