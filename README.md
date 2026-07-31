# Sarideo

Mavzu nomini yozasiz — ilova skript yozadi, ovoz beradi, har bir sahna uchun rasm
yaratadi, subtitr qo'shadi va tayyor MP4 ni render qiladi.

**Bitta Gemini kaliti yetarli.** Skript, rasm promptlari, subtitr, ovoz va YouTube
matnlari — hammasi o'sha bitta kalitdan ishlaydi. Bir nechta kalit qo'ysangiz esa
ilova ularni navbat bilan ishlatadi va limitga urilganda kutmaydi —
[API kalitlari](#api-kalitlari--bir-nechta-ilovaning-ozida).

---

## Nima qiladi

```
Mavzu ─▶ director ─▶ ✋ MATNNI O'QISH ─▶ imagesmith ─▶ ovoz ─▶ rasmlar
        (skript)    (hali hech narsa      (promptlar)   (TTS)    (AI)
                     yaratilmagan)                                 │
                                                                   ▼
                     MP4 ◀── render ◀── Ken Burns ◀── ✋ KO'RIB CHIQISH
                             (ffmpeg)   zoom / pan      (tahrirlash)
```

## Matnni avval o'qish

Video ikki joyda to'xtaydi, va ular **har xil narxda** to'xtaydi.

**Birinchi to'xtash — matn.** «Avval matnni o'qib chiqaman» belgilansa (standart
holatda yoqiq), ilova skript yozilgan zahoti to'xtaydi: **hali na bitta ovoz
yozilgan, na bitta rasm chizilgan.** Ya'ni bu — fikringizni o'zgartirish
**bepul** bo'lgan yagona payt.

Bu yerda ikki narsa qila olasiz:

- **Qo'lda tuzatish** — har bir qatorni to'g'ridan-to'g'ri yozasiz.
- **AI'ga aytish** — «3-sahna quruq chiqibdi, qiziqroq qil», «oxirini kuchaytir».
  AI faqat aytilganini o'zgartiradi, qolgan qatorlar **harfma-harf o'z holicha
  qoladi** — va qaysi qator o'zgargani belgilanib ko'rsatiladi, butun matnni
  qaytadan o'qib chiqmasligingiz uchun.

**Faylga yuklab olish.** Shu oynada **«Matnni yuklab olish»** bor — butun skript
`.txt` bo'lib tushadi (fayl nomi videoning nomi bilan). Uzun matnni telefonda
emas, boshqa joyda o'qish qulayroq.

`.srt` va `.vtt` bu bosqichda **bo'sh fayl bermaydi**, aniq sabab bilan rad
etadi: vaqtlar hali hisoblanmagan, ular render paytida paydo bo'ladi. So'zlar
vaqtlardan oldin bor bo'ladi — matn bosqichi shuning o'zi.

«Tasdiqlash va davom etish» bosilgandan keyingina ovoz yoziladi va rasm
chiziladi — ya'ni **siz tasdiqlagan matn** ovozga aylanadi.

**Ikkinchi to'xtash — qoralama.** Qoralama tayyor bo'lgach ilova to'xtaydi va sahnalarni ko'rsatadi. Istalgan
sahnaning matnini, rasm promptini, kamera harakatini yoki ekran yozuvini
o'zgartirasiz, kerak bo'lsa faqat o'sha sahnaning rasmini/ovozini qayta
yaratasiz — butun video qaytadan hisoblanmaydi. Keyin **Render** bosasiz.

Ko'rib chiqish kerak bo'lmasa, formada "Render'dan oldin ko'rib chiqaman"
katagini olib tashlang — u holda hammasi bir yo'la tugaydi.

## Suhbat

Forma to'ldirishning o'rniga aytib ham bo'ladi. **Suhbat** bo'limida:

1. **Kanalingiz skrinshotini yuklaysiz** — Instagram, YouTube yoki TikTok. AI
   uni bir marta o'qiydi va **aniq narsalarni** yozib qo'yadi: mavzu, kim ko'radi,
   postlar tili, nima post qilasiz, qanday olinadi. O'qigani saqlanadi, ya'ni
   keyingi har savolda rasm qayta yuborilmaydi.

   Kartochkadagi **✎** tugmasi bilan har birini qo'lda tuzatasiz. Bu muhim:
   g'oyalar aynan shu ma'lumotlarga tayanib beriladi, noto'g'ri bo'lsa g'oyalar
   ham umumiy chiqadi.
2. **G'oya so'raysiz** — «YouTube Shorts uchun g'oyalar ber». Javob kartalar bilan
   keladi: sarlavha, ilk jumla, nega shu kanalda ishlaydi, **nimaga tayangani**
   (qaysi ruknni davom ettiradi yoki qaysi bo'shliqni to'ldiradi), va necha soniya.

   Har bir g'oya bitta sinovdan o'tishi kerak: **shu platformadagi har qanday
   kanalga to'g'ri keladigan g'oya — g'oya emas.** Agar AI g'oya nimaga tayanganini
   yozib bera olmasa, u g'oya ro'yxatga tushmaydi. Kanal ko'rsatilmagan bo'lsa
   taxmin qilmaydi — nima haqida kanal ekanini so'raydi.
3. **Yoqqanini bosasiz** — AI qolgan narsalarni so'raydi (uzunlik, shakl, qaysi
   personajlar, harakatlanadimi), tugmalar ko'rinishida. Hammasi ma'lum bo'lgach
   videoni o'zi boshlaydi.

Muhim qoida: **hammasini bilmaguncha video boshlanmaydi.** Yarim to'ldirilgan
so'rov savolga aylanadi, ishga emas — chunki noto'g'ri taxmin qilingan video ham
pul turadi.

Suhbat va kanallar bazada saqlanadi, ya'ni telefonda boshlab kompyuterda davom
ettirasiz.

## Reja — oldindan aytib qo'yish

**Reja** bo'limida videoni *qachon chiqishi* bilan aytasiz. Keyin ilova o'zi:

1. Belgilangan vaqtdan **oldin** tayyorlashni boshlaydi (necha soat oldin —
   o'zingiz tanlaysiz). Ellik sahnali video besh daqiqalik ish emas, shuning uchun
   soat to'qqizda kutib turmaydi.
2. Video tayyor bo'lgach **to'xtaydi va sizni kutadi**. Ko'rib chiqasiz, kerak
   bo'lsa tahrirlaysiz, keyin **«Tasdiqlash va joylash»**.
3. YouTube'ga *private* holda chiqadi va **YouTube uni belgilangan paytda o'zi
   ochadi**. Ya'ni erta tasdiqlasangiz ham vaqtida chiqadi, ilova o'sha payt
   ishlab turishi shart emas.

Tasdiqlash majburiy emas — «men ko'rib tasdiqlayman» belgisini olib tashlasangiz
ilova o'zi joylaydi. Lekin standart holat tasdiqlash bilan: ko'rmasdan chiqqan
videoni ko'rgan odamdan qaytarib olib bo'lmaydi.

Reja bekor qilinadi, vaqti o'zgartiriladi, «Hozir boshlash» bilan navbatsiz
tayyorlanadi. Rejalar bazada — deploy qilinsa yo'qolmaydi.

### Batch — vaqt ko'p bo'lsa yarim narx

Rasmlarni Gemini'ning **Batch API**si orqali tayyorlash mumkin: **narxi yarmi**,
javobi sekinroq.

Buni siz tanlaysiz — reja formasida ham, keyin kartochkadagi **«Rasmlar»**
ro'yxatidan ham:

| Tanlov | Nima bo'ladi |
|---|---|
| **Avtomatik** | Chiqishiga 6 soatdan ko'p qolgan bo'lsa batch, aks holda oddiy |
| **Batch — yarim narx** | Har doim batch, vaqtiga qaramay |
| **Oddiy — tez** | Batch umuman ishlatilmaydi |

Kartochkada qaysi yo'l tanlanganini yorliq ko'rsatib turadi.

Bu hech qachon xavf tug'dirmaydi:

- Batch rad etilsa — oddiy yo'l bilan tayyorlanadi.
- Batch yarmini qaytarsa — qolgani oddiy yo'l bilan.
- Batch belgilangan vaqtda javob bermasa — kutish to'xtatiladi va qolgani oddiy
  yo'l bilan. Qancha kutish rejaning o'zidan kelib chiqadi: chiqishiga qancha
  qolganining yarmi, lekin 3 soatdan ko'p emas.

Har holatda jurnalda nima bo'lgani yozib qo'yiladi. Ya'ni **rasmsiz video
chiqmaydi** — faqat narxi farq qiladi.

## YouTube'ga joylash

Tayyor video kartochkasida **«YouTube'ga joylash»** tugmasi. Sarlavha, tavsif va
teglar ilova o'zi yozgan publishing pack'dan to'ldirilgan bo'ladi — nimasi
yoqmasa o'zgartirasiz.

**Vaqt belgilasangiz** video YouTube'ga *private* holda chiqadi va YouTube uni
o'sha paytda o'zi ochadi. Ya'ni ilova o'sha payt ishlab turishi shart emas.

### Ulash

Bu API kalit emas — ilova **sizning kanalingiz nomidan** ish qiladi, ya'ni sizning
ruxsatingiz kerak (OAuth).

1. [Google Cloud Console](https://console.cloud.google.com) → loyiha yarating →
   **APIs & Services** → **Library** → **YouTube Data API v3** ni yoqing.
2. **Credentials** → **Create credentials** → **OAuth client ID** →
   *Web application*.
3. **Authorized redirect URIs** ga ilovadagi manzilni qo'ying. Uni **Kutubxona →
   YouTube** bo'limi ko'rsatib turadi, nusxalash tugmasi bilan.
4. Client ID va secret'ni Railway'ga qo'ying:

```
YOUTUBE_CLIENT_ID=...apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=...
```

`PUBLIC_URL` Railway'da o'zi topiladi (`RAILWAY_PUBLIC_DOMAIN` dan); boshqa joyda
`PUBLIC_URL=https://sizning-domeningiz` deb yozing.

5. **Kutubxona → YouTube → «Kanalni ulash»**. Bir marta ruxsat berasiz, keyin
   ilova o'zi joylaydi. Ruxsat bazada saqlanadi — deploy qilinsa yo'qolmaydi.

### Bilib qo'yish kerak

| Nima | Nega |
|---|---|
| Kuniga ~6 ta video | Bitta yuklash YouTube kvotasidan 1600 birlik oladi, kunlik limit 10 000. Tugasa aniq xabar chiqadi, ertaga tiklanadi. |
| Videolar `private` bo'lib chiqishi mumkin | Google tomonidan tasdiqlanmagan ilovalar faqat private yuklaydi. Bu Google qoidasi — ilova nima so'ragani va nima olganini aytib beradi. |

## AI skills

| Skill | Vazifasi |
|---|---|
| `director` | Mavzuni sahna-ba-sahna skriptga aylantiradi (yoki tayyor ovozni sahnalarga bo'ladi) |
| `translator` | Matnni boshqa tilga, aytilish uzunligini saqlagan holda o'giradi |
| `imagesmith` | Har bir sahnani rasm generatori tushunadigan promptga aylantiradi |
| `choreographer` | Multfilmni sahnalashtiradi: kim ekranda, qayerda, qanday harakat qiladi |
| `subtitler` | Subtitr qatorlarini qayerda bo'lishni hal qiladi |
| `publisher` | YouTube sarlavha, tavsif, teglar, chapterlar, thumbnail prompt |
| `strategist` | Kanallaringizni o'qiydi, g'oya beradi va videoni o'zi boshlaydi |
| `shorts` | Uzun videoning ichidan alohida ishlaydigan Shorts bo'laklarini topadi |
| `rewriter` | Matnni siz aytgan izohga qarab qayta yozadi — hech narsa yaratilishidan oldin |

Reja va joylashtirish skill emas — `planner` moduli: u vaqtni kuzatib turadi va
har bir rejani bir holatdan ikkinchisiga o'tkazadi.

## Provayderlar

Hammasi adapter — `.env` orqali almashtirasiz, kod o'zgarmaydi.

| Tur | Variantlar |
|---|---|
| Skript | `gemini` (default), `anthropic` (Claude) |
| Rasm | `gemini`, `fal` (Flux Kontext), `openai` (gpt-image-1) |
| Ovoz | `gemini`, `elevenlabs`, `openai`, yoki **o'z audiongizni yuklash** |
| Til | en, uz, ru, tr, es, ar, hi, de, fr, **ko** |
| Subtitr vaqti | ElevenLabs timestamps → Whisper → proporsional taxmin |
| Saqlash | Lokal disk (default) yoki Supabase Storage |

Suhbat va skrinshot o'qish skript provayderining o'zidan ishlaydi — Gemini ham,
Claude ham rasmni ko'radi, alohida kalit kerak emas.

Qaysi kalit bor-yo'qligi UI tepasidagi yorliqlarda ko'rinadi; kalitsiz provayder
tanlanmaydi va job yaratilganda aniq xabar beriladi.

## API kalitlari — bir nechta, ilovaning o'zida

Limit kalitga sotiladi: bitta Gemini kaliti daqiqasiga o'nta satr o'qiydi, ya'ni
ellik sahnali video vaqtining ko'pini **ish emas, kutish** bilan o'tkazadi. O'nta
kalit — o'nta limit.

**Kutubxona → API kalitlari** da har provayderga xohlagancha kalit qo'shasiz.
Deploy qilish, env o'zgartirish shart emas: qo'shdingiz — shu zahoti ishlatiladi.

- **Navbat bilan** ishlatiladi (round-robin), ya'ni birinchi kalit hammasini
  yutib, qolgan to'qqiztasi bo'sh turmaydi.
- Kalit limitga urilsa, ilova **kutmaydi** — o'sha zahoti keyingi kalitga o'tadi.
  Rad etgan kalit belgilanadi va limiti tugaguncha chetlab o'tiladi (daqiqalik
  limit ~1 daqiqa, kunlik kvota 30 daqiqa, noto'g'ri kalit 1 soat).
- Har kalitning yonida **necha marta ishlatilgani, nechta xatosi, qancha dam
  olishi va oxirgi xato sababi** yoziladi. «Tekshirish» tugmasi kalitni
  provayderning o'ziga urib ko'radi — video ishga tushmasdan avval bilinadi.
- Kalitni **o'chirib-yoqib** qo'yish mumkin; o'chirilgani navbatda qatnashmaydi.
- Kalit **hech qachon qaytarib ko'rsatilmaydi** — qo'shgandan keyin faqat nomi,
  holati va statistikasi ko'rinadi.
- Kalitning **shakli tekshirilmaydi**: Google ham `AIza…`, ham `AQ.…` ko'rinishidagi
  kalitlar beradi, uzunliklari ham har xil — shuning uchun ilova hech qanday
  "shunday bo'lishi kerak" qoidasi qo'ymaydi. Kalit to'g'ri yoki noto'g'riligini
  provayderning o'zi aytadi ("Tekshirish" tugmasi). Telefondan nusxa olganda
  qo'shilib ketadigan qator uzilishi, qo'shtirnoq va bo'shliqlar avtomatik
  tozalanadi.
- Env o'zgaruvchilari (`GEMINI_API_KEY` va h.k.) ishlashda davom etadi: ilovada
  kalit bo'lmasa, o'shalar ishlatiladi.

Daqiqalik cheklov (`TTS_RATE_LIMIT`) kalit soniga ko'paytiriladi — uchta kalit
uchta limit degani.

## Modellar

Standart modellar: skript `gemini-3.1-pro-preview`, rasm
**`gemini-2.5-flash-image`**, ovoz `gemini-3.1-flash-tts-preview`. Ovoz uchun
zaxira ham bor (`gemini-2.5-flash-preview-tts`) — preview model kalitingizga
berilmagan bo'lsa, video to'xtamasdan o'shanga tushadi.

**Kutubxona → Modellar** da har bosqichni **ro'yxatdan tanlaysiz** — deploy
qilish shart emas, tanlov bazaga yoziladi. Ro'yxat bo'lim ochilishi bilan
**provayderdan sizning kalitingiz uchun** olinadi, ya'ni bugun chiqqan model
ham darhol menyuda bo'ladi. Har bosqich faqat o'ziga mos modellarni ko'rsatadi
(ovoz joyida rasm modeli chiqmaydi). Ro'yxatda yo'q nomni «boshqa nom
yozaman…» orqali qo'lda kiritasiz. «Standart» ni tanlasangiz env'dagi qiymatga
qaytadi.

**Kutubxona → Holat** har doim aynan qaysi model chaqirilayotganini ko'rsatadi.

### Koreys va boshqa "zich" yozuvlar

Koreys tili qo'shildi (`ko`). Ikkita narsa alohida qilingan, chunki tilni
ro'yxatga qo'shishning o'zi yetmaydi:

- **Shrift.** DejaVu'da hangul yo'q, libass esa yo'q harfni xato deb hisoblamaydi
  — u shunchaki **hech narsa chizmaydi**. Shuning uchun image'ga `fonts-nanum`
  o'rnatiladi va koreys videoning subtitri **NanumGothic**da yoziladi. Boshqa
  tillar avvalgi shriftda qoladi. (`SUBTITLE_FONT_KO` bilan o'zgartirsa bo'ladi.)
- **Qator uzunligi.** Hangul bo'g'ini lotin harfidan ~2 barobar keng. 42 belgili
  qator ingliz tilida sig'adi, koreyschada kadrdan chiqib ketadi — o'lchandi: 30
  belgi allaqachon 1920px kadrning 76%ini egallaydi. Shuning uchun koreys uchun
  belgi chegarasi ~yarmiga tushadi, so'z chegarasi esa **ko'tariladi** (koreys
  so'zlari qisqa). Tahrirlagichdagi subtitr namunasi ham xuddi shu qoidaga
  ko'ra bo'linadi — ya'ni ko'rsatgani bilan render bir xil bo'ladi.

Xuddi shu qoida yapon va xitoy tillari uchun ham tayyor (`DENSE_SCRIPTS`), ular
ro'yxatga qo'shilsa shriftini ham qo'shish kerak bo'ladi (`fonts-noto-cjk`).

## Ovoz

Ovozni ro'yxatdan tanlaysiz, har birining yonida tembri yozilgan, **▶ tugmasi
bilan namunasini eshitasiz** — namuna siz tanlagan tilda o'qiladi va keshlanadi,
ikkinchi marta bepul.

| Provayder | Ovozlar qayerdan |
|---|---|
| Gemini | 30 ta tayyor ovoz (Puck, Kore, Fenrir, Aoede…) |
| OpenAI | 11 ta ovoz (alloy, onyx, nova…) |
| ElevenLabs | **kalitingizdagi barcha ovozlar**, `GET /v1/voices` orqali |

ElevenLabs ulasangiz o'z akkountingizdagi va Voice Library'dan qo'shgan barcha
ovozlaringiz ro'yxatda chiqadi — Voice ID ni qo'lda ko'chirish shart emas.
Ro'yxat `/v2/voices` dan sahifalab olinadi (Voice Library'dan qo'shilganlar faqat
shu yerda ko'rinadi); v2 ishlamasa `/v1/voices` ga tushadi. Ro'yxat bo'sh chiqsa
yoki kalit rad etilsa, aniq sabab yoziladi — 401, 403 (ruxsat yetmaydi) va 429
har biri o'z xabari bilan.
Namunasi ham ElevenLabs'ning o'z sample'idan olinadi, ya'ni **kredit
sarflanmaydi**. API kalitiga `text_to_speech` va `voices_read` ruxsati kerak.

### Har qahramonga o'z ovozi

**Kutubxona → Herolar** da har bir qahramon kartochkasi ostida ovoz tugmasi bor.
Ovoz bersangiz — o'sha qahramon **o'z gaplarini o'zi aytadi**: Direktor unga
dialog yozadi va ovoz bosqichi o'sha qatorni shu ovozda yozadi. Ovoz
bermasangiz uni diktor o'qiydi, ya'ni hech narsa o'zgarmaydi.

Tahrirlashda har sahnada **«Kim gapiradi»** degan tanlov chiqadi — Diktor yoki
ovozi bor qahramonlardan biri. Ovozsiz qahramonlar u yerda ko'rinmaydi: ularni
baribir diktor o'qigan bo'lardi, ya'ni hech narsani o'zgartirmaydigan tanlov
bo'lardi.

Bir necha ovoz bo'lsa ham so'rovlar baribir guruhlanadi: har ovoz o'z
qatorlarini bitta so'rovda o'qiydi. To'rt qahramonli multfilm — sahna soniga
qarab emas, **to'rtta so'rov**.

### Ovozni qayta yozish — bitta, oraliq yoki hammasi

Sahna panelida **«Ovozni qayta yozish»** bosganda **qaysi sahnalar** degan tanlov
chiqadi:

- **Faqat shu sahna** — bittasini tuzatish uchun.
- **Oraliq** — masalan 52 dan 78 gacha. Faqat o'shalari qayta yoziladi.
- **Barchasi** — butun video.

Oraliq nima uchun kerak: 78 sahnaning 51 tasi yaxshi chiqib, qolgani boshqa
ovozda bo'lsa, hammasini qayta yozish **yaxshi chiqqan 51 tasi uchun ikkinchi
marta to'lash** demakdir. Oraliq tanlasangiz faqat 27 tasiga to'lanadi.

Ovozni almashtirsangiz u butun videoga tegishli bo'ladi: oraliqdan tashqaridagi
sahnalar **belgilanadi**, ya'ni keyingi render'da qayta yoziladi — hozir emas.

### O'z ovozingizni yozish

Sahna panelida **🎙 O'zim aytaman** tugmasi. Mikrofonga yozasiz, eshitib
ko'rasiz, yoqmasa qaytadan yozasiz. «Ishlatish» bosilgach o'sha sahnaning ovozi
sizniki bo'ladi.

Muhimi: vaqtlar **yozuvingizdan** qayta o'lchanadi. Ya'ni sahna uzunligi,
subtitr so'zlari va undan keyingi hamma sahnalar sizning aytganingizga moslanadi
— hayvon ovozi, qichqiriq, sun'iy ovoz umuman o'qiy olmaydigan narsalar uchun
aynan shu kerak.

## Multfilm rejimi

Yaratish sahifasida ikkita narsa bor:

- **«Nima bo'lsin»** — sahnada nima sodir bo'lishini o'z so'zingiz bilan
  yozasiz. Mavzu — video *nima haqida*; bu esa tomoshabin *nimani ko'radi*.
  Masalan: «Tarzan daraxt ostida turadi, chapdan dinozavr keladi, Tarzan o'ngga
  qochadi».
- **«Multfilm»** katagi — qahramonlar fondan kesilib, sahna ustida
  harakatlanadi.

Yoqsangiz nima bo'ladi:

1. Har bir qahramon **har bir holati uchun bir marta** chiziladi: turgan
   holati, qo'rqqan holati, yugurayotgan holati. Bir xil holat necha sahnada
   takrorlansa ham bitta rasm. Ya'ni ellik sahnali multfilm sahna soniga emas,
   **holatlar soniga** qarab to'lanadi.
2. Fon kesib tashlanadi. Kesish rangga qarab emas — **kadr chetidan ichkariga
   qarab** ishlaydi: fon gradient bo'lsa ham, teksturali bo'lsa ham, oq studiya
   foni bo'lsa ham ketadi. Qahramonning ichidagi fon rangidagi kiyim esa
   tegilmaydi, chunki u chetga ulanmagan.
3. **Xoreograf** agenti har sahnani sahnalashtiradi: kim ekranda, qanday
   holatda, qayerda turadi, qanday harakat qiladi, qachon kiradi.
4. Sahnaning rasm prompti **fon promptiga** aylanadi — qahramonlarsiz, va
   qahramon suratlari ham fonga **berilmaydi**. Ikkovi ham shart: prompt tozalab
   qo'yilib, surat berilsa, model o'sha suratni fonga chizib qo'yadi.
5. Aktyorlar qatlam sifatida sahna ustiga qo'yiladi va **fon qimirlamaydi** —
   fon turadi, qahramon ko'chadi.

**Gaplashishi uchun** qahramonlarga ovoz bering (Kutubxona → Herolar). Ovozi
bor qahramon bo'lsa, Direktor unga **dialog** yozadi — «Tarzan ikkilandi» emas,
Tarzanning o'z gapi. Hech kimga ovoz berilmagan bo'lsa, ilova buni boshida
aytadi va hammasini diktor o'qiydi.

Kamera harakati ham o'chiriladi: yurayotgan qahramon ostida sekin zoom bo'lsa,
u yerga emas, shishaga sirg'alayotganday ko'rinadi.

Agar biror qahramonning foni kesilmasa — aniq aytiladi, va qolgani oddiy
rejimda davom etadi. Tahrirlashda har bir aktyorni qo'lda ham surib, harakatini
almashtirib chiqishingiz mumkin.

## Ma'lumotlar qayerda

Ikki xil narsa saqlanadi va ikkovi ikki joyda turadi:

| Nima | Qayerda | Nimaga |
|---|---|---|
| Herolar, musiqa, effektlar, qatlam rasmlari, aktyorlar, brend, **loyihalar** | Baza (SQLite yoki Postgres) | Kichkina yozuvlar, tez o'qiladi |
| Sahna rasmlari, ovoz bo'laklari, tayyor MP4 | Supabase Storage, u yo'q bo'lsa baza | Kattaroq fayllar |

Standart holatda ikkovi ham konteyner ichida — ya'ni **deploy qilinsa
o'chadi**.

`DATABASE_URL` qo'ysangiz **hammasi saqlanadi**: yozuvlar ham, sahna rasmlari
ham, ovoz bo'laklari ham. Rasm va ovozlar `media` jadvaliga tushadi va loyihani
o'chirmaguningizcha turadi.

Yuqorida `STORAGE_BACKEND=supabase` ham qo'shsangiz, kattaroq fayllar bazaga
emas **Storage bucket'ga** chiqadi — bu tavsiya etiladi, chunki baza hajmi
arzonroq ishlatiladi va tayyor videoga to'g'ridan-to'g'ri havola beriladi.

---

## Supabase'ni ulash

Ikki qism bor va **ikkalasini ham** qo'yish kerak: baza (yozuvlar uchun) va
Storage (rasm/ovoz/video uchun). Bittasi qolib ketsa yarmi saqlanadi.

### 1. Loyiha yarating

[supabase.com](https://supabase.com) → **New project**. Parolni yozib qo'ying —
u ulanish satrida kerak bo'ladi.

### 2. Baza ulanish satrini oling

**Project Settings → Database → Connection string → URI**, u yerda
**Session pooler** ni tanlang. Shunday ko'rinadi:

```
postgresql://postgres.abcdefghijklm:PAROL@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
```

> **Nega pooler?** «Direct connection» (`db.xxx.supabase.co:5432`) endi faqat
> IPv6 orqali ishlaydi va Railway'dan unga yetib bo'lmaydi. Logda shunday
> chiqadi:
>
> ```
> connection to server at "2406:da1a:...", port 5432 failed: Network is unreachable
> ```
>
> Manzil `2406:` yoki `2a05:` bilan boshlansa — bu IPv6, ya'ni pooler emas,
> to'g'ridan-to'g'ri ulanish. Pooler IPv4'da ham ishlaydi.
>
> Parolda `@ : / ? # &` kabi belgi bo'lsa, uni URL-kodlash kerak (`@` → `%40`).
> Eng osoni — parolni faqat harf va raqamdan qilish.

### 3. Storage kalitlarini oling

**Project Settings → API**:

- **Project URL** → `SUPABASE_URL`
- **service_role** kaliti (`anon` emas!) → `SUPABASE_SERVICE_KEY`

Bucket'ni qo'lda yaratish shart emas — ilova ishga tushganda o'zi yaratadi, va
keyin ham bucket topilmasa birinchi yuklashda qayta yaratadi.

`anon` kalit qo'yilsa bucket yaratilmaydi (Supabase RLS ruxsat bermaydi). U
holda **Sozlamalar** sahifasida sababi yozib turadi, rasm va ovozlar esa bazaga
saqlanadi — ya'ni hech narsa yo'qolmaydi, lekin tayyor video faqat shu
konteynerda qoladi. Tuzatish: `service_role` kalitini qo'ying, yoki Supabase →
**Storage** → **New bucket** da `videos` nomli **public** bucket yarating.

### 4. Railway'ga to'rtta o'zgaruvchi qo'ying

```
DATABASE_URL=postgresql://postgres.abcdefghijklm:PAROL@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
STORAGE_BACKEND=supabase
SUPABASE_URL=https://abcdefghijklm.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOi...
```

### 5. Deploy qiling va tekshiring

Jadvallarni qo'lda yaratish **shart emas** — ilova birinchi ulanganda o'zi
yaratadi. Qanday ko'rinishini oldindan ko'rmoqchi bo'lsangiz yoki qo'lda
tayyorlab qo'ymoqchi bo'lsangiz: [`supabase/schema.sql`](supabase/schema.sql)
ni Supabase → **SQL Editor** ga qo'yib «Run» bosing. Ikkinchi marta bossangiz
ham hech narsa buzilmaydi.

Deploy tugagach ilovadagi **Sozlamalar** ni oching. Ko'rishingiz kerak:

```
baza — postgres                    bor
deploydan keyin saqlanadi          bor
```

Ikkinchi qator «yo'q» bo'lsa — nimasi yetishmayotgani o'sha yerning o'zida
yozib turadi.

### Jadvallar va ular nimani saqlaydi

| Jadval | Ichida |
|---|---|
| `heroes` | Siz yuklagan personaj suratlari (qayta yaratib bo'lmaydigan yagona narsa) |
| `music` | Fon musiqasi va tovush effektlari |
| `assets` | Stikerlar, logotip, kesib olingan aktyorlar, yozib olingan ovozlar |
| `settings` | Brend to'plami, tanlangan modellar va ovozlar |
| `jobs` | Loyihalar: ssenariy, sahnalar, ovoz uzunliklari, tayyor video havolasi |
| `profiles` | Kanal skrinshotlari va AI ulardan o'qib olgani |

Storage bucket'ida (`videos`) esa har bir loyihaning sahna rasmlari, ovoz
bo'laklari va tayyor MP4 si `<job_id>/...` ko'rinishida turadi.

### Avvalgi ishlaringiz yo'qolmaydi

Ilova birinchi marta Supabase bilan ishga tushganda konteynerdagi SQLite
bazasida nima bo'lsa — herolar, musiqa, sozlamalar va loyihalar — hammasini
Postgres'ga ko'chiradi. Ikki marta ishga tushsa ham nusxalanmaydi.

### Yarim tayyor loyiha endi yo'qolmaydi

Sahna rasmlari va ovoz bo'laklari tayyor bo'lgan sayin Storage'ga chiqariladi.
Konteyner 30-sahnada o'lsa ham, qaytib kelganingizda o'sha 30 ta rasm joyida
turadi: **Render** bosasiz, ilova qolganidan davom ettiradi — allaqachon
to'langan narsa uchun ikkinchi marta to'lanmaydi.

## Animatsiya

Har bir rasm sekin zoom qiladi yoki suriladi (Ken Burns), sahnalar orasida
cross-fade bo'ladi. Ikkita narsa buni "slayd-shou" emas, "kamera" qilib
ko'rsatadi:

1. Rasm avval kadrdan **2 barobar katta** qilib olinadi — shuning uchun zoom
   paytida piksel cho'zilmaydi, aksincha kichraytiriladi va tiniq qoladi.
2. Harakat **chiziqli emas, easing bilan** (smoothstep). Boshida va oxirida
   deyarli qimirlamaydi, o'rtada tezlashadi — o'rtadagi tezlik chetlarnikidan
   ~45 barobar katta. Chiziqli harakatdagi "keskin boshlanib keskin to'xtash"
   yo'qoladi.

Har bir sahna uchun **15 xil harakat**: zoom in/out, to'rt tomonga surilish,
zoom+surilish kombinatsiyalari, diagonal, nafas (`pulse`), tebranish (`sway`) va
harakatsiz. Ustiga **harakat kuchi** (0.3×…1.8×) — bir xil harakatni sezilar-
sezilmas siljishdan haqiqiy push'gacha sozlaydi.

## Ilova tuzilishi

Yuqorida to'rtta bo'lim: **Yaratish · Tahrirlash · Tayyor · Kutubxona**.
Pastda esa **dock** — o'sha bo'limning asboblari, barmoq yetadigan joyda.

**Yaratish** ekranida bitta savol, bitta maydon va bitta tugma. Sozlamalar
pastdagi dock'da: Format, Uzunlik, Uslub, Herolar, Ovoz, Musiqa, Subtitr,
Boshqa. Har biri pastdan chiqadigan panel ochadi. Maydon ostidagi qatorda esa
hozirgi tanlovlaringiz turadi — bosib o'sha panelni ochasiz. Hech narsa
pastga scroll bo'lib ketmaydi.

**Musiqani va heroni to'g'ridan-to'g'ri o'sha panelda qurilmangizdan
yuklaysiz** — Kutubxonaga borib qaytish shart emas.

**Tahrirlash** bo'limida yuqorida loyihalaringiz lentasi, birini bossangiz
ostida studio ochiladi. Dock'da: Eshitish, Matn, Rasm, Sahna, Qatlam, Subtitr,
Render.

## Studio — tahrirlagich

Qoralama tayyor bo'lgach oddiy ro'yxat emas, **studio** ochiladi: chapda kadr
qanday eksport bo'lsa shundayligicha, o'ngda sozlamalar, pastda sahnalar lentasi.

**Qatlamlar.** Kadr ustiga matn va rasm qo'yasiz — **sichqoncha bilan sudrab
joylashtirasiz**, burchagidan tortib kattalashtirasiz. Har bir qatlamning rangi,
orqa foni, burilishi, shaffofligi, vaqti (sahnaning qaysi soniyasidan qaysigacha)
va kirish animatsiyasi bor. Bir sahnada 8 tagacha qatlam.

Matn qatlamlari subtitr bilan bitta ASS faylida chiziladi — qo'shimcha kodlash
yo'q, sifat yo'qolmaydi. Rasm qatlamlari o'sha sahnaning ffmpeg zanjiriga
qo'shiladi. Qatlam buzilsa render to'xtamaydi: o'sha sahna qatlamsiz chiqadi.

**Subtitr ko'rinishi.** 8 ta tayyor uslub (Qalin, Toza, Karaoke, Neon, Fonli,
Pop, So'zma-so'z, Nozik) va ularning ostida barcha tugmalar: matn rangi,
aytilayotgan so'z rangi, kontur/soya/fon, fon rangi va shaffofligi, o'lcham,
chetdan masofa, joylashuvi (yuqori/o'rta/past), BOSH HARFLAR, kirish
animatsiyasi. **Kadrdagi namuna real vaqtda o'zgaradi** — render kutish shart
emas: namuna aynan renderer ishlatadigan shrift o'lchamidan hisoblanadi.

**Eshitish.** Sahnaning o'z ovozini o'ynatadi va kadr shu soat bo'yicha yuradi:
subtitr so'zma-so'z yonadi, qatlamlar o'z vaqtida chiqib-yo'qoladi. Qaysi so'zga
qaysi yozuv tushishini render kutmasdan ko'rasiz.

**Sahna jarrohligi.** Tanlangan kadrning pastida **‹ ›** tugmalari chiqadi —
sahnani chapga yoki o'ngga surasiz. Kompyuterda sudrab ham bo'ladi; telefonda
esa gorizontal sudrash lentani aylantirish uchun ishlatiladi, shuning uchun
tugmalar ishonchliroq.

**Sahna bir butun ko'chadi**: matni, ovozi, so'z vaqtlari, rasmi, qatlamlari va
tovush effekti — hammasi birga. Har bir sahnaning fayllari o'ziniki bo'lgan
o'zgarmas nom bilan saqlanadi, shuning uchun tartib o'zgarganda rasm va ovoz
aralashib ketmaydi. Keraksizini o'chirasiz, yangisini qo'shasiz — matnini
yozasiz, qolganini AI qiladi (prompt, ovoz, rasm).

**Tovush effektlari.** Sahnaga qisqa tovush (whoosh, ding) qo'yiladi — balandligi
va kechikishi sozlanadi. Ovoz va musiqa bilan bir mikserda birlashadi.

O'zgarishlar **o'zi saqlanadi** (yozganingizdan ~0.8 soniya keyin), tepada
"saqlandi" deb turadi.

## Brend

Kutubxonada bir marta sozlaysiz: logotip (har sahnaga qo'yiladi), brend rangi,
doimiy vizual uslub, ohang, ovoz va fon musiqasi. Har yangi video shundan
boshlanadi — lekin bu qulf emas, formada nima yozsangiz o'sha g'olib.

**Hook.** "Hook matnini birinchi 3 soniyaga qo'yish" belgilansa, Director
yozgan hook jumlasi kadrga brend rangidagi fonda chiqadi. Shorts va TikTok
uchun eng muhim uch soniya.

## Tarjima

Uch xil yo'l bor.

**Shu videoning o'zini boshqa tilga o'tkazish.** Tahrirlash bo'limida
**«Ovozni qayta yozish»** oynasida endi **til** ham tanlanadi. Tanlasangiz matn
o'sha tilga o'giriladi va **butun video** qayta o'qiladi — oraliq tanlab
bo'lmaydi, chunki o'rtasida tili o'zgaradigan video nuqson. **Subtitr ham o'sha
tilda** bo'ladi: u matndan yoziladi, ya'ni alohida qadam kerak emas.

Bu nusxa olmaydi — o'sha loyihaning o'zi o'zgaradi. Ikkala til ham kerak bo'lsa
quyidagi «Boshqa tilga» dan foydalaning.

**Bitta videodan bir necha til.** «Tayyor» bo'limida **Boshqa tilga** tugmasi:
matn tarjima qilinib, tanlagan ovozingizda qaytadan o'qiladi. Rasmlar,
qatlamlar, kamera harakatlari va subtitr uslubi o'zgarmaydi — faqat ovoz va
matn. Sahna uzunliklari yangi ovozga qarab o'zi qayta hisoblanadi. Bitta
videodan 2-3 tilga shu tarzda chiqarasiz.

**Tayyor videoni dublyaj qilish.** «Yaratish → Dublyaj» bo'limiga o'z
videongizni (yoki boshqa joydan olingan videoni) yuklaysiz:

```
video ─▶ ovozni ajratish ─▶ tinglash (transkript + vaqtlar) ─▶ tarjima
                                                                  │
   MP4 ◀── rasm o'zgarmaydi, faqat ovoz almashadi ◀── ovozlash ◀──┘
```

Tarjima **original vaqtga sig'diriladi**: har bir jumla o'z o'rnida qoladi,
kerak bo'lsa 0.75×–1.45× oralig'ida sekinlashtiriladi yoki tezlashtiriladi
(bundan tashqarisi odam ovoziga o'xshamay qoladi), qolgan joyi jimlik bilan
to'ldiriladi. Rasm umuman qayta kodlanmaydi — shuning uchun 10 daqiqalik video
bir necha soniyada dublyaj bo'ladi.

Original ovozni ostida past darajada qoldirish mumkin — musiqa va effektlar
eshitilib turadi.

Tinglash uchun **Gemini yoki OpenAI kaliti** kerak (Gemini audio ham tushunadi,
shuning uchun bitta kalit yetadi).

## Render tezligi

«Boshqa» panelidan tanlanadi:

| | Nima o'zgaradi |
|---|---|
| **Tez** | ~1.7× tezroq. Kuchli zoom paytida rasm biroz yumshoqroq |
| **Muvozanat** | tavsiya etiladi |
| **Sifat** | eng tiniq, sezilarli sekinroq |

Sahna kliplari endi ketma-ket emas, bir vaqtda renderlanadi. Eng katta ta'sir
qiluvchi narsa — rasmni kadrdan necha barobar katta olish (zoom sifatini
belgilaydi va ishlash vaqtini kvadratik oshiradi), shuning uchun asosiy farq
shundan chiqadi.

## Muqova va boshqa formatlar

**Muqova.** Tayyor video ostidagi «Muqova yaratish» uchta variant chizadi — yaqin
plan, umumiy plan va hissiy cho'qqi. Ichida yozuv bo'lmaydi (sarlavhani o'zingiz
qo'yasiz, generator uni baribir xato yozadi).

**Boshqa formatga.** Bitta skriptdan 16:9, 9:16, 1:1 va 4:5 chiqadi. Ovoz,
vaqtlar, subtitr va qatlamlar o'zgarmaydi — ular formatga bog'liq emas. Rasmlar
esa bog'liq: eskilarini ishlatsangiz o'rtasidan kesiladi (tez), qayta
yaratsangiz yangi kadr uchun chiziladi (sifatli).

## Uzun videodan Shorts

Uzun video tayyor bo'lgach, «Tayyor» bo'limida **«Shortsga bo'lish»** chiqadi.

**AI topib beradi.** «AI mos joylarni topsin» bosilsa, videoning ichidan o'zicha
tugallangan bo'laklar tanlanadi. Har taklif yonida: **nechchi soniya**, nechta
sahna, taklif qilingan **sarlavha**, ekranga chiqadigan **hook** va *nega* aynan
shu bo'lak alohida ishlashi. Uzunlik taxmin emas — yozilgan ovozdan hisoblanadi,
ya'ni kesishdan **oldin** bilasiz.

**Hammasini bir bosishda.** «Hammasini kesib ber» — videoda nechta mustaqil
bo'lak bo'lsa, shuncha Short. Beshtami, o'ntami — oldindan aytish shart emas,
model videoda tugagan joyda to'xtaydi. Har biri alohida loyiha bo'lib navbatga
qo'yiladi, ya'ni bir vaqtda emas, ketma-ket render bo'ladi. Vertikal kadr uchun
rasmlarni qayta chizish bu yerda **standart holatda yoqiq**: hech kim qarab
turmagan yo'lda, chetda qolgan narsani sezadigan odam yo'q.

**O'zingiz ham kesasiz.** Qaysi sahnadan qaysi sahnagacha — tanlaysiz, uzunligi
darrov ko'rinadi (60 soniyadan oshsa ogohlantiradi). Teskari tanlansa o'zi
to'g'rilaydi.

Short **tayyor MP4 dan kesilmaydi** — u alohida loyiha bo'lib tug'iladi. Rasm,
ovoz, qatlamlar allaqachon bor, shuning uchun vertikal kadr **qayta teriladi**,
subtitr esa tor kadr uchun qaytadan bo'linadi — kengini qirqib qo'yilmaydi.
Demak Short ham oddiy loyiha: tahrirlash, muqova qo'yish va YouTube'ga joylash
mumkin. Ovoz va rasm qayta yaratilmaydi — ular allaqachon to'langan.

## Subtitrni yuklab olish

Har bir tayyor video ostida **Subtitr: .srt · .vtt · matn**:

| Format | Nimaga |
|---|---|
| `.srt` | montaj dasturlari va YouTube'ga subtitr sifatida yuklash |
| `.vtt` | brauzer pleeri (`<track>`), veb-sahifaga qo'yish |
| `matn` | tavsif oynasi, blog, ssenariyni o'qish — vaqtlarsiz, butun jumlalar |

Uchalasi ham **bitta manbadan** olinadi, shuning uchun bir-biridan farq qilmaydi.
Matn varianti sahnalar bo'yicha abzatslarga bo'linadi — ekrandagi uch so'zli
bo'laklar emas, o'qib bo'ladigan matn.

---

## Railway'ga deploy

1. Reponi Railway'da **New Project → Deploy from GitHub repo** qilib ulang.
   `Dockerfile` avtomatik topiladi (ffmpeg va shriftlar shu yerda o'rnatiladi).
2. **Variables** ga eng kamida shuni qo'ying:

   ```
   GEMINI_API_KEY=AIza...
   ```

   Tamom. Qolgan hammasining default qiymati bor.

3. **Hamma narsa saqlanishi uchun** Supabase'ni ulang — usuli yuqoridagi
   «Supabase'ni ulash» bo'limida. Qisqasi: `DATABASE_URL`,
   `STORAGE_BACKEND=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`.

   Supabase'siz ishlatmoqchi bo'lsangiz, hech bo'lmasa **Volume** qo'shib
   `/data` ga mount qiling va `DATA_DIR=/data` deb belgilang. Volume ham,
   Supabase ham bo'lmasa har deploy'da hammasi o'chadi.
4. Deploy tugagach domenni oching — UI shu yerda.

## Lokal ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install -y ffmpeg          # macOS: brew install ffmpeg

export GEMINI_API_KEY=AIza...
uvicorn app.main:app --reload --port 8000
```

`http://localhost:8000` ni oching.

Docker bilan:

```bash
docker build -t ai-video-studio .
docker run --rm -p 8000:8000 -e GEMINI_API_KEY=AIza... -v "$PWD/data:/data" ai-video-studio
```

---

## Ishlatish

1. **Herolar va musiqa** bo'limida personaj rasmini yuklang (ism + qisqa tavsif).
   Tavsif AI ga qiyofani saqlashda yordam beradi.
2. **Yangi video**: mavzu, format, uzunlik, til, herolar → **Video yaratish**.
3. Qoralama tayyor bo'lgach **studio** ochiladi. Pastdagi lentadan sahnani
   tanlaysiz, o'ngdagi uch bo'limda ishlaysiz:
   - **Sahna** — matn (ovoz va subtitr shundan olinadi; o'zgartirsangiz "ovoz
     eskirgan" belgisi chiqadi), rasm prompti, kamera harakati va kuchi, o'tish
     effekti, ekran yozuvi, shu sahnadagi herolar. Shu yerda **Rasmni qayta**,
     **Ovozni qayta** va **O'z rasmim** tugmalari.
   - **Qatlamlar** — kadrga matn/rasm qo'shish, sudrab joylashtirish, rang, fon,
     burilish, vaqt, animatsiya.
   - **Subtitr** — tayyor uslublar va barcha ranglar/o'lchamlar. Namuna kadrda
     darhol ko'rinadi.
4. **Render qilish** — eskirgan sahnalar avtomatik yangilanadi, keyin video
   yig'iladi.
5. Tugagach **Videoni yuklab olish**. Subtitr `.srt` alohida. **Tayyor** bo'limida
   YouTube / TikTok / Instagram uchun sarlavha va hashtaglar nusxalashga tayyor.

Video tayyor bo'lgandan keyin ham tahrirlab, **qayta render** qilishingiz mumkin.

### Tayyor ovoz yuklash

"Ovozni o'zim yuklayman" katagini belgilab audio fayl bering. Ilova uni
transkripsiya qiladi, sahnalarga bo'ladi va rasmlarni aynan siz gapirgan
vaqtlarga moslaydi. Buning uchun `OPENAI_API_KEY` kerak (so'z-vaqtlari uchun).
Bu rejimda sahna matnini tahrirlash faqat subtitrni o'zgartiradi — audio
o'zgarmaydi, shuning uchun sinxron buzilmaydi.

---

## Sozlash

Barcha o'zgaruvchilar `.env.example` da izohi bilan. Eng ko'p ishlatiladiganlari:

| O'zgaruvchi | Default | Nima qiladi |
|---|---|---|
| `SECONDS_PER_SCENE` | `6.5` | Uzunlik nechta sahnaga bo'linishini belgilaydi |
| `TRANSITION_SECONDS` | `0.6` | Sahnalar orasidagi cross-fade |
| `VIDEO_CRF` | `20` | Kichikroq raqam = yaxshiroq sifat, kattaroq fayl |
| `VIDEO_PRESET` | `medium` | `ultrafast`…`veryslow` — render tezligi |
| `MUSIC_VOLUME` | `0.10` | Fon musiqasi (ovoz ostida avtomatik pasayadi) |
| `MAX_CONCURRENT_JOBS` | `1` | Bir vaqtda nechta render — RAM yetsa oshiring |

## API

| Endpoint | Vazifasi |
|---|---|
| `POST /api/jobs` | Yangi video (`auto_render: false` — qoralamada to'xtaydi) |
| `POST /api/jobs/with-audio` | Tayyor ovoz bilan |
| `GET /api/jobs/{id}` | Holat, progress, sahnalar, jurnal |
| `PATCH /api/jobs/{id}` | Subtitr uslubi, musiqa, subtitrni yoqish/o'chirish |
| `PATCH /api/jobs/{id}/scenes/{i}` | Sahnani, qatlamlarini va tovush effektini tahrirlash |
| `POST /api/jobs/{id}/scenes` | Yangi sahna qo'shish (prompt + ovoz + rasm) |
| `DELETE /api/jobs/{id}/scenes/{i}` | Sahnani o'chirish |
| `POST /api/jobs/{id}/scenes/order` | Tartibni o'zgartirish |
| `POST /api/jobs/{id}/scenes/{i}/regenerate` | Sahnani qayta yaratish; ovoz, ovoz oralig'i yoki **til** |
| `POST /api/jobs/{id}/script/revise` | Matnni izohga qarab qayta yozdirish (hali hech narsa yaratilmagan) |
| `POST /api/jobs/{id}/script/approve` | Matnni tasdiqlash — ovoz va rasmlar shundan keyin boshlanadi |
| `POST /api/jobs/{id}/thumbnails` | Uchta muqova varianti |
| `POST /api/jobs/{id}/shorts/suggest` | Uzun videoning ichidan Shorts bo'laklarini topish |
| `POST /api/jobs/{id}/shorts` | Tanlangan sahnalarni alohida Short qilib kesish |
| `POST /api/jobs/{id}/shorts/all` | Videodagi hamma Shortsni topib, hammasini kesish |
| `POST /api/jobs/{id}/repurpose` | Boshqa formatga nusxa olish |
| `POST /api/jobs/{id}/translate` | Boshqa tilga nusxa olish |
| `POST /api/dub` | Tayyor videoni dublyaj qilish |
| `GET/PUT /api/brand` | Brend to'plami |
| `GET/PUT /api/models` | Har bosqich qaysi modelni chaqiradi |
| `GET /api/models/available?provider=` | Provayderdagi mavjud modellar |
| `GET /api/voices?provider=` | Ovozlar ro'yxati |
| `GET /api/voices/preview?provider=&voice_id=&language=` | Ovoz namunasi (keshlanadi) |
| `GET/POST/DELETE /api/assets` | Qatlam rasmlari (stiker, logotip) |
| `GET/POST /api/music?kind=sfx` | Fon musiqasi va tovush effektlari |
| `POST /api/jobs/{id}/render` | Render qilish / qayta render |
| `GET /api/jobs/{id}/download` | MP4 yuklab olish |
| `GET /api/jobs/{id}/subtitles.srt\|.vtt\|.txt` | To'liq subtitr — montaj, brauzer yoki matn uchun |
| `GET /api/health` | Qaysi kalitlar bor, qaysi modellar ishlaydi, formatlar, harakatlar |

## Loyiha tuzilishi

```
app/
├── main.py            FastAPI: API, yuklash, tahrirlash, yuklab olish
├── pipeline.py        Ikki bosqich: draft (qoralama) va render
├── config.py          Barcha env sozlamalari
├── store.py           Baza: herolar, musiqa, aktyorlar, sozlamalar, loyihalar
├── pgstore.py         Postgres (Supabase) ulanishi — SQL bitta joyda, store.py da
├── skills/            AI promptlari + provayder adapteri (llm.py)
├── providers/         Tashqi API adapterlari (images, tts, align, storage)
├── render/            ffmpeg: kenburns, subtitles (ASS), overlays, video
└── static/            UI (vanilla HTML/CSS/JS, build kerak emas)
```

## Eslatmalar

- Bitta sahna rasmi yaratilmasa butun render to'xtamaydi — o'sha sahnaga gradient
  qo'yiladi va ogohlantirish job natijasida ko'rinadi.
- Musiqa mikslashda `sidechaincompress` ishlatiladi (ovoz ostida musiqa pasayadi).
  Agar ffmpeg build'da bu filtr bo'lmasa, render oddiy mikslashga, keyin esa
  musiqasiz rejimga qaytadi.
- 10 daqiqalik video ≈ 90 ta sahna. Render vaqti asosan rasm generatsiyasiga
  ketadi — `IMAGE_CONCURRENCY` ni oshirsangiz tezlashadi (provayder
  rate-limitiga qarang).
- Provayder javob bermay qolsa job muzlab qolmaydi: har bir chaqiruvning
  `TTS_DEADLINE` / `IMAGE_DEADLINE` chegarasi bor. Ovozi chiqmagan sahna
  `needs_voice` bo'lib qoladi va qolgan sahnalar saqlanadi — render bosqichi
  uni qaytadan urinib ko'radi. Rasm chiqmasa gradient qo'yiladi.
- Jarayon 45 soniyadan ortiq jim qolsa progress kartasida "To'xtatish" tugmasi
  chiqadi. To'xtatilgan job tayyor sahnalari bilan tahrirlash bo'limida qoladi.
- **Ovozni keyin ham almashtirsa bo'ladi.** Sahna panelidagi «Ovozni qayta
  yozish» endi provayder va ovozni tanlashni so'raydi, namunasini ▶ bilan shu
  yerda eshitasiz. Ovoz butun videoga tegishli, shuning uchun o'zgartirsangiz
  qolgan sahnalar ham belgilanadi va render paytida qayta yoziladi — yoki
  «Barcha sahnalarni qayta yozish» bilan darhol. `PATCH /api/jobs/{id}` ham
  `voice_id` / `tts_provider` ni qabul qiladi.
- **Telefonni o'chirsangiz ham ishlayveradi.** Render serverda ketadi — brauzer
  faqat holatni ko'rsatadi. Ilovani yopsangiz ham to'xtamaydi; qaytib
  kirganingizda o'zingiz kuzatayotgan job avtomatik ochiladi (u tugab bo'lgan
  bo'lsa ham ko'rsatiladi). Tayyor bo'lganda bildirishnoma keladi — ruxsat
  birinchi render boshlanganda so'raladi.
- Konteyner qayta ishga tushsa ham ish yo'qolmaydi: sahnalar bosqichlar orasida
  diskka yoziladi, shuning uchun render o'rtasida uzilgan job avtomatik davom
  ettiriladi. Ketma-ket 2 marta uzilsa qo'lda render qilish uchun qoldiriladi
  (cheksiz qayta urinish halqasining oldini oladi). Hech narsa saqlanmagan job
  esa avvalgidek xato beradi.
- **ElevenLabs'da sahnalar to'plamlab o'qiladi.** Bir nechta sahna bitta so'rovda
  yuboriladi, keyin yozuv **har qator tugagan aniq harf o'rnida** kesiladi —
  ElevenLabs har bir harf uchun vaqt qaytaradi, shuning uchun taxmin yo'q.
  58 sahnalik video ~5 ta so'rovga tushadi, va diktor gaplar orasida ohangni
  saqlaydi (har nuqtada qaytadan boshlamaydi). Javob biz yuborgan matnga mos
  kelmasa kesilmaydi — o'sha to'plam avvalgidek qator-qator o'qiladi.
  Gemini va OpenAI vaqt qaytarmaydi, shuning uchun ular avvalgidek qoladi.
  `TTS_BATCH=false` bilan o'chiriladi.
- Ovoz so'rovlari **har provayder uchun alohida** cheklanadi: Gemini daqiqasiga
  10 ta (bepul tarif shunday sotiladi), ElevenLabs va OpenAI esa cheklanmaydi —
  ular bir vaqtda nechta so'rov ketishini cheklaydi, nechta boshlanishini emas.
  `TTS_RATE_LIMIT_ELEVENLABS` bilan o'zgartirasiz; `TTS_RATE_LIMIT` esa hammasiga
  birdek qo'llanadi. 429 kelsa xato deb hisoblanmaydi — kutiladi va davom etadi,
  va bu kutish `TTS_DEADLINE` hisobiga kirmaydi.
- Bitta sahna (bitta matn/ovoz) ostida 4 tagacha rasm bo'lishi mumkin — "kadr".
  Yaratishda `Kadr almashishi` ni tanlang: `Jonli` uzun sahnalarni 2-3 rasmga,
  `Tez` esa har ~3 soniyada bo'ladi. Har bir kadrning o'z kamera harakati va
  o'z kirish effekti bor (standart — tez kesish). Tahrirlashda kadrlarni qo'lda
  qo'shish, o'chirish, joyini almashtirish va ekranda turish ulushini o'zgartirish
  mumkin. Kadr promptini o'zgartirsangiz faqat o'sha kadr qayta chiziladi.
  Diqqat: har bir kadr — alohida rasm generatsiyasi.
- Tayyor videoga musiqa keyin ham qo'shsa bo'ladi: "Tayyor" bo'limida
  "Musiqa qo'shish". Faqat tovush qayta mikslanadi, rasm nusxalanadi
  (`-c:v copy`) — shuning uchun bir necha soniya oladi va trekni xohlagancha
  almashtirish mumkin.
- `DATABASE_URL` berilsa **hamma narsa** Postgres'da saqlanadi — herolar,
  musiqa, aktyorlar, sozlamalar va loyihalar. Birinchi ishga tushishda
  konteynerdagi SQLite'da nima bo'lsa, o'sha ko'chiriladi. Baza o'chib turgan
  bo'lsa ham ilova ishga tushaveradi: sabab Sozlamalar sahifasida yoziladi.
