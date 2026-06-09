"""
keyboards.py — Semua keyboard & helper kirim pesan.
"""
import asyncio

from pyrogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from config import OWNER_ID, HARGA_STAR, DURASI_DITAMBAH

_P = dict(resize_keyboard=True)
_DEL_DELAY = 1.5
_last_bot_msg: dict[int, int] = {}


# ── Reply keyboards ────────────────────────────────────────────────────────────

KB_USER = ReplyKeyboardMarkup([
    [KeyboardButton("📝 Buat Postingan"), KeyboardButton("💸 Widraw")],
    [KeyboardButton("⭐ Star / Kuota"),   KeyboardButton("👤 Profil & Dompet")],
    [KeyboardButton("🎁 Durasi Gratis"), KeyboardButton("💬 Chat Owner")],
], **_P)

KB_OWNER = ReplyKeyboardMarkup([
    [KeyboardButton("📝 Buat Postingan"), KeyboardButton("💸 Widraw")],
    [KeyboardButton("⚙️ Menu Admin"),     KeyboardButton("👤 Profil & Dompet")],
    [KeyboardButton("🎁 Durasi Gratis")],
], **_P)

KB_ADMIN = ReplyKeyboardMarkup([
    [KeyboardButton("📊 Statistik Bot"),  KeyboardButton("👻 Ghost Eye"),   KeyboardButton("💱 Update Kurs")],
    [KeyboardButton("📋 Log Channel"),    KeyboardButton("🔗 Update QRIS"), KeyboardButton("🔄 User Mode")],
    [KeyboardButton("📡 Broadcast Manual")],
], **_P)

KB_POSTING = ReplyKeyboardMarkup([
    [KeyboardButton("🕵️ Kirim Anonim"), KeyboardButton("👤 Tampilkan Nama")],
    [KeyboardButton("🔙 Kembali")],
], **_P)

KB_BACK = ReplyKeyboardMarkup([
    [KeyboardButton("🔙 Kembali")],
], **_P)

KB_CHAT_CANCEL = ReplyKeyboardMarkup([
    [KeyboardButton("❌ Cancel")],
], **_P)


def main_kb(uid: int) -> ReplyKeyboardMarkup:
    return KB_OWNER if uid == OWNER_ID else KB_USER


def state_kb(uid: int, state: str) -> ReplyKeyboardMarkup:
    if state == "MODE_POSTING":
        return KB_POSTING
    if state == "ADMIN":
        return KB_ADMIN
    if state in ("CHAT_QUEUE",) or state.startswith("CHAT_WITH_"):
        return KB_CHAT_CANCEL
    if state in {"WAIT_BUKTI", "WD_INPUT", "WAIT_KURS",
                 "WAIT_QRIS_LINK", "WAIT_GHOST_ID", "WAIT_AMOUNT",
                 "OWNER_BROADCAST"} \
       or state.startswith(("WD_QRIS_", "KIRIM_VIDEO_", "WAIT_AMOUNT_",
                             "WAIT_GHOST_AMOUNT_")):
        return KB_BACK
    return main_kb(uid)


# ── Inline keyboards ───────────────────────────────────────────────────────────

def kb_watch(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎬 Lihat Video", callback_data=f"watch_{pid}")
    ]])


def kb_add_time(creator_id: int) -> InlineKeyboardMarkup:
    menit = DURASI_DITAMBAH // 60
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"➕ Tambah Durasi  {HARGA_STAR} ⭐ = +{menit} menit",
            callback_data=f"addtime_{creator_id}",
        )
    ]])


def kb_add_time_full(creator_id: int, ghost_eye_count: int = 0) -> InlineKeyboardMarkup:
    menit = DURASI_DITAMBAH // 60
    rows = []
    if ghost_eye_count > 0:
        rows.append([
            InlineKeyboardButton(
                f"👻 Gunakan Ghost Eye = +{menit} menit",
                callback_data=f"addtime_ghost_{creator_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            f"➕ Tambah Durasi  {HARGA_STAR} ⭐ = +{menit} menit",
            callback_data=f"addtime_{creator_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


def kb_topup(uid: int, jumlah: int = 10) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ +10", callback_data=f"tr_add_{uid}"),
            InlineKeyboardButton("➖ -10", callback_data=f"tr_sub_{uid}"),
        ],
        [InlineKeyboardButton(
            f"✅ Konfirmasi Kirim {jumlah} ⭐",
            callback_data=f"tr_confirm_{uid}",
        )],
    ])


def kb_wd_owner(tx_id: int, uid: int, coin: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ DONE — Dana Sudah Dikirim",
            callback_data=f"wd_done_owner_{tx_id}_{uid}_{coin}",
        )],
        [
            InlineKeyboardButton("🚫 BAN",   callback_data=f"ban_{uid}"),
            InlineKeyboardButton("✅ UNBAN", callback_data=f"unban_{uid}"),
        ],
    ])


def kb_wd_user(tx_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Dana Sudah Masuk — DONE", callback_data=f"wd_done_user_{tx_id}")
    ]])


def kb_log(uid: int, pid: int, is_skipped: bool = False) -> InlineKeyboardMarkup:
    skip_label = "⏭️ Skip ✓ (Aktif)" if is_skipped else "⏭️ Skip"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Batasi Konten",   callback_data=f"vban_{uid}"),
            InlineKeyboardButton("✅ Izinkan Konten",  callback_data=f"vunban_{uid}"),
        ],
        [
            InlineKeyboardButton(skip_label, callback_data=f"skip_{uid}_{pid}"),
        ],
    ])


def kb_ban_unban(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🚫 BAN",   callback_data=f"ban_{uid}"),
        InlineKeyboardButton("✅ UNBAN", callback_data=f"unban_{uid}"),
    ]])


def kb_chat_start(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Mulai Chat", callback_data=f"chat_start_{uid}"),
    ]])


def kb_bcast_pick(videos: list) -> InlineKeyboardMarkup:
    rows = []
    for vid_id, durasi, caption in videos:
        dur = f"{durasi // 60}m{durasi % 60}s" if durasi else "—"
        label = (caption[:20] + "…") if caption and len(caption) > 20 else (caption or f"Video #{vid_id}")
        rows.append([InlineKeyboardButton(
            f"🎬 #{vid_id} · {dur} · {label}",
            callback_data=f"bcast_pick_{vid_id}",
        )])
    rows.append([InlineKeyboardButton("❌ Batal", callback_data="bcast_no")])
    return InlineKeyboardMarkup(rows)


def kb_stats_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐ Star",       callback_data="stats_cat_star_0"),
            InlineKeyboardButton("🪙 Koin",       callback_data="stats_cat_coin_0"),
            InlineKeyboardButton("👻 Ghost Eye",  callback_data="stats_cat_ge_0"),
        ],
        [
            InlineKeyboardButton("🎬 Kreator",   callback_data="stats_cat_creator_0"),
            InlineKeyboardButton("🎁 Gifter",    callback_data="stats_cat_gifter_0"),
        ],
    ])


def kb_stats_page(category: str, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"stats_cat_{category}_{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"stats_cat_{category}_{page + 1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔙 Kategori", callback_data="stats_menu")])
    return InlineKeyboardMarkup(rows)


def kb_bcast_confirm(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Ya, Broadcast Sekarang!", callback_data=f"bcast_yes_{pid}")],
        [InlineKeyboardButton("❌ Batal",                  callback_data="bcast_no")],
    ])


# ── Helper kirim pesan dengan auto-delete pesan sebelumnya ────────────────────

async def _delayed_delete(client, uid: int, msg_id: int):
    await asyncio.sleep(_DEL_DELAY)
    try:
        await client.delete_messages(uid, msg_id)
    except Exception:
        pass


async def send(client, uid: int, text: str, markup=None, *, state: str = None):
    if markup is None:
        if state is None:
            from database import get_user
            _, _, state, _, _ = get_user(uid)
        markup = state_kb(uid, state)

    msg = await client.send_message(uid, text, reply_markup=markup)

    old_mid = _last_bot_msg.get(uid)
    if old_mid and old_mid != msg.id:
        asyncio.create_task(_delayed_delete(client, uid, old_mid))

    _last_bot_msg[uid] = msg.id
    return msg


async def send_photo(client, uid: int, photo, caption: str, markup=None, *, state: str = None):
    if markup is None:
        if state is None:
            from database import get_user
            _, _, state, _, _ = get_user(uid)
        markup = state_kb(uid, state)
    return await client.send_photo(uid, photo, caption=caption, reply_markup=markup)
