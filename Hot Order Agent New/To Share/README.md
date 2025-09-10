
# Hot Order Agent (OpenAI-enabled)

## Mac/Linux
```bash
cd HotOrderAgent_Full_OpenAI
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
streamlit run app.py   # UI
# in another terminal:
python -m scripts.poll_inbox  # IMAP poller
```

## Windows (PowerShell)
```powershell
cd HotOrderAgent_Full_OpenAI
py -3 -m venv venv
.env\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
streamlit run app.py   # UI
# in another window:
python -m scripts.poll_inbox  # IMAP poller
```
