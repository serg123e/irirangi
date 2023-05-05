import os
import re
import sys
import logging
import time
import telegram
from telegram.ext import Application, Updater, CommandHandler, MessageHandler, filters
import subprocess

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
            output = subprocess.check_output(cmd).decode(sys.stdout.encoding).strip()
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
    try:
        # Check if the message contains an audio file

        audio = update.message.audio
        voice = update.message.voice
        if audio:
            file_id = audio.file_id
            newFile = await context.bot.getFile(file_id)
            filename = newFile.file_path.split("/")[-1]
            await newFile.download_to_drive(custom_path="/music/" + filename)
            mpc_add_file(filename)
            await context.bot.send_message(chat_id=chat_id, text="Audio file added to playlist!")
        elif voice:
            file_id = voice.file_id
            newFile = await context.bot.getFile(file_id)
            filename = newFile.file_path.split("/")[-1]
            await newFile.download_to_drive(custom_path="/tmp/" + filename)
            cmd = ["ffmpeg", "-i", "/tmp/" + filename, "-af", "highpass=f=50, lowpass=f=4000, equalizer=f=80:t=q:w=1:g=3, equalizer=f=400:t=h:width_type=q:width=2:g=-6, dynaudnorm=f=60:g=15", "/voice/" + filename]
            output = subprocess.check_output(cmd)
            mpc_voice_command("clear")
            mpc_voice_command("--wait",["update"])
            mpc_voice_command("insert", [filename])
            mpc_voice_command("play")
            await context.bot.send_message(chat_id=chat_id, text="Voice file added to voice stream!")
        else:
            # Check if the message contains a link to a SoundCloud or YouTube track
            text = update.message.text
            if text is None:
                # await context.bot.send_message(chat_id=chat_id, text="No text and I don't recognize that type of message.")
                return
            elif "soundcloud.com" in text or "youtu" in text:
                # await context.bot.send_message(chat_id=chat_id, text="try to dl "+text)
                # Download the track using yt-dlp
                # cmd = ["timeout", "300s", "yt-dlp", "--print", "filename", "--no-simulate", "-x", "--audio-quality", "0", "--audio-format", "mp3", "-o", "../music/%(title)s.%(ext)s", text]
                # , "--restrict-filenames",

                match = re.search("(?P<url>https?://[^\s]+)", text)

                if match is not None:
                    # Extract the URL from the match object and store it in a variable
                    url = match.group("url")
                    if "soundcloud" in url:
                      cmd = ["timeout", "300s", "yt-dlp", "--print", "after_move:filepath", "--no-simulate", "--add-metadata", "--extract-audio", url]
                    else:
                      cmd = ["timeout", "300s", "yt-dlp", "--print", "after_move:filepath", "--no-simulate", "--add-metadata", "--extract-audio", "-f", "140", url]
                    output = subprocess.check_output(cmd).decode().strip()
                    path, filename_ext = os.path.split(output)
                    mpc_add_file(filename_ext)
                    await context.bot.send_message(chat_id=chat_id, text="Track "+filename_ext+" downloaded and added to playlist!")

            else:
                # await context.bot.send_message(chat_id=chat_id, text="Sorry, I don't recognize that type of message.")
                return
    except Exception as e:
        logging.error(str(e))
        logging.exception(e)
        await context.bot.send_message(chat_id=chat_id, text="Sorry, an error occurred while processing your message.")


async def add_to_playlist(update, context):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide a filename to add to the playlist.")
        return

    filename = " ".join(args)

    try:
        # Add the provided filename to MPD playlist
        mpc_add_file(filename)

        await context.bot.send_message(chat_id=chat_id, text=f"Added {filename} to the playlist!")
    except Exception as e:
        logging.error(str(e))
        logging.exception(e)
        await context.bot.send_message(chat_id=chat_id, text="Sorry, an error occurred while adding the file to the playlist.")



async def seek(update, context):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide an position seek.")
        return

    await cmd("seek", update, context)


async def delete(update, context):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide an # in playlist to delete.")
        return

    await cmd("del", update, context)


async def move(update, context):
    chat_id = update.effective_chat.id
    args = context.args

    if not args:
        await context.bot.send_message(chat_id=chat_id, text="Please provide pos1 and pos2 in playlist to move.")
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
    await cmd("stop", update, context)

async def play(update, context):
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
    # updater.idle()

if __name__ == '__main__':
    main()
