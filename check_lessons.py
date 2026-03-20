"""
Swim Lesson Availability Checker
Checks the Moraga Valley Swim & Tennis Club booking page for
available lessons with teacher Sadie and sends an email notification.
"""

import os
import re
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup


BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TEACHER_NAME = "Sadie"


def fetch_page():
    """Fetch the booking page HTML."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(BOOKING_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_availability(html):
    """
    Parse the page for Sadie's available time slots.
    Returns a list of dicts with 'time' and 'date' for each available slot.
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(separator="\n")

    # --- Detect available dates from the calendar ---
    # The page shows a date in the header area. If multiple dates are
    # selectable, they show as highlighted calendar cells.
    available_dates = []
    date_match = re.search(r"DATE\s*\n\s*(\w+ \d+)", page_text)
    current_date = date_match.group(1).strip() if date_match else "Unknown date"

    # --- Parse the time slot grid ---
    # The grid shows rows like:
    #   10:00 AM   Not available SG  Sadie   Not available AK  Avery
    # When a slot IS available, it won't say "Not available" before the teacher name.
    available_slots = []

    # Strategy 1: Look at the raw text for time + availability patterns
    lines = page_text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]

    current_time = None
    for i, line in enumerate(lines):
        # Match time slots like "10:00 AM", "1:20 PM", etc.
        time_match = re.match(r"^(\d{1,2}:\d{2}\s*(?:AM|PM))$", line)
        if time_match:
            current_time = time_match.group(1)
            continue

        # If we see "Sadie" and the previous line is NOT "Not available SG"
        if TEACHER_NAME.lower() in line.lower() and current_time:
            # Check if the preceding context indicates availability
            preceding = lines[max(0, i - 2) : i]
            preceding_text = " ".join(preceding).lower()

            if "not available" not in preceding_text:
                available_slots.append({
                    "time": current_time,
                    "date": current_date,
                })

    # Strategy 2: Also check for explicit "Book" or "Available" or "Reserve"
    # buttons/text near Sadie's name, in case the HTML structure differs
    for tag in soup.find_all(string=re.compile(r"Sadie", re.I)):
        parent = tag.find_parent()
        if parent:
            sibling_text = parent.get_text().lower()
            if any(word in sibling_text for word in ["book", "reserve", "available", "open"]):
                # Found a bookable slot
                time_el = parent.find_previous(string=re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)"))
                slot_time = time_el.strip() if time_el else "Unknown time"
                if not any(s["time"] == slot_time for s in available_slots):
                    available_slots.append({
                        "time": slot_time,
                        "date": current_date,
                    })

    # Strategy 3: Count total Sadie slots vs "Not available" Sadie slots
    # If the counts differ, something is available
    total_sadie_mentions = len(re.findall(r"Sadie", page_text, re.I))
    not_available_sadie = len(re.findall(r"Not available.*?Sadie", page_text, re.I))

    # The teacher name appears in the filter tab and upcoming reservations too,
    # so subtract those (roughly 2-3 extra mentions)
    filter_mentions = 3  # filter tab + header + upcoming section
    data_sadie_mentions = max(0, total_sadie_mentions - filter_mentions)

    if data_sadie_mentions > not_available_sadie and not available_slots:
        # There are more Sadie mentions than "Not available" ones
        # This means some slots are available but we couldn't parse the exact times
        available_slots.append({
            "time": "Check the page for exact times",
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
    msg["Subject"] = "Swim Lessons Open — Sadie has availability!"
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, app_password)
        server.sendmail(sender_email, recipient, msg.as_string())

    print(f"Email sent to {recipient}!")


def main():
    print(f"Checking {BOOKING_URL} ...")
    html = fetch_page()
    available_slots, current_date = parse_availability(html)

    if available_slots:
        print(f"FOUND {len(available_slots)} available slot(s) with {TEACHER_NAME}!")
        for slot in available_slots:
            print(f"  - {slot['date']} at {slot['time']}")
        send_email(available_slots, current_date)
    else:
        print(f"No available slots with {TEACHER_NAME} yet. All full or not posted.")


if __name__ == "__main__":
    main()
