"""
plugins/start.py — /start dan tombol Kembali
"""
import asyncio
from datetime import datetime, timezone, timedelta

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    ensure_user, set_state, is_banned, q,
    get_invite_link, mark_invite_used, delete_invite,
)
from keyboards import send, main_kb
from config import GHOST_EYE_PER_REFERRAL


@Client.on_message(filters.command("start") & filters.private)
async def cmd_start(client: Client, message):
    if not message.from_user:
        return

    uid   = message.from_user.id
    name  = message.from_user.first_name or "Pengguna"
    uname = message.from_user.username or ""

    # Cek apakah user baru (sebelum ensure_user)
    is_new = not bool(q("SELECT user_id FROM users WHERE user_id=?", (uid,)))

    ensure_user(uid, uname, name)

    if is_banned(uid):
        return await message.reply("🚫 Akun Anda telah dibanned.")

    set_state(uid, "MAIN_MENU")

    # ── Proses kode referral dari deep link (?start=CODE) ─────────────────────
    if len(message.command) > 1:
        code = message.command[1]
        await _proses_invite(client, uid, is_new, code)

    # ── Pesan sambutan ────────────────────────────────────────────────────────
    try:
        me = await client.get_me()
        share_link = (
            f"https://t.me/share/url"
            f"?url=https://t.me/{me.username}"
            f"&text=Yuk%20gabung%20Platform%20Premium%20Sharing%20Video!%20"
            f"Berbagi%20video%2C%20nonton%20konten%20eksklusif%2C%20dan%20hasilkan%20Rupiah%21"
        )
        inline = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Ajak Teman", url=share_link)
        ]])
    except Exception:
        inline = None

    teks = (
        f"👋 **Halo, {name}!**\n\n"
        "Selamat datang di **Platform Premium Sharing Video** 🎬\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 **Cara kerja platform:**\n\n"
        "🎬 **Kreator** — Upload video → broadcast otomatis ke semua pengguna\n"
        "👁 **Penonton** — Nikmati **60 detik GRATIS**, tambah durasi pakai ⭐ Star\n"
        "💰 **Penghasilan** — Setiap Star penonton = 🪙 Koin masuk ke dompet kreator\n"
        "💸 **Cairkan** — Koin bisa dikonversi ke **Rupiah** via Owner\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Makin banyak pengguna = makin besar penghasilan! 🚀\n"
        "_Ajak teman bergabung dan raih cuan bersama._"
    )

    await client.send_message(uid, teks, reply_markup=inline)
    await send(client, uid, "📋 Pilih menu:", markup=main_kb(uid), state="MAIN_MENU")

    # ── Kirim video perkenalan ke user baru (setelah 10 detik) ────────────────
    if is_new:
        from plugins.video_handler import kirim_video_selamat_datang
        asyncio.create_task(kirim_video_selamat_datang(client, uid))


async def _proses_invite(client, uid: int, is_new: bool, code: str):
    """
    Dijalankan ketika /start dilakukan dengan parameter kode undangan.
    Hanya user BARU yang dihitung sebagai undangan berhasil.
    """
    data = get_invite_link(code)
    if not data:
        return

    owner_id, created_str, used = data

    if owner_id == uid:
        return
    if used:
        return

    try:
        created_at = datetime.fromisoformat(created_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
    except Exception:
        delete_invite(code)
        return

    if datetime.now(timezone.utc) - created_at > timedelta(hours=1):
        delete_invite(code)
        try:
            await client.send_message(
                owner_id,
                "⌛ **Link undangan Anda sudah kadaluarsa.**\n\n"
                "_Buka menu_ **🎁 Durasi Gratis** _untuk membuat link baru._"
            )
        except Exception:
            pass
        return

    if not is_new:
        return

    mark_invite_used(code)
    q("UPDATE users SET ghost_eye = ghost_eye + ? WHERE user_id=?",
      (GHOST_EYE_PER_REFERRAL, owner_id), commit=True)

    try:
        await client.send_message(
            owner_id,
            f"🎉 **Teman Anda berhasil bergabung!**\n\n"
            f"Anda mendapat **👻 {GHOST_EYE_PER_REFERRAL} Ghost Eye** sebagai hadiah undangan!\n\n"
            f"_Gunakan Ghost Eye untuk menambah durasi tonton video._"
        )
    except Exception:
        pass


@Client.on_message(filters.regex(r"^🔙 Kembali$") & filters.private)
async def btn_kembali(client: Client, message):
    if not message.from_user:
        return
    uid = message.from_user.id
    set_state(uid, "MAIN_MENU")
    await send(client, uid, "🏠 **Menu Utama**\n_Pilih menu di bawah:_", state="MAIN_MENU")
