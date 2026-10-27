[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$DestinationDirectory = [Environment]::GetFolderPath('DesktopDirectory'),
    [string]$Database,
    [ValidatePattern('^[^<>:"/\\|?*]+$')]
    [string]$ShortcutName = 'Controle de finanças'
)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not $Database) { $Database = Join-Path $projectRoot 'data\finance-active.db' }
$databasePath = if ([IO.Path]::IsPathRooted($Database)) { [IO.Path]::GetFullPath($Database) } else { [IO.Path]::GetFullPath((Join-Path $projectRoot $Database)) }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Python da .venv não encontrado.' }
if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) { throw 'Banco não encontrado; o atalho não prepara bancos.' }
if (-not (Test-Path -LiteralPath $DestinationDirectory -PathType Container)) { throw 'A pasta de destino precisa existir.' }
$shortcutPath = Join-Path ([IO.Path]::GetFullPath($DestinationDirectory)) ($ShortcutName + '.lnk')
$arguments = '-m app.launch --wait-on-error --database "' + $databasePath + '"'
$shellObject = New-Object -ComObject WScript.Shell
try {
    if (Test-Path -LiteralPath $shortcutPath) {
        $existingShortcut = $shellObject.CreateShortcut($shortcutPath)
        if ($existingShortcut.TargetPath -ne $pythonPath -or $existingShortcut.Arguments -ne $arguments) {
            throw 'Já existe outro atalho com esse nome; ele foi preservado.'
        }
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($existingShortcut) | Out-Null
    } elseif ($PSCmdlet.ShouldProcess($shortcutPath, 'Criar atalho local para a aplicação')) {
        $shortcut = $shellObject.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $pythonPath
        $shortcut.Arguments = $arguments
        $shortcut.WorkingDirectory = $projectRoot
        $shortcut.IconLocation = $pythonPath + ',0'
        $shortcut.Description = 'Controle financeiro local. Mantenha o terminal aberto; encerre com Ctrl+C.'
        $shortcut.Save()
        [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) | Out-Null
    }
    [pscustomobject]@{ Path=$shortcutPath; Target=$pythonPath; Arguments=$arguments; WorkingDirectory=$projectRoot }
} finally {
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shellObject) | Out-Null
}
