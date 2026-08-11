# Sarideo'ni o'z noutbukingizda ishlatish

Video yasashning eng og'ir qismi — har bir sahna rasmini animatsiya qilib,
videoga kodlash. Bu sof protsessor ishi. Noutbukingizda protsessor kichik bulut
serveridan ko'proq, daqiqasiga pul olmaydi, xotira chegarasi yo'q va disk
qancha bo'lsa shuncha. Bulutda tugamaydigan 92 sahnali video shu yerda tugaydi.

Telefoningizdan boshqarasiz, og'ir ishni noutbuk qiladi.

---

## 1. Docker'ni o'rnating

Bir marta, 5 daqiqa: <https://docs.docker.com/get-docker/>

Windows va macOS uchun **Docker Desktop**, Linux uchun `docker` va
`docker-compose-plugin`. O'rnatgach Docker Desktop'ni **ishga tushirib qo'ying**
(Windows'da u avtomatik ishlamaydi).

## 2. Loyihani oling

```
git clone https://github.com/UZBLEADERRR/Dark-mode.git
cd Dark-mode
```

`git` bo'lmasa: GitHub sahifasidan **Code → Download ZIP**, keyin arxivni oching
va o'sha papkaga kiring.

## 3. Ishga tushiring

**Windows:** `start.bat` faylini ikki marta bosing.

**macOS / Linux:**

```
./start.sh
```

Birinchi marta Docker rasmni yig'adi — 3-5 daqiqa. Keyingi safarlar bir necha
soniya.

Tugagach ekranda ikkita manzil chiqadi:

```
  Shu noutbukda:  http://localhost:8000
  Telefonda:      http://192.168.1.15:8000   (bir xil Wi-Fi'da)
```

Ikkinchisini telefon brauzeriga yozing — o'sha Sarideo, lekin render
noutbukda ketadi.

> Telefonda ochilmasa: noutbuk va telefon bir xil Wi-Fi'da ekaniga ishonch
> hosil qiling, va Windows'da birinchi safar chiqadigan «Windows Defender
> Firewall» oynasida **Allow access** ni bosing.

## 4. Kalitlaringizni kiriting

Ikki yo'l bor, ikkinchisi osonroq:

- **Ilova ichida:** Kutubxona → **API kalitlari** → qo'shing. Bitta provayderga
  bir nechta kalit qo'shsangiz, biri limitga urilganda o'zi keyingisiga o'tadi.
- **Fayl orqali:** papkadagi `.env` faylini oching (birinchi ishga tushirishda
  o'zi yaratiladi) va kalitlarni yozing, keyin `./start.sh` ni qayta bosing.

## 5. Flow Agent ham shu yerda (ixtiyoriy)

Rasmlarni Google Flow orqali yasasangiz, uning backendini ham shu noutbukda
ishlating — kengaytma bilan bitta mashinada bo'ladi, tarmoqqa chiqmaydi:

```
./start.sh flow          # Windows: start.bat flow
```

Keyin kengaytma sozlamalarida manzilni `http://localhost:8001` qiling.

---

## Kundalik buyruqlar

| | macOS / Linux | Windows |
|---|---|---|
| Ishga tushirish | `./start.sh` | `start.bat` |
| To'xtatish | `./start.sh stop` | `start.bat stop` |
| Yangilash | `./start.sh update` | `start.bat update` |
| Jurnalni ko'rish | `docker compose logs -f sarideo` | bir xil |

Hamma narsa — videolar, rasmlar, ovozlar, herolar — papkadagi **`data/`** ichida.
Uni nusxalasangiz, butun ish ko'chadi.

---

## Tezlik

Render tezligi bitta narsaga bog'liq: **bir vaqtda nechta sahna animatsiya
qilinmoqda**. O'lchangan: to'rtta sahna bitta sahnaning vaqtida chiqadi, ya'ni
bu son to'g'ridan-to'g'ri tezlik.

Ilova buni o'zi hisoblaydi — noutbuk yadrolari soniga qarab, 8 tagacha. Qo'lda
o'zgartirmoqchi bo'lsangiz `.env` da:

```
RENDER_WORKERS=6      # noutbukni ish paytida band qilmaslik uchun kamaytiring
RENDER_SPEED=fast     # har sahna ikki barobar tez, telefonda farqi bilinmaydi
```

Bulutda kerak bo'lgan **«Videoni bo'laklab berish»** sozlamasi bu yerda shart
emas — noutbukda xotira chegarasi yo'q, uzun videoni bitta fayl qilib beraveradi.
Xohlasangiz baribir ishlatasiz, ishlaydi.
