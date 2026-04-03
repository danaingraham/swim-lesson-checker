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


def fetch_rendered_page(page):
    """
    Load the booking page and wait for the booking data API to finish.
    Uses expect_response() BEFORE navigation so we catch the API
    response even though it arrives during page load.
    Returns the fully rendered HTML.
    """
    # Set up response listener BEFORE navigating so we don't miss it
    with page.expect_response(
        lambda resp: "retrieve_bookings_for_calendar" in resp.url,
        timeout=60000
    ) as response_info:
        page.goto(BOOKING_URL, timeout=60000)

    print(f"  Bookings API responded with status: {response_info.value.status}")

    # Wait for availability buttons to appear in the DOM
    try:
        page.wait_for_selector("div.availabilityButtonV2", timeout=20000)
    except Exception:
        print("  Timed out waiting for availability buttons to render.")
        return page.content()

    # Give Vue.js a moment to update the DOM after the API response
    page.wait_for_timeout(3000)

    return page.content()


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

            print("Loading page and waiting for bookings API...")
            html = fetch_rendered_page(page)
            slots, date_label = parse_availability(html)
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
