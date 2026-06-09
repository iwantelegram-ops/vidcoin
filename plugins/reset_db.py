"""
plugins/reset_db.py — Perintah /reset khusus Owner via DM bot.

Reset semua data terkait POSTINGAN & aktivitas transaksi.
Data berikut TIDAK disentuh:
  • users          — identitas, dompet (coin/star/ghost_eye), status ban
  • settings       — konfigurasi bot (QRIS, kurs, dll.)

Data yang DIRESET:
  • postingan      — daftar video/foto yang dijual
  • watch_sessions — sesi nonton aktif
  • kartu_blur     — kartu blur yang sudah dikirim
  • log_channel_msgs — log pesan channel
  • invite_links   — link invite
  • transaksi_wd   — riwayat transaksi withdraw
  • counters       — auto-increment ID untuk postingan & transaksi_wd (Mongo)
"""

import asyncio

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNER_ID
from database import q, _USE_MONGO

# ── Tabel yang akan di-reset (urutan aman: hapus dependan dulu) ───────────────
_RESET_TABLES = [
    "watch_sessions",
    "kartu_blur",
    "log_channel_msgs",
    "invite_links",
    "transaksi_wd",
    "postingan",
]

# ── Counter yang di-reset di MongoDB (auto-increment) ─────────────────────────
_RESET_COUNTERS = ["postingan", "transaksi_wd"]


def _do_reset() -> dict:
    """
    Jalankan reset semua tabel di backend aktif.
    Kembalikan dict {tabel: jumlah_baris_dihapus}.
    """
    result = {}

    # ── MongoDB backend ───────────────────────────────────────────────────────
    if _USE_MONGO:
        mdb = None
        try:
            from database import _mdb as _imported_mdb
            mdb = _imported_mdb
        except Exception:
            pass

        if mdb is not None:
            for tbl in _RESET_TABLES:
                try:
                    col = mdb[tbl]
                    n = col.count_documents({})
                    col.delete_many({})
                    result[tbl] = n
                except Exception as e:
                    result[tbl] = f"❌ error: {e}"

            # Reset counter auto-increment (set ke 0) agar ID mulai dari 1 lagi
            try:
                for ctr in _RESET_COUNTERS:
                    mdb["counters"].update_one(
                        {"_id": ctr},
                        {"$set": {"seq": 0}},
                        upsert=True,
                    )
            except Exception as e:
                result["counters"] = f"❌ error: {e}"
        else:
            # Mongo tidak tersedia meski _USE_MONGO=True (down)
            for tbl in _RESET_TABLES:
                result[tbl] = "⚠️ Mongo tidak tersedia"

        # Juga kosongkan SQLite lokal (backup offline) supaya sinkron
        for tbl in _RESET_TABLES:
            try:
                q(f"DELETE FROM {tbl}", commit=True)
            except Exception:
                pass

        return result

    # ── SQLite backend ────────────────────────────────────────────────────────
    for tbl in _RESET_TABLES:
        rows  = q(f"SELECT COUNT(*) FROM {tbl}")
        count = rows[0][0] if rows else 0
        q(f"DELETE FROM {tbl}", commit=True)
        result[tbl] = count
    return result


# ── Tombol konfirmasi ─────────────────────────────────────────────────────────
def _kb_confirm():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ya, Reset Sekarang", callback_data="reset_confirm"),
            InlineKeyboardButton("❌ Batal",              callback_data="reset_cancel"),
        ]
    ])


# ── Handler /reset ────────────────────────────────────────────────────────────
@Client.on_message(filters.command("reset") & filters.private)
async def cmd_reset(client: Client, message):
    """Tampilkan konfirmasi reset ke owner."""
    if not (message.from_user and message.from_user.id == OWNER_ID):
        return  # Diam saja — bukan owner

    backend = "MongoDB + SQLite lokal" if _USE_MONGO else "SQLite lokal"

    text = (
        "⚠️ **KONFIRMASI RESET DATABASE** ⚠️\n\n"
        f"Backend aktif: `{backend}`\n\n"
        "Data yang akan **DIHAPUS PERMANEN**:\n"
        "• `postingan` — semua video/foto yang dijual\n"
        "• `watch_sessions` — sesi nonton aktif\n"
        "• `kartu_blur` — kartu blur yang terkirim\n"
        "• `log_channel_msgs` — log channel\n"
        "• `invite_links` — link invite\n"
        "• `transaksi_wd` — riwayat withdraw\n"
        "• Counter ID postingan & transaksi direset ke 0\n\n"
        "Data yang **AMAN** (tidak disentuh):\n"
        "• `users` — identitas, coin, star, ghost eye, status ban\n"
        "• `settings` — konfigurasi bot\n\n"
        "❗ Tindakan ini **tidak bisa dibatalkan**. Lanjutkan?"
    )
    await message.reply(text, reply_markup=_kb_confirm())


# ── Callback konfirmasi/batal ─────────────────────────────────────────────────
# Dipanggil langsung dari callback_handler.py → _dispatch()
async def cb_reset(client: Client, callback_query):
    """Proses pilihan owner pada konfirmasi reset."""
    uid = callback_query.from_user.id
    if uid != OWNER_ID:
        return await callback_query.answer("❌ Hanya owner yang bisa ini.", show_alert=True)

    action = callback_query.data  # "reset_confirm" atau "reset_cancel"

    if action == "reset_cancel":
        await callback_query.message.edit_text("❌ **Reset dibatalkan.**\n\nTidak ada data yang diubah.")
        return await callback_query.answer("Dibatalkan.")

    # ── Eksekusi reset ────────────────────────────────────────────────────────
    await callback_query.answer("⏳ Memproses reset…")
    await callback_query.message.edit_text("⏳ **Sedang mereset database…**\n\nMohon tunggu sebentar.")

    try:
        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _do_reset)
    except Exception as e:
        await callback_query.message.edit_text(
            f"❌ **Reset gagal!**\n\n`{e}`\n\nDatabase tidak berubah."
        )
        return

    # ── Susun laporan ─────────────────────────────────────────────────────────
    backend_label = "MongoDB + SQLite" if _USE_MONGO else "SQLite"
    lines = [f"✅ **Reset selesai** [{backend_label}]\n"]
    total_rows = 0
    for tbl, val in result.items():
        if isinstance(val, int):
            lines.append(f"• `{tbl}` → {val} baris dihapus")
            total_rows += val
        else:
            lines.append(f"• `{tbl}` → {val}")

    if "counters" not in result and _USE_MONGO:
        lines.append("• `counters` → direset ke 0")

    lines.append(f"\n📊 Total dihapus: **{total_rows} baris**")
    lines.append("🔒 Data user & dompet **tidak tersentuh**.")

    await callback_query.message.edit_text("\n".join(lines))
