"""Google Drive upload through a service account."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _build_service(account_json: str):
    """Create a Drive client from the service account JSON payload."""
    info = json.loads(account_json)
    # Some dashboards store the key with escaped newlines.
    if isinstance(info.get("private_key"), str):
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _ensure_folder(service, folder_id: str, name: str) -> str:
    """Return the id of the target folder, creating it when needed."""
    if folder_id:
        return folder_id
    query = f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    existing = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
    files = existing.get("files", [])
    if files:
        return files[0]["id"]
    created = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return created["id"]


def upload(
    file_path: Path,
    account_json: str,
    folder_id: str = "",
    folder_name: str = "youtube-shorts-factory",
) -> str:
    """Upload a file to Drive and return its web link."""
    service = _build_service(account_json)
    target_folder = _ensure_folder(service, folder_id, folder_name)

    media = MediaFileUpload(str(file_path), mimetype="video/mp4", resumable=True)
    created = service.files().create(
        body={"name": file_path.name, "parents": [target_folder]},
        media_body=media,
        fields="id, name, webViewLink",
    ).execute()

    link = created.get("webViewLink") or f"https://drive.google.com/file/d/{created.get('id')}"
    log.info("uploaded %s to Drive: %s", file_path.name, link)
    return link
