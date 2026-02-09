from google import genai
from google.genai import types
import json
import os
import struct
import io
from datetime import datetime


def _pcm_to_wav(pcm_data, sample_rate=24000, channels=1, sample_width=2):
    """Wrap raw PCM data in a WAV header for browser playback."""
    buf = io.BytesIO()
    data_size = len(pcm_data)
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', 36 + data_size))
    buf.write(b'WAVE')
    buf.write(b'fmt ')
    buf.write(struct.pack('<I', 16))
    buf.write(struct.pack('<H', 1))  # PCM format
    buf.write(struct.pack('<H', channels))
    buf.write(struct.pack('<I', sample_rate))
    buf.write(struct.pack('<I', sample_rate * channels * sample_width))
    buf.write(struct.pack('<H', channels * sample_width))
    buf.write(struct.pack('<H', sample_width * 8))
    buf.write(b'data')
    buf.write(struct.pack('<I', data_size))
    buf.write(pcm_data)
    return buf.getvalue()


# The one personality that drives every response
BUDDY_SYSTEM = """\
You are the user's walking buddy in Chicago. Your name is SafePath Buddy.
You're like a street-smart best friend who grew up in Chicago — you know every block,
every shortcut, the L train lines, and which neighborhoods get sketchy after dark.

HOW YOU TALK:
- Like a real person on a phone call, NOT like an assistant or AI.
- Short sentences. Casual. Warm. Sometimes funny.
- Use filler words naturally: "so", "like", "honestly", "oh wait", "you know what".
- React to what the user says — "whoa that's far!", "oh nice area", "yeah that part can get iffy at night".
- Have opinions: "honestly I'd skip that street", "oh you gotta check out that area though".
- When giving directions/safety info, weave it into the conversation naturally.
  Don't use bullet points or headers. Just talk.
- If the user is chatting about random stuff, chat back! You're a friend first, navigator second.
- If the user sounds nervous, be reassuring but honest.
- If the user sounds excited, match their energy.

WHAT YOU KNOW:
- Chicago streets, neighborhoods, landmarks, vibes at different hours.
- Neighborhoods: the Loop, River North, Magnificent Mile, Wicker Park, Lincoln Park,
  Lakeview, Hyde Park, Pilsen, Logan Square, South Loop, Old Town, Gold Coast, etc.
- The L train lines: Red, Blue, Brown, Green, Orange, Pink, Purple.
- Safety patterns: which areas are riskier at night, where crashes happen more, etc.
- You have access to real crash + crime data for Chicago (the system feeds you the numbers).

WHAT YOU DON'T DO:
- Never say "I'm an AI", "as a language model", "I don't have feelings", etc.
- Never use markdown headers like **Bold Header** in spoken conversation.
- Never give disclaimers about not being a real person.
- Never list things with bullet points — just talk naturally.
- Never be overly formal or corporate-sounding.
- Keep responses SHORT. 2-3 sentences for casual chat, 4-5 max for route info. You're on a phone call, not writing an essay.
"""


class GeminiService:
    def __init__(self):
        api_key = os.environ.get('GEMINI_API_KEY')
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-3-flash-preview"
        self.tts_model = "gemini-2.5-flash-preview-tts"  # No Gemini 3 TTS yet
        self.tts_voice = "Kore"
        # Shared conversation memory across ALL interactions
        self.conversation_history = []

    def _add_to_history(self, role, text):
        """Track conversation so the buddy remembers everything."""
        self.conversation_history.append({
            "role": role,
            "parts": [{"text": text}]
        })
        # Keep last 10 exchanges (20 messages) — shorter history = faster API calls
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]

    def _chat_with_context(self, user_text, extra_context=None, update_history=True):
        """Core chat method — all buddy responses go through here for consistent personality + memory.
        If update_history=False, reads history for context but doesn't modify it (for speculative parallel calls)."""
        if extra_context:
            augmented = f"[SYSTEM CONTEXT — use this info naturally in your reply, don't read it out literally]\n{extra_context}\n\n{user_text}"
        else:
            augmented = user_text

        # Build contents: system prompt + existing history + this message
        contents = ([{"role": "user", "parts": [{"text": BUDDY_SYSTEM}]}]
                     + self.conversation_history
                     + [{"role": "user", "parts": [{"text": augmented}]}])

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
            )
            reply = response.text.strip()
            if update_history:
                self._add_to_history("user", augmented)
                self._add_to_history("model", reply)
            else:
                # Store for later commit via commit_pending_exchange()
                self._pending_exchange = (augmented, reply)
            return reply
        except Exception as e:
            print(f"Buddy chat error: {e}")
            return "Hey sorry, my brain glitched for a sec. What were you saying?"

    def commit_pending_exchange(self):
        """Commit the last dry-run exchange to conversation history."""
        if hasattr(self, '_pending_exchange') and self._pending_exchange:
            user_text, model_reply = self._pending_exchange
            self._add_to_history("user", user_text)
            self._add_to_history("model", model_reply)
            self._pending_exchange = None

    def parse_route_request(self, user_message, user_hour=None):
        """Extract structured routing parameters from natural language.
        This is a separate non-conversational call — just JSON extraction."""
        current_hour = user_hour if user_hour is not None else datetime.now().hour

        prompt = f"""You are a routing assistant. Extract structured routing parameters from this message.

Return ONLY valid JSON with this schema:
{{
  "start_name": string or null,
  "end_name": string or null,
  "hour": integer 0-23,
  "is_weekend": boolean,
  "beta": float 0-10,
  "travel_mode": string,
  "travel_mode_explicit": boolean,
  "context": string or null
}}

Rules:
- Both start_name and end_name are REQUIRED. If one is missing, set to null.
- IMPORTANT: If the user only mentions ONE place (e.g. "I want to go to Navy Pier", "take me to Wrigley Field", "how do I get to the Bean"), assume they want to go FROM their current location. Set start_name to "my current location" and end_name to the place they mentioned.
- Recognize Chicago landmarks, addresses, cross-streets, neighborhoods.
- For start_name and end_name, use the FULL official name of the place (e.g. "Navy Pier" not "the pier", "Millennium Park" not "the park", "Willis Tower" not "the tower"). Be specific.
- If user says "here", "my location", "current location", "where I am", "my position", set that to "my current location" exactly.
- Detect travel mode from context: "drive"/"driving"/"car" -> "driving", "bike"/"cycle"/"cycling" -> "cycling", "walk"/"walking"/"on foot" -> "walking".
- travel_mode_explicit: set to true ONLY if the user explicitly mentions a travel mode word (walk, walking, drive, driving, car, bike, biking, cycle, cycling, on foot). Set to false if you are defaulting because they did NOT mention any travel mode.
- Time: "11 PM" -> 23, "morning" -> 8, "evening" -> 19, "midnight" -> 0, "rush hour" -> 17. Default: {current_hour}.
- Weekend: "Saturday"/"Sunday"/"weekend" -> true. Default false.
- Beta (safety priority): "alone at night" -> 8, "fastest" -> 2, "safest" -> 10. Default 5.
- Context: any safety-relevant info ("solo", "with kids", "late night", "carrying valuables", "raining", "bad weather").
- travel_mode: "walking", "cycling", or "driving". Default "walking".
- Return ONLY JSON, no markdown, no explanation.

MESSAGE: {user_message}"""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            parsed = json.loads(text)

            # Return partial parse (with nulls) so the caller can auto-fill from user location
            if not parsed.get('start_name') and not parsed.get('end_name'):
                return None

            parsed['hour'] = max(0, min(23, int(parsed.get('hour', current_hour))))
            parsed['beta'] = max(0.0, min(10.0, float(parsed.get('beta', 5.0))))
            parsed['is_weekend'] = bool(parsed.get('is_weekend', False))
            parsed['travel_mode'] = parsed.get('travel_mode', 'walking')
            parsed['travel_mode_explicit'] = bool(parsed.get('travel_mode_explicit', False))
            parsed['context'] = parsed.get('context', None)

            return parsed
        except (json.JSONDecodeError, Exception) as e:
            print(f"Gemini parse error: {e}")
            return None

    def generate_route_summary(self, parsed, metrics, hourly_multiplier=1.0,
                                fastest_coords=None, safest_coords=None,
                                weather_context=None):
        """Generate a buddy-style conversational summary of the route results.
        This replaces both the old ai_summary template AND the formal safety briefing."""

        def fmt_time(seconds):
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            if mins > 0 and secs > 0:
                return f"{mins} min {secs} sec"
            if mins > 0:
                return f"{mins} min"
            return f"{secs} seconds"

        def format_hour(h):
            if h == 0: return "midnight"
            if h == 12: return "noon"
            if h < 12: return f"{h} AM"
            return f"{h - 12} PM"

        def sample_waypoints(coords, n=5):
            if not coords or len(coords) < 2:
                return []
            step = max(1, len(coords) // (n + 1))
            return [coords[i] for i in range(step, len(coords) - 1, step)][:n]

        if hourly_multiplier > 1.5:
            risk_vibe = "it's a higher-risk time of day"
        elif hourly_multiplier > 1.2:
            risk_vibe = "risk is a bit elevated right now"
        elif hourly_multiplier > 0.8:
            risk_vibe = "it's pretty average risk-wise"
        else:
            risk_vibe = "it's actually a pretty safe time to be out"

        hour = parsed.get('hour', 12)
        hour_label = format_hour(hour)
        day_type = "weekend" if parsed.get('is_weekend') else "weekday"

        fastest_wp = sample_waypoints(fastest_coords) if fastest_coords else []
        safest_wp = sample_waypoints(safest_coords) if safest_coords else []
        fastest_wp_str = ", ".join([f"({c[0]:.4f}, {c[1]:.4f})" for c in fastest_wp]) if fastest_wp else "N/A"
        safest_wp_str = ", ".join([f"({c[0]:.4f}, {c[1]:.4f})" for c in safest_wp]) if safest_wp else "N/A"

        route_context = f"""ROUTE RESULTS just calculated for the user:
- Going from: {parsed['start_name']} to {parsed['end_name']}
- Time: {hour_label} on a {day_type}
- Travel mode: {parsed.get('travel_mode', 'walking')}
- User's situation: {parsed.get('context', 'just walking around')}

FASTEST route: {fmt_time(metrics['fastest']['total_time'])}, risk score {round(metrics['fastest']['total_risk'], 1)}
  Waypoints: {fastest_wp_str}

SAFEST route: {fmt_time(metrics['safest']['total_time'])}, risk score {round(metrics['safest']['total_risk'], 1)}
  Waypoints: {safest_wp_str}

Taking the safer route: {metrics['reduction_in_risk_pct']}% less risk, only {fmt_time(metrics['extra_time_seconds'])} extra time.
Time risk context: {risk_vibe} (multiplier: {round(hourly_multiplier, 2)}x).
Weather: {weather_context or 'No weather data available'}

Tell the user about these routes like a friend would — mention the neighborhoods/streets each route goes through
(use the waypoint coordinates to figure out which Chicago streets and areas they pass through),
which one you'd recommend and why, and any tips for this time of day.
If the weather is bad (rain, snow, ice, storm), DEFINITELY mention it and how it affects their trip.
If the weather is nice, you can briefly mention it positively.
Keep it conversational — like you're on a phone call with them, not writing a report.
No bullet points, no headers. Just talk naturally. Around 4-6 sentences."""

        return self._chat_with_context(
            f"I wanna go from {parsed['start_name']} to {parsed['end_name']}",
            extra_context=route_context
        )

    def generate_safety_briefing(self, parsed, metrics, hourly_multiplier=1.0,
                                  fastest_coords=None, safest_coords=None,
                                  weather_context=None):
        """Generate a brief safety card for the UI (displayed as text, not spoken).
        Kept shorter and structured since this shows in the chat as a card."""

        def fmt_time(seconds):
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            if mins > 0:
                return f"{mins} min {secs} sec"
            return f"{secs} seconds"

        def format_hour(h):
            if h == 0: return "12 AM"
            if h == 12: return "12 PM"
            if h < 12: return f"{h} AM"
            return f"{h - 12} PM"

        def sample_waypoints(coords, n=5):
            if not coords or len(coords) < 2:
                return []
            step = max(1, len(coords) // (n + 1))
            return [coords[i] for i in range(step, len(coords) - 1, step)][:n]

        if hourly_multiplier > 1.5:
            risk_level = "high"
        elif hourly_multiplier > 1.2:
            risk_level = "elevated"
        elif hourly_multiplier > 0.8:
            risk_level = "moderate"
        else:
            risk_level = "low"

        hour_label = format_hour(parsed.get('hour', 12))
        day_type = "weekend" if parsed.get('is_weekend') else "weekday"

        fastest_wp = sample_waypoints(fastest_coords) if fastest_coords else []
        safest_wp = sample_waypoints(safest_coords) if safest_coords else []
        fastest_wp_str = ", ".join([f"({c[0]:.4f}, {c[1]:.4f})" for c in fastest_wp]) if fastest_wp else "N/A"
        safest_wp_str = ", ".join([f"({c[0]:.4f}, {c[1]:.4f})" for c in safest_wp]) if safest_wp else "N/A"

        weather_str = weather_context or "No weather data"

        prompt = f"""Write a short safety info card for a route in Chicago.

From: {parsed['start_name']} to {parsed['end_name']}
Time: {hour_label} ({day_type}), risk level: {risk_level}
Mode: {parsed.get('travel_mode', 'walking')}
Context: {parsed.get('context', 'none')}
Weather: {weather_str}

Fastest: {fmt_time(metrics['fastest']['total_time'])}, risk {round(metrics['fastest']['total_risk'], 1)}
  Waypoints: {fastest_wp_str}
Safest: {fmt_time(metrics['safest']['total_time'])}, risk {round(metrics['safest']['total_risk'], 1)}
  Waypoints: {safest_wp_str}
Safer route saves {metrics['reduction_in_risk_pct']}% risk for {fmt_time(metrics['extra_time_seconds'])} extra.

Use these exact **bold headers**, keep each section to 1-2 sentences, under 150 words total:

**Risk Summary** (include weather impact if relevant)
**Fastest Route** (identify streets/neighborhoods from waypoint coordinates)
**Recommended Safe Route** (identify streets/neighborhoods from waypoint coordinates)
**Weather & Safety Tips** (2-3 specific tips considering weather, time, and travel mode)

Tone: confident local friend, not formal."""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            return response.text.strip()
        except Exception as e:
            print(f"Gemini briefing error: {e}")
            return (f"The safer route cuts risk by {metrics['reduction_in_risk_pct']}% "
                    f"for just {fmt_time(metrics['extra_time_seconds'])} extra. "
                    f"Risk right now: {risk_level}.")

    def chat_reply(self, user_message, nav_state=None, weather_context=None, update_history=True):
        """Handle general conversation — goes through the shared buddy personality.
        If nav_state is provided, the buddy knows where the user is on their route.
        If update_history=False, runs speculatively without modifying history."""
        nav_context = None
        if weather_context:
            nav_context = f"Current weather in Chicago: {weather_context}"
        if nav_state and nav_state.get('is_navigating'):
            parts = ["You're currently guiding the user on a walking route."]
            if nav_state.get('next_turn'):
                parts.append(f"Next turn: {nav_state['next_turn']}")
                if nav_state.get('next_turn_dist'):
                    parts.append(f"(about {nav_state['next_turn_dist']}m ahead)")
            if nav_state.get('dest_dist'):
                parts.append(f"Distance to destination: ~{nav_state['dest_dist']}m")
            parts.append(
                "You're chatting with the user while guiding them. "
                "If there's a turn coming up soon (under 80m), naturally mention it — "
                "like 'oh hold on, you've got a left turn coming up' — then continue the conversation. "
                "If the turn is far away, just chat normally and don't mention navigation unless asked."
            )
            nav_context = " ".join(parts)
        return self._chat_with_context(user_message, extra_context=nav_context, update_history=update_history)

    def text_to_speech(self, text, voice=None):
        """Convert text to natural speech using Gemini's audio generation."""
        voice = voice or self.tts_voice
        try:
            response = self.client.models.generate_content(
                model=self.tts_model,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    )
                )
            )

            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    audio_data = part.inline_data.data
                    mime_type = part.inline_data.mime_type

                    # If raw PCM, wrap in WAV header for browser playback
                    if mime_type and ('pcm' in mime_type.lower() or 'l16' in mime_type.lower()):
                        audio_data = _pcm_to_wav(audio_data)
                        mime_type = 'audio/wav'
                    elif not mime_type:
                        audio_data = _pcm_to_wav(audio_data)
                        mime_type = 'audio/wav'

                    return audio_data, mime_type

            return None, None
        except Exception as e:
            print(f"TTS error: {e}")
            return None, None

    def get_fallback_message(self, user_message):
        return ("I couldn't identify both a start and end location from your message. "
                "Try something like: \"Walk from Millennium Park to Navy Pier at 11 PM\" "
                "or \"Willis Tower to Wrigley Field, morning rush hour\"")
