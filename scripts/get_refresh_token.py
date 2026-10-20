"""
get_refresh_token.py
--------------------
One-time LOCAL helper. Opens a browser, lets you log in to the Google account
that owns the Blogger blog, and prints a REFRESH TOKEN to the terminal.

The refresh token lets GitHub Actions post without you being present. It is
printed to the terminal only - this script never writes it to a file, so it
cannot be committed by accident.

Run locally:
    1. Copy .env.example to .env and fill BLOGGER_CLIENT_ID and
       BLOGGER_CLIENT_SECRET (from Google Cloud Console).
    2. python scripts/get_refresh_token.py
    3. Copy the printed token into:
       - your local .env as BLOGGER_REFRESH_TOKEN
       - GitHub repo secret BLOGGER_REFRESH_TOKEN
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

# The only scope we need.
SCOPES = ["https://www.googleapis.com/auth/blogger"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def main() -> None:
    client_id = os.environ.get("BLOGGER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("BLOGGER_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print(
            "ERROR: BLOGGER_CLIENT_ID and BLOGGER_CLIENT_SECRET must be set in "
            "your local .env file before running this script."
        )
        return

    # Build a client config dict from the .env values, so no
    # client_secret*.json file is required.
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    # access_type="offline" + prompt="consent" guarantees a refresh token even
    # if this account already authorized the app before.
    credentials = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening your browser for Google login...",
    )

    print("\n" + "=" * 64)
    print("REFRESH TOKEN (copy this now - it is shown only once):")
    print("=" * 64)
    print(credentials.refresh_token)
    print("=" * 64)
    print(
        "\nNext steps:\n"
        "  1. Put it in your local .env as BLOGGER_REFRESH_TOKEN=...\n"
        "  2. Add it as a GitHub Actions secret named BLOGGER_REFRESH_TOKEN\n"
        "  3. Publish your OAuth consent screen to Production (Testing mode\n"
        "     tokens expire after ~7 days)."
    )


if __name__ == "__main__":
    main()
