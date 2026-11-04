# memory_snapshot.ps1: the game's memory on Windows, sampled while it runs (#640, docs/perf.md "Memory").
#
#   powershell -ExecutionPolicy Bypass -File tools\memory_snapshot.ps1 [-Interval 30] [-Out FILE.csv]
#
# Start it before or after the game (it waits for forza.exe) and stop it with Ctrl+C; it appends a CSV row
# every Interval seconds and prints the same:
#   ws_mb       the process's working set (its resident RAM)
#   private_mb  its private bytes: the commit charge it adds (the guest memory's 512 MB of physical memory
#               and every guest page committed since the start, the heaps, the driver's own memory)
#   peak_ws_mb  the working set's peak
#   gpu_dedicated_mb, gpu_shared_mb  its video memory (GPU Process Memory counters: dedicated VRAM, and the
#               system RAM the GPU driver uses for it)
#   commit_mb, commit_limit_mb  the whole system's commit charge and its limit (RAM + page file)
#   avail_mb    free RAM; hard_faults_s  pages read back from the disk per second (the system pages)
# The counter names are English: on a Windows in another language the GPU and system columns stay empty.
# The game's own log adds what it holds: "Guest memory: N MB committed on the host" (ReIGlue 0427) and the
# "GPU minute" lines (textures, render targets, GPU memory, process resident).
param([int]$Interval = 30, [string]$Out = "$env:USERPROFILE\Desktop\forza-memory.csv", [string]$Name = "forza")

function Counter([string[]]$paths) {
    try { return (Get-Counter -Counter $paths -ErrorAction SilentlyContinue).CounterSamples } catch { return @() }
}

if (-not (Test-Path $Out)) {
    "time,pid,ws_mb,private_mb,peak_ws_mb,gpu_dedicated_mb,gpu_shared_mb,commit_mb,commit_limit_mb,avail_mb,hard_faults_s" |
        Out-File -FilePath $Out -Encoding ascii
}
Write-Host "Writing to $Out every $Interval s; Ctrl+C stops."
while ($true) {
    $p = Get-Process -Name $Name -ErrorAction SilentlyContinue | Sort-Object WorkingSet64 -Descending | Select-Object -First 1
    if (-not $p) { Start-Sleep -Seconds 2; continue }
    $gpu = Counter @("\GPU Process Memory(pid_$($p.Id)*)\Dedicated Usage", "\GPU Process Memory(pid_$($p.Id)*)\Shared Usage")
    $ded = ($gpu | Where-Object { $_.Path -like "*dedicated usage" } | Measure-Object CookedValue -Sum).Sum
    $sha = ($gpu | Where-Object { $_.Path -like "*shared usage" } | Measure-Object CookedValue -Sum).Sum
    $sys = Counter @("\Memory\Committed Bytes", "\Memory\Commit Limit", "\Memory\Available MBytes", "\Memory\Pages Input/sec")
    $v = @{}
    foreach ($s in $sys) { $v[($s.Path -split "\\")[-1]] = $s.CookedValue }
    $mb = 1MB
    # Invariant culture: no thousands separators or decimal commas in the CSV.
    $row = [string]::Format([cultureinfo]::InvariantCulture,
        "{0},{1},{2:F0},{3:F0},{4:F0},{5:F0},{6:F0},{7:F0},{8:F0},{9:F0},{10:F1}", (Get-Date -Format "yyyy-MM-dd HH:mm:ss"),
        $p.Id, ($p.WorkingSet64 / $mb), ($p.PrivateMemorySize64 / $mb), ($p.PeakWorkingSet64 / $mb),
        ($ded / $mb), ($sha / $mb), ($v["committed bytes"] / $mb), ($v["commit limit"] / $mb),
        $v["available mbytes"], $v["pages input/sec"])
    $row | Out-File -FilePath $Out -Append -Encoding ascii
    Write-Host $row
    Start-Sleep -Seconds $Interval
}
