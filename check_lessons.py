"""
Swim Lesson Availability Checker
Checks the Moraga Valley Swim & Tennis Club booking page for
available lessons with teacher Sadie and sends an email notification.

Uses Playwright (headless browser) because the booking page is a Vue.js
app that renders availability data client-side via JavaScript.

The page loads availability in two API calls:
  1. retrieve_events_for_time_blocks_calendar  (the time slots)
  2. retrieve_bookings_for_calendar            (which are booked)
We must wait for BOTH to complete before reading the DOM, otherwise all
buttons appear as "booked" before the real data arrives.

We use page.expect_response() BEFORE navigation so the listener is
active when the API responses arrive during page load.
"""

import os
import re
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TEACHER_NAME = "Sadie"
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)")


def fetch_availability_from_api(page):
    """
    Load the booking page and intercept the API responses to determine
    availability directly from the JSON data, bypassing DOM rendering
    issues (the page shows all slots as 'booked' for anonymous users).

    Returns a list of available slot dicts.
    """
    import json

    events_data = None
    bookings_data = None

    def capture_events(response):
        nonlocal events_data
        if "retrieve_events_for_time_blocks_calendar" in response.url:
            try:
                events_data = response.json()
            except Exception:
                pass

    def capture_bookings(response):
        nonlocal bookings_data
        if "retrieve_bookings_for_calendar" in response.url:
            try:
                bookings_data = response.json()
            except Exception:
                pass

    page.on("response", capture_events)
    page.on("response", capture_bookings)

    page.goto(BOOKING_URL, wait_until="networkidle", timeout=60000)

    # Give a moment for callbacks to fire
    page.wait_for_timeout(2000)

    print(f"  Events API captured: {events_data is not None}")
    print(f"  Bookings API captured: {bookings_data is not None}")

    if events_data is None or bookings_data is None:
        print("  ERROR: Could not capture API responses.")
        return [], "Unknown date"

    # Extract all time slots from events data
    all_slots = []
    events = events_data if isinstance(events_data, list) else events_data.get("data", events_data.get("events", []))

    # Debug: show structure
    if isinstance(events_data, dict):
        print(f"  Events response keys: {list(events_data.keys())}")
    print(f"  Events count: {len(events) if isinstance(events, list) else 'not a list'}")

    # Extract booked slot IDs from bookings data
    bookings = bookings_data if isinstance(bookings_data, list) else bookings_data.get("data", bookings_data.get("bookings", []))
    if isinstance(bookings_data, dict):
        print(f"  Bookings response keys: {list(bookings_data.keys())}")
    print(f"  Bookings count: {len(bookings) if isinstance(bookings, list) else 'not a list'}")

    # Build set of booked event IDs/times
    booked_keys = set()
    if isinstance(bookings, list):
        for b in bookings:
            if isinstance(b, dict):
                # Try common key patterns
                key = b.get("event_id") or b.get("eventId") or b.get("id") or ""
                area = b.get("area_id") or b.get("areaId") or ""
                start = b.get("start_time") or b.get("startTime") or b.get("start") or ""
                booked_keys.add(f"{area}_{start}")
                if key:
                    booked_keys.add(str(key))

    # Find Sadie's available slots
    available = []
    sadie_total = 0

    if isinstance(events, list):
        for evt in events:
            if not isinstance(evt, dict):
                continue
            # Look for Sadie in area/instructor name
            area_name = str(evt.get("area_name", "") or evt.get("areaName", "") or evt.get("name", ""))
            instructor = str(evt.get("instructor", "") or evt.get("instructor_name", ""))
            combined = f"{area_name} {instructor}".lower()

            if TEACHER_NAME.lower() not in combined:
                continue

            sadie_total += 1
            area_id = evt.get("area_id") or evt.get("areaId") or ""
            start_time = evt.get("start_time") or evt.get("startTime") or evt.get("start") or ""
            evt_id = str(evt.get("id", ""))

            is_booked = (
                f"{area_id}_{start_time}" in booked_keys
                or evt_id in booked_keys
            )

            if not is_booked:
                # Format time for display
                slot_time = "Unknown time"
                try:
                    st = int(start_time) if start_time else 0
                    hours = st // 100
                    mins = st % 100
                    ampm = "AM" if hours < 12 else "PM"
                    display_hr = hours if hours <= 12 else hours - 12
                    if display_hr == 0:
                        display_hr = 12
                    slot_time = f"{display_hr}:{mins:02d} {ampm}"
                except (ValueError, TypeError):
                    slot_time = str(start_time)

                available.append({
                    "time": slot_time,
                    "date": str(evt.get("date", "Unknown date")),
                })

    print(f"  Sadie: {sadie_total} slots total, {len(available)} available.")

    # Also dump a sample event and booking for debugging
    if isinstance(events, list) and events:
        sample = {k: v for k, v in (events[0] if isinstance(events[0], dict) else {}).items()}
        print(f"  Sample event keys: {list(sample.keys())[:15]}")
    if isinstance(bookings, list) and bookings:
        sample_b = {k: v for k, v in (bookings[0] if isinstance(bookings[0], dict) else {}).items()}
        print(f"  Sample booking keys: {list(sample_b.keys())[:15]}")

    return available, "today"


def parse_availability(html):
    """
    Parse the rendered page for Sadie's available time slots.

    Available (blue) slots are div.availabilityButtonV2 elements that do NOT
    have the 'booked' CSS class. Booked/unavailable (gray) slots have 'booked'.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Extract current date from the date input field
    date_input = soup.find("input", class_="pointer")
    if date_input and date_input.get("value"):
        current_date = date_input["value"]
    else:
        page_text = soup.get_text(separator="\n")
        date_match = re.search(r"DATE\s*\n\s*(\w+ \d+)", page_text)
        current_date = date_match.group(1).strip() if date_match else "Unknown date"

    available_slots = []

    buttons = soup.find_all("div", class_="availabilityButtonV2")
    print(f"  Found {len(buttons)} total availability buttons.")

    sadie_total = 0
    for btn in buttons:
        area_name = btn.find("span", class_="area-name")
        if not area_name or TEACHER_NAME.lower() not in area_name.get_text().lower():
            continue

        sadie_total += 1
        is_booked = "booked" in btn.get("class", [])

        if not is_booked:
            btn_text = btn.get_text()
            time_match = TIME_PATTERN.search(btn_text)
            slot_time = time_match.group(0) if time_match else "Unknown time"

            if slot_time == "Unknown time":
                start_attr = btn.get("start-time", "")
                if start_attr:
                    hours = int(start_attr) // 100
                    mins = int(start_attr) % 100
                    ampm = "AM" if hours < 12 else "PM"
                    display_hr = hours if hours <= 12 else hours - 12
                    if display_hr == 0:
                        display_hr = 12
                    slot_time = f"{display_hr}:{mins:02d} {ampm}"

            available_slots.append({
                "time": slot_time,
                "date": current_date,
            })

    print(f"  Sadie: {sadie_total} slots total, {len(available_slots)} available (blue).")
    return available_slots, current_date


def send_email(available_slots):
    """Send an email notification about available slots."""
    sender_email = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("NOTIFY_EMAIL", sender_email)

    if not sender_email or not app_password:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)

    slot_list = "\n".join(
        f"  - {s['date']} at {s['time']}" for s in available_slots
    )

    body = f"""Hi Dana!

Sadie has swim lesson slots available! Here's what I found:

{slot_list}

Book now before they fill up:
{BOOKING_URL}

-- Swim Lesson Checker Bot
"""

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient
    msg["Subject"] = f"Swim Lessons Open -- Sadie has availability!"
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, app_password)
        server.sendmail(sender_email, recipient, msg.as_string())

    print(f"Email sent to {recipient}!")


NOTIFIED_FLAG = ".notified"


def mark_notified():
    """Create a flag file so GitHub Actions cache knows we already notified."""
    with open(NOTIFIED_FLAG, "w") as f:
        f.write("notified")
    print(f"Created {NOTIFIED_FLAG} flag for weekly cache.")


def main():
    already_notified = os.environ.get("ALREADY_NOTIFIED", "").lower() == "true"
    if already_notified:
        print("Already notified this week. Skipping until next Saturday.")
        sys.exit(0)

    print(f"Checking {BOOKING_URL} ...")

    all_available = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            print("Loading page and intercepting API responses...")
            slots, date_label = fetch_availability_from_api(page)
            all_available.extend(slots)

            browser.close()

    except Exception as e:
        print(f"Error: {e}")
        print("Will retry on next scheduled run.")
        sys.exit(0)

    if all_available:
        print(f"\nFOUND {len(all_available)} available slot(s) with {TEACHER_NAME}!")
        for slot in all_available:
            print(f"  - {slot['date']} at {slot['time']}")
        try:
            send_email(all_available)
            mark_notified()
        except Exception as e:
            print(f"Error sending email: {e}")
            sys.exit(1)
    else:
        print(f"\nNo available slots with {TEACHER_NAME}. All full or not posted yet.")


if __name__ == "__main__":
    main()
