# =========================================================
#  BOT SOZLAMALARI
#
#  BOT_TOKEN endi shu faylga yozilmaydi — Railway'dagi "Variables"
#  bo'limidan o'qiladi. Bu tokenni GitHub'ga (hatto private repoga
#  ham) tasodifan yuklab qo'yishning oldini oladi.
#
#  Railway'da: loyihangiz -> Variables -> "New Variable" ->
#    Name:  BOT_TOKEN
#    Value: @BotFather'dan olgan tokeningiz
#  Kompyuterda mahalliy sinash uchun esa muhit o'zgaruvchisini
#  o'zingiz belgilang (masalan PowerShell'da: $env:BOT_TOKEN="...").
# =========================================================

import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN muhit o'zgaruvchisi topilmadi!\n"
        "Railway'da: loyiha -> Variables -> New Variable -> "
        "Name=BOT_TOKEN, Value=@BotFather'dan olgan tokeningiz.\n"
        "Mahalliy sinashda: $env:BOT_TOKEN=\"tokeningiz\" (PowerShell)."
    )

# Xabarlar yuboriladigan guruh IDsi (odatda manfiy son, masalan -1001234567890)
# Guruh IDsini @getidsbot yordamida bilib olishingiz mumkin.
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "-5489791329"))

# Ishchi qo'shish huquqiga ega adminlarning Telegram ID raqamlari.
# Bir nechta bo'lsa vergul yoki bo'sh joy bilan yozing: "123,456"
# O'z IDingizni @userinfobot orqali bilib olasiz.
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "8205744310").replace(",", " ").split()
}

# Ish joyining markaziy nuqtasi (lokatsiyasi) — BOSHLANG'ICH qiymat.
# Botda admin "📍 Ish joyi lokatsiyasi" tugmasi orqali buni istalgan vaqtda
# o'zgartira oladi (ish joyi ichida turib jonli joylashuv yuborsa yetarli).
# O'zgartirilgan qiymat bazada saqlanadi va shu yerdagi sondan ustun turadi.
CENTER_LATITUDE = 40.5161740
CENTER_LONGITUDE = 70.9620972

# Ruxsat etilgan radius (metrlarda) — shu radius ichida bo'lsa "keldi" deb hisoblanadi.
# Bino ichida GPS signali zaif bo'ladi, telefon Wi-Fi/uyali tarmoq bo'yicha
# joylashuvni taxminlaydi — shuning uchun 50 metr juda tor. 100 metr xavfsizroq.
# Buni ham admin botdan turib o'zgartira oladi.
RADIUS_METERS = 50

# GPS xatoligiga beriladigan qo'shimcha yon berish (metrlarda).
# Telegram har bir joylashuv bilan birga "horizontal_accuracy" — ya'ni
# "joylashuv shuncha metr xato bo'lishi mumkin" degan qiymatni yuboradi.
# Bino ichida telefon sun'iy yo'ldoshni ko'rmaydi va joylashuvni Wi-Fi/uyali
# tarmoq bo'yicha taxminlaydi — xatolik 100-300 metrgacha chiqadi. Shu xatolikni
# hisobga olmasak, ish joyida o'tirgan ishchi ham "uzoqdasiz" javobini oladi.
#
# Shuning uchun masofadan telefon aytgan xatolik ayiriladi, lekin ko'pi bilan
# quyidagi qiymatgacha. Chegara kerak: aks holda soxta GPS dasturi "mening
# xatoligim 10 000 metr" deb yozib, uzoqdan ham "keldim" qilishi mumkin.
#
# Qiymatni pasaytirsangiz — qattiqroq nazorat, lekin noto'g'ri rad etish ko'payadi.
# Ko'tarsangiz — aksincha.
GPS_ACCURACY_TOLERANCE_METERS = 300

# ============ JARIMA (kechikish uchun) ============
# DIQQAT: bu yerdagi qiymatlar faqat BOSHLANG'ICH. Admin botda
# "💰 Jarima sozlamalari" tugmasi orqali istalgan vaqtda o'zgartira oladi —
# o'zgartirilgan qiymat bazada saqlanadi va shu yerdagi sondan ustun turadi.
# Ishchi belgilangan vaqtdan shuncha daqiqa OLDIN kelishi kerak.
# Masalan belgilangan vaqt 09:00 bo'lsa, ishchi 08:55 gacha kelishi shart.
EARLY_REQUIRED_MINUTES = 5

# "Erta kelish" oynasida — ya'ni deadline (08:55) bilan belgilangan vaqt (09:00)
# orasida — kechiktirilgan har bir daqiqa uchun jarima (so'mda).
FINE_EARLY_PER_MINUTE = 5000

# Belgilangan vaqtdan (09:00) keyin kechikkan har bir daqiqa uchun jarima (so'mda).
FINE_LATE_PER_MINUTE = 7000

# Belgilangan vaqtdan keyin jarima eng ko'pi shu daqiqagacha hisoblanadi.
# Undan ham ko'proq kech qolsa — hisoblash to'xtaydi va ishchiga ogohlantirish yuboriladi.
LATE_FINE_CAP_MINUTES = 10

# Necha marta "keragidan ortiq kechikish" (ogohlantirish) bo'lsa qattiq chora ko'riladi.
WARNING_STRIKE_LIMIT = 3

# ============ KETISH (jonli joylashuv bilan) ============
# Ishchi "🏠 Ketyapman" ni bosganda jonli joylashuv yuboradi.
# True  — joylashuv ish joyi radiusi ichida bo'lishi shart (kelishdagi kabi):
#         ishchi uydan turib "ketdim" deya olmaydi, ketish vaqti aniq bo'ladi.
# False — istalgan joydan qabul qilinadi; masofa adminga xabarda ko'rsatiladi.
CHECKOUT_REQUIRES_CENTER = True

# Vaqt zonasi
TIMEZONE = "Asia/Tashkent"

# Ma'lumotlar bazasi fayli
# Render'da persistent disk ulanganda Environment'da
# DB_PATH=/data/attendance.db deb belgilash mumkin.
DB_PATH = os.getenv("DB_PATH", "attendance.db")