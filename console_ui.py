"""Polished, dependency-free terminal UI for Telegram BOT Hosting V3."""
from __future__ import annotations
import logging, os, platform, sys
from pathlib import Path

RESET="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
RED="\033[31m"; YELLOW="\033[33m"; WHITE="\033[97m"; GREEN="\033[32m"; CYAN="\033[36m"; BLACK="\033[30m"
DIV="∘₊✧──────🌹──────✧₊∘"


def _color(text, color, bold=False):
    return f"{color}{BOLD if bold else ''}{text}{RESET}"


def _art():
    p=Path(__file__).with_name("ascii_art.txt")
    try: return p.read_text(encoding="utf-8").rstrip()
    except OSError: return "[ASCII artwork unavailable]"


def _mask_status(value):
    return _color("configured", GREEN, True) if value else _color("MISSING", RED, True)


def print_banner(config):
    print("\n" + _color(_art(), YELLOW))
    print(_color("Telegram BOT Hosting", RED, True))
    print(_color("Python Issue", WHITE, True) + "  " + _color(platform.python_version(), WHITE))
    print(_color("🇸🇩  SUDAN • TELEGRAM BOT HOSTING", RED, True))
    print(_color("━"*58, YELLOW))
    print(_color("V3 BOOT CHECK", CYAN, True))
    print(f"  {_color('Panel URL', WHITE, True):s} : {config.PANEL_URL}")
    print(f"  {_color('BOT_TOKEN', WHITE, True):s} : {_mask_status(config.BOT_TOKEN)}")
    print(f"  {_color('APPLICATION_API_KEY', WHITE, True):s} : {_mask_status(config.APPLICATION_API_KEY)}")
    print(f"  {_color('DEVELOPER_ID', WHITE, True):s} : {_mask_status(config.DEVELOPER_ID)}")
    print(f"  {_color('Database', WHITE, True):s} : {config.DB_PATH}")
    print(_color("━"*58, YELLOW))
    print(_color("✓ Console ready — Telegram BOT Hosting is starting", GREEN, True))
    print()


class V3Formatter(logging.Formatter):
    def format(self, record):
        msg=record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        if record.levelno >= logging.ERROR:
            return (f"{YELLOW}{BOLD}{DIV}{RESET}\n"
                    f"{YELLOW}{BOLD}┊ ERROR{RESET}\n"
                    f"{RED}{BOLD}∘₊✧──────🌹──────✧₊∘{RESET}\n"
                    f"{RED}{BOLD}> TELEGRAM BOT HOSTING{RESET}\n"
                    f"{YELLOW}{BOLD}┊ {msg}{RESET}\n"
                    f"{YELLOW}{BOLD}{DIV}{RESET}")
        if record.levelno >= logging.WARNING:
            return f"{YELLOW}{BOLD}⚠ WARNING{RESET} {msg}"
        if record.levelno >= logging.INFO:
            return f"{GREEN}✓{RESET} {DIM}{msg}{RESET}"
        return f"{DIM}· {msg}{RESET}"


def configure_logging():
    root=logging.getLogger()
    root.handlers.clear()
    handler=logging.StreamHandler()
    handler.setFormatter(V3Formatter())
    root.addHandler(handler); root.setLevel(logging.INFO)
    for name in ("httpx", "httpcore", "telegram.ext", "telegram" ):
        logging.getLogger(name).setLevel(logging.WARNING)


def error(title, exc):
    logging.getLogger("telegram_hosting").error("%s: %s", title, exc)
