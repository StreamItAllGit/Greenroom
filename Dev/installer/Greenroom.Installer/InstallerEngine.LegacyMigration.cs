// @module InstallerEngine.LegacyMigration.cs
// @purpose Opt-in non-destructive migration of data/, config/, and drops/ from legacy flat Programs\Greenroom installs.
// @inputs Target install directory, InstallerOptions, InstallerRuntimeContext, and log directory (@depends-on Foundation.Core, Foundation.Installer)
// @outputs Migration status summary and copied user runtime state (@exports Program.MigrateLegacyFlatInstallIfRequested)

using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    internal static int MigrateLegacyFlatInstallIfRequested(
        string targetInstallDir,
        InstallerOptions options,
        InstallerRuntimeContext runtimeCtx,
        string? logDir)
    {
        if (!options.MigrateLegacy || options.DryRun)
            return 0;

        string? sourceDir = ResolveLegacyFlatInstallSource(targetInstallDir, options, runtimeCtx);
        if (string.IsNullOrWhiteSpace(sourceDir) || !Directory.Exists(sourceDir))
        {
            WriteLog(logDir, "INFO", "Opt-in legacy migration requested, but no distinct legacy Programs\\Greenroom install was found.");
            return 0;
        }

        int copied = 0;
        foreach (string subDirName in new[] { "config", "data", "drops" })
        {
            string srcSub = Path.Combine(sourceDir, subDirName);
            if (!Directory.Exists(srcSub)) continue;

            string dstSub = EnsureWithinBoundary(targetInstallDir, Path.Combine(targetInstallDir, subDirName));
            copied += CopyDirectoryPreservingExisting(srcSub, dstSub);
        }

        WriteLog(logDir, "INFO", $"Migrated {copied:N0} user file(s) from legacy install '{sourceDir}' to '{targetInstallDir}' (non-destructive).");
        return copied;
    }

    private static string? ResolveLegacyFlatInstallSource(
        string targetInstallDir,
        InstallerOptions options,
        InstallerRuntimeContext runtimeCtx)
    {
        string normalizedTarget = Path.GetFullPath(targetInstallDir).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (runtimeCtx.IsStage1Isolated)
        {
            string? boundary = FirstNonEmpty(options.ActiveAppHome, options.ActiveDataDir);
            if (string.IsNullOrWhiteSpace(boundary)) return null;

            string containedLegacy = Path.Combine(boundary, "legacy-greenroom");
            if (!Directory.Exists(containedLegacy)) return null;
            EnsureWithinBoundary(boundary, containedLegacy);
            return string.Equals(Path.GetFullPath(containedLegacy).TrimEnd(Path.DirectorySeparatorChar), normalizedTarget, StringComparison.OrdinalIgnoreCase)
                ? null
                : containedLegacy;
        }

        if (!OperatingSystem.IsWindows() && !runtimeCtx.IsSandboxActive)
            return null;

        string localAppData = KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, GetEffectiveSandbox(runtimeCtx));
        var candidates = new List<string> { Path.Combine(localAppData, "Programs", "Greenroom") };
        if (runtimeCtx.IsSandboxActive && !string.IsNullOrWhiteSpace(runtimeCtx.Sandbox.StorageDriveRoot))
            candidates.Add(Path.Combine(runtimeCtx.Sandbox.StorageDriveRoot, "Programs", "Greenroom"));

        foreach (string candidate in candidates)
        {
            if (!Directory.Exists(candidate)) continue;
            string fullCand = Path.GetFullPath(candidate).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            if (string.Equals(fullCand, normalizedTarget, StringComparison.OrdinalIgnoreCase)) continue;
            bool hasState = File.Exists(Path.Combine(fullCand, "data", "greenroom.db")) ||
                            File.Exists(Path.Combine(fullCand, "config", "greenroom.yaml"));
            if (hasState) return fullCand;
        }
        return null;
    }

    private static int CopyDirectoryPreservingExisting(string sourceDir, string destDir)
    {
        int copied = 0;
        Directory.CreateDirectory(destDir);

        foreach (string file in Directory.EnumerateFiles(sourceDir, "*", SearchOption.AllDirectories))
        {
            string rel = Path.GetRelativePath(sourceDir, file);
            if (rel.StartsWith("backups" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) &&
                rel.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            string dstFile = Path.Combine(destDir, rel);
            string? dstParent = Path.GetDirectoryName(dstFile);
            if (!string.IsNullOrEmpty(dstParent)) Directory.CreateDirectory(dstParent);

            if (!File.Exists(dstFile))
            {
                File.Copy(file, dstFile, overwrite: false);
                copied++;
            }
        }

        return copied;
    }
}
