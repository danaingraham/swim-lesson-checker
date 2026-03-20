# Swim Lesson Checker

Automatically checks the Moraga Valley Swim & Tennis Club booking page for available lessons with **Sadie** and emails you when slots open up. Runs on GitHub Actions — no need to keep your computer on.

## Setup (5 minutes)

### Step 1: Create a Gmail App Password

You need an "App Password" so the script can send emails from your Gmail. This is different from your regular password.

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. You may need to enable 2-Factor Authentication first if you haven't already
3. Enter a name like "Swim Lesson Checker"
4. Click **Create**
5. Copy the 16-character password it gives you (you'll need it in Step 3)

### Step 2: Create the GitHub Repository

1. Go to [github.com/new](https://github.com/new)
2. Name it `swim-lesson-checker`
3. Set it to **Private**
4. Click **Create repository**
5. Upload all the files from this folder (drag and drop works), or use git:

```bash
cd swim-lesson-checker
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/swim-lesson-checker.git
git push -u origin main
```

### Step 3: Add Secrets

1. In your GitHub repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add these three:

| Secret Name          | Value                                |
|---------------------|--------------------------------------|
| `GMAIL_ADDRESS`     | Your Gmail address (e.g. danabressler@gmail.com) |
| `GMAIL_APP_PASSWORD`| The 16-character app password from Step 1 |
| `NOTIFY_EMAIL`      | Where to send alerts (e.g. danabressler@gmail.com) |

### Step 4: Enable the Workflow

1. Go to the **Actions** tab in your repo
2. You should see the "Check Swim Lessons" workflow
3. Click **Enable** if prompted
4. Click **Run workflow** → **Run workflow** to test it manually

That's it! The workflow will now run every 30 minutes on weekends automatically.

## How It Works

- Every 30 minutes on Saturday and Sunday, GitHub runs the Python script
- The script fetches the booking page and checks if any of Sadie's time slots are available
- If slots are found → you get an email with the times and a direct booking link
- If nothing's open → it stays quiet (no spam)

## Customization

- **Change frequency**: Edit the `cron` lines in `.github/workflows/check-lessons.yml`
- **Change teacher**: Edit `TEACHER_NAME` in `check_lessons.py`
- **Stop checking**: Disable the workflow in GitHub Actions (Actions tab → click workflow → "..." → Disable)
