// @module Greenroom.Installer.Tests.PathPrecedenceAndSandbox
// @purpose Tests 6-step path precedence, LastInstallBaseDir, manifest DTOs, ExitCode=3 containment, AGENTS purge, and IsOwnInstallManifest.
// @inputs InstallerTestFixtureHelper sandboxes and Dev/installer/Greenroom.Installer/installer.manifest.json (@depends-on Xunit, Foundation.Installer)
// @outputs Verified path precedence, containment guards, Mother registration path, and product ownership tests (@exports PathPrecedenceAndSandboxTests)

using System.Text;
using System.Text.Json;
using Foundation.Core;
using Foundation.Installer;
using Xunit;

namespace Greenroom.Installer.Tests;

public sealed class PathPrecedenceAndSandboxTests
{
    [Fact]
    public void SixStepPathPrecedence_ResolvesExpectedTargetsAndHistory()
    {
        using var fx = new InstallerTestFixtureHelper();
        fx.SetEnv("APP_ISOLATED_TEST", null);
        fx.SetEnv("GREENROOM_HOME", null);
        fx.SetEnv("MOTHER_HOME", null);

        (SandboxResolution sbx, InstallerRuntimeContext ctx, JsonHivePlatformRegistry hive, string cDrive, string dDrive) = fx.CreateSandboxContext();

        // Step 6: Fresh standalone fallback (%LOCALAPPDATA%\Programs\Greenroom)
        InstallTargetResolution step6 = Program.ResolveInstallTarget(ctx);
        string expectedStandalone = Path.Combine(cDrive, "Users", "Sandbox", "AppData", "Local", "Programs", "Greenroom");
        Assert.Equal(expectedStandalone, step6.InstallDir);
        Assert.Equal("Greenroom", step6.UmbrellaSubpath);
        Assert.False(step6.IsMotherSuiteMode);
        Assert.Equal("fresh-standalone-fallback", step6.ResolutionStep);

        // Step 5: LastInstallBaseDir via TrySaveLastInstallBaseDir + TryResolveSuggestedInstallDirFromHistory
        string customStandalone = Path.Combine(dDrive, "Apps", "Greenroom");
        Assert.True(Foundation.Installer.InstallerEngine.TrySaveLastInstallBaseDir(hive, customStandalone, "Greenroom", sbx, suppressHostMutations: false));
        InstallTargetResolution step5 = Program.ResolveInstallTarget(ctx);
        Assert.Equal(customStandalone, step5.InstallDir);
        Assert.Equal("last-install-base-dir", step5.ResolutionStep);

        // Step 4: Detected Mother suite directory -> <MotherSuiteDir>\Greenroom\
        string motherUmbrella = Path.Combine(cDrive, "Users", "Sandbox", "AppData", "Local", "Programs", "StreamItAll", "Mother");
        string motherDaemon = Path.Combine(motherUmbrella, "Mother");
        Directory.CreateDirectory(Path.Combine(motherDaemon, "manifests"));
        InstallTargetResolution step4 = Program.ResolveInstallTarget(ctx);
        Assert.Equal(Path.Combine(motherUmbrella, "Greenroom"), step4.InstallDir);
        Assert.True(step4.IsMotherSuiteMode);
        Assert.Equal(Path.Combine("StreamItAll", "Mother", "Greenroom"), step4.UmbrellaSubpath);
        Assert.Equal("mother-suite-detected", step4.ResolutionStep);

        // Step 4 override: explicit Full Suite toggle = false stays standalone even when Mother exists
        InstallTargetResolution step4Off = Program.ResolveInstallTarget(ctx, fullSuiteToggle: false);
        Assert.False(step4Off.IsMotherSuiteMode);
        Assert.Equal("Greenroom", step4Off.UmbrellaSubpath);

        // Step 3: Existing registered Greenroom install via IPlatformRegistry + IsOwnInstallManifest
        string regDir = Path.Combine(dDrive, "ExistingGreenroom");
        Directory.CreateDirectory(regDir);
        File.WriteAllText(Path.Combine(regDir, Program.InstallManifestFileName), "{\"product_name\":\"Greenroom\",\"version\":\"0.2.1\"}");
        hive.SetValue(PlatformRegistryHive.CurrentUser, Program.StreamItAllGreenroomRegistrySubKey, "InstallDir", regDir);
        InstallTargetResolution step3 = Program.ResolveInstallTarget(ctx);
        Assert.Equal(regDir, step3.InstallDir);
        Assert.Equal("existing-registry-install", step3.ResolutionStep);

        // Step 2: Env GREENROOM_HOME & MOTHER_HOME
        string envMother = Path.Combine(dDrive, "EnvMother");
        fx.SetEnv("MOTHER_HOME", envMother);
        Assert.Equal(Path.Combine(envMother, "Greenroom"), Program.ResolveInstallTarget(ctx).InstallDir);
        string envGreenroom = Path.Combine(dDrive, "EnvGreenroom");
        fx.SetEnv("GREENROOM_HOME", envGreenroom);
        Assert.Equal(envGreenroom, Program.ResolveInstallTarget(ctx).InstallDir);

        // Step 1: CLI --suite-dir & --install-dir win over everything
        string cliSuite = Path.Combine(dDrive, "CliSuite");
        Assert.Equal(Path.Combine(cliSuite, "Greenroom"), Program.ResolveInstallTarget(ctx, explicitSuiteDir: cliSuite).InstallDir);
        string cliInstall = Path.Combine(dDrive, "CliExplicit");
        Assert.Equal(cliInstall, Program.ResolveInstallTarget(ctx, explicitInstallDir: cliInstall).InstallDir);
    }

    [Fact]
    public void InstallerManifestStore_ValidatesSchemaV2_SuiteId_And_DualDto()
    {
        string manifestPath = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "installer", "Greenroom.Installer", "installer.manifest.json"));
        Assert.True(File.Exists(manifestPath), $"Expected manifest at {manifestPath}");
        string raw = File.ReadAllText(manifestPath, Encoding.UTF8);

        InstallerMarkupManifest markup = InstallerManifestStore.LoadFromJson(raw);
        Assert.Equal(2, markup.SchemaVersion);
        Assert.Equal("greenroom", markup.AppId);
        Assert.Equal("mother", markup.EffectiveSuiteId);
        Assert.False(markup.AllowAutoStart);

        DualInstallerManifestDto? dual = JsonSerializer.Deserialize<DualInstallerManifestDto>(raw, Program.JsonOpts);
        Assert.NotNull(dual);
        Assert.Equal("onlineSetup", dual!.InstallerDeliveryKind);
        Assert.StartsWith("https://", dual.PayloadDownloadUrl);
        Assert.NotEmpty(dual.PayloadAssets);
    }

    [Fact]
    public void SandboxContainment_EscapingPaths_ReturnExitCode3()
    {
        using var fx = new InstallerTestFixtureHelper();
        fx.SetEnv("APP_ISOLATED_TEST", null);
        (_, InstallerRuntimeContext ctx, _, _, _) = fx.CreateSandboxContext();

        string escapedHostPath = Path.Combine(Path.GetTempPath(), $"HostEscape_{Guid.NewGuid():N}");
        int installCode = Program.RunInstall(
            "0.3.0", escapedHostPath, null, createShortcuts: false, skipVenv: true, forcePip: false,
            enableAutostart: false, noDaemonRestart: true, dryRun: false, activeDataDir: null,
            activeAppHome: null, activeLogDir: null, runtimeCtx: ctx);
        Assert.Equal(SandboxEnvironmentSeam.ContainmentExitCode, installCode);

        int uninstallCode = Program.RunUninstall(
            "0.3.0", escapedHostPath, purgeData: false, dryRun: false,
            activeDataDir: null, activeAppHome: null, activeLogDir: null, runtimeCtx: ctx);
        Assert.Equal(SandboxEnvironmentSeam.ContainmentExitCode, uninstallCode);
    }

    [Fact]
    public void UpgradePurgesStaleAgentsMd_RegistersInManifests_And_GuardsOwnership()
    {
        using var fx = new InstallerTestFixtureHelper();
        fx.SetEnv("APP_ISOLATED_TEST", null);
        (_, InstallerRuntimeContext ctx, JsonHivePlatformRegistry hive, string cDrive, string dDrive) = fx.CreateSandboxContext();

        (_, _, string portablePath) = InstallerTestFixtureHelper.CreatePortableTestZip(Path.Combine(dDrive, "bundle.zip"));
        string motherUmbrella = Path.Combine(cDrive, "Users", "Sandbox", "AppData", "Local", "Programs", "StreamItAll", "Mother");
        string motherDaemon = Path.Combine(motherUmbrella, "Mother");
        Directory.CreateDirectory(motherDaemon);
        File.WriteAllText(Path.Combine(motherDaemon, "Mother.exe"), "stub");

        string installDir = Path.Combine(motherUmbrella, "Greenroom");
        Directory.CreateDirectory(installDir);
        string staleAgents = Path.Combine(installDir, "AGENTS.md");
        File.WriteAllText(staleAgents, "# stale agents file");

        int okCode = Program.RunInstall(
            "0.3.0", installDir, null, createShortcuts: true, skipVenv: true, forcePip: false,
            enableAutostart: false, noDaemonRestart: true, dryRun: false, activeDataDir: null,
            activeAppHome: null, activeLogDir: null, suiteDir: motherUmbrella, profileId: "lightweight",
            runtimeCtx: ctx, offlineBundlePath: portablePath, fullSuiteToggle: true,
            transferHooks: new OnlineTransferTestHooks(AllowUnpinnedOfflineBundle: true));

        Assert.Equal(0, okCode);
        Assert.False(File.Exists(staleAgents));
        Assert.True(File.Exists(Path.Combine(motherDaemon, "manifests", "greenroom.manifest.json")));
        Assert.False(Directory.Exists(Path.Combine(motherDaemon, "data", "manifests")));
        Assert.True(hive.SubKeyExists(PlatformRegistryHive.CurrentUser, Program.WindowsUninstallRegistrySubKey));

        // Seed user DB then uninstall without --purge-data: preserves data/ while removing binaries & Mother manifest
        string userDb = Path.Combine(installDir, "data", "greenroom.db");
        File.WriteAllText(userDb, "user-db");
        int uninstCode = Program.RunUninstall(
            "0.3.0", installDir, purgeData: false, dryRun: false, null, null, null, runtimeCtx: ctx);
        Assert.Equal(0, uninstCode);
        Assert.True(File.Exists(userDb));
        Assert.False(File.Exists(Path.Combine(motherDaemon, "manifests", "greenroom.manifest.json")));
        Assert.False(hive.SubKeyExists(PlatformRegistryHive.CurrentUser, Program.WindowsUninstallRegistrySubKey));

        // Foreign ownership guard on BOTH upgrade and uninstall
        string foreignDir = Path.Combine(dDrive, "ForeignApp");
        Directory.CreateDirectory(foreignDir);
        string foreignManifest = Path.Combine(foreignDir, Program.InstallManifestFileName);
        File.WriteAllText(foreignManifest, "{\"product_name\":\"ShadowLibrary\",\"appId\":\"shadowlibrary\",\"version\":\"0.5.0\"}");

        int blockedUpgrade = Program.RunInstall(
            "0.3.0", foreignDir, null, false, true, false, false, true, false, null, null, null,
            runtimeCtx: ctx, offlineBundlePath: portablePath,
            transferHooks: new OnlineTransferTestHooks(AllowUnpinnedOfflineBundle: true));
        Assert.Equal(1, blockedUpgrade);
        Assert.True(File.Exists(foreignManifest));

        int blockedUninstall = Program.RunUninstall(
            "0.3.0", foreignDir, purgeData: true, dryRun: false, null, null, null, runtimeCtx: ctx);
        Assert.Equal(1, blockedUninstall);
        Assert.True(File.Exists(foreignManifest));
    }
}
