import os
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
import bot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_admin_usernames():
    """Clear ADMIN_USERNAMES before each test."""
    bot.ADMIN_USERNAMES.clear()
    yield
    bot.ADMIN_USERNAMES.clear()


def make_update(username=None, chat_id=123, text=None, audio=None, voice=None):
    update = MagicMock()
    update.effective_chat.id = chat_id
    if username is not None:
        update.effective_user.username = username
    else:
        update.effective_user = None
    update.message.text = text
    update.message.audio = audio
    update.message.voice = voice
    return update


def make_context(args=None):
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    ctx.bot.getFile = AsyncMock()
    ctx.args = args or []
    return ctx


# ===========================================================================
# sanitize_filename
# ===========================================================================

class TestSanitizeFilename:
    def test_simple_name(self):
        assert bot.sanitize_filename("song.mp3") == "song.mp3"

    def test_strips_directory_traversal(self):
        assert bot.sanitize_filename("../../etc/passwd") == "passwd"

    def test_strips_absolute_path(self):
        assert bot.sanitize_filename("/etc/shadow") == "shadow"

    def test_replaces_special_chars(self):
        result = bot.sanitize_filename("file;rm -rf.mp3")
        assert ";" not in result
        assert result.endswith(".mp3")

    def test_empty_after_sanitize_gives_uuid(self):
        result = bot.sanitize_filename("...")
        assert len(result) == 36  # UUID format

    def test_preserves_spaces_and_dashes(self):
        assert bot.sanitize_filename("my song - remix.mp3") == "my song - remix.mp3"

    def test_unicode_letters_preserved(self):
        result = bot.sanitize_filename("песня.mp3")
        assert "mp3" in result

    def test_null_bytes_removed(self):
        result = bot.sanitize_filename("file\x00.mp3")
        assert "\x00" not in result


# ===========================================================================
# validate_url
# ===========================================================================

class TestValidateUrl:
    # --- valid ---
    def test_youtube_watch(self):
        assert bot.validate_url("https://www.youtube.com/watch?v=abc123") is True

    def test_youtube_short(self):
        assert bot.validate_url("https://youtu.be/abc123") is True

    def test_youtube_music(self):
        assert bot.validate_url("https://music.youtube.com/watch?v=abc") is True

    def test_youtube_mobile(self):
        assert bot.validate_url("https://m.youtube.com/watch?v=abc") is True

    def test_soundcloud(self):
        assert bot.validate_url("https://soundcloud.com/artist/track") is True

    def test_soundcloud_www(self):
        assert bot.validate_url("https://www.soundcloud.com/artist/track") is True

    def test_soundcloud_mobile(self):
        assert bot.validate_url("https://m.soundcloud.com/artist/track") is True

    def test_http_also_works(self):
        assert bot.validate_url("http://youtube.com/watch?v=abc") is True

    # --- invalid ---
    def test_random_domain(self):
        assert bot.validate_url("https://evil.com/payload") is False

    def test_ftp_scheme(self):
        assert bot.validate_url("ftp://youtube.com/file") is False

    def test_no_scheme(self):
        assert bot.validate_url("youtube.com/watch?v=abc") is False

    def test_subdomain_spoofing(self):
        assert bot.validate_url("https://youtube.com.evil.com/x") is False

    def test_empty_string(self):
        assert bot.validate_url("") is False

    def test_garbage(self):
        assert bot.validate_url("not a url at all") is False


# ===========================================================================
# load_admin_usernames
# ===========================================================================

class TestLoadAdminUsernames:
    def test_loads_comma_separated(self):
        with patch.dict(os.environ, {"ADMIN_USERNAMES": "alice,bob,charlie"}):
            bot.load_admin_usernames()
        assert bot.ADMIN_USERNAMES == {"alice", "bob", "charlie"}

    def test_strips_whitespace(self):
        with patch.dict(os.environ, {"ADMIN_USERNAMES": " alice , bob "}):
            bot.load_admin_usernames()
        assert bot.ADMIN_USERNAMES == {"alice", "bob"}

    def test_strips_at_sign(self):
        with patch.dict(os.environ, {"ADMIN_USERNAMES": "@alice,@bob"}):
            bot.load_admin_usernames()
        assert bot.ADMIN_USERNAMES == {"alice", "bob"}

    def test_lowercases(self):
        with patch.dict(os.environ, {"ADMIN_USERNAMES": "Alice,BOB"}):
            bot.load_admin_usernames()
        assert bot.ADMIN_USERNAMES == {"alice", "bob"}

    def test_empty_string_gives_empty_set(self):
        with patch.dict(os.environ, {"ADMIN_USERNAMES": ""}):
            bot.load_admin_usernames()
        assert bot.ADMIN_USERNAMES == set()

    def test_missing_env_gives_empty_set(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_USERNAMES", None)
            bot.load_admin_usernames()
        assert bot.ADMIN_USERNAMES == set()


# ===========================================================================
# is_admin
# ===========================================================================

class TestIsAdmin:
    def test_empty_whitelist_allows_everyone(self):
        update = make_update(username="anyone")
        assert bot.is_admin(update) is True

    def test_admin_in_whitelist(self):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="alice")
        assert bot.is_admin(update) is True

    def test_admin_case_insensitive(self):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="Alice")
        assert bot.is_admin(update) is True

    def test_non_admin_rejected(self):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        assert bot.is_admin(update) is False

    def test_no_user_rejected(self):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username=None)
        assert bot.is_admin(update) is False


# ===========================================================================
# mpc_port_command
# ===========================================================================

class TestMpcPortCommand:
    @patch("bot.subprocess.check_output")
    def test_builds_correct_command(self, mock_sub):
        mock_sub.return_value = b"OK"
        bot.mpc_port_command("play", "mpd", "6600", ["3"], retries=0)
        mock_sub.assert_called_once_with(["mpc", "-h", "mpd", "-p", "6600", "play", "3"])

    @patch("bot.subprocess.check_output")
    def test_no_args_defaults_to_empty(self, mock_sub):
        mock_sub.return_value = b"OK"
        bot.mpc_port_command("status", "mpd", "6600", retries=0)
        mock_sub.assert_called_once_with(["mpc", "-h", "mpd", "-p", "6600", "status"])

    @patch("bot.time.sleep")
    @patch("bot.subprocess.check_output")
    def test_retries_on_failure(self, mock_sub, mock_sleep):
        from subprocess import CalledProcessError
        mock_sub.side_effect = [CalledProcessError(1, "mpc"), b"OK"]
        result = bot.mpc_port_command("play", "mpd", "6600", retries=1, delay=1)
        assert result == "OK"
        assert mock_sub.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("bot.time.sleep")
    @patch("bot.subprocess.check_output")
    def test_raises_after_exhausted_retries(self, mock_sub, mock_sleep):
        from subprocess import CalledProcessError
        mock_sub.side_effect = CalledProcessError(1, "mpc")
        with pytest.raises(CalledProcessError):
            bot.mpc_port_command("play", "mpd", "6600", retries=2, delay=0)
        assert mock_sub.call_count == 3


# ===========================================================================
# mpc_add_file
# ===========================================================================

class TestMpcAddFile:
    @patch("bot.mpc_command")
    def test_calls_update_add_play(self, mock_mpc):
        bot.mpc_add_file("song.mp3")
        assert mock_mpc.call_args_list == [
            call("--wait", ["update"]),
            call("add", ["song.mp3"]),
            call("play"),
        ]


# ===========================================================================
# Async handler tests
# ===========================================================================

@pytest.mark.asyncio
class TestStartHandler:
    async def test_sends_welcome(self):
        update = make_update(username="user1")
        ctx = make_context()
        await bot.start(update, ctx)
        ctx.bot.send_message.assert_called_once()
        text = ctx.bot.send_message.call_args.kwargs["text"]
        assert "radio streaming bot" in text


@pytest.mark.asyncio
class TestDownloadHandler:
    async def test_non_admin_ignored_silently(self):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob", text="hello")
        ctx = make_context()
        await bot.download(update, ctx)
        ctx.bot.send_message.assert_not_called()

    @patch("bot.mpc_add_file")
    async def test_audio_file_saved_and_added(self, mock_add):
        audio = MagicMock()
        audio.file_id = "file_123"
        update = make_update(username="alice", audio=audio)
        ctx = make_context()
        mock_file = AsyncMock()
        mock_file.file_path = "music/song.mp3"
        ctx.bot.getFile.return_value = mock_file

        await bot.download(update, ctx)

        mock_file.download_to_drive.assert_called_once_with(custom_path="/music/song.mp3")
        mock_add.assert_called_once_with("song.mp3")
        ctx.bot.send_message.assert_called_once()
        assert "Audio file added" in ctx.bot.send_message.call_args.kwargs["text"]

    @patch("bot.os.path.exists", return_value=True)
    @patch("bot.os.remove")
    @patch("bot.subprocess.check_output", return_value=b"")
    @patch("bot.mpc_voice_command")
    async def test_voice_message_processes_and_cleans_tmp(self, mock_voice, mock_sub, mock_rm, mock_exists):
        voice = MagicMock()
        voice.file_id = "voice_456"
        update = make_update(username="alice", voice=voice)
        ctx = make_context()
        mock_file = AsyncMock()
        mock_file.file_path = "voice/msg.oga"
        ctx.bot.getFile.return_value = mock_file

        await bot.download(update, ctx)

        mock_rm.assert_called_once_with("/tmp/msg.oga")
        mock_voice.assert_any_call("play")
        assert "Voice file added" in ctx.bot.send_message.call_args.kwargs["text"]

    @patch("bot.mpc_add_file")
    @patch("bot.subprocess.check_output", return_value=b"/music/track.opus\n")
    async def test_youtube_url_downloaded(self, mock_sub, mock_add):
        update = make_update(username="alice", text="check this https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        update.message.audio = None
        update.message.voice = None
        ctx = make_context()

        await bot.download(update, ctx)

        mock_sub.assert_called_once()
        cmd_args = mock_sub.call_args[0][0]
        assert "yt-dlp" in cmd_args
        assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in cmd_args
        mock_add.assert_called_once_with("track.opus")

    async def test_invalid_url_rejected(self):
        update = make_update(username="alice", text="check https://youtu.be.evil.com/hack")
        update.message.audio = None
        update.message.voice = None
        ctx = make_context()

        await bot.download(update, ctx)

        ctx.bot.send_message.assert_called_once()
        assert "only YouTube and SoundCloud" in ctx.bot.send_message.call_args.kwargs["text"]

    @patch("bot.mpc_add_file")
    @patch("bot.subprocess.check_output", return_value=b"/music/track.mp3\n")
    async def test_soundcloud_url_downloaded(self, mock_sub, mock_add):
        update = make_update(username="alice", text="https://soundcloud.com/artist/song")
        update.message.audio = None
        update.message.voice = None
        ctx = make_context()

        await bot.download(update, ctx)

        cmd_args = mock_sub.call_args[0][0]
        assert "-f" not in cmd_args  # soundcloud doesn't use -f 140


@pytest.mark.asyncio
class TestAdminOnlyCommands:
    """Commands that require admin: add, seek, del, move, stop, play."""

    @patch("bot.mpc_add_file")
    async def test_add_blocked_for_non_admin(self, mock_add):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context(args=["song.mp3"])
        await bot.add_to_playlist(update, ctx)
        mock_add.assert_not_called()
        assert "admins only" in ctx.bot.send_message.call_args.kwargs["text"]

    @patch("bot.mpc_command")
    async def test_seek_blocked_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context(args=["50%"])
        await bot.seek(update, ctx)
        mock_mpc.assert_not_called()

    @patch("bot.mpc_command")
    async def test_delete_blocked_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context(args=["1"])
        await bot.delete(update, ctx)
        mock_mpc.assert_not_called()

    @patch("bot.mpc_command")
    async def test_move_blocked_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context(args=["1", "3"])
        await bot.move(update, ctx)
        mock_mpc.assert_not_called()

    @patch("bot.mpc_command")
    async def test_stop_blocked_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context()
        await bot.stop(update, ctx)
        mock_mpc.assert_not_called()

    @patch("bot.mpc_command")
    async def test_play_blocked_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context()
        await bot.play(update, ctx)
        mock_mpc.assert_not_called()


@pytest.mark.asyncio
class TestPublicCommands:
    """Commands available to everyone: next, status, playlist."""

    @patch("bot.mpc_command", return_value="[playing] song.mp3")
    async def test_next_allowed_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context()
        await bot.playnext(update, ctx)
        mock_mpc.assert_called_once_with("next", [])

    @patch("bot.mpc_command", return_value="volume:100 repeat:on")
    async def test_status_allowed_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context()
        await bot.status(update, ctx)
        mock_mpc.assert_called_once_with("status", [])

    @patch("bot.mpc_command", return_value="1) song.mp3\n2) track.mp3")
    async def test_playlist_allowed_for_non_admin(self, mock_mpc):
        bot.ADMIN_USERNAMES.add("alice")
        update = make_update(username="bob")
        ctx = make_context()
        await bot.playlist(update, ctx)
        mock_mpc.assert_called_once_with("playlist")
        assert "song.mp3" in ctx.bot.send_message.call_args.kwargs["text"]
