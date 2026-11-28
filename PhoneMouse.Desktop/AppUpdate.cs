using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace PhoneMouse.Desktop;

internal sealed record AppUpdate(Version Version, Uri DownloadUrl, long Size, string Digest)
{
    private const string Repository = "nova-soft-jp/Phone-Mouse";
    private const string AssetName = "PhoneMouse.exe";
    private static readonly HttpClient Client = new() { Timeout = TimeSpan.FromSeconds(15) };
    public static Version CurrentVersion => typeof(AppUpdate).Assembly.GetName().Version!;

    static AppUpdate() => Client.DefaultRequestHeaders.UserAgent.ParseAdd("Phone-Mouse/" + CurrentVersion);

    public static async Task<AppUpdate?> CheckAsync()
    {
        if (!CanReplaceExecutable()) return null;
        using var response = await Client.GetAsync($"https://api.github.com/repos/{Repository}/releases/latest");
        if (response.StatusCode == HttpStatusCode.NotFound) return null;
        response.EnsureSuccessStatusCode();
        using var release = JsonDocument.Parse(await response.Content.ReadAsStreamAsync());
        var tag = release.RootElement.GetProperty("tag_name").GetString();
        if (tag is null || !Version.TryParse(tag.TrimStart('v'), out var version) ||
            version.Build < 0 || version.Revision >= 0 ||
            version <= new Version(CurrentVersion.ToString(3)))
            return null;

        foreach (var asset in release.RootElement.GetProperty("assets").EnumerateArray())
        {
            if (asset.GetProperty("name").GetString() != AssetName ||
                !asset.TryGetProperty("digest", out var digestValue) ||
                digestValue.GetString() is not { Length: 71 } digest ||
                !digest.StartsWith("sha256:", StringComparison.Ordinal) ||
                !digest.AsSpan(7).ToString().All(Uri.IsHexDigit))
                continue;

            var size = asset.GetProperty("size").GetInt64();
            if (size <= 0 || size >= 2L * 1024 * 1024 * 1024) return null;
            var url = new Uri($"https://github.com/{Repository}/releases/download/{Uri.EscapeDataString(tag)}/{AssetName}");
            return new AppUpdate(version, url, size, digest[7..]);
        }
        return null;
    }

    [System.Diagnostics.CodeAnalysis.UnconditionalSuppressMessage("SingleFile", "IL3000",
        Justification = "An empty assembly location identifies the single-file deployment.")]
    private static bool CanReplaceExecutable() =>
        string.IsNullOrEmpty(typeof(AppUpdate).Assembly.Location) &&
        System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture ==
            System.Runtime.InteropServices.Architecture.X64 &&
        Environment.ProcessPath is not null;

    public async Task PrepareAsync()
    {
        if (!CanReplaceExecutable()) throw new InvalidOperationException("更新対象の実行ファイルを確認できません。");
        var target = Environment.ProcessPath!;
        var staged = target + "." + Guid.NewGuid().ToString("N") + ".download";
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(10));
        try
        {
            await DownloadAsync(staged, timeout.Token).ConfigureAwait(false);
            var file = FileVersionInfo.GetVersionInfo(staged);
            if (new Version(file.FileMajorPart, file.FileMinorPart, file.FileBuildPart) != Version)
                throw new InvalidDataException("配布ファイルのバージョンが一致しません。");
            StartUpdater(target, staged);
        }
        catch
        {
            File.Delete(staged);
            throw;
        }
    }

    private async Task DownloadAsync(string destination, CancellationToken cancellationToken)
    {
        using var response = await Client.GetAsync(DownloadUrl, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false);
        response.EnsureSuccessStatusCode();
        await using var source = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        await using var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, true);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        var buffer = new byte[81920];
        long total = 0;
        int read;
        while ((read = await source.ReadAsync(buffer, cancellationToken).ConfigureAwait(false)) != 0)
        {
            total += read;
            if (total > Size) throw new InvalidDataException("配布ファイルのサイズが一致しません。");
            hash.AppendData(buffer.AsSpan(0, read));
            await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
        }
        if (total != Size || !Convert.ToHexString(hash.GetHashAndReset()).Equals(Digest, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("配布ファイルを検証できません。");
    }

    private static void StartUpdater(string target, string staged)
    {
        // The running executable must exit before Windows can replace it.
        var start = new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.SystemDirectory, "WindowsPowerShell", "v1.0", "powershell.exe"),
            UseShellExecute = false,
            CreateNoWindow = true
        };
        start.ArgumentList.Add("-NoProfile");
        start.ArgumentList.Add("-NonInteractive");
        start.ArgumentList.Add("-EncodedCommand");
        start.ArgumentList.Add(Convert.ToBase64String(Encoding.Unicode.GetBytes(ReplaceScript)));
        start.Environment["PHONEMOUSE_UPDATE_TARGET"] = target;
        start.Environment["PHONEMOUSE_UPDATE_STAGED"] = staged;
        start.Environment["PHONEMOUSE_UPDATE_PID"] = Environment.ProcessId.ToString(System.Globalization.CultureInfo.InvariantCulture);
        using var updater = Process.Start(start) ?? throw new InvalidOperationException("更新プログラムを起動できません。");
    }

    private const string ReplaceScript = """
        $ErrorActionPreference = 'Stop'
        $target = $env:PHONEMOUSE_UPDATE_TARGET
        $staged = $env:PHONEMOUSE_UPDATE_STAGED
        $backup = $staged + '.bak'
        $workingDirectory = [IO.Path]::GetDirectoryName($target)
        $original = Get-Process -Id ([int]$env:PHONEMOUSE_UPDATE_PID) -ErrorAction SilentlyContinue
        $exitConfirmed = $false
        $replaced = $false
        function Start-PhoneMouse {
            $start = [Diagnostics.ProcessStartInfo]::new($target)
            $start.WorkingDirectory = $workingDirectory
            $start.UseShellExecute = $false
            $process = [Diagnostics.Process]::Start($start)
            $process.Dispose()
        }
        try {
            if ($null -ne $original -and -not $original.WaitForExit(20000)) {
                throw 'Phone-Mouse did not exit.'
            }
            $exitConfirmed = $true
            [IO.File]::Replace($staged, $target, $backup)
            $replaced = $true
            Start-PhoneMouse
        } catch {
            $message = $_.ToString()
            if ($replaced) {
                try { [IO.File]::Replace($backup, $target, [NullString]::Value) }
                catch { $message += [Environment]::NewLine + $_.ToString() }
            }
            if ($exitConfirmed) {
                try { Start-PhoneMouse }
                catch { $message += [Environment]::NewLine + $_.ToString() }
            }
            try {
                $logDirectory = Join-Path $env:LOCALAPPDATA 'PhoneMouse'
                [IO.Directory]::CreateDirectory($logDirectory) | Out-Null
                [IO.File]::WriteAllText((Join-Path $logDirectory 'update-error.log'), $message)
            } catch {}
        } finally {
            Remove-Item -LiteralPath $staged -ErrorAction SilentlyContinue
        }
        if ($replaced -and -not $message) {
            Remove-Item -LiteralPath $backup -ErrorAction SilentlyContinue
        }
        """;
}
