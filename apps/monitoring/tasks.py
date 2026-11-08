"""Monitoring: fayllarni tekshirish va saqlash — orqa fonda, jonli progress bilan."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model

from apps.core import jobs

from . import services

KIND_TITLE = {"dcu": "Offline konsentratorlar", "meters": "Aloqasiz hisoblagichlar"}


def analyse_steps(names: list[str]) -> list[tuple[str, int]]:
    steps = []
    for n in names:
        short = n if len(n) <= 38 else n[:35] + "…"
        steps += [(f"«{short}» — qatorlar o‘qilmoqda", 70), (f"«{short}» — tahlil qilinmoqda", 15)]
    steps.append(("Natija tayyorlanmoqda", 5))
    return steps


def analyse_job(job: jobs.Job, items: list[tuple[str, str, str]]):
    """items: [(token, fayl yo‘li, asl nom)]. Natija job.result da, tahlil qilingan qatorlar .pkl da."""
    from pathlib import Path

    cfg = services.get_settings()
    edges = services.parse_edges(cfg.meter_edges)
    cards, hint = [], None
    for i, (token, path, name) in enumerate(items):
        base = i * 2
        job.step(base)

        def cb(stage, frac, n, base=base):
            if stage == "read":
                label = f"{n:,} qator o‘qildi".replace(",", " ") if n else None
                job.progress(frac, label)
            elif job.d["step"] == base:
                job.step(base + 1, "Ustunlar va qatorlar tekshirilmoqda…")

        res = services.analyse(Path(path), name, cb)
        services.save_parsed(token, res)
        card = {"token": token, "name": name, "kind": res.kind, "title": KIND_TITLE.get(res.kind, "Noma’lum"), "ok": res.ok,
                "errors": list(res.errors), "warnings": list(res.warnings), "rows": len(res.rows), "preview": None}
        if res.ok:
            hint = hint or (res.date_hint.isoformat() if res.date_hint else None)
            if res.kind == "meters":
                counts = [0] * len(edges)
                for r in res.rows:
                    b = services.bucket_index(r["days"], edges)
                    if b is not None:
                        counts[b] += 1
                card["preview"] = [[lb, c] for lb, c in zip(services.bucket_labels(edges), counts)]
        else:
            Path(path).unlink(missing_ok=True)
        cards.append(card)

    job.step(len(items) * 2, "Tekshiruv yakunlanmoqda…")
    kinds = [c["kind"] for c in cards if c["ok"]]
    if len(kinds) != len(set(kinds)):
        for c in cards:
            if c["ok"]:
                c["ok"] = False
                c["errors"].append("Ikkita bir xil turdagi fayl yuklandi — har turdan bittadan yuklang.")
                p = services.pending_path(c["token"])
                p and p.unlink(missing_ok=True)
    job.set_result({"cards": cards, "hint": hint})


SAVE_STAGES = [("prepare", "Tayyorlanmoqda", 4), ("dcu", "Konsentratorlar bazaga yozilmoqda", 30),
              ("meters", "Hisoblagichlar bazaga yozilmoqda", 45), ("chain", "Keyingi kunlar qayta hisoblanmoqda", 10),
              ("summary", "Svod hisoblanmoqda", 8), ("purge", "Eski tafsilotlar tozalanmoqda", 3)]


def save_job(job: jobs.Job, tokens: list[str], report_date: str, user_id, replace: bool):
    dcu = meters = None
    for t in tokens:
        res = services.load_parsed(t)
        if res and res.ok and res.kind == "dcu":
            dcu = res
        elif res and res.ok and res.kind == "meters":
            meters = res
    if not (dcu or meters):
        raise jobs.JobError("Fayllar topilmadi yoki muddati o‘tgan. Qaytadan yuklang.")
    user = get_user_model().objects.filter(pk=user_id).first()
    rd = date.fromisoformat(report_date)
    index = {k: i for i, k in enumerate(job.d["stage_keys"])}

    def pr(stage, frac):
        i = index.get(stage)
        if i is None:
            return
        if job.d["step"] != i:
            job.step(i, job.d["steps"][i][0])
        job.progress(frac)

    try:
        snap = services.save_parts(rd, dcu, meters, user, replace=replace, progress=pr)
    except services.AlreadyUploaded as exc:
        what = "konsentratorlar" if exc.part == "dcu" else "hisoblagichlar"
        raise jobs.JobError(f"{rd:%d.%m.%Y} uchun {what} allaqachon yuklangan. Almashtirish uchun administrator ruxsati kerak.")
    for t in tokens:
        p = services.pending_path(t)
        p and p.unlink(missing_ok=True)
        services.parsed_path(t).unlink(missing_ok=True)
    from apps.reports.services import audit
    parts = ", ".join(x for x, ok in (("konsentratorlar", dcu), ("hisoblagichlar", meters)) if ok)
    audit(user, "mon_upload", f"{rd:%d.%m.%Y} · {parts}" + (" · almashtirildi" if replace else ""))
    job.set_result({"snapshot": snap.pk, "parts": parts})
    job.done(job.d["redirect"])


def save_plan(has_dcu: bool, has_meters: bool):
    """(steps, stage_keys) — faqat kerakli bosqichlar."""
    use = [x for x in SAVE_STAGES if (x[0] != "dcu" or has_dcu) and (x[0] != "meters" or has_meters) and (x[0] != "chain" or has_dcu)]
    return [(label, w) for _, label, w in use], [k for k, _, _ in use]
