# YouTube Shorts Factory

مصنع فيديوهات شورتس بالذكاء الاصطناعي. كل تشغيل بيولّد فيديوهات عمودية (1080×1920)
بالعربي والإنجليزي: سكريبت من Gemini، تعليق صوتي بـ edge-tts، لقطات من مصادر footage،
ترجمة محروقة على الفيديو، ثم رفع لـ Google Drive وإشعار على Telegram.

## المعمارية

```
config/topics.json          ← خطة المحتوى (فكرتين/يوم × لغتين)
        │
        ▼
   shorts_factory/llm.py     ← سكريبت + hook + كلمات مفتاحية (Gemini)
        │
        ▼
   shorts_factory/tts.py     ← تعليق صوتي جملة جملة (edge-tts، مجاني)
        │
        ▼
   shorts_factory/subtitles.py ← ترجمة SRT/ASS متظبطة على مدة الصوت الفعلية
        │
        ▼
   shorts_factory/media.py   ← لقطات: Pexels → Pixabay → Internet Archive
        │                       → Wikimedia → خلفية متدرجة مولّدة
        ▼
   shorts_factory/compose.py ← دمج + ترجمة محروقة + mux الصوت
        │
        ▼
   drive.py / telegram.py    ← رفع + إشعار (+ webhook لـ n8n)
```

## التشغيل السريع

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# فحص الخدمات المتاحة
GEMINI_API_KEY=... .venv/bin/python run.py --check

# إنتاج كل الفيديوهات
GEMINI_API_KEY=... .venv/bin/python run.py

# إنتاج فيديو واحد
.venv/bin/python run.py --topic ai-tools --lang ar
```

## المتغيرات والـ Secrets

كل الأسرار بتقرأ من الـ environment، وبتترفع كـ GitHub Actions Secrets.

| الاسم | مطلوب | الوصف |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | توليد السكريبت |
| `PEXELS_API_KEY` | اختياري | لقطات من Pexels |
| `PIXABAY_API_KEY` | اختياري | لقطات من Pixabay (الأفضل) |
| `GOOGLE_SERVICE_ACCOUNT` | اختياري | JSON الـ Service Account للرفع على Drive |
| `GOOGLE_DRIVE_FOLDER_ID` | اختياري | مجلد Drive الوجهة |
| `TELEGRAM_BOT_TOKEN` | اختياري | إشعارات تيليجرام |
| `TELEGRAM_CHAT_ID` | اختياري | المحادثة المستقبِلة |
| `N8N_WEBHOOK_URL` | اختياري | إشعار n8n بنتيجة التشغيل |
| `GEMINI_MODEL` | اختياري | موديل محدد، وإلا بيتم اكتشافه تلقائياً |
| `TARGET_SECONDS` | اختياري | طول الفيديو المستهدف (افتراضي 45) |

### إضافة الـ Secrets على GitHub

الطريقة السريعة — سكربت واحد بيسألك على كل حاجة:

```bash
./scripts/setup_secrets.sh
```

أو يدوي:

```bash
gh secret set GEMINI_API_KEY --body "<your-key>"
gh secret set PIXABAY_API_KEY --body "<your-key>"
gh secret set TELEGRAM_BOT_TOKEN --body "<your-token>"
gh secret set TELEGRAM_CHAT_ID --body "<your-chat-id>"
```

### إزاي تجيب كل مفتاح

| الخدمة | الطريقة | ملاحظة |
|---|---|---|
| Gemini | https://aistudio.google.com/apikey | بدونه مفيش سكريبت |
| Pixabay | https://pixabay.com/api/docs/ | مجاني، أفضل لقطات. لو `Pexels` رافع `401` ده بديله |
| Google Drive | Google Cloud Console → مشروع → تفعيل Drive API → Service Account | **لازم تشارك المجلد مع إيميل الـ service account** وإلا الرفع هيفشل بـ `404` |
| Telegram | `@BotFather` → `/newbot` | الـ chat id من `@userinfobot` |
| n8n | استورد الـ workflow من `n8n/` وفعّله | الرابط بيكون `https://host/webhook/shorts-factory` |

لتشخيص الحالة في أي وقت:

```bash
python run.py --check
```

بيقولك إيه اللي ناقص وطلبه منين بالظبط.

## الاختبارات

```bash
python tests/test_integrations.py
```

بتغطي ربط Drive (إنشاء المجلد، إعادة استخدامه، الـ folder id) وتيليجرام والـ webhook
من غير ما تحتاج credentials حقيقية، عشان أول ما تحط الـ secrets تشتغل من أول مرة.
الـ workflow بيشغّلها أوتوماتيك قبل الإنتاج.

## مصدر اللقطات

النظام بيجرّب المزودين بالترتيب:

1. **Pexels** — أفضل جودة، محتاج مفتاح صالح.
2. **Pixabay** — مجاني وسهل، وده أحسن بديل لو Pexels رافع `401`.
3. **Internet Archive** — مجاني وبدون مفتاح، الجودة عشوائية.
4. **Wikimedia Commons** — مجاني وبدون مفتاح، الجودة عشوائية.
5. **خلفية متدرجة مولّدة** — مفيش فشل أبداً، الفيديو بيتنتج في كل الأحوال.

الملفات اللي أكبر من 120MB بتتخطى، والـ downloads عندها حد أقصى 80MB وميزانية
وقت 4 دقايق، بعد كده النظام بيتحوّل للخلفية المتدرجة بدل ما يعلق.

## الترجمة

- الصوت بيتولّد جملة جملة، فمدة كل جملة معروفة بالظبط.
- الـ SRT والـ ASS بيتحسبوا من نفس المدد دي، فمفيش انزلاق بين الصوت والترجمة.
- العربي بيستخدم `{\rArabic}` مع خط Noto Sans Arabic و direction من اليمين للشمال.
- ملفات SRT كمان بتتحفظ جنب الفيديو عشان ترفعها على يوتيوب.

## التشغيل التلقائي

`.github/workflows/produce-videos.yml` بيشتغل:

- **يومياً** الساعة 12:00 UTC (قابل للتعديل).
- **يدوياً** من صفحة Actions (مع اختيار اللغة أو الموضوع).
- بيحفظ الفيديوهات كـ artifacts لو الـ Drive upload فشل.

## ربط n8n

`n8n/shorts-factory-notifications.json` فيه workflow جاهز للاستيراد:

1. في n8n: **Workflows → Import from File** واختار الملف.
2. اربط credential تيليجرام (اسمه `Telegram account`) بالـ bot token.
3. فعّل الـ workflow، وخد رابط الـ webhook:
   `https://<your-n8n-host>/webhook/shorts-factory`
4. حطه كـ secret:
   ```bash
   gh secret set N8N_WEBHOOK_URL --body "https://<your-n8n-host>/webhook/shorts-factory"
   ```

الـ workflow بيستقبل نتيجة كل تشغيل وبيبعت رسالة تيليجرام للنجاح أو الفشل.

## المخرجات

```
out/<topic>_<lang>_<timestamp>/
├── <title>.mp4        ← الفيديو النهائي
├── subtitles.srt      ← ترجمة جاهزة للرفع
├── narration.m4a      ← الصوت
└── subtitles.ass
```
