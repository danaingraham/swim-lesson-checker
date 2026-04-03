"""
Swim Lesson Availability Checker
Checks the Moraga Valley Swim & Tennis Club booking page for
available lessons with teacher Sadie and sends an email notification.
"""

import os
import re
import sys
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup


BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TEACHER_NAME = "Sadie"
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)")


def fetch_page():
    """Fetch the booking page HTML with retry on transient errors."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }
    for attempt in range(3):
        resp = requests.get(BOOKING_URL, headers=headers, timeout=30)
        if resp.status_code == 403 and attempt < 2:
            print(f"Got 403 on attempt {attempt + 1}, retrying in 10s...")
            time.sleep(10)
            continue
        resp.raise_for_status()
        return resp.text
    return None


def parse_availability(html):
    """
    Parse the page for Sadie's available time slots.

    Available (blue) slots are div.availabilityButtonV2 elements that do NOT
    have the 'booked' CSS class. Booked/unavailable (gray) slots have 'booked'.
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(separator="\n")

    # Extract current date from page header
    date_match = re.search(r"DATE\s*\n\s*(\w+ \d+)", page_text)
    current_date = date_match.group(1).strip() if date_match else "Unknown date"

    available_slots = []

    # PRIMARY STRATEGY: Check CSS classes on availability buttons
    # Available (blue) buttons: div.availabilityButtonV2 WITHOUT class "booked"
    # Booked/unavailable (gray): div.availabilityButtonV2 WITH class "booked"
    buttons = soup.find_all("div", class_="availabilityButtonV2")
    for btn in buttons:
        # Only look at Sadie's column
        area_name = btn.find("span", class_="area-name")
        if not area_name or TEACHER_NAME.lower() not in area_name.get_text().lower():
            continue

        # Available = no "booked" class
        if "booked" not in btn.get("class", []):
            # Extract time from the button text (e.g. "2:40 PM Sadie")
            btn_text = btn.get_text()
            time_match = TIME_PATTERN.search(btn_text)
            slot_time = time_match.group(0) if time_match else "Unknown time"

            # Also try the start-time attribute as fallback (military time like "1440")
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

    # FALLBACK STRATEGY: Text-based parsing if no buttons found in HTML
    # (in case the page renders differently server-side)
    if not buttons:
        print("No availabilityButtonV2 elements found -- using text fallback.")
        lines = [line.strip() for line in page_text.split("\n") if line.strip()]
        for i, line in enumerate(lines):
            if line == TEACHER_NAME and i > 0:
                prev_line = lines[i - 1]
                if TIME_PATTERN.match(prev_line):
                    available_slots.append({
                        "time": prev_line,
                        "date": current_date,
                    })

    return available_slots, current_date


def send_email(available_slots, current_date):
    """Send an email notification about available slots."""
    sender_email = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("NOTIFY_EMAIL", sender_email)

    if not sender_email or not app_password:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)

    # Build email body
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
    msg["Subject"] = "Swim Lessons Open -- Sadie has availability!"
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
    # Check if we already sent a notification this week
    already_notified = os.environ.get("ALREADY_NOTIFIED", "").lower() == "true"
    if already_notified:
        print("Already notified this week. Skipping until next Saturday.")
        sys.exit(0)

    print(f"Checking {BOOKING_URL} ...")

    try:
        html = fetch_page()
    except Exception as e:
        print(f"Error fetching page: {e}")
        print("Will retry on next scheduled run.")
        sys.exit(0)  # Exit cleanly so GitHub Actions doesn't mark as failed

    if not html:
        print("Failed to fetch page after retries.")
        sys.exit(0)

    available_slots, current_date = parse_availability(html)

    if available_slots:
        print(f"FOUND {len(available_slots)} available slot(s) with {TEACHER_NAME}!")
        for slot in available_slots:
            print(f"  - {slot['date']} at {slot['time']}")
        try:
            send_email(available_slots, current_date)
            mark_notified()
        except Exception as e:
            print(f"Error sending email: {e}")
            sys.exit(1)
    else:
        print(f"No available slots with {TEACHER_NAME} yet. All full or not posted.")


if __name__ == "__main__":
    main()
