# AI Video Studio

Mavzu nomini yozasiz — ilova skript yozadi, ovoz beradi, har bir sahna uchun rasm
yaratadi, subtitr qo'shadi va tayyor MP4 ni render qiladi.

**Bitta Gemini kaliti yetarli.** Skript, rasm promptlari, subtitr, ovoz va YouTube
matnlari — hammasi o'sha bitta kalitdan ishlaydi.

---

## Nima qiladi

```
Mavzu ─▶ director ─▶ imagesmith ─▶ ovoz ─▶ rasmlar ─▶  ✋ KO'RIB CHIQISH
        (skript)     (promptlar)   (TTS)    (AI)         (tahrirlash)
                                                              │
                     MP4 ◀── render ◀── Ken Burns ◀───────────┘
                             (ffmpeg)   zoom / pan
```

Qoralama tayyor bo'lgach ilova to'xtaydi va sahnalarni ko'rsatadi. Istalgan
sahnaning matnini, rasm promptini, kamera harakatini yoki ekran yozuvini
o'zgartirasiz, kerak bo'lsa faqat o'sha sahnaning rasmini/ovozini qayta
yaratasiz — butun video qaytadan hisoblanmaydi. Keyin **Render** bosasiz.

Ko'rib chiqish kerak bo'lmasa, formada "Render'dan oldin ko'rib chiqaman"
katagini olib tashlang — u holda hammasi bir yo'la tugaydi.

## AI skills

| Skill | Vazifasi |
|---|---|
| `director` | Mavzuni sahna-ba-sahna skriptga aylantiradi (yoki tayyor ovozni sahnalarga bo'ladi) |
| `translator` | Matnni boshqa tilga, aytilish uzunligini saqlagan holda o'giradi |
| `imagesmith` | Har bir sahnani rasm generatori tushunadigan promptga aylantiradi |
| `subtitler` | Subtitr qatorlarini qayerda bo'lishni hal qiladi |
| `publisher` | YouTube sarlavha, tavsif, teglar, chapterlar, thumbnail prompt |

## Provayderlar

Hammasi adapter — `.env` orqali almashtirasiz, kod o'zgarmaydi.

| Tur | Variantlar |
|---|---|
| Skript | `gemini` (default), `anthropic` (Claude) |
| Rasm | `gemini`, `fal` (Flux Kontext), `openai` (gpt-image-1) |
| Ovoz | `gemini`, `elevenlabs`, `openai`, yoki **o'z audiongizni yuklash** |
| Subtitr vaqti | ElevenLabs timestamps → Whisper → proporsional taxmin |
| Saqlash | Lokal disk (default) yoki Supabase Storage |

Qaysi kalit bor-yo'qligi UI tepasidagi yorliqlarda ko'rinadi; kalitsiz provayder
tanlanmaydi va job yaratilganda aniq xabar beriladi.

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
Namunasi ham ElevenLabs'ning o'z sample'idan olinadi, ya'ni **kredit
sarflanmaydi**. API kalitiga `text_to_speech` va `voices_read` ruxsati kerak.

Supabase ulasangiz: `STORAGE_BACKEND=supabase` + `SUPABASE_URL` va
`SUPABASE_SERVICE_KEY`. Bucket avtomatik yaratiladi, render tugagach video
bucket'ga yuklanadi va havolasi o'shanga qarab turadi — konteyner o'chsa ham
video qoladi. Baza (herolar, brend, sozlamalar) baribir `DATA_DIR/app.db` da,
shuning uchun Railway volume'i baribir kerak.

## Ma'lumotlar qayerda

- **Herolar, musiqa, tovush effektlari, qatlam rasmlari va brend** — SQLite
  bazasida, fayllari ham blob sifatida ichida. Alohida papka kerak emas: bitta
  `app.db` — butun kutubxona.
- **Renderlar** — `DATA_DIR/projects/<job>/` ichida. Video tayyor bo'lgach
  yuklab olasiz. Supabase yoqilgan bo'lsa nusxasi bucket'ga ham chiqadi.

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

Ikki xil yo'l bor.

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

---

## Railway'ga deploy

1. Reponi Railway'da **New Project → Deploy from GitHub repo** qilib ulang.
   `Dockerfile` avtomatik topiladi (ffmpeg va shriftlar shu yerda o'rnatiladi).
2. **Variables** ga eng kamida shuni qo'ying:

   ```
   GEMINI_API_KEY=AIza...
   ```

   Tamom. Qolgan hammasining default qiymati bor.

3. **Volume** qo'shing va `/data` ga mount qiling, `DATA_DIR=/data` deb belgilang.
   Bu bo'lmasa herolar bazasi har deploy'da o'chib ketadi.
4. Deploy tugagach domenni oching — UI shu yerda.

Videolar deploy'dan keyin ham turishi kerak bo'lsa `STORAGE_BACKEND=supabase`
qo'ying va `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` bering; bucket avtomatik
yaratiladi.

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
| `POST /api/jobs/{id}/scenes/{i}/regenerate` | Faqat o'sha sahnani qayta yaratish |
| `POST /api/jobs/{id}/thumbnails` | Uchta muqova varianti |
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
| `GET /api/health` | Qaysi kalitlar bor, qaysi modellar ishlaydi, formatlar, harakatlar |

## Loyiha tuzilishi

```
app/
├── main.py            FastAPI: API, yuklash, tahrirlash, yuklab olish
├── pipeline.py        Ikki bosqich: draft (qoralama) va render
├── config.py          Barcha env sozlamalari
├── store.py           SQLite: herolar (blob), musiqa (blob), joblar
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
- Ovoz so'rovlari daqiqasiga `TTS_RATE_LIMIT` tadan oshmaydi (standart 10).
  429 kelsa xato deb hisoblanmaydi — kutiladi va davom etadi, va bu kutish
  `TTS_DEADLINE` hisobiga kirmaydi.
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
- `DATABASE_URL` berilsa hero kutubxonasi Postgres'da saqlanadi (Railway'da
  Postgres qo'shsangiz o'zi qo'yiladi). Faqat hero — chunki deploy konteyner
  diskini o'chiradi va hero yagona qayta yaratib bo'lmaydigan fayl. Birinchi
  ishga tushishda SQLite'dagi hero'lar avtomatik ko'chiriladi.
