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

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TEACHER_NAME = "Sadie"


def check_availability(page):
    """Navigate to booking page, wait for render, parse DOM for Sadie's open slots."""
    page.goto(BOOKING_URL, wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".availabilityButtonV2", timeout=30000)
    # Extra wait for bookings data to load and Vue to re-render
    page.wait_for_timeout(5000)

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    buttons = soup.find_all("div", class_="availabilityButtonV2")

    print(f"  Found {len(buttons)} total availability buttons.")

    available = []
    sadie_total = 0

    for btn in buttons:
        area_span = btn.find("span", class_="area-name")
        if not area_span or TEACHER_NAME.lower() not in area_span.get_text().lower():
            continue

        sadie_total += 1
        is_booked = "booked" in btn.get("class", [])

        if not is_booked:
            start_attr = btn.get("start-time", "")
            slot_time = format_time(start_attr)
            available.append({"time": slot_time})

    print(f"  {TEACHER_NAME}: {sadie_total} total, {len(available)} available.")
    return available


def format_time(military):
    """Convert '1400' to '2:00 PM'."""
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

    slot_list = "\n".join(f"  - {s['time']}" for s in slots)
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
            slots = check_availability(page)
            browser.close()
    except Exception as e:
        print(f"Error: {e}")
        print("Will retry on next scheduled run.")
        sys.exit(0)

    if slots:
        print(f"\nFOUND {len(slots)} available slot(s) with {TEACHER_NAME}!")
        for s in slots:
            print(f"  - {s['time']}")
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
