"""
plugins/admin.py — Panel Admin (Owner only)
"""
import asyncio

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import OWNER_ID
from database import q, set_state, get_setting, get_user
from keyboards import send, KB_ADMIN, main_kb, kb_stats_menu, kb_stats_page

# ── Error permanen saat blast (tidak perlu retry) ─────────────────────────────
_BLAST_SKIP = (
    "UserDeactivated", "UserBlocked", "InputUserDeactivated",
    "PeerIdInvalid", "ChatWriteForbidden", "BotBlocked",
    "AccessTokenExpired", "UserIsBot",
)

def _blast_ignorable(e: Exception) -> bool:
    name = type(e).__name__
    msg  = str(e)
    return name in _BLAST_SKIP or any(s in msg for s in _BLAST_SKIP)


async def _safe_copy_msg(client, source_msg, tid: int) -> bool:
    """
    Copy pesan owner ke satu user dengan perlindungan penuh.
    Pakai client.copy_message() langsung — lebih reliable dari source_msg.copy().
    3 percobaan, exponential backoff pada FloodWait, skip permanen pada error user.
    """
    from_chat = source_msg.chat.id
    msg_id    = source_msg.id
    for attempt in range(3):
        try:
            await client.copy_message(
                chat_id      = tid,
                from_chat_id = from_chat,
                message_id   = msg_id,
            )
            return True
        except FloodWait as fw:
            if attempt < 2:
                wait = int(fw.value * (1 + attempt * 0.5)) + 2
                await asyncio.sleep(wait)
            else:
                return False
        except Exception as e:
            if _blast_ignorable(e):
                return False
            if attempt < 2:
                await asyncio.sleep(1 + attempt)
            else:
                return False
    return False


def _owner(msg) -> bool:
    return msg.from_user and msg.from_user.id == OWNER_ID


@Client.on_message(filters.regex(r"^⚙️ Menu Admin$") & filters.private)
async def panel_admin(client: Client, message):
    if not _owner(message): return
    set_state(OWNER_ID, "ADMIN")
    await send(client, OWNER_ID,
        "🛠️ **PANEL KENDALI OWNER**\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"

        "📋 **TOMBOL MENU**\n"
        "├ 📊 Statistik Bot — ranking star/coin/ghost eye/kreator\n"
        "├ 👻 Ghost Eye — kirim token GE ke user\n"
        "├ 💱 Update Kurs — ubah harga Star & Coin\n"
        "├ 📋 Log Channel — set/ganti channel log video\n"
        "├ 🔗 Update QRIS — ganti link/foto QRIS\n"
        "└ 📡 Broadcast Manual — siaran pesan ke semua user\n\n"

        "⌨️ **PERINTAH SLASH**\n"
        "├ `/delID <id>` — hapus data user (jadi member baru)\n"
        "├ `/reset` — reset semua postingan & transaksi\n"
        "│\n"
        "├ `/addstar <id> <jml>` — tambah ⭐ Star ke user\n"
        "├ `/minstar <id> <jml>` — kurangi ⭐ Star dari user\n"
        "├ `/addcoin <id> <jml>` — tambah 🪙 Coin ke user\n"
        "├ `/mincoin <id> <jml>` — kurangi 🪙 Coin dari user\n"
        "├ `/addge <id> <jml>` — tambah 👻 Ghost Eye ke user\n"
        "└ `/minge <id> <jml>` — kurangi 👻 Ghost Eye dari user\n\n"

        "🔘 **TOMBOL INLINE** _(muncul otomatis di konteks)\n"
        "├ ✅/❌ Konfirmasi topup — di foto bukti bayar\n"
        "├ ✅ DONE WD — di pengajuan widraw\n"
        "├ 🚫 Ban / ✅ Unban — di pesan WD & log\n"
        "├ ⏭️ Skip/Aktifkan video — di log channel\n"
        "├ 🚫 Batasi / ✅ Pulihkan posting — di log channel\n"
        "└ 📡 Pilih & konfirmasi broadcast manual\n\n"

        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Pilih menu di bawah:",
        markup=KB_ADMIN, state="ADMIN",
    )


@Client.on_message(filters.regex(r"^🔄 User Mode$") & filters.private)
async def beralih_user(client: Client, message):
    if not _owner(message): return
    set_state(OWNER_ID, "MAIN_MENU")
    await send(client, OWNER_ID,
        "🔄 Beralih ke **User Mode**.",
        markup=main_kb(OWNER_ID), state="MAIN_MENU",
    )


_PER_PAGE = 10

_STAT_META = {
    "star":    ("⭐ Star",     "⭐"),
    "coin":    ("🪙 Koin",     "🪙"),
    "ge":      ("👻 Ghost Eye","👻"),
    "creator": ("🎬 Kreator",  "🎬"),
    "gifter":  ("🎁 Gifter",   "⭐"),
}


def _display_name(first_name, username, uid) -> str:
    """Pilih nama terbaik untuk display."""
    fn = (first_name or "").strip()
    un = (username or "").strip()
    return fn or un or f"User{uid}"


def _get_all_stats(category: str) -> list:
    """Return list of (uid, display_name, primary_value, extra) sorted descending."""
    if category == "star":
        rows = q("SELECT user_id, first_name, username, kuota_star, coin, ghost_eye FROM users ORDER BY kuota_star DESC")
        return [(r[0], _display_name(r[1], r[2], r[0]), r[3], (r[4], r[5])) for r in rows]
    elif category == "coin":
        rows = q("SELECT user_id, first_name, username, kuota_star, coin, ghost_eye FROM users ORDER BY coin DESC")
        return [(r[0], _display_name(r[1], r[2], r[0]), r[4], (r[3], r[5])) for r in rows]
    elif category == "ge":
        rows = q("SELECT user_id, first_name, username, kuota_star, coin, ghost_eye FROM users ORDER BY ghost_eye DESC")
        return [(r[0], _display_name(r[1], r[2], r[0]), r[5], (r[3], r[4])) for r in rows]
    elif category == "gifter":
        rows = q("SELECT user_id, first_name, username, total_stars_used, last_star_used FROM users ORDER BY total_stars_used DESC")
        return [(r[0], _display_name(r[1], r[2], r[0]), r[3] or 0, r[4] or "") for r in rows]
    elif category == "creator":
        all_posts = q("SELECT creator_id FROM postingan")
        counts: dict = {}
        for (cid,) in all_posts:
            counts[cid] = counts.get(cid, 0) + 1
        sorted_list = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        result = []
        for cid, cnt in sorted_list:
            urow = q("SELECT first_name, username FROM users WHERE user_id=?", (cid,))
            name = _display_name(urow[0][0] if urow else "", urow[0][1] if urow else "", cid)
            result.append((cid, name, cnt, None))
        return result
    return []


def _fmt_row(i: int, uid: int, name: str, category: str, primary, extra) -> str:
    link = f"[{name}](tg://user?id={uid})"
    id_part = f"`{uid}`"
    if category == "star":
        coin, ge = extra
        return f"{i}. {link} — {id_part} | ⭐ {primary} | 🪙 {coin} | 👻 {ge}"
    elif category == "coin":
        star, ge = extra
        return f"{i}. {link} — {id_part} | ⭐ {star} | 🪙 {primary} | 👻 {ge}"
    elif category == "ge":
        star, coin = extra
        return f"{i}. {link} — {id_part} | ⭐ {star} | 🪙 {coin} | 👻 {primary}"
    elif category == "creator":
        return f"{i}. {link} — {id_part} | 🎬 {primary} video"
    elif category == "gifter":
        ts = ""
        if extra:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromisoformat(extra)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = " · " + dt.strftime("%d/%m %H:%M")
            except Exception:
                pass
        return f"{i}. {link} — {id_part} | ⭐ {primary} dipakai{ts}"
    return f"{i}. {link} — {id_part} | {primary}"


def build_stats_page_text(category: str, page: int):
    all_rows   = _get_all_stats(category)
    total      = len(all_rows)
    label, _   = _STAT_META.get(category, ("?", "?"))
    start      = page * _PER_PAGE
    end        = start + _PER_PAGE
    page_rows  = all_rows[start:end]
    has_prev   = page > 0
    has_next   = end < total
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)

    text = (
        f"📊 **{label}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Halaman {page + 1}/{total_pages} · {total} pengguna\n\n"
    )
    if not page_rows:
        text += "_Tidak ada data._"
    else:
        text += "\n".join(
            _fmt_row(start + i + 1, uid, name, category, val, extra)
            for i, (uid, name, val, extra) in enumerate(page_rows)
        )

    kb = kb_stats_page(category, page, has_prev, has_next)
    return text, kb


def _stats_summary_text() -> str:
    total_user  = q("SELECT COUNT(*) FROM users")[0][0]
    total_video = q("SELECT COUNT(*) FROM postingan")[0][0]
    total_wd    = q("SELECT COUNT(*) FROM transaksi_wd WHERE status='SUCCESS'")[0][0]
    return (
        f"📊 **STATISTIK BOT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Pengguna : **{total_user}**\n"
        f"🎬 Total Video    : **{total_video}**\n"
        f"✅ WD Berhasil    : **{total_wd}**\n\n"
        f"Pilih kategori untuk melihat ranking:"
    )


@Client.on_message(filters.regex(r"^📊 Statistik Bot$") & filters.private)
async def statistik(client: Client, message):
    if not _owner(message): return
    await client.send_message(OWNER_ID, _stats_summary_text(), reply_markup=kb_stats_menu())


# ── Perintah dompet owner: /addstar /minstar /addcoin /mincoin /addge /minge ──

_WALLET_FIELDS = {
    "addstar": ("kuota_star", "⭐ Star",     True),
    "minstar": ("kuota_star", "⭐ Star",     False),
    "addcoin": ("coin",       "🪙 Koin",     True),
    "mincoin": ("coin",       "🪙 Koin",     False),
    "addge":   ("ghost_eye",  "👻 Ghost Eye",True),
    "minge":   ("ghost_eye",  "👻 Ghost Eye",False),
}


@Client.on_message(
    filters.command(["addstar","minstar","addcoin","mincoin","addge","minge"])
    & filters.private
)
async def cmd_wallet_adjust(client: Client, message):
    if not (message.from_user and message.from_user.id == OWNER_ID):
        return

    cmd = message.command[0].lower()
    parts = message.text.strip().split()
    if len(parts) < 3:
        return await message.reply(
            f"⚠️ Format: `/{cmd} ID JUMLAH`\n_Contoh: `/{cmd} 123456789 50`_"
        )

    try:
        target_id = int(parts[1])
        jumlah    = int(parts[2])
    except ValueError:
        return await message.reply("❌ ID dan JUMLAH harus berupa angka.")

    if jumlah <= 0:
        return await message.reply("❌ Jumlah harus lebih dari 0.")

    existing = q("SELECT user_id FROM users WHERE user_id=?", (target_id,))
    if not existing:
        return await message.reply(
            f"❌ User `{target_id}` tidak ditemukan di database.\n"
            f"_User belum pernah /start bot._"
        )

    field, emoji, is_add = _WALLET_FIELDS[cmd]

    # field berasal dari _WALLET_FIELDS (whitelist internal) — aman dari injeksi
    # tapi kita gunakan mapping SQL eksplisit untuk kejelasan
    _SQL_ADD = {
        "kuota_star": "UPDATE users SET kuota_star = kuota_star + ? WHERE user_id=?",
        "coin":       "UPDATE users SET coin = coin + ? WHERE user_id=?",
        "ghost_eye":  "UPDATE users SET ghost_eye = ghost_eye + ? WHERE user_id=?",
    }
    _SQL_GET = {
        "kuota_star": "SELECT kuota_star FROM users WHERE user_id=?",
        "coin":       "SELECT coin FROM users WHERE user_id=?",
        "ghost_eye":  "SELECT ghost_eye FROM users WHERE user_id=?",
    }
    _SQL_SET = {
        "kuota_star": "UPDATE users SET kuota_star = ? WHERE user_id=?",
        "coin":       "UPDATE users SET coin = ? WHERE user_id=?",
        "ghost_eye":  "UPDATE users SET ghost_eye = ? WHERE user_id=?",
    }
    if is_add:
        q(_SQL_ADD[field], (jumlah, target_id), commit=True)
        action = "ditambah"
    else:
        cur_row = q(_SQL_GET[field], (target_id,))
        cur_val = cur_row[0][0] if cur_row else 0
        new_val = max(0, cur_val - jumlah)
        q(_SQL_SET[field], (new_val, target_id), commit=True)
        action = "dikurangi"

    new_row = q(_SQL_GET[field], (target_id,))
    saldo   = new_row[0][0] if new_row else 0

    await message.reply(
        f"✅ **Berhasil!**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID      : `{target_id}`\n"
        f"{emoji} {action} : **{jumlah}**\n"
        f"💼 Saldo baru   : **{saldo}**"
    )


@Client.on_message(filters.regex(r"^👻 Ghost Eye$") & filters.private)
async def ghost_eye_menu(client: Client, message):
    if not _owner(message): return
    set_state(OWNER_ID, "WAIT_GHOST_ID")
    await send(client, OWNER_ID,
        "👻 **KIRIM GHOST EYE**\n\n"
        "Masukkan **ID Telegram** user tujuan:\n"
        "_(Contoh: `123456789`)_",
        state="WAIT_GHOST_ID",
    )


@Client.on_message(filters.regex(r"^🔗 Update QRIS$") & filters.private)
async def update_qris(client: Client, message):
    if not _owner(message): return
    set_state(OWNER_ID, "WAIT_QRIS_LINK")
    await send(client, OWNER_ID,
        "🔗 **UPDATE LINK QRIS**\n\n"
        "Kirimkan **link Telegraph** berisi gambar QRIS Anda:\n"
        "_(Format: `https://telegra.ph/...`)_",
        state="WAIT_QRIS_LINK",
    )


@Client.on_message(filters.regex(r"^💱 Update Kurs$") & filters.private)
async def update_kurs(client: Client, message):
    if not _owner(message): return
    set_state(OWNER_ID, "WAIT_KURS")
    await send(client, OWNER_ID,
        "💱 **UPDATE KURS SISTEM**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Format: `HargaStar, JumlahStar, HargaCoin, JumlahCoin`\n\n"
        "💡 Contoh: `5000, 20, 10000, 50`\n"
        "_(20 ⭐ = Rp 5.000 | 50 🪙 = Rp 10.000)_",
        state="WAIT_KURS",
    )


@Client.on_message(filters.regex(r"^📋 Log Channel$") & filters.private)
async def log_channel_menu(client: Client, message):
    if not _owner(message): return

    current = get_setting("log_channel_id", "")
    status_txt = (
        f"📡 **Channel aktif:** `{current}`" if current
        else "❌ **Belum ada channel log yang dikonfigurasi.**"
    )

    me = await client.get_me()
    add_url = (
        f"https://t.me/{me.username}"
        f"?startchannel=true"
        f"&admin=post_messages+delete_messages+restrict_members"
    )

    inline = InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Tambahkan Bot ke Channel", url=add_url)
    ]])

    await client.send_message(
        OWNER_ID,
        f"📋 **KONFIGURASI LOG CHANNEL**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_txt}\n\n"
        f"📋 **Cara setup:**\n"
        f"1️⃣ Tekan tombol di bawah\n"
        f"2️⃣ Pilih channel Anda\n"
        f"3️⃣ Jadikan bot sebagai **Admin** dengan izin kirim pesan\n"
        f"4️⃣ Bot otomatis mendeteksi dan menyimpan ID channel\n\n"
        f"📌 **Catatan:**\n"
        f"• Setiap video yang diupload user akan dicatat di channel\n"
        f"• Log menampilkan identitas asli walaupun user kirim anonim\n"
        f"• Tersedia tombol untuk membatasi/memulihkan akses posting user",
        reply_markup=inline,
    )


# ── Siaran Owner (Broadcast Manual) ──────────────────────────────────────────

@Client.on_message(filters.regex(r"^📡 Broadcast Manual$") & filters.private)
async def broadcast_manual(client: Client, message):
    """Owner masuk mode OWNER_BROADCAST — bisa kirim pesan apapun ke semua user."""
    if not _owner(message): return

    n_users = q("SELECT COUNT(*) FROM users WHERE user_id != ?", (OWNER_ID,))[0][0]
    set_state(OWNER_ID, "OWNER_BROADCAST")
    await send(client, OWNER_ID,
        f"📡 **SIARAN OWNER**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Penerima: **{n_users}** pengguna\n\n"
        f"Kirim pesan apa saja sekarang:\n"
        f"✉️ Teks  •  📷 Foto  •  🎬 Video\n"
        f"📄 Dokumen  •  🎵 Audio  •  🎭 Stiker\n\n"
        f"Pesan dikirim persis seperti aslinya, **tanpa template**. \n"
        f"_Tekan_ **🔙 Kembali** _untuk batal._",
        state="OWNER_BROADCAST",
    )


# ── FIX UTAMA: group=0 khusus owner saat OWNER_BROADCAST ─────────────────────
# Menggunakan group=0 (sama dengan handler regex) agar TIDAK terhalangi
# oleh text_handler (group=1), video_handler, photo_handler yang default group=0
# tapi berjalan SETELAH regex handler karena Pyrogram urut registrasi.
# Handler ini HANYA aktif bila owner + state OWNER_BROADCAST.

_BROADCAST_KB_SKIP = {
    "📝 Buat Postingan", "💸 Widraw", "⭐ Star / Kuota", "👤 Profil & Dompet",
    "⚙️ Menu Admin", "🔄 User Mode", "🔙 Kembali", "📊 Statistik Bot",
    "👻 Ghost Eye", "🔗 Update QRIS", "💱 Update Kurs", "📋 Log Channel",
    "📡 Broadcast Manual", "💬 Chat Owner", "❌ Cancel", "🎁 Durasi Gratis",
    "🕵️ Kirim Anonim", "👤 Tampilkan Nama",
}


async def _is_owner_broadcasting(_, __, message) -> bool:
    """Filter kustom: True hanya jika pengirim adalah owner dan state=OWNER_BROADCAST."""
    if not message.from_user or message.from_user.id != OWNER_ID:
        return False
    if message.text and message.text.startswith("/"):
        return False
    if message.text and message.text in _BROADCAST_KB_SKIP:
        return False
    try:
        _, _, state, _, _ = get_user(OWNER_ID)
    except Exception:
        return False
    return state == "OWNER_BROADCAST"


_owner_broadcasting = filters.create(_is_owner_broadcasting)


@Client.on_message(_owner_broadcasting & filters.private, group=0)
async def owner_broadcast_catcher(client: Client, message):
    """
    Tangkap SEMUA jenis pesan owner saat state OWNER_BROADCAST.
    group=0 dengan filter kustom → jalan bersama handler regex lain,
    TIDAK terblokir oleh text_handler(group=1) maupun handler lain.
    Filter kustom memastikan handler ini hanya aktif bila kondisi tepat.
    """
    asyncio.create_task(_exec_owner_blast(client, message))


async def _exec_owner_blast(client, source_msg):
    """
    Background task: copy pesan owner ke semua user.
    • Pakai _safe_copy_msg() — client.copy_message() + FloodWait backoff
    • Jeda 65ms antar pesan (~15 msg/detik, aman di bawah batas Telegram)
    • Update progress setiap 50 user
    • State direset ke ADMIN hanya setelah blast selesai
    • Error dilaporkan ke owner, tidak hilang diam-diam
    """
    # ✅ Reset state DULU agar owner tidak bisa trigger blast ganda
    set_state(OWNER_ID, "ADMIN")

    notif = None
    try:
        semua  = q("SELECT user_id FROM users WHERE user_id != ?", (OWNER_ID,))
        total  = len(semua)
        sukses = 0

        if total == 0:
            await client.send_message(OWNER_ID, "ℹ️ Tidak ada pengguna lain untuk menerima siaran.")
            set_state(OWNER_ID, "ADMIN")
            await send(client, OWNER_ID, "📋 Kembali ke panel admin.", state="ADMIN")
            return

        notif = await client.send_message(
            OWNER_ID,
            f"⏳ **Mengirim siaran ke {total} pengguna...**\n_Harap tunggu._",
        )

        for i, (tid,) in enumerate(semua, 1):
            ok = await _safe_copy_msg(client, source_msg, tid)
            if ok:
                sukses += 1

            # Update progress setiap 50 user
            if i % 50 == 0 or i == total:
                try:
                    pct = int(i / total * 100) if total else 100
                    await notif.edit_text(
                        f"⏳ **Mengirim siaran... {pct}%**\n\n"
                        f"📤 Diproses : {i}/{total}\n"
                        f"✅ Berhasil : {sukses}\n"
                        f"❌ Gagal    : {i - sukses}"
                    )
                except Exception:
                    pass

            await asyncio.sleep(0.065)  # ~15 msg/detik — aman dari rate limit

        try:
            await notif.edit_text(
                f"✅ **Siaran Selesai!**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Terkirim : **{sukses}** pengguna\n"
                f"❌ Gagal   : **{total - sukses}** pengguna\n"
                f"👥 Total   : **{total}** pengguna"
            )
        except Exception:
            pass
        await send(client, OWNER_ID, "✅ Siaran selesai. Kembali ke panel admin.", state="ADMIN")

    except Exception as err:
        err_msg = f"❌ **Blast gagal!**\n`{type(err).__name__}: {err}`"
        try:
            if notif:
                await notif.edit_text(err_msg)
            else:
                await client.send_message(OWNER_ID, err_msg)
        except Exception:
            pass


# ── Hapus ID User dari database (reset jadi user baru) ────────────────────────

@Client.on_message(filters.command("delID") & filters.private)
async def cmd_del_id(client: Client, message):
    """
    /delID <user_id>  — Hapus data user dari database.
    User yang dihapus akan diperlakukan sebagai pengguna baru saat /start.
    Hanya bisa dipakai owner via DM bot.
    """
    if not (message.from_user and message.from_user.id == OWNER_ID):
        return

    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply(
            "⚠️ **Cara pakai:** `/delID <user_id>`\n\n"
            "_Contoh: `/delID 123456789`_"
        )

    try:
        target_id = int(parts[1])
    except ValueError:
        return await message.reply("❌ ID tidak valid. Gunakan angka, contoh: `123456789`")

    if target_id == OWNER_ID:
        return await message.reply("❌ Tidak bisa menghapus akun Owner sendiri.")

    existing = q("SELECT user_id, username FROM users WHERE user_id=?", (target_id,))
    if not existing:
        return await message.reply(
            f"❌ User `{target_id}` tidak ditemukan di database.\n"
            f"_Mungkin belum pernah /start bot._"
        )

    uname = existing[0][1] or "—"

    q("DELETE FROM users           WHERE user_id=?",    (target_id,), commit=True)
    q("DELETE FROM watch_sessions  WHERE user_id=?",    (target_id,), commit=True)
    q("DELETE FROM kartu_blur      WHERE user_id=?",    (target_id,), commit=True)
    q("DELETE FROM invite_links    WHERE owner_id=?",   (target_id,), commit=True)
    q("DELETE FROM transaksi_wd    WHERE user_id=? AND status='PENDING'", (target_id,), commit=True)

    await message.reply(
        f"✅ **User berhasil dihapus dari database.**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID       : `{target_id}`\n"
        f"👤 Username : @{uname}\n\n"
        f"_Saat user ini klik /start, akan diperlakukan sebagai member baru._\n"
        f"_Video yang pernah dikirim tidak dihapus._"
    )
