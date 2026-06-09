"""
plugins/user_menu.py — Menu Profil, Star/Topup, Widraw, Buat Postingan,
                        Kirim Anonim, Tampilkan Nama, Durasi Gratis
"""
import os
import secrets
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    get_user, get_ghost_eye, set_state, get_setting, is_banned,
    q, create_invite_link,
)
from keyboards import send, send_photo
from config import GHOST_EYE_PER_REFERRAL, QRIS_DEFAULT, ASSETS_DIR


def _kurs():
    return {
        "star_rp":  int(get_setting("kurs_star_rp",  "5000")),
        "star_jml": int(get_setting("kurs_star_jml", "20")),
        "coin_rp":  int(get_setting("kurs_coin_rp",  "10000")),
        "coin_jml": int(get_setting("kurs_coin_jml", "50")),
    }


# ── Profil & Dompet ────────────────────────────────────────────────────────────

@Client.on_message(filters.regex(r"^👤 Profil & Dompet$") & filters.private)
async def menu_profil(client: Client, message):
    if not message.from_user: return
    uid = message.from_user.id
    if is_banned(uid): return

    kuota, _, _, coin, _ = get_user(uid)
    ghost = get_ghost_eye(uid)
    k = _kurs()
    est = (coin // k["coin_jml"]) * k["coin_rp"] if k["coin_jml"] else 0

    teks = (
        f"👤 **PROFIL AKUN**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: `{uid}`\n\n"
        f"⭐ **Star:** `{kuota}`\n"
        f"_Digunakan untuk menambah waktu tonton._\n\n"
        f"🪙 **Coin:** `{coin}`\n"
        f"_Estimasi nilai: ~Rp {est:,}_\n"
        f"_Diperoleh setiap penonton menambah durasi video Anda._\n\n"
    )

    if ghost > 0:
        teks += f"👻 `{ghost}`\n\n"

    teks += (
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Gunakan **💸 Widraw** untuk mencairkan koin."
    )

    await send(client, uid, teks, state="MAIN_MENU")


# ── Star / Kuota (Top Up) ──────────────────────────────────────────────────────

@Client.on_message(filters.regex(r"^⭐ Star / Kuota$") & filters.private)
async def menu_star(client: Client, message):
    if not message.from_user: return
    uid = message.from_user.id
    if is_banned(uid): return

    kuota, _, _, _, _ = get_user(uid)
    k    = _kurs()
    qris = get_setting("qris_link", QRIS_DEFAULT)
    set_state(uid, "WAIT_BUKTI")

    caption = (
        f"💳 **TOP UP STAR**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⭐ Star Anda: `{kuota}`\n"
        f"💵 Harga: **Rp {k['star_rp']:,} = {k['star_jml']} ⭐**\n\n"
        f"📋 **Cara Top Up:**\n"
        f"1️⃣ Bayar ke QRIS Owner di bawah\n"
        f"2️⃣ Kirim **foto bukti transfer** ke bot ini\n"
        f"3️⃣ Owner akan memvalidasi & menambahkan Star\n\n"
        f"⚠️ _Pastikan nominal sesuai agar proses cepat._"
    )

    if qris.startswith("http"):
        await send(client, uid, f"{caption}\n\n🔗 **QRIS:** {qris}", state="WAIT_BUKTI")
    elif os.path.exists(qris):
        await send_photo(client, uid, qris, caption, state="WAIT_BUKTI")
    else:
        await send(client, uid, f"⚠️ _QRIS belum dikonfigurasi._\n\n{caption}", state="WAIT_BUKTI")


# ── Widraw ─────────────────────────────────────────────────────────────────────

@Client.on_message(filters.regex(r"^💸 Widraw$") & filters.private)
async def menu_widraw(client: Client, message):
    if not message.from_user: return
    uid = message.from_user.id
    if is_banned(uid): return

    _, _, _, coin, _ = get_user(uid)
    k   = _kurs()
    est = (coin // k["coin_jml"]) * k["coin_rp"] if k["coin_jml"] else 0
    set_state(uid, "WD_INPUT")

    await send(client, uid,
        f"💸 **WIDRAW COIN**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Coin Anda: **{coin}**\n"
        f"💱 Kurs: **{k['coin_jml']} Coin = Rp {k['coin_rp']:,}**\n"
        f"💰 Estimasi pencairan: **~Rp {est:,}**\n\n"
        f"Masukkan **jumlah coin** yang ingin dicairkan:\n"
        f"_(Contoh: `{k['coin_jml']}`)_",
        state="WD_INPUT",
    )


# ── Buat Postingan ─────────────────────────────────────────────────────────────

@Client.on_message(filters.regex(r"^📝 Buat Postingan$") & filters.private)
async def menu_posting(client: Client, message):
    if not message.from_user: return
    uid = message.from_user.id
    if is_banned(uid): return

    set_state(uid, "MODE_POSTING")
    await send(client, uid,
        "📝 **BUAT POSTINGAN**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Pilih mode identitas Anda:\n\n"
        "🕵️ **Anonim** — Nama pengirim disembunyikan\n"
        "👤 **Tampilkan Nama** — Nama Telegram Anda tampil di broadcast",
        state="MODE_POSTING",
    )


# ── Pilih mode kirim ───────────────────────────────────────────────────────────

@Client.on_message(filters.regex(r"^🕵️ Kirim Anonim$") & filters.private)
async def btn_anonim(client: Client, message):
    if not message.from_user: return
    uid = message.from_user.id
    if is_banned(uid): return
    _, _, state, _, _ = get_user(uid)
    if state != "MODE_POSTING": return

    set_state(uid, "KIRIM_VIDEO_ANONIM")
    await send(client, uid,
        "🕵️ **Mode: Anonim**\n\n"
        "Identitas Anda tersembunyi di broadcast.\n\n"
        "Kirim **video** sekarang.\n"
        "💡 _Caption di video = keterangan di broadcast._",
        state="KIRIM_VIDEO_ANONIM",
    )


@Client.on_message(filters.regex(r"^👤 Tampilkan Nama$") & filters.private)
async def btn_tampil_nama(client: Client, message):
    if not message.from_user: return
    uid   = message.from_user.id
    if is_banned(uid): return
    _, _, state, _, _ = get_user(uid)
    if state != "MODE_POSTING": return

    nama = message.from_user.first_name or f"User{uid}"
    # State menggunakan flag KIRIM_VIDEO_NAMA (bukan embed nama langsung)
    # agar karakter spesial di nama tidak merusak parsing state.
    # Nama asli diambil dari message.from_user saat video dikirim.
    set_state(uid, "KIRIM_VIDEO_NAMA")
    await send(client, uid,
        f"👤 **Mode: {nama}**\n\n"
        f"Nama Anda akan tampil di broadcast sebagai kreator.\n\n"
        f"Kirim **video** sekarang.\n"
        f"💡 _Caption di video = keterangan di broadcast._",
        state="KIRIM_VIDEO_NAMA",
    )


# ── Durasi Gratis (deep-link referral) ────────────────────────────────────────

@Client.on_message(filters.regex(r"^🎁 Durasi Gratis$") & filters.private)
async def menu_durasi_gratis(client: Client, message):
    if not message.from_user: return
    uid = message.from_user.id
    if is_banned(uid): return

    code = secrets.token_urlsafe(10)
    now_str = datetime.now(timezone.utc).isoformat()
    create_invite_link(uid, code, now_str)

    me = await client.get_me()
    bot_link = f"https://t.me/{me.username}?start={code}"
    share_url = (
        "https://t.me/share/url"
        f"?url={bot_link}"
        "&text=Yuk%20gabung%20dan%20tonton%20video%20eksklusif!%20"
        "Klik%20link%20ini%20untuk%20join%20sekarang."
    )

    inline = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Bagikan Link Undangan", url=share_url)
    ]])

    await client.send_message(
        uid,
        f"🎁 **DAPATKAN DURASI GRATIS**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Undang teman baru bergabung dan dapatkan **👻 {GHOST_EYE_PER_REFERRAL} Ghost Eye** gratis!\n\n"
        f"📋 **Cara:**\n"
        f"1️⃣ Tekan tombol **📤 Bagikan Link Undangan** di bawah\n"
        f"2️⃣ Pilih teman yang ingin Anda ajak\n"
        f"3️⃣ Teman klik link tersebut dan tekan **Start**\n"
        f"4️⃣ Anda otomatis dapat **👻 {GHOST_EYE_PER_REFERRAL} Ghost Eye**!\n\n"
        f"⚠️ **Link berlaku 1 jam** sejak dibuat.\n"
        f"_Setiap klik tombol ini menghasilkan link baru yang unik._\n\n"
        f"🔗 Link Anda:\n`{bot_link}`",
        reply_markup=inline,
    )
