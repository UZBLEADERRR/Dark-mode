# Flow Agent — Railway'da, Sarideo yonida

Maqsad: telefondan Sarideoga «shu videoni qil» deysiz, u o'zi Flow'ga borib
rasmlarni yasab qaytadi. Siz hech narsa qilmaysiz.

Bu papkada Flow Agentning **kodi yo'q**. U [`kodelyx/flow-agent`](https://github.com/kodelyx/flow-agent)
degan alohida loyiha va shunday qolaveradi — bu yerda faqat uni deploy qilish
retsepti va kengaytmasini o'z serveringizga qaratadigan skript bor.

## Avval bitta narsani bilib qo'ying

**Brauzer baribir kerak.** Uni Railway'ga ko'chirib bo'lmaydi, va bu men
tuzatolmaydigan narsa: Google Flow har bir so'rovda

1. sizning Google seansingizni (`Bearer ya29…`), va
2. o'sha sahifada yechiladigan **reCAPTCHA** ni

talab qiladi. Ikkalasi ham `labs.google` sahifasida, sizning akkauntingiz bilan
kirilgan brauzerda bo'ladi. Railway'da esa na sizning akkauntingiz bor, na
brauzer.

Shuning uchun bo'linish shunday:

```
Railway (doim ishlaydi)              Uydagi kompyuter (yoqib ketasiz)
┌────────────────────────┐           ┌──────────────────────────────┐
│ Sarideo                │           │ Chrome                       │
│   ↓ HTTP               │           │   Flow Agent kengaytmasi     │
│ Flow Agent backend  ◄──┼── WSS ────┼── o'zi ulanadi               │
└────────────────────────┘           │   labs.google — siz kirgan   │
                                     └──────────────────────────────┘
```

Kengaytma **o'zi ulanadi** — kompyuteringizda port ochish, oq IP, tunnel kerak
emas. Sizdan talab: **Chrome ochiq tursin va Flow varag'iga kirilgan bo'lsin.**

Kompyuterni o'chirsangiz rasm yasalmaydi. Unda Sarideo sahnaga vaqtinchalik rasm
qo'yib, jurnalda sababini yozib, **davom etadi** — render yo'qolmaydi, keyin
o'sha sahnalarni qayta yaratasiz.

## 1. Backendni Railway'ga qo'yish

Railway'da yangi servis:

- **New → GitHub Repo** → shu repo (`Dark-mode`)
- **Settings → Config-as-code** → `flowagent/railway.json`

Shu bitta qator hammasini hal qiladi: qaysi Dockerfile, qanday ishga tushadi.
Dockerfile Flow Agentni build paytida o'z reposidan klon qilib oladi.

**Variables:**

| Nom | Qiymat | Nega |
|---|---|---|
| `SERVER_API_KEY` | uzun parol | **Albatta qo'ying.** Bo'lmasa manzilni bilgan har kim sizning Flow obunangizdan rasm yasaydi — manzil esa ochiq internetda |
| `DEFAULT_PROJECT` | **o'z Flow loyihangizning id'si** | Pastga qarang — bu eng ko'p e'tibordan chetda qoladigan sozlama |
| `FLOW_OUTPUT_DIR` | `/data/flow` | Volume shu yerga ulanadi |
| `IMAGE_MODEL` | `gem_pix_2` | `gem_pix_2` — Nano Banana Pro, `narwhal` — oddiy, `harbor_seal` — yengil va tez |
| `MAX_CONCURRENT_REQUESTS` | `5` | Dockerfile'da shunday; kamaytirsangiz sekinlashadi |
| `REQUEST_MIN_INTERVAL` | `2` | So'rovlar orasidagi eng kam tanaffus, soniyada |

### `DEFAULT_PROJECT` — buni albatta qo'ying

Flow Agent kodida **tayyor loyiha id'si yozilgan** va u sizniki emas — muallifniki:

```python
DEFAULT_PROJECT = os.environ.get("DEFAULT_PROJECT", "0143adf4-…")
```

Qo'ymasangiz, rasmlar o'sha begona loyiha nomidan so'raladi. Ishlashi ham
mumkin, ishlamasligi ham — lekin sizning Flow'ingizda ular ko'rinmaydi va
xato chiqsa sababini topib bo'lmaydi.

**O'z id'ingizni qanday topasiz:**

1. Chrome'da `labs.google/fx/tools/flow` ni oching
2. Loyihangizni oching (yo'q bo'lsa — yangi yarating)
3. Manzil satriga qarang:

```
labs.google/fx/tools/flow/project/a1b2c3d4-5678-90ab-cdef-1234567890ab
                                  └────────── shu qism ──────────┘
```

4. O'sha uzun qismni `DEFAULT_PROJECT` ga qo'ying

**Volume:** `/data` ga ulang. Bo'lmasa har deployda hero rasmlarining `media_id`
lari yo'qoladi va herolar qaytadan yuklanadi.

Deploy bo'lgach manzilni oching — `https://…up.railway.app/health` shuni beradi:

```json
{"status":"unauthorized_or_disconnected","extension_connected":false}
```

`extension_connected: false` — **normal**. Brauzer hali ulanmagan.

## 2. Kengaytmani o'z serveringizga qaratish

Flow Agent kengaytmasi muallifning serveriga ulanadi va uni panelda
o'zgartirib bo'lmaydi — manzil `config.js` ga yozilgan. Shu skript o'zgartiradi:

```bash
python flowagent/point-extension.py ~/flow-extension https://sizning.up.railway.app
```

Nima qiladi:

- `config.js` dagi manzilni sizniki qiladi
- `manifest.json` ga o'sha manzilga ruxsat qo'shadi (busiz Chrome ulanishni
  taqiqlaydi va panel «server ishlamayapti» deb ko'rsatadi)
- panelni **qorong'i, Sarideo rangida** qiladi

Har bir faylning nusxasi bir marta saqlanadi. Qaytarish:

```bash
python flowagent/point-extension.py ~/flow-extension --restore
```

Upstream yangilangach skriptni qayta ishlatavering — ikki marta ishlatsa hech
narsa buzilmaydi.

Keyin `chrome://extensions` da kengaytmani **Reload** qiling va `labs.google/fx/tools/flow`
ni oching. Railway'dagi `/health` endi `extension_connected: true` deyishi kerak.

## 3. Sarideoni unga qaratish

Sarideo servisining Variables'ida:

| Nom | Qiymat |
|---|---|
| `IMAGE_PROVIDER` | `flowagent` |
| `FLOW_AGENT_URL` | `https://sizning.up.railway.app` |
| `FLOW_AGENT_KEY` | yuqoridagi `SERVER_API_KEY` bilan bir xil |

**Keyin — muhim:** Sarideo → **Kutubxona → Modellar → «Rasmlarni kim yasaydi»**
da **«Flow Agent — o'zi yasaydi»** turganini tekshiring.

Nega: **Flow navbati** tugmasini bir marta yoqqan bo'lsangiz, o'sha tanlov bazaga
saqlangan va **`IMAGE_PROVIDER` dan kuchli** — ilova qayta ishga tushganda
o'shani oladi. Ikkalasi bir xil bo'lmasa, o'sha ro'yxatning tagida shu haqda
yozib turadi.

Jurnalda **«Flow'dan rasm kutilyapti (navbatda N ta)»** degan qator ko'rsangiz —
demak hali `flow` rejimida, `flowagent` da emas.

**Boshlanib qolgan loyihalar alohida.** Har bir loyiha o'zi boshlangandagi
provayderni eslab qoladi — ilovadagi tanlovni o'zgartirsangiz ham, yarim tayyor
video eskisini so'rayveradi. Uni ko'chirish uchun: loyihani **Tahrirlash** da
oching → sarlavha ostidagi **«Rasmlarni kim yasaydi»** dan yangisini tanlang.
O'sha loyihaning Flow navbatida turgan promptlari ham bekor qilinadi — bo'lmasa
render ularning har birini oxirigacha kutib chiqadi.

Tamom. Endi Sarideoda video yaratsangiz, har bir sahnaning rasmini u o'zi Flow
Agentdan so'raydi — navbat yo'q, tugma bosish yo'q.

## Herolar

Hero rasmlari **avtomatik** yuboriladi: sahnada hero bo'lsa, uning fotosi Flow'ga
yuklanib, o'sha rasm uchun **reference** qilib beriladi (`ref_media_ids`). Yuz va
kiyim shuning uchun sahnadan sahnaga saqlanadi.

Bir hero bir marta yuklanadi — o'ntacha sahnada qatnashsa ham. Fotosini
almashtirsangiz, yangisi qayta yuklanadi.

Bitta kadrda 10 tagacha hero.

## `flow` va `flowagent` — farqi

| | `flow` | `flowagent` |
|---|---|---|
| Sarideo nima qiladi | promptni **navbatga qo'yadi** va kutadi | rasmni **so'raydi** va oladi |
| Brauzerda kim ishlaydi | Sarideoning kengaytmasi sahifani bosib yuradi | Flow Agent Google API'si bilan gaplashadi |
| Sahifa o'zgarsa | buziladi, selektor tuzatiladi | ta'sir qilmaydi |
| Siz uxlab yotganda | ishlaydi (brauzer ochiq bo'lsa) | ishlaydi (brauzer ochiq bo'lsa) |

Ikkalasi ham bitta Flow obunasidan foydalanadi. `flowagent` ishonchliroq, chunki
sahifaning ko'rinishiga bog'liq emas.

## Nima sinaldi, nima sinalmadi

**Sinaldi:** Sarideoning Flow Agentga qiladigan hamma murojaati — uning
backendining o'rniga qo'yilgan soxta server bilan, 23 ta tekshiruv: promptlar,
o'lchamlar, hero yuklash va takror yuklamaslik, kalit, link bilan javob,
brauzer ulanmagan holat, manzil noto'g'ri holat. Backendning o'zi ham shu yerda
haqiqatan ishga tushirildi va `/v1/images/generations`, `/v1/upload`,
`/v1/history` manzillari borligi tasdiqlandi.

**Sinalmadi:** haqiqiy Google Flow bilan uchidan-uchiga ishlashi. Bu muhitdan
`labs.google` ga chiqib bo'lmaydi (proksi 403 beradi), va Docker demoni yo'q —
ya'ni Dockerfile ham shu yerda build qilinmagan. Birinchi deployda `/health` ni
tekshirib ko'ring.
