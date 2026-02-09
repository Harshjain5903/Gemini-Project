from flask import Flask, Blueprint, request, jsonify, Response, send_from_directory
from routing_engine import RoutingEngine
from gemini_service import GeminiService
from weather_service import WeatherService
from flask_cors import CORS
from dotenv import load_dotenv
import json
import copy
import os
import base64
import requests as http_requests
from concurrent.futures import ThreadPoolExecutor

load_dotenv()

app = Flask(__name__)
CORS(app)
api = Blueprint('api', __name__)

# Init Engine (Chicago) — uses cached graph for fast startup
GRAPH_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output_chicago', 'chicago_graph.graphml')
engine = RoutingEngine("Chicago, Illinois, USA", cache_path=GRAPH_CACHE)
engine.load_risk_api("../output_chicago/routing_risk_api.json")

# Load static data for dynamic heatmap
with open("../output_chicago/grid_risk.geojson", 'r') as f:
    base_heatmap = json.load(f)
with open("../output_chicago/time_patterns.json", 'r') as f:
    time_patterns = json.load(f)

# Create hourly multiplier lookup
hourly_multipliers = {h['hour']: h['risk_multiplier'] for h in time_patterns['hourly']}

# Init Gemini AI service
gemini_svc = None
if os.environ.get('GEMINI_API_KEY'):
    gemini_svc = GeminiService()
    print("Gemini AI service initialized.")
else:
    print("WARNING: GEMINI_API_KEY not set. /chat endpoint will not work.")

# Init Weather service (Chicago coordinates)
weather_svc = WeatherService(lat=41.8781, lng=-87.6298)
print(f"Weather: {weather_svc.get_weather()['current']['description']}")

# Phrases that mean "use my GPS location"
MY_LOCATION_PHRASES = {
    'my location', 'my current location', 'current location',
    'here', 'where i am', 'my position', 'my place',
}

# Mapbox geocoding (replaces slow osmnx — ~200ms vs 3-5s)
MAPBOX_TOKEN = os.environ.get('MAPBOX_TOKEN', '')
_geocode_cache = {}

def geocode_place(place_name, user_coords=None):
    """Convert a place name to [lat, lng] using Mapbox Geocoding API."""
    normalized = place_name.strip().lower()
    if normalized in _geocode_cache:
        return _geocode_cache[normalized]
    query = f"{place_name}, Chicago, Illinois"
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{http_requests.utils.quote(query)}.json"
    # Use user's location for proximity bias (avoids defaulting to the Loop)
    if user_coords and len(user_coords) == 2:
        proximity = f'{user_coords[1]},{user_coords[0]}'
    else:
        proximity = '-87.6298,41.8781'
    resp = http_requests.get(url, params={
        'access_token': MAPBOX_TOKEN,
        'proximity': proximity,
        'bbox': '-87.94,41.644,-87.524,42.023',
        'limit': 1,
    }, timeout=5)
    resp.raise_for_status()
    features = resp.json().get('features', [])
    if not features:
        raise ValueError(f"Could not find: {place_name}")
    lng, lat = features[0]['geometry']['coordinates']
    coords = [lat, lng]
    _geocode_cache[normalized] = coords
    return coords

def get_time_label(h):
    if h == 0: return "12 AM"
    if h == 12: return "12 PM"
    if h < 12: return f"{h} AM"
    return f"{h - 12} PM"

@api.route('/get-route', methods=['POST'])
def get_route():
    req = request.json
    # Expects: {"start": [lat, lng], "end": [lat, lng], "beta": 0.5, "hour": 17}
    
    try:
        path = engine.get_route(
            start_coords=req['start'],
            end_coords=req['end'],
            beta=float(req.get('beta', 0.5)),
            hour=int(req.get('hour', 12)),
            is_weekend=bool(req.get('is_weekend', False)),
            travel_mode=req.get('travel_mode', 'walking')
        )
        return jsonify({"status": "success", "route": path})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@api.route('/compare-routes', methods=['POST'])
def compare_routes():
    req = request.json
    try:
        comparison = engine.get_comparison(
            start_coords=req['start'],
            end_coords=req['end'],
            beta=float(req.get('beta', 5.0)),
            hour=int(req.get('hour', 12)),
            is_weekend=bool(req.get('is_weekend', False)),
            travel_mode=req.get('travel_mode', 'walking')
        )
        return jsonify({"status": "success", "data": comparison})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@api.route('/weather', methods=['GET'])
def get_weather():
    """Returns current weather conditions and hourly forecast."""
    try:
        data = weather_svc.get_weather()
        return jsonify({"status": "success", "data": data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@api.route('/heatmap/<int:hour>', methods=['GET'])
def get_heatmap(hour):
    """Returns risk heatmap GeoJSON adjusted for time + weather"""
    try:
        hour = max(0, min(23, hour))  # Clamp to valid range
        time_mult = hourly_multipliers.get(hour, 1.0)
        weather_mult = weather_svc.get_risk_multiplier(hour)

        # Create adjusted copy of heatmap
        adjusted = copy.deepcopy(base_heatmap)
        for feature in adjusted['features']:
            base_risk = feature['properties'].get('risk_score', 0)
            # Apply time + weather multipliers and normalize
            adjusted_risk = base_risk * time_mult * weather_mult
            feature['properties']['risk_score'] = min(100, adjusted_risk)
            feature['properties']['hour'] = hour
            feature['properties']['time_multiplier'] = time_mult
            feature['properties']['weather_multiplier'] = weather_mult

        return jsonify(adjusted)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@api.route('/chat', methods=['POST'])
def chat():
    """AI-powered natural language routing with safety briefing.
    Optimized: parallel parse+chat, inline TTS audio for voice mode."""
    if not gemini_svc:
        return jsonify({
            "status": "error",
            "error_type": "no_api_key",
            "message": "Gemini AI is not configured. Please set GEMINI_API_KEY in the .env file."
        })

    user_message = request.json.get('message', '')
    if not user_message.strip():
        return jsonify({
            "status": "error",
            "error_type": "empty_message",
            "message": "Please type a message describing where you want to go."
        })

    user_hour = request.json.get('user_hour', None)
    nav_state = request.json.get('nav_state', None)
    user_coords = request.json.get('user_coords', None)
    voice = request.json.get('voice', False)  # If true, include TTS audio in response

    # Weather context (cached, instant)
    weather_ctx = weather_svc.get_context_string()

    # Check for travel mode selection (from frontend travel mode buttons/voice)
    pending_parsed = request.json.get('pending_parsed', None)
    selected_travel_mode = request.json.get('selected_travel_mode', None)

    if pending_parsed and selected_travel_mode:
        # User selected travel mode — use pending parsed data with chosen mode
        parsed = pending_parsed
        parsed['travel_mode'] = selected_travel_mode
    else:
        # OPTIMIZATION: Run parse + speculative chat reply IN PARALLEL
        pool = ThreadPoolExecutor(max_workers=2)
        parse_f = pool.submit(gemini_svc.parse_route_request, user_message, user_hour=user_hour)
        chat_f = pool.submit(
            gemini_svc.chat_reply, user_message,
            nav_state=nav_state, weather_context=weather_ctx,
            update_history=False  # Don't commit yet — speculative
        )
        parsed = parse_f.result()

        # Auto-fill missing start/end with user's current location (like Google Maps)
        if parsed and not parsed.get('start_name') and parsed.get('end_name') and user_coords:
            parsed['start_name'] = 'my current location'
        if parsed and parsed.get('start_name') and not parsed.get('end_name') and user_coords:
            parsed['end_name'] = 'my current location'

        if not parsed or not parsed.get('start_name') or not parsed.get('end_name'):
            # Not a route — use the pre-computed speculative chat reply
            reply = chat_f.result()
            pool.shutdown(wait=False)
            gemini_svc.commit_pending_exchange()  # Now commit to history

            result = {"status": "chat", "message": reply, "parsed": None}

            if voice:
                audio_data, mime_type = gemini_svc.text_to_speech(reply)
                if audio_data:
                    result['audio'] = base64.b64encode(audio_data).decode('utf-8')
                    result['audio_mime'] = mime_type

            return jsonify(result)

        # It IS a route — discard speculative chat without blocking
        pool.shutdown(wait=False)

        # Check if user specified travel mode explicitly
        if not parsed.get('travel_mode_explicit', True):
            buddy_msg = gemini_svc.chat_reply(
                user_message,
                weather_context=f"The user wants to go from {parsed['start_name']} to {parsed['end_name']} but didn't say how they're traveling. Ask them casually if they're walking, driving, or biking. One short sentence max, keep it natural like a friend would ask.",
            )
            result = {
                "status": "need_travel_mode",
                "message": buddy_msg,
                "pending_parsed": parsed
            }
            if voice:
                audio_data, mime_type = gemini_svc.text_to_speech(buddy_msg)
                if audio_data:
                    result['audio'] = base64.b64encode(audio_data).decode('utf-8')
                    result['audio_mime'] = mime_type
            return jsonify(result)

    # Step 2: Geocode place names (with "my location" support + parallel geocoding)
    start_is_here = parsed.get('start_name', '').lower().strip() in MY_LOCATION_PHRASES
    end_is_here = parsed.get('end_name', '').lower().strip() in MY_LOCATION_PHRASES

    start_coords = end_coords = None
    start_error = end_error = None

    if start_is_here:
        if user_coords and len(user_coords) == 2:
            start_coords = user_coords
            parsed['start_name'] = 'your current location'
        else:
            return jsonify({
                "status": "error",
                "error_type": "no_location",
                "message": "I need your location to route from here. Make sure location access is enabled in your browser.",
                "parsed": parsed
            })

    if end_is_here:
        if user_coords and len(user_coords) == 2:
            end_coords = user_coords
            parsed['end_name'] = 'your current location'
        else:
            return jsonify({
                "status": "error",
                "error_type": "no_location",
                "message": "I need your location. Make sure location access is enabled in your browser.",
                "parsed": parsed
            })

    # OPTIMIZATION: geocode both locations in parallel (saves 3-5s on first lookup)
    if not start_coords or not end_coords:
        with ThreadPoolExecutor(max_workers=2) as geo_pool:
            start_f = geo_pool.submit(geocode_place, parsed['start_name'], user_coords) if not start_coords else None
            end_f = geo_pool.submit(geocode_place, parsed['end_name'], user_coords) if not end_coords else None

            if start_f:
                try:
                    start_coords = start_f.result()
                except Exception:
                    start_error = parsed['start_name']
            if end_f:
                try:
                    end_coords = end_f.result()
                except Exception:
                    end_error = parsed['end_name']

    if start_error:
        return jsonify({
            "status": "error",
            "error_type": "geocode_failed",
            "message": f"I couldn't find '{start_error}' in Chicago. Could you try a different landmark or address?",
            "parsed": parsed
        })
    if end_error:
        return jsonify({
            "status": "error",
            "error_type": "geocode_failed",
            "message": f"I couldn't find '{end_error}' in Chicago. Could you try a different landmark or address?",
            "parsed": parsed
        })

    # Step 3: Bounds check
    if not engine.is_in_bounds(start_coords[0], start_coords[1]):
        return jsonify({
            "status": "error",
            "error_type": "out_of_bounds",
            "message": f"'{parsed['start_name']}' is outside our Chicago coverage area. Try a Chicago landmark instead.",
            "parsed": {**parsed, "start_coords": start_coords, "end_coords": end_coords}
        })

    if not engine.is_in_bounds(end_coords[0], end_coords[1]):
        return jsonify({
            "status": "error",
            "error_type": "out_of_bounds",
            "message": f"'{parsed['end_name']}' is outside our Chicago coverage area. Try a Chicago landmark instead.",
            "parsed": {**parsed, "start_coords": start_coords, "end_coords": end_coords}
        })

    # Step 4: Calculate routes
    try:
        comparison = engine.get_comparison(
            start_coords=start_coords,
            end_coords=end_coords,
            beta=parsed.get('beta', 5.0),
            hour=parsed.get('hour', 12),
            is_weekend=parsed.get('is_weekend', False),
            travel_mode=parsed.get('travel_mode', 'walking')
        )
    except Exception as e:
        return jsonify({
            "status": "error",
            "error_type": "routing_failed",
            "message": f"I found both locations but couldn't calculate a route between them: {str(e)}",
            "parsed": {**parsed, "start_coords": start_coords, "end_coords": end_coords}
        })

    # Step 5: Get weather context for this route
    route_hour = parsed.get('hour', 12)
    multiplier = hourly_multipliers.get(route_hour, 1.0)
    weather_context = weather_svc.get_context_string(hour=route_hour)
    weather_data = weather_svc.get_weather()

    # Step 6: Generate buddy summary + safety briefing in PARALLEL
    gen_kwargs = dict(
        parsed=parsed,
        metrics=comparison['metrics'],
        hourly_multiplier=multiplier,
        fastest_coords=comparison.get('fastest_route'),
        safest_coords=comparison.get('safest_route'),
        weather_context=weather_context
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        summary_f = pool.submit(gemini_svc.generate_route_summary, **gen_kwargs)
        briefing_f = pool.submit(gemini_svc.generate_safety_briefing, **gen_kwargs)
        buddy_summary = summary_f.result()
        briefing = briefing_f.result()

    # NOTE: TTS for route summaries is handled by the frontend via /tts endpoint.
    # Including it inline would add 3 simultaneous Gemini calls which hits rate limits.

    return jsonify({
        "status": "success",
        "parsed": {**parsed, "start_coords": start_coords, "end_coords": end_coords},
        "route_data": comparison,
        "safety_briefing": briefing,
        "ai_summary": buddy_summary,
        "weather": weather_data["current"]
    })


@api.route('/tts', methods=['POST'])
def tts():
    """Convert text to natural speech using Gemini's voice."""
    if not gemini_svc:
        return jsonify({"error": "Gemini not configured"}), 400

    text = request.json.get('text', '')
    if not text.strip():
        return jsonify({"error": "No text provided"}), 400

    audio_data, mime_type = gemini_svc.text_to_speech(text)
    if audio_data:
        return Response(audio_data, mimetype=mime_type)
    return jsonify({"error": "TTS generation failed"}), 500


# Register API routes under /api prefix
app.register_blueprint(api, url_prefix='/api')

# Serve React frontend in production (built files in ../frontend/dist)
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'frontend', 'dist')

if os.path.exists(FRONTEND_DIR):
    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_frontend(path):
        file_path = os.path.join(FRONTEND_DIR, path)
        if path and os.path.isfile(file_path):
            return send_from_directory(FRONTEND_DIR, path)
        return send_from_directory(FRONTEND_DIR, 'index.html')

if __name__ == '__main__':
    app.run(debug=True, port=5001)