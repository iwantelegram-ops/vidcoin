"""
main.py — Entry point bot Premium Sharing Video
Jalankan: python main.py
"""
import asyncio
import os
from pyrogram import Client, idle
from config import API_ID, API_HASH, BOT_TOKEN, OWNER_ID, SESSION_NAME, BOT_DATA_DIR, \
    DOWNLOADS_DIR, ASSETS_DIR, BLUR_CACHE_DIR
from database import init_db

# Pastikan semua direktori data & aset selalu ada (path absolut → aman dari manapun dijalankan)
os.makedirs(BOT_DATA_DIR,   exist_ok=True)
os.makedirs(DOWNLOADS_DIR,  exist_ok=True)
os.makedirs(ASSETS_DIR,     exist_ok=True)
os.makedirs(BLUR_CACHE_DIR, exist_ok=True)


async def _restore_wd(app: Client):
    """Restore tombol inline transaksi WD PENDING setelah restart."""
    from database import q
    from keyboards import kb_wd_owner, kb_wd_user, kb_ban_unban

    rows = q(
        "SELECT id, user_id, jumlah_coin, done_user, done_owner, msg_id_user, msg_id_owner "
        "FROM transaksi_wd WHERE status='PENDING'"
    )
    if not rows:
        return
    print(f"🔄 Restore {len(rows)} WD pending...")
    for tx_id, uid, jumlah, done_u, done_o, mid_u, mid_o in rows:
        if mid_u:
            try:
                await app.edit_message_reply_markup(
                    uid, mid_u,
                    reply_markup=None if done_u else kb_wd_user(tx_id)
                )
            except Exception:
                pass
        if mid_o:
            try:
                mk = kb_ban_unban(uid) if done_o else kb_wd_owner(tx_id, uid, jumlah)
                await app.edit_message_reply_markup(OWNER_ID, mid_o, reply_markup=mk)
            except Exception:
                pass


async def main():
    init_db()

    # SESSION_NAME sudah berisi path lengkap ke direktori home Termux,
    # sehingga sesi Pyrogram selalu ditemukan dari mana pun bot dijalankan.
    app = Client(
        SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        plugins={"root": "plugins"},
    )

    await app.start()
    me = await app.get_me()
    print(f"✅ Bot aktif: @{me.username}")
    print(f"📁 Data dir : {BOT_DATA_DIR}")

    # ── Pastikan DEFAULT_THUMB adalah gambar visual (bukan kotak hitam lama) ──
    from config import DEFAULT_THUMB
    from plugins.video_handler import _make_placeholder_thumb
    import os as _os
    _needs_regen = False
    if not _os.path.exists(DEFAULT_THUMB):
        _needs_regen = True
    else:
        # Jika ukuran file sangat kecil (<5KB) kemungkinan itu gambar solid lama
        _needs_regen = _os.path.getsize(DEFAULT_THUMB) < 5_000
    if _needs_regen:
        _make_placeholder_thumb(DEFAULT_THUMB)
        print(f"🖼️  DEFAULT_THUMB diperbarui → {DEFAULT_THUMB}")

    await _restore_wd(app)

    from plugins.video_handler import restore_sessions, restore_blur_timers, broadcast_scheduler
    asyncio.create_task(restore_sessions(app))
    asyncio.create_task(restore_blur_timers(app))
    asyncio.create_task(broadcast_scheduler(app))

    await idle()
    await app.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot dihentikan.")
