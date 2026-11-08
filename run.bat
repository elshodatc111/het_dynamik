@echo off
rem Windows: ishlab chiqish serverini bir bosishda ishga tushirish
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo [1/4] Virtual muhit yaratilmoqda...
  py -3 -m venv .venv || goto :fail
  call .venv\Scripts\activate || goto :fail
  python -m pip install -r requirements.txt || goto :fail
) else (
  call .venv\Scripts\activate || goto :fail
)

python -m pip install -q -r requirements.txt || goto :fail

if not exist .env (
  copy .env.example .env >nul
  echo .env yaratildi. Uni oching, sozlang va run.bat ni qayta ishga tushiring.
  notepad .env
  goto :end
)

echo [2/4] Baza tekshirilmoqda...
python manage.py create_db || goto :fail
echo [3/4] Migratsiya...
python manage.py migrate || goto :fail
python manage.py bootstrap_admin || goto :fail
echo Customer boti alohida oynada ishga tushmoqda...
start "Customer boti" cmd /k "cd /d %~dp0 && call .venv\Scripts\activate && python manage.py run_bot"
echo [4/4] Server: http://127.0.0.1:8000  (to'xtatish: Ctrl+C)
python manage.py runserver 127.0.0.1:8000
goto :end

:fail
echo.
echo XATO yuz berdi. Yuqoridagi xabarni o'qing.
:end
pause
