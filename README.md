# Telegram Group Monitor

## დაყენება

```bash
cd tg-monitor
pip install -r requirements.txt
cp .env.example .env
# შეავსე .env ფაილი
python monitor.py
```

## ბრძანებები (ბოტში)

| ბრძანება | რასაც აკეთებს |
|----------|--------------|
| `/status` | მდგომარეობა |
| `/send <id> <num>` | ვარიანტი გაგზავნე target ჯგუფში |
| `/custom <id> ტექსტი` | საკუთარი ტექსტი გაგზავნე |
| `/help` | მენიუ |
