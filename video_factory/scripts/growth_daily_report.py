"""Evidence-only daily report. Model commentary cannot overwrite measured facts."""
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import growth_actions as ga


def cell(value):
    return str(value if value is not None else 'unknown').replace('|', '/').replace('\n', ' ')


def publication_receipts(entries):
    """A script event is not a publication; require a receipt for the same bytes."""
    found, seen = [], set()
    for entry in entries:
        run = entry.get('run_id') or ''
        if entry.get('consumer') != 'video_script' or not run or Path(run).name != run or run in seen:continue
        seen.add(run)
        root = ga.BASE_DIR / 'output/runs' / run
        manifest = ga.load(root/'manifest.json')
        receipt = manifest.get('publication') or {}
        if manifest.get('status') != 'published' or receipt.get('status') != 'success' or not receipt.get('url'):continue
        try:digest = hashlib.sha256((root/'short.mp4').read_bytes()).hexdigest()
        except OSError:continue
        if receipt.get('sha256') != digest:continue
        found.append({'brand':entry['brand'], 'run_id':run, 'url':receipt['url'], 'sha256':digest})
    return found


def write_report(output_dir=None):
    snapshot = ga.status()
    snapshot['report_generated_at'] = time.time()
    snapshot['strategy'] = ga.load(ga.DATA/'growth_strategy.json')
    snapshot['scout_attempts'] = ga.load(ga.DATA/'growth_scout/attempts.json')
    snapshot['publication_receipts'] = publication_receipts(snapshot['recent_consumption'])
    date = datetime.fromtimestamp(snapshot['report_generated_at'], ZoneInfo('Europe/Moscow'))
    target = Path(output_dir) if output_dir is not None else ga.BASE_DIR.parent/'output/growth'
    target.mkdir(parents=True, exist_ok=True)
    state = snapshot['state']
    lines = [f'# Аналитика и применение решений — {date:%Y-%m-%d}', '',
        f'Срез: {date:%H:%M:%S} МСК. revision `{cell(state.get("revision"))}`; measurements_at `{state.get("measurements_at")}`.',
        f'Свежесть: {snapshot["effective_status"]}, возраст измерений {snapshot["measurement_age_seconds"]} секунд.', '',
        'Это наблюдения и редакторские гипотезы. Причина роста и прирост от правок пока не установлены.', '',
        '## Назначения и зрелые результаты экспериментов', '',
        '| Тест | Назначено test/control | Зрелые n_test/n_control | Нужно на группу | Решение кода |',
        '| --- | --- | --- | --- | --- |']
    for experiment in snapshot['experiments']:
        lines.append('| '+ ' | '.join(cell(v) for v in (experiment['id'],
            f'{experiment["assigned_test"]}/{experiment["assigned_control"]}',
            f'{experiment.get("n_test")}/{experiment.get("n_control")}',
            experiment.get('need_per_arm', experiment.get('min_samples')), experiment.get('decision'))) +' |')
    for brand, plan in state.get('brands', {}).items():
        lines += ['', f'## {brand}: текущий снимок решений', '',
            'Защищённые факторы: '+', '.join(plan.get('protected_experiment_factors', [])), '',
            '| Площадка/формат/язык | Тема | n | Медиана просмотров в 24–30 ч | Вес | Статус |',
            '| --- | --- | --- | --- | --- | --- |']
        for key, allocation in plan.get('allocations', {}).items():
            for topic, stats in allocation['topics'].items():
                lines.append('| '+' | '.join(cell(v) for v in (key,topic,stats['n'],stats['median_views'],stats['weight'],stats['status']))+' |')
        if snapshot['effective_status'] != 'active':lines += ['', 'Срез устарел: правки из него не применяются.']
        elif not plan['actions']:lines += ['', 'Дополнительных активных сценарных действий нет. Тематические веса действуют независимо от занятых факторов тестов.']
        else:
            for action in plan['actions']:
                lines += ['', f'Сценарная гипотеза `{action["id"]}`: n={action["n"]}, язык={cell(action.get("lang"))}, {action["metric"]}={action["median"]}.',
                    'Наблюдения: '+', '.join(action['urls'])]
    lines += ['', '## Последние записи применения — это не список публикаций', '',
        '| Бренд | Время UTC | Потребитель | Источник/задание | Исторические actions | revision |',
        '| --- | --- | --- | --- | --- | --- |']
    for event in snapshot['recent_consumption']:
        stamp = datetime.fromtimestamp(event['at'], ZoneInfo('UTC')).isoformat()
        lines.append('| '+' | '.join(cell(v) for v in (event['brand'],stamp,event['consumer'],
            event.get('run_id', event.get('source_id',event.get('rubric'))),event.get('actions', []),event['revision']))+' |')
    lines += ['', '## Подтверждённые квитанции для заданий из этого среза', '']
    for receipt in snapshot['publication_receipts']:
        lines += [f'- {receipt["brand"]}: {receipt["run_id"]} — {receipt["url"]}; SHA-256 файла совпал с квитанцией.']
    if not snapshot['publication_receipts']:lines += ['В этом срезе подтверждённых квитанций нет. Это не доказывает отсутствие публикаций вне среза.']
    lines += ['', '## Автономный поиск и смена направления', '']
    for brand,attempt in snapshot['scout_attempts'].items():
        lines += [f'- {brand}: поиск {cell(attempt.get("status"))}; последний запуск {cell(attempt.get("at"))}.']
    for brand,plan in snapshot['strategy'].get('brands',{}).items():
        for trial in plan.get('trials',[]):
            if trial.get('status')!='active':continue
            lines += [f'- {brand} / {cell(trial.get("scope"))}: {cell(trial["idea"]["name_ru"])}; '
                f'наблюдаемые решения {cell(trial.get("cell_decisions",{}))}.']
    lines += ['', '## Что остаётся неизвестным', '',
        'Готовый сценарий не означает публикацию. Ошибка получения статистики не означает отказ загрузчика. Статус всех площадок этим отчётом не проверяется.',
        'Эффект правок на просмотры и удержание требует будущих сопоставимых замеров. Победителей A/B объявляет только decision кода.',
        'Отсутствующий измеритель переходов не даёт права писать «ноль конверсий». Занятые факторы не блокируют тематическую адаптацию.']
    matured = sum((e.get('n_test') or 0)+(e.get('n_control') or 0) for e in snapshot['experiments'])
    brief = (f'Аналитика {date:%d.%m %H:%M} МСК, revision {state.get("revision", "unknown")}. '
        f'Статус: {snapshot["effective_status"]}. {len(snapshot["experiments"])} тестов; {matured} зрелых замеров по тестам '
        '(один ролик может участвовать в нескольких). '+
        ('Тематические веса и сценарные гипотезы передаются производству; занятые факторы защищены. '
         if snapshot['effective_status']=='active' else 'Измерения устарели: правки из этого среза не применяются. ')+
        f'В последних {len(snapshot["recent_consumption"])} записях применения подтверждено {len(snapshot["publication_receipts"])} квитанций YouTube с совпавшим хешем MP4. '
        'Это не число всех публикаций. Рост от изменений ещё не измерен. Подробности и JSON-срез сохранены в output/growth/.')
    for suffix, content in (('-evidence.json',json.dumps(snapshot,ensure_ascii=False,indent=1)),('.md','\n'.join(lines)+'\n'),('-brief.txt',brief+'\n')):
        path = target/(date.strftime('%Y-%m-%d')+suffix)
        temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
        temporary.write_text(content);temporary.chmod(0o600);temporary.replace(path)
    return brief


if __name__ == '__main__':
    print(write_report())
