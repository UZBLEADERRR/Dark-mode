# Sarideo — Flow bridge

Sarideo sahnalari uchun rasmlarni **Google Flow** da yasab, ilovaga qaytaradigan
Chrome kengaytmasi.

Sabab oddiy: rasm API'lari qimmat, Google Flow obunasi esa allaqachon to'langan.
Kengaytma sizning brauzeringizdagi Flow varag'ini boshqaradi — ya'ni rasm o'sha
obuna hisobidan chiqadi, API hisobidan emas.

## Nima qiladi

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

1. Sarideo'da **Kutubxona → Modellar** dan yoki video yaratayotganda **Rasm
   provayderi** ni `flow` qilib qo'ying.
2. Chrome'da `chrome://extensions` ni oching, o'ng yuqoridan **Developer mode**
   ni yoqing.
3. **Load unpacked** → shu `extension/` papkasini tanlang.
4. Kengaytma belgisini bosing, **Sarideo manzili** ga ilovangiz manzilini yozing
   (masalan `https://sarideo.up.railway.app`).
5. `labs.google/fx/tools/flow` ni oching va Google akkauntingiz bilan kiring.
6. Kengaytmada **Boshlash** ni bosing.

Endi video yaratganingizda sahnalarning rasmlari o'sha varaqda yasaladi.

## Tugmalar

| Tugma | Nima qiladi |
|---|---|
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
hamma narsa **bitta faylda** — `flow.js`, eng boshidagi `SELECTORS` bloki:

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

Boshqa fayllarga tegish shart emas: `background.js` faqat "prompt yubordim,
rasm keldi" ni biladi.

## Kengaytmasiz

Kengaytma shart emas. Sarideo'ning **Kutubxona → Flow navbati** bo'limida har
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
