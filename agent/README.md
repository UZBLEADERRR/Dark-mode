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

## Railway'da — telefondan, kompyutersiz

Agar sizda faqat telefon bo'lsa, agentni Sarideo yonida **ikkinchi servis** qilib
qo'yasiz. Hammasi telefon brauzeridan bajariladi.

**1. Servis yarating.** Railway loyihangizda `+ New` → `GitHub Repo` → shu repo.

**2. Config faylini ko'rsating.** Bu **eng muhim qadam** va uni o'tkazib
yuborish oson.

Repo ildizida `railway.json` bor va u **Sarideo uchun**. Railway config faylini
dashboard sozlamalaridan **ustun** qo'yadi, ya'ni agent servisi ham o'shani
o'qiydi: Sarideo'ning Dockerfile'ini quradi va Sarideo'ning `/api/health` ini
kutadi. Natijada log'da shunday chiqadi:

```
python: can't open file '/srv/sarideo_agent.py': No such file or directory
1/1 replicas never became healthy!
```

Shuning uchun agent servisining Settings'ida **Config-as-code** (yoki *Railway
Config File*) maydoniga shuni yozing:

```
agent/railway.json
```

O'sha faylda to'g'ri Dockerfile ham, healthcheck yo'qligi ham yozilgan —
Dockerfile Path'ni qo'lda o'zgartirish shart emas.

**3. Volume ulang** — `/profile` ga. Loyiha ekranida `+ New` → `Volume` →
servisni tanlaysiz. Bo'lmasa ham ishlaydi, faqat har restartda qayta kirasiz.

Dockerfile'da `VOLUME` ko'rsatmasi **yo'q** va bo'lmasligi kerak ham: Railway
mount'larni o'zi boshqaradi va bunday Dockerfile'ni umuman qurmaydi —
`dockerfile invalid: docker VOLUME ... is not supported, use Railway Volumes`.

**4. O'zgaruvchilar** (Variables):

```
SARIDEO_URL=https://<sarideo-manzilingiz>
SARIDEO_LOGIN_TOKEN=<o'zingiz o'ylab topgan parol>
```

**5. Bir marta kiring.** Start Command'ni vaqtincha shunga o'zgartiring:

```
python sarideo_agent.py login
```

Deploy bo'lgach servisga **Public Domain** bering (Settings → Networking →
Generate Domain). Log'da manzil chiqadi:

```
Telefoningizdan oching:  https://<agent-manzili>/?t=<parolingiz>
```

Shu havolani telefonda oching → brauzerning ekranini ko'rasiz → Google'ga
kiring → **«Kirdim — saqla»**.

Token **`?t=`** dan keyin turadi, yo'l sifatida emas: `…app/?t=1111` to'g'ri,
`…app/1111` noto'g'ri (ikkinchisini ham tushunadi va o'zi to'g'ri manzilga
o'tkazadi, lekin bilib qo'ygan yaxshi).

Tokenni jiddiy tanlang — `1111` emas. O'sha havolani bilgan odam sizning
Google seansingiz turgan brauzerni boshqara oladi.

Google `400 — malformed` bersa, sabab odatda brauzerning o'zini
`HeadlessChrome` deb tanishtirishi bo'ladi — ba'zi Google sahifalari shunga
sahifa o'rniga xato qaytaradi. Agent endi oddiy Chrome sifatida tanishtiradi
(kerak bo'lsa `SARIDEO_USER_AGENT` bilan o'zgartirasiz).

Yuqorida **manzil satri** bor. Flow ochilmasa (Google `400` yoki boshqa xato
bersa), o'sha yerga to'g'ri manzilni yozib **«Ochish»** bosasiz — brauzer o'sha
yerga o'tadi. Qayerga tushgani satrda ko'rinadi, ya'ni redirect ham bilinadi.
Ishlagan manzilni topsangiz, uni `SARIDEO_FLOW_URL` o'zgaruvchisiga yozing —
shunda `run` ham o'shani ochadi.

**6. Ishga tushiring.** Start Command'ni **bo'shating** (image'ning o'zi
`run` bilan boshlanadi), yoki shunday yozing:

```
python sarideo_agent.py run
```

Tamom. Endi Sarideo'da video buyursangiz, rasmlar o'zi yasaladi.

**Public Domain'ni o'chirib qo'ying** — u faqat kirish uchun kerak edi, va
`run` rejimida hech qanday port ochilmaydi.

`agent/Dockerfile` Sarideo image'iga qo'shilmagan: Chromium bir necha yuz
megabayt va ffmpeg'ga kerak bo'lgan xotirani yeydi; kichik konteynerda ikkovi
birga render'ni o'ldiradi.

## Sozlamalar

| O'zgaruvchi | Nima |
|---|---|
| `SARIDEO_URL` | Sarideo manzili |
| `SARIDEO_WORKER` | Bu mashinaning nomi (navbatda kim olganini ko'rsatadi) |
| `SARIDEO_PROFILE` | Brauzer profili qayerda saqlanadi |
| `SARIDEO_CHROME` | Boshqa Chromium ishlatmoqchi bo'lsangiz |
| `SARIDEO_LOGIN_TOKEN` | Kirish sahifasining paroli (bermasangiz o'zi o'ylab topadi va log'ga yozadi) |
| `PORT` | Kirish sahifasi qaysi portda — hosting o'zi beradi, qo'lda yozish shart emas |
| `SARIDEO_FLOW_URL` | Flow manzili. Google uni o'zgartirsa, kodga tegmasdan shu yerdan to'g'irlaysiz |
| `SARIDEO_USER_AGENT` | Brauzer o'zini qanday tanishtiradi. Standarti — oddiy Chrome |

Bir nechta mashina bir vaqtda ishlashi mumkin — har biri o'z promptini oladi,
navbat ularni chalkashtirmaydi.
