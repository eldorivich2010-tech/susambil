# Ishchilar davomat boti

Ishchilarning ish joyiga qancha vaqt kech kelganini lokatsiya orqali tekshiradigan Telegram bot.

## Qanday ishlaydi

1. Ishchi botga `/start` yozadi. Agar u ro'yxatda bo'lsa, "✅ Keldim" tugmasi chiqadi. Ro'yxatda bo'lmasa, bot unga o'z Telegram ID raqamini ko'rsatadi — u shu raqamni adminga yuboradi va admin uni ro'yxatga qo'shadi.
2. Ishchi ish joyiga kelib, "✅ Keldim" tugmasini bosadi.
3. Bot lokatsiya so'raydi.
4. Ishchi lokatsiyasini yuboradi:
   - Agar lokatsiya ish joyidan `RADIUS_METERS` dan uzoqroq bo'lsa → bot "Siz hali ish joyiga yetib kelmagansiz" deb javob beradi, guruhga hech narsa yubormaydi. Masofa hisoblanganda telefonning GPS xatoligi hisobga olinadi (pastdagi "Joylashuv aniqligi" bo'limiga qarang).
   - Agar lokatsiya mos tushsa → guruhga ism-familiya va real vaqt bilan xabar yuboriladi. Agar belgilangan vaqtdan kech bo'lsa, necha daqiqa/soat kechikkani ham yoziladi.
5. Har bir ishchi kuniga faqat bitta marta "keldim" deb belgilashi mumkin.
6. Ishchi istalgan vaqtda "📊 Statistikam" tugmasini bosib, **Bugun / Bu hafta / Bu oy** bo'yicha nechta kun kelgani, nechta marta va jami necha daqiqa/soat kech qolganini ko'rishi mumkin (masalan: "6 marta, jami 30 daqiqa kech qoldingiz").
7. "🏅 Bonus va jazolarim" tugmasi orqali ishchi o'ziga nechta bonus va nechta jazo berilganini, har birining sababi va sanasi bilan ko'radi.

## Ketish — jonli joylashuv bilan

Kelishdagi kabi, ketayotganda ham lokatsiya yuboriladi:

1. Ishchi ish joyidan chiqayotganda **"🏠 Ketyapman"** tugmasini bosadi.
2. Bot **jonli joylashuv** so'raydi (qadam-baqadam yo'riqnoma bilan). Xaritadan belgilangan oddiy nuqta va forward qilingan joylashuv qabul qilinmaydi.
3. Joylashuv kelgan paytda ketish vaqti yoziladi. Guruhga "Ishdan ketdi" xabari boradi; **adminga xarita nuqtasi va ish joyidan masofa** yuboriladi. Ketish koordinatasi bazada (`left_lat`, `left_lon`) saqlanadi.
4. Shu bilan admin ishchilar aynan qachon ketganini biladi. Belgilangan ketish vaqtidan oldin ketgan bo'lsa, xabarda necha daqiqa oldin ketgani ham yoziladi.

Joylashuv ish joyi radiusi ichida bo'lishi shart — shunda ishchi uydan turib "ketdim" deya olmaydi. Buni o'chirish uchun `config.py` da `CHECKOUT_REQUIRES_CENTER = False` qiling (u holda istalgan joydan qabul qilinadi, masofa adminga ko'rsatiladi).

## Haftalik jadval

Har bir ishchiga hafta kunlari bo'yicha alohida kelish va ketish vaqti belgilanishi mumkin — masalan dushanba/chorshanba/juma soat 12:00, seshanba/payshanba/shanba soat 13:00, yakshanba esa dam olish kuni.

Kechikish o'sha kunga belgilangan vaqt bo'yicha hisoblanadi. Kun uchun alohida vaqt belgilanmagan bo'lsa, ishchining standart vaqti ishlatiladi. Dam olish kuni deb belgilangan kunlarda kechikish umuman hisoblanmaydi.

## Jarima (kechikish uchun)

Ishchi belgilangan vaqtdan **5 daqiqa oldin** kelishi kerak. Masalan belgilangan vaqt 09:00 bo'lsa, ishchi 08:55 gacha kelishi shart.

- **08:55 gacha** kelsa — jarima yo'q.
- **08:55 dan 09:00 gacha** kechikkan har bir daqiqa — **5 000 so'm** (masalan 08:58 da kelsa, 3 daqiqa × 5 000 = 15 000 so'm).
- **09:00 dan keyin** kechikkan har bir daqiqa — **7 000 so'm** (bunda avvalgi 5 daqiqalik oyna to'liq, ya'ni 25 000 so'm, ustiga qo'shiladi).

Har bir "keldim"da jarima avtomatik hisoblanadi, guruhga va ishchiga xabar sifatida boradi hamda PDF hisobotga tushadi. Dam olish kunlarida jarima hisoblanmaydi.

### Ortiqcha kechikish va ogohlantirish

Belgilangan vaqtdan **10 daqiqadan ortiq** kech qolsa, jarima hisoblash 10 daqiqada **to'xtaydi** (ya'ni kech qism eng ko'pi 10 × 7 000 = 70 000 so'm) va ishchiga **ogohlantirish** yuboriladi:

> ⚠️ Siz keragidan ortiq kech qoldingiz. Yana 2 marta shunday holat takrorlansa qattiq chora ko'riladi.

Har bir bunday ogohlantirish yozib boriladi. Uchinchi martadan keyin xabar "qattiq chora ko'riladi" deb o'zgaradi. Ogohlantirishlar admin PDF hisobotida (ham umumiy yakunda, ham alohida ro'yxatda) bonus va jazolar bilan birga ko'rinadi. Ishchi ham "🏅 Bonus va jazolarim" bo'limida o'z ogohlantirishlarini ko'radi.

### Jarima summalarini admin botdan o'zgartiradi

Admin **"💰 Jarima sozlamalari"** tugmasini bosadi va istalgan vaqtda o'zgartiradi:

- ⏰ Oldindan kelish vaqti (standart 5 daqiqa)
- 🟡 Erta oyna jarimasi, har daqiqa (standart 5 000 so'm)
- 🔴 Kechikish jarimasi, har daqiqa (standart 7 000 so'm)
- 🛑 Jarima hisoblash chegarasi (standart 10 daqiqa)
- 🔔 Ogohlantirish limiti (standart 3 marta)

Panelda joriy qiymatlar va jonli misol ("09:03 da kelsa jarima: …") ko'rinadi. Raqamni `9000`, `9 000` yoki `9,000` ko'rinishida yozish mumkin. **O'zgarish faqat keyingi "Keldim"larga ta'sir qiladi** — eski davomat yozuvlarida jarima o'sha paytdagi summa bilan qoladi. PDF hisobot sarlavhasida joriy tarif chiqadi.

`config.py` dagi `EARLY_REQUIRED_MINUTES`, `FINE_EARLY_PER_MINUTE`, `FINE_LATE_PER_MINUTE`, `LATE_FINE_CAP_MINUTES`, `WARNING_STRIKE_LIMIT` endi faqat **boshlang'ich** qiymat: admin botdan o'zgartirgach, bazadagi qiymat ustun turadi (ish joyi nuqtasi va radius kabi).

## Bonus va jazolar

Admin istalgan ishchiga bonus yoki jazo yozib qo'yishi mumkin — ishchiga darhol shaxsiy xabar boradi va yozuv hisobotga tushadi.

- **Bonus** — sababini admin o'zi yozadi.
- **Jazo** — sabab tayyor ro'yxatdan tanlanadi (ro'yxat `main.py` dagi `JAZO_REASONS` da, xohlaganingizcha o'zgartiring):
  1. Rangli ichimlik yoki xidli mahsulot iste'mol qilish
  2. Uniforma kiymaganligi
  3. Ish vaqtida mobil qurilmalardan foydalanish

  Kerak bo'lsa "✏️ Boshqa sabab" orqali o'z matnini ham yozish mumkin.

## O'rnatish

```bash
pip install -r requirements.txt
```

**Bot tokeni** `config.py` faylida yoziladi. Faylni oching va `BOT_TOKEN` qatoriga @BotFather'dan olgan tokeningizni yozing:

```python
BOT_TOKEN = "123456789:ABCdef..."
```

⚠️ Token yozilgan `config.py` ni **ochiq (public) repoga yuklamang** — token ochilib qoladi va birov botingizni boshqarib olishi mumkin. Repo **private** bo'lsin. Token oshkor bo'lib qolsa, @BotFather → `/revoke` orqali yangisini oling.

Qolgan sozlamalar ham `config.py` da:

- `GROUP_CHAT_ID` — xabarlar yuboriladigan guruh IDsi (botni guruhga admin qilib qo'shing)
- `ADMIN_IDS` — ishchi qo'shish huquqiga ega shaxslarning Telegram ID raqamlari (masalan: `{123456789, 987654321}`)
- `CENTER_LATITUDE`, `CENTER_LONGITUDE` — ish joyining lokatsiyasi (**boshlang'ich** qiymat — botdan turib o'zgartirish mumkin)
- `RADIUS_METERS` — ruxsat etilgan masofa, metr (bu ham botdan o'zgartiriladi)
- `GPS_ACCURACY_TOLERANCE_METERS` — GPS xatoligiga beriladigan eng katta yon berish (metr)
- `EARLY_REQUIRED_MINUTES`, `FINE_EARLY_PER_MINUTE`, `FINE_LATE_PER_MINUTE`, `LATE_FINE_CAP_MINUTES`, `WARNING_STRIKE_LIMIT` — jarima sozlamalarining **boshlang'ich** qiymati (botdan o'zgartiriladi)
- `CHECKOUT_REQUIRES_CENTER` — ketayotganda joylashuv ish joyi radiusi ichida bo'lishi shartmi

`GROUP_CHAT_ID` va `ADMIN_IDS` ni ham xohlasangiz environment variable orqali o'zgartirish mumkin (BOT_TOKEN esa faqat `config.py` da).

## Admin paneli (tugmalar orqali)

Admin botga `/start` yozsa, boshqaruv tugmalari chiqadi — hech qanday buyruq yodlash shart emas:

- **➕ Ishchi qo'shish** — bot 5 qadamda hamma narsani o'zi so'raydi: ID, ism, familiya, kelish vaqti va ketish vaqti. Ishchining xabarini forward qilsangiz, ID avtomatik olinadi.
- **📋 Ishchilar ro'yxati** — har bir ishchi yonida ⏰ (kelish/ketish vaqtini o'zgartirish), 🗓 (haftalik jadval) va 🗑 (o'chirish, tasdiq bilan) tugmalari bo'ladi.
- **🏅 Bonus berish** — ishchini tanlaysiz, sababini yozasiz, bot ishchiga xabar yuboradi.
- **⚠️ Jazo berish** — ishchini tanlaysiz, sababini ro'yxatdan tanlaysiz.
- **📄 PDF hisobot** — "Shu oy", "O'tgan oy" yoki istalgan davr uchun hisobotni bir bosishda yuklab olish.
- **📍 Ish joyi lokatsiyasi** — hozirgi nuqtani xaritada ko'rsatadi, uni qaytadan belgilash va radiusni o'zgartirish imkonini beradi.
- **💰 Jarima sozlamalari** — kechikish jarimasi summalari, oldindan kelish vaqti va chegaralarni istalgan vaqtda o'zgartirish.

**🗓 Haftalik jadval qanday belgilanadi:** kerakli kunlarni bosib belgilaysiz (☑️), so'ng "⏰ Tanlangan kunlarga vaqt belgilash" tugmasini bosib kelish va ketish vaqtini kiritasiz. "🌙 Tanlangan kunlar — dam olish" belgilangan kunlarni dam olish kuniga aylantiradi, "🗑 Jadvalni tozalash" esa standart vaqtga qaytaradi.

Har qadamda "❌ Bekor qilish" tugmasi bor.

**Ishchining ID raqamini qanday bilish mumkin?** Ishchi botga `/start` yozsa, bot unga o'z ID raqamini ko'rsatadi — o'sha raqamni adminga yuborsa kifoya.

## Joylashuv aniqligi

Ishchilar ish joyida o'tirgan bo'lsa ham "siz N metr uzoqdasiz" degan javob olayotgan bo'lsa, sababi ikkitadan biri:

**1. Ish joyi nuqtasi noto'g'ri belgilangan.** `config.py` dagi koordinata bino ustiga aniq tushmagan bo'lishi mumkin. Tekshirish oson: adminda **📍 Ish joyi lokatsiyasi** tugmasini bosing — bot hozirgi nuqtani xaritada ko'rsatadi. Nuqta boshqa joyda bo'lsa, **🎯 Yangi ish joyini belgilash** ni bosib, ish joyi ichida turib jonli joylashuvingizni yuboring. Yangi nuqta bazada saqlanadi va `config.py` dagi qiymatdan ustun turadi.

**2. Telefonning GPS xatoligi.** Bino ichida telefon sun'iy yo'ldoshni ko'rmaydi va joylashuvni Wi-Fi/uyali tarmoq bo'yicha taxminlaydi — xatolik 100-300 metrgacha yetadi. Telegram har bir joylashuv bilan birga shu xatolik radiusini (`horizontal_accuracy`) yuboradi, bot esa uni masofadan ayiradi:

```
hisobga olinadigan masofa = haqiqiy masofa − min(GPS xatoligi, GPS_ACCURACY_TOLERANCE_METERS)
```

Yon berish `GPS_ACCURACY_TOLERANCE_METERS` (standart 300 m) bilan cheklangan — aks holda soxta GPS dasturi "xatoligim 10 000 metr" deb yozib, uzoqdan ham "keldim" qila olardi.

Ishchilarga aytiladigan maslahat: telefon sozlamalarida joylashuv aniqligini **High accuracy** ga qo'ying va Wi-Fi'ni yoqib qo'ying — bino ichida aniqlik sezilarli oshadi.

## Admin buyruqlari (ixtiyoriy)

Tugmalar o'rniga matnli buyruqlardan ham foydalansa bo'ladi:

- `/add_employee <telegram_id> <Ism> <Familiya> <HH:MM>` — yangi ishchi qo'shish
  masalan: `/add_employee 123456789 Ali Valiyev 09:00`
- `/set_time <telegram_id> <HH:MM>` — ishchining belgilangan vaqtini o'zgartirish
- `/remove_employee <telegram_id>` — ishchini ro'yxatdan o'chirish
- `/list_employees` — barcha ishchilar ro'yxati
- `/pdf_hisobot` — shu oy uchun barcha ishchilarning hisobotini PDF fayl qilib yuboradi
- `/pdf_hisobot 2026-07-01 2026-07-16` — belgilangan sanalar oralig'i uchun hisobot

## PDF hisobot ichida nima bo'ladi

1. **Umumiy yakun** — har bir ishchi bo'yicha: kelgan kunlar, kech qolgan kunlar, **jami jarima**, bonus, jazo va ogohlantirishlar. Pastida barcha ishchilar bo'yicha umumiy jarima yig'indisi.
2. **Kunlik davomat** — sana, kelgan va ketgan vaqt, belgilangan vaqt, kech qolgan daqiqalar va o'sha kungi **jarima summasi**. Sarlavhada joriy tarif ko'rsatiladi.
3. **Bonus, jazo va ogohlantirishlar** — kimga, qachon, qaysi turi va **sababi** bilan.

## Ishga tushirish

```bash
python main.py
```

## Render.com'ga deploy qilish

1. Kodingiz Git repozitoriyada bo'lishi kerak.
2. [dashboard.render.com](https://dashboard.render.com) ga kiring.
3. **New +** → **Background Worker** ni tanlang va repozitoriyingizni ulang.
4. Sozlamalar:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Instance Type:** Starter (bepul rejimda 15 daqiqa faolsizlikda uxlab qolishi mumkin)
5. **Create Background Worker** ni bosing — Render build qilib, botni avtomatik ishga tushiradi. Loglarda ishga tushgani ko'rinsa, hammasi tayyor.

**Maslahatlar:**
- Telegram botda HTTP server kerak emas — shuning uchun **Background Worker** turi tanlanadi (**Web Service** emas!).
- Baza fayli (`attendance.db`) deploy'da yo'qolmasligi uchun Render'da **Disk** ulash mumkin: Worker → Settings → Disks → Mount Path `/data`, va Environment'da `DB_PATH=/data/attendance.db` qo'shing.
- Repodagi `render.yaml` (Blueprint) orqali ham deploy qilsa bo'ladi: **New +** → **Blueprint** → repozitoriyni tanlash.

## Railway'ga deploy qilish

1. Railway'da yangi loyiha yarating va repozitoriyni ulang.
2. `config.py` da `BOT_TOKEN` yozilganiga ishonch hosil qiling.
3. `Procfile` avtomatik ravishda `worker: python main.py` jarayonini ishga tushiradi.
4. Diqqat: SQLite fayli (`attendance.db`) konteyner qayta ishga tushganda o'chib ketishi mumkin — agar davomat tarixini doimiy saqlamoqchi bo'lsangiz, Railway'ning Postgres qo'shimchasidan foydalanib, `main.py`dagi ma'lumotlar bazasi qismini PostgreSQL'ga moslashtirish tavsiya etiladi.

## Fayl tuzilishi

```
employee_attendance/
├── main.py            # Botning barcha kodi (baza, handlerlar, klaviaturalar)
├── config.py          # Sozlamalar (BOT_TOKEN shu yerda yoziladi)
├── requirements.txt
├── Procfile           # Railway uchun
├── render.yaml        # Render uchun
└── .gitignore         # baza fayli git'ga tushmasligi uchun
```
