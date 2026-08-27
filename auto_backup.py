import os
import shutil
import glob
from datetime import datetime

# Captured before main.py wraps stdout, then replayed into Project Live Console.
BACKUP_LOGS = []


def record_log(message):
    """Print a backup status line and keep it for the web live console."""
    text = str(message)
    print(text, flush=True)
    BACKUP_LOGS.append(text)


def drain_logs_for_console():
    """Return captured backup lines once so they can be streamed to the UI."""
    lines = [str(line).rstrip('\n') for line in BACKUP_LOGS if str(line).strip()]
    BACKUP_LOGS.clear()
    return lines


def backup_database():
    """
    Backs up the local SQLite database (pos_local.db) to a 'backups' folder.
    Keeps only the 10 most recent backups to save space.
    """
    try:
        # Determine the base directory (works for both dev and PyInstaller)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, 'pos_local.db')
        
        if not os.path.exists(db_path):
            record_log(f"⚠️ [Backup] No database found at {db_path}. Skipping backup.")
            return

        # Create backups directory if it doesn't exist
        backup_dir = os.path.join(base_dir, 'backups')
        os.makedirs(backup_dir, exist_ok=True)

        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"pos_local_backup_{timestamp}.db"
        backup_path = os.path.join(backup_dir, backup_filename)

        # Copy the database file
        shutil.copy2(db_path, backup_path)
        record_log(f"✅ [Backup] Database successfully backed up to: {backup_path}")

        # Cleanup older backups (keep only the latest 10)
        # Find all backup files matching the pattern
        search_pattern = os.path.join(backup_dir, 'pos_local_backup_*.db')
        existing_backups = glob.glob(search_pattern)
        
        # Sort by modification time (oldest first)
        existing_backups.sort(key=os.path.getmtime)
        
        # If we have more than 10 backups, delete the oldest ones
        max_backups = 10
        if len(existing_backups) > max_backups:
            backups_to_delete = existing_backups[:-max_backups]
            for old_backup in backups_to_delete:
                try:
                    os.remove(old_backup)
                    record_log(f"🧹 [Backup] Cleaned up old backup: {os.path.basename(old_backup)}")
                except Exception as e:
                    record_log(f"⚠️ [Backup] Failed to delete old backup {old_backup}: {e}")

    except Exception as e:
        record_log(f"❌ [Backup] Error during database backup: {e}")

if __name__ == '__main__':
    backup_database()
