#Requires -Version 5.1
<#
.SYNOPSIS
  为 master 分支配置 GitHub 分支保护（需已安装 gh 且 git 已保存 GitHub 凭据）

.USAGE
  powershell -ExecutionPolicy Bypass -File scripts/setup_branch_protection.ps1
#>

$ErrorActionPreference = "Stop"

function Get-GitHubTokenFromCredentialManager {
    $input = "protocol=https`nhost=github.com`n"
    $output = $input | git credential fill 2>$null
    if (-not $output) { throw "无法从 git credential 读取 GitHub 凭据，请先 git push 一次完成登录" }
    foreach ($line in $output -split "`n") {
        if ($line -match '^password=(.+)$') { return $Matches[1] }
    }
    throw "git credential 未返回 token"
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "未找到 gh CLI，请运行: winget install GitHub.cli"
}

$token = Get-GitHubTokenFromCredentialManager
$env:GH_TOKEN = $token
$token | gh auth login --with-token | Out-Null

$payload = @{
    required_status_checks = @{
        strict = $true
        checks = @(@{ context = "test"; app_id = $null })
    }
    enforce_admins = $false
    required_pull_request_reviews = @{
        dismiss_stale_reviews = $false
        require_code_owner_reviews = $false
        required_approving_review_count = 0
    }
    restrictions = $null
    required_linear_history = $false
    allow_force_pushes = $false
    allow_deletions = $false
    block_creations = $false
    required_conversation_resolution = $false
} | ConvertTo-Json -Depth 6

$tmp = Join-Path $env:TEMP "devflow-branch-protection.json"
$payload | Set-Content -Path $tmp -Encoding UTF8

try {
    gh api --method PUT repos/iokcloud/DevFlowCI/branches/master/protection --input $tmp
    Write-Host ""
    Write-Host "分支保护已配置: master"
    Write-Host "  - 必须通过 CI 检查: test"
    Write-Host "  - 必须通过 Pull Request（无需他人 approve）"
    Write-Host "  - 禁止 force push / 删除分支"
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}
