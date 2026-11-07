@echo off
rem Windows: ishlab chiqish serverini bir bosishda ishga tushirish
if not exist .venv (
  py -3 -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
if not exist .env copy .env.example .env
python manage.py migrate
python manage.py bootstrap_admin
python manage.py runserver 127.0.0.1:8000
