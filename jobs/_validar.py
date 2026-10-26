import json

a = json.load(open('jobs/vivo_validacion.json', encoding='utf-8'))['settings']
b = json.load(open('job_produccion_paralelo.json', encoding='utf-8'))['new_settings']
ok = True


def chk(c, m):
    global ok
    print(('  OK    ' if c else '  FALLA ') + m)
    if not c:
        ok = False


txt = json.dumps(b)
print('=== TASK VALUES DE seleccionar_guiones ===')
n = txt.count('tasks.seleccionar_guiones.values')
chk(n == 6, f'total = {n}, esperadas 6')
chk('tasks.generar_video.values' not in txt, 'cero refs a generar_video.values')

print('=== TAREAS Y PERMISOS ===')
for t in b['tasks']:
    k = t['task_key']
    dep = [d['task_key'] for d in t.get('depends_on', [])]
    r = t.get('run_if', 'ALL_SUCCESS')
    off = '[OFF]' if t.get('disabled') else '     '
    if 'for_each_task' in t:
        fe = t['for_each_task']
        h = fe['task']
        print(f"  {k:24} {off} for_each conc={fe.get('concurrency', 1)} run_if={r} <- {dep}")
        print(f"       hijo {h['task_key']:22} timeout={h.get('timeout_seconds')} "
              f"retries={h.get('max_retries')} run_if={h.get('run_if', 'ALL_SUCCESS')} "
              f"env={h.get('environment_key')}")
    else:
        print(f"  {k:24} {off} notebook      run_if={r} env={t.get('environment_key')} <- {dep}")

print('=== RUTAS DE NOTEBOOK ===')
todo = [(t['task_key'], t['notebook_task']['notebook_path'])
        for t in b['tasks'] if 'notebook_task' in t]
todo += [(t['task_key'] + '.' + t['for_each_task']['task']['task_key'],
          t['for_each_task']['task']['notebook_task']['notebook_path'])
         for t in b['tasks'] if 'for_each_task' in t]
for k, p in todo:
    print(f'  {k:38} {p.split("/")[-1]}')

print()
print('RESULTADO:', 'TODO OK' if ok else 'HAY FALLOS')
