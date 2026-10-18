"""Brand guidelines as hard constraints (always a filter, never a soft score)."""
from __future__ import annotations

from dataclasses import dataclass

from . import data


@dataclass
class Brand:
    brand_id: str
    hard: dict
    soft_defaults: dict

    @classmethod
    def load(cls, brand_id: str) -> "Brand | None":
        g = data.brand_guidelines()
        if g["brand_id"] != brand_id:
            return None
        return cls(brand_id, g["hard_constraints"], g.get("soft_defaults", {}))

    def blocks(self, category: str, action: str) -> str | None:
        """Return the rule that blocks (category, action), or None if allowed."""
        h = self.hard
        if category == "font":
            if action in h["fonts"].get("banned", []):
                return f"{self.brand_id}.fonts.banned contains {action}"
            allowed = h["fonts"].get("allowed")
            if allowed and action not in allowed:
                return f"{self.brand_id}.fonts.allowed = {allowed}; {action} is not allowed"
        if category == "transition":
            if action in h["transitions"].get("banned", []):
                return f"{self.brand_id}.transitions.banned contains {action}"
            if action in h["effects"].get("banned", []):
                return f"{self.brand_id}.effects.banned contains {action}"
        if category == "music_mood" and action in h["music_mood"].get("banned", []):
            return f"{self.brand_id}.music_mood.banned contains {action}"
        if category in ("effect", "color_grade") and action in h["effects"].get("banned", []):
            return f"{self.brand_id}.effects.banned contains {action}"
        return None

    def requirements(self, fmt: str | None, section: str | None) -> list[str]:
        out = []
        if fmt and fmt in self.hard["captions"].get("required_on_formats", []):
            out.append(f"captions required on {fmt} ({self.brand_id}.captions.required_on_formats)")
        if section and section in self.hard["logo"].get("required_in_sections", []):
            out.append(f"logo {self.hard['logo']['asset']} required in {section} at {self.hard['logo']['position']}")
        return out

    def constraint_rows(self) -> list[dict]:
        """Brand hard constraints as Preference-like rows (scope=brand) for the graph."""
        h = self.hard
        rows = []
        for f in h["fonts"].get("allowed", []):
            rows.append(("font", f, "allow"))
        for f in h["fonts"].get("banned", []):
            rows.append(("font", f, "ban"))
        for t in h["transitions"].get("banned", []):
            rows.append(("transition", t, "ban"))
        for t in h["effects"].get("banned", []):
            rows.append(("effect", t, "ban"))
        for m in h["music_mood"].get("banned", []):
            rows.append(("music_mood", m, "ban"))
        out = [{"pref_id": f"{self.brand_id}/brand/{c}/*/{a}#{pol}", "category": c, "action": a, "polarity": pol,
                "hard": True} for c, a, pol in rows]
        for c, a in self.soft_defaults.items():
            out.append({"pref_id": f"{self.brand_id}/brand/{c}/*/{a}#default", "category": c, "action": a,
                        "polarity": "prefer", "hard": False})
        return out
