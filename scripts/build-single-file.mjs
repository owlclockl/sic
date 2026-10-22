import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(__dirname, '..');
const DIST_DIR = path.join(ROOT_DIR, 'dist');
const OUTPUT_CMD = path.join(ROOT_DIR, 'RevealAtlas.cmd');

import { renderAppIconPng, crc32, pngChunk, encodeRgbaPng } from './lib/app-icon.mjs';

function buildIcoFromPngs(entries) {
  const count = entries.length;
  const header = Buffer.alloc(6 + count * 16);
  header.writeUInt16LE(0, 0); // Reserved
  header.writeUInt16LE(1, 2); // Type = 1 (ICO)
  header.writeUInt16LE(count, 4);

  let offset = header.length;
  const buffers = [header];

  entries.forEach(({ size, png }, i) => {
    const pos = 6 + i * 16;
    header[pos] = size >= 256 ? 0 : size;
    header[pos + 1] = size >= 256 ? 0 : size;
    header[pos + 2] = 0; // Color palette
    header[pos + 3] = 0; // Reserved
    header.writeUInt16LE(1, pos + 4);  // Color planes
    header.writeUInt16LE(32, pos + 6); // Bits per pixel
    header.writeUInt32LE(png.length, pos + 8);
    header.writeUInt32LE(offset, pos + 12);
    offset += png.length;
    buffers.push(png);
  });

  return Buffer.concat(buffers);
}

// --- Inline Vite Build into Single HTML ---

function buildSelfContainedHtml() {
  const indexPath = path.join(DIST_DIR, 'index.html');
  if (!fs.existsSync(indexPath)) {
    throw new Error('dist/index.html not found. Run vite build first.');
  }

  let html = fs.readFileSync(indexPath, 'utf8');

  const stylesheetRe = /<link\b[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi;
  const cssBlocks = [];
  html = html.replace(stylesheetRe, (_full, href) => {
    const cleanRel = href.replace(/^\//, '');
    const cssPath = path.join(DIST_DIR, cleanRel);
    const css = fs.readFileSync(cssPath, 'utf8').replace(/<\/(style)/gi, '<\\/$1');
    cssBlocks.push(`<style>${css}</style>`);
    return '';
  });

  const scriptRe = /<script\b[^>]*src=["']([^"']+)["'][^>]*><\/script>/gi;
  const jsBlocks = [];
  html = html.replace(scriptRe, (_full, src) => {
    const cleanRel = src.replace(/^\//, '');
    const jsPath = path.join(DIST_DIR, cleanRel);
    const js = fs.readFileSync(jsPath, 'utf8').replace(/<\/(script)/gi, '<\\/$1');
    jsBlocks.push(`<script type="module">${js}</script>`);
    return '';
  });

  // favicon и manifest уже подключены в index.html, здесь только инлайн-CSS.
  const extraHeadLinks = [
    '<link rel="icon" href="/favicon.ico" sizes="any" />',
    ...cssBlocks,
  ].join('\n    ');

  html = html.replace('</head>', () => `    ${extraHeadLinks}\n  </head>`);
  html = html.replace('</body>', () => `    ${jsBlocks.join('\n    ')}\n  </body>`);
  return html;
}

function gzipBase64Block(tag, buffer) {
  const gz = zlib.gzipSync(buffer, { level: 9 });
  const b64 = gz.toString('base64');
  const lines = [`:: === BEGIN_${tag} ===`];
  for (let i = 0; i < b64.length; i += 100) {
    lines.push(b64.slice(i, i + 100));
  }
  lines.push(`:: === END_${tag} ===`);
  return lines.join('\r\n');
}

// --- Windows Single-File Launcher + Embedded C# Local Host ---

function buildWindowsCmd(payloadBlocks) {
  const batchHeader = [
    '@echo off',
    'setlocal DisableDelayedExpansion',
    'set "RA_SELF=%~f0"',
    'set "RA_MODE=%~1"',
    'if /i "%~1"=="--debug" goto :RA_FG',
    'if /i "%~1"=="--export-html" goto :RA_FG',
    'start "" /min powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:RA_SELF,[Text.Encoding]::UTF8);$a=\'###PS_\'+\'BEGIN###\';$b=\'###PS_\'+\'END###\';$i=$s.IndexOf($a)+$a.Length;$j=$s.IndexOf($b,$i);iex $s.Substring($i,$j-$i)"',
    'exit /b 0',
    ':RA_FG',
    'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:RA_SELF,[Text.Encoding]::UTF8);$a=\'###PS_\'+\'BEGIN###\';$b=\'###PS_\'+\'END###\';$i=$s.IndexOf($a)+$a.Length;$j=$s.IndexOf($b,$i);iex $s.Substring($i,$j-$i)"',
    'if /i "%~1"=="--debug" pause',
    'exit /b 0',
  ].join('\r\n');

  const psScript = String.raw`###PS_BEGIN###
$ErrorActionPreference = 'Stop'

function Unpack-Payload([string]$fullText, [string]$name) {
  $startTag = ':: === BEGIN_' + $name + ' ==='
  $endTag = ':: === END_' + $name + ' ==='
  $p0 = $fullText.IndexOf($startTag)
  if ($p0 -lt 0) { throw ('Missing payload: ' + $name) }
  $p0 += $startTag.Length
  $p1 = $fullText.IndexOf($endTag, $p0)
  if ($p1 -lt 0) { throw ('Corrupted payload: ' + $name) }
  $b64 = $fullText.Substring($p0, $p1 - $p0)
  $gzBytes = [Convert]::FromBase64String($b64)
  $inMs = New-Object System.IO.MemoryStream(,$gzBytes)
  $gz = New-Object System.IO.Compression.GZipStream($inMs, [System.IO.Compression.CompressionMode]::Decompress)
  $outMs = New-Object System.IO.MemoryStream
  $buf = New-Object byte[] 65536
  while (($read = $gz.Read($buf, 0, $buf.Length)) -gt 0) {
    $outMs.Write($buf, 0, $read)
  }
  $gz.Dispose()
  $inMs.Dispose()
  $res = $outMs.ToArray()
  $outMs.Dispose()
  return ,$res
}

function Find-ChromiumBrowsers {
  $raw = New-Object System.Collections.ArrayList

  $regNames = @('msedge.exe', 'chrome.exe', 'browser.exe', 'brave.exe', 'vivaldi.exe', 'opera.exe')
  foreach ($exe in $regNames) {
    foreach ($hive in @('HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\', 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\')) {
      $regPath = $hive + $exe
      if (Test-Path -LiteralPath $regPath) {
        try {
          $val = (Get-ItemProperty -LiteralPath $regPath -ErrorAction SilentlyContinue).'(default)'
          if ($val) { [void]$raw.Add([string]$val) }
        } catch {}
      }
    }
  }

  $candidates = @()
  $pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
  if ($pf86) {
    $candidates += [IO.Path]::Combine($pf86, 'Microsoft\Edge\Application\msedge.exe')
    $candidates += [IO.Path]::Combine($pf86, 'Google\Chrome\Application\chrome.exe')
    $candidates += [IO.Path]::Combine($pf86, 'BraveSoftware\Brave-Browser\Application\brave.exe')
  }
  if ($env:ProgramFiles) {
    $candidates += [IO.Path]::Combine($env:ProgramFiles, 'Microsoft\Edge\Application\msedge.exe')
    $candidates += [IO.Path]::Combine($env:ProgramFiles, 'Google\Chrome\Application\chrome.exe')
    $candidates += [IO.Path]::Combine($env:ProgramFiles, 'BraveSoftware\Brave-Browser\Application\brave.exe')
    $candidates += [IO.Path]::Combine($env:ProgramFiles, 'Opera\opera.exe')
  }
  if ($env:LOCALAPPDATA) {
    $candidates += [IO.Path]::Combine($env:LOCALAPPDATA, 'Microsoft\Edge\Application\msedge.exe')
    $candidates += [IO.Path]::Combine($env:LOCALAPPDATA, 'Google\Chrome\Application\chrome.exe')
    $candidates += [IO.Path]::Combine($env:LOCALAPPDATA, 'Yandex\YandexBrowser\Application\browser.exe')
    $candidates += [IO.Path]::Combine($env:LOCALAPPDATA, 'BraveSoftware\Brave-Browser\Application\brave.exe')
    $candidates += [IO.Path]::Combine($env:LOCALAPPDATA, 'Vivaldi\Application\vivaldi.exe')
    $candidates += [IO.Path]::Combine($env:LOCALAPPDATA, 'Programs\Opera\opera.exe')
  }
  foreach ($p in $candidates) { [void]$raw.Add($p) }

  $result = New-Object System.Collections.ArrayList
  $seen = @{}
  foreach ($p in $raw) {
    if ([string]::IsNullOrEmpty($p)) { continue }
    try {
      if (-not (Test-Path -LiteralPath $p)) { continue }
      $full = (Get-Item -LiteralPath $p).FullName
    } catch {
      continue
    }
    $key = $full.ToLowerInvariant()
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    [void]$result.Add($full)
  }
  return $result
}

function Start-AppWindow([string]$browserExe, [string]$url, [string]$dataDir) {
  # One profile per browser: Chromium variants do not always accept a user-data-dir
  # written by another (newer) sibling, and the next candidate must start clean.
  $profileName = 'window-profile'
  if ($browserExe) {
    $exeName = [IO.Path]::GetFileNameWithoutExtension($browserExe)
    $profileName = 'window-profile-' + $exeName.ToLowerInvariant()
  }
  $profileDir = [IO.Path]::Combine($dataDir, $profileName)
  [IO.Directory]::CreateDirectory($profileDir) | Out-Null
  $cmdArgs = '--user-data-dir="' + $profileDir + '" --app="' + $url + '" --window-size=1380,880 --no-first-run --no-default-browser-check --disable-features=Translate,msEdgeSidebarV2,msHubApps,EdgeCollections --disable-extensions --disable-sync --disable-background-mode'
  if ($browserExe) {
    return Start-Process -FilePath $browserExe -ArgumentList $cmdArgs -PassThru -ErrorAction Stop
  }

  # Default handler: some shell handlers do not expose a process handle, so the
  # launch itself is retried without -PassThru and reported as 'started'.
  $proc = $null
  try {
    $proc = Start-Process -FilePath $url -PassThru -ErrorAction Stop
  } catch {
    $proc = $null
  }
  if (-not $proc) {
    Start-Process -FilePath $url -ErrorAction Stop | Out-Null
    return $true
  }
  return $proc
}

function Test-ProcessExited($proc) {
  if (-not $proc) { return $false }
  if ($proc -isnot [System.Diagnostics.Process]) { return $false }
  try { return $proc.HasExited } catch { return $false }
}

function Wait-ForAppConnect($server, $proc, [int]$timeoutMs) {
  $deadline = [DateTime]::UtcNow.AddMilliseconds($timeoutMs)
  while ([DateTime]::UtcNow -lt $deadline) {
    if ($server.HasConnected) { return 'connected' }
    if (Test-ProcessExited $proc) { return 'exited' }
    Start-Sleep -Milliseconds 120
  }
  if ($server.HasConnected) { return 'connected' }
  if (Test-ProcessExited $proc) { return 'exited' }
  return 'timeout'
}

# Tries every installed Chromium browser until one of them really opens the app
# window (a browser that cannot start, e.g. with a broken side-by-side manifest,
# is skipped instead of failing the whole launch).
# Returns $null on success, otherwise a short report of what was attempted.
function Launch-AppWindow([string]$url, [string]$dataDir, $server) {
  $notes = New-Object System.Collections.ArrayList
  foreach ($exe in @(Find-ChromiumBrowsers)) {
    if ([string]::IsNullOrEmpty($exe)) { continue }
    $proc = $null
    try {
      $proc = Start-AppWindow $exe $url $dataDir
    } catch {
      [void]$notes.Add([IO.Path]::GetFileName($exe) + ' -> ' + $_.Exception.Message)
      continue
    }
    if (-not $server) { return $null }
    $state = Wait-ForAppConnect $server $proc 20000
    if ($state -eq 'connected') { return $null }
    if ($state -eq 'timeout' -and ($proc -is [System.Diagnostics.Process]) -and (-not (Test-ProcessExited $proc))) {
      try { $proc.Kill() } catch {}
    }
    [void]$notes.Add([IO.Path]::GetFileName($exe) + ' -> ' + $state)
  }

  # Last resort: let Windows open the URL in the default browser.
  $proc = $null
  try {
    $proc = Start-AppWindow '' $url $dataDir
  } catch {
    [void]$notes.Add('default browser -> ' + $_.Exception.Message)
  }
  if ($proc -and (-not $server)) { return $null }
  if ($proc) {
    $state = Wait-ForAppConnect $server $proc 25000
    if ($state -eq 'connected') { return $null }
    [void]$notes.Add('default browser -> ' + $state)
  }
  return ($notes -join '; ')
}

function Show-LaunchFailure([string]$detail, [string]$url) {
  # Message text is Russian, so it is embedded as base64 to keep this file ASCII-only.
  $tplB64 = @(
    'UmV2ZWFsIEF0bGFzINGB0LzQvtCzINC30LDQv9GD0YHRgtC40YLRjNGB0Y8sINC90L4gV2luZG93cyDQvdC1INC00LDQu9Cw'
    'INC+0YLQutGA0YvRgtGMINC+0LrQvdC+INC/0YDQvtCz0YDQsNC80LzRizoK0LHRgNCw0YPQt9C10YAsINCyINC60L7RgtC+'
    '0YDQvtC8INC00L7Qu9C20L3QviDQvtGC0LrRgNGL0YLRjNGB0Y8g0L7QutC90L4sINC90LUg0YHRgtCw0YDRgtGD0LXRgi4K'
    'CtCn0LDRidC1INCy0YHQtdCz0L4g0Y3RgtC+INC+0YjQuNCx0LrQsCDCq3NpZGUtYnktc2lkZSBjb25maWd1cmF0aW9uIGlz'
    'IGluY29ycmVjdMK7ICjQutC+0LQgMTQwMDEpCtCyINGB0LDQvNC+0Lwg0LHRgNCw0YPQt9C10YDQtSAo0L7QsdGL0YfQvdC+'
    'INCyIE1pY3Jvc29mdCBFZGdlKSwg0LAg0L3QtSDQsiBSZXZlYWwgQXRsYXMuCgrQp9GC0L4g0LzQvtC20L3QviDRgdC00LXQ'
    'u9Cw0YLRjDoKMS4g0J7RgtC60YDQvtC50YLQtSDQv9GA0L7Qs9GA0LDQvNC80YMg0LLRgNGD0YfQvdGD0Y4g0LIg0LvRjtCx'
    '0L7QvCDQsdGA0LDRg9C30LXRgNC1OgogICB7dXJsfQoyLiDQntCx0L3QvtCy0LjRgtC1INC40LvQuCDQv9C10YDQtdGD0YHR'
    'gtCw0L3QvtCy0LjRgtC1IE1pY3Jvc29mdCBFZGdlLCDQu9C40LHQviDRg9GB0YLQsNC90L7QstC40YLQtSBHb29nbGUgQ2hy'
    'b21lCiAgINC40LvQuCDQr9C90LTQtdC60YEu0JHRgNCw0YPQt9C10YAg0Lgg0LfQsNC/0YPRgdGC0LjRgtC1IFJldmVhbEF0'
    'bGFzLmNtZCDQt9Cw0L3QvtCy0L4uCjMuINCf0L7QtNGA0L7QsdC90LDRjyDQtNC40LDQs9C90L7RgdGC0LjQutCwOiBSZXZl'
    'YWxBdGxhcy5jbWQgLS1kZWJ1ZwoK0J/QvtC/0YvRgtC60Lgg0LfQsNC/0YPRgdC60LA6IHtkZXRhaWx9'
  ) -join ''
  $tpl = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($tplB64))
  $body = $tpl.Replace('{url}', $url).Replace('{detail}', $detail)
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
      $body,
      'Reveal Atlas',
      [System.Windows.Forms.MessageBoxButtons]::OK,
      [System.Windows.Forms.MessageBoxIcon]::Warning
    ) | Out-Null
  } catch {}
}

try {
  $selfPath = $env:RA_SELF
  $mode = $env:RA_MODE
  $rawSelf = [IO.File]::ReadAllText($selfPath, [Text.Encoding]::UTF8)

  if ($mode -ieq '--export-html') {
    $htmlRaw = Unpack-Payload $rawSelf 'HTML'
    $outHtml = [IO.Path]::Combine([IO.Path]::GetDirectoryName($selfPath), 'RevealAtlas.html')
    [IO.File]::WriteAllBytes($outHtml, $htmlRaw)
    Write-Host ('Saved standalone HTML: ' + $outHtml)
    exit 0
  }

  $cmdDir = [IO.Path]::GetDirectoryName($selfPath)
  $portableDir = [IO.Path]::Combine($cmdDir, 'RevealAtlas-Data')
  $portable = ($mode -ieq '--portable') -or ([IO.Directory]::Exists($portableDir))

  if ($portable) {
    $dataDir = $portableDir
  } else {
    $localAppData = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrEmpty($localAppData)) {
      $dataDir = $portableDir
      $portable = $true
    } else {
      $dataDir = [IO.Path]::Combine($localAppData, 'RevealAtlas')
    }
  }

  [IO.Directory]::CreateDirectory($dataDir) | Out-Null
  [IO.Directory]::CreateDirectory([IO.Path]::Combine($dataDir, 'blobs')) | Out-Null

  $instanceFile = [IO.Path]::Combine($dataDir, 'instance.json')
  if (Test-Path -LiteralPath $instanceFile) {
    try {
      $instText = [IO.File]::ReadAllText($instanceFile, [Text.Encoding]::UTF8)
      if ($instText -match '"port"\s*:\s*(\d+)') {
        $existingPort = [int]$Matches[1]
        $probe = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:' + $existingPort + '/api/health')
        $probe.Timeout = 450
        $probe.ReadWriteTimeout = 450
        $resp = [System.Net.HttpWebResponse]$probe.GetResponse()
        $statusOk = ([int]$resp.StatusCode -eq 200)
        $resp.Close()
        if ($statusOk) {
          $focused = $false
          try {
            Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class RaWin { [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h); }'
            $procs = Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like '*Reveal Atlas*' }
            foreach ($p in $procs) {
              if ([RaWin]::IsIconic($p.MainWindowHandle)) {
                [RaWin]::ShowWindowAsync($p.MainWindowHandle, 9) | Out-Null
              }
              [RaWin]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
              $focused = $true
              break
            }
          } catch {}
          if (-not $focused) {
            $existingUrl = 'http://127.0.0.1:' + $existingPort + '/'
            $detail = Launch-AppWindow $existingUrl $dataDir $null
            if ($detail) {
              if ($env:RA_MODE -ieq '--debug') {
                Write-Warning ('Unable to open the application window: ' + $detail)
                Write-Host ('Open this URL manually: ' + $existingUrl)
              } else {
                Show-LaunchFailure $detail $existingUrl
              }
            }
          }
          exit 0
        }
      }
    } catch {}
  }

  $csCode = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;

public sealed class RevealAtlasHost {
    private readonly TcpListener _listener;
    private readonly int _port;
    private readonly string _token;
    private readonly string _dataDir;
    private readonly string _blobsDir;
    private readonly string _stateFile;
    private readonly string _backupFile;
    private readonly string _icoFile;
    private readonly string _selfCmdPath;
    private readonly byte[] _htmlBytes;
    private readonly byte[] _icoBytes;
    private readonly byte[] _png192Bytes;
    private readonly byte[] _png256Bytes;
    private readonly byte[] _manifestBytes;
    private readonly object _fileLock = new object();

    private volatile bool _running = true;
    private volatile bool _everConnected = false;
    private volatile bool _appLoaded = false;
    private volatile bool _byeReceived = false;
    private int _activeSessions = 0;
    private int _inFlightRequests = 0;
    private long _lastDisconnectUtcTicks = 0;

    public int Port { get { return _port; } }

    // True as soon as a real browser window has loaded the app (the SSE session may
    // still be connecting), which is what the launcher waits for.
    public bool HasConnected { get { return _everConnected || _appLoaded; } }

    public static RevealAtlasHost Create(
        string token,
        string dataDir,
        string selfCmdPath,
        byte[] htmlBytes,
        byte[] icoBytes,
        byte[] png192Bytes,
        byte[] png256Bytes,
        byte[] manifestBytes
    ) {
        return new RevealAtlasHost(token, dataDir, selfCmdPath, htmlBytes, icoBytes, png192Bytes, png256Bytes, manifestBytes);
    }

    public RevealAtlasHost(
        string token,
        string dataDir,
        string selfCmdPath,
        byte[] htmlBytes,
        byte[] icoBytes,
        byte[] png192Bytes,
        byte[] png256Bytes,
        byte[] manifestBytes
    ) {
        _token = token;
        _dataDir = dataDir;
        _blobsDir = Path.Combine(dataDir, "blobs");
        _stateFile = Path.Combine(dataDir, "state.json");
        _backupFile = Path.Combine(dataDir, "state.backup.json");
        _icoFile = Path.Combine(dataDir, "RevealAtlas.ico");
        _selfCmdPath = selfCmdPath;
        _htmlBytes = htmlBytes;
        _icoBytes = icoBytes;
        _png192Bytes = png192Bytes;
        _png256Bytes = png256Bytes;
        _manifestBytes = manifestBytes;

        int[] candidatePorts = new int[] { 45731, 45732, 45733, 45734, 45735, 0 };
        TcpListener bound = null;
        for (int i = 0; i < candidatePorts.Length; i++) {
            try {
                TcpListener candidate = new TcpListener(IPAddress.Loopback, candidatePorts[i]);
                candidate.Start();
                bound = candidate;
                break;
            } catch {
            }
        }
        if (bound == null) {
            throw new InvalidOperationException("Unable to bind loopback port.");
        }
        _listener = bound;
        _port = ((IPEndPoint)_listener.LocalEndpoint).Port;
    }

    public void Start() {
        Thread acceptThread = new Thread(AcceptLoop);
        acceptThread.IsBackground = true;
        acceptThread.Start();
    }

    public void Stop() {
        _running = false;
        try { _listener.Stop(); } catch { }
    }

    public void WaitUntilFinished(int startupTimeoutSeconds) {
        DateTime bootUtc = DateTime.UtcNow;
        while (_running) {
            Thread.Sleep(350);
            if (!_everConnected) {
                if ((DateTime.UtcNow - bootUtc).TotalSeconds > startupTimeoutSeconds) {
                    break;
                }
                continue;
            }

            int active = Interlocked.CompareExchange(ref _activeSessions, 0, 0);
            int inFlight = Interlocked.CompareExchange(ref _inFlightRequests, 0, 0);
            if (active <= 0 && inFlight <= 0) {
                long lastTicks = Interlocked.Read(ref _lastDisconnectUtcTicks);
                if (lastTicks > 0) {
                    double idleSeconds = (DateTime.UtcNow.Ticks - lastTicks) / 10000000.0;
                    double grace = _byeReceived ? 2.5 : 4.5;
                    if (idleSeconds >= grace) {
                        lock (_fileLock) {
                            break;
                        }
                    }
                }
            }
        }
    }

    private void AcceptLoop() {
        while (_running) {
            TcpClient client = null;
            try {
                client = _listener.AcceptTcpClient();
                ThreadPool.QueueUserWorkItem(HandleClient, client);
            } catch {
                if (client != null) {
                    try { client.Close(); } catch { }
                }
                if (!_running) break;
            }
        }
    }

    private void HandleClient(object state) {
        TcpClient client = (TcpClient)state;
        bool isSse = false;
        Interlocked.Increment(ref _inFlightRequests);
        try {
            client.NoDelay = true;
            client.ReceiveTimeout = 30000;
            client.SendTimeout = 30000;
            using (NetworkStream stream = client.GetStream()) {
                string method;
                string rawTarget;
                Dictionary<string, string> headers;
                byte[] body;
                if (!TryReadHttpRequest(stream, out method, out rawTarget, out headers, out body)) {
                    return;
                }

                string path = rawTarget;
                string query = "";
                int qIndex = rawTarget.IndexOf('?');
                if (qIndex >= 0) {
                    path = rawTarget.Substring(0, qIndex);
                    query = rawTarget.Substring(qIndex + 1);
                }

                if (method == "GET" && (path == "/" || path == "/index.html")) {
                    _appLoaded = true;
                }

                if (path == "/api/session" && method == "GET") {
                    isSse = true;
                    Interlocked.Decrement(ref _inFlightRequests);
                    HandleSseSession(client, stream, query);
                    return;
                }

                RouteRequest(stream, method, path, query, headers, body);
            }
        } catch {
        } finally {
            if (!isSse) {
                Interlocked.Decrement(ref _inFlightRequests);
            }
            try { client.Close(); } catch { }
        }
    }

    private void HandleSseSession(TcpClient client, NetworkStream stream, string query) {
        string qToken = GetQueryParam(query, "t");
        if (!string.Equals(qToken, _token, StringComparison.Ordinal)) {
            WriteResponse(stream, 403, "Forbidden", "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Forbidden"));
            return;
        }

        _everConnected = true;
        _byeReceived = false;
        Interlocked.Increment(ref _activeSessions);
        try {
            string sseHead =
                "HTTP/1.1 200 OK\r\n" +
                "Content-Type: text/event-stream; charset=utf-8\r\n" +
                "Cache-Control: no-cache, no-store\r\n" +
                "Connection: keep-alive\r\n\r\n" +
                ": connected\n\ndata: ok\n\n";
            byte[] headBytes = Encoding.ASCII.GetBytes(sseHead);
            stream.Write(headBytes, 0, headBytes.Length);
            stream.Flush();

            byte[] pingBytes = Encoding.ASCII.GetBytes(": ping\n\n");
            byte[] drainBuf = new byte[64];
            int ticks = 0;
            Socket sock = client.Client;

            while (_running) {
                bool readable = sock.Poll(1000000, SelectMode.SelectRead);
                if (readable) {
                    int avail = sock.Available;
                    if (avail <= 0) {
                        break;
                    }
                    int r = sock.Receive(drainBuf, 0, Math.Min(drainBuf.Length, avail), SocketFlags.None);
                    if (r <= 0) {
                        break;
                    }
                }
                ticks++;
                if (ticks >= 20) {
                    ticks = 0;
                    stream.Write(pingBytes, 0, pingBytes.Length);
                    stream.Flush();
                }
            }
        } catch {
        } finally {
            Interlocked.Exchange(ref _lastDisconnectUtcTicks, DateTime.UtcNow.Ticks);
            Interlocked.Decrement(ref _activeSessions);
        }
    }

    private bool IsAuthorized(Dictionary<string, string> headers, string query) {
        string origin;
        if (headers.TryGetValue("origin", out origin) && !string.IsNullOrEmpty(origin)) {
            string allowed1 = "http://127.0.0.1:" + _port;
            string allowed2 = "http://localhost:" + _port;
            if (!string.Equals(origin, allowed1, StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(origin, allowed2, StringComparison.OrdinalIgnoreCase)) {
                return false;
            }
        }
        string headerToken;
        if (headers.TryGetValue("x-atlas-token", out headerToken)) {
            if (string.Equals(headerToken, _token, StringComparison.Ordinal)) return true;
        }
        string qToken = GetQueryParam(query, "t");
        return string.Equals(qToken, _token, StringComparison.Ordinal);
    }

    private void RouteRequest(
        NetworkStream stream,
        string method,
        string path,
        string query,
        Dictionary<string, string> headers,
        byte[] body
    ) {
        if ((method == "GET" || method == "HEAD") && (path == "/" || path == "/index.html")) {
            WriteResponse(stream, 200, "OK", "text/html; charset=utf-8", method == "HEAD" ? null : _htmlBytes);
            return;
        }

        if ((method == "GET" || method == "HEAD") && path == "/favicon.ico") {
            WriteResponse(stream, 200, "OK", "image/x-icon", method == "HEAD" ? null : _icoBytes);
            return;
        }

        if ((method == "GET" || method == "HEAD") && path == "/icon-192.png") {
            WriteResponse(stream, 200, "OK", "image/png", method == "HEAD" ? null : _png192Bytes);
            return;
        }

        if ((method == "GET" || method == "HEAD") && path == "/icon-256.png") {
            WriteResponse(stream, 200, "OK", "image/png", method == "HEAD" ? null : _png256Bytes);
            return;
        }

        if ((method == "GET" || method == "HEAD") && path == "/manifest.webmanifest") {
            WriteResponse(stream, 200, "OK", "application/manifest+json; charset=utf-8", method == "HEAD" ? null : _manifestBytes);
            return;
        }

        if (method == "GET" && path == "/api/health") {
            WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true}"));
            return;
        }

        if (!IsAuthorized(headers, query)) {
            WriteResponse(stream, 403, "Forbidden", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"error\":\"forbidden\"}"));
            return;
        }

        if (path == "/api/bye" && method == "POST") {
            _byeReceived = true;
            Interlocked.Exchange(ref _lastDisconnectUtcTicks, DateTime.UtcNow.Ticks);
            WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true}"));
            return;
        }

        if (path == "/api/state") {
            if (method == "GET") {
                byte[] data = ReadStateBytes();
                if (data == null || data.Length == 0) {
                    WriteResponse(stream, 204, "No Content", "text/plain", new byte[0]);
                } else {
                    WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", data);
                }
                return;
            }
            if (method == "PUT") {
                if (body == null || body.Length < 2) {
                    WriteResponse(stream, 400, "Bad Request", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"error\":\"empty\"}"));
                    return;
                }
                int firstNonWs = 0;
                while (firstNonWs < body.Length && (body[firstNonWs] == 32 || body[firstNonWs] == 9 || body[firstNonWs] == 13 || body[firstNonWs] == 10)) firstNonWs++;
                int lastNonWs = body.Length - 1;
                while (lastNonWs >= 0 && (body[lastNonWs] == 32 || body[lastNonWs] == 9 || body[lastNonWs] == 13 || body[lastNonWs] == 10)) lastNonWs--;
                if (firstNonWs >= body.Length || body[firstNonWs] != (byte)'{' || body[lastNonWs] != (byte)'}') {
                    WriteResponse(stream, 400, "Bad Request", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"error\":\"invalid_json\"}"));
                    return;
                }
                lock (_fileLock) {
                    SaveFileAtomic(_stateFile, _backupFile, body);
                }
                WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true}"));
                return;
            }
        }

        if (path.StartsWith("/api/blob/", StringComparison.Ordinal)) {
            string rawKey = Uri.UnescapeDataString(path.Substring("/api/blob/".Length));
            string key = SanitizeBlobKey(rawKey);
            if (string.IsNullOrEmpty(key)) {
                WriteResponse(stream, 400, "Bad Request", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"error\":\"bad_key\"}"));
                return;
            }
            string blobPath = Path.Combine(_blobsDir, key + ".dat");
            if (method == "GET") {
                byte[] blobBytes = null;
                lock (_fileLock) {
                    if (File.Exists(blobPath)) {
                        blobBytes = File.ReadAllBytes(blobPath);
                    }
                }
                if (blobBytes == null) {
                    WriteResponse(stream, 404, "Not Found", "text/plain; charset=utf-8", Encoding.UTF8.GetBytes("Not Found"));
                } else {
                    WriteResponse(stream, 200, "OK", "text/plain; charset=utf-8", blobBytes);
                }
                return;
            }
            if (method == "PUT") {
                if (body == null || body.Length < 16) {
                    WriteResponse(stream, 400, "Bad Request", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"error\":\"empty_blob\"}"));
                    return;
                }
                lock (_fileLock) {
                    Directory.CreateDirectory(_blobsDir);
                    SaveFileAtomic(blobPath, null, body);
                }
                WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true}"));
                return;
            }
            if (method == "DELETE") {
                lock (_fileLock) {
                    if (File.Exists(blobPath)) {
                        try { File.Delete(blobPath); } catch { }
                    }
                }
                WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true}"));
                return;
            }
        }

        if (path == "/api/open-data-folder" && method == "POST") {
            try {
                if (File.Exists(_stateFile)) {
                    Process.Start("explorer.exe", "/select,\"" + _stateFile + "\"");
                } else {
                    Process.Start("explorer.exe", "\"" + _dataDir + "\"");
                }
                WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true}"));
            } catch {
                WriteResponse(stream, 500, "Internal Server Error", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":false}"));
            }
            return;
        }

        if (path == "/api/create-shortcut" && method == "POST") {
            try {
                string lnkPath = CreateDesktopShortcut();
                string safeLnk = lnkPath.Replace("\\", "\\\\").Replace("\"", "\\\"");
                WriteResponse(stream, 200, "OK", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":true,\"shortcutPath\":\"" + safeLnk + "\"}"));
            } catch {
                WriteResponse(stream, 500, "Internal Server Error", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"ok\":false}"));
            }
            return;
        }

        WriteResponse(stream, 404, "Not Found", "application/json; charset=utf-8", Encoding.UTF8.GetBytes("{\"error\":\"not_found\"}"));
    }

    private string CreateDesktopShortcut() {
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        string lnkPath = Path.Combine(desktop, "Reveal Atlas.lnk");
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        object shell = Activator.CreateInstance(shellType);
        object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
        Type scType = shortcut.GetType();
        scType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { _selfCmdPath });
        scType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { Path.GetDirectoryName(_selfCmdPath) });
        if (File.Exists(_icoFile)) {
            scType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { _icoFile + ",0" });
        }
        scType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { "Reveal Atlas" });
        scType.InvokeMember("WindowStyle", BindingFlags.SetProperty, null, shortcut, new object[] { 7 });
        scType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
        return lnkPath;
    }

    private byte[] ReadStateBytes() {
        lock (_fileLock) {
            if (File.Exists(_stateFile)) {
                try {
                    byte[] main = File.ReadAllBytes(_stateFile);
                    if (main != null && main.Length >= 2) return main;
                } catch { }
            }
            if (File.Exists(_backupFile)) {
                try {
                    byte[] bak = File.ReadAllBytes(_backupFile);
                    if (bak != null && bak.Length >= 2) return bak;
                } catch { }
            }
            return null;
        }
    }

    private static void SaveFileAtomic(string destPath, string backupPath, byte[] data) {
        string tmpPath = destPath + ".tmp";
        File.WriteAllBytes(tmpPath, data);
        if (!File.Exists(destPath)) {
            File.Move(tmpPath, destPath);
            return;
        }
        try {
            File.Replace(tmpPath, destPath, backupPath, true);
        } catch {
            Thread.Sleep(20);
            if (!string.IsNullOrEmpty(backupPath) && File.Exists(destPath)) {
                try { File.Copy(destPath, backupPath, true); } catch { }
            }
            File.Copy(tmpPath, destPath, true);
            try { File.Delete(tmpPath); } catch { }
        }
    }

    private static string SanitizeBlobKey(string key) {
        if (string.IsNullOrEmpty(key) || key.Length > 80) return null;
        StringBuilder sb = new StringBuilder(key.Length);
        for (int i = 0; i < key.Length; i++) {
            char c = key[i];
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-') {
                sb.Append(c);
            } else {
                return null;
            }
        }
        return sb.ToString();
    }

    private static string GetQueryParam(string query, string name) {
        if (string.IsNullOrEmpty(query)) return "";
        string[] parts = query.Split('&');
        string prefix = name + "=";
        for (int i = 0; i < parts.Length; i++) {
            if (parts[i].StartsWith(prefix, StringComparison.Ordinal)) {
                return Uri.UnescapeDataString(parts[i].Substring(prefix.Length));
            }
        }
        return "";
    }

    private static void WriteResponse(NetworkStream stream, int code, string reason, string contentType, byte[] body) {
        long len = body == null ? 0 : body.Length;
        string head =
            "HTTP/1.1 " + code + " " + reason + "\r\n" +
            "Content-Type: " + contentType + "\r\n" +
            "Content-Length: " + len + "\r\n" +
            "Cache-Control: no-store\r\n" +
            "Connection: close\r\n\r\n";
        byte[] headBytes = Encoding.ASCII.GetBytes(head);
        stream.Write(headBytes, 0, headBytes.Length);
        if (body != null && body.Length > 0) {
            stream.Write(body, 0, body.Length);
        }
        stream.Flush();
    }

    private static bool TryReadHttpRequest(
        NetworkStream stream,
        out string method,
        out string target,
        out Dictionary<string, string> headers,
        out byte[] body
    ) {
        method = "";
        target = "";
        headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        body = new byte[0];

        MemoryStream headerStream = new MemoryStream();
        byte[] chunk = new byte[8192];
        int headerEnd = -1;

        byte[] rawBuf = null;
        while (headerEnd < 0) {
            int n = stream.Read(chunk, 0, chunk.Length);
            if (n <= 0) return false;
            headerStream.Write(chunk, 0, n);
            rawBuf = headerStream.ToArray();
            headerEnd = FindHeaderEnd(rawBuf, rawBuf.Length);
            if (rawBuf.Length > 65536 && headerEnd < 0) {
                return false;
            }
        }

        int rawLen = rawBuf.Length;
        string headerText = Encoding.ASCII.GetString(rawBuf, 0, headerEnd);
        string[] lines = headerText.Split(new string[] { "\r\n" }, StringSplitOptions.None);
        if (lines.Length == 0 || string.IsNullOrEmpty(lines[0])) return false;

        string[] firstParts = lines[0].Split(' ');
        if (firstParts.Length < 2) return false;
        method = firstParts[0].Trim().ToUpperInvariant();
        target = firstParts[1].Trim();

        for (int i = 1; i < lines.Length; i++) {
            string line = lines[i];
            if (string.IsNullOrEmpty(line)) continue;
            int colon = line.IndexOf(':');
            if (colon > 0) {
                string hKey = line.Substring(0, colon).Trim().ToLowerInvariant();
                string hVal = line.Substring(colon + 1).Trim();
                headers[hKey] = hVal;
            }
        }

        int bodyStart = headerEnd + 4;
        int leftover = rawLen - bodyStart;

        string clHeader;
        long contentLength = 0;
        if (headers.TryGetValue("content-length", out clHeader)) {
            if (!long.TryParse(clHeader, out contentLength) || contentLength < 0 || contentLength > 134217728L) {
                return false;
            }
        }

        if (contentLength > 0) {
            body = new byte[contentLength];
            int copied = 0;
            if (leftover > 0) {
                copied = (int)Math.Min((long)leftover, contentLength);
                Buffer.BlockCopy(rawBuf, bodyStart, body, 0, copied);
            }
            int offset = copied;
            while (offset < contentLength) {
                int toRead = (int)Math.Min(65536L, contentLength - offset);
                int read = stream.Read(body, offset, toRead);
                if (read <= 0) {
                    throw new EndOfStreamException("Incomplete HTTP request body");
                }
                offset += read;
            }
        }

        return true;
    }

    private static int FindHeaderEnd(byte[] buffer, int length) {
        for (int i = 0; i <= length - 4; i++) {
            if (buffer[i] == 13 && buffer[i + 1] == 10 && buffer[i + 2] == 13 && buffer[i + 3] == 10) {
                return i;
            }
        }
        return -1;
    }
}
'@

  Add-Type -TypeDefinition $csCode -Language CSharp

  $icoBytes = Unpack-Payload $rawSelf 'ICO'
  $png192Bytes = Unpack-Payload $rawSelf 'PNG192'
  $png256Bytes = Unpack-Payload $rawSelf 'PNG256'
  $manifestBytes = Unpack-Payload $rawSelf 'MANIFEST'
  $htmlRawBytes = Unpack-Payload $rawSelf 'HTML'

  $icoFile = [IO.Path]::Combine($dataDir, 'RevealAtlas.ico')
  try {
    if ((-not (Test-Path -LiteralPath $icoFile)) -or ((Get-Item -LiteralPath $icoFile).Length -ne $icoBytes.Length)) {
      [IO.File]::WriteAllBytes($icoFile, $icoBytes)
    }
  } catch {}

  $token = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
  $escapedDir = $dataDir.Replace('\', '\\').Replace('"', '\"')
  $portableLiteral = if ($portable) { 'true' } else { 'false' }
  $desktopBootstrap = '<script>window.__REVEAL_ATLAS_DESKTOP__={token:"' + $token + '",dataDir:"' + $escapedDir + '",portable:' + $portableLiteral + '};</script>'

  $htmlString = [Text.Encoding]::UTF8.GetString($htmlRawBytes)
  $headClose = $htmlString.IndexOf('</head>')
  if ($headClose -ge 0) {
    $htmlString = $htmlString.Substring(0, $headClose) + $desktopBootstrap + $htmlString.Substring($headClose)
  } else {
    $htmlString = $desktopBootstrap + $htmlString
  }
  $htmlServedBytes = [Text.Encoding]::UTF8.GetBytes($htmlString)

  $server = [RevealAtlasHost]::Create($token, $dataDir, $selfPath, $htmlServedBytes, $icoBytes, $png192Bytes, $png256Bytes, $manifestBytes)
  $server.Start()

  $port = $server.Port
  $myPid = $PID
  $instJson = '{"pid":' + $myPid + ',"port":' + $port + '}'
  try {
    [IO.File]::WriteAllText($instanceFile, $instJson, [Text.Encoding]::UTF8)
  } catch {}

  $url = 'http://127.0.0.1:' + $port + '/'
  $launchDetail = Launch-AppWindow $url $dataDir $server
  $startupWait = 120
  if ($launchDetail) {
    if ($env:RA_MODE -ieq '--debug') {
      Write-Warning ('Unable to open the application window: ' + $launchDetail)
      Write-Host ('Open this URL manually: ' + $url)
    } else {
      Show-LaunchFailure $launchDetail $url
    }
    # Keep serving for a while so the URL can still be opened by hand.
    $startupWait = 900
  }

  $server.WaitUntilFinished($startupWait)
  $server.Stop()

  try {
    if (Test-Path -LiteralPath $instanceFile) {
      $curInst = [IO.File]::ReadAllText($instanceFile, [Text.Encoding]::UTF8)
      if ($curInst.Contains('"pid":' + $myPid)) {
        [IO.File]::Delete($instanceFile)
      }
    }
  } catch {}
} catch {
  if ($env:RA_MODE -ieq '--debug') {
    Write-Error $_
  } else {
    try {
      Add-Type -AssemblyName System.Windows.Forms
      [System.Windows.Forms.MessageBox]::Show(
        ('Reveal Atlas startup error:' + [Environment]::NewLine + $_.Exception.Message),
        'Reveal Atlas',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
      ) | Out-Null
    } catch {}
  }
  exit 1
}
###PS_END###`
    .replace(/\r?\n/g, '\r\n');

  return [
    batchHeader,
    ':: Embedded PowerShell + Local Desktop Host + Bundled Application (7-bit ASCII)',
    psScript,
    ...payloadBlocks,
    '',
  ].join('\r\n');
}

function assertPureAsciiCrlf(content) {
  for (let i = 0; i < content.length; i++) {
    const code = content.charCodeAt(i);
    if (code > 127 || code === 0) {
      throw new Error(`Non-ASCII byte ${code} at offset ${i}`);
    }
    if (code === 10 && (i === 0 || content.charCodeAt(i - 1) !== 13)) {
      throw new Error(`Bare LF without CR at offset ${i}`);
    }
  }
}

function main() {
  const html = buildSelfContainedHtml();
  const png16 = renderAppIconPng(16);
  const png32 = renderAppIconPng(32);
  const png48 = renderAppIconPng(48);
  const png192 = renderAppIconPng(192);
  const png256 = renderAppIconPng(256);

  const ico = buildIcoFromPngs([
    { size: 16, png: png16 },
    { size: 32, png: png32 },
    { size: 48, png: png48 },
    { size: 256, png: png256 },
  ]);

  const manifest = JSON.stringify({
    name: 'Reveal Atlas',
    short_name: 'Reveal Atlas',
    description: 'Reveal Atlas — интерактивная карта мира, открывающаяся по клеткам',
    start_url: '/',
    display: 'standalone',
    background_color: '#080d16',
    theme_color: '#080d16',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-256.png', sizes: '256x256', type: 'image/png' },
    ],
  });

  const blocks = [
    gzipBase64Block('ICO', ico),
    gzipBase64Block('PNG192', png192),
    gzipBase64Block('PNG256', png256),
    gzipBase64Block('MANIFEST', Buffer.from(manifest, 'utf8')),
    gzipBase64Block('HTML', Buffer.from(html, 'utf8')),
  ];

  const cmdContent = buildWindowsCmd(blocks);
  assertPureAsciiCrlf(cmdContent);
  fs.writeFileSync(OUTPUT_CMD, Buffer.from(cmdContent, 'ascii'));

  const sizeKb = (Buffer.byteLength(cmdContent, 'ascii') / 1024).toFixed(1);
  console.log(`Built ${path.basename(OUTPUT_CMD)} (${sizeKb} KB, pure ASCII + CRLF)`);
}

main();
