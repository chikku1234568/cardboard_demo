from stylemem.config import DEFAULT
from stylemem.engine import MemoryEngine
from stylemem.extract import RuleBasedExtractor, _Labelled
from stylemem.suggest import suggest

CTX = {"footage_type": "talking_head", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}
KEY = ("ava", "any", "transition", "footage_type=talking_head|prev_edit=jump_cut|format=16:9|section=body")


def _engine():
    return MemoryEngine(DEFAULT, extractor=_Labelled(RuleBasedExtractor()))


def test_single_odd_session_does_not_supersede(stream):
    s = stream()
    for _ in range(6):
        s.session([("transition", CTX, "crossfade", "override")] * 2)
    s.session([("transition", CTX, "whip_pan", "override")] * 2)        # one odd session
    s.session([("transition", CTX, "crossfade", "override")] * 2)
    e = _engine()
    e.ingest(s.events)
    assert e.slots[KEY].active.action == "crossfade"
    assert not [x for x in e.supersessions if x["context_key"] == KEY[3]]


def test_drift_supersedes_after_n_sessions_and_closes_old(stream):
    s = stream()
    for _ in range(6):
        s.session([("transition", CTX, "crossfade", "override")] * 2)
    new_sids = [s.session([("transition", CTX, "whip_pan", "override")] * 2) for _ in range(6)]
    e = _engine()
    e.ingest(s.events)
    slot = e.slots[KEY]
    assert slot.active.action == "whip_pan"
    old = [p for p in slot.history if p.action == "crossfade"][0]
    assert old.valid_to is not None and old.superseded_by == slot.active.pref_id
    sup = [x for x in e.supersessions if x["context_key"] == KEY[3]][0]
    # needs N consecutive sessions in which the challenger leads the fast window
    assert new_sids.index(sup["session_id"]) + 1 >= DEFAULT.supersede_after_sessions
    # the old style does not linger: slow weights restarted from the recent window
    assert slot.w.get("crossfade", 0) / sum(slot.w.values()) < 0.25
    r = suggest(e.profile(), CTX, "ava", "private", "transition")
    assert r["best_guess"] == "whip_pan"


def test_explicit_statement_supersedes_immediately(stream):
    s = stream()
    for _ in range(4):
        s.session([("transition", {"prev_edit": "jump_cut"}, "crossfade", "override")])
    s.session([], statement="I've been into whip pans after jump cuts lately, keep doing that.")
    e = _engine()
    e.ingest(s.events)
    slot = e.slots[("ava", "any", "transition", "prev_edit=jump_cut")]
    assert slot.active.action == "whip_pan" and slot.active.origin == "explicit"
    assert slot.active.reason.startswith("explicit statement")
    assert any(x["reason"].startswith("explicit") for x in e.supersessions)


def test_real_drift_and_control(train_engine):
    # planted: ava switches crossfade -> whip_pan at s18 (talking head after jump cut); b-roll stays crossfade
    prof = train_engine.profile()
    assert suggest(prof, CTX, "ava", "private", "transition")["best_guess"] == "whip_pan"
    br = {"footage_type": "broll", "prev_edit": "jump_cut", "format": "16:9", "section": "body"}
    assert suggest(prof, br, "ava", "private", "transition")["best_guess"] == "crossfade"
