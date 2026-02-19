import sys
from unittest.mock import MagicMock

# Mock the telegram package before bot.py imports it,
# so tests can run without a working cryptography backend.
telegram_mock = MagicMock()
sys.modules["telegram"] = telegram_mock
sys.modules["telegram.ext"] = telegram_mock.ext
