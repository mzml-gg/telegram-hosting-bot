"""Runtime configuration loaded from environment variables.

Copy .env.example to .env for local development. Never commit .env.
"""
import os

_file_env = {}
for _path in ("/home/container/.runtime.env", ".runtime.env"):
    if os.path.exists(_path):
        for _line in open(_path, encoding="utf-8"):
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _value = _line.split("=", 1)
                _file_env[_key.strip()] = _value.strip().strip('"').strip("'")


def env(name, default=""):
    return os.getenv(name, _file_env.get(name, default)).strip()


PANEL_URL = env("PANEL_URL", "https://panel.example.com")
APPLICATION_API_KEY = env("APPLICATION_API_KEY")
BOT_TOKEN = env("BOT_TOKEN")
DEVELOPER_ID = int(env("DEVELOPER_ID", "0") or 0)
DEVELOPER_USERNAME = env("DEVELOPER_USERNAME", "Monte_dev_md")

SERVER_DURATION_DAYS = int(env("SERVER_DURATION_DAYS", "7"))
ADMIN_SERVER_DURATION_DAYS = int(env("ADMIN_SERVER_DURATION_DAYS", "30"))
DB_PATH = env("DB_PATH", "hosting_bot.sqlite3")
SUBSCRIPTION_MESSAGE = env(
    "SUBSCRIPTION_MESSAGE",
    "💳 الاشتراك بعد انتهاء التجربة\n\nFor subscription after the trial, contact @Monte_dev_md\n\nأرسل للمطور اسم المستخدم أو رقم السيرفر ليتم التجديد.\n\nاليوزر الصحيح: @Monte_dev_md",
)
BRAND_IMAGE = env("BRAND_IMAGE", "eagle.jpg")
TRIAL_MEMORY_MB = int(env("TRIAL_MEMORY_MB", "1024"))
TRIAL_DISK_MB = int(env("TRIAL_DISK_MB", "2048"))
TRIAL_CPU = int(env("TRIAL_CPU", "100"))
TRIAL_CHECK_INTERVAL_SECONDS = int(env("TRIAL_CHECK_INTERVAL_SECONDS", "300"))

PRICE_CURRENCY = env("PRICE_CURRENCY", "USD")
PRICE_RAM_PER_GB = float(env("PRICE_RAM_PER_GB", "1.0"))
PRICE_DISK_PER_GB = float(env("PRICE_DISK_PER_GB", "0.10"))
PRICE_CPU_PER_100 = float(env("PRICE_CPU_PER_100", "1.0"))
INFINITE_RESOURCE_PRICE = env("INFINITE_RESOURCE_PRICE", "Contact developer")
BRAND_FOOTER = env(
    "BRAND_FOOTER",
    "ARAB CODE HOSTING\nADMIN : MONTE\nWHATSAPP : +249918614328",
)

DOCKER_NODE = env("DOCKER_NODE", "ghcr.io/parkervcp/yolks:nodejs_22")
DOCKER_PYTHON = env("DOCKER_PYTHON", "ghcr.io/parkervcp/yolks:python_3.11")
STARTUP_NODE = env("STARTUP_NODE", "npm start")
STARTUP_PYTHON = env("STARTUP_PYTHON", "python main.py")

if not BOT_TOKEN or not APPLICATION_API_KEY:
    raise RuntimeError("BOT_TOKEN and APPLICATION_API_KEY must be configured")
