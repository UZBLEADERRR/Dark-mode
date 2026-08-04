# Sarideo — Flow

Sarideo sahnalari uchun rasmlarni **Google Flow** dan olib keladigan Chrome
kengaytmasi.

Sabab oddiy: rasm API'lari qimmat, Google Flow obunasi esa allaqachon to'langan.
Rasm o'sha obuna hisobidan chiqadi, API hisobidan emas.

Kengaytmada **ikki bo'lim** bor:

| Bo'lim | Kim ishlaydi | Qachon |
|---|---|---|
| **Rasm yuborish** | Rasmlarni **siz** yasaysiz, kengaytma ularni loyihaga yuboradi | Promptlarni o'zingiz Flow'da bajarganda — yoki **Flow Agent** ishlatganda |
| **Avtomatik navbat** | Kengaytma **o'zi** navbatdan prompt olib, Flow varag'ida yasaydi | Hech narsa qilmasdan qo'yib qo'ymoqchi bo'lsangiz |

## Rasm yuborish — bir tugma

Flow'da o'zingiz ishlab, rasmlarni bittalab yuklab olish va qo'lda joylashtirish
— eng ko'p vaqt oladigan qismi shu edi. Endi bitta tugma:

1. Loyihani tanlaysiz (Sarideo'dagi tayyor loyihalar ro'yxati o'zi keladi).
2. **Rasmlar qayerdan** ni tanlaysiz:
   - **Flow varag'idan** — ochiq turgan Flow sahifasidagi hamma rasm olinadi.
     Sahifa qanday ko'rsatsa, shu tartibda; har biri eng katta o'lchamda olinadi
     (thumbnail emas).
   - **Flow Agent'dan** — [`kodelyx/flow-agent`](https://github.com/kodelyx/flow-agent)
     o'rnatilgan bo'lsa, uning tarixidagi rasmlar olinadi. Flow Agent'ning o'zi
     **o'zgartirilmaydi** va kodidan hech narsa ko'chirilmagan: undan faqat
     `GET /v1/history` o'qiladi va rasmlar yuklab olinadi. Videolar o'tkazib
     yuboriladi, tartib esa **eskidan yangiga** — ya'ni promptlarni qanday
     ketma-ket bajargan bo'lsangiz, shunday.
3. **Qanday taqsimlansin** — AI o'zi qarab, yoki fayl tartibida.
4. **Sarideoga yuborish**.

Qolganini Sarideo qiladi: har bir rasmga qarab, qaysi sahnaning promptiga to'g'ri
kelishini o'zi topadi ([asosiy README](../README.md#rasmlarni-topi-bilan-yuklash--ai-ozi-joylashtiradi)).

Bir martada 200 tagacha rasm. Sahnadan ko'p bo'lsa ortganini aytadi, kam bo'lsa
qaysi sahnalar rasmsiz qolganini aytadi.

## Avtomatik navbat — nima qiladi

```
Sarideo (serverda)                    brauzeringiz (uyda)
  sahna prompti  ──── navbat ────►  kengaytma olib ketadi
                                          │
                                     Google Flow varag'i
                                          │
  sahnaga tushadi ◄─── PNG ─────────  kengaytma qaytaradi
```

Aylanish shu qadar: navbatdan bitta prompt olinadi, Flow varag'iga yoziladi,
chiqqan rasm Sarideo'ga yuboriladi, keyingisiga o'tiladi.

## O'rnatish

1. Sarideo'da **Kutubxona → Flow navbati** dagi tugmachani yoqing.
2. Arxivni **doimiy papkaga** chiqaring — Temp emas. Chrome papkani o'z joyida
   o'qib turadi, Temp esa tozalanib ketadi.
3. Chrome'da `chrome://extensions` ni oching, o'ng yuqoridan **Developer mode**
   ni yoqing.
4. **Load unpacked** → o'sha **papkani** tanlang.
5. Kengaytma belgisini bosing (Chrome panelida), **Sarideo manzili** ga
   ilovangiz manzilini yozing — masalan `https://dark-mode-production.up.railway.app`.
   **Ushbu brauzer nomi** — shunchaki nom (`laptop`), manzil emas.
6. `labs.google/fx/tools/flow` ni oching va Google akkauntingiz bilan kiring.
   Google sizni o'z tilingizdagi manzilga o'tkazishi mumkin
   (`labs.google/fx/uz/tools/flow/...`) — bu normal, kengaytma baribir ulanadi.
7. **Bittasini hozir bajarish** bilan sinab ko'ring, ishlasa **Boshlash**.

Endi video yaratganingizda sahnalarning rasmlari o'sha varaqda yasaladi.

> **`options.html` ni ikki marta bosib ochmang.** U oddiy fayl bo'lib ochiladi
> (`file:///…`), bunda `chrome.*` API'lari mavjud emas — oyna ko'rinadi, lekin
> birorta tugma bosilmaydi. Sahifa buni endi o'zi aytadi va nima qilishni
> tushuntiradi. To'g'ri yo'l — yuqoridagi 3-4 qadam.

## Tugmalar

| Tugma | Nima qiladi |
|---|---|
| **Sarideoga yuborish** | Tanlangan manbadagi hamma rasmni loyihaga yuboradi |
| **Boshlash** | Navbat bo'shaguncha ishlaydi, keyin har yarim daqiqada qarab turadi |
| **To'xtatish** | Yangi prompt olmaydi (boshlangani tugaydi) |
| **Bittasini hozir bajarish** | Bitta prompt — sinab ko'rish uchun |
| **Flow sahifasini tekshirish** | Varaqda prompt maydoni va tugma topilyaptimi — buzilganda birinchi qaraladigan joy |

## Sizning ma'lumotlaringiz

Kengaytma **Google parolingizni ham, cookie'ngizni ham o'qimaydi va hech qayerga
yubormaydi.** Flow varag'iga kirish brauzeringizdagi mavjud seans orqali bo'ladi
— xuddi o'zingiz o'sha sahifani ochganingizdek. Kengaytmadan chiqadigan yagona
narsa — Sarideo'ga ketayotgan PNG.

Sarideo'ning o'zida login yo'q, shuning uchun ilovani ochiq internetda hammaga
ko'rinadigan qilib qo'ymang: manzilni bilgan har kim navbatga qarashi mumkin.

## Buzilganda

Flow — Google'ning sahifasi, va u xohlagan payti o'zgaradi. Sahifaga tegishli
hamma narsa **bitta faylda** — `flow-dom.js`, eng boshidagi `SELECTORS` bloki.
O'sha fayl telefon uchun mo'ljallangan `agent/` bilan **umumiy**: bir marta
to'g'irlasangiz, ikkalasi ham tuzaladi.

```js
const SELECTORS = {
  prompt: [ ... ],   // prompt yoziladigan maydon
  submit: [ ... ],   // generatsiyani boshlaydigan tugma
  result: [ ... ],   // tayyor rasm
};
```

Har biri — ro'yxat: birinchisi topilmasa, keyingisi sinaladi. Buzilsa:

1. **Flow sahifasini tekshirish** ni bosing — nima topilib, nima topilmayotgani
   yoziladi.
2. Flow varag'ida o'ng tugma → **Inspect** → kerakli elementning selektorini
   oling.
3. Uni tegishli ro'yxatning **boshiga** qo'shing va `chrome://extensions` da
   kengaytmani yangilang.

Boshqa fayllarga tegish shart emas: `flow.js` faqat xabar almashadi va
`background.js` faqat "prompt yubordim, rasm keldi" ni biladi.

## Kengaytmasiz

Telefonda kengaytma **ishlamaydi** — Android'dagi Chrome uni qo'llamaydi. Unga
javob `agent/` da: brauzer boshqa mashinada turadi va hammasini o'zi qiladi
([agent/README.md](../agent/README.md)).

Umuman kengaytmasiz ham bo'ladi. Sarideo'ning **Kutubxona → Flow navbati** bo'limida har
bir kutayotgan prompt turadi: promptni nusxalaysiz, Flow'da (yoki xohlagan
joyda) rasm yasaysiz, va o'sha yerga yuklaysiz. Telefondan ham ishlaydi.

## Cheklovlar

- **Flow'ning shartlari.** Sahifani avtomatlashtirish Google'ning foydalanish
  shartlariga zid bo'lishi mumkin. Bu o'z akkauntingiz va o'z obunangiz, lekin
  xavf — akkauntning cheklanishi — sizning zimmangizda. Tezlikni oshirmang:
  promptlar orasidagi tanaffus shuning uchun bor.
- **Selektorlar sinovdan o'tmagan.** Yuqoridagi `SELECTORS` — Flow sahifasining
  bugungi ko'rinishiga qarab yozilgan taxmin; bu repoda ular haqiqiy Flow'da
  emas, soxta sahifada sinaldi. Birinchi ishga tushirishda «Flow sahifasini
  tekshirish» dan boshlang.
- **Bitta varaq, bitta rasm.** Bir vaqtda bitta prompt bajariladi.
