# Bu papka — Flow Agent, o'zgartirilmagan nusxa

Kodi [`kodelyx/flow-agent`](https://github.com/kodelyx/flow-agent) loyihasiniki.
Bu yerga **aynan o'sha holicha** ko'chirilgan: birorta qatori o'zgartirilmagan.

## Nega ko'chirildi

Avval Dockerfile uni build paytida GitHub'dan klon qilardi. Bu ikki narsaga
bog'liq edi: o'sha repo o'chib ketmasligi va build paytida internet ishlashi.
Endi kod shu yerda — deploy o'zgarmaydi va tashqi hech narsani kutmaydi.

## Yangilash

```bash
# yangi versiyani olib, shu papkani almashtiring
rm -rf flowagent/upstream/flow-agent flowagent/upstream/flow-extension
# ... yangi nusxani shu yerga qo'ying
```

Bizning kodimiz uning fayllariga tegmaydi, faqat HTTP orqali gaplashadi —
shuning uchun yangilash oddiy fayl almashtirish.

## Litsenziya

Manba repoda litsenziya fayli yo'q. Ya'ni mualliflik huquqi to'liq
mualliflarida qoladi. Bu nusxa shaxsiy foydalanish uchun saqlanadi;
tarqatmoqchi bo'lsangiz avval mualliflardan ruxsat so'rang.
