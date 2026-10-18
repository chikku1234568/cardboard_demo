import pytest

from stylemem import data
from stylemem.extract import RuleBasedExtractor, validate


def test_rule_based_fallback_schema():
    x = RuleBasedExtractor()
    out = x.extract("Team standard: stacked captions on all vertical shorts.", "cara", True, True)
    assert validate(out)["kind"] == "standard" and out["action"] == "stacked" and out["context"]["format"] == "9:16"
    out = x.extract("Honestly beast mode captions would do better on our shorts.", "ben", False, True)
    assert out["kind"] == "proposal"
    assert not x.extract("The render is slow today, ugh.", "ava", False, False)["is_preference"]
    assert not x.extract("Make the intro snappier.", "ava", False, False)["is_preference"]


def test_every_event_has_provenance():
    for e in data.events():
        assert e.get("op_id") or e.get("chat_id")


def _neo():
    try:
        from stylemem.graph import Neo4jGraph
        g = Neo4jGraph()
        g.run("RETURN 1")
        return g
    except Exception:
        return None


@pytest.mark.skipif(_neo() is None, reason="Neo4j not running")
def test_neo4j_profile_matches_engine():
    """Requires `python -m stylemem.ingest --reset` (full history) to have been run."""
    from stylemem.engine import MemoryEngine
    from stylemem.suggest import suggest
    g = _neo()
    if g.run("MATCH (s:Session) RETURN count(s) AS c")[0]["c"] < 106:
        pytest.skip("graph not fully ingested")
    gp = g.load_profile()
    e = MemoryEngine()
    e.ingest(data.events())
    e.finalize()
    ep = e.profile()
    for d in data.heldout_decisions()[:80]:
        a = suggest(ep, d["context"], d["actor"], d["project_scope"], d["category"], d["subject"], d["brand_id"])
        b = suggest(gp, d["context"], d["actor"], d["project_scope"], d["category"], d["subject"], d["brand_id"])
        assert (a["best_guess"], a["abstain"]) == (b["best_guess"], b["abstain"])
