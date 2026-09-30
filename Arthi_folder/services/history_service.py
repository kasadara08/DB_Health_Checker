import os
import sys
import json
import uuid
import shutil
from datetime import datetime
from services.config_service import CSV_PATH, save_database_configs

def get_data_dir():
    """Returns the absolute path to the data directory."""
    return os.path.dirname(CSV_PATH)

def get_uploads_dir():
    """Returns the path to data/uploads directory, creating it if needed."""
    uploads_dir = os.path.join(get_data_dir(), "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    return uploads_dir

def get_history_file_path():
    """Returns path to data/upload_history.json."""
    return os.path.join(get_data_dir(), "upload_history.json")

def init_history_storage():
    """Initializes uploads directory and history JSON file."""
    get_uploads_dir()
    history_file = get_history_file_path()
    if not os.path.exists(history_file):
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2)

def get_upload_history():
    """Reads and returns upload history list, sorted by upload_time descending."""
    init_history_storage()
    history_file = get_history_file_path()
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                # Normalize field names for legacy compatibility
                for item in data:
                    if "stored_filename" not in item and "saved_file_name" in item:
                        item["stored_filename"] = item["saved_file_name"]
                    if "upload_time" not in item and "upload_date" in item:
                        item["upload_time"] = item["upload_date"]
                    if "db_count" not in item and "database_count" in item:
                        item["db_count"] = item["database_count"]
                return data
            return []
    except Exception as e:
        print(f"Error reading upload_history.json: {e}")
        return []

def save_upload_history(history_list):
    """Saves history list to data/upload_history.json."""
    init_history_storage()
    history_file = get_history_file_path()
    try:
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history_list, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving upload_history.json: {e}")
        return False

def add_upload_record(filename, raw_content, db_configs):
    """
    Saves a new uploaded file independently into data/uploads/,
    overwrites data/databases.csv with this file's DB configs,
    marks all previous history entries INACTIVE and the new entry ACTIVE.
    """
    init_history_storage()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    upload_id = f"upload_{timestamp_str}_{short_uuid}"

    # Clean filename for local storage
    safe_filename = "".join([c if c.isalnum() or c in ('.', '_', '-') else '_' for c in filename])
    stored_filename = f"{upload_id}_{safe_filename}"
    stored_filepath = os.path.join(get_uploads_dir(), stored_filename)

    # Save physical copy in uploads folder
    with open(stored_filepath, 'w', encoding='utf-8') as f:
        f.write(raw_content)

    # Overwrite data/databases.csv with only the new database configuration
    save_database_configs(db_configs)

    # Extract db_ids
    db_ids = [cfg.get("db_id") for cfg in db_configs if cfg.get("db_id")]

    history = get_upload_history()
    # Mark all previous entries as INACTIVE
    for item in history:
        item["status"] = "INACTIVE"

    # Format upload time
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    new_entry = {
        "upload_id": upload_id,
        "filename": filename,
        "stored_filename": stored_filename,
        "upload_time": formatted_time,
        "db_count": len(db_configs),
        "db_ids": db_ids,
        "status": "ACTIVE"
    }

    # Prepend new active record
    history.insert(0, new_entry)
    save_upload_history(history)
    return new_entry

def restore_upload_record(upload_id):
    """
    Restores a previous uploaded configuration file to become ACTIVE,
    overwriting data/databases.csv with its content and setting all others INACTIVE.
    """
    history = get_upload_history()
    target_entry = None

    for item in history:
        if item.get("upload_id") == upload_id:
            target_entry = item
            break

    if not target_entry:
        return False, "Upload history record not found."

    stored_filename = target_entry.get("stored_filename")
    stored_filepath = os.path.join(get_uploads_dir(), stored_filename)

    if not os.path.exists(stored_filepath):
        return False, f"Archived file '{stored_filename}' was not found on server storage."

    # Read archived content and parse/save to databases.csv
    try:
        from app import parse_db_file_content
        db_configs = parse_db_file_content(open(stored_filepath, 'r', encoding='utf-8-sig').read())
    except Exception:
        # Fallback manual line reader if import fails
        db_configs = []
        with open(stored_filepath, 'r', encoding='utf-8-sig') as f:
            content = f.read()
            import csv, io
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                clean_row = {k.strip(): v.strip() for k, v in row.items() if k}
                if clean_row.get('db_id'):
                    db_configs.append(clean_row)

    if not db_configs:
        return False, "Failed to parse database configurations from archived file."

    # Overwrite databases.csv
    save_database_configs(db_configs)

    # Update statuses in history
    for item in history:
        if item.get("upload_id") == upload_id:
            item["status"] = "ACTIVE"
            item["db_count"] = len(db_configs)
            item["db_ids"] = [cfg.get("db_id") for cfg in db_configs if cfg.get("db_id")]
        else:
            item["status"] = "INACTIVE"

    save_upload_history(history)
    return True, target_entry

def delete_upload_record(upload_id):
    """
    Deletes an entry from upload history and removes its file from data/uploads/.
    If the deleted item was ACTIVE, activates the next most recent upload.
    """
    history = get_upload_history()
    target_index = -1

    for idx, item in enumerate(history):
        if item.get("upload_id") == upload_id:
            target_index = idx
            break

    if target_index == -1:
        return False, "Upload history record not found."

    deleted_entry = history.pop(target_index)
    stored_filename = deleted_entry.get("stored_filename")
    stored_filepath = os.path.join(get_uploads_dir(), stored_filename)

    if os.path.exists(stored_filepath):
        try:
            os.remove(stored_filepath)
        except Exception as e:
            print(f"Warning: Could not remove archived file {stored_filepath}: {e}")

    # If the deleted entry was ACTIVE, make the top remaining item ACTIVE and restore its DBs
    if deleted_entry.get("status") == "ACTIVE":
        if history:
            history[0]["status"] = "ACTIVE"
            restore_upload_record(history[0]["upload_id"])
        else:
            save_database_configs([])

    save_upload_history(history)
    return True, "History entry deleted successfully."

def clear_all_history():
    """
    Clears all upload history records and removes archived upload files from data/uploads/.
    Ensures a clean state on fresh reload.
    """
    init_history_storage()
    uploads_dir = get_uploads_dir()
    if os.path.exists(uploads_dir):
        for filename in os.listdir(uploads_dir):
            file_path = os.path.join(uploads_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Error removing upload file {file_path}: {e}")
    save_upload_history([])
