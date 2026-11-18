# StudentFlow

StudentFlow — оқу үлгерімін есепке алатын FastAPI + SQLite веб-сайты. Жоба коды `student_site/` ішінде орналасқан; интерфейс қазақ тілінде.

## Іске қосу

```bash
cd student_site
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Сайт `http://127.0.0.1:8000` мекенжайында ашылады. Алғашқы бар аккаунттарға құпиясөз тағайындау үшін `student_site/set_passwords.py` скриптін орындаңыз. Сондай-ақ `/register` арқылы студент рөлі бар аккаунт жасауға болады.

## Жаңартылған модуль — зертханалық жұмыс №12

- `/register`: қауіпсіз тіркелу, оқу тобы, Argon2 хэші және автоматты кіру.
- `/account`: ағымдағы парольді растау арқылы құпиясөзді ауыстыру; ескі сессиялар жарамсыз болады.
- `/lab12`: RBAC таныстырылымы және SQL-инъекция қауіпсіз тесті.
- `/admin`: тек әкімшіге арналған панель және пайдаланушыларды іздеу.
- `/teacher`: журналды іздеу/сүзу; оқытушы тек өз пәндерін көреді.
- `student_site/backup_db.py`: SQLite backup API арқылы тексерілген сақтық көшірме.
- 404/500 беттері түсінікті, кіру әрекеттері жиілігі бойынша шектеледі.
- Толық қорғау есебі: [`student_site/docs/LAB12_REPORT.md`](student_site/docs/LAB12_REPORT.md).

Тесттер, браузерлік E2E және операциялық нұсқаулық: `student_site/docs/OPERATIONS.md`. CI конфигурациясы — `.github/workflows/ci.yml`.
