"""
Filename Utilities for Auto Rename Feature
Handles auto renaming of files for mirror operations
"""

import json
import os
import re
import subprocess
from logging import getLogger

from aiofiles.os import path as aiopath
from aiofiles.os import rename

from bot import LOGGER
from bot.helper.ext_utils.bot_utils import sync_to_async
from bot.helper.ext_utils.media_utils import get_media_info

LOGGER = getLogger(__name__)


async def apply_auto_rename_to_path(path: str, listener) -> str:
    """
    Apply Auto Rename functionality to files/directories for mirror operations.
    This replicates the Auto Rename logic from TelegramUploader for mirror uploads.
    
    Args:
        path (str): Path to file or directory
        listener: Task listener object with user_dict
        
    Returns:
        str: Updated path (may be the same if no changes)
    """
    if not hasattr(listener, 'user_dict'):
        return path
        
    user_dict = getattr(listener, 'user_dict', {})
    auto_rename = user_dict.get('AUTO_RENAME', False)
    
    if not auto_rename:
        return path
        
    template = user_dict.get('RENAME_TEMPLATE', 'S{season}E{episode}Q{quality}')
    episode = int(user_dict.get('_CURRENT_EPISODE', user_dict.get('START_EPISODE', 1)))
    season = int(user_dict.get('START_SEASON', 1))
    
    LOGGER.info(f"Applying Auto Rename to: {path}")
    
    try:
        if await aiopath.isfile(path):
            # Single file
            new_path = await _auto_rename_single_file(path, template, episode, season, user_dict)
            return new_path if new_path else path
            
        elif await aiopath.isdir(path):
            # Directory - rename all files recursively
            from os import walk
            for root, dirs, files in await sync_to_async(walk, path, topdown=False):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        await _auto_rename_single_file(file_path, template, episode, season, user_dict)
                        # Update episode for next file
                        episode = user_dict.get('_CURRENT_EPISODE', episode + 1)
                    except Exception as e:
                        LOGGER.error(f"Error auto-renaming file {file_path}: {e}")
            
            return path
    
    except Exception as e:
        LOGGER.error(f"Error applying auto rename to {path}: {e}")
    
    return path


async def _auto_rename_single_file(file_path: str, template: str, episode: int, season: int, user_dict: dict):
    """
    Apply Auto Rename to a single file
    
    Returns:
        str: New file path if renamed, original path if not renamed
    """
    from bot.modules.imdb import get_poster
    
    old_filename = os.path.basename(file_path)
    directory = os.path.dirname(file_path)
    
    try:
        # Get media quality
        _, quality, lang, _ = await get_media_info(file_path, True)
        quality = str(quality).replace('p', '') if quality else ''
        
        # Clean filename to get probable title
        def clean_filename_for_title(filename):
            # Try to extract meaningful title using extract_media_info if available
            try:
                from bot.helper.mirror_leech_utils.telegram_uploader import extract_media_info
                name, _season, _episode, year, part, volume = extract_media_info(filename)
                # If we have both name and year, return 'Name Year'
                if name and year:
                    result = f"{name} {year}"
                elif name:
                    result = name
                else:
                    raise Exception("No name from extract_media_info")
            except:
                # Fallback to old logic if extract_media_info fails or doesn't exist
                name = os.path.splitext(filename)[0]
                name = re.sub(r'[\[\](){}⟨⟩【】『』""''«»‹›❮❯❰❱❲❳❴❵]', ' ', name)
                name = re.sub(r'\s+', ' ', name).strip()
                result = name if name else 'Unknown'
            LOGGER.info(f"Final cleaned title for lookups: '{result}'")
            return result
            
        probable_title = clean_filename_for_title(old_filename)
        
        # Fetch IMDB info
        imdb_info = None
        imdb_data = {}
        if probable_title:
            imdb_info = get_poster(probable_title)
        if imdb_info:
            imdb_data = {
                'title': imdb_info.get('title', ''),
                'year': imdb_info.get('year', ''),
                'rating': imdb_info.get('rating', '').replace(' / 10', ''),
                'genre': imdb_info.get('genres', ''),
            }
        else:
            imdb_data = {'title': probable_title, 'year': '', 'rating': '', 'genre': ''}
            
        # Get audio language(s)
        audio_count = 0
        audio = lang or ''
        try:
            ffprobe_cmd = [
                'ffprobe', '-v', 'error', '-select_streams', 'a', 
                '-show_entries', 'stream=index', '-of', 'json', file_path
            ]
            ffprobe_out = subprocess.run(ffprobe_cmd, capture_output=True, text=True, timeout=30)
            if ffprobe_out.returncode == 0:
                audio_json = json.loads(ffprobe_out.stdout)
                audio_count = len(audio_json.get('streams', []))
            if audio_count >= 2:
                audio = 'MultiAuD'
        except Exception:
            pass
            
        # Merge all fields for template - episode will be updated later
        template_fields = dict(
            season=season,
            episode2=episode,  # integer, for E1, E2, ...
            episode=f"{episode:02d}",  # zero-padded, for E01, E02, ...
            quality=quality,
            audio=audio,
            **imdb_data
        )
        
        # Check if this is a multi-resolution file (contains pattern like _720p_BL, _480p_BL, etc.)
        is_multi_resolution_file = bool(re.search(r'_\d+p_BL\.', old_filename))
        
        # Handle episode numbering for multi-resolution files
        if is_multi_resolution_file:
            # Check if we've already processed a file from this batch
            base_filename = re.sub(r'_\d+p_BL\.', '.', old_filename)  # Remove quality suffix
            batch_key = f"multi_res_batch_{base_filename}"
            
            # For multi-resolution batch, use the same episode number for all files
            if not user_dict.get(batch_key, False):
                # First file in batch - increment and store the episode number
                user_dict['_CURRENT_EPISODE'] = episode + 1
                user_dict[batch_key] = episode  # Store the episode number to use for this batch
                current_episode = episode  # Use original episode for this batch
                LOGGER.info(f"Multi-resolution batch detected. Episode {episode} will be used for all files in batch: {base_filename}")
            else:
                # Subsequent files in batch - use the same episode number as the first file
                current_episode = user_dict[batch_key]
                LOGGER.info(f"Multi-resolution file from same batch, using episode {current_episode}: {old_filename}")
            
            # Clean up old batch keys to prevent memory buildup (keep only recent 50 keys)
            batch_keys = [k for k in user_dict.keys() if k.startswith("multi_res_batch_")]
            if len(batch_keys) > 50:
                # Remove oldest batch keys (simple cleanup)
                for key in sorted(batch_keys)[:-50]:
                    user_dict.pop(key, None)
                    
            # Update template fields with correct episode
            template_fields['episode2'] = current_episode
            template_fields['episode'] = f"{current_episode:02d}"
        else:
            # Regular single file - increment episode counter normally
            user_dict['_CURRENT_EPISODE'] = episode + 1
            LOGGER.info(f"Single file processed. Using episode {episode}, next will be {episode + 1}: {old_filename}")
            
        new_name = template.format(**template_fields)
        ext = os.path.splitext(old_filename)[1]
        new_filename = f"{new_name}{ext}"
        new_file_path = os.path.join(directory, new_filename)
        
        # Rename the file if name changed
        if file_path != new_file_path and os.path.exists(file_path):
            await rename(file_path, new_file_path)
            LOGGER.info(f"Auto renamed file: '{old_filename}' -> '{new_filename}'")
            return new_file_path
        else:
            return file_path
            
    except Exception as e:
        LOGGER.error(f"Error in auto rename for {old_filename}: {e}")
        return file_path
