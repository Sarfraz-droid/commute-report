import os
import sys
import time
import argparse
import requests
from datetime import datetime, timedelta, timezone

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

HOME = "14th Avenue, Gaur City 2, Greater Noida West, Uttar Pradesh, India"
OFFICE = "B-8, Info Edge Office, Sector 132, Noida, Uttar Pradesh, India"

DEFAULT_OFFICE_HOURS = 8.5
DEFAULT_ARRIVE_OFFICE_START = 9
DEFAULT_ARRIVE_OFFICE_END = 11
DEFAULT_LEAVE_OFFICE_START = 17
DEFAULT_LEAVE_OFFICE_END = 20

PREDICTION_INTERVAL_MINUTES = 30
DELAY_BETWEEN_CALLS_SECONDS = 0.3


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


def fetch_predictions(origin, destination, label):
    now = datetime.now()
    results = []
    print(f"  Fetching {label} predictions", end="", flush=True)

    start_t = now + timedelta(minutes=5)
    end_t = now + timedelta(hours=24)
    t = start_t.replace(minute=0, second=0, microsecond=0)

    while t <= end_t:
        if t > start_t:
            dep_str = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            data = get_eta(origin, destination, dep_str)
            eta_min, distance = parse_eta(data)
            if eta_min is not None:
                results.append({"depart": t, "eta_min": eta_min, "distance": distance})
                print(".", end="", flush=True)
            else:
                print("x", end="", flush=True)
            time.sleep(DELAY_BETWEEN_CALLS_SECONDS)
        t += timedelta(minutes=PREDICTION_INTERVAL_MINUTES)

    print(f" ({len(results)} datapoints)")
    return results


def fmt_time(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_time_full(dt):
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%a %I:%M %p").lstrip("0")


def fmt_duration(mins):
    if mins < 60:
        return f"{mins} min"
    h = mins // 60
    m = mins % 60
    return f"{h}h {m}m" if m else f"{h}h"


def hour_of(t):
    return t.hour + t.minute / 60


def filter_by_hour(data, min_hour, max_hour):
    return [p for p in data if min_hour <= hour_of(p["depart"]) <= max_hour]


def build_schedule_options(home_to_office, office_to_home,
                           min_office_minutes, arrive_office_start,
                           arrive_office_end, leave_office_start,
                           leave_office_end):

    morning_matches = []
    for trip in home_to_office:
        arrival = trip["depart"] + timedelta(minutes=trip["eta_min"])
        ah = hour_of(arrival)
        if arrive_office_start <= ah <= arrive_office_end:
            morning_matches.append({**trip, "arrival": arrival})

    evening_trips = filter_by_hour(office_to_home, leave_office_start, leave_office_end)

    options = []
    for morning in morning_matches:
        earliest_leave = morning["arrival"] + timedelta(minutes=min_office_minutes)

        for evening in evening_trips:
            if evening["depart"] < earliest_leave:
                continue

            total_commute = morning["eta_min"] + evening["eta_min"]
            office_time = (evening["depart"] - morning["arrival"]).total_seconds() / 3600

            options.append({
                "leave_home": morning["depart"],
                "reach_office": morning["arrival"],
                "morning_eta": morning["eta_min"],
                "leave_office": evening["depart"],
                "reach_home": evening["depart"] + timedelta(minutes=evening["eta_min"]),
                "evening_eta": evening["eta_min"],
                "total_commute": total_commute,
                "office_hours": round(office_time, 1),
            })

    return options


def print_header():
    now = datetime.now()
    print()
    print("=" * 64)
    print("  COMMUTE OPTIMIZER")
    print("=" * 64)
    print(f"  Home:    {HOME}")
    print(f"  Office:  {OFFICE}")
    print(f"  Arrive:  {DEFAULT_ARRIVE_OFFICE_START}:00 - {DEFAULT_ARRIVE_OFFICE_END}:00")
    print(f"  Leave:   {DEFAULT_LEAVE_OFFICE_START}:00 - {DEFAULT_LEAVE_OFFICE_END}:00")
    print(f"  Min office: {DEFAULT_OFFICE_HOURS}h")
    print(f"  Generated: {now.strftime('%d %b %Y, %I:%M %p')}")
    print("=" * 64)


def print_top_options(options, top_n=5):
    if not options:
        print(f"\n  No valid schedule options found in the given time windows.")
        return

    sorted_options = sorted(options, key=lambda o: o["total_commute"])

    print(f"\n  TOP {min(top_n, len(sorted_options))} SCHEDULES (by total commute)")
    print(f"  {'─' * 56}")

    for i, opt in enumerate(sorted_options[:top_n], 1):
        total_span = (opt["reach_home"] - opt["leave_home"]).total_seconds() / 3600

        tag = ""
        if i == 1:
            tag = " \033[1;32m← BEST\033[0m"

        print(f"\n  #{i}{tag}")
        print(f"    {'Leave home':<16} {fmt_time(opt['leave_home']):>8}  "
              f"({fmt_duration(opt['morning_eta'])})")
        print(f"    {'Reach office':<16} {fmt_time(opt['reach_office']):>8}")
        print(f"    {'Office time':<16} {opt['office_hours']:>6}h")
        print(f"    {'Leave office':<16} {fmt_time(opt['leave_office']):>8}  "
              f"({fmt_duration(opt['evening_eta'])})")
        print(f"    {'Reach home':<16} {fmt_time(opt['reach_home']):>8}")
        print(f"    {'─' * 56}")
        print(f"    Total commute: {fmt_duration(opt['total_commute'])}  │  "
              f"Day span: {fmt_duration(round(total_span * 60))}")

    return sorted_options


def print_commute_graph(home_to_office, office_to_home,
                        arrive_start, arrive_end, leave_start, leave_end):
    print(f"\n\n  COMMUTE TIME BY DEPARTURE HOUR (practical window)")
    print(f"  {'─' * 56}")

    morning_visible = [p for p in home_to_office
                       if arrive_start <= hour_of(p["depart"] + timedelta(minutes=p["eta_min"])) <= arrive_end]
    evening_visible = filter_by_hour(office_to_home, leave_start, leave_end)

    all_visible = morning_visible + evening_visible
    if not all_visible:
        print("\n  No data in practical windows.")
        return

    max_eta = max(p["eta_min"] for p in all_visible)

    for data, label, is_morning in [
        (morning_visible, "Home → Office (arrive {}-{})".format(
            fmt_hour(arrive_start), fmt_hour(arrive_end)), True),
        (evening_visible, "Office → Home (depart {}-{})".format(
            fmt_hour(leave_start), fmt_hour(leave_end)), False),
    ]:
        if not data:
            continue
        print(f"\n  {label}")
        best_eta = min(p["eta_min"] for p in data)
        for p in data:
            bar_len = max(1, int(p["eta_min"] / max(max_eta, 1) * 25))
            bar = "█" * bar_len
            trailing = (25 - bar_len) * "░"
            marker = ""
            if p["eta_min"] <= best_eta + 2:
                marker = " ✓ BEST"
            print(f"    {fmt_time(p['depart']):>8}  {bar}{trailing} {p['eta_min']:>3} min{marker}")


def fmt_hour(h):
    return f"{h:02d}:00"


def print_traffic_breakdown(home_to_office, office_to_home,
                             arrive_start, arrive_end, leave_start, leave_end):
    print(f"\n\n  BEST & WORST TIMES (practical window)")
    print(f"  {'─' * 56}")

    morning_visible = [p for p in home_to_office
                       if arrive_start <= hour_of(p["depart"] + timedelta(minutes=p["eta_min"])) <= arrive_end]
    evening_visible = filter_by_hour(office_to_home, leave_start, leave_end)

    for data, name in [
        (morning_visible, "Home → Office (arrive {}-{})".format(fmt_hour(arrive_start), fmt_hour(arrive_end))),
        (evening_visible, "Office → Home (depart {}-{})".format(fmt_hour(leave_start), fmt_hour(leave_end))),
    ]:
        if not data:
            print(f"\n  {name}:\n    No data in this window.")
            continue
        best = min(data, key=lambda p: p["eta_min"])
        worst = max(data, key=lambda p: p["eta_min"])
        avg = sum(p["eta_min"] for p in data) // len(data)
        print(f"\n  {name}:")
        print(f"    Best:  {fmt_time(best['depart']):>8} → {best['eta_min']} min")
        print(f"    Worst: {fmt_time(worst['depart']):>8} → {worst['eta_min']} min")
        print(f"    Avg:   {avg} min")


def run_quick_check():
    now = datetime.now()
    print(f"\n  QUICK STATUS ─ {now.strftime('%d %b, %I:%M %p')}")
    print(f"  {'─' * 48}")

    for origin, dest, label in [(HOME, OFFICE, "Home → Office"), (OFFICE, HOME, "Office → Home")]:
        data = get_eta(origin, dest)
        eta_min, distance = parse_eta(data)
        if eta_min is not None:
            arrival = now + timedelta(minutes=eta_min)
            if eta_min <= 30:
                tag = "\033[1;32m← GREEN\033[0m"
            elif eta_min <= 50:
                tag = "\033[1;33m← AMBER\033[0m"
            else:
                tag = "\033[1;31m← HEAVY\033[0m"
            print(f"  {label:<16} {eta_min:>4} min  ({distance} km)  → arrive {fmt_time(arrival)}  {tag}")
        else:
            print(f"  {label:<16}  No data")
    print()


def run_single_direction(origin, dest, label, is_to_office):
    print_header()
    data = fetch_predictions(origin, dest, label)
    if not data:
        print("  No predictions available.\n")
        return

    if is_to_office:
        print_commute_graph(data, [], 0, 24, 0, 24)
        print_traffic_breakdown(data, [], 0, 24, 0, 24)
    else:
        print_commute_graph([], data, 0, 24, 0, 24)
        print_traffic_breakdown([], data, 0, 24, 0, 24)
    print()


def main():
    parser = argparse.ArgumentParser(description="Commute optimizer for Home ↔ Office round trips")
    parser.add_argument("--now", action="store_true",
                        help="Quick check: show current ETA for both directions")
    parser.add_argument("--to-office", action="store_true",
                        help="Show only Home → Office predictions")
    parser.add_argument("--to-home", action="store_true",
                        help="Show only Office → Home predictions")
    parser.add_argument("--office-hours", type=float, default=DEFAULT_OFFICE_HOURS,
                        help=f"Minimum hours in office (default: {DEFAULT_OFFICE_HOURS})")
    parser.add_argument("--arrive-start", type=int, default=DEFAULT_ARRIVE_OFFICE_START,
                        help=f"Earliest arrival hour at office (default: {DEFAULT_ARRIVE_OFFICE_START})")
    parser.add_argument("--arrive-end", type=int, default=DEFAULT_ARRIVE_OFFICE_END,
                        help=f"Latest arrival hour at office (default: {DEFAULT_ARRIVE_OFFICE_END})")
    parser.add_argument("--leave-start", type=int, default=DEFAULT_LEAVE_OFFICE_START,
                        help=f"Earliest departure hour from office (default: {DEFAULT_LEAVE_OFFICE_START})")
    parser.add_argument("--leave-end", type=int, default=DEFAULT_LEAVE_OFFICE_END,
                        help=f"Latest departure hour from office (default: {DEFAULT_LEAVE_OFFICE_END})")
    parser.add_argument("--compact", action="store_true",
                        help="Show only the schedule summary (skip graphs)")
    args = parser.parse_args()

    if not GOOGLE_API_KEY:
        print("Error: GOOGLE_API_KEY environment variable is not set.", file=sys.stderr)
        print("  Get a key from https://console.cloud.google.com/apis/credentials", file=sys.stderr)
        print("  Then: export GOOGLE_API_KEY='your-key'", file=sys.stderr)
        sys.exit(1)

    min_office_minutes = int(args.office_hours * 60)

    if args.now:
        run_quick_check()
        return

    if args.to_office:
        run_single_direction(HOME, OFFICE, "Home → Office", True)
        return

    if args.to_home:
        run_single_direction(OFFICE, HOME, "Office → Home", False)
        return

    print_header()

    home_to_office = fetch_predictions(HOME, OFFICE, "Home → Office")
    office_to_home = fetch_predictions(OFFICE, HOME, "Office → Home")

    options = build_schedule_options(
        home_to_office, office_to_home,
        min_office_minutes,
        args.arrive_start, args.arrive_end,
        args.leave_start, args.leave_end,
    )

    print_top_options(options)

    if not args.compact:
        print_commute_graph(home_to_office, office_to_home,
                            args.arrive_start, args.arrive_end,
                            args.leave_start, args.leave_end)
        print_traffic_breakdown(home_to_office, office_to_home,
                                args.arrive_start, args.arrive_end,
                                args.leave_start, args.leave_end)

    print()


if __name__ == "__main__":
    main()
