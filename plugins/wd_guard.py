"""
plugins/wd_guard.py — Blokir semua aksi user yang memiliki WD pending konfirmasi.

Alur:
  Setelah Owner klik DONE (dana sudah dikirim), user mendapat notifikasi
  dengan tombol DONE di sisi user. Selama user belum menekan tombol itu,
  SEMUA interaksi user ke bot akan diblokir dengan pesan peringatan.
  Ini mencegah user mengabaikan konfirmasi dan melindungi integritas koin.

group=-1 → jalan paling awal, sebelum semua handler lain.
"""
from pyrogram import Client, filters
from pyrogram.types import Message

from config import OWNER_ID
from database import has_pending_wd_confirmation, is_banned

_MSG_BLOCKED = (
    "⏳ **Aksi Dibatasi Sementara!**\n\n"
    "Anda memiliki proses **Widraw** yang sedang menunggu konfirmasi Anda.\n\n"
    "📋 **Yang perlu dilakukan:**\n"
    "Cari pesan dari bot yang berisi tombol\n"
    "**✅ Dana Sudah Masuk — DONE**\n"
    "dan tekan tombol tersebut jika dana sudah diterima.\n\n"
    "⚠️ _Semua fitur bot akan aktif kembali setelah Anda menekan DONE._"
)


@Client.on_message(filters.private, group=-1)
async def wd_guard(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id

    if uid == OWNER_ID:
        return

    if is_banned(uid):
        return

    if not has_pending_wd_confirmation(uid):
        return

    await message.reply(_MSG_BLOCKED, quote=True)
    message.stop_propagation()
