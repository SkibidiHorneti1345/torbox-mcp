import logging

logger = logging.getLogger(__name__)

MEDIA_WHITELIST = {".mkv", ".mp4", ".mp3", ".flac", ".avi", ".webm", ".srt", ".sub"}
EXECUTABLE_BLACKLIST = {".exe", ".bat", ".sh", ".cmd", ".ps1", ".vbs", ".msi", ".scr", ".pif", ".application", ".app", ".dmg"}

def is_safe_file(filename: str, intent: str) -> bool:
    """
    Returns True if the file is considered safe based on the intent.
    Returns False if an executable is found (especially critical for media intent).
    """
    filename_lower = filename.lower()
    
    # Check for executables regardless of intent, but for media it's a hard abort.
    # For software, executables are expected, but maybe we still want to warn?
    # The prompt says: "If user intent = Media ... Immediately abort if any executable types are spotted."
    
    is_executable = any(filename_lower.endswith(ext) for ext in EXECUTABLE_BLACKLIST)
    
    if intent == "media":
        if is_executable:
            logger.error(f"Malicious file detected in media intent: {filename}")
            return False
            
        # Optional: strictly enforce whitelist for media.
        # However, torrents often have .txt, .nfo, etc. 
        # We can just reject executables to be safe, but let's warn if not in whitelist.
        has_whitelisted_ext = any(filename_lower.endswith(ext) for ext in MEDIA_WHITELIST)
        if not has_whitelisted_ext and not any(filename_lower.endswith(ext) for ext in [".txt", ".nfo", ".jpg", ".png"]):
            logger.warning(f"File not in media whitelist: {filename}")
            
        return True # As long as it's not executable, it might be safe (like an NFO).
        
    elif intent == "software":
        # For software, executables are expected, so we don't abort on them.
        return True
        
    return True

def validate_file_tree(files: list[dict], intent: str) -> bool:
    """
    Validates a list of files from TorBox.
    Returns True if safe, False if malicious/executable found in media intent.
    files format expected: [{"id": 1, "name": "movie.mkv", ...}, ...]
    """
    for file_info in files:
        filename = file_info.get("name", "")
        if not is_safe_file(filename, intent):
            return False
    return True
