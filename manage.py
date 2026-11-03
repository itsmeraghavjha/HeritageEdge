"""Set-up commands for the people who can log in.

    python manage.py create-admin you@company.com "Your name"

Run it once, before anyone else uses the app. It creates the one admin and gives you a link to set the password
(emailed too when SMTP is set up in .env). After that the admin adds everyone else on the Users page.
"""
import argparse
import os
import sys

from engine import env  # noqa: F401  (loads .env)
from engine import mailer, users


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="create the admin account and print its set-password link")
    c.add_argument("email")
    c.add_argument("name", nargs="?", default="")
    args = ap.parse_args(argv)

    try:
        uid = users.create(args.email, args.name, role="admin")
    except users.UserError as exc:
        print(f"Not created: {exc}")
        return 1
    user = users.get(uid)
    token = users.issue_token(uid, "invite")
    base = (os.environ.get("APP_BASE_URL") or "http://localhost:5000").rstrip("/")
    link = f"{base}/set-password/{token}"
    sent, why = mailer.send(user["email"], "Your HeritageEdge admin account",
                            f"Set your password here:\n\n{link}\n\nThe link works once and expires in "
                            f"{users.INVITE_HOURS} hours.\n")
    print(f"Admin account created for {user['email']}.")
    print("The link was emailed to you." if sent else f"Email not sent ({why})")
    print(f"Open this link to set the password (works once, expires in {users.INVITE_HOURS} hours):\n\n{link}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
