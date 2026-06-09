import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_ID    = int(os.getenv("API_ID",    "0"))
API_HASH  =     os.getenv("API_HASH",  "")
BOT_TOKEN =     os.getenv("BOT_TOKEN", "")
OWNER_ID  = int(os.getenv("OWNER_ID",  "0"))

# ── CODE_BOT — penanda unik bot ini ───────────────────────────────────────────
CODE_BOT = os.getenv("CODE_BOT", "default")

# ── MongoDB (opsional — prioritas) ────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "")

# ── SQLITE_PATH — path database lokal yang tidak pernah berubah ───────────────
# Format yang diterima:
#   1. Nama saja (tanpa / dan tanpa .db) → ~/psv_bot_data/{nama}.db
#      Contoh: SQLITE_PATH=jual_video  →  ~/psv_bot_data/jual_video.db
#   2. Path relatif ke home (~)
#      Contoh: SQLITE_PATH=~/psv_data/toko.db
#   3. Path absolut
#      Contoh: SQLITE_PATH=/data/data/com.termux/files/home/psv/data.db
#
# Jika kosong → ~/psv_bot_data/{CODE_BOT}/data.db
def _resolve_sqlite_path(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    # Jika hanya nama (tidak ada / dan tidak diakhiri .db) → masuk ke folder standar
    if "/" not in raw and "\\" not in raw and not raw.endswith(".db"):
        return os.path.expanduser(f"~/psv_bot_data/{raw}.db")
    return os.path.expanduser(raw)

_sqlite_path_env = _resolve_sqlite_path(os.getenv("SQLITE_PATH", ""))

if _sqlite_path_env:
    DB_PATH      = _sqlite_path_env
    BOT_DATA_DIR = os.path.dirname(DB_PATH) or os.path.expanduser("~")
else:
    BOT_DATA_DIR = os.path.expanduser(f"~/psv_bot_data/{CODE_BOT}")
    DB_PATH      = os.path.join(BOT_DATA_DIR, "data.db")

# ── IMPORT_FROM — MIGRASI SATU KALI dari database lama ───────────────────────
# Isi ini dengan path ke file database lama (data.db / bot_data.db dll.)
# yang ingin dipindahkan ke MongoDB atau ke DB_PATH baru.
#
# Cara kerja:
#   • Jika MONGO_URI aktif → data dari IMPORT_FROM dipindahkan ke MongoDB,
#     lalu file IMPORT_FROM dikosongkan.
#   • Jika SQLite saja → data dari IMPORT_FROM disalin ke DB_PATH,
#     lalu file IMPORT_FROM dikosongkan.
#
# Setelah migrasi berhasil, hapus atau kosongkan baris ini di .env.
#
# Format sama dengan SQLITE_PATH — nama saja, ~, atau path absolut.
# Contoh: IMPORT_FROM=~/jual_video/data.db
#         IMPORT_FROM=~/jual_video/bot_data.db
IMPORT_FROM = _resolve_sqlite_path(os.getenv("IMPORT_FROM", ""))

# ── Path session Pyrogram ─────────────────────────────────────────────────────
SESSION_DIR  = BOT_DATA_DIR
SESSION_NAME = os.path.join(SESSION_DIR, "psv_bot")

# ── Path absolut untuk semua file runtime ─────────────────────────────────────
# Semua path di bawah ini berbasis BOT_DATA_DIR sehingga dari direktori
# manapun bot dijalankan, file selalu ditemukan di tempat yang sama.
DOWNLOADS_DIR  = os.path.join(BOT_DATA_DIR, "downloads")
ASSETS_DIR     = os.path.join(BOT_DATA_DIR, "assets")
BLUR_CACHE_DIR = os.path.join(BOT_DATA_DIR, "assets", "blur_cache")
DEFAULT_THUMB  = os.path.join(BOT_DATA_DIR, "assets", "default_thumb.jpg")
QRIS_DEFAULT   = os.path.join(BOT_DATA_DIR, "assets", "qris.jpg")

JATAH_GRATIS         = 60
HARGA_STAR           = 1
DURASI_DITAMBAH      = 600
NILAI_BLUR           = 5

# ── Parameter Ghost Eye ───────────────────────────────────────────────────────
GHOST_EYE_PER_REFERRAL = 10

# ── Jadwal Broadcast Otomatis (WIB) ──────────────────────────────────────────
BROADCAST_SLOT_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)
