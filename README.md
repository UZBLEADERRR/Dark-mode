# AI Video Studio

Mavzu nomini yozasiz — ilova skript yozadi, ovoz beradi, har bir sahna uchun rasm
yaratadi, subtitr qo'shadi va tayyor MP4 ni render qiladi.

Yuklab qo'ygan **hero** rasmlaringiz sahnalarda reference sifatida ishlatiladi, shuning
uchun personajlar butun video davomida bir xil qiyofada chiqadi.

---

## Nima qiladi

```
Mavzu  ─▶  Director   ─▶  Imagesmith  ─▶  Ovoz    ─▶  Rasmlar  ─▶  Subtitler
           (skript)       (promptlar)     (TTS)       (AI)         (vaqt)
                                                                      │
                          MP4  ◀── render ◀── Ken Burns ◀────────────┘
                                   (ffmpeg)    zoom/pan
```

- **Ken Burns** — har bir rasm sekin zoom in/out yoki pan qiladi, sahnalar orasida
  cross-fade bo'ladi.
- **Sinxronlash** — har bir sahna klipi o'z ovozidan aynan `TRANSITION_SECONDS`
  uzunroq render qilinadi va cross-fade o'sha ortiqchani "yeydi". Natijada sahna *k*
  timeline'da aynan ovozning kumulyativ vaqtiga tushadi — rasm, ovoz va subtitr
  necha sahna bo'lishidan qat'i nazar bir joyda turadi.
- **Subtitr** — Claude qayerda qatorni bo'lishni hal qiladi, kod esa qachon
  ko'rinishini ovoz provayderidan kelgan so'z-vaqtlaridan hisoblaydi. Shu bo'linish
  tufayli AI xato so'z yozsa ham subtitr vaqti buzilmaydi.

## AI skills

| Skill | Vazifasi |
|---|---|
| `director` | Mavzuni sahna-ba-sahna skriptga aylantiradi (yoki tayyor ovozni sahnalarga bo'ladi) |
| `imagesmith` | Har bir sahnani rasm generatori tushunadigan promptga aylantiradi |
| `subtitler` | Subtitr qatorlarini bo'ladi, kod ularni so'z-vaqtlariga bog'laydi |
| `publisher` | YouTube sarlavha, tavsif, teglar, chapterlar, thumbnail prompt |

## Provayderlar

Hammasi adapter — `.env` orqali almashtirasiz, kod o'zgarmaydi.

| Tur | Variantlar |
|---|---|
| Rasm | `gemini` (hero konsistensiyasi eng yaxshi), `fal` (Flux Kontext), `openai` (gpt-image-1) |
| Ovoz | `elevenlabs` (so'z-vaqti bilan), `openai`, `gemini`, yoki **o'z audiongizni yuklash** |
| Subtitr vaqti | ElevenLabs timestamps → Whisper → proporsional taxmin |
| Saqlash | Supabase Storage (tavsiya) yoki lokal disk |

Faqat `ANTHROPIC_API_KEY` + bitta rasm kaliti + bitta ovoz kaliti bo'lsa yetadi.
Qaysi kalit bor-yo'qligi UI tepasidagi yorliqlarda ko'rinadi.

---

## Railway'ga deploy

1. Reponi Railway'da **New Project → Deploy from GitHub repo** qilib ulang.
   `Dockerfile` avtomatik topiladi (ffmpeg va shriftlar shu yerda o'rnatiladi).
2. **Variables** bo'limiga `.env.example` dagi kalitlarni qo'ying. Minimum:

   ```
   ANTHROPIC_API_KEY=...
   IMAGE_PROVIDER=gemini
   GEMINI_API_KEY=...
   TTS_PROVIDER=elevenlabs
   ELEVENLABS_API_KEY=...
   STORAGE_BACKEND=supabase
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_SERVICE_KEY=...
   ```

3. **Volume** qo'shing va `/data` ga mount qiling (`DATA_DIR=/data`). Bu bo'lmasa
   hero rasmlari va loyihalar har deploy'da o'chib ketadi.
4. Deploy tugagach domenni oching — UI shu yerda.

> Railway diski efemer. `STORAGE_BACKEND=supabase` qo'yilsa tayyor videolar public
> bucket'ga yuklanadi va yuklab olish linki deploy'dan keyin ham ishlaydi.
> Bucket avtomatik yaratiladi.

### Supabase sozlash

Supabase loyihangizda **Settings → API** dan `service_role` kalitini oling va
`SUPABASE_SERVICE_KEY` ga qo'ying. `videos` nomli public bucket ilova ishga
tushganda o'zi yaratiladi.

---

## Lokal ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install -y ffmpeg          # macOS: brew install ffmpeg
cp .env.example .env                    # kalitlarni to'ldiring
set -a && source .env && set +a
uvicorn app.main:app --reload --port 8000
```

`http://localhost:8000` ni oching.

Docker bilan:

```bash
docker build -t ai-video-studio .
docker run --rm -p 8000:8000 --env-file .env -v "$PWD/data:/data" ai-video-studio
```

---

## Ishlatish

1. **Herolar va musiqa** bo'limida personaj rasmlarini yuklang (ism + qisqa tavsif).
   Tavsif AI ga qiyofani saqlashda yordam beradi.
2. **Yangi video** bo'limida:
   - Mavzuni yozing
   - Format tanlang (16:9, 9:16 Shorts, 1:1, 4:5)
   - Taxminiy uzunlikni belgilang
   - Tilni tanlang
   - Sahnalarda qatnashadigan herolarni belgilang
   - **Video yaratish** bosing
3. O'ng tomonda progress, jurnal va tugagach video pleyer chiqadi.
   **⬇ Videoni yuklab olish** tugmasi bitta bosishda MP4 ni beradi, `.srt` alohida.

### Tayyor ovoz yuklash

"Ovozni o'zim yuklayman" katagini belgilab audio fayl bering. Ilova uni
transkripsiya qiladi, sahnalarga bo'ladi va rasmlarni aynan siz gapirgan
vaqtlarga moslaydi. Buning uchun `OPENAI_API_KEY` kerak (so'z-vaqtlari uchun).

---

## Sozlash

Barcha o'zgaruvchilar `.env.example` da izohi bilan. Eng ko'p ishlatiladiganlari:

| O'zgaruvchi | Default | Nima qiladi |
|---|---|---|
| `SECONDS_PER_SCENE` | `6.5` | Uzunlik nechta sahnaga bo'linishini belgilaydi |
| `TRANSITION_SECONDS` | `0.6` | Sahnalar orasidagi cross-fade |
| `VIDEO_CRF` | `20` | Kichikroq raqam = yaxshiroq sifat, kattaroq fayl |
| `VIDEO_PRESET` | `medium` | `ultrafast`…`veryslow` — render tezligi |
| `MUSIC_VOLUME` | `0.10` | Fon musiqasi balandligi (ovoz ostida avtomatik pasayadi) |
| `MAX_CONCURRENT_JOBS` | `1` | Bir vaqtda nechta render — RAM yetsa oshiring |

## Loyiha tuzilishi

```
app/
├── main.py            FastAPI: API, fayl yuklash, yuklab olish
├── pipeline.py        Bosqichlarni ketma-ket boshqaradi
├── config.py          Barcha env sozlamalari
├── store.py           SQLite: herolar, musiqa, joblar
├── skills/            Claude promptlari (director, imagesmith, subtitler, publisher)
├── providers/         Tashqi API adapterlari (images, tts, align, storage)
├── render/            ffmpeg: kenburns, subtitles (ASS), video
└── static/            UI (vanilla HTML/CSS/JS, build kerak emas)
```

## Eslatmalar

- Bitta sahna rasmi yaratilmasa butun render to'xtamaydi — o'sha sahnaga gradient
  qo'yiladi va ogohlantirish job natijasida ko'rinadi.
- Musiqa mikslashda `sidechaincompress` ishlatiladi (ovoz ostida musiqa pasayadi).
  Agar ffmpeg build'da bu filtr bo'lmasa, render avtomatik oddiy mikslashga
  qaytadi, keyin esa musiqasiz.
- 10 daqiqalik video ≈ 90 ta sahna. Render vaqti asosan rasm generatsiyasiga
  ketadi — `IMAGE_CONCURRENCY` ni oshirsangiz tezlashadi (provayder rate-limitiga qarang).
