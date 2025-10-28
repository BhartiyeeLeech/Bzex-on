"""
Auto Leech/Mirror Processor
Handles automatic processing of links and media files without manual commands
"""
import copy
from pyrogram import filters
from pyrogram.types import Message

from bot import LOGGER, user_data
from bot.helper.ext_utils.links_utils import (
    is_url,
    is_magnet,
    is_telegram_link,
)
from bot.helper.telegram_helper.bot_commands import BotCommands


class AutoProcessor:
    """
    Handles automatic processing of messages for auto leech/mirror functionality.
    Detects URLs, magnets, Telegram links, and media files in messages,
    then automatically triggers download/upload operations based on user settings.
    """

    @staticmethod
    async def process_auto_message(client, message: Message):
        """
        Main handler for automatic message processing.
        
        Args:
            client: Pyrogram client instance
            message: Incoming message to process
            
        Flow:
            1. Determine user settings (AUTO_LEECH or AUTO_MIRROR)
            2. Detect processable content (URLs, media files)
            3. Generate appropriate command
            4. Route to corresponding handler (Mirror, YtDlp, etc.)
        """
        user_id = message.from_user.id if message.from_user else None
        if not user_id:
            return

        user_dict = user_data.get(user_id, {})
        
        # Determine mode: Priority order: AUTO_YT_LEECH > AUTO_LEECH > AUTO_MIRROR
        auto_yt_leech = user_dict.get("AUTO_YT_LEECH", False)
        auto_leech = user_dict.get("AUTO_LEECH", False)
        auto_mirror = user_dict.get("AUTO_MIRROR", False)
        
        if not (auto_yt_leech or auto_leech or auto_mirror):
            return
        
        # Store original text for restoration
        original_text = message.text
        original_caption = message.caption
        
        try:
            # Check for media files
            has_media = bool(
                message.document or 
                message.photo or 
                message.video or 
                message.audio or 
                message.voice or 
                message.video_note or 
                message.sticker or 
                message.animation
            )
            
            # Check for URLs in text or caption
            message_text = message.text or message.caption or ""
            words = message_text.split()
            
            links = []
            video_links = []
            regular_links = []
            
            # Video domains for YT-DLP processing
            video_domains = [
                'youtube.', 'youtu.be', 'twitter.', 'x.com', 'instagram.',
                'facebook.', 'vimeo.', 'dailymotion.', 'soundcloud.', 
                'tiktok.', 'twitch.', 'reddit.com'
            ]
            
            for word in words:
                if is_url(word) or is_magnet(word) or is_telegram_link(word):
                    links.append(word)
                    # Categorize links
                    if any(domain in word.lower() for domain in video_domains):
                        video_links.append(word)
                    else:
                        regular_links.append(word)
            
            has_url = len(links) > 0
            has_video_url = len(video_links) > 0
            has_regular_url = len(regular_links) > 0
            
            if not has_media and not has_url:
                return
            
            # Priority handling based on enabled modes:
            # 1. AUTO_YT_LEECH: Only process video URLs (YouTube, etc.)
            # 2. AUTO_LEECH: Process all content (media + all URLs)
            # 3. AUTO_MIRROR: Process all content (media + all URLs)
            
            if auto_yt_leech and has_video_url:
                # AUTO_YT_LEECH mode: Only process video URLs
                LOGGER.info(f"Auto YT Leech triggered for user {user_id}: Video URL detected")
                await AutoProcessor._process_url(client, message, video_links[0], is_leech=True, force_ytdlp=True)
            elif has_media and (auto_leech or auto_mirror):
                # Process media files (AUTO_LEECH or AUTO_MIRROR)
                is_leech = auto_leech  # AUTO_LEECH has priority
                LOGGER.info(f"Auto processing triggered for user {user_id}: Media file detected")
                await AutoProcessor._process_media(client, message, is_leech)
            elif has_url and (auto_leech or auto_mirror):
                # Process URLs (AUTO_LEECH or AUTO_MIRROR)
                is_leech = auto_leech  # AUTO_LEECH has priority
                LOGGER.info(f"Auto processing triggered for user {user_id}: URL detected")
                # Process the first URL found
                await AutoProcessor._process_url(client, message, links[0], is_leech)
                
        except Exception as e:
            LOGGER.error(f"Auto processing failed for user {user_id}: {e}", exc_info=True)
        finally:
            # Restore original message text/caption
            if original_text is not None:
                message.text = original_text
            if original_caption is not None:
                message.caption = original_caption

    @staticmethod
    async def _process_media(client, message: Message, is_leech: bool):
        """
        Process media files (documents, videos, photos, etc.)
        
        Args:
            client: Pyrogram client
            message: Message containing media
            is_leech: Whether to leech (True) or mirror (False)
        """
        from bot.modules.mirror_leech import Mirror
        
        # Generate command text
        command_name = BotCommands.LeechCommand[0] if is_leech else BotCommands.MirrorCommand[0]
        command_text = f"/{command_name}"
        
        # Create a copy of the message with modified text
        command_message = copy.copy(message)
        command_message.text = command_text
        command_message.caption = None
        
        # Set client references
        if not hasattr(command_message, '_client') or command_message._client is None:
            command_message._client = client
        if not hasattr(command_message, 'client') or command_message.client is None:
            command_message.client = client
        
        # Set reply_to_message to the original message (for media processing)
        command_message.reply_to_message = message
        
        # Clear media from command message to avoid duplication
        command_message.document = None
        command_message.photo = None
        command_message.video = None
        command_message.audio = None
        command_message.voice = None
        command_message.video_note = None
        command_message.sticker = None
        command_message.animation = None
        
        # Create Mirror task
        LOGGER.info(f"Creating Mirror task for media file from user {message.from_user.id}")
        mirror_task = Mirror(client, command_message, is_qbit=False, is_leech=is_leech)
        await mirror_task.new_event()

    @staticmethod
    async def _process_url(client, message: Message, url: str, is_leech: bool, force_ytdlp: bool = False):
        """
        Process URLs (regular links, magnets, Telegram links)
        
        Args:
            client: Pyrogram client
            message: Message containing URL
            url: The URL to process
            is_leech: Whether to leech (True) or mirror (False)
            force_ytdlp: Force routing to YtDlp (for AUTO_YT_LEECH mode)
        """
        from bot.modules.mirror_leech import Mirror
        from bot.modules.ytdlp import YtDlp
        
        # Check if it's a video URL (for YtDlp routing)
        video_domains = [
            'youtube.', 'youtu.be', 'twitter.', 'x.com', 'instagram.',
            'facebook.', 'vimeo.', 'dailymotion.', 'soundcloud.', 
            'tiktok.', 'twitch.', 'reddit.com'
        ]
        is_video_url = any(domain in url.lower() for domain in video_domains)
        
        # Force YtDlp if specified (AUTO_YT_LEECH mode)
        if force_ytdlp:
            is_video_url = True
        
        # Generate command text with URL
        if is_video_url:
            command_name = BotCommands.YtdlLeechCommand[0] if is_leech else BotCommands.YtdlCommand[0]
        else:
            command_name = BotCommands.LeechCommand[0] if is_leech else BotCommands.MirrorCommand[0]
        
        command_text = f"/{command_name} {url}"
        
        # Create a copy of the message with modified text
        command_message = copy.copy(message)
        command_message.text = command_text
        command_message.caption = None
        
        # Set client references
        if not hasattr(command_message, '_client') or command_message._client is None:
            command_message._client = client
        if not hasattr(command_message, 'client') or command_message.client is None:
            command_message.client = client
        
        # Clear reply_to_message for URL processing
        command_message.reply_to_message = None
        
        # Clear any media
        command_message.document = None
        command_message.photo = None
        command_message.video = None
        command_message.audio = None
        command_message.voice = None
        command_message.video_note = None
        command_message.sticker = None
        command_message.animation = None
        
        # Route to appropriate handler
        if is_video_url:
            LOGGER.info(f"Creating YtDlp task for video URL from user {message.from_user.id}")
            ytdlp_task = YtDlp(client, command_message, is_leech=is_leech)
            await ytdlp_task.new_event()
        else:
            LOGGER.info(f"Creating Mirror task for URL from user {message.from_user.id}")
            mirror_task = Mirror(client, command_message, is_qbit=False, is_leech=is_leech)
            await mirror_task.new_event()


def auto_message_filter(_, __, message: Message) -> bool:
    """
    Custom filter for automatic message processing.
    
    Returns True if:
        - Message is not a command
        - Sender is not a bot
        - User has AUTO_LEECH or AUTO_MIRROR enabled
        - Message contains processable content (URLs, media)
    
    Args:
        _: Filter object (unused)
        __: Client object (unused)
        message: Incoming message
        
    Returns:
        bool: True if message should be auto-processed
    """
    # Skip commands
    if message.text and message.text.startswith('/'):
        return False
    
    # Skip bot messages
    if message.from_user and message.from_user.is_bot:
        return False
    
    # Check if user exists
    if not message.from_user:
        return False
    
    user_id = message.from_user.id
    user_dict = user_data.get(user_id, {})
    
    # Check if auto processing is enabled (any mode)
    auto_yt_leech = user_dict.get("AUTO_YT_LEECH", False)
    auto_leech = user_dict.get("AUTO_LEECH", False)
    auto_mirror = user_dict.get("AUTO_MIRROR", False)
    
    if not (auto_yt_leech or auto_leech or auto_mirror):
        return False
    
    # Check for processable content
    message_text = message.text or message.caption or ""
    
    # Check for URLs, magnets, or Telegram links
    has_url = any(
        is_url(word) or is_magnet(word) or is_telegram_link(word)
        for word in message_text.split()
    )
    
    # Check for media files
    has_media = bool(
        message.document or 
        message.photo or 
        message.video or 
        message.audio or 
        message.voice or 
        message.video_note or 
        message.sticker or 
        message.animation
    )
    
    # AUTO_YT_LEECH only processes video URLs
    if auto_yt_leech and not (auto_leech or auto_mirror):
        # Check if any URL is a video URL
        if has_url:
            video_domains = [
                'youtube.', 'youtu.be', 'twitter.', 'x.com', 'instagram.',
                'facebook.', 'vimeo.', 'dailymotion.', 'soundcloud.', 
                'tiktok.', 'twitch.', 'reddit.com'
            ]
            for word in message_text.split():
                if is_url(word) and any(domain in word.lower() for domain in video_domains):
                    return True
        return False
    
    # AUTO_LEECH or AUTO_MIRROR: process any content
    return has_url or has_media


# Create filter instance
auto_process_filter = filters.create(auto_message_filter)
