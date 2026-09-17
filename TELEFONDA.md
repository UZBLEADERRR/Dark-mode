# Sarideo'ni telefonning o'zida ishlatish

Bu — `.apk`. O'rnatasiz, ochasiz, kalitni yozasiz, video yasaysiz. Boshqa hech
narsa kerak emas: server yo'q, Railway yo'q, Supabase yo'q, noutbuk yo'q, bir xil
Wi-Fi ham shart emas. Internet faqat bitta narsa uchun kerak — Gemini'dan matn,
ovoz va rasm so'rash uchun. Videoni **telefonning o'zi** yig'adi.

Kalitlaringiz ham shu yerda qoladi. Ular telefonning ichidagi bazada, ilovaning
o'z papkasida yotadi — hech qayerga yuborilmaydi va boshqa hech kim ularni
ko'rmaydi.

---

## 1. APK'ni oling

**GitHub'dan.** Loyihaning **Actions** bo'limiga kiring → **APK** ishini oching →
pastdagi **Artifacts** ichidan `sarideo-apk` ni yuklab oling. Ichida bitta
`.apk` fayl bo'ladi.

**O'zingiz yig'moqchi bo'lsangiz** — pastda, «O'zingiz yig'ish» bo'limida.

## 2. O'rnating

Telefon o'zi yuklab olingan `.apk` ni ochishga ruxsat so'raydi — «Noma'lum
manbalar»ga ruxsat bering, faqat shu safar uchun. Keyin **O'rnatish**.

> Telefoningiz **arm64** bo'lishi kerak. 2019-yildan keyingi deyarli hamma
> Android telefon shunday.

## 3. Birinchi ochilish

Birinchi marta ilova bir necha soniya «ishga tushmoqda» deb turadi: ichidagi
Python o'zini telefonga yoyadi. Keyingi safarlar tezroq.

Keyin o'sha tanish Sarideo oynasi ochiladi — noutbukdagi va bulutdagi bilan
bitta oyna, chunki bu o'sha ilovaning o'zi.

## 4. Kalitni yozing

**Kutubxona → API kalitlari → qo'shing.**

Bitta Gemini kaliti yetarli: skript ham, rasm ham, ovoz ham, YouTube matnlari
ham o'sha kalitdan chiqadi. Bir nechta kalit qo'ysangiz, ilova ularni navbat
bilan ishlatadi va biri limitga urilganda kutmay keyingisiga o'tadi — bulutdagi
bilan bir xil.

Kalit telefondagi bazaga yoziladi. `.env` fayl ham, muhit o'zgaruvchisi ham,
bulutdagi «secret» ham kerak emas.

---

## Ekran o'chsa nima bo'ladi

Render davom etadi. Ilova ishlayotgan paytda yuqorida bildirishnoma turadi —
**«Sarideo ishlamoqda»**. O'sha bildirishnoma Android'ga «bu ishni to'xtatma»
deb aytadi; usiz telefon ekran o'chgan zahoti renderni uzib qo'yardi.

Tugagach, bildirishnomadagi **To'xtatish** ni bossangiz ilova butunlay yopiladi.

## Tayyor video qayerda

Videoni ilova ichida ko'rasiz. **Yuklab olish** bosilsa — telefonning
**Downloads** papkasiga tushadi, ya'ni galereya ham, fayl menejeri ham, Telegram
ham uni ko'radi. Subtitr fayllari (`.srt`, `.vtt`) va skript (`.txt`) ham shu
yerga.

Ish papkasining o'zi — loyihalar, rasmlar, ovozlar, herolar, kalitlar — ilovaning
ichki xotirasida. Ilovani o'chirsangiz, o'sha bilan birga ketadi.

## Qancha vaqt oladi

Videoni yig'ish — sof protsessor ishi, va telefon protsessori noutbuknikidan
kichik. Ilova buni hisobga oladi:

- **Bir vaqtda ikki-uch sahna** animatsiya qilinadi, noutbukdagi sakkiztaning
  o'rniga. Telefonda yadrolar soni ko'p ko'rinadi, lekin ularning yarmi kichik
  yadrolar, hammasi bitta xotira yo'lini bo'lishadi va telefon qizigan zahoti
  o'zini sekinlashtiradi — sakkiztani so'rash tezlik emas, issiqlik beradi.
- **`RENDER_SPEED=fast`** standart holatda yoqiq: har sahna ikki barobar tez
  kodlanadi, telefon ekranida farqi bilinmaydi.
- **Xotira chegarasi** telefonning o'z xotirasidan hisoblanadi, uchdan bir qismi.
  Android boshqa ilovaga joy kerak bo'lganda xotira ushlab turgan jarayonni
  o'ldiradi, va 60-sahnada uzilgan render sekinroq tugagan renderdan qimmatroq.

Bularning hammasini **Sozlamalar**dan o'zgartirasiz.

Baribir uzun video telefonda uzoq ketadi. 90 sahnali videoni noutbukda qilish
tezroq — **[NOUTBUKDA.md](NOUTBUKDA.md)**.

## Nimalar ishlamaydi

Halol ro'yxat:

- **YouTube'ga to'g'ridan-to'g'ri joylash.** Google OAuth ilovaga tashqaridan
  ko'rinadigan manzil talab qiladi, telefonda esa u yo'q. Videoni yuklab olib,
  YouTube ilovasidan joylaysiz.
- **Flow (brauzer kengaytmasi orqali rasm).** Kengaytma kompyuter brauzeriga
  o'rnatiladi. Telefonda rasmlarni Gemini yoki fal.ai yasaydi.
- **Koreys, arab, hind yozuvlaridagi subtitr.** Telefon versiyasidagi ffmpeg
  harfbuzz'siz yig'ilgan va ichida DejaVu shrifti bor — lotin va kirill to'liq
  ishlaydi, boshqa yozuvlar esa yo'q. O'zbek tili uchun ikkisi ham yetarli.

Qolgan hammasi — suhbat, reja, studio, tarjima, Shorts, brend, multfilm rejimi,
o'z ovozingizni yuklash — bulutdagidek ishlaydi, chunki bu o'sha kod.

---

## O'zingiz yig'ish

Kerak bo'ladi: **Android Studio** (yoki JDK 17 + Android SDK) va **Android NDK**.

```
git clone https://github.com/UZBLEADERRR/Dark-mode.git
cd Dark-mode

# ffmpeg — bir marta, 20-40 daqiqa. Natija: ikkita fayl.
ANDROID_NDK_HOME=~/Android/Sdk/ndk/26.3.11579264 \
  scripts/build-ffmpeg-android.sh android/app/src/main/jniLibs/arm64-v8a

cd android
./gradlew assembleDebug
```

`android/app/build/outputs/apk/debug/app-debug.apk` — o'sha.

Yoki hech narsa o'rnatmasdan: GitHub'da **Actions → APK → Run workflow**, keyin
tayyor APK'ni artifacts'dan olasiz.

## Ichida nima bor

| | |
|---|---|
| **Python 3.12** | Chaquopy orqali paketning ichida. Telefonda Python o'rnatish shart emas. |
| **`app/` paketi** | Serverdagi kodning **o'zi**, nusxasi emas. Birinchi ochilishda telefon xotirasiga yoyiladi. |
| **ffmpeg + ffprobe** | arm64 uchun statik yig'ilgan, x264 va libass bilan. `lib*.so` nomi bilan yuriydi, chunki Android faqat paketning kutubxona papkasidagi faylni ishga tushirishga ruxsat beradi. |
| **DejaVu shrifti** | Telefonda shrift bazasi yo'q, libass esa oilani nom bilan so'raydi. Shriftlar paketning ichida. |
| **Web UI** | O'sha `app/static`. WebView uni `127.0.0.1` dan oladi. |

Server faqat **loopback** manzilini eshitadi — ya'ni Wi-Fi'dagi boshqa hech
qaysi qurilma unga ulana olmaydi.

Bitta o'zgartirish bor: telefonda **pydantic 2** emas, **pydantic 1** ishlatiladi.
Pydantic 2 ning yuragi Rust'da yozilgan va Android uchun yig'ilmagan. Ikkalasi
farq qiladigan ikki joyni `pydantic_v1_bridge.py` bog'laydi — `app/` dagi kod
o'zgarmagan.

## Litsenziya haqida

Telefon versiyasidagi ffmpeg **x264** bilan yig'ilgan, x264 esa GPL. Ya'ni
tarqatilayotgan APK GPL shartlari ostida bo'ladi va uning manbasi ochiq turishi
kerak — bu repozitoriy allaqachon ochiq, shuning uchun qo'shimcha hech narsa
qilish shart emas.
