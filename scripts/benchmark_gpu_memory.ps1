param([Parameter(Mandatory=$true)][int]$TargetPid,
      [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$ownerProcess=Get-Process -Id $TargetPid
$creation=$ownerProcess.StartTime.ToUniversalTime().Ticks
$writer=[System.IO.StreamWriter]::new($OutputPath,$true,[System.Text.UTF8Encoding]::new($false))
$writer.AutoFlush=$true
try {
    while ($true) {
        $current=Get-Process -Id $TargetPid -ErrorAction SilentlyContinue
        if (!$current -or $current.StartTime.ToUniversalTime().Ticks -ne $creation) { break }
        $dedicated=$null; $shared=$null; $available=$false
        try {
            $items=@(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -Filter "Name LIKE 'pid_${TargetPid}_%'" -ErrorAction Stop)
            if ($items.Count) {
                $dedicated=($items | Measure-Object -Property DedicatedUsage -Sum).Sum
                $shared=($items | Measure-Object -Property SharedUsage -Sum).Sum
                $available=$true
            }
        } catch { }
        @{at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0; available=$available;
          dedicated_bytes=$dedicated; shared_bytes=$shared} | ConvertTo-Json -Compress | ForEach-Object {$writer.WriteLine($_)}
        Start-Sleep -Milliseconds 1000
    }
} finally { $writer.Dispose() }
