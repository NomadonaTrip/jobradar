#!/usr/bin/env python3
"""
Auto-Import — polls Google Drive for new onboarding submissions.

Reads JSON files from the 'Onboarding_Inbox' Drive folder, imports each
customer via manage.do_import(), then moves the file to 'Onboarding_Processed'.

Requires:
    - Google Cloud service account with Drive API enabled
    - service_account.json in project root
    - Onboarding_Inbox folder shared with the service account email

Usage:
    python auto_import.py              # import all pending submissions
    python auto_import.py --dry-run    # preview without importing
"""

import argparse
import json
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

ROOT = Path(__file__).resolve().parent
SERVICE_ACCOUNT_FILE = ROOT / "service_account.json"

SCOPES = ["https://www.googleapis.com/auth/drive"]
INBOX_FOLDER = "Onboarding_Inbox"
PROCESSED_FOLDER = "Onboarding_Processed"


def get_drive_service():
    """Authenticate and return a Google Drive API service."""
    if not SERVICE_ACCOUNT_FILE.exists():
        print("  ERROR: service_account.json not found.")
        print("  Setup: https://console.cloud.google.com → APIs → Drive API → Service Account")
        print(f"  Expected at: {SERVICE_ACCOUNT_FILE}")
        sys.exit(1)

    creds = service_account.Credentials.from_service_account_file(
        str(SERVICE_ACCOUNT_FILE), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def find_folder(service, name: str) -> str | None:
    """Find a folder by name in Drive. Returns folder ID or None."""
    resp = service.files().list(
        q=f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        spaces="drive",
        fields="files(id, name)",
    ).execute()
    files = resp.get("files", [])
    return files[0]["id"] if files else None


def get_or_create_folder(service, name: str) -> str:
    """Get folder ID by name, creating it if it doesn't exist."""
    folder_id = find_folder(service, name)
    if folder_id:
        return folder_id

    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    print(f"  Created Drive folder: {name}")
    return folder["id"]


def list_json_files(service, folder_id: str) -> list[dict]:
    """List all JSON files in a Drive folder."""
    resp = service.files().list(
        q=f"'{folder_id}' in parents and mimeType = 'application/json' and trashed = false",
        spaces="drive",
        fields="files(id, name)",
        orderBy="createdTime",
    ).execute()
    return resp.get("files", [])


def download_json(service, file_id: str) -> dict:
    """Download a JSON file from Drive and parse it."""
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buffer.seek(0)
    return json.loads(buffer.read().decode("utf-8"))


def move_file(service, file_id: str, dest_folder_id: str):
    """Move a file to a different folder (remove from current parents, add to dest)."""
    file = service.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=dest_folder_id,
        removeParents=previous_parents,
        fields="id, parents",
    ).execute()


def run(dry_run: bool = False):
    print("=" * 60)
    print("  Auto-Import — checking Drive for new submissions")
    print("=" * 60)

    service = get_drive_service()

    # Find inbox folder
    inbox_id = find_folder(service, INBOX_FOLDER)
    if not inbox_id:
        print(f"\n  No '{INBOX_FOLDER}' folder found in Drive. Nothing to import.")
        print("  Make sure the folder is shared with the service account email.")
        return

    # List pending files
    files = list_json_files(service, inbox_id)
    if not files:
        print("\n  No new submissions in inbox.")
        return

    print(f"\n  Found {len(files)} submission(s) to import.\n")

    # Lazy import — only needed if we actually have files to process
    from manage import do_import

    # Get or create processed folder
    processed_id = None
    if not dry_run:
        processed_id = get_or_create_folder(service, PROCESSED_FOLDER)

    imported = 0
    skipped = 0
    for f in files:
        fname = f["name"]
        fid = f["id"]

        print(f"  Processing: {fname}")
        try:
            data = download_json(service, fid)
        except Exception as e:
            print(f"    ERROR downloading: {e}")
            skipped += 1
            continue

        if dry_run:
            name = f"{data.get('firstName', '?')} {data.get('lastName', '?')}"
            print(f"    [DRY RUN] Would import: {name}")
            print(f"    Roles: {', '.join(data.get('roles', []))}")
            imported += 1
            continue

        try:
            customer_dir = do_import(data)
            print(f"    Imported → {customer_dir.name}")
            imported += 1
        except FileExistsError:
            print(f"    SKIPPED — customer already exists")
            skipped += 1
        except Exception as e:
            print(f"    ERROR importing: {e}")
            skipped += 1
            continue

        # Move to processed folder
        try:
            move_file(service, fid, processed_id)
            print(f"    Moved to {PROCESSED_FOLDER}/")
        except Exception as e:
            print(f"    WARNING: could not move file: {e}")

    print(f"\n  Done: {imported} imported, {skipped} skipped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-import customers from Google Drive")
    parser.add_argument("--dry-run", action="store_true", help="Preview without importing")
    args = parser.parse_args()

    run(dry_run=args.dry_run)
