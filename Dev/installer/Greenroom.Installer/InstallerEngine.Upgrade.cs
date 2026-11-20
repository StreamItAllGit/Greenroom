// @module InstallerEngine.Upgrade.cs
// @purpose Atomic pre-upgrade SQLite backups, companion log UTF-16LE sanitization, and Companion daemon lifecycle.
// @inputs Target install directory, version string, InstallerRuntimeContext, and log directory (@depends-on InstallerEngine.Verification.cs)
// @outputs Compressed pre-upgrade backup archive and daemon stop/restart actions (@exports Program.CreatePreUpgradeDatabaseBackup, Program.TryExtractPidFromLockfile)

using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    private static bool IsProtectedUserRuntimeFile(string relPath) =>
        relPath.Equals("config/greenroom.yaml", StringComparison.OrdinalIgnoreCase) ||
        relPath.Equals("config/schedule.yaml", StringComparison.OrdinalIgnoreCase) ||
        relPath.StartsWith("data/greenroom.db", StringComparison.OrdinalIgnoreCase) ||
        relPath.Equals("data/token.json", StringComparison.OrdinalIgnoreCase) ||
        relPath.Equals("data/credentials.json", StringComparison.OrdinalIgnoreCase) ||
        relPath.Equals("data/companion_secret.key", StringComparison.OrdinalIgnoreCase) ||
        relPath.Equals("data/pricecharting.csv", StringComparison.OrdinalIgnoreCase) ||
        relPath.Equals("data/companion.log", StringComparison.OrdinalIgnoreCase) ||
        relPath.StartsWith("data/backups/", StringComparison.OrdinalIgnoreCase) ||
        relPath.StartsWith("drops/", StringComparison.OrdinalIgnoreCase) ||
        relPath.StartsWith("logs/", StringComparison.OrdinalIgnoreCase);

    private static string? Find7ZipExecutable()
    {
        foreach (string cand in new[] { "7z.exe", @"C:\Program Files\7-Zip\7z.exe", @"C:\Program Files (x86)\7-Zip\7z.exe" })
        {
            try
            {
                if (Path.IsPathRooted(cand))
                {
                    if (File.Exists(cand)) return cand;
                }
                else
                {
                    var res = RunProcess("where.exe", [cand], AppContext.BaseDirectory, 15_000);
                    if (res.ExitCode == 0)
                    {
                        string found = res.StdOut.Trim().Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? string.Empty;
                        if (!string.IsNullOrWhiteSpace(found) && File.Exists(found)) return found;
                    }
                }
            }
            catch { /* Ignore probe failure */ }
        }
        return null;
    }

    private static string? CreatePreUpgradeDatabaseBackup(string installDir, string version, string? logDir)
    {
        string dbPath = Path.Combine(installDir, "data", "greenroom.db");
        if (!File.Exists(dbPath)) return null;

        string backupsDir = Path.Combine(installDir, "data", "backups");
        Directory.CreateDirectory(backupsDir);

        string safeVer = new string(version.Select(ch => char.IsLetterOrDigit(ch) || ch is '.' or '_' or '-' ? ch : '_').ToArray());
        string rawBackupPath = Path.Combine(backupsDir, $"greenroom_pre_upgrade_v{safeVer}_{DateTime.UtcNow:yyyyMMdd_HHmmss}.db");

        string? pyExe = ResolveInstalledOrSystemPython(installDir);
        bool backedUpViaSqlite = false;
        if (!string.IsNullOrWhiteSpace(pyExe))
        {
            try
            {
                string pyScript = "import sqlite3, sys; src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()";
                var res = RunProcess(pyExe, ["-c", pyScript, dbPath, rawBackupPath], installDir, 60_000);
                if (res.ExitCode == 0 && File.Exists(rawBackupPath)) backedUpViaSqlite = true;
            }
            catch { backedUpViaSqlite = false; }
        }

        if (!backedUpViaSqlite) File.Copy(dbPath, rawBackupPath, overwrite: true);

        string finalArchivePath = Path.ChangeExtension(rawBackupPath, ".7z");
        bool compressed = false;
        string? sevenZip = Find7ZipExecutable();
        if (!string.IsNullOrWhiteSpace(sevenZip))
        {
            try
            {
                var zRes = RunProcess(sevenZip, ["a", "-t7z", "-mx=1", "-y", finalArchivePath, rawBackupPath], backupsDir, 60_000);
                if (zRes.ExitCode == 0 && File.Exists(finalArchivePath)) { ForceDeleteFile(rawBackupPath); compressed = true; }
            }
            catch { compressed = false; }
        }

        if (!compressed)
        {
            finalArchivePath = Path.ChangeExtension(rawBackupPath, ".zip");
            try
            {
                if (File.Exists(finalArchivePath)) ForceDeleteFile(finalArchivePath);
                using (var zf = ZipFile.Open(finalArchivePath, ZipArchiveMode.Create))
                    zf.CreateEntryFromFile(rawBackupPath, Path.GetFileName(rawBackupPath), CompressionLevel.Fastest);
                ForceDeleteFile(rawBackupPath);
            }
            catch { finalArchivePath = rawBackupPath; }
        }

        try
        {
            var archives = new DirectoryInfo(backupsDir).GetFiles("greenroom_*.*")
                .Where(f => f.Extension is ".7z" or ".zip" or ".db" or ".7Z" or ".ZIP" or ".DB")
                .OrderByDescending(f => f.LastWriteTimeUtc).ThenByDescending(f => f.Name, StringComparer.OrdinalIgnoreCase).ToList();
            foreach (FileInfo stale in archives.Skip(10)) ForceDeleteFile(stale.FullName);
        }
        catch { /* Ignore retention cleanup errors */ }

        WriteLog(logDir, "INFO", $"Created atomic pre-upgrade database backup: '{finalArchivePath}'.");
        return finalArchivePath;
    }

    private static void SanitizeCompanionLogIfPresent(string companionLogPath, string? logDir)
    {
        if (!File.Exists(companionLogPath)) return;
        try
        {
            byte[] raw = File.ReadAllBytes(companionLogPath);
            if (Array.IndexOf(raw, (byte)0) >= 0)
            {
                byte[] cleaned = raw.Where(b => b != 0 && b != 0xFF && b != 0xFE).ToArray();
                File.WriteAllText(companionLogPath, Encoding.UTF8.GetString(cleaned), new UTF8Encoding(false));
                WriteLog(logDir, "INFO", $"Normalized UTF-16LE NUL bytes in '{companionLogPath}' to clean UTF-8.");
            }
        }
        catch { /* Ignore non-fatal log cleanup error */ }
    }

    private static string ComputeFileSha256Hex(string filePath) =>
        File.Exists(filePath) ? Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(filePath))).ToLowerInvariant() : string.Empty;

    internal static int? TryExtractPidFromLockfile(string lockFilePath)
    {
        if (!File.Exists(lockFilePath)) return null;
        try
        {
            string raw = File.ReadAllText(lockFilePath, Encoding.UTF8).Trim();
            if (int.TryParse(raw, out int plainPid) && plainPid > 0) return plainPid;
            if (raw.StartsWith("{", StringComparison.Ordinal))
            {
                using JsonDocument doc = JsonDocument.Parse(raw);
                if (doc.RootElement.TryGetProperty("pid", out JsonElement pidEl) && pidEl.TryGetInt32(out int jsonPid) && jsonPid > 0)
                    return jsonPid;
            }
        }
        catch { /* ignore malformed lockfile */ }
        return null;
    }

    private static bool TryStopExistingDaemon(string installDir, string? logDir)
    {
        bool wasRunning = false;
        string venvPy = File.Exists(Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe"))
            ? Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe") : Path.Combine(installDir, ".venv", "Scripts", "python.exe");
        string pidFile = Path.Combine(installDir, "data", "companion.pid");
        string lockFile = Path.Combine(installDir, "data", "greenroom.lock");
        string stoppedSentinel = Path.Combine(installDir, "data", "companion.stopped");

        if (File.Exists(pidFile))
        {
            wasRunning = true;
            if (File.Exists(venvPy))
            {
                try
                {
                    var stopRes = RunProcess(venvPy, ["-m", "greenroom.cli", "companion", "stop", "--no-pause"], installDir, 20_000);
                    if (stopRes.ExitCode == 0 && stopRes.StdOut.Contains("stopped", StringComparison.OrdinalIgnoreCase)) wasRunning = true;
                }
                catch { /* fallback */ }
            }
        }

        foreach (string lf in new[] { pidFile, lockFile })
        {
            int? pid = TryExtractPidFromLockfile(lf);
            if (pid is > 0 && pid.Value != Environment.ProcessId)
            {
                try { RunProcess("taskkill.exe", ["/PID", pid.Value.ToString(), "/T", "/F"], installDir, 15_000); wasRunning = true; }
                catch { /* ignore */ }
            }
            if (File.Exists(lf)) ForceDeleteFile(lf);
        }

        try
        {
            string normRoot = Path.GetFullPath(installDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            foreach (string procName in new[] { "python", "pythonw" })
                foreach (Process proc in Process.GetProcessesByName(procName))
                    using (proc)
                    {
                        try
                        {
                            if (proc.Id == Environment.ProcessId) continue;
                            string? modPath = proc.MainModule?.FileName;
                            if (!string.IsNullOrWhiteSpace(modPath) && modPath.StartsWith(normRoot, StringComparison.OrdinalIgnoreCase))
                            { proc.Kill(entireProcessTree: true); proc.WaitForExit(3000); wasRunning = true; }
                        }
                        catch { /* Ignore Win32Exception on elevated/exited processes */ }
                    }
        }
        catch { /* ignore enumeration failure */ }

        if (File.Exists(stoppedSentinel)) ForceDeleteFile(stoppedSentinel);
        if (wasRunning) WriteLog(logDir, "INFO", "Stopped running Greenroom Companion process before update.");
        return wasRunning;
    }

    private static void TryRestartDaemonAfterUpgrade(string installDir, InstallerRuntimeContext runtimeCtx, string? logDir)
    {
        if (runtimeCtx.ShouldSuppressLiveHostMutations) return;
        string venvPy = File.Exists(Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe"))
            ? Path.Combine(installDir, "app", ".venv", "Scripts", "python.exe") : Path.Combine(installDir, ".venv", "Scripts", "python.exe");
        if (!File.Exists(venvPy)) return;
        try { RunProcess(venvPy, ["-m", "greenroom.cli", "companion", "start", "--no-pause"], installDir, 30_000); }
        catch (Exception ex) { WriteLog(logDir, "WARN", $"Post-upgrade daemon restart skipped: {ex.Message}"); }
    }
}
