param(
    [string]$AppDir = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [string]$LogPath = (Join-Path $PWD 'moya_windows10_test.log')
)
$ErrorActionPreference = 'Stop'
function Log([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $Message"
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}
try {
    Set-Content -LiteralPath $LogPath -Value 'ISH Windows 10 22H2 test' -Encoding UTF8
    $app = [IO.Path]::GetFullPath($AppDir)
    $exe = Join-Path $app 'ISH.exe'
    $internal = Join-Path $app '_internal'
    $os = Get-CimInstance Win32_OperatingSystem
    Log "OS: $($os.Caption), build $($os.BuildNumber), architecture $($os.OSArchitecture)"
    if ($os.Caption -notlike '*Windows 10*' -or [int]$os.BuildNumber -ne 19045) {
        throw 'Тест предназначен для Windows 10 22H2 (build 19045).'
    }
    if ($os.OSArchitecture -notlike '*64*') { throw 'Требуется 64-разрядная Windows.' }
    foreach ($name in @('ISH.exe','_internal/python311.dll','_internal/vcruntime140.dll','_internal/vcruntime140_1.dll','_internal/msvcp140.dll')) {
        $path = Join-Path $app $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Не найден файл: $path" }
    }
    Log 'Структура onedir и Python DLL: OK'

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = '--self-test-imports'
    $psi.WorkingDirectory = $app
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    [void]$psi.EnvironmentVariables.Remove('PYTHONHOME')
    [void]$psi.EnvironmentVariables.Remove('PYTHONPATH')
    $test = [System.Diagnostics.Process]::Start($psi)
    if (-not $test.WaitForExit(120000)) { $test.Kill(); throw 'Smoke test не завершился за 120 секунд.' }
    if ($test.ExitCode -ne 0) { throw "Автономный Python/cryptography test завершился с кодом $($test.ExitCode)." }
    Log 'Автономный запуск без Python в PATH и AES-GCM round-trip: OK'

    $guiInfo = New-Object System.Diagnostics.ProcessStartInfo
    $guiInfo.FileName = $exe
    $guiInfo.WorkingDirectory = $app
    $guiInfo.UseShellExecute = $false
    $guiInfo.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    [void]$guiInfo.EnvironmentVariables.Remove('PYTHONHOME')
    [void]$guiInfo.EnvironmentVariables.Remove('PYTHONPATH')
    $gui = [System.Diagnostics.Process]::Start($guiInfo)
    Start-Sleep -Seconds 8
    $gui.Refresh()
    if ($gui.HasExited) { throw "GUI закрылся при запуске (код $($gui.ExitCode))." }
    Log "GUI запустился без Python (PID $($gui.Id)): OK"
    if ($gui.CloseMainWindow()) {
        if (-not $gui.WaitForExit(10000)) { $gui.Kill() }
    } else {
        $gui.Kill()
    }
    Log 'Проверка Windows 10 завершилась успешно.'
} catch {
    Log ("ОШИБКА: " + $_.Exception.Message)
    exit 1
}
exit 0
