"""Set-up commands for the people who can log in.

    python manage.py create-admin you@company.com "Your name"

Run it once, before anyone else uses the app. It creates the first admin and gives you a link to set the password
(emailed too when SMTP is set up in .env). After that the admin adds everyone else on the Users page.

    python manage.py test-email

Checks the SMTP settings in .env step by step and says where it stops. It sends nothing.
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
    sub.add_parser("test-email", help="check the SMTP settings in .env step by step (sends nothing)")
    args = ap.parse_args(argv)

    if args.cmd == "test-email":
        return 0 if mailer.diagnose() else 1

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
