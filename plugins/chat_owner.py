"""
plugins/chat_owner.py — Fitur Chat 2 Arah antara User dan Owner

Alur:
  1. User klik "💬 Chat Owner" → masuk antrean (CHAT_QUEUE)
     • User dapat pesan "sedang dalam antrean"
     • Owner dapat notifikasi dengan tombol "✅ Mulai Chat"
  2. Owner klik "✅ Mulai Chat" → sesi aktif (CHAT_WITH_{uid})
     • Kedua pihak dapat notifikasi terhubung
     • Semua pesan diteruskan 2 arah
     • Keyboard kedua pihak = hanya tombol "❌ Cancel"
  3. Salah satu klik "❌ Cancel" → sesi berakhir
     • Kedua pihak kembali ke menu utama
     • Notifikasi antrean di owner dihapus otomatis
"""
from pyrogram import Client, filters
from pyrogram.types import Message

from config import OWNER_ID
from database import get_user, set_state, is_banned
from keyboards import send, KB_CHAT_CANCEL, kb_chat_start

# uid_user → msg_id notifikasi antrean di sisi owner (untuk dihapus saat selesai)
_pending_notif: dict[int, int] = {}


def _partner_of(uid: int) -> int | None:
    """Ambil ID partner dari state CHAT_WITH_{id}."""
    _, _, state, _, _ = get_user(uid)
    if state.startswith("CHAT_WITH_"):
        try:
            return int(state.replace("CHAT_WITH_", "", 1))
        except ValueError:
            pass
    return None


async def _end_chat(client, uid_a: int, uid_b: int, who_ended: int):
    """Akhiri sesi chat untuk kedua pihak, kirim notif, dan hapus pesan antrean."""
    set_state(uid_a, "MAIN_MENU")
    set_state(uid_b, "MAIN_MENU")

    pesan_akhir_a = "❌ **Chat diakhiri.**\n\n_Anda kembali ke menu utama._"
    pesan_akhir_b = "❌ **Chat telah diakhiri oleh pihak lain.**\n\n_Anda kembali ke menu utama._"

    if who_ended == uid_a:
        msg_a, msg_b = pesan_akhir_a, pesan_akhir_b
    else:
        msg_a, msg_b = pesan_akhir_b, pesan_akhir_a

    await send(client, uid_a, msg_a, state="MAIN_MENU")
    try:
        await send(client, uid_b, msg_b, state="MAIN_MENU")
    except Exception:
        pass

    # Tentukan uid user asli (bukan owner) untuk bersihkan notif
    real_uid = uid_a if uid_a != OWNER_ID else uid_b
    notif_id = _pending_notif.pop(real_uid, None)
    if notif_id:
        try:
            await client.delete_messages(OWNER_ID, notif_id)
        except Exception:
            pass


# ── User klik "💬 Chat Owner" ─────────────────────────────────────────────────

@Client.on_message(filters.regex(r"^💬 Chat Owner$") & filters.private)
async def btn_chat_owner(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if uid == OWNER_ID:
        return
    if is_banned(uid):
        return

    _, _, state, _, _ = get_user(uid)

    if state == "CHAT_QUEUE":
        return await message.reply("⏳ Anda sudah dalam antrean. Tunggu respons Owner.")
    if state.startswith("CHAT_WITH_"):
        return await message.reply("💬 Anda sedang dalam sesi chat aktif.")

    # Masukkan user ke antrean
    set_state(uid, "CHAT_QUEUE")

    full_name = (
        (message.from_user.first_name or "")
        + (" " + message.from_user.last_name if message.from_user.last_name else "")
    ).strip() or f"User{uid}"
    username_str = f"@{message.from_user.username}" if message.from_user.username else "—"

    # Beritahu user
    await client.send_message(
        uid,
        "⏳ **Anda sedang dalam antrean untuk terhubung.**\n\n"
        "_Mohon tunggu, Owner akan segera merespons._",
        reply_markup=KB_CHAT_CANCEL,
    )

    # Kirim notifikasi ke owner
    try:
        notif = await client.send_message(
            OWNER_ID,
            f"📩 **Permintaan Chat Masuk**\n\n"
            f"👤 **Nama:** {full_name}\n"
            f"🆔 **ID:** `{uid}`\n"
            f"👤 **Username:** {username_str}\n\n"
            f"_Tekan tombol di bawah untuk memulai percakapan._",
            reply_markup=kb_chat_start(uid),
        )
        _pending_notif[uid] = notif.id
    except Exception:
        pass


# ── User / Owner klik "❌ Cancel" ─────────────────────────────────────────────

@Client.on_message(filters.regex(r"^❌ Cancel$") & filters.private)
async def btn_cancel_chat(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    _, _, state, _, _ = get_user(uid)

    # User membatalkan antrean (belum terhubung)
    if state == "CHAT_QUEUE":
        set_state(uid, "MAIN_MENU")
        await send(client, uid,
            "❌ **Permintaan chat dibatalkan.**\n\n_Anda kembali ke menu utama._",
            state="MAIN_MENU")
        notif_id = _pending_notif.pop(uid, None)
        if notif_id:
            try:
                await client.delete_messages(OWNER_ID, notif_id)
            except Exception:
                pass
        return

    # Salah satu pihak memutus sesi chat aktif
    if state.startswith("CHAT_WITH_"):
        partner_id = _partner_of(uid)
        if partner_id:
            await _end_chat(client, uid, partner_id, who_ended=uid)
        else:
            set_state(uid, "MAIN_MENU")
            await send(client, uid,
                "❌ **Chat diakhiri.**\n\n_Anda kembali ke menu utama._",
                state="MAIN_MENU")
        return

    # Tidak dalam mode chat — abaikan saja
    await send(client, uid, "↩️ Kembali ke menu.", state="MAIN_MENU")


# ── Forward semua pesan saat chat aktif ──────────────────────────────────────

@Client.on_message(filters.private, group=2)
async def forward_chat(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    _, _, state, _, _ = get_user(uid)

    # ── Owner sedang broadcast → jangan ganggu ───────────────────────────────
    if uid == OWNER_ID and state == "OWNER_BROADCAST":
        return

    if not state.startswith("CHAT_WITH_"):
        return

    # Jangan forward tombol Cancel (sudah ditangani handler group=0)
    if message.text == "❌ Cancel":
        return

    try:
        partner_id = int(state.replace("CHAT_WITH_", "", 1))
    except ValueError:
        return

    # Pastikan partner masih dalam sesi yang sama
    _, _, partner_state, _, _ = get_user(partner_id)
    if not partner_state.startswith("CHAT_WITH_"):
        return

    # Teruskan pesan ke partner (copy_message menjaga semua jenis konten)
    try:
        await client.copy_message(
            chat_id=partner_id,
            from_chat_id=uid,
            message_id=message.id,
        )
    except Exception:
        pass
