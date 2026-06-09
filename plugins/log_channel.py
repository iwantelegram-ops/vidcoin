"""
plugins/log_channel.py — Deteksi bot ditambah/dikeluarkan dari channel sebagai admin.

Jika bot dijadikan admin di channel:
  - Jika belum ada log channel → langsung simpan.
  - Jika sudah ada log channel yang BERBEDA → tanya konfirmasi ke owner
    sebelum menimpa (mencegah penggantian tidak sengaja).

Jika bot dikeluarkan → hapus setting log channel jika itu channel yang aktif.
"""
from pyrogram import Client
from pyrogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatMemberStatus, ChatType

from config import OWNER_ID
from database import q, get_setting


@Client.on_chat_member_updated()
async def track_bot_channel(client: Client, update: ChatMemberUpdated):
    if not update.chat:
        return
    if update.chat.type not in (ChatType.CHANNEL, ChatType.SUPERGROUP):
        return

    try:
        bot_id = client.me.id
    except Exception:
        return

    new = update.new_chat_member
    if not new or new.user.id != bot_id:
        return

    ch_id    = str(update.chat.id)
    ch_title = update.chat.title or ch_id

    # ── Bot dijadikan admin ────────────────────────────────────────────────────
    if new.status == ChatMemberStatus.ADMINISTRATOR:
        current = get_setting("log_channel_id", "")

        if current and current != ch_id:
            # Sudah ada channel lain yang aktif → tanya konfirmasi dulu
            q("INSERT OR REPLACE INTO settings (key, value) VALUES ('log_channel_id_pending', ?)",
              (ch_id,), commit=True)
            try:
                await client.send_message(
                    OWNER_ID,
                    f"⚠️ **Konfirmasi Ganti Log Channel**\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📡 **Channel aktif saat ini:** `{current}`\n"
                    f"📢 **Channel baru:** **{ch_title}** (`{ch_id}`)\n\n"
                    f"Apakah Anda ingin **mengganti** log channel ke channel baru ini?\n\n"
                    f"⚠️ _Jika ya, semua log video baru akan dikirim ke channel baru tersebut._",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Ya, Ganti Sekarang", callback_data=f"lch_confirm_{ch_id}"),
                        InlineKeyboardButton("❌ Tidak, Batalkan",    callback_data="lch_cancel"),
                    ]])
                )
            except Exception:
                pass
        else:
            # Belum ada channel atau channel yang sama → langsung simpan
            q("INSERT OR REPLACE INTO settings (key, value) VALUES ('log_channel_id', ?)",
              (ch_id,), commit=True)
            try:
                await client.send_message(
                    OWNER_ID,
                    f"✅ **Log Channel dikonfigurasi!**\n\n"
                    f"📢 Channel: **{ch_title}**\n"
                    f"🆔 ID: `{ch_id}`\n\n"
                    f"_Semua log video kiriman user akan dikirim ke channel ini._"
                )
            except Exception:
                pass

    # ── Bot dikeluarkan / diturunkan dari admin ────────────────────────────────
    elif new.status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
        ChatMemberStatus.RESTRICTED,
        ChatMemberStatus.MEMBER,
    ):
        current = get_setting("log_channel_id", "")
        if current == ch_id:
            q("DELETE FROM settings WHERE key='log_channel_id'", commit=True)
            try:
                await client.send_message(
                    OWNER_ID,
                    f"⚠️ **Bot dikeluarkan dari Log Channel.**\n\n"
                    f"📢 Channel: **{ch_title}**\n\n"
                    f"_Log channel dihapus. Tambahkan bot sebagai admin di channel "
                    f"mana pun untuk mengatur ulang._"
                )
            except Exception:
                pass
