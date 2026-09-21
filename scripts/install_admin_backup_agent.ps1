[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidatePattern('^[A-Za-z]$')]
    [string]$DriveLetter,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$TargetId,
    [ValidatePattern('^[A-Za-z0-9._-]+@[A-Za-z0-9:._-]+$')]
    [string]$SshTarget = 'user@100.111.141.119',
    [string]$TaskName = 'AutPlay Admin Backup Agent',
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

function Get-AutPlayEligibleBackupDrive {
    Get-Partition | Where-Object DriveLetter | ForEach-Object {
        $partition = $_
        $disk = $partition | Get-Disk
        $volume = Get-Volume -DriveLetter $partition.DriveLetter
        if (
            $disk.BusType -eq 'USB' -and
            -not $disk.IsBoot -and
            -not $disk.IsSystem -and
            $volume.FileSystem -eq 'NTFS'
        ) {
            [pscustomobject]@{
                DriveLetter = [string]$partition.DriveLetter
                Label = [string]$volume.FileSystemLabel
                DiskNumber = [int]$disk.Number
                SizeGiB = [math]::Round([double]$disk.Size / 1GB, 1)
                FreeGiB = [math]::Round([double]$volume.SizeRemaining / 1GB, 1)
            }
        }
    }
}

if ($Uninstall) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled backup agent')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    }
    return
}

$eligible = @(Get-AutPlayEligibleBackupDrive | Sort-Object DriveLetter)
if ($eligible.Count -eq 0) {
    throw 'No non-system USB NTFS backup drive is attached.'
}

if ($DriveLetter) {
    $selected = $eligible | Where-Object DriveLetter -eq $DriveLetter.ToUpperInvariant()
    if ($null -eq $selected) {
        throw "Drive $DriveLetter is not an eligible non-system USB NTFS drive."
    }
} elseif ($eligible.Count -eq 1) {
    $selected = $eligible[0]
} else {
    $eligible | Format-Table DriveLetter, Label, DiskNumber, SizeGiB, FreeGiB -AutoSize
    $choice = (Read-Host 'Choose the backup drive letter').Trim().ToUpperInvariant()
    $selected = $eligible | Where-Object DriveLetter -eq $choice
    if ($null -eq $selected) {
        throw 'The selected drive is not in the eligible list.'
    }
}

$drive = $selected.DriveLetter.ToUpperInvariant()
$resolvedTargetId = if ($TargetId) { $TargetId } else { "windows-usb-$($drive.ToLowerInvariant())" }
$destinationRoot = "${drive}:\AutPlayBackups"
$baselineFile = Join-Path $destinationRoot 'admin-backup-baseline.json'
$launcher = Join-Path $PSScriptRoot 'run_admin_backup_agent.ps1'

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Backup launcher is missing: $launcher"
}
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $baselineFile -PathType Leaf)) {
    throw "Reviewed deployment baseline is missing: $baselineFile"
}

function Quote-TaskArgument([string]$Value) {
    if ($Value.Contains('"')) {
        throw 'A scheduled-task argument contains an unsupported quote.'
    }
    return '"' + $Value + '"'
}

$arguments = @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    (Quote-TaskArgument $launcher),
    '-DestinationRoot',
    (Quote-TaskArgument $destinationRoot),
    '-BaselineFile',
    (Quote-TaskArgument $baselineFile),
    '-SshTarget',
    (Quote-TaskArgument $SshTarget),
    '-TargetId',
    (Quote-TaskArgument $resolvedTargetId)
) -join ' '

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

if ($PSCmdlet.ShouldProcess($TaskName, "Register backup agent for $destinationRoot")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
}

[pscustomobject]@{
    Status = 'INSTALLED'
    TaskName = $TaskName
    TargetId = $resolvedTargetId
    DriveLetter = $drive
    DestinationRoot = $destinationRoot
    BaselineFile = $baselineFile
    SshTarget = $SshTarget
} | ConvertTo-Json -Compress
