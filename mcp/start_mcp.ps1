param(
    [string]$Python = "python",
    [int]$Port = 27184,
    [string]$Adb = "adb",
    [string]$Serial = "",
    [switch]$NoAdbForward,
    [switch]$Direct,
    [string]$AdminHost = "127.0.0.1",
    [int]$AdminPort = 27185,
    [string]$AdminToken = "",
    [switch]$NoAdmin
)

$ErrorActionPreference = "Stop"
$server = Join-Path $PSScriptRoot "mcp_server.py"
$serverArgs = @(
    $server,
    "--port", [string]$Port,
    "--adb", $Adb,
    "--admin-host", $AdminHost,
    "--admin-port", [string]$AdminPort
)

if ($Serial) {
    $serverArgs += @("--serial", $Serial)
}
if ($NoAdbForward) {
    $serverArgs += "--no-adb-forward"
}
if ($Direct) {
    $serverArgs += "--direct"
}
if ($AdminToken) {
    $serverArgs += @("--admin-token", $AdminToken)
}
if ($NoAdmin) {
    $serverArgs += "--no-admin"
}

& $Python @serverArgs
exit $LASTEXITCODE
