<#
.SYNOPSIS
  Preview exclusive catalog model weight files, then ask for confirmation. Never removes environments or media.
#>
param(
  [Parameter(Mandatory=$true)]
  [string]$Model,
  [string]$Models = '',
  [switch]$PlanOnly
)
$ErrorActionPreference = 'Stop'
$Python = Join-Path $env:LOCALAPPDATA 'ImaginationStudio\ui-venv\Scripts\python.exe'
$PythonArgs = @()
if (-not (Test-Path -LiteralPath $Python)) {
  $Python = (Get-Command py.exe -ErrorAction Stop).Source
  $PythonArgs = @('-3.13')
}
$Helper = Join-Path (Split-Path $PSScriptRoot -Parent) 'app\windows_model_remove.py'
$Args_ = @('-X','utf8',$Helper,'--model',$Model)
if ($Models) { $Args_ += @('--models',$Models) }
if ($PlanOnly) { $Args_ += '--plan-only' }
& $Python @PythonArgs @Args_
if ($LASTEXITCODE -ne 0) { throw 'Model removal did not complete; see the error above.' }
