// @module InstallerEngine.Verification.cs
// @purpose Payload SHA-256 verification, containment validation, async non-deadlocking process runner, and logging.
// @inputs Embedded/external payload streams, InstallerRuntimeContext, and CLI options (@depends-on InstallerModels.cs, Foundation.Core, Foundation.Installer)
// @outputs Verified payload results, containment-validated paths, and structured installer.log entries (@exports Program.VerifyPayload, Program.RunProcess)

using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    internal static (bool Valid, string Sha256, long SizeBytes, int EntryCount, string Message) VerifyPayload(
        string? externalPayloadPath,
        string? offlineBundlePath = null,
        string? manifestPath = null,
        string? payloadUrlOverride = null,
        string? payloadShaOverride = null,
        InstallerRuntimeContext? runtimeCtx = null) =>
        VerifyOnlineOrOffline(
            externalPayloadPath,
            offlineBundlePath,
            manifestPath,
            payloadUrlOverride,
            payloadShaOverride,
            typeof(Program).Assembly,
            runtimeCtx);

    internal static (bool Valid, string Sha256, long SizeBytes, int EntryCount, string Message) VerifyEmbeddedOrExternalArchive(string? externalPayloadPath)
    {
        PayloadMetadata? meta = LoadEmbeddedPayloadMetadata();
        using Stream? stream = OpenPayloadStream(externalPayloadPath);
        if (stream == null)
            return (false, string.Empty, 0, 0, "Embedded payload.zip resource was not found in this installer binary.");

        using var mem = new MemoryStream();
        stream.CopyTo(mem);
        byte[] bytes = mem.ToArray();
        if (bytes.Length == 0)
            return (false, string.Empty, 0, 0, "Installer payload archive is empty.");

        string actualSha = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(externalPayloadPath) &&
            meta != null &&
            !string.IsNullOrWhiteSpace(meta.PayloadSha256) &&
            !actualSha.Equals(meta.PayloadSha256, StringComparison.OrdinalIgnoreCase))
        {
            return (false, actualSha, bytes.Length, 0, $"Payload SHA-256 mismatch (expected {meta.PayloadSha256}, actual {actualSha}).");
        }

        mem.Position = 0;
        int entryCount = InspectAndValidateZipEntries(mem);
        if (entryCount == 0)
            return (false, actualSha, bytes.Length, 0, "Installer payload archive contains zero entries.");

        string verLabel = meta?.Version ?? ResolveProductVersion();
        int wheels = meta?.WheelCount ?? 0;
        return (
            true,
            actualSha,
            bytes.Length,
            entryCount,
            $"Verified Greenroom v{verLabel} payload ({bytes.Length:N0} bytes, {entryCount} entries, {wheels} wheels, SHA-256: {actualSha})."
        );
    }

    internal static string ResolveDefaultInstallDir(InstallerRuntimeContext runtimeCtx, string? activeAppHome = null) =>
        ResolveInstallTarget(runtimeCtx, activeAppHome: activeAppHome).InstallDir;

    internal static string ValidateAndNormalizeInstallDir(
        string? explicitInstallDir,
        InstallerRuntimeContext runtimeCtx,
        string? activeAppHome = null,
        string? suiteDir = null,
        bool? fullSuiteToggle = null) =>
        ResolveInstallTarget(runtimeCtx, explicitInstallDir, suiteDir, fullSuiteToggle, activeAppHome).InstallDir;

    internal static string EnsureWithinBoundary(string boundaryDir, string targetPath)
    {
        string normBoundary = Path.GetFullPath(boundaryDir)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string normTarget = Path.GetFullPath(targetPath)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

        if (string.Equals(normBoundary, normTarget, StringComparison.OrdinalIgnoreCase))
            return normTarget;

        string prefix = normBoundary + Path.DirectorySeparatorChar;
        if (!normTarget.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                $"Fail-closed path containment violation: '{normTarget}' escapes containment boundary '{normBoundary}'.");
        }

        return normTarget;
    }

    internal static InstallerConfigFile? LoadOptionalConfig(string? configPath, InstallerRuntimeContext runtimeCtx, string? activeAppHome)
    {
        if (string.IsNullOrWhiteSpace(configPath)) return null;
        string full = Path.GetFullPath(configPath);
        if (runtimeCtx.IsSandboxActive)
            full = SandboxEnvironmentSeam.AssertSandboxPathIfActive(full, runtimeCtx.Sandbox);
        if (runtimeCtx.IsStage1Isolated)
        {
            string? boundary = FirstNonEmpty(runtimeCtx.DataDirectory, activeAppHome);
            if (!string.IsNullOrWhiteSpace(boundary))
                EnsureWithinBoundary(boundary, full);
        }
        if (!File.Exists(full))
            throw new FileNotFoundException($"Specified --config file was not found: '{full}'.");
        return JsonSerializer.Deserialize<InstallerConfigFile>(File.ReadAllText(full, Encoding.UTF8), JsonOpts);
    }

    private static PayloadMetadata? LoadEmbeddedPayloadMetadata()
    {
        using Stream? s = typeof(Program).Assembly.GetManifestResourceStream(EmbeddedPayloadMetaResource);
        return s == null ? null : JsonSerializer.Deserialize<PayloadMetadata>(s, JsonOpts);
    }

    private static Stream? OpenPayloadStream(string? externalPayloadPath)
    {
        if (!string.IsNullOrWhiteSpace(externalPayloadPath))
        {
            string full = Path.GetFullPath(externalPayloadPath);
            return File.Exists(full) ? File.OpenRead(full) : null;
        }
        return typeof(Program).Assembly.GetManifestResourceStream(EmbeddedPayloadZipResource);
    }

    private static void SeedConfigIfMissing(string templatePath, string activePath)
    {
        if (!File.Exists(activePath) && File.Exists(templatePath))
            File.Copy(templatePath, activePath, overwrite: false);
    }

    private static string ResolveProductVersion()
    {
        string? infoVer = typeof(Program).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
        if (!string.IsNullOrWhiteSpace(infoVer))
        {
            int plusIdx = infoVer.IndexOf('+');
            return plusIdx > 0 ? infoVer[..plusIdx] : infoVer;
        }
        return DefaultFallbackVersion;
    }

    internal static string? ResolveDirectoryOption(string? cliVal, params string[] envVars)
    {
        if (!string.IsNullOrWhiteSpace(cliVal))
            return Path.GetFullPath(cliVal.Trim());
        foreach (string env in envVars)
        {
            string? val = Environment.GetEnvironmentVariable(env);
            if (!string.IsNullOrWhiteSpace(val))
                return Path.GetFullPath(val.Trim());
        }
        return null;
    }

    internal static void WriteLog(string? logDir, string level, string message)
    {
        if (string.IsNullOrWhiteSpace(logDir)) return;
        try
        {
            Directory.CreateDirectory(logDir);
            string logPath = Path.Combine(logDir, "installer.log");
            string line = $"[{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss.fff}] [{level,-5}] [Greenroom.Installer] {message}{Environment.NewLine}";
            File.AppendAllText(logPath, line, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        }
        catch { /* Auxiliary log write failure must never crash setup */ }
    }

    internal static (int ExitCode, string StdOut, string StdErr, bool TimedOut) RunProcess(
        string fileName,
        IReadOnlyList<string> arguments,
        string workingDir,
        int timeoutMs = 300_000,
        Action<string, bool>? onOutputLine = null,
        IReadOnlyDictionary<string, string?>? extraEnv = null)
    {
        var psi = new ProcessStartInfo(fileName)
        {
            WorkingDirectory = workingDir,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        foreach (string arg in arguments) psi.ArgumentList.Add(arg);
        psi.Environment["PYTHONUTF8"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        if (extraEnv is not null)
        {
            foreach (KeyValuePair<string, string?> kv in extraEnv)
            {
                if (kv.Value is null) psi.Environment.Remove(kv.Key);
                else psi.Environment[kv.Key] = kv.Value;
            }
        }

        using Process proc = Process.Start(psi) ?? throw new InvalidOperationException($"Failed to launch process '{fileName}'.");
        proc.StandardInput.Close();

        var stdoutSb = new StringBuilder();
        var stderrSb = new StringBuilder();
        var sync = new object();
        using var outDone = new ManualResetEventSlim(false);
        using var errDone = new ManualResetEventSlim(false);

        proc.OutputDataReceived += (_, e) =>
        {
            if (e.Data is null) { outDone.Set(); return; }
            lock (sync) { stdoutSb.AppendLine(e.Data); }
            onOutputLine?.Invoke(e.Data, false);
        };
        proc.ErrorDataReceived += (_, e) =>
        {
            if (e.Data is null) { errDone.Set(); return; }
            lock (sync) { stderrSb.AppendLine(e.Data); }
            onOutputLine?.Invoke(e.Data, true);
        };

        proc.BeginOutputReadLine();
        proc.BeginErrorReadLine();

        bool exited = proc.WaitForExit(Math.Max(100, timeoutMs));
        if (!exited)
        {
            try { proc.Kill(entireProcessTree: true); } catch { /* ignore already-exited */ }
            try { proc.WaitForExit(5000); } catch { /* ignore */ }
            outDone.Wait(2000);
            errDone.Wait(2000);
            lock (sync) { return (-1, stdoutSb.ToString(), stderrSb.ToString(), true); }
        }

        outDone.Wait(5000);
        errDone.Wait(5000);
        lock (sync) { return (proc.ExitCode, stdoutSb.ToString(), stderrSb.ToString(), false); }
    }

    internal static void RunProcessOrThrow(
        string fileName,
        IReadOnlyList<string> arguments,
        string workingDir,
        int timeoutMs = 300_000,
        Action<string, bool>? onOutputLine = null,
        IReadOnlyDictionary<string, string?>? extraEnv = null)
    {
        var res = RunProcess(fileName, arguments, workingDir, timeoutMs, onOutputLine, extraEnv);
        if (res.TimedOut)
        {
            throw new TimeoutException(
                $"Command '{fileName} {string.Join(' ', arguments)}' timed out after {timeoutMs}ms and was terminated.");
        }
        if (res.ExitCode != 0)
        {
            string errDetail = string.IsNullOrWhiteSpace(res.StdErr) ? res.StdOut : res.StdErr;
            throw new InvalidOperationException(
                $"Command '{fileName} {string.Join(' ', arguments)}' exited with code {res.ExitCode}: {errDetail}".Trim());
        }
    }
}
