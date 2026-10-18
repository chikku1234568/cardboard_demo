"""World specification: cast, brand, vocab, planted preference rules, chat
statements and planted brand violations. Pure data (no randomness).

EVERYTHING IS SYNTHETIC. Rules here are planted on purpose and are copied into
data/memory/ground_truth.json.
"""
from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
HELDOUT_LAST_N = 6
SESSIONS_PER_PROJECT = 3

CAST = {
    "people": {
        "ava": {"role": "creator", "bio": "talking-head YouTuber; lead editor on team projects"},
        "ben": {"role": "creator", "bio": "short-form creator; second editor on team projects"},
        "cara": {"role": "team_lead", "bio": "Northwind team lead; owns titles/motion and the brand"},
    },
    "agents": {"director": {"role": "agent", "bio": "Cardboard-style editing agent; every op it makes "
                                                  "is on_behalf_of the session's user"}},
    "teams": {"team_northwind": {"members": ["cara", "ava", "ben"], "lead": "cara", "brand_id": "northwind"}},
    "subjects": {"ava": "creator", "ben": "creator", "team_northwind": "team"},
}

CONTEXT_KEYS = ["footage_type", "prev_edit", "format", "section"]
VOCAB = {
    "footage_type": ["talking_head", "broll", "screen_recording", "product"],
    "prev_edit": ["jump_cut", "scene_change", "broll_insert", "none"],
    "format": ["16:9", "9:16", "1:1"],
    "section": ["hook", "body", "outro"],
}
ACTIONS = {
    "transition": ["hard_cut", "crossfade", "whip_pan", "dip_to_black", "zoom_punch", "slide", "glitch"],
    "font": ["Inter", "Montserrat", "Bebas Neue", "Playfair Display", "Roboto", "Comic Sans MS"],
    "color_grade": ["warm_film", "teal_orange", "neutral_clean", "high_contrast", "pastel"],
    "cut_length": ["short", "medium", "long"],          # short<1.5s, medium 1.5-4s, long>4s
    "caption_style": ["word_pop", "minimal_modern", "stacked", "beast_mode", "golden_serif"],
    "music_mood": ["upbeat", "chill_lofi", "cinematic", "corporate", "dramatic_horror"],
}
CUT_SECONDS = {"short": (0.6, 1.5), "medium": (1.5, 4.0), "long": (4.0, 7.5)}
# What an un-personalised Director tends to propose (its generic prior).
AGENT_PRIOR = {
    "transition": {"crossfade": 0.4, "hard_cut": 0.3, "dip_to_black": 0.1, "slide": 0.1, "zoom_punch": 0.05, "whip_pan": 0.05},
    "font": {"Inter": 0.35, "Roboto": 0.25, "Montserrat": 0.2, "Playfair Display": 0.1, "Bebas Neue": 0.1},
    "color_grade": {"neutral_clean": 0.4, "teal_orange": 0.25, "warm_film": 0.2, "high_contrast": 0.1, "pastel": 0.05},
    "cut_length": {"medium": 0.6, "short": 0.25, "long": 0.15},
    "caption_style": {"minimal_modern": 0.35, "word_pop": 0.3, "stacked": 0.2, "beast_mode": 0.1, "golden_serif": 0.05},
    "music_mood": {"upbeat": 0.35, "chill_lofi": 0.25, "corporate": 0.2, "cinematic": 0.2},
}

BRAND = {
    "brand_id": "northwind",
    "team_id": "team_northwind",
    "name": "Northwind (synthetic brand)",
    "applies_to": "Any project with brand_id == 'northwind' (all team projects, plus members' brand work). "
                  "Hard constraints: a suggestion that violates them must never be returned.",
    "version": "2026-05-15",
    "hard_constraints": {
        "fonts": {"allowed": ["Inter", "Montserrat"], "banned": ["Comic Sans MS", "Papyrus"]},
        "transitions": {"banned": ["glitch", "star_wipe"]},
        "effects": {"banned": ["vhs", "glitch", "chromatic_aberration"]},
        "music_mood": {"banned": ["dramatic_horror"]},
        "captions": {"required_on_formats": ["9:16"], "keep_out_of_safe_zone_bottom_pct": 15},
        "logo": {"asset": "northwind_logo.png", "position": "bottom_right", "required_in_sections": ["outro"]},
    },
    "palette": {"primary": "#0B3D2E", "secondary": "#F4E9D8", "accent": "#E07A2F", "text": "#FFFFFF"},
    "soft_defaults": {"color_grade": "neutral_clean", "font": "Inter"},
}

# ------------------------------------------------------------------ planted rules
# Each rule: subject, category, match (subset of context + optional scope),
# action, strength (prob the actor picks it when the rule applies), and an
# optional session window [from, to] (1-indexed session numbers of that subject).
# Rules are checked most-specific first (more match keys wins).
DRIFT_SESSION = 18
RULES = [
    # ---- creator_1 (ava): talking-head YouTuber
    dict(id="ava.tr.th_jump.old", subject="ava", category="transition",
         match={"footage_type": "talking_head", "prev_edit": "jump_cut"}, action="crossfade",
         strength=0.88, sessions=[1, DRIFT_SESSION - 1], note="pre-drift style"),
    dict(id="ava.tr.th_jump.new", subject="ava", category="transition",
         match={"footage_type": "talking_head", "prev_edit": "jump_cut"}, action="whip_pan",
         strength=0.9, sessions=[DRIFT_SESSION, None], ramp=3, ramp_old_action="crossfade",
         note="DRIFT: replaces crossfade; first 3 sessions are a ramp (~65% whip_pan)"),
    dict(id="ava.tr.broll", subject="ava", category="transition", match={"footage_type": "broll"},
         action="crossfade", strength=0.85,
         note="crossfade stays correct for b-roll after the drift (context-specific, must NOT be superseded)"),
    dict(id="ava.tr.screen", subject="ava", category="transition", match={"footage_type": "screen_recording"},
         action="hard_cut", strength=0.85),
    dict(id="ava.tr.default", subject="ava", category="transition", match={}, action="hard_cut", strength=0.7),
    dict(id="ava.cap.vertical", subject="ava", category="caption_style", match={"format": "9:16"},
         action="word_pop", strength=0.9),
    dict(id="ava.cap.default", subject="ava", category="caption_style", match={}, action="minimal_modern", strength=0.85),
    dict(id="ava.cg.th", subject="ava", category="color_grade", match={"footage_type": "talking_head"},
         action="warm_film", strength=0.9),
    dict(id="ava.cg.broll", subject="ava", category="color_grade", match={"footage_type": "broll"},
         action="teal_orange", strength=0.8),
    dict(id="ava.cg.default", subject="ava", category="color_grade", match={}, action="neutral_clean", strength=0.7),
    dict(id="ava.cut.hook", subject="ava", category="cut_length", match={"section": "hook"}, action="short", strength=0.85),
    dict(id="ava.cut.body", subject="ava", category="cut_length", match={"section": "body"}, action="medium", strength=0.8),
    dict(id="ava.cut.outro", subject="ava", category="cut_length", match={"section": "outro"}, action="long", strength=0.75),
    dict(id="ava.mus.hook", subject="ava", category="music_mood", match={"section": "hook"}, action="upbeat", strength=0.85),
    dict(id="ava.mus.outro", subject="ava", category="music_mood", match={"section": "outro"}, action="cinematic", strength=0.8),
    dict(id="ava.mus.default", subject="ava", category="music_mood", match={}, action="chill_lofi", strength=0.75),
    dict(id="ava.font", subject="ava", category="font", match={}, action="Montserrat", strength=0.92),

    # ---- creator_2 (ben): short-form, punchy
    dict(id="ben.tr.th_jump", subject="ben", category="transition",
         match={"footage_type": "talking_head", "prev_edit": "jump_cut"}, action="zoom_punch", strength=0.88),
    dict(id="ben.tr.product", subject="ben", category="transition", match={"footage_type": "product"},
         action="slide", strength=0.85),
    dict(id="ben.tr.default", subject="ben", category="transition", match={}, action="hard_cut", strength=0.75),
    dict(id="ben.cap.vertical.private", subject="ben", category="caption_style",
         match={"format": "9:16", "project_scope": "private"}, action="beast_mode", strength=0.9,
         note="CONFLICT: ben's private preference vs team rule team.cap.vertical (stacked)"),
    dict(id="ben.cap.vertical.team", subject="ben", category="caption_style",
         match={"format": "9:16", "project_scope": "team"}, action="stacked", strength=0.7, alt_action="beast_mode",
         note="CONFLICT: in team-scope projects ben mostly follows the team (stacked); the other ~30% is beast_mode"),
    dict(id="ben.cap.default", subject="ben", category="caption_style", match={}, action="stacked", strength=0.8),
    dict(id="ben.cg", subject="ben", category="color_grade", match={}, action="high_contrast", strength=0.85),
    dict(id="ben.font.private", subject="ben", category="font", match={"project_scope": "private"},
         action="Bebas Neue", strength=0.9),
    dict(id="ben.font.team", subject="ben", category="font", match={"project_scope": "team"},
         action="Montserrat", strength=0.85, note="brand-safe in team projects; a few Bebas Neue attempts are planted brand violations"),
    dict(id="ben.cut.th_body", subject="ben", category="cut_length", match={"footage_type": "talking_head", "section": "body"},
         action="medium", strength=0.8),
    dict(id="ben.cut.default", subject="ben", category="cut_length", match={}, action="short", strength=0.85),
    dict(id="ben.mus", subject="ben", category="music_mood", match={}, action="upbeat", strength=0.85),

    # ---- team_northwind: shared brand projects (actors: cara, ava, ben)
    dict(id="team.tr.th_jump", subject="team_northwind", category="transition",
         match={"footage_type": "talking_head", "prev_edit": "jump_cut"}, action="hard_cut", strength=0.85),
    dict(id="team.tr.default", subject="team_northwind", category="transition", match={}, action="crossfade", strength=0.75),
    dict(id="team.cap.vertical", subject="team_northwind", category="caption_style", match={"format": "9:16"},
         action="stacked", strength=0.92, note="CONFLICT counterpart of ben.cap.vertical.private"),
    dict(id="team.cap.default", subject="team_northwind", category="caption_style", match={}, action="minimal_modern", strength=0.85),
    dict(id="team.font", subject="team_northwind", category="font", match={}, action="Inter", strength=0.92),
    dict(id="team.cg", subject="team_northwind", category="color_grade", match={}, action="neutral_clean", strength=0.88),
    dict(id="team.cut", subject="team_northwind", category="cut_length", match={}, action="medium", strength=0.8),
    dict(id="team.mus.outro", subject="team_northwind", category="music_mood", match={"section": "outro"},
         action="upbeat", strength=0.8),
    dict(id="team.mus.default", subject="team_northwind", category="music_mood", match={}, action="corporate", strength=0.85),
]

SUBJECTS = {
    "ava": dict(kind="creator", sessions=36, team="team_northwind", brand_share=0.25,
                formats={"16:9": 0.55, "9:16": 0.35, "1:1": 0.10},
                footage={"talking_head": 0.55, "broll": 0.3, "screen_recording": 0.1, "product": 0.05}),
    "ben": dict(kind="creator", sessions=34, team="team_northwind", brand_share=0.3,
                formats={"9:16": 0.7, "16:9": 0.2, "1:1": 0.1},
                footage={"talking_head": 0.45, "product": 0.3, "broll": 0.2, "screen_recording": 0.05}),
    "team_northwind": dict(kind="team", sessions=32, members=["cara", "ava", "ben"],
                           formats={"16:9": 0.45, "9:16": 0.45, "1:1": 0.1},
                           footage={"talking_head": 0.45, "broll": 0.25, "product": 0.2, "screen_recording": 0.1}),
}

# Explicit chat statements: (subject, session_no, actor, text, refers_to_rule or None)
STATEMENTS = [
    ("ava", 2, "ava", "Always use Montserrat for my titles.", "ava.font"),
    ("ava", 5, "ava", "Keep my face shots warm, like film. No cold grades on me.", "ava.cg.th"),
    ("ava", 9, "ava", "For vertical shorts I like the word pop captions.", "ava.cap.vertical"),
    ("ava", 14, "ava", "The render is slow today, ugh.", None),
    ("ava", 21, "ava", "I've been into whip pans after jump cuts lately, keep doing that.", "ava.tr.th_jump.new"),
    ("ava", 27, "ava", "Make the intro snappier.", None),
    ("ben", 3, "ben", "Bebas Neue on everything for my channel.", "ben.font.private"),
    ("ben", 6, "ben", "For my own shorts I prefer beast mode captions, big and loud.", "ben.cap.vertical.private"),
    ("ben", 15, "ben", "Can we try something different this time?", None),
    ("ben", 22, "ben", "Zoom punch on jump cuts, always.", "ben.tr.th_jump"),
    ("team_northwind", 1, "cara", "Team standard: stacked captions on all vertical shorts.", "team.cap.vertical"),
    ("team_northwind", 1, "cara", "Brand font is Inter for anything Northwind.", "team.font"),
    ("team_northwind", 8, "cara", "Hard cuts on jump cuts for team videos, no fancy transitions.", "team.tr.th_jump"),
    ("team_northwind", 19, "ben", "Honestly beast mode captions would do better on our shorts.", "ben.cap.vertical.private"),
    ("team_northwind", 20, "cara", "Sticking with stacked captions for team shorts, that's the standard.", "team.cap.vertical"),
]

# Planted brand-violating attempts: (subject, session_no, who, category, action)
# who = 'agent' -> Director proposes it and the user overrides;
# who = 'user'  -> the user themselves picks it (memory must still never suggest it for brand projects).
BRAND_VIOLATIONS = [
    ("team_northwind", 4, "agent", "font", "Comic Sans MS"),
    ("team_northwind", 12, "agent", "transition", "glitch"),
    ("ava", 11, "agent", "transition", "glitch"),          # ava session 11 is forced to be a brand project
    ("ben", 9, "user", "font", "Bebas Neue"),              # ben session 9 forced brand project
    ("ben", 25, "user", "font", "Bebas Neue"),             # ben session 25 forced brand project
    ("team_northwind", 26, "agent", "music_mood", "dramatic_horror"),
]
FORCED_BRAND = {("ava", 11), ("ben", 9), ("ben", 25)}

NOISE = {"off_rule_choice": "1 - rule.strength (uniform over non-banned alternatives)", "agent_matches_pref": 0.30, "undo_then_override": 0.25,
         "undo_no_replacement": 0.03}

def rules_for(subject):
    return [r for r in RULES if r["subject"] == subject]


def applicable_rule(subject, category, ctx, scope, sess_no):
    full = {**ctx, "project_scope": scope}
    best = None
    for r in rules_for(subject):
        if r["category"] != category:
            continue
        lo, hi = (r.get("sessions") or [None, None])
        if lo and sess_no < lo or hi and sess_no > hi:
            continue
        if all(full.get(k) == v for k, v in r["match"].items()):
            if best is None or len(r["match"]) > len(best["match"]):
                best = r
    return best

