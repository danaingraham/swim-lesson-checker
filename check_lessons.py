import os
import re
import sys
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright

BOOKING_URL = "https://moragavalleyswimtennisclub.theclubspot.com/reserve/LtrQVDM3b8"
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(AM|PM)", re.IGNORECASE)
DAYS_TO_CHECK = 8


def get_unlocked_days(page):
    """Open calendar, wait for lock states to settle, return unlocked day data-time values."""
    page.click("#reserve-date-picker")
    # Wait for the litepicker to apply is-locked classes (happens asynchronously)
    page.wait_for_timeout(3000)

    today = datetime.utcnow().date()
    unlocked = []

    days = page.locator(".day-item:not(.is-previous-month):not(.is-next-month)")
    for i in range(days.count()):
        day_el = days.nth(i)
        day_num = day_el.text_content().strip()
        classes = day_el.get_attribute("class") or ""
        data_time = day_el.get_attribute("data-time") or ""

        if "is-locked" in classes:
            continue

        # Determine the actual date from data-time (ms timestamp)
        if data_time:
            from datetime import timezone
            ts = int(data_time) / 1000
            day_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            offset = (day_date - today).days
            if 1 <= offset <= DAYS_TO_CHECK and day_date.weekday() < 5:
                unlocked.append(data_time)
                print(f"  Day {day_num} ({day_date}) is unlocked")

    # Close the calendar
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    return unlocked


def check_date_by_timestamp(page, data_time):
    """Click a specific day in the litepicker by its data-time attribute."""
    page.click("#reserve-date-picker")
    page.wait_for_timeout(1000)

    day_el = page.locator(f'.day-item[data-time="{data_time}"]')
    if day_el.count() == 0:
        print(f"  Day element not found for {data_time}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        return []

    day_el.click(force=True)
    page.wait_for_timeout(4000)

    date_label = page.input_value("#reserve-date-picker")
    print(f"Checking {date_label}...")

    # Check for available slots
    slots = []
    seen = set()
    buttons = page.locator(".availabilityButtonV2")
    count = buttons.count()

    for i in range(count):
        btn = buttons.nth(i)
        text = (btn.text_content() or "").strip()
        classes = btn.get_attribute("class") or ""
        if "booked" in classes:
            continue
        match = TIME_PATTERN.search(text)
        if not match:
            continue

        time_str = match.group(0)
        teacher = text.replace(time_str, "").strip()
        key = (time_str, teacher)
        if key in seen:
            continue
        seen.add(key)

        slots.append({
            "date": date_label,
            "time": time_str,
            "teacher": teacher,
        })

    if slots:
        print(f"  FOUND {len(slots)} available slot(s)!")
    else:
        print(f"  No available slots.")
    return slots


def find_available_slots(page):
    page.goto(BOOKING_URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(5000)

    print("Scanning calendar for unlocked days...")
    unlocked = get_unlocked_days(page)

    if not unlocked:
        print("No unlocked days found in the next week.")
        return []

    print(f"Found {len(unlocked)} unlocked day(s), checking each...\n")

    all_slots = []
    for data_time in unlocked:
        all_slots.extend(check_date_by_timestamp(page, data_time))

    return all_slots


def send_email(slots):
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("NOTIFY_EMAIL", sender).strip()

    if not sender or not password:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set.")
        sys.exit(1)

    slot_lines = []
    current_date = ""
    for s in slots:
        if s["date"] != current_date:
            current_date = s["date"]
            slot_lines.append(f"\n{current_date}:")
        teacher = f" ({s['teacher']})" if s["teacher"] else ""
        slot_lines.append(f"  - {s['time']}{teacher}")

    body = (
        f"Hi Dana!\n\n"
        f"Swim lessons just opened up! Here's what's available:\n"
        f"{''.join(slot_lines)}\n\n"
        f"Book now: {BOOKING_URL}\n\n"
        f"-- Swim Lesson Checker Bot\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = "Swim Lessons Are Open!"

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.send_message(msg)

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
            slots = find_available_slots(page)
            browser.close()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("Will retry on next scheduled run.")
        sys.exit(0)

    if slots:
        print(f"\nFOUND {len(slots)} available slot(s)!")
        for s in slots:
            teacher = f" ({s['teacher']})" if s["teacher"] else ""
            print(f"  - {s['date']} at {s['time']}{teacher}")
        try:
            send_email(slots)
            with open(".notified", "w") as f:
                f.write("notified")
        except Exception as e:
            print(f"Error sending email: {e}")
            sys.exit(1)
    else:
        print("\nNo available slots found.")


if __name__ == "__main__":
    main()
