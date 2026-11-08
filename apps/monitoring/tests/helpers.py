"""Test uchun sintetik Excel fayllar (haqiqiy hisobot tuzilmasiga o‘xshash)."""
from io import BytesIO

from openpyxl import Workbook

DCU_HEADER = ["ЭСП", "Подстанция", "Фидер", "ТП", "Тип устройства", "№ Устройства", "Статус", "Время онлайна/офлайна",
              "Оператор связи", "Широта", "Долгота", "IP-адрес устройства", "Порт оборудования", "Сервер IP", "Номер сим карты",
              "Имя менеджера", "Телефон менеджера", "Телефон", "Лицевой счет потребителя"]
METER_HEADER = ["№", "ЭСП", "Подстанция", "Фидер", "Номер трансформатора", "Лицевой счет потребителя", "Тип потребителя",
                "Имя потребителя", "Номер счетчика", "Тип связи", "Имя менеджера", "Телефон менеджера",
                "Несобранный период (день)", "Несобранный период (время)", "GPS(X,Y)", "Адрес установки"]


def break_dimension(data: bytes) -> bytes:
    """Haqiqiy hisobotlardagi kabi sheet o‘lchamini noto‘g‘ri (A1:A1) qilib qo‘yadi."""
    import re
    import zipfile
    src, out = zipfile.ZipFile(BytesIO(data)), BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            raw = src.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet"):
                raw = re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:A1"', raw)
            dst.writestr(item, raw)
    return out.getvalue()


def dcu_row(dev, esp="Koson TETK", status="Offline", at="08/11/2026 15:47:34", sim="8999807020033131942", **kw):
    return [esp, kw.get("sub", "NS Koson 110/35/10 kV"), kw.get("feeder", "Shaxar-1 10 kV"), kw.get("tp", "516"), "DCU", dev, status, at,
            kw.get("op", "UMS"), 39.05, 65.6, "185.213.230.24", "32808", "hes-1", sim, "Kamolov Abbos", "31210892640011", None, None]


def dcu_xlsx(rows, header=None):
    wb = Workbook()
    ws = wb.active
    ws.append(["Отчет по мониторингу сетевых устройств"])
    ws.append(["Region / TR:Qashqadaryo HETK", " "])
    ws.append(header or DCU_HEADER)
    for r in rows:
        ws.append(r)
    b = BytesIO()
    wb.save(b)
    return b.getvalue()


def meter_row(i, days, ctype="Бытовые", esp="Koson TETK", meter=None, account=None, **kw):
    return [float(i), esp, "NS Koson 110/35/10 kV", "Shaxar-1 10 kV", kw.get("tr", "516"), account or f"{i:07d}", ctype,
            f"TEST ISTEMOLCHI {i}", meter or f"1212{i:08d}", kw.get("comm", "PLC"), "Kamolov A", None, days, "04:03:13", ",", "Manzil " + str(i)]


def meters_xlsx(rows, header=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "sheet1"
    ws.append(["Нет информации счетчика Отчет"])
    ws.append(header or METER_HEADER)
    for r in rows:
        ws.append(r)
    b = BytesIO()
    wb.save(b)
    return b.getvalue()
