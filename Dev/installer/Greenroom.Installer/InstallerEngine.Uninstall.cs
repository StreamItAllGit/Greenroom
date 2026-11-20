// @module InstallerEngine.Uninstall.cs
// @purpose Safe uninstall orchestration with product ownership check, IPlatformRegistry cleanup, and bounded deferred self-delete.
// @inputs Target install directory, purgeData flag, InstallerRuntimeContext (@depends-on InstallerModels.cs, InstallerPathResolver.cs)
// @outputs Removed managed files/shortcuts/registry records and exit code (@exports Program.ExecuteUninstallOperation, Program.RunUninstall)

using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    public static int ExecuteUninstallOperation(
        string version,
        string? explicitInstallDir,
        bool purgeData,
        bool containedMode,
        bool testMode,
        bool dryRun,
        string? activeDataDir,
        string? activeAppHome,
        string? activeLogDir,
        IProgress<string>? progress = null)
    {
        _ = containedMode;
        _ = testMode;
        var ctx = new InstallerRuntimeContext(activeDataDir, null, activeLogDir, dryRun, "GREENROOM");
        return RunUninstall(version, explicitInstallDir, purgeData, dryRun, activeDataDir, activeAppHome, activeLogDir, progress, ctx, null);
    }

    internal static int RunUninstall(
        string version,
        string? explicitInstallDir,
        bool purgeData,
        bool dryRun,
        string? activeDataDir,
        string? activeAppHome,
        string? activeLogDir,
        IProgress<string>? progress = null,
        InstallerRuntimeContext? runtimeCtx = null,
        Action<int, string>? onProgress = null)
    {
        InstallerRuntimeContext ctx = runtimeCtx ?? new InstallerRuntimeContext(activeDataDir, null, activeLogDir, dryRun, "GREENROOM");
        string? currentLogDir = string.IsNullOrWhiteSpace(activeLogDir) ? ResolveActiveLogDirectory(ctx, null, activeAppHome) : activeLogDir;
        void Report(int pct, string msg)
        {
            onProgress?.Invoke(pct, msg);
            progress?.Report(msg);
        }

        try
        {
            string? fallbackExplicit = !string.IsNullOrWhiteSpace(explicitInstallDir)
                ? explicitInstallDir
                : (File.Exists(Path.Combine(AppContext.BaseDirectory, InstallManifestFileName))
                    ? AppContext.BaseDirectory
                    : null);
            string installDir = ValidateAndNormalizeInstallDir(fallbackExplicit, ctx, activeAppHome);
            currentLogDir = string.IsNullOrWhiteSpace(activeLogDir) ? ResolveActiveLogDirectory(ctx, installDir, activeAppHome) : activeLogDir;

            if (dryRun)
            {
                string dryMsg = $"[DRY-RUN] Would uninstall Greenroom from '{installDir}' (PurgeData={purgeData}).";
                WriteLog(currentLogDir, "INFO", dryMsg);
                Console.WriteLine(dryMsg);
                return 0;
            }

            if (!Directory.Exists(installDir))
            {
                string missingMsg = $"Install directory does not exist: '{installDir}'.";
                WriteLog(currentLogDir, "ERROR", missingMsg);
                Console.Error.WriteLine($"[ERROR] {missingMsg}");
                return 1;
            }

            if (!IsOwnInstallManifest(installDir, out InstallManifest? manifest, out string? foreignProduct, allowLegacyWithoutManifest: true))
            {
                string ownErr = $"Refusing to uninstall '{installDir}': install-manifest.json belongs to '{foreignProduct}', not Greenroom.";
                WriteLog(currentLogDir, "ERROR", ownErr);
                Console.Error.WriteLine($"[ERROR] {ownErr}");
                return 1;
            }

            Report(15, "Stopping running Greenroom Companion daemon...");
            TryStopExistingDaemon(installDir, currentLogDir);

            Report(35, "Removing application files...");
            int removedFiles = 0;
            var dirsToDelete = new List<string>(manifest?.ManagedDirectories ?? ["app", "greenroom", "scripts", "wheelhouse"])
            {
                "app", ".venv", "greenroom.egg-info"
            };
            if (purgeData) dirsToDelete.AddRange(["data", "drops", "logs", "config"]);

            foreach (string relDir in dirsToDelete.Distinct(StringComparer.OrdinalIgnoreCase))
            {
                string target = EnsureWithinBoundary(installDir, Path.Combine(installDir, relDir));
                if (Directory.Exists(target))
                {
                    removedFiles += Directory.GetFiles(target, "*", SearchOption.AllDirectories).Length;
                    ForceDeleteDirectory(target);
                }
            }

            foreach (string relFile in manifest?.ManagedFiles ?? [])
            {
                string target = EnsureWithinBoundary(installDir, Path.Combine(installDir, relFile.Replace('/', Path.DirectorySeparatorChar)));
                if (File.Exists(target)) { ForceDeleteFile(target); removedFiles++; }
            }

            string rootAgents = EnsureWithinBoundary(installDir, Path.Combine(installDir, "AGENTS.md"));
            if (File.Exists(rootAgents)) { ForceDeleteFile(rootAgents); removedFiles++; }

            if (!purgeData) TryDeleteEmptySubtree(Path.Combine(installDir, "data", "migrations"));

            Report(65, "Removing shortcuts & registry metadata...");
            if (manifest?.CreatedShortcuts != null)
            {
                foreach (string lnk in manifest.CreatedShortcuts)
                {
                    if (string.IsNullOrWhiteSpace(lnk) || !File.Exists(lnk)) continue;
                    try
                    {
                        if (ctx.IsSandboxActive) SandboxEnvironmentSeam.AssertSandboxPathIfActive(lnk, ctx.Sandbox);
                        File.Delete(lnk);
                        removedFiles++;
                        TryDeleteIfEmpty(Path.GetDirectoryName(lnk));
                    }
                    catch { /* Ignore locked shortcut edge case */ }
                }
            }

            if (!string.IsNullOrWhiteSpace(manifest?.AutostartRecord) && File.Exists(manifest.AutostartRecord))
            {
                try
                {
                    if (ctx.IsSandboxActive) SandboxEnvironmentSeam.AssertSandboxPathIfActive(manifest.AutostartRecord, ctx.Sandbox);
                    File.Delete(manifest.AutostartRecord);
                    removedFiles++;
                }
                catch { /* ignore */ }
            }

            if (!ctx.ShouldSuppressLiveHostMutations && OperatingSystem.IsWindows())
            {
                try { RunProcess("schtasks.exe", ["/Delete", "/F", "/TN", "\\Greenroom_Watchdog"], installDir, 15_000); }
                catch { /* ignore */ }
            }

            TryUnregisterMotherManifest(manifest?.MotherManifestRecord, ctx, currentLogDir);
            RemoveUninstallerRegistry(manifest?.UninstallRegistryRecord, ctx);

            string manifestPath = Path.Combine(installDir, InstallManifestFileName);
            if (File.Exists(manifestPath)) { ForceDeleteFile(manifestPath); removedFiles++; }

            if (!purgeData)
            {
                foreach (string sub in new[]
                         {
                             "drops/agent_queue", "drops/processed", "drops/failed",
                             "drops/takeout", "drops/receipts", "drops/manual_csv", "drops/dats", "drops",
                             "data/backups", "data/dats", "data/quarantine", "data/config_snapshots", "logs/crashes", "logs"
                         })
                {
                    TryDeleteIfEmpty(Path.Combine(installDir, sub.Replace('/', Path.DirectorySeparatorChar)));
                }
            }

            string uninstallerPath = Path.Combine(installDir, UninstallerExeName);
            if (File.Exists(uninstallerPath))
            {
                string? selfExe = Environment.ProcessPath;
                bool runningSelf = !string.IsNullOrWhiteSpace(selfExe) &&
                                   string.Equals(Path.GetFullPath(selfExe), Path.GetFullPath(uninstallerPath), StringComparison.OrdinalIgnoreCase);
                if (!runningSelf)
                {
                    ForceDeleteFile(uninstallerPath);
                    removedFiles++;
                }
                else if (!ctx.ShouldSuppressHostMutations && RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
                {
                    if (ctx.IsSandboxActive)
                    {
                        SandboxEnvironmentSeam.AssertSandboxPathIfActive(uninstallerPath, ctx.Sandbox);
                        SandboxEnvironmentSeam.AssertSandboxPathIfActive(installDir, ctx.Sandbox);
                    }
                    ScheduleDeferredSelfDelete(uninstallerPath, installDir);
                }
            }

            TryDeleteIfEmpty(installDir);
            string doneMsg = $"Uninstalled Greenroom v{version} from '{installDir}' ({removedFiles:N0} files removed, PurgeData={purgeData}).";
            Report(100, doneMsg);
            WriteLog(currentLogDir, "INFO", doneMsg);
            Console.WriteLine(doneMsg);
            return 0;
        }
        catch (Exception ex) when (SandboxEnvironmentSeam.IsContainmentViolation(ex))
        {
            WriteLog(currentLogDir, "ERROR", $"Sandbox containment violation (ExitCode=3): {ex.Message}");
            Console.Error.WriteLine($"[ERROR] {ex.Message}");
            return SandboxEnvironmentSeam.ContainmentExitCode;
        }
        catch (Exception ex)
        {
            WriteLog(currentLogDir, "ERROR", $"Uninstall failed: {ex.Message}");
            Console.Error.WriteLine($"[ERROR] {ex.Message}");
            return 1;
        }
    }

    internal static void ClearReadOnlyAttributesRecursively(string path)
    {
        try
        {
            if (File.Exists(path)) { File.SetAttributes(path, FileAttributes.Normal); return; }
            if (!Directory.Exists(path)) return;

            FileAttributes rootAttr = File.GetAttributes(path);
            if ((rootAttr & (FileAttributes.ReadOnly | FileAttributes.Hidden | FileAttributes.System)) != 0)
                File.SetAttributes(path, FileAttributes.Directory);

            foreach (string entry in Directory.EnumerateFileSystemEntries(path, "*", SearchOption.AllDirectories))
            {
                try
                {
                    FileAttributes attr = File.GetAttributes(entry);
                    if ((attr & FileAttributes.Directory) != 0)
                    {
                        if ((attr & (FileAttributes.ReadOnly | FileAttributes.Hidden | FileAttributes.System)) != 0)
                            File.SetAttributes(entry, FileAttributes.Directory);
                    }
                    else if ((attr & (FileAttributes.ReadOnly | FileAttributes.Hidden | FileAttributes.System)) != 0)
                    {
                        File.SetAttributes(entry, FileAttributes.Normal);
                    }
                }
                catch { /* Continue unlocking remaining entries */ }
            }
        }
        catch { /* Ignore top-level attribute probe error */ }
    }

    internal static void ForceDeleteDirectory(string dirPath, int maxAttempts = 5, int retryDelayMs = 150)
    {
        if (!Directory.Exists(dirPath)) return;
        for (int attempt = 0; attempt < maxAttempts; attempt++)
        {
            ClearReadOnlyAttributesRecursively(dirPath);
            try { if (Directory.Exists(dirPath)) Directory.Delete(dirPath, recursive: true); return; }
            catch when (attempt < maxAttempts - 1) { GC.Collect(); GC.WaitForPendingFinalizers(); Thread.Sleep(retryDelayMs); }
        }
    }

    internal static void ForceDeleteFile(string filePath, int maxAttempts = 5, int retryDelayMs = 150)
    {
        if (!File.Exists(filePath)) return;
        for (int attempt = 0; attempt < maxAttempts; attempt++)
        {
            try { if (File.Exists(filePath)) { File.SetAttributes(filePath, FileAttributes.Normal); File.Delete(filePath); } return; }
            catch when (attempt < maxAttempts - 1) { Thread.Sleep(retryDelayMs); }
        }
    }

    private static void RemoveUninstallerRegistry(string? recordedRecord, InstallerRuntimeContext ctx)
    {
        if (!string.IsNullOrWhiteSpace(recordedRecord) &&
            !recordedRecord.StartsWith("HKCU\\", StringComparison.OrdinalIgnoreCase) &&
            File.Exists(recordedRecord))
        {
            try { ForceDeleteFile(recordedRecord); } catch { /* ignore */ }
            return;
        }

        if (!ctx.ShouldSuppressHostMutations && (OperatingSystem.IsWindows() || ctx.IsSandboxActive))
        {
            try
            {
                ctx.Registry.DeleteSubKeyTree(PlatformRegistryHive.CurrentUser, WindowsUninstallRegistrySubKey, throwOnMissingSubKey: false);
                ctx.Registry.DeleteSubKeyTree(PlatformRegistryHive.CurrentUser, StreamItAllGreenroomRegistrySubKey, throwOnMissingSubKey: false);
                ctx.Registry.DeleteSubKeyTree(PlatformRegistryHive.CurrentUser, MotherAppRegistrationSubKey, throwOnMissingSubKey: false);
            }
            catch { /* Ignore absent key */ }
        }
    }

    internal static string BuildDeferredSelfDeleteCommand(string uninstallerPath, string installDir) =>
        Foundation.Installer.InstallerEngine.BuildSelfDeleteTrampolineCommand(uninstallerPath, installDir, maxAttempts: 15);

    private static void ScheduleDeferredSelfDelete(string uninstallerPath, string installDir)
    {
        try
        {
            var psi = new ProcessStartInfo("cmd.exe")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            psi.ArgumentList.Add("/c");
            psi.ArgumentList.Add(BuildDeferredSelfDeleteCommand(uninstallerPath, installDir));
            Process.Start(psi);
        }
        catch { /* Ignore deferred cleanup failures */ }
    }

    private static void TryDeleteEmptySubtree(string dirPath)
    {
        if (!Directory.Exists(dirPath)) return;
        foreach (string sub in Directory.GetDirectories(dirPath)) TryDeleteEmptySubtree(sub);
        TryDeleteIfEmpty(dirPath);
    }

    private static void TryDeleteIfEmpty(string? dirPath)
    {
        if (!string.IsNullOrWhiteSpace(dirPath) && Directory.Exists(dirPath) && !Directory.EnumerateFileSystemEntries(dirPath).Any())
        {
            try { File.SetAttributes(dirPath, FileAttributes.Directory); Directory.Delete(dirPath, recursive: false); }
            catch { /* ignore */ }
        }
    }
}
