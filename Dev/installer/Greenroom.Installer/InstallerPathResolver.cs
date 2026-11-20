// @module InstallerPathResolver.cs
// @purpose 6-step smart install path precedence, Mother suite detection, IsOwnInstallManifest guard, and sandbox-safe log directory resolution.
// @inputs InstallerRuntimeContext, CLI flags, environment variables, and IPlatformRegistry (@depends-on Foundation.Core, Foundation.Installer)
// @outputs InstallTargetResolution, activeLogDir, and validated target directories (@exports Program.ResolveInstallTarget, Program.IsOwnInstallManifest, Program.ResolveActiveLogDirectory)

using System.Text;
using System.Text.Json;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    internal const string StandaloneUmbrellaSubpath = "Greenroom";
    internal static readonly string SuiteUmbrellaSubpath = Path.Combine("StreamItAll", "Mother", "Greenroom");

    internal static InstallTargetResolution ResolveInstallTarget(
        InstallerRuntimeContext runtimeCtx,
        string? explicitInstallDir = null,
        string? explicitSuiteDir = null,
        bool? fullSuiteToggle = null,
        string? activeAppHome = null)
    {
        ArgumentNullException.ThrowIfNull(runtimeCtx);

        // Step 1: CLI --install-dir / --suite-dir (and config override)
        string? cliInstall = FirstNonEmpty(explicitInstallDir, runtimeCtx.ConfigOverride?.InstallDirectory);
        if (!string.IsNullOrWhiteSpace(cliInstall))
        {
            string fullCli = NormalizeAndGuardTargetPath(cliInstall, runtimeCtx, activeAppHome);
            string? detectedSuite = TryDetectMotherSuiteDirectory(runtimeCtx, explicitSuiteDir);
            bool suiteMode = fullSuiteToggle ?? (!string.IsNullOrWhiteSpace(explicitSuiteDir) || IsChildOfMotherSuite(fullCli, detectedSuite));
            return new InstallTargetResolution(fullCli, suiteMode ? (detectedSuite ?? Path.GetDirectoryName(fullCli)) : null, suiteMode, suiteMode ? SuiteUmbrellaSubpath : StandaloneUmbrellaSubpath, "cli-install-dir");
        }

        string? cliSuite = FirstNonEmpty(explicitSuiteDir, runtimeCtx.ConfigOverride?.SuiteInstallDir);
        if (!string.IsNullOrWhiteSpace(cliSuite))
        {
            string suiteFull = NormalizeAndGuardTargetPath(cliSuite, runtimeCtx, activeAppHome);
            string target = NormalizeAndGuardTargetPath(Path.Combine(suiteFull, "Greenroom"), runtimeCtx, activeAppHome);
            return new InstallTargetResolution(target, suiteFull, true, SuiteUmbrellaSubpath, "cli-suite-dir");
        }

        // Stage 1 isolated runs without explicit --install-dir / --suite-dir bind inside DataDirectory / APP_HOME
        if (runtimeCtx.IsStage1Isolated)
        {
            string? stage1Root = FirstNonEmpty(runtimeCtx.DataDirectory, activeAppHome);
            if (string.IsNullOrWhiteSpace(stage1Root))
                SandboxEnvironmentSeam.ThrowContainmentViolation("Fail-closed isolation violation: Stage 1 isolated mode requires --data-dir or APP_HOME.");
            string target = NormalizeAndGuardTargetPath(Path.Combine(stage1Root, "installed-app"), runtimeCtx, activeAppHome);
            return new InstallTargetResolution(target, null, false, StandaloneUmbrellaSubpath, "stage1-isolated");
        }

        // Step 2: Environment GREENROOM_HOME / MOTHER_HOME
        string? envGreenroom = Environment.GetEnvironmentVariable("GREENROOM_HOME");
        if (!string.IsNullOrWhiteSpace(envGreenroom))
        {
            string fullEnv = NormalizeAndGuardTargetPath(envGreenroom, runtimeCtx, activeAppHome);
            bool inSuite = fullSuiteToggle ?? IsMotherFolderParent(fullEnv);
            return new InstallTargetResolution(fullEnv, inSuite ? Path.GetDirectoryName(fullEnv) : null, inSuite, inSuite ? SuiteUmbrellaSubpath : StandaloneUmbrellaSubpath, "env-greenroom-home");
        }

        string? envMother = Environment.GetEnvironmentVariable("MOTHER_HOME");
        if (!string.IsNullOrWhiteSpace(envMother))
        {
            string motherFull = NormalizeAndGuardTargetPath(envMother, runtimeCtx, activeAppHome);
            string target = NormalizeAndGuardTargetPath(Path.Combine(motherFull, "Greenroom"), runtimeCtx, activeAppHome);
            return new InstallTargetResolution(target, motherFull, true, SuiteUmbrellaSubpath, "env-mother-home");
        }

        // Step 3: Existing registered/detected Greenroom install (via runtimeCtx.Registry + IsOwnInstallManifest)
        if (TryResolveRegisteredInstall(runtimeCtx, out string regInstall))
        {
            bool inSuite = fullSuiteToggle ?? IsMotherFolderParent(regInstall);
            return new InstallTargetResolution(regInstall, inSuite ? Path.GetDirectoryName(regInstall) : null, inSuite, inSuite ? SuiteUmbrellaSubpath : StandaloneUmbrellaSubpath, "existing-registry-install");
        }

        string? detectedMother = TryDetectMotherSuiteDirectory(runtimeCtx, null);
        string standaloneDefault = ResolveStandaloneFallbackDir(runtimeCtx);

        // Check un-registered existing Greenroom folders (Mother sibling or standalone) before fresh resolution
        if (detectedMother is not null)
        {
            string existingSuiteApp = Path.Combine(detectedMother, "Greenroom");
            if (Directory.Exists(existingSuiteApp) && IsOwnInstallManifest(existingSuiteApp, out _, out _, allowLegacyWithoutManifest: true) &&
                HasExistingInstallationArtifacts(existingSuiteApp))
            {
                return new InstallTargetResolution(Path.GetFullPath(existingSuiteApp), detectedMother, true, SuiteUmbrellaSubpath, "existing-detected-suite-install");
            }
        }
        if (Directory.Exists(standaloneDefault) && IsOwnInstallManifest(standaloneDefault, out _, out _, allowLegacyWithoutManifest: true) &&
            HasExistingInstallationArtifacts(standaloneDefault))
        {
            return new InstallTargetResolution(standaloneDefault, null, false, StandaloneUmbrellaSubpath, "existing-detected-standalone-install");
        }

        // Step 4: Mother detected or explicit GUI Full Suite toggle -> <MotherSuiteDir>\Greenroom\
        if ((fullSuiteToggle == true || (fullSuiteToggle is null && detectedMother is not null)) && !string.IsNullOrWhiteSpace(detectedMother))
        {
            string suiteTarget = NormalizeAndGuardTargetPath(Path.Combine(detectedMother, "Greenroom"), runtimeCtx, activeAppHome);
            return new InstallTargetResolution(suiteTarget, detectedMother, true, SuiteUmbrellaSubpath, "mother-suite-detected");
        }
        if (fullSuiteToggle == true)
        {
            string defaultMother = GetDefaultMotherSuiteDirectory(runtimeCtx);
            string suiteTarget = NormalizeAndGuardTargetPath(Path.Combine(defaultMother, "Greenroom"), runtimeCtx, activeAppHome);
            return new InstallTargetResolution(suiteTarget, defaultMother, true, SuiteUmbrellaSubpath, "full-suite-toggle");
        }

        // Step 5: Foundation InstallerEngine.TryResolveSuggestedInstallDirFromHistory (LastInstallBaseDir)
        if (!runtimeCtx.ShouldSuppressHostMutations &&
            Foundation.Installer.InstallerEngine.TryResolveSuggestedInstallDirFromHistory(
                runtimeCtx.Registry, StandaloneUmbrellaSubpath, GetEffectiveSandbox(runtimeCtx), out string historyDir))
        {
            string safeHistory = NormalizeAndGuardTargetPath(historyDir, runtimeCtx, activeAppHome);
            return new InstallTargetResolution(safeHistory, null, false, StandaloneUmbrellaSubpath, "last-install-base-dir");
        }

        // Step 6: Fresh standalone fallback (%LOCALAPPDATA%\Programs\Greenroom)
        return new InstallTargetResolution(standaloneDefault, null, false, StandaloneUmbrellaSubpath, "fresh-standalone-fallback");
    }

    internal static SandboxResolution GetEffectiveSandbox(InstallerRuntimeContext ctx) =>
        ctx.IsSandboxActive ? ctx.Sandbox : SandboxResolution.Inactive;

    internal static string ResolveStandaloneFallbackDir(InstallerRuntimeContext runtimeCtx)
    {
        string localAppData = KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, GetEffectiveSandbox(runtimeCtx));
        return Path.GetFullPath(Path.Combine(localAppData, "Programs", "Greenroom"));
    }

    internal static string GetDefaultMotherSuiteDirectory(InstallerRuntimeContext runtimeCtx)
    {
        string localAppData = KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, GetEffectiveSandbox(runtimeCtx));
        return Path.GetFullPath(Path.Combine(localAppData, "Programs", "StreamItAll", "Mother"));
    }

    internal static string? TryDetectMotherSuiteDirectory(InstallerRuntimeContext runtimeCtx, string? explicitSuiteDir)
    {
        if (!string.IsNullOrWhiteSpace(explicitSuiteDir))
            return Path.GetFullPath(explicitSuiteDir.Trim());

        try
        {
            string? regSuite = runtimeCtx.Registry.GetValue(PlatformRegistryHive.CurrentUser, MotherInstallRegistrySubKey, "SuiteDir")?.AsString();
            if (!string.IsNullOrWhiteSpace(regSuite) && IsValidCandidateMotherRoot(regSuite, runtimeCtx))
                return Path.GetFullPath(regSuite.Trim());

            string? regMotherInstall = runtimeCtx.Registry.GetValue(PlatformRegistryHive.CurrentUser, MotherInstallRegistrySubKey, "InstallDir")?.AsString();
            if (!string.IsNullOrWhiteSpace(regMotherInstall) && IsValidCandidateMotherRoot(regMotherInstall, runtimeCtx))
                return NormalizeMotherUmbrellaFromInstallDir(regMotherInstall);
        }
        catch { /* Ignore registry lookup failure */ }

        var candidates = new List<string> { GetDefaultMotherSuiteDirectory(runtimeCtx) };
        if (runtimeCtx.IsSandboxActive && !string.IsNullOrWhiteSpace(runtimeCtx.Sandbox.StorageDriveRoot))
            candidates.Add(Path.Combine(runtimeCtx.Sandbox.StorageDriveRoot, "Programs", "StreamItAll", "Mother"));

        foreach (string cand in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (IsMotherInstalledAtUmbrella(cand)) return Path.GetFullPath(cand);
        }
        return null;
    }

    private static bool IsValidCandidateMotherRoot(string rawPath, InstallerRuntimeContext runtimeCtx)
    {
        string full = Path.GetFullPath(rawPath.Trim());
        if (runtimeCtx.IsSandboxActive && !SandboxEnvironmentSeam.IsPathWithinSandbox(full, runtimeCtx.Sandbox))
            return false;
        return Directory.Exists(full);
    }

    private static string NormalizeMotherUmbrellaFromInstallDir(string rawMotherDir)
    {
        string full = Path.GetFullPath(rawMotherDir.Trim()).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (Directory.Exists(Path.Combine(full, "manifests")) || Directory.Exists(Path.Combine(full, "data", "manifests")) ||
            File.Exists(Path.Combine(full, "Mother.exe")) || File.Exists(Path.Combine(full, "app", "Mother.App.exe")))
        {
            string? parent = Path.GetDirectoryName(full);
            if (!string.IsNullOrWhiteSpace(parent) && string.Equals(Path.GetFileName(parent), "Mother", StringComparison.OrdinalIgnoreCase))
                return parent;
        }
        return full;
    }

    private static bool IsMotherInstalledAtUmbrella(string umbrellaDir)
    {
        if (!Directory.Exists(umbrellaDir)) return false;
        string daemonDir = Path.Combine(umbrellaDir, "Mother");
        return HasMotherMarkers(daemonDir) || HasMotherMarkers(umbrellaDir);
    }

    private static bool HasMotherMarkers(string dir) =>
        Directory.Exists(dir) &&
        (File.Exists(Path.Combine(dir, "Mother.exe")) ||
         File.Exists(Path.Combine(dir, "app", "Mother.App.exe")) ||
         Directory.Exists(Path.Combine(dir, "manifests")) ||
         Directory.Exists(Path.Combine(dir, "data", "manifests")));

    private static bool TryResolveRegisteredInstall(InstallerRuntimeContext runtimeCtx, out string resolvedPath)
    {
        resolvedPath = string.Empty;
        if (runtimeCtx.ShouldSuppressHostMutations) return false;
        string?[] candidates =
        [
            runtimeCtx.Registry.GetValue(PlatformRegistryHive.CurrentUser, StreamItAllGreenroomRegistrySubKey, "InstallDir")?.AsString(),
            runtimeCtx.Registry.GetValue(PlatformRegistryHive.CurrentUser, WindowsUninstallRegistrySubKey, "InstallLocation")?.AsString()
        ];

        foreach (string? raw in candidates)
        {
            if (string.IsNullOrWhiteSpace(raw)) continue;
            string full = Path.GetFullPath(raw.Trim());
            if (runtimeCtx.IsSandboxActive && !SandboxEnvironmentSeam.IsPathWithinSandbox(full, runtimeCtx.Sandbox)) continue;
            if (!Directory.Exists(full)) continue;
            if (!IsOwnInstallManifest(full, out _, out _, allowLegacyWithoutManifest: true)) continue;
            resolvedPath = full;
            return true;
        }
        return false;
    }

    internal static bool IsOwnInstallManifest(
        string installDir, out InstallManifest? manifest, out string? foreignProduct, bool allowLegacyWithoutManifest = false)
    {
        manifest = null;
        foreignProduct = null;
        if (string.IsNullOrWhiteSpace(installDir)) return false;

        foreach (string candidate in new[] { Path.Combine(installDir, InstallManifestFileName), Path.Combine(installDir, "app", InstallManifestFileName) })
        {
            if (!File.Exists(candidate)) continue;
            try
            {
                string raw = File.ReadAllText(candidate, Encoding.UTF8);
                manifest = JsonSerializer.Deserialize<InstallManifest>(raw, JsonOpts);
                string owner = FirstNonEmpty(manifest?.ProductName, manifest?.AppId) ?? string.Empty;
                if (owner.Equals("Greenroom", StringComparison.OrdinalIgnoreCase) || owner.Equals("greenroom", StringComparison.OrdinalIgnoreCase))
                    return true;
                foreignProduct = string.IsNullOrWhiteSpace(owner) ? "unknown" : owner;
                return false;
            }
            catch
            {
                foreignProduct = "corrupt-manifest";
                return false;
            }
        }
        return allowLegacyWithoutManifest;
    }

    internal static string NormalizeAndGuardTargetPath(string rawPath, InstallerRuntimeContext runtimeCtx, string? activeAppHome)
    {
        string candidate = Path.GetFullPath(rawPath.Trim());
        string? root = Path.GetPathRoot(candidate);
        if (string.IsNullOrWhiteSpace(root) ||
            string.Equals(candidate.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Refusing to target a filesystem drive root.");
        }

        if (runtimeCtx.ShouldSuppressLiveHostMutations &&
            FileLockRetryHelper.IsCanonicalProductionOrSystemPath(candidate, GetEffectiveSandbox(runtimeCtx)))
        {
            SandboxEnvironmentSeam.ThrowContainmentViolation($"Fail-closed isolation violation: refusing canonical host path '{candidate}'.");
        }
        if (runtimeCtx.IsSandboxActive)
            candidate = SandboxEnvironmentSeam.AssertSandboxPathIfActive(candidate, runtimeCtx.Sandbox);

        if (runtimeCtx.IsStage1Isolated)
        {
            string? boundary = !string.IsNullOrWhiteSpace(runtimeCtx.DataDirectory)
                ? (Directory.GetParent(Path.GetFullPath(runtimeCtx.DataDirectory))?.FullName ?? runtimeCtx.DataDirectory)
                : activeAppHome;
            if (string.IsNullOrWhiteSpace(boundary))
                SandboxEnvironmentSeam.ThrowContainmentViolation("Fail-closed isolation violation: isolated mode requires --data-dir or APP_HOME.");
            try
            {
                AppEnvironment.AssertPathContained(candidate, boundary);
                EnsureWithinBoundary(boundary, candidate);
            }
            catch (Exception ex) when (!SandboxEnvironmentSeam.IsContainmentViolation(ex))
            {
                SandboxEnvironmentSeam.ThrowContainmentViolation(ex.Message, ex);
            }
        }
        return candidate;
    }

    internal static string ResolveActiveLogDirectory(InstallerRuntimeContext runtimeCtx, string? installDir = null, string? activeAppHome = null)
    {
        if (!string.IsNullOrWhiteSpace(runtimeCtx.LogDirectory)) return runtimeCtx.LogDirectory;
        if (!string.IsNullOrWhiteSpace(installDir))
        {
            string candidate = Path.Combine(Path.GetFullPath(installDir), "logs");
            return runtimeCtx.IsSandboxActive ? SandboxEnvironmentSeam.AssertSandboxPathIfActive(candidate, runtimeCtx.Sandbox) : candidate;
        }
        if (runtimeCtx.IsSandboxActive)
        {
            string sandboxLocal = KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, runtimeCtx.Sandbox);
            return Path.Combine(sandboxLocal, "Temp", "Greenroom-Setup-logs");
        }
        if (runtimeCtx.IsIsolatedTestProcess)
        {
            string? isoRoot = FirstNonEmpty(runtimeCtx.DataDirectory, activeAppHome);
            return !string.IsNullOrWhiteSpace(isoRoot) ? Path.Combine(Path.GetFullPath(isoRoot), "logs") : string.Empty;
        }
        if (!string.IsNullOrWhiteSpace(runtimeCtx.DataDirectory)) return Path.Combine(runtimeCtx.DataDirectory, "logs");
        if (!string.IsNullOrWhiteSpace(activeAppHome)) return Path.Combine(Path.GetFullPath(activeAppHome), "logs");
        return Path.Combine(Path.GetTempPath(), "Greenroom-Setup-logs");
    }

    private static bool IsChildOfMotherSuite(string installDir, string? motherSuiteDir) =>
        !string.IsNullOrWhiteSpace(motherSuiteDir) &&
        string.Equals(Path.GetDirectoryName(installDir)?.TrimEnd(Path.DirectorySeparatorChar), motherSuiteDir.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase);

    private static bool IsMotherFolderParent(string installDir) =>
        string.Equals(Path.GetFileName(Path.GetDirectoryName(installDir.TrimEnd(Path.DirectorySeparatorChar))), "Mother", StringComparison.OrdinalIgnoreCase);

    private static string? FirstNonEmpty(params string?[] values) =>
        values.FirstOrDefault(v => !string.IsNullOrWhiteSpace(v))?.Trim();
}
