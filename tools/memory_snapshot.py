#!/usr/bin/env python3
"""memory_snapshot.py PID [LABEL]: the game's memory on Linux, split by what it is (#640, docs/perf.md "Memory").

RSS/PSS from /proc/PID/smaps per mapping class, the guest memory file's touched pages
(/dev/shm/xenia_memory_*: tmpfs blocks), private writable (commit-like) totals, and the
GPU memory the process's DRM clients hold (amdgpu fdinfo: drm-memory-vram/gtt).
GTT (system RAM the GPU driver holds for the process) is not in RSS: add it for the total.
The guest's pages are mapped twice (virtual and physical views), so their RSS counts them twice
and their PSS (or the file's touched size) once.
Prints a JSON line (append it to a file) on stdout and a table on stderr.

  python3 tools/memory_snapshot.py $(pgrep -x forza) free_roam >> mem.jsonl"""
import json, os, re, sys, time

pid = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else ""
KB = 1024

def classify(path, perms):
    if 'xenia_memory' in path:
        return 'guest'
    if path.startswith('/dev/dri') or 'amdgpu' in path or path.startswith('/memfd:') and 'dri' in path:
        return 'gpu_mapped'
    if path.startswith('/memfd:'):
        return 'memfd:' + path.split(':', 1)[1].split(' ')[0]
    if path == '[heap]':
        return 'anon_heap'
    if path.startswith('[stack') or path == '[stack]':
        return 'stack'
    if path in ('[vdso]', '[vvar]', '[vsyscall]', '[vvar_vclock]'):
        return 'kernel'
    if path == '' or path.startswith('['):
        return 'anon'
    if '/dev/shm/' in path:
        return 'shm:' + os.path.basename(path)
    base = os.path.basename(path.replace(' (deleted)', ''))
    if base == 'forza':
        return 'file:forza (game code)'
    if base.startswith('librexgpu'):
        return 'file:librexgpu-xenos.so'
    if base.startswith('librexruntime'):
        return 'file:librexruntime.so'
    if base.startswith('libvulkan_radeon') or base.startswith('libLLVM') or 'libdrm' in base or base.startswith('libgallium'):
        return 'file:driver libs'
    if path.endswith('.so') or '.so.' in base:
        return 'file:other libs'
    return 'file:other ' + base[:40]

cats = {}
priv_write = 0  # private writable mappings: what Linux counts toward Committed_AS
anon_big = []
cur = None
with open(f'/proc/{pid}/smaps') as f:
    for line in f:
        m = re.match(r'^([0-9a-f]+)-([0-9a-f]+) (\S+) ([0-9a-f]+) \S+ \S+\s*(.*)$', line)
        if m:
            lo, hi, perms, path = int(m[1], 16), int(m[2], 16), m[3], m[5].strip()
            c = classify(path, perms)
            cur = cats.setdefault(c, {'vsz': 0, 'Rss': 0, 'Pss': 0, 'Private_Dirty': 0, 'Shared_Clean': 0,
                                      'Private_Clean': 0, 'Swap': 0, 'n': 0})
            cur['vsz'] += hi - lo
            cur['n'] += 1
            if perms[1] == 'w' and perms[3] == 'p':
                priv_write += hi - lo
            last = (lo, hi, path, c)
            continue
        k, _, v = line.partition(':')
        if cur is not None and k in cur and k not in ('vsz', 'n'):
            kb = int(v.split()[0])
            cur[k] += kb * KB
            if k == 'Rss' and last[3] == 'anon' and kb * KB >= 32 << 20:
                anon_big.append((kb * KB, hex(last[0]), last[1] - last[0]))

status = {}
for line in open(f'/proc/{pid}/status'):
    k, _, v = line.partition(':')
    if k in ('VmPeak', 'VmSize', 'VmHWM', 'VmRSS', 'RssAnon', 'RssFile', 'RssShmem', 'VmData', 'VmSwap'):
        status[k] = int(v.split()[0]) * KB
rollup = {}
for line in open(f'/proc/{pid}/smaps_rollup'):
    k, _, v = line.partition(':')
    if v.strip().endswith('kB'):
        rollup[k] = int(v.split()[0]) * KB

# Guest memory file(s) this process maps: tmpfs blocks = pages ever touched and not freed.
guest_files = {}
for c in open(f'/proc/{pid}/maps'):
    m = re.search(r'(/dev/shm/xenia_memory_\d+)', c)
    if m and m[1] not in guest_files:
        rng = c.split()[0]
        for path in (f'/proc/{pid}/map_files/{rng}', m[1]):  # map_files also works once unlinked
            try:
                st = os.stat(path)
                guest_files[m[1]] = {'size': st.st_size, 'allocated': st.st_blocks * 512}
                break
            except OSError:
                pass

# DRM clients (amdgpu fdinfo), one per client id.
gpu = {}
fdd = f'/proc/{pid}/fdinfo'
for fd in os.listdir(fdd):
    try:
        txt = open(f'{fdd}/{fd}').read()
    except OSError:
        continue
    if 'drm-client-id' not in txt:
        continue
    cid = re.search(r'drm-client-id:\s*(\d+)', txt)[1]
    if cid in gpu:
        continue
    d = {}
    for k, v in re.findall(r'^(drm-[a-z-]+|amd-[a-z-]+):\s*(\d+) KiB', txt, re.M):
        d[k] = int(v) * KB
    gpu[cid] = d

out = {'t': time.time(), 'label': label, 'pid': int(pid), 'status': status, 'rollup': rollup,
       'private_writable_vsz': priv_write, 'cats': cats, 'guest_files': guest_files, 'gpu': gpu,
       'anon_big': sorted(anon_big, reverse=True)[:12]}
print(json.dumps(out))

MB = lambda b: f'{b / 1048576:8.1f}'
print(f'# {label} pid {pid}  RSS {MB(status["VmRSS"])} MB  PSS {MB(rollup.get("Pss", 0))}  '
      f'anon {MB(status["RssAnon"])}  file {MB(status["RssFile"])}  shmem {MB(status["RssShmem"])}  '
      f'swap {MB(status.get("VmSwap", 0))}  HWM {MB(status["VmHWM"])}', file=sys.stderr)
print(f'#   private writable (commit-like) {MB(priv_write)} MB, VmData {MB(status["VmData"])}', file=sys.stderr)
for name, g in guest_files.items():
    print(f'#   guest file {os.path.basename(name)}: size {MB(g["size"])} MB, touched {MB(g["allocated"])} MB',
          file=sys.stderr)
print(f'#   {"class":34s} {"vsz":>9s} {"rss":>9s} {"pss":>9s} {"privdirty":>9s} {"swap":>8s}', file=sys.stderr)
for c, v in sorted(cats.items(), key=lambda kv: -kv[1]['Pss']):
    if v['Rss'] < 1 << 20:
        continue
    print(f'#   {c:34s} {MB(v["vsz"])} {MB(v["Rss"])} {MB(v["Pss"])} {MB(v["Private_Dirty"])} {MB(v["Swap"])}',
          file=sys.stderr)
for cid, d in gpu.items():
    print('#   drm client %s: ' % cid + ', '.join(f'{k[4:] if k.startswith("drm-") else k} {MB(v).strip()}'
                                              for k, v in d.items() if v >= 1 << 20), file=sys.stderr)
for rss, addr, size in out['anon_big']:
    print(f'#   big anon {addr} vsz {MB(size)} rss {MB(rss)}', file=sys.stderr)
