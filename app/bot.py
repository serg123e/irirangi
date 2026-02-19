import os
import re
import sys
import logging
import time
import uuid
import shutil
from urllib.parse import urlparse
import telegram
from telegram.ext import Application, Updater, CommandHandler, MessageHandler, filters
import subprocess

MIN_FREE_SPACE_BYTES = 1024 * 1024 * 1024  # 1 GB

SOUNDCLOUD_DOMAINS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
YOUTUBE_DOMAINS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
ALLOWED_URL_DOMAINS = SOUNDCLOUD_DOMAINS | YOUTUBE_DOMAINS

ADMIN_USERNAMES = set()

def load_admin_usernames():
    raw = os.environ.get("ADMIN_USERNAMES", "")
    for name in raw.split(","):
        name = name.strip().lstrip("@")
        if name:
            ADMIN_USERNAMES.add(name.lower())

def is_admin(update):
    if not ADMIN_USERNAMES:
        return True
    user = update.effective_user
    if user and user.username:
        return user.username.lower() in ADMIN_USERNAMES
    return False

def sanitize_filename(filename):
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\s\-.]', '_', filename)
    filename = filename.strip('. ')
    if not filename:
        filename = str(uuid.uuid4())
    return filename

def validate_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.hostname and parsed.hostname.lower() in ALLOWED_URL_DOMAINS:
            return True
    except Exception:
        pass
    return False

def is_soundcloud_url(url):
    try:
        parsed = urlparse(url)
        return bool(parsed.hostname and parsed.hostname.lower() in SOUNDCLOUD_DOMAINS)
    except Exception:
        return False

def check_disk_space(path):
    try:
        return shutil.disk_usage(path).free >= MIN_FREE_SPACE_BYTES
    except OSError:
        return False

def validate_seek_arg(arg):
    return bool(re.match(r'^[+-]?(\d+:)*\d+%?$', arg))

def validate_position_arg(arg):
    return bool(re.match(r'^\d+$', arg))

def mpc_command(command, args=None, retries=0, delay=5):
    return mpc_port_command(command, "mpd", "6600", args, retries, delay)

def mpc_voice_command(command, args=None, retries=0, delay=5):
    return mpc_port_command(command, "mpd-voice", "6700", args, retries, delay)

def mpc_port_command(command, host, port, args=None, retries=3, delay=5):
    """
    Helper method to execute MPC commands and return the output

    :param command: the command to execute
    :param host: the host to connect to
    :param port: the port to use
    :param args: a list of additional arguments (default: None)
    :param retries: the number of times to retry the command in case of failure (default: 3)
    :param delay: the time in seconds to wait between retries (default: 5)
    :return: the output of the command
    """
    if args is None:
        args = []

    cmd = ["mpc", "-h", host, "-p", port, command] + args

    for i in range(retries + 1):
        try:
            output = subprocess.check_output(cmd).decode("utf-8").strip()
            return output
        except subprocess.CalledProcessError as e:
            if i < retries:
                logging.warning(f"Error occurred: {e}, retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                logging.error(f"Error occurred: {e}")
                raise

def init_mpc(host, port, settings, retries=3):
    for command, args in settings.items():
        mpc_port_command(command, host, port, args, retries)

def mpd_init():
    settings = {
        "consume": ["off"],
        "repeat": ["on"],
        "random": ["off"],
        "crossfade": ["3"],
        "mixrampdb": ["-24"],
        "mixrampdelay": ["3"]
    }
    init_mpc("mpd", "6600", settings)

def mpd_voice_init():
    settings = {
        "consume": ["on"],
        "repeat": ["off"],
        "random": ["off"],
        "crossfade": ["2"]
    }
    init_mpc("mpd-voice", "6700", settings)

async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Hi, I'm a radio streaming bot! Send me audio files, soundcloud or youtube links to add to the playlist. You can also use the /next command to skip to the next song in the playlist.")

def mpc_add_file(filename):
    mpc_command("--wait",["update"])
    mpc_command("add", [filename])
    mpc_command("play")

async def download(update, context):
    chat_id = update.effective_chat.id
    if not is_admin(update):
        return
    try:
        audio = update.message.audio
        voice = update.message.voice
        if audio:
            if not check_disk_space("/music"):
                await context.bot.send_message(chat_id=chat_id, text="Low disk space, cannot save file.")
                return
            file_id = audio.file_id
            newFile = await context.bot.getFile(file_id)
            filename = sanitize_filename(newFile.file_path.split("/")[-1])
            await newFile.download_to_drive(custom_path="/music/" + filename)
            mpc_add_file(filename)
            await context.bot.send_message(chat_id=chat_id, text="Audio file added to playlist!")
        elif voice:
            if not check_disk_space("/voice"):
                await context.bot.send_message(chat_id=chat_id, text="Low disk space, cannot save file.")
                return
            file_id = voice.file_id
            newFile = await context.bot.getFile(file_id)
            filename = sanitize_filename(newFile.file_path.split("/")[-1])
            tmp_path = "/tmp/" + filename
            await newFile.download_to_drive(custom_path=tmp_path)
            cmd = ["timeout", "60s", "ffmpeg", "-i", tmp_path, "-af", "highpass=f=50, lowpass=f=4000, equalizer=f=80:t=q:w=1:g=3, equalizer=f=400:t=h:width_type=q:width=2:g=-6, dynaudnorm=f=60:g=15", "/voice/" + filename]
            try:
                output = subprocess.check_output(cmd)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            mpc_voice_command("clear")
            mpc_voice_command("--wait",["update"])
            mpc_voice_command("insert", [filename])
            mpc_voice_command("play")
            await context.bot.send_message(chat_id=chat_id, text="Voice file added to voice stream!")
        else:
            text = update.message.text
            if text is None:
                return
            elif "soundcloud.com" in text or "youtu" in text:
                match = re.search(r"(?P<url>https?://[^\s]+)", text)

                if match is not None:
                    url = match.group("url")
                    if not validate_url(url):
                        await context.bot.send_message(chat_id=chat_id, text="Sorry, only YouTube and SoundCloud links are supported.")
                        return
                    if not check_disk_space("/music"):
                        await context.bot.send_message(chat_id=chat_id, text="Low disk space, cannot download.")
                        return
                    if is_soundcloud_url(url):
                      cmd = ["timeout", "300s", "yt-dlp", "--print", "after_move:filepath", "--no-simulate", "--add-metadata", "--extract-audio", url]
                    else:
                      cmd = ["timeout", "300s", "yt-dlp", "--print", "after_move:filepath", "--no-simulate", "--add-metadata", "--extract-audio", "-f", "140", url]
                    output = subprocess.check_output(cmd).decode().strip()
                    path, filename_ext = os.path.split(output)
                    filename_ext = sanitize_filename(filename_ext)
                    mpc_add_file(filename_ext)
                    await context.bot.send_message(chat_id=chat_id, text="Track "+filename_ext+" downloaded and added to playlist!")

            else:
                return
    except Exception as e:
        logging.error(str(e))
        logging.exception(e)
        await context.bot.send_message(chat_id=chat_id, text="Sorry, an error occurred while processing your message.")


async def admin_only(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, this command is for admins only.")

async def add_to_playlist(update, context):
    chat_id = update.effective_chat.id
    if not is_admin(update):
        await admin_only(update, context)
        return
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide a filename to add to the playlist.")
        return

    filename = " ".join(args)
    if filename != sanitize_filename(filename):
        await context.bot.send_message(chat_id=chat_id, text="Invalid filename.")
        return

    try:
        mpc_add_file(filename)

        await context.bot.send_message(chat_id=chat_id, text=f"Added {filename} to the playlist!")
    except Exception as e:
        logging.error(str(e))
        logging.exception(e)
        await context.bot.send_message(chat_id=chat_id, text="Sorry, an error occurred while adding the file to the playlist.")



async def seek(update, context):
    chat_id = update.effective_chat.id
    if not is_admin(update):
        await admin_only(update, context)
        return
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide a position to seek.")
        return

    if not validate_seek_arg(args[0]):
        await context.bot.send_message(chat_id=chat_id, text="Invalid seek position. Use seconds, MM:SS, or percentage%.")
        return

    await cmd("seek", update, context)


async def delete(update, context):
    chat_id = update.effective_chat.id
    if not is_admin(update):
        await admin_only(update, context)
        return
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide a # in playlist to delete.")
        return

    if not validate_position_arg(args[0]):
        await context.bot.send_message(chat_id=chat_id, text="Invalid position. Use a number.")
        return

    await cmd("del", update, context)


async def move(update, context):
    chat_id = update.effective_chat.id
    if not is_admin(update):
        await admin_only(update, context)
        return
    args = context.args

    if len(args) < 2:
        await context.bot.send_message(chat_id=chat_id, text="Please provide pos1 and pos2 in playlist to move.")
        return

    if not validate_position_arg(args[0]) or not validate_position_arg(args[1]):
        await context.bot.send_message(chat_id=chat_id, text="Invalid positions. Use numbers.")
        return

    await cmd("move", update, context)

async def cmd(command, update, context):
    chat_id = update.effective_chat.id
    args = context.args

    output = mpc_command(command,args)
    await context.bot.send_message(chat_id=chat_id, text=output)

async def playnext(update, context):
    await cmd("next", update, context)

async def stop(update, context):
    if not is_admin(update):
        await admin_only(update, context)
        return
    await cmd("stop", update, context)

async def play(update, context):
    if not is_admin(update):
        await admin_only(update, context)
        return
    await cmd("play", update, context)

async def playlist(update, context):
    chat_id = update.effective_chat.id
    output = mpc_command("playlist")
    await context.bot.send_message(chat_id=chat_id, text=output)

async def lslist(update, context):
    await cmd("list", update, context)

async def status(update, context):
    await cmd("status", update, context)

def main():
    load_admin_usernames()
    application = Application.builder().token(os.environ['TELEGRAM_BOT_TOKEN']).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("next", playnext))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("add", add_to_playlist))
    application.add_handler(CommandHandler("play", play))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("seek", seek))
    application.add_handler(CommandHandler("del", delete))
    application.add_handler(CommandHandler("move", move))
    application.add_handler(CommandHandler("playlist", playlist))
    application.add_handler(CommandHandler("list", lslist))
    application.add_handler(MessageHandler(filters.ALL, download))
    mpd_init()
    mpd_voice_init()
    os.chdir('/music')
    application.run_polling(1.0)

if __name__ == '__main__':
    main()
