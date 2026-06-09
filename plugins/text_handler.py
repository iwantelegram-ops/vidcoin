"""
plugins/text_handler.py — Handler teks FSM (input data yang diketik user)

group=1 → jalan setelah semua handler regex (group=0).
Teks tombol keyboard dilewati (sudah ditangani handler group=0 masing-masing).
"""
from pyrogram import Client, filters
from config import OWNER_ID
from database import q, get_user, set_state, is_banned

_TOMBOL = {
    "📝 Buat Postingan", "💸 Widraw", "⭐ Star / Kuota", "👤 Profil & Dompet",
    "⚙️ Menu Admin",    "🔄 User Mode",     "🔙 Kembali",
    "📊 Statistik Bot", "👻 Ghost Eye",     "🔗 Update QRIS",
    "💱 Update Kurs",   "🕵️ Kirim Anonim", "👤 Tampilkan Nama",
    "🎁 Durasi Gratis", "📋 Log Channel",   "📡 Broadcast Manual",
    "💬 Chat Owner",    "❌ Cancel",
}


@Client.on_message(filters.text & filters.private, group=1)
async def fsm_input(client: Client, message):
    if not message.from_user:
        return
    if message.text in _TOMBOL:
        return

    uid = message.from_user.id
    if is_banned(uid):
        return

    try:
        _, _, state, coin, _ = get_user(uid)
    except Exception:
        return

    # ── Owner sedang broadcast manual → serahkan ke owner_broadcast_catcher ──
    if uid == OWNER_ID and state == "OWNER_BROADCAST":
        return

    from keyboards import send

    # ── Jumlah coin WD ────────────────────────────────────────────────────────
    if state == "WD_INPUT":
        amt_text = message.text.strip()
        if not amt_text.isdigit():
            return await send(client, uid, "❌ Masukkan **angka** jumlah coin.", state=state)
        n = int(amt_text)
        if n <= 0:
            return await send(client, uid, "❌ Jumlah harus lebih dari 0.", state=state)
        if n > coin:
            return await send(client, uid,
                f"❌ **Saldo tidak cukup.**\nCoin Anda: `{coin}` 🪙\nDiminta: `{n}` 🪙",
                state=state)
        set_state(uid, f"WD_QRIS_{n}")
        await send(client, uid,
            f"✅ **Widraw {n} 🪙**\n\nSekarang kirim **Foto QRIS Anda** agar Owner bisa memproses dana.",
            state=f"WD_QRIS_{n}")

    # ── Update link QRIS (Owner) ───────────────────────────────────────────────
    elif state == "WAIT_QRIS_LINK" and uid == OWNER_ID:
        if not message.text.startswith("http"):
            return await send(client, uid, "❌ Harus berupa URL valid (`http...`).", state=state)
        q("INSERT OR REPLACE INTO settings (key,value) VALUES ('qris_link',?)", (message.text,), commit=True)
        set_state(OWNER_ID, "ADMIN")
        await send(client, OWNER_ID,
            f"✅ **Link QRIS diperbarui!**\n🔗 `{message.text}`", state="ADMIN")

    # ── Update kurs (Owner) ────────────────────────────────────────────────────
    elif state == "WAIT_KURS" and uid == OWNER_ID:
        parts = [p.strip() for p in message.text.split(",")]
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            return await send(client, uid,
                "❌ Format salah.\nGunakan: `HargaStar, JumlahStar, HargaCoin, JumlahCoin`\n"
                "Contoh: `5000, 20, 10000, 50`", state=state)
        hs, js, hc, jc = parts
        for k, v in [("kurs_star_rp", hs), ("kurs_star_jml", js),
                     ("kurs_coin_rp", hc), ("kurs_coin_jml", jc)]:
            q("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, v), commit=True)
        set_state(OWNER_ID, "ADMIN")
        await send(client, OWNER_ID,
            f"✅ **Kurs diperbarui!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⭐ Rp {hs} = {js} Star\n"
            f"🪙 {jc} Coin = Rp {hc}", state="ADMIN")

    # ── Input ID target (Owner kirim ghost eye) ────────────────────────────────
    elif state == "WAIT_GHOST_ID" and uid == OWNER_ID:
        if not message.text.isdigit():
            return await send(client, uid, "❌ ID harus berupa angka.", state=state)
        target = int(message.text)
        row = q("SELECT username FROM users WHERE user_id=?", (target,))
        if not row:
            return await send(client, uid, "❌ User ID tidak terdaftar.", state=state)
        set_state(OWNER_ID, f"WAIT_GHOST_AMOUNT_{target}")
        await send(client, OWNER_ID,
            f"👤 User: @{row[0][0] or '—'} (`{target}`)\n\n"
            f"Masukkan **jumlah Ghost Eye** yang ingin dikirim:",
            state=f"WAIT_GHOST_AMOUNT_{target}")

    # ── Input jumlah ghost eye (Owner kirim ghost eye) ────────────────────────
    elif state.startswith("WAIT_GHOST_AMOUNT_") and uid == OWNER_ID:
        try:
            target = int(state.replace("WAIT_GHOST_AMOUNT_", ""))
        except ValueError:
            set_state(OWNER_ID, "ADMIN")
            return await send(client, OWNER_ID, "❌ Sesi rusak. Kembali ke panel.", state="ADMIN")
        if not message.text.isdigit():
            return await send(client, uid, "❌ Jumlah Ghost Eye harus angka.", state=state)
        n = int(message.text)
        if n <= 0:
            return await send(client, uid, "❌ Jumlah harus lebih dari 0.", state=state)
        q("UPDATE users SET ghost_eye = ghost_eye + ? WHERE user_id=?", (n, target), commit=True)
        set_state(OWNER_ID, "ADMIN")
        await send(client, OWNER_ID,
            f"✅ **👻 {n} Ghost Eye** dikirim ke `{target}`.", state="ADMIN")
        try:
            await client.send_message(
                target,
                f"👻 **Kamu Mendapat Ghost Eye!**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎁 **+{n} Ghost Eye** ditambahkan ke akun Anda.\n\n"
                f"📋 **Apa itu Ghost Eye?**\n"
                f"Ghost Eye digunakan untuk **menambah durasi tonton video secara gratis** "
                f"tanpa perlu menggunakan ⭐ Star.\n\n"
                f"Saat menonton video, gunakan tombol:\n"
                f"**👻 Gunakan Ghost Eye = +X menit**\n\n"
                f"_Cek saldo Ghost Eye di menu 👤 Profil & Dompet._"
            )
        except Exception:
            pass
