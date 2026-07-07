import os
import sys
import time
import argparse
import requests
from datetime import datetime, timedelta, timezone

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

HOME = "14th Avenue, Gaur City 2, Greater Noida West, Uttar Pradesh, India"
OFFICE = "B-8, Info Edge Office, Sector 132, Noida, Uttar Pradesh, India"

DEFAULT_LEAVE_START = 16
DEFAULT_LEAVE_END = 22
PREDICTION_INTERVAL_MINUTES = 30
DELAY_BETWEEN_CALLS_SECONDS = 0.3
TOP_N = 8


def get_eta(origin, destination, departure_time=None):
    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    payload = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": False,
    }
    if departure_time:
        payload["departureTime"] = departure_time
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }
    r = requests.post(url, json=payload, headers=headers, timeout=15)
    if not r.ok:
        print(f"\n  API error ({r.status_code}): {r.text}", file=sys.stderr)
        return None
    return r.json()


def parse_eta(data):
    if not data:
        return None, None
    routes = data.get("routes", [])
    if not routes:
        return None, None
    eta_seconds = int(routes[0]["duration"].replace("s", ""))
    eta_min = eta_seconds // 60
    distance = round(routes[0]["distanceMeters"] / 1000, 1)
    return eta_min, distance


def fetch_predictions(origin, destination, leave_start, leave_end):
    now = datetime.now()
    interval = PREDICTION_INTERVAL_MINUTES

    end_t = now.replace(hour=leave_end, minute=0, second=0, microsecond=0)

    mins_since_hour = now.minute + now.second / 60
    next_slot = now.replace(minute=0, second=0, microsecond=0) + timedelta(
        minutes=((mins_since_hour // interval) + 1) * interval
    )
    t = max(next_slot, now.replace(hour=leave_start, minute=0, second=0, microsecond=0))

    if t > end_t:
        t = now.replace(hour=leave_start, minute=0, second=0, microsecond=0) + timedelta(days=1)
        end_t = now.replace(hour=leave_end, minute=0, second=0, microsecond=0) + timedelta(days=1)

    results = []
    day_label = "today" if t.date() == now.date() else f"tomorrow ({t.strftime('%d %b')})"
    print(f"  Fetching predictions for {day_label}: {fmt_time(t)} to {fmt_time(end_t)}", end="", flush=True)

    while t <= end_t:
        dep_str = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data = get_eta(origin, destination, dep_str)
        eta_min, distance = parse_eta(data)
        if eta_min is not None:
            arrival = t + timedelta(minutes=eta_min)
            results.append({
                "depart": t,
                "eta_min": eta_min,
                "distance": distance,
                "arrival": arrival,
            })
            print(".", end="", flush=True)
        else:
            print("x", end="", flush=True)
        time.sleep(DELAY_BETWEEN_CALLS_SECONDS)
        t += timedelta(minutes=PREDICTION_INTERVAL_MINUTES)

    print(f" ({len(results)} datapoints)")
    return results


def fmt_time(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_duration(mins):
    if mins < 60:
        return f"{mins} min"
    h = mins // 60
    m = mins % 60
    return f"{h}h {m}m" if m else f"{h}h"


def print_header(leave_start, leave_end):
    now = datetime.now()
    print()
    print("=" * 52)
    print("  BEST TIME TO REACH HOME")
    print("=" * 52)
    print(f"  From:   {OFFICE}")
    print(f"  To:     {HOME}")
    print(f"  Window: {leave_start}:00 - {leave_end}:00")
    print(f"  Generated: {now.strftime('%d %b %Y, %I:%M %p')}")
    print("=" * 52)


def print_now_status():
    now = datetime.now()
    print(f"\n  CURRENT STATUS ─ {now.strftime('%d %b, %I:%M %p')}")
    print(f"  {'─' * 44}")

    data = get_eta(OFFICE, HOME)
    eta_min, distance = parse_eta(data)
    if eta_min is not None:
        arrival = now + timedelta(minutes=eta_min)
        if eta_min <= 30:
            tag = "\033[1;32m← LIGHT\033[0m"
        elif eta_min <= 50:
            tag = "\033[1;33m← MODERATE\033[0m"
        else:
            tag = "\033[1;31m← HEAVY\033[0m"
        print(f"  Leave now     → {eta_min:>4} min  ({distance} km)  reach {fmt_time(arrival)}  {tag}")
    else:
        print(f"  Leave now     → No data")


def print_top_departures(data, top_n=TOP_N):
    if not data:
        print("\n  No predictions available.")
        return None

    ranked = sorted(data, key=lambda p: p["eta_min"])

    print(f"\n  BEST {min(top_n, len(ranked))} DEPARTURES (shortest commute first)")
    print(f"  {'─' * 44}")

    best_eta = ranked[0]["eta_min"]
    for i, p in enumerate(ranked[:top_n], 1):
        saving = p["eta_min"] - best_eta
        tag = ""
        if i == 1:
            tag = " \033[1;32m◀ BEST\033[0m"
        elif saving <= 5:
            tag = " \033[1;33m(only +{})\033[0m".format(saving)
        elif saving <= 15:
            tag = f"  (+{saving} min)"
        else:
            tag = f"  \033[90m(+{saving} min)\033[0m"

        print(f"  #{i:<2} Leave {fmt_time(p['depart']):>8}  →  {p['eta_min']:>3} min"
              f"  ({p['distance']} km)  reach {fmt_time(p['arrival']):>8}{tag}")

    return ranked


def print_time_graph(data, leave_start, leave_end):
    if not data:
        return

    window_data = [p for p in data if leave_start <= p["depart"].hour <= leave_end]
    if not window_data:
        window_data = data

    max_eta = max(p["eta_min"] for p in window_data)
    best_eta = min(p["eta_min"] for p in window_data)

    print(f"\n  TRAVEL TIME BY DEPARTURE")
    print(f"  {'─' * 44}")

    for p in window_data:
        bar_len = max(1, int(p["eta_min"] / max(max_eta, 1) * 30))
        bar = "█" * bar_len
        trailing = (30 - bar_len) * "░"
        marker = ""
        if p["eta_min"] <= best_eta + 2:
            marker = " ✓ BEST"
        elif p["eta_min"] >= max_eta - 2:
            marker = " ✗ WORST"
        print(f"  {fmt_time(p['depart']):>8}  {bar}{trailing} {p['eta_min']:>3} min{marker}")


def print_summary(data):
    if not data:
        return
    best = min(data, key=lambda p: p["eta_min"])
    worst = max(data, key=lambda p: p["eta_min"])
    avg = sum(p["eta_min"] for p in data) // len(data)

    print(f"\n  SUMMARY")
    print(f"  {'─' * 44}")
    print(f"  Best:      Leave {fmt_time(best['depart']):>8}  →  {best['eta_min']:>3} min  "
          f"(reach {fmt_time(best['arrival']):>8})")
    print(f"  Worst:     Leave {fmt_time(worst['depart']):>8}  →  {worst['eta_min']:>3} min  "
          f"(reach {fmt_time(worst['arrival']):>8})")
    print(f"  Average:   {avg} min")
    print(f"  Spread:    {worst['eta_min'] - best['eta_min']} min difference")


def print_comparison(data):
    if not data:
        return
    now = datetime.now()
    current = get_eta(OFFICE, HOME)
    curr_eta, _ = parse_eta(current)

    upcoming = [p for p in data if p["depart"] > now]
    if not upcoming or curr_eta is None:
        return

    best_upcoming = min(upcoming, key=lambda p: p["eta_min"])
    diff = curr_eta - best_upcoming["eta_min"]

    print(f"\n  WORTH WAITING?")
    print(f"  {'─' * 44}")
    if diff > 10:
        wait_min = int((best_upcoming["depart"] - now).total_seconds() / 60)
        print(f"  Leave now:  {curr_eta} min")
        print(f"  Wait until: {fmt_time(best_upcoming['depart'])} → {best_upcoming['eta_min']} min")
        print(f"  Save {diff} min by waiting ~{wait_min} min to leave")
        if diff > wait_min:
            print(f"  \033[1;32m⇒ Worth waiting! You save more time than you wait.\033[0m")
        else:
            print(f"  \033[1;33m⇒ Even trade — saving {diff} min for {wait_min} min of waiting.\033[0m")
    elif diff > 0:
        print(f"  Current commute ({curr_eta} min) is close to best upcoming ({best_upcoming['eta_min']} min).")
        print(f"  \033[1;32m⇒ Leave whenever convenient.\033[0m")
    else:
        print(f"  Current ETA ({curr_eta} min) is already the best option.")
        print(f"  \033[1;32m⇒ Now is a great time to leave!\033[0m")


def main():
    parser = argparse.ArgumentParser(
        description="Find the best time to leave office and reach home fastest"
    )
    parser.add_argument("--now", action="store_true",
                        help="Quick check: show current ETA from office to home")
    parser.add_argument("--leave-start", type=int, default=DEFAULT_LEAVE_START,
                        help=f"Earliest departure hour (default: {DEFAULT_LEAVE_START})")
    parser.add_argument("--leave-end", type=int, default=DEFAULT_LEAVE_END,
                        help=f"Latest departure hour (default: {DEFAULT_LEAVE_END})")
    parser.add_argument("--compact", action="store_true",
                        help="Show only top departures (skip graph and summary)")
    parser.add_argument("--top", type=int, default=TOP_N,
                        help=f"Number of top departures to show (default: {TOP_N})")
    args = parser.parse_args()

    if not GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable is not set.", file=sys.stderr)
        print("  Get a key from https://console.cloud.google.com/apis/credentials", file=sys.stderr)
        print("  Then: export GOOGLE_API_KEY='your-key'", file=sys.stderr)
        sys.exit(1)

    print_now_status()

    if args.now:
        print()
        return

    print_header(args.leave_start, args.leave_end)
    data = fetch_predictions(OFFICE, HOME, args.leave_start, args.leave_end)
    print_top_departures(data, args.top)

    if not args.compact and data:
        print_time_graph(data, args.leave_start, args.leave_end)
        print_summary(data)
        print_comparison(data)

    print()


if __name__ == "__main__":
    main()
