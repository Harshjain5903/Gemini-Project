"""
Walking Buddy – Gemini-powered conversational walking companion.

Provides a friendly chat experience that naturally integrates
navigation directions and safety alerts from the geospatial risk data.
"""
import json
import math
import os
from pathlib import Path
from typing import Optional

import h3

try:
    from google import genai
except ImportError:
    genai = None


# ── Helpers ──────────────────────────────────────────────────────────────

def _haversine(lat1, lng1, lat2, lng2):
    """Distance in metres between two lat/lng pairs."""
    R = 6_371_000
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(lat1, lng1, lat2, lng2):
    """Compass bearing from point 1 to point 2."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlng = math.radians(lng2 - lng1)
    x = math.sin(dlng) * math.cos(rlat2)
    y = (math.cos(rlat1) * math.sin(rlat2)
         - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlng))
    deg = (math.degrees(math.atan2(x, y)) + 360) % 360
    dirs = ["north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest"]
    return dirs[int((deg + 22.5) / 45) % 8]


def _h3_line(origin_cell: str, dest_cell: str) -> list[str]:
    """Return an ordered list of H3 cells forming a path."""
    try:
        return h3.grid_path_cells(origin_cell, dest_cell)
    except Exception:
        return [origin_cell, dest_cell]


# ── Risk loader ──────────────────────────────────────────────────────────

class RiskMap:
    """Thin wrapper around the routing_risk_api.json export."""

    def __init__(self, risk_file: str | Path):
        path = Path(risk_file)
        if not path.exists():
            raise FileNotFoundError(f"Risk data not found: {path}")
        with open(path) as f:
            data = json.load(f)
        self.cells: dict = data.get("cells", {})
        self.resolution: int = data.get("metadata", {}).get("h3_resolution", 9)

    def risk_for(self, cell_id: str) -> dict:
        return self.cells.get(cell_id, {})

    def walking_risk(self, cell_id: str) -> float:
        """Combined walking risk: 70 % crime + 30 % crash."""
        c = self.risk_for(cell_id)
        if not c:
            return 0.0
        crime = c.get("smoothed_crime_risk", c.get("crime_risk", 0))
        crash = c.get("smoothed_risk", c.get("base_risk", 0))
        return round(crime * 0.7 + crash * 0.3, 2)

    def risk_label(self, score: float) -> str:
        if score >= 70:
            return "high-risk"
        if score >= 40:
            return "moderate-risk"
        return "low-risk"


# ── Route ────────────────────────────────────────────────────────────────

class Route:
    """A planned walking route as an ordered list of H3 cells."""

    def __init__(self, cells: list[str], risk_map: RiskMap):
        self.cells = cells
        self.risk_map = risk_map
        self.current_idx = 0

    @property
    def total_cells(self) -> int:
        return len(self.cells)

    @property
    def current_cell(self) -> str:
        return self.cells[min(self.current_idx, len(self.cells) - 1)]

    @property
    def finished(self) -> bool:
        return self.current_idx >= len(self.cells) - 1

    def advance(self) -> dict | None:
        """Move one step. Returns a nav event dict or None."""
        if self.finished:
            return {"type": "arrived", "message": "You've arrived at your destination!"}

        prev = self.cells[self.current_idx]
        self.current_idx += 1
        curr = self.cells[self.current_idx]

        events = []

        # Direction change?
        if self.current_idx + 1 < len(self.cells):
            nxt = self.cells[self.current_idx + 1]
            lat_c, lng_c = h3.cell_to_latlng(curr)
            lat_n, lng_n = h3.cell_to_latlng(nxt)
            lat_p, lng_p = h3.cell_to_latlng(prev)
            old_dir = _bearing(lat_p, lng_p, lat_c, lng_c)
            new_dir = _bearing(lat_c, lng_c, lat_n, lng_n)
            if old_dir != new_dir:
                events.append({
                    "type": "turn",
                    "direction": new_dir,
                    "message": f"Turn coming up — head {new_dir}."
                })

        # Risk change?
        risk = self.risk_map.walking_risk(curr)
        label = self.risk_map.risk_label(risk)
        if label == "high-risk":
            events.append({
                "type": "danger",
                "risk_score": risk,
                "message": "Heads up — you're entering a higher-risk area. Stay alert and stick to well-lit paths."
            })

        # Progress milestone
        pct = int(self.current_idx / (len(self.cells) - 1) * 100)
        if pct in (25, 50, 75) and pct == int((self.current_idx) / (len(self.cells) - 1) * 100):
            events.append({
                "type": "progress",
                "percent": pct,
                "message": f"You're about {pct}% of the way there."
            })

        if not events:
            return None
        return events[0] if len(events) == 1 else {
            "type": "multi",
            "events": events,
            "message": " ".join(e["message"] for e in events)
        }

    def summary(self) -> str:
        """Human-readable route summary."""
        risks = [self.risk_map.walking_risk(c) for c in self.cells]
        avg = sum(risks) / len(risks) if risks else 0
        high = sum(1 for r in risks if r >= 70)
        est_m = len(self.cells) * 150  # rough estimate ~150m per cell at res 9
        est_min = max(1, round(est_m / 80))  # ~80 m/min walking
        parts = [
            f"Route: {len(self.cells)} segments, ~{est_m}m (~{est_min} min walk).",
            f"Average safety score: {avg:.0f}/100 ({self.risk_map.risk_label(avg)}).",
        ]
        if high:
            parts.append(f"{high} segment(s) are high-risk — I'll warn you when we get there.")
        else:
            parts.append("No high-risk segments — looking good!")
        return " ".join(parts)


# ── Conversation state machine ───────────────────────────────────────────

STATE_IDLE = "idle"
STATE_ROUTE_PLANNED = "route_planned"
STATE_GUIDING = "guiding"


# ── WalkingBuddy ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are WalkingBuddy, a friendly and street-smart walking companion in New York City.
You chat naturally — like a good friend walking alongside the user.

Rules:
1. Keep replies SHORT (1-3 sentences) unless the user asks for detail.
2. Be warm, casual, and encouraging. Use simple language.
3. When NAV_EVENT context is provided, ALWAYS weave the navigation info
   into your reply FIRST before continuing the conversation. Treat it
   like you're naturally interrupting: "Oh hey — turn left up ahead!" then
   continue chatting.
4. When ROUTE_SUMMARY context is provided, share the route details in
   a friendly way and ask if the user wants to start walking.
5. Never invent risk data or directions you weren't given.
6. If the user provides an origin and destination, acknowledge it and
   say you've planned a route (the system will supply ROUTE_SUMMARY).
"""


class WalkingBuddy:
    """Gemini-powered conversational walking companion."""

    def __init__(
        self,
        risk_file: str | Path = "output/routing_risk_api.json",
        api_key: str | None = None,
        resolution: int = 9,
    ):
        if genai is None:
            raise ImportError(
                "google-genai package is required. "
                "Install it with: pip install google-genai"
            )

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise ValueError(
                "Gemini API key required. Set GEMINI_API_KEY env var "
                "or pass api_key= to WalkingBuddy."
            )

        self.client = genai.Client(api_key=key)
        self.model = "gemini-2.0-flash"
        self.resolution = resolution

        # Load risk data
        risk_path = Path(risk_file)
        if risk_path.exists():
            self.risk_map = RiskMap(risk_path)
            self.resolution = self.risk_map.resolution
        else:
            self.risk_map = None

        # Conversation state
        self.state = STATE_IDLE
        self.route: Optional[Route] = None
        self.history: list[dict] = []
        self._step_counter = 0

    # ── Route planning ───────────────────────────────────────────────

    def plan_route(self, origin_lat: float, origin_lng: float,
                   dest_lat: float, dest_lng: float) -> Route:
        """Plan a walking route between two coordinates."""
        origin_cell = h3.latlng_to_cell(origin_lat, origin_lng, self.resolution)
        dest_cell = h3.latlng_to_cell(dest_lat, dest_lng, self.resolution)
        cells = _h3_line(origin_cell, dest_cell)

        if self.risk_map is None:
            # No risk data — still provide the route
            self.risk_map = _DummyRiskMap()

        self.route = Route(cells, self.risk_map)
        self.state = STATE_ROUTE_PLANNED
        return self.route

    # ── Main chat method ─────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        """
        Send a message and get a response.
        Handles navigation events automatically during guiding.
        """
        # Check if user wants to start guiding
        if self.state == STATE_ROUTE_PLANNED and _wants_to_start(user_message):
            self.state = STATE_GUIDING
            self._step_counter = 0

        # Build context injection
        context_parts = []

        # During guiding, simulate a step and check for nav events
        if self.state == STATE_GUIDING and self.route and not self.route.finished:
            self._step_counter += 1
            event = self.route.advance()
            if event:
                context_parts.append(f"[NAV_EVENT: {event['message']}]")
            if self.route.finished:
                context_parts.append("[NAV_EVENT: You've arrived at your destination!]")
                self.state = STATE_IDLE

        # If we just planned a route, inject the summary
        if self.state == STATE_ROUTE_PLANNED and self.route:
            context_parts.append(f"[ROUTE_SUMMARY: {self.route.summary()}]")

        # Compose the full prompt for Gemini
        context_block = "\n".join(context_parts)
        augmented_message = (
            f"{context_block}\n\nUser: {user_message}" if context_block
            else user_message
        )

        # Build message history for Gemini
        self.history.append({"role": "user", "parts": [{"text": augmented_message}]})

        response = self.client.models.generate_content(
            model=self.model,
            contents=[{"role": "user", "parts": [{"text": SYSTEM_PROMPT}]}] + self.history,
        )

        reply = response.text.strip()
        self.history.append({"role": "model", "parts": [{"text": reply}]})

        return reply

    def get_status(self) -> dict:
        """Current buddy status for UI display."""
        status = {"state": self.state}
        if self.route:
            status["progress"] = f"{self.route.current_idx}/{self.route.total_cells}"
            status["current_cell"] = self.route.current_cell
            if self.risk_map:
                risk = self.risk_map.walking_risk(self.route.current_cell)
                status["current_risk"] = risk
                status["risk_label"] = self.risk_map.risk_label(risk)
        return status


# ── Helpers ──────────────────────────────────────────────────────────────

def _wants_to_start(msg: str) -> bool:
    """Detect if the user wants to start walking / get guided."""
    triggers = [
        "guide me", "let's go", "lets go", "start walking", "start",
        "lead the way", "ok let's go", "okay let's go", "yeah let's go",
        "walk", "go ahead", "ready", "sure", "yes", "yep", "yea", "yeah",
        "ok", "okay"
    ]
    lower = msg.lower().strip()
    return any(t in lower for t in triggers)


class _DummyRiskMap:
    """Fallback when no risk file is available."""
    resolution = 9

    def risk_for(self, cell_id):
        return {}

    def walking_risk(self, cell_id):
        return 0.0

    def risk_label(self, score):
        return "unknown"
