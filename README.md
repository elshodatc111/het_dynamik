# Dinamika — Qashqadaryo HETK kunlik hisobotlar dinamikasi

Kunlik PDF hisobotlarni (CAS konsentratorlar va hisoblagichlar holati) o‘qiydi, **lotin alifbosida** MySQL bazasiga kun-ma-kun saqlaydi va istalgan ikki sana orasidagi **dinamikani** veb-ilovada ko‘rsatadi hamda tayyor **Excel** fayl sifatida beradi.

**Texnologiyalar:** Python 3.12 · Django 5/6 · MySQL 8 · pdfplumber · openpyxl · Alpine.js · ApexCharts (hammasi lokal, internet shart emas).

## Imkoniyatlar

- PDF yuklash → avtomatik o‘qish → **ko‘rib chiqish oynasi** (xato va ogohlantirishlar) → tasdiqlash → bazaga yozish
- Bir sana uchun **faqat bitta PDF**; takroriy yuklash bloklanadi, almashtirish faqat ruxsati bor foydalanuvchiga
- Kirill → lotin (hudud nomlari, bo‘lim va ko‘rsatkichlar)
- **Solishtirish:** ixtiyoriy ikki sana, barcha bo‘limlar, ↑↓ farqlar (neytral rang), «Excel yuklab olish»
- **Trend:** ko‘rsatkich va hududlarni tanlab, sana oralig‘ida grafik
- Dashboard (asosiy ko‘rsatkichlar, mini-grafiklar, yuklanmagan kunlar ogohlantirishi)
- **Autentifikatsiya va rollar:** Admin, Operator, Tahlilchi, Kuzatuvchi + har bir foydalanuvchiga alohida ruxsat; foydalanuvchini o‘chirib qo‘yish
- Arxiv (asl PDF), audit jurnali, yorug‘/qorong‘i rejim, mobil moslashuv

## Tez ishga tushirish (Windows)

```bat
cd C:\Works\code
copy .env.example .env      :: .env ni oching va sozlang (pastga qarang)
run.bat
```

`run.bat` virtual muhit yaratadi, kutubxonalarni o‘rnatadi, migratsiya qiladi, administratorni yaratadi va serverni `http://127.0.0.1:8000` da ishga tushiradi.

### Sozlash (`.env`)

| O‘zgaruvchi | Izoh |
|---|---|
| `DB_ENGINE` | `mysql` (asosiy) yoki `sqlite` (faqat sinov uchun) |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | MySQL ulanishi |
| `DJANGO_SECRET_KEY` | uzun tasodifiy satr (ishlab chiqarishda majburiy) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | birinchi administrator |

MySQL da bazani oldindan yarating:

```sql
CREATE DATABASE dinamika CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'dinamika'@'%' IDENTIFIED BY 'parol';
GRANT ALL ON dinamika.* TO 'dinamika'@'%';
```

## Docker (server uchun)

```bash
cp .env.example .env        # DB_PASSWORD, DJANGO_SECRET_KEY, ADMIN_PASSWORD ni to‘ldiring
docker compose up -d --build
```

Ilova `http://server:8000` da ishlaydi. HTTPS uchun oldiga Nginx Proxy Manager qo‘ying va `.env` da
`DJANGO_CSRF_TRUSTED_ORIGINS=https://sizning-domen` ni belgilang.
PDF fayllar `media` volume ida, baza `dbdata` volume ida saqlanadi. **Zaxira nusxa:** kuniga bir marta
`docker compose exec db mysqldump ... > backup.sql` va `media` volume ni nusxalang.

## Rollar

| Ruxsat | Admin | Operator | Tahlilchi | Kuzatuvchi |
|---|:-:|:-:|:-:|:-:|
| Ko‘rish (bosh sahifa, solishtirish, trend, arxiv) | ✓ | ✓ | ✓ | ✓ |
| Excel va asl PDF yuklab olish | ✓ | ✓ | ✓ | |
| PDF yuklash | ✓ | ✓ | | |
| Sanani almashtirish / o‘chirish | ✓ | | | |
| Foydalanuvchilarni boshqarish | ✓ | | | |
| Audit jurnali | ✓ | | | |

«Foydalanuvchilar» sahifasida har bir kishiga rol berish, qo‘shimcha ruxsat qo‘shish va kirishni o‘chirish mumkin.

## Qanday ishlaydi

```
PDF ──► parser/extract.py ──► parser/validate.py ──► ko‘rib chiqish ──► MySQL (metric_values)
        (jadval → qiymatlar)   (yig‘indi, farq, bazaga      (tasdiqlash)        │
                                mosligi tekshiruvi)                              ▼
                                                              solishtirish (A → B) ──► veb + Excel
```

- Dinamika PDF dagi strelkalardan **olinmaydi** — ikki sananing qiymatlari farqi sifatida hisoblanadi. PDF dagi ↑↓ faqat parserni tekshirish uchun ishlatiladi.
- Har bir PDF da oldingi kun holati ham bor. U faqat bazada shu sana uchun qiymat **yo‘q bo‘lsa** saqlanadi (`is_prev`); keyinroq o‘sha sananing o‘z PDF i kelsa, o‘zi ustun bo‘ladi.
- Hududlar yig‘indisi «Jami» ga tengligi, oldingi sana qiymatlari bazadagiga mosligi tekshiriladi; nomuvofiqlik **ogohlantirish** sifatida ko‘rsatiladi (tuzilma xatosi esa saqlashga yo‘l qo‘ymaydi).

## Yangi hisobot ko‘rinishi kelsa

PDF tuzilmasi o‘zgarsa, yuklashda «noma’lum jadval tuzilmasi» xatosi chiqadi (jimgina noto‘g‘ri o‘qimaydi). Tuzatish:

1. `apps/reports/parser/layouts.py` — yangi shablon qo‘shing (ustunlar tartibi, ro‘yxat ko‘rinishida).
2. Yangi ko‘rsatkich bo‘lsa — `apps/reports/parser/catalog.py` ga lotincha nomi bilan qo‘shing (bazaga avtomatik tushadi).
3. `tests/fixtures/` ga namuna PDF qo‘shib, `apps/reports/tests/test_parser.py` ga test yozing.

Hozir qo‘llab-quvvatlanadi: 5 sahifali viloyat ko‘rinishi (ikki variant) va 9 sahifali HETK ko‘rinishi — jami 8 ta bo‘lim.

## Testlar

```bat
set DB_ENGINE=sqlite
python manage.py test
```

Testlar `tests/fixtures/` dagi haqiqiy PDF lar bilan ishlaydi (parser, saqlash oqimi, solishtirish, Excel, ruxsatlar).

## Papkalar

```
apps/reports/parser/   catalog.py (ko‘rsatkichlar) · layouts.py (jadval shablonlari) · extract.py · validate.py · translit.py · districts.py
apps/reports/          models · services (biznes-mantiq) · excel · views
apps/accounts/         kirish, foydalanuvchilar va rollar
templates/ static/     interfeys (lokal shrift va kutubxonalar)
tests/fixtures/        namuna PDF lar
```
