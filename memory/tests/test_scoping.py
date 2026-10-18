from stylemem.suggest import suggest

VERT = {"footage_type": "talking_head", "prev_edit": "jump_cut", "format": "9:16", "section": "hook"}


def test_private_project_uses_private_preference(train_engine):
    r = suggest(train_engine.profile(), VERT, "ben", "private", "caption_style")
    assert r["action"] == "beast_mode"
    assert r["conflicts"] == []          # private > team: no conflict raised in a private project
    assert r["source_events"]            # explains itself with provenance


def test_team_project_returns_team_standard_and_surfaces_conflict(train_engine):
    r = suggest(train_engine.profile(), VERT, "ben", "team", "caption_style")
    assert r["project_owner"] == "team_northwind"
    assert r["action"] == "stacked"
    tv = [c for c in r["conflicts"] if c["kind"] == "team_vs_private"]
    assert tv, "team vs private conflict must be surfaced, not silently resolved"
    c = tv[0]
    assert c["private"]["action"] == "beast_mode" and c["team"]["action"] == "stacked"
    assert c["private"]["source_events"] and c["team"]["source_events"]   # both sides cited
    cited = {x["chat_id"] for x in c["chat_citations"]}
    assert "chat_0014" in cited          # ben's proposal to the team is cited, not applied


def test_brand_hard_constraint_always_wins(train_engine):
    prof = train_engine.profile()
    for owner in (None, "ben"):
        r = suggest(prof, {"format": "9:16", "section": "hook"}, "ben", "team", "font", project_owner=owner)
        assert r["best_guess"] != "Bebas Neue"
        assert any(b["action"] == "Bebas Neue" for b in r["blocked"])
    r = suggest(prof, {"format": "9:16", "section": "hook"}, "ben", "private", "font")
    assert r["best_guess"] == "Bebas Neue" and r["brand_id"] is None   # no brand in a private project


def test_brand_blocks_banned_transition():
    from stylemem.brand import Brand
    b = Brand.load("northwind")
    assert b.blocks("transition", "glitch")
    assert b.blocks("font", "Comic Sans MS")
    assert b.blocks("music_mood", "dramatic_horror")
    assert b.blocks("transition", "whip_pan") is None
