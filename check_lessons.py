"""
Swim Lesson Availability Checker
Checks the Moraga Valley Swim & Tennis Club booking page for
available lessons with teacher Sadie and sends an email notification.

Uses Playwright to load the booking page and intercepts the Parse Server
API responses to determine availability (works without login).
Falls back to DOM text analysis if API interception finds nothing.
"""

import os
import re
import json
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TEACHER_NAME = "Sadie"
TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}\s*(AM|PM)$", re.IGNORECASE)


def check_availability_via_api(page):
    """
    Intercept Parse Server API responses at the network level.
    Returns (time_blocks_list, bookings_list) or ([], []) if not captured.
    """
    time_blocks = []
    bookings = []

    def handle_response(response):
        url = response.url
        if response.status != 200:
            return
        try:
            if "retrieve_events_for_time_blocks_calendar" in url:
                data = response.json()
                if "result" in data:
                    time_blocks.extend(data["result"])
                    print(f"  [API] Captured {len(data['result'])} time blocks.")
            elif "retrieve_bookings_for_calendar" in url:
                data = response.json()
                if "result" in data:
                    bookings.extend(data["result"])
                    print(f"  [API] Captured {len(data['result'])} bookings.")
        except Exception as e:
            print(f"  [API] Warning: {e}")

    page.on("response", handle_response)
    print(f"  Loading {BOOKING_URL} ...")
    page.goto(BOOKING_URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(8000)

    return time_blocks, bookings


def find_available_from_api(time_blocks, bookings):
    """Compare time blocks vs bookings to find Sadie's open slots."""
    # Build a set of booked (event_id, area_id) pairs
    booked_keys = set()
    for b in bookings:
        ev = b.get("event", {})
        ar = b.get("area", {})
        ev_id = ev.get("objectId", "") if isinstance(ev, dict) else ""
        ar_id = ar.get("objectId", "") if isinstance(ar, dict) else ""
        if ev_id and ar_id:
            booked_keys.add((ev_id, ar_id))
    print(f"  [API] {len(booked_keys)} booked (event, area) pairs.")

    # Find Sadie's time block/area combos
    available = []
    sadie_total = 0
    for block in time_blocks:
        block_id = block.get("objectId", "")
        areas = block.get("areas", [])
        start_time = block.get("startTime", 0)

        # Get date - could be a Parse Date object or string
        date_obj = block.get("date", {})
        if isinstance(date_obj, dict):
            date_str = date_obj.get("iso", "")
        else:
            date_str = str(date_obj)

        for area in areas:
            area_name = area.get("name", "")
            if TEACHER_NAME.lower() not in area_name.lower():
                continue
            sadie_total += 1
            area_id = area.get("objectId", "")
            key = (block_id, area_id)
            if key not in booked_keys:
                available.append({
                    "date": date_str[:10] if date_str else "unknown",
                    "time": start_time,
                })

    print(f"  [API] {TEACHER_NAME}: {sadie_total} total, {len(available)} available.")
    return available


def check_availability_via_dom(page):
    """
    Fallback: parse the rendered DOM.
    Available slots show a time (e.g. "2:40 PM") in the button text,
    while booked slots show a person's name or "not available".
    """
    try:
        page.wait_for_selector(".availabilityButtonV2", timeout=10000)
    except Exception:
        print("  [DOM] No availability buttons found.")
        return []

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    buttons = soup.find_all("div", class_="availabilityButtonV2")
    print(f"  [DOM] Found {len(buttons)} total availability buttons.")

    available = []
    sadie_total = 0
    sadie_booked_class = 0
    sadie_text_avail = 0

    for btn in buttons:
        area_span = btn.find("span", class_="area-name")
        if not area_span or TEACHER_NAME.lower() not in area_span.get_text().lower():
            continue

        sadie_total += 1
        has_booked_class = "booked" in btn.get("class", [])
        if has_booked_class:
            sadie_booked_class += 1

        # Get the text BEFORE the area-name span (the first text node)
        p_tag = btn.find("p")
        if not p_tag:
            continue

        # Extract text content excluding the area-name span
        button_text = ""
        for child in p_tag.children:
            if hasattr(child, 'name') and child.name == 'span':
                break  # stop before area-name
            if hasattr(child, 'name') and child.name == 'br':
                continue
            text = child.get_text().strip() if hasattr(child, 'get_text') else str(child).strip()
            if text:
                button_text = text

        # Check if it's a time (available) or a name (booked)
        is_time = bool(TIME_PATTERN.match(button_text))
        if is_time:
            sadie_text_avail += 1
            start_attr = btn.get("start-time", "")
            available.append({
                "date": "see-page",
                "time": start_attr,
            })

        # Log first few for debugging
        if sadie_total <= 4:
            print(f"    [DOM] Sadie slot: text='{button_text}', booked_class={has_booked_class}, is_time={is_time}")

    print(f"  [DOM] {TEACHER_NAME}: {sadie_total} total, {sadie_booked_class} with booked class, {sadie_text_avail} with time text (available).")
    return available


def format_time(military):
    """Convert 1400 to '2:00 PM'."""
    try:
        t = int(military)
        hours = t // 100
        mins = t % 100
        ampm = "AM" if hours < 12 else "PM"
        display = hours if hours <= 12 else hours - 12
        if display == 0:
            display = 12
        return f"{display}:{mins:02d} {ampm}"
    except (ValueError, TypeError):
        return str(military)


def send_email(slots):
    """Send an email notification about available slots."""
    sender = os.environ.get("GMAIL_ADDRESS")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("NOTIFY_EMAIL", sender)

    if not sender or not password:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)

    slot_list = "\n".join(
        f"  - {s.get('date', '?')} at {format_time(s['time'])}"
        for s in slots
    )
    body = (
        f"Hi Dana!\n\n"
        f"Sadie has swim lesson slots available! Here's what I found:\n\n"
        f"{slot_list}\n\n"
        f"Book now before they fill up:\n{BOOKING_URL}\n\n"
        f"-- Swim Lesson Checker Bot\n"
    )

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = "Swim Lessons Open -- Sadie has availability!"
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    print(f"Email sent to {recipient}!")


def main():
    already = os.environ.get("ALREADY_NOTIFIED", "").lower() == "true"
    if already:
        print("Already notified this week. Skipping.")
        sys.exit(0)

    print(f"Checking {BOOKING_URL} ...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Method 1: Intercept API responses
            time_blocks, bookings = check_availability_via_api(page)

            slots = []
            if time_blocks:
                print(f"\n  Using API method ({len(time_blocks)} blocks, {len(bookings)} bookings).")
                slots = find_available_from_api(time_blocks, bookings)
            else:
                print(f"\n  API interception captured nothing. Falling back to DOM analysis.")

            # Method 2: DOM text analysis (always run as verification or fallback)
            dom_slots = check_availability_via_dom(page)
            if not slots and dom_slots:
                print(f"  Using DOM method results.")
                slots = dom_slots
            elif slots and dom_slots:
                print(f"  API found {len(slots)}, DOM found {len(dom_slots)}. Using max.")
                if len(dom_slots) > len(slots):
                    slots = dom_slots

            browser.close()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("Will retry on next scheduled run.")
        sys.exit(0)

    if slots:
        print(f"\nFOUND {len(slots)} available slot(s) with {TEACHER_NAME}!")
        for s in slots:
            print(f"  - {s.get('date', '?')} at {format_time(s['time'])}")
        try:
            send_email(slots)
            with open(".notified", "w") as f:
                f.write("notified")
        except Exception as e:
            print(f"Error sending email: {e}")
            sys.exit(1)
    else:
        print(f"\nNo available slots with {TEACHER_NAME}.")


if __name__ == "__main__":
    main()
