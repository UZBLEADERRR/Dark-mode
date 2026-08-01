# Flow agenti — telefon uchun

Kengaytma kompyuter uchun. **Telefonda kengaytma ishlamaydi** — Android'dagi
Chrome uni umuman qo'llamaydi. Shuning uchun brauzer doim yoqiq turadigan
boshqa joyda bo'lishi kerak: uydagi kompyuter, arzon VPS, yoki Sarideo yonidagi
ikkinchi servis. Bu — o'sha brauzer.

Sizdan talab qilinadigan yagona narsa — **bir marta** Google'ga kirish. Undan
keyin telefoningizga hech narsa qilish kerak emas: video buyurasiz, rasmlar o'zi
yasaladi, video tayyor bo'ladi.

## Avval bilib qo'ying

- **Google seansi o'sha mashinada qoladi.** Kengaytma ataylab teskari qilingan
  edi — u akkauntni umuman ko'rmaydi. Bu esa o'shani beradi, evaziga sizsiz
  ishlaydi. Faqat o'zingiz nazorat qiladigan mashinada ishlating.
- **Parol hech qayerda saqlanmaydi va hech narsa uni yozmaydi.** Siz o'zingiz
  kirasiz — quyidagi `login` shunchaki brauzerning boshqaruvini telefoningizga
  uzatadi. Saqlanadigan narsa — brauzer profili, xuddi o'sha kompyuterda
  o'zingiz kirganingizdek.
- **VPS'dan kirish qiyinroq.** Google datacenter IP'laridan kirishni ancha
  qattiq tekshiradi: uydagi kompyuterda chiqmaydigan tasdiqlash oynasi VPS'da
  chiqishi mumkin. U ekranda ko'rinadi, odatdagidek javob berasiz.
- **Sahifani avtomatlashtirish Google shartlariga zid bo'lishi mumkin.** Bu o'z
  akkauntingiz, lekin xavf sizning zimmangizda.

## O'rnatish

```bash
cd agent
pip install -r requirements.txt
playwright install chromium
```

### 1. Bir marta — Google'ga kirish

```bash
python sarideo_agent.py login --port 8777
```

Terminalda havola chiqadi:

```
Telefoningizdan oching:  http://<shu-mashina>:8777/?t=AbC123xyz
```

Shu havolani telefonda oching. Brauzerning ekranini ko'rasiz:

- **rasm ustiga bossangiz** — o'sha joyga bosiladi;
- **tepadagi maydonga yozib Enter bossangiz** — o'sha matn yoziladi;
- Google'ga kirib bo'lgach — **«Kirdim — saqla»**.

Havoladagi `?t=` — parol. Usiz sahifa ochilmaydi (403), chunki bu havola
brauzeringizni boshqaradi.

Ekrani bor kompyuterda ishlayotgan bo'lsangiz, oddiyroq yo'l:

```bash
python sarideo_agent.py login --headed --headless=false
```

### 2. Ishga tushirish

```bash
SARIDEO_URL=https://sarideo.up.railway.app python sarideo_agent.py run
```

Bo'shi shu. Navbatni kuzatib turadi, prompt chiqishi bilan Flow'da rasm yasab
qaytaradi. Sarideo'da esa **Kutubxona → Flow navbati** dagi tugmacha yoqilgan
bo'lsin.

### 3. Buzilganda

```bash
python sarideo_agent.py probe
```

Flow sahifasida prompt maydoni va tugma topilyaptimi — shuni aytadi. Topilmasa
`extension/flow-dom.js` dagi `SELECTORS` ni to'g'irlash kerak (o'sha fayl
kengaytma bilan **umumiy** — bir marta tuzatsangiz, ikkalasi ham tuzaladi).

## Servis sifatida (Railway va h.k.)

`agent/Dockerfile` bor. Sarideo image'iga qo'shilmagan — Chromium bir necha yuz
megabayt va ffmpeg'ga kerak bo'lgan xotirani yeydi; kichik konteynerda ikkovi
birga render'ni o'ldiradi. Alohida servis qiling:

- Dockerfile: `agent/Dockerfile`, kontekst — reponing ildizi
- `SARIDEO_URL` — Sarideo manzili
- `/profile` ga **volume** ulang, aks holda har qayta ishga tushganda yangi
  brauzer va yangi kirish bo'ladi
- birinchi marta buyruqni `python sarideo_agent.py login --port 8777` qilib,
  portni ochib, telefondan kirasiz; keyin `run` ga qaytarasiz

## Sozlamalar

| O'zgaruvchi | Nima |
|---|---|
| `SARIDEO_URL` | Sarideo manzili |
| `SARIDEO_WORKER` | Bu mashinaning nomi (navbatda kim olganini ko'rsatadi) |
| `SARIDEO_PROFILE` | Brauzer profili qayerda saqlanadi |
| `SARIDEO_CHROME` | Boshqa Chromium ishlatmoqchi bo'lsangiz |

Bir nechta mashina bir vaqtda ishlashi mumkin — har biri o'z promptini oladi,
navbat ularni chalkashtirmaydi.
