"""
WalkingBuddy CLI Demo
─────────────────────
Interactive terminal chat with your AI walking companion.

Usage:
    set GEMINI_API_KEY=your_key_here
    python buddy_demo.py

Or pass an API key directly:
    python buddy_demo.py --key YOUR_KEY

Commands during chat:
    /route LAT1,LNG1 LAT2,LNG2   – Plan a route between two points
    /status                        – Show current buddy status
    /quit                          – Exit
"""
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.walking_buddy import WalkingBuddy


# ── NYC landmark shortcuts ───────────────────────────────────────────────
LANDMARKS = {
    "times square":    (40.7580, -73.9855),
    "central park":    (40.7829, -73.9654),
    "empire state":    (40.7484, -73.9856),
    "brooklyn bridge": (40.7061, -73.9969),
    "grand central":   (40.7527, -73.9772),
    "penn station":    (40.7506, -73.9935),
    "union square":    (40.7359, -73.9911),
    "washington sq":   (40.7308, -73.9973),
    "wall street":     (40.7074, -74.0113),
    "flatiron":        (40.7411, -73.9897),
}


def resolve_location(text: str) -> tuple[float, float] | None:
    """Try to parse 'lat,lng' or match a landmark name."""
    text = text.strip().lower()
    if text in LANDMARKS:
        return LANDMARKS[text]
    parts = text.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    # Fuzzy match landmarks
    for name, coords in LANDMARKS.items():
        if name in text or text in name:
            return coords
    return None


def print_banner():
    print("\n" + "=" * 56)
    print("   WalkingBuddy — Your AI Walking Companion")
    print("=" * 56)
    print("  Chat naturally! Tell me where you want to go.")
    print("  Example: 'I want to walk from Times Square")
    print("            to Central Park'")
    print()
    print("  Commands:")
    print("    /route FROM TO  – Plan a route (coords or landmark)")
    print("    /status         – Show navigation status")
    print("    /landmarks      – List known landmarks")
    print("    /quit           – Exit")
    print("=" * 56 + "\n")


def try_extract_route(msg: str) -> tuple | None:
    """Try to detect 'from X to Y' pattern in natural language."""
    lower = msg.lower()
    if " from " in lower and " to " in lower:
        parts = lower.split(" from ", 1)[1]
        if " to " in parts:
            origin_text, dest_text = parts.split(" to ", 1)
            origin = resolve_location(origin_text.strip())
            dest = resolve_location(dest_text.strip())
            if origin and dest:
                return origin, dest
    return None


def main():
    parser = argparse.ArgumentParser(description="WalkingBuddy CLI Demo")
    parser.add_argument("--key", help="Gemini API key (or set GEMINI_API_KEY env var)")
    parser.add_argument(
        "--risk-file",
        default="output/routing_risk_api.json",
        help="Path to routing risk JSON"
    )
    args = parser.parse_args()

    risk_path = Path(args.risk_file)
    if not risk_path.exists():
        print(f"[info] Risk file not found at {risk_path}.")
        print("[info] Buddy will work without safety data.\n")

    try:
        buddy = WalkingBuddy(risk_file=args.risk_file, api_key=args.key)
    except ImportError as e:
        print(f"Error: {e}")
        print("Install with: pip install google-genai")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print_banner()

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye! Stay safe out there.")
            break

        if not user_input:
            continue

        # ── Slash commands ───────────────────────────────────────────
        if user_input.startswith("/"):
            cmd = user_input.lower().split()

            if cmd[0] == "/quit":
                print("Bye! Stay safe out there.")
                break

            elif cmd[0] == "/landmarks":
                print("\nKnown landmarks:")
                for name, (lat, lng) in sorted(LANDMARKS.items()):
                    print(f"  {name:20s}  ({lat:.4f}, {lng:.4f})")
                print()
                continue

            elif cmd[0] == "/status":
                status = buddy.get_status()
                print(f"\n  State: {status['state']}")
                if "progress" in status:
                    print(f"  Progress: {status['progress']}")
                    print(f"  Risk: {status.get('current_risk', '?')} ({status.get('risk_label', '?')})")
                print()
                continue

            elif cmd[0] == "/route" and len(cmd) >= 3:
                origin = resolve_location(cmd[1])
                dest = resolve_location(cmd[2])
                if origin and dest:
                    route = buddy.plan_route(*origin, *dest)
                    print(f"\nBuddy: {route.summary()}")
                    print("       Say 'let's go' when you're ready!\n")
                else:
                    print("[error] Could not parse locations. Use lat,lng or a landmark name.\n")
                continue

        # ── Natural route detection ──────────────────────────────────
        route_match = try_extract_route(user_input)
        if route_match and buddy.state == "idle":
            origin, dest = route_match
            route = buddy.plan_route(*origin, *dest)
            # Let Gemini respond with the route context injected
            reply = buddy.chat(user_input)
            print(f"\nBuddy: {reply}\n")
            continue

        # ── Normal chat ──────────────────────────────────────────────
        reply = buddy.chat(user_input)
        print(f"\nBuddy: {reply}\n")


if __name__ == "__main__":
    main()
