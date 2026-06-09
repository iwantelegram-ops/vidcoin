"""
plugins/photo_handler.py — Handler foto & stiker

• Foto di mode KIRIM_VIDEO → tolak, minta video
• Stiker kapan saja → abaikan diam-diam
• Stiker di mode KIRIM_VIDEO → tolak dengan pesan
• Foto bukti topup → teruskan ke Owner
• Foto QRIS WD → teruskan ke Owner
• Foto di luar konteks → informasi
"""
from pyrogram import Client, filters
from config import OWNER_ID
from database import q, get_user, set_state, is_banned
from keyboards import send, kb_topup, kb_wd_owner

_ONLY_VIDEO = (
    "⚠️ **Maaf, hanya konten video yang diizinkan.**\n\n"
    "Kirim file **video** (bukan foto atau stiker).\n"
    "_Caption/keterangan bisa ditambahkan langsung di video._"
)


@Client.on_message((filters.photo | filters.sticker) & filters.private)
async def on_foto_stiker(client: Client, message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if is_banned(uid):
        return

    try:
        kuota, _, state, coin, _ = get_user(uid)
    except Exception:
        return

    # ── Owner sedang broadcast manual → serahkan ke owner_broadcast_catcher ──
    if state == "OWNER_BROADCAST":
        return

    # ── Mode upload video → tolak apapun selain video ─────────────────────────
    if state.startswith("KIRIM_VIDEO_"):
        return await send(client, uid, _ONLY_VIDEO, state=state)

    # ── Stiker di luar mode posting → abaikan ────────────────────────────────
    if message.sticker:
        return

    # ═══ Dari sini: hanya foto ════════════════════════════════════════════════

    # ── Bukti top up Star ─────────────────────────────────────────────────────
    if state == "WAIT_BUKTI":
        await client.send_photo(
            OWNER_ID,
            message.photo.file_id,
            caption=(
                f"🔔 **BUKTI PEMBAYARAN MASUK!**\n\n"
                f"👤 User: {message.from_user.mention}\n"
                f"🆔 ID: `{uid}`\n\n"
                f"_Sesuaikan jumlah Star lalu tekan Konfirmasi._"
            ),
            reply_markup=kb_topup(uid, 10),
            protect_content=True,
        )
        set_state(uid, "MAIN_MENU")
        await send(client, uid,
            "⚡ **Bukti pembayaran diteruskan ke Owner.**\n\n"
            "Tunggu konfirmasi — Star akan ditambahkan setelah Owner memverifikasi.",
            state="MAIN_MENU")

    # ── Foto QRIS widraw ──────────────────────────────────────────────────────
    elif state.startswith("WD_QRIS_"):
        try:
            jumlah = int(state[len("WD_QRIS_"):])
        except (IndexError, ValueError):
            set_state(uid, "MAIN_MENU")
            return await send(client, uid,
                "❌ Sesi widraw rusak. Silakan mulai ulang.", state="MAIN_MENU")

        if jumlah > coin:
            set_state(uid, "MAIN_MENU")
            return await send(client, uid,
                f"❌ **Coin tidak cukup.**\n"
                f"Diminta: {jumlah} 🪙 | Saldo: {coin} 🪙\n"
                f"Widraw dibatalkan.", state="MAIN_MENU")

        # INSERT transaksi_wd — MongoDB membutuhkan id dari counter
        q("INSERT INTO transaksi_wd "
          "(user_id, jumlah_coin, status, done_user, done_owner, msg_id_user, msg_id_owner) "
          "VALUES (?,?,'PENDING',0,0,0,0)",
          (uid, jumlah), commit=True)

        # Ambil tx_id: di Mongo ambil berdasarkan user_id + jumlah + status PENDING terbaru
        tx_rows = q(
            "SELECT id FROM transaksi_wd "
            "WHERE user_id=? AND status='PENDING' AND done_user=0 AND done_owner=0 "
            "ORDER BY id DESC LIMIT 1",
            (uid,)
        )
        if not tx_rows:
            set_state(uid, "MAIN_MENU")
            return await send(client, uid,
                "❌ Gagal membuat transaksi. Coba lagi.", state="MAIN_MENU")
        tx_id = tx_rows[0][0]

        omsg = await client.send_photo(
            OWNER_ID,
            message.photo.file_id,
            caption=(
                f"🚨 **PENGAJUAN WIDRAW!**\n\n"
                f"👤 {message.from_user.mention} (`{uid}`)\n"
                f"🪙 Jumlah: **{jumlah} coin**\n\n"
                f"Selesaikan transfer ke QRIS user, lalu klik ✅ DONE."
            ),
            reply_markup=kb_wd_owner(tx_id, uid, jumlah),
            protect_content=True,
        )
        q("UPDATE transaksi_wd SET msg_id_owner=? WHERE id=?", (omsg.id, tx_id), commit=True)
        set_state(uid, "MAIN_MENU")
        await send(client, uid,
            f"⏳ **Pengajuan Widraw Terkirim!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 Jumlah: **{jumlah} coin**\n"
            f"📋 Status: _Menunggu konfirmasi Owner_\n\n"
            f"Anda akan mendapat notifikasi saat dana sudah dikirim.",
            state="MAIN_MENU")

    # ── Foto di luar konteks ──────────────────────────────────────────────────
    else:
        await send(client, uid,
            "⚠️ Tidak ada proses aktif yang membutuhkan foto.\n\n"
            "_Gunakan **📝 Buat Postingan** untuk upload video._",
            state=state)
