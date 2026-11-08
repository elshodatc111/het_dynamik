"""Dinamika PDF: tekshirish va saqlash — orqa fonda, jonli progress bilan."""
from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model

from apps.core import jobs

from . import services
from .models import Upload
from .parser.catalog import SECTION_BY_KEY
from .parser.districts import TOTAL_KEY


def _short(name: str) -> str:
    return name if len(name) <= 38 else name[:35] + "…"


def analyse_steps(names: list[str]) -> list[tuple[str, int]]:
    steps = []
    for n in names:
        steps += [(f"«{_short(n)}» — sahifalar o‘qilmoqda", 80), (f"«{_short(n)}» — tekshirilmoqda", 15)]
    steps.append(("Natija tayyorlanmoqda", 5))
    return steps


def _summary(rep) -> list:
    out = []
    for key, sd in rep.sections.items():
        total = sd.cur.get(TOTAL_KEY, {})
        sdef = SECTION_BY_KEY[key]
        first = [(m.label, total[m.key], m.kind) for m in sdef.metrics if m.key in total][:3]
        out.append({"title": sdef.short, "districts": len(sd.cur) - 1, "page": sd.page, "first": first})
    return out


def analyse_job(job: jobs.Job, items: list[tuple[str, str, str]], precards: list[dict]):
    """items: [(token, yo‘l, nom)] — oldindan tekshiruvdan o‘tgan PDF lar; precards — o‘tmaganlar (xato bilan)."""
    cards = list(precards)
    for i, (token, path, name) in enumerate(items):
        base = i * 2
        job.step(base)

        def cb(stage, a, b, base=base):
            if stage == "read":
                job.progress(a / b if b else 0, f"{a} / {b} sahifa")
            elif job.d["step"] == base:
                job.step(base + 1, "Qiymatlar o‘tgan kun va bazadagi ma’lumot bilan solishtirilmoqda…")

        rep = services.analyse_file(Path(path), cb)
        services.save_parsed(token, rep)
        card = {"token": token, "name": name, "ok": rep.ok, "errors": list(rep.errors), "warnings": list(rep.warnings),
                "report_date": rep.report_date.isoformat() if rep.report_date else None,
                "prev_date": rep.prev_date.isoformat() if rep.prev_date else None,
                "pages": rep.pages, "sections": len(rep.sections), "skipped_pages": list(rep.skipped_pages),
                "summary": _summary(rep) if rep.ok else []}
        cards.append(card)
    job.step(len(items) * 2, "Natija tayyorlanmoqda…")
    job.set_result({"cards": cards})


def save_steps(names: list[str]) -> list[tuple[str, int]]:
    return [(f"«{_short(n)}» — saqlanmoqda", 1) for n in names]


def save_job(job: jobs.Job, entries: list[tuple[str, str, bool]], user_id):
    """entries: [(token, nom, almashtirish)] — sanasi bo‘yicha tartiblangan holda saqlanadi."""
    user = get_user_model().objects.filter(pk=user_id).first()
    saved, skipped = [], []
    loaded = []
    for token, name, replace in entries:
        rep, path = services.load_parsed(token), services.pending_path(token)
        if rep and rep.ok and path:
            loaded.append((rep, path, name, token, replace))
    loaded.sort(key=lambda x: x[0].report_date)  # eski sanalar avval — «oldingi holat» to‘g‘ri to‘ldirilsin
    if not loaded:
        raise jobs.JobError("Fayllar topilmadi yoki muddati o‘tgan. Qaytadan yuklang.")
    for i, (rep, path, name, token, replace) in enumerate(loaded):
        job.step(i)
        try:
            services.save_report(rep, path, name, user, replace=replace)
            saved.append(rep.report_date.isoformat())
            path.unlink(missing_ok=True)
            services.parsed_path(token).unlink(missing_ok=True)
        except services.DuplicateUpload as exc:
            skipped.append(exc.existing.report_date.isoformat())
    job.set_result({"saved": saved, "skipped": skipped})
    job.done(job.d["redirect"])
