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

## Ma'lumotlar qayerda

- **Herolar, musiqa va qatlam rasmlari** — SQLite bazasida, fayllari ham blob
  sifatida ichida. Alohida papka kerak emas: bitta `app.db` — butun kutubxona.
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

O'zgarishlar **o'zi saqlanadi** (yozganingizdan ~0.8 soniya keyin), tepada
"saqlandi" deb turadi.

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
| `PATCH /api/jobs/{id}/scenes/{i}` | Sahnani va uning qatlamlarini tahrirlash |
| `POST /api/jobs/{id}/scenes/{i}/regenerate` | Faqat o'sha sahnani qayta yaratish |
| `GET/POST/DELETE /api/assets` | Qatlam rasmlari (stiker, logotip) |
| `POST /api/jobs/{id}/render` | Render qilish / qayta render |
| `GET /api/jobs/{id}/download` | MP4 yuklab olish |
| `GET /api/health` | Qaysi kalitlar bor, formatlar, harakatlar |

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
