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
NOTIFIED_FLAG = ".notified"
TIME_PATTERN = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$")


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

    The page text has this pattern for each time row:
      - Available:   "2:40 PM" -> "2:40 PM" -> "Sadie"
        (time appears twice: once as row label, once as clickable button)
      - Unavailable:  "4:00 PM" -> "not available SG" -> "Sadie"
      - Booked:       "2:00 PM" -> "Kristen Sgarlata" -> "Sadie"

    So an available Sadie slot = the line immediately before "Sadie"
    matches a time pattern (the duplicate time from the button).
    """
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(separator="\n")

    date_match = re.search(r"DATE\s*\n\s*(\w+ \d+)", page_text)
    current_date = date_match.group(1).strip() if date_match else "Unknown date"

    lines = page_text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]

    available_slots = []
    seen_filter_tab = False

    for i, line in enumerate(lines):
        # Skip the "Sadie" that appears in the filter tab area
        if line == TEACHER_NAME and not seen_filter_tab:
            # Check if this is in the filter area (near "All options")
            context = " ".join(lines[max(0, i - 3):i])
            if "All options" in context or "Booking rules" in context:
                seen_filter_tab = True
                continue

        if line == TEACHER_NAME and i > 0:
            prev_line = lines[i - 1]

            # AVAILABLE: the line before "Sadie" is a time like "2:40 PM"
            # This is the clickable button text (time appears as button label)
            if TIME_PATTERN.match(prev_line):
                slot_time = prev_line
                # Make sure this isn't the row label time by checking
                # if there's another time 2 lines back (the row label)
                available_slots.append({
                    "time": slot_time,
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


def mark_notified():
    """Create a flag file so the workflow knows we already notified this week."""
    with open(NOTIFIED_FLAG, "w") as f:
        f.write(f"Notified at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
    print(f"Created {NOTIFIED_FLAG} flag - will pause until next Saturday.")


def main():
    # Check if we already notified this week (cache hit from GitHub Actions)
    already_notified = os.environ.get("ALREADY_NOTIFIED", "").lower() == "true"
    if already_notified:
        print("Already notified this week. Skipping until next Saturday.")
        sys.exit(0)

    print(f"Checking {BOOKING_URL} ...")
    try:
        html = fetch_page()
    except requests.exceptions.HTTPError as e:
        print(f"Page returned an error (probably temporary): {e}")
        print("Will try again on the next scheduled run.")
        sys.exit(0)
    except requests.exceptions.RequestException as e:
        print(f"Network error (probably temporary): {e}")
        print("Will try again on the next scheduled run.")
        sys.exit(0)

    if not html:
        print("Could not fetch page after retries. Will try next run.")
        sys.exit(0)

    available_slots, current_date = parse_availability(html)

    if available_slots:
        print(f"FOUND {len(available_slots)} available slot(s) with {TEACHER_NAME}!")
        for slot in available_slots:
            print(f"  - {slot['date']} at {slot['time']}")
        send_email(available_slots, current_date)
        mark_notified()
    else:
        print(f"No available slots with {TEACHER_NAME} yet. All full or not posted.")


if __name__ == "__main__":
    main()
