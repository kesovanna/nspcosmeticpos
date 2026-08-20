"""
gdrive_storage.py — Robust image upload helper for NSP Cosmetic POS.
Handles fallback between Google Drive and Firebase Storage seamlessly.
"""

import os
import traceback
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import firebase_admin
from firebase_admin import storage

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'serviceAccountKey.json')
_SCOPES = ['https://www.googleapis.com/auth/drive']
_drive_service = None

# Correct Firebase Storage Bucket Name for this project
_FIREBASE_BUCKET_NAME = 'nsp-cosmetic-store-pos.firebasestorage.app'


# ---------------------------------------------------------------------------
# Internal helpers (Google Drive)
# ---------------------------------------------------------------------------

def _get_drive_service():
    global _drive_service
    if _drive_service is not None:
        return _drive_service

    if not os.path.exists(_KEY_FILE):
        print(f"[GDrive] ⚠️ serviceAccountKey.json not found at: {_KEY_FILE}")
        return None

    try:
        creds = service_account.Credentials.from_service_account_file(
            _KEY_FILE, scopes=_SCOPES
        )
        _drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        print("[GDrive] ✅ Drive API v3 service initialised successfully.")
        return _drive_service
    except Exception as e:
        print(f"[GDrive] ⚠️ Failed to initialise Drive service: {e}")
        return None


def _make_file_public(service, file_id: str) -> bool:
    try:
        service.permissions().create(
            fileId=file_id,
            body={'role': 'reader', 'type': 'anyone'},
            fields='id',
        ).execute()
        return True
    except Exception as e:
        print(f"[GDrive] ⚠️ Could not set public permission for file {file_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Fallback helper (Firebase Storage)
# ---------------------------------------------------------------------------

def _upload_to_firebase_storage(file_path: str, filename: str) -> str | None:
    """
    Fallback method to upload image to Firebase Storage if Google Drive fails.
    """
    try:
        # Ensure firebase app is initialized
        if not firebase_admin._apps:
            from firebase_admin import credentials
            if os.path.exists(_KEY_FILE):
                cred = credentials.Certificate(_KEY_FILE)
                firebase_admin.initialize_app(cred, {
                    'storageBucket': _FIREBASE_BUCKET_NAME
                })
            else:
                print(f"[Storage Warning] Cannot init Firebase: key file missing.")
                return None

        bucket = storage.bucket(_FIREBASE_BUCKET_NAME)
        blob = bucket.blob(f"products/{filename}")
        
        # Upload file
        blob.upload_from_filename(file_path)
        blob.make_public()
        
        public_url = blob.public_url
        print(f"[Firebase Storage] ✅ Uploaded '{filename}' → {public_url}")
        return public_url

    except Exception as e:
        print(f"[Storage Warning] Firebase Storage upload failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload_image_to_gdrive(
    file_path: str,
    folder_id: str,
    custom_filename: str = None,
) -> str | None:
    """
    Upload a local image file. Tries Google Drive first; if it fails (Quota/Service Account),
    automatically falls back to Firebase Storage.
    """
    if not file_path or not os.path.exists(file_path):
        print(f"[GDrive] ⚠️ File not found, skipping upload: {file_path}")
        return None

    filename = custom_filename or os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()
    _mime_map = {
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.png': 'image/png',  '.gif': 'image/gif',
        '.webp': 'image/webp', '.bmp': 'image/bmp',
        '.svg': 'image/svg+xml',
    }
    mime_type = _mime_map.get(ext, 'application/octet-stream')

    # --- Attempt 1: Google Drive ---
    service = _get_drive_service()
    if service and folder_id and folder_id.strip():
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
        }
        try:
            media = MediaFileUpload(file_path, mimetype=mime_type, resumable=False)
            uploaded = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name',
            ).execute()

            file_id = uploaded.get('id')
            if file_id:
                _make_file_public(service, file_id)
                drive_url = f"https://drive.google.com/uc?export=view&id={file_id}"
                print(f"[GDrive] ✅ Uploaded '{filename}' → {drive_url}")
                return drive_url
        except Exception as e:
            print(f"[GDrive] ⚠️ Upload failed for '{filename}': {e}. Falling back to Firebase Storage...")

    # --- Attempt 2: Firebase Storage Fallback ---
    print(f"[Storage] Attempting upload to Firebase Storage bucket: {_FIREBASE_BUCKET_NAME}")
    return _upload_to_firebase_storage(file_path, filename)


def is_gdrive_url(url: str) -> bool:
    if not url:
        return False
    return str(url).startswith('https://drive.google.com/uc?export=view&id=') or 'firebasestorage.googleapis.com' in str(url)