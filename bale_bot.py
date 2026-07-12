#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bale bot for browsing Enamad domain database.

Setup:
  1. Create a bot via @botfather on Bale and copy the token
  2. Add [bale] section to config.ini
  3. python bale_bot.py

Usage:
  python bale_bot.py
  python bale_bot.py --config path/to/config.ini
"""

from telegram_bot import run_bot

if __name__ == "__main__":
    raise SystemExit(run_bot(platform="bale"))
