"""Jalankan sekali: python init_db.py"""
import os
from config import BOT_DATA_DIR
os.makedirs(BOT_DATA_DIR, exist_ok=True)
os.makedirs("downloads", exist_ok=True)
os.makedirs("assets",    exist_ok=True)

from database import init_db
init_db()
print("Sekarang jalankan: python main.py")
