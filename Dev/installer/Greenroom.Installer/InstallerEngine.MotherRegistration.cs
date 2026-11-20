// @module InstallerEngine.MotherRegistration.cs
// @purpose Registers Greenroom suite manifest under Mother\manifests\greenroom.manifest.json via IPlatformRegistry and KnownFolderPaths.
// @inputs Target install directory, InstallerOptions, InstallerRuntimeContext, and log directory (@depends-on Foundation.Core, Foundation.Installer)
// @outputs Registered manifest path in Mother\manifests\greenroom.manifest.json (@exports Program.TryRegisterMotherManifest, Program.TryUnregisterMotherManifest)

using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    internal static string? TryRegisterMotherManifest(
        string installDir,
        InstallerOptions options,
        InstallerRuntimeContext runtimeCtx,
        string? logDir)
    {
        if (options.DryRun || (!OperatingSystem.IsWindows() && !runtimeCtx.IsSandboxActive))
            return null;

        if (!runtimeCtx.ShouldSuppressHostMutations)
        {
            RecordSuiteInstallRegistry(runtimeCtx, installDir, options.SuiteDir, options.ProfileId ?? "full");
        }

        string? srcManifest = ResolveSourceSuiteManifestPath(installDir);
        if (string.IsNullOrWhiteSpace(srcManifest) || !File.Exists(srcManifest))
            return null;

        string? motherDir = ResolveMotherDaemonDirectory(installDir, options, runtimeCtx);
        if (string.IsNullOrWhiteSpace(motherDir) || !Directory.Exists(motherDir))
            return null;

        try
        {
            if (runtimeCtx.IsSandboxActive)
                motherDir = SandboxEnvironmentSeam.AssertSandboxPathIfActive(motherDir, runtimeCtx.Sandbox);

            string manifestsDir = Path.Combine(motherDir, "manifests");
            Directory.CreateDirectory(manifestsDir);
            string dstManifest = Path.Combine(manifestsDir, "greenroom.manifest.json");
            if (File.Exists(dstManifest)) ForceDeleteFile(dstManifest);
            File.Copy(srcManifest, dstManifest, overwrite: true);
            WriteLog(logDir, "INFO", $"Registered Greenroom suite manifest with Mother: '{dstManifest}'.");
            return dstManifest;
        }
        catch (Exception ex) when (!SandboxEnvironmentSeam.IsContainmentViolation(ex))
        {
            WriteLog(logDir, "WARN", $"Skipped Mother manifest registration: {ex.Message}");
            return null;
        }
    }

    internal static void TryUnregisterMotherManifest(
        string? recordedManifestPath,
        InstallerRuntimeContext runtimeCtx,
        string? logDir)
    {
        if (!OperatingSystem.IsWindows() && !runtimeCtx.IsSandboxActive)
            return;

        if (!runtimeCtx.ShouldSuppressHostMutations)
        {
            try
            {
                runtimeCtx.Registry.DeleteSubKeyTree(PlatformRegistryHive.CurrentUser, StreamItAllGreenroomRegistrySubKey, throwOnMissingSubKey: false);
                runtimeCtx.Registry.DeleteSubKeyTree(PlatformRegistryHive.CurrentUser, MotherAppRegistrationSubKey, throwOnMissingSubKey: false);
            }
            catch { /* Ignore registry cleanup error */ }

            try
            {
                if (!string.IsNullOrWhiteSpace(recordedManifestPath) && File.Exists(recordedManifestPath))
                {
                    if (runtimeCtx.IsSandboxActive)
                        SandboxEnvironmentSeam.AssertSandboxPathIfActive(recordedManifestPath, runtimeCtx.Sandbox);
                    File.Delete(recordedManifestPath);
                    WriteLog(logDir, "INFO", $"Removed Mother suite manifest registration: '{recordedManifestPath}'.");
                }
            }
            catch { /* Ignore non-fatal cleanup error */ }
        }
    }

    private static void RecordSuiteInstallRegistry(InstallerRuntimeContext runtimeCtx, string installDir, string? suiteDir, string profileId)
    {
        if (runtimeCtx.ShouldSuppressHostMutations || runtimeCtx.Registry is NoOpPlatformRegistry)
            return;

        try
        {
            string resolvedSuite = !string.IsNullOrWhiteSpace(suiteDir)
                ? Path.GetFullPath(suiteDir.Trim())
                : (Path.GetDirectoryName(Path.GetFullPath(installDir)) ?? installDir);
            var values = new Dictionary<string, PlatformRegistryValue>
            {
                ["AppId"] = new(PlatformRegistryValueKind.String, "greenroom"),
                ["InstallDir"] = new(PlatformRegistryValueKind.String, Path.GetFullPath(installDir)),
                ["SuiteDir"] = new(PlatformRegistryValueKind.String, resolvedSuite),
                ["Profile"] = new(PlatformRegistryValueKind.String, profileId)
            };
            runtimeCtx.Registry.SetValues(PlatformRegistryHive.CurrentUser, StreamItAllGreenroomRegistrySubKey, values);
            runtimeCtx.Registry.SetValues(PlatformRegistryHive.CurrentUser, MotherAppRegistrationSubKey, values);
        }
        catch { /* Ignore optional registry metadata error */ }
    }

    private static string? ResolveSourceSuiteManifestPath(string installDir)
    {
        string[] candidates =
        [
            Path.Combine(installDir, "app", "suite.manifest.json"),
            Path.Combine(installDir, "suite.manifest.json")
        ];
        return candidates.FirstOrDefault(File.Exists);
    }

    private static string? ResolveMotherDaemonDirectory(string installDir, InstallerOptions options, InstallerRuntimeContext runtimeCtx)
    {
        if (runtimeCtx.IsStage1Isolated)
        {
            if (string.IsNullOrWhiteSpace(options.SuiteDir)) return null;
            string boundary = !string.IsNullOrWhiteSpace(options.ActiveDataDir)
                ? (Directory.GetParent(Path.GetFullPath(options.ActiveDataDir))?.FullName ?? options.ActiveDataDir)
                : (options.ActiveAppHome ?? installDir);
            string suiteFull = EnsureWithinBoundary(boundary, options.SuiteDir);
            string nestedMother = Path.Combine(suiteFull, "Mother");
            return Directory.Exists(nestedMother) ? nestedMother : (Directory.Exists(suiteFull) ? suiteFull : null);
        }

        var candidates = new List<string>();
        if (!string.IsNullOrWhiteSpace(options.SuiteDir))
        {
            string suiteFull = Path.GetFullPath(options.SuiteDir.Trim());
            candidates.Add(Path.Combine(suiteFull, "Mother"));
            candidates.Add(suiteFull);
        }

        string? installParent = Path.GetDirectoryName(Path.GetFullPath(installDir));
        if (!string.IsNullOrWhiteSpace(installParent))
        {
            candidates.Add(Path.Combine(installParent, "Mother"));
            candidates.Add(installParent);
        }

        try
        {
            string? regDir = runtimeCtx.Registry.GetValue(PlatformRegistryHive.CurrentUser, MotherInstallRegistrySubKey, "InstallDir")?.AsString();
            if (!string.IsNullOrWhiteSpace(regDir))
                candidates.Add(Path.GetFullPath(regDir.Trim()));
        }
        catch { /* Ignore registry lookup failure */ }

        string localAppData = KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, GetEffectiveSandbox(runtimeCtx));
        candidates.Add(Path.Combine(localAppData, "Programs", "StreamItAll", "Mother", "Mother"));
        candidates.Add(Path.Combine(localAppData, "Programs", "StreamItAll", "Mother"));

        return candidates
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .FirstOrDefault(dir =>
                (!runtimeCtx.IsSandboxActive || SandboxEnvironmentSeam.IsPathWithinSandbox(dir, runtimeCtx.Sandbox)) &&
                Directory.Exists(dir) &&
                (File.Exists(Path.Combine(dir, "Mother.exe")) ||
                 File.Exists(Path.Combine(dir, "app", "Mother.App.exe")) ||
                 Directory.Exists(Path.Combine(dir, "manifests")) ||
                 Directory.Exists(Path.Combine(dir, "data", "manifests"))));
    }
}
