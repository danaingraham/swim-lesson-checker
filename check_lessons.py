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

We also check multiple dates since the default date may be fully booked
while other dates within the 1-7 day booking window have openings.
"""

import os
import re
import sys
import smtplib
import time as _time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TEACHER_NAME = "Sadie"
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)")


def get_bookable_dates():
    """
    Return the list of bookable dates based on the booking rules:
    - At least 1 day in advance, no more than 7
    - Only Monday-Friday
    """
    today = datetime.now().date()
    dates = []
    for offset in range(1, 8):
        d = today + timedelta(days=offset)
        if d.weekday() < 5:  # Mon=0 ... Fri=4
            dates.append(d)
    return dates


def fetch_rendered_page(page, date_str=None):
    """
    Load the booking page and wait for the booking data API to finish.
    If date_str is provided, click the date picker and select that date.
    Returns the fully rendered HTML.
    """
    if date_str is None:
        # Fresh page load
        page.goto(BOOKING_URL, wait_until="networkidle", timeout=60000)

    # Wait for availability buttons to appear
    try:
        page.wait_for_selector("div.availabilityButtonV2", timeout=20000)
    except Exception:
        print("  Timed out waiting for availability buttons to render.")
        return page.content()

    # CRITICAL: Wait for the bookings API response which determines
    # which slots are booked vs available. Without this, all buttons
    # initially appear as "booked" before the real data loads.
    try:
        page.wait_for_response(
            lambda resp: "retrieve_bookings_for_calendar" in resp.url,
            timeout=15000
        )
    except Exception:
        print("  Timed out waiting for bookings API response.")

    # Give Vue.js a moment to update the DOM after the API response
    page.wait_for_timeout(2000)

    return page.content()


def click_date(page, target_date):
    """
    Click the date picker and select a specific date.
    The date picker input shows e.g. "April 3".
    """
    month_name = target_date.strftime("%B")  # e.g. "April"
    day_num = target_date.day  # e.g. 3
    target_label = f"{month_name} {day_num}"

    # Click the date input to open the picker
    date_input = page.query_selector('input.pointer')
    if not date_input:
        print(f"  Could not find date picker input.")
        return False

    date_input.click()
    page.wait_for_timeout(500)

    # Look for the target date in the date picker popup
    # The picker shows calendar days - try clicking the day number
    try:
        # Try to find and click the date in a calendar popup
        day_cell = page.locator(f'text="{day_num}"').first
        if day_cell:
            day_cell.click()
            page.wait_for_timeout(1000)

            # Wait for new data to load
            try:
                page.wait_for_response(
                    lambda resp: "retrieve_bookings_for_calendar" in resp.url,
                    timeout=15000
                )
            except Exception:
                pass
            page.wait_for_timeout(2000)
            return True
    except Exception as e:
        print(f"  Could not select date {target_label}: {e}")

    return False


def parse_availability(html):
    """
    Parse the rendered page for Sadie's available time slots.

    Available (blue) slots are div.availabilityButtonV2 elements that do NOT
    have the 'booked' CSS class. Booked/unavailable (gray) slots have 'booked'.
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(separator="\n")

    # Extract current date from the date input field
    date_input = soup.find("input", class_="pointer")
    if date_input and date_input.get("value"):
        current_date = date_input["value"]
    else:
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

            # Load the default page first
            print("Loading default date...")
            html = fetch_rendered_page(page)
            slots, date_label = parse_availability(html)
            all_available.extend(slots)

            # Now try other bookable dates
            bookable_dates = get_bookable_dates()
            print(f"Will also check dates: {[d.strftime('%B %d') for d in bookable_dates]}")

            for target_date in bookable_dates:
                date_label_check = target_date.strftime("%B %-d")
                # Skip if we already checked this date (the default)
                if date_label_check in [s["date"] for s in all_available] or date_label == date_label_check:
                    if date_label == date_label_check:
                        print(f"  Skipping {date_label_check} (already checked as default).")
                        continue

                print(f"Checking {date_label_check}...")
                if click_date(page, target_date):
                    html = page.content()
                    slots, _ = parse_availability(html)
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
        print(f"\nNo available slots with {TEACHER_NAME} on any date. All full or not posted.")


if __name__ == "__main__":
    main()
