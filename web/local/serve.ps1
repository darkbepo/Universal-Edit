# Serves the Universal Edit web app on this computer only (http://localhost), with the headers
# the video engine needs, then opens it in your browser. Close this window to stop it.
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot 'app'
if (-not (Test-Path (Join-Path $root 'index.html'))) { Write-Host "Can't find the 'app' folder next to this script."; exit 1 }
$rootFull = [IO.Path]::GetFullPath($root)

$mime = @{
  '.html' = 'text/html; charset=utf-8'; '.js' = 'text/javascript; charset=utf-8'; '.mjs' = 'text/javascript; charset=utf-8'
  '.css' = 'text/css; charset=utf-8'; '.wasm' = 'application/wasm'; '.json' = 'application/json'
  '.png' = 'image/png'; '.svg' = 'image/svg+xml'; '.ico' = 'image/x-icon'; '.txt' = 'text/plain; charset=utf-8'
}

$listener = $null
foreach ($port in 8790..8799) {
  $l = New-Object System.Net.HttpListener
  $l.Prefixes.Add("http://localhost:$port/")
  try { $l.Start(); $listener = $l; break } catch { $l.Close() }
}
if (-not $listener) { Write-Host 'No free port between 8790 and 8799.'; exit 1 }
$url = "http://localhost:$port/"

Write-Host ''
Write-Host "  Universal Edit is running at $url" -ForegroundColor Green
Write-Host '  Your files stay on this computer. Keep this window open while you use it;'
Write-Host '  close it (or press Ctrl+C) to stop.'
Write-Host ''
if (-not $env:UMC_NO_BROWSER) { Start-Process $url }

try {
  while ($listener.IsListening) {
    $task = $listener.GetContextAsync()
    while (-not $task.AsyncWaitHandle.WaitOne(500)) { }  # stays responsive to Ctrl+C
    $ctx = $task.GetAwaiter().GetResult()
    $res = $ctx.Response
    try {
      $rel = [Uri]::UnescapeDataString($ctx.Request.Url.AbsolutePath).TrimStart('/')
      if ($rel -eq '' -or $rel.EndsWith('/')) { $rel += 'index.html' }
      $path = [IO.Path]::GetFullPath((Join-Path $rootFull $rel))
      $res.Headers.Add('Cross-Origin-Opener-Policy', 'same-origin')
      $res.Headers.Add('Cross-Origin-Embedder-Policy', 'require-corp')
      $res.Headers.Add('Cross-Origin-Resource-Policy', 'same-origin')
      $res.Headers.Add('X-Content-Type-Options', 'nosniff')
      if (-not $path.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $res.StatusCode = 404
      } else {
        $ext = [IO.Path]::GetExtension($path).ToLowerInvariant()
        $res.ContentType = if ($mime.ContainsKey($ext)) { $mime[$ext] } else { 'application/octet-stream' }
        if ($ext -eq '.html') { $res.Headers.Add('Cache-Control', 'no-cache') }
        $fs = [IO.File]::OpenRead($path)
        try { $res.ContentLength64 = $fs.Length; $fs.CopyTo($res.OutputStream) } finally { $fs.Dispose() }
      }
    } catch {
      # The browser closed the connection early, etc. Keep serving.
    } finally {
      try { $res.Close() } catch { }
    }
  }
} finally {
  $listener.Stop()
  $listener.Close()
}
