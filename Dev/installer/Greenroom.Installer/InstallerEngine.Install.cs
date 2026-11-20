// @module InstallerEngine.Install.cs
// @purpose Install/upgrade orchestration, DualSetup payload acquisition, ownership guard, and LastInstallBaseDir recording.
// @inputs Target install directory, Dual/Offline payload sources, InstallerRuntimeContext, and progress sink (@depends-on InstallerEngine.Extraction.cs, InstallerPathResolver.cs)
// @outputs Installed/upgraded Clean Release Root with install-manifest.json and granular progress (@exports Program.ExecuteInstallOperation, Program.RunInstall)

using System.Text;
using System.Text.Json;
using Foundation.Core;
using Foundation.Installer;

namespace Greenroom.Installer;

public static partial class Program
{
    public static int ExecuteInstallOperation(
        string version,
        string? explicitInstallDir,
        string? externalPayloadPath,
        bool createShortcuts,
        bool skipVenv,
        bool forcePip,
        bool enableAutostart,
        bool noDaemonRestart,
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
        return RunInstall(
            version, explicitInstallDir, externalPayloadPath, createShortcuts, skipVenv, forcePip,
            enableAutostart, noDaemonRestart, dryRun, activeDataDir, activeAppHome, activeLogDir,
            progress, null, null, false, ctx, null, null, null, null, null, null);
    }

    internal static int RunInstall(
        string version,
        string? explicitInstallDir,
        string? externalPayloadPath,
        bool createShortcuts,
        bool skipVenv,
        bool forcePip,
        bool enableAutostart,
        bool noDaemonRestart,
        bool dryRun,
        string? activeDataDir,
        string? activeAppHome,
        string? activeLogDir,
        IProgress<string>? progress = null,
        string? suiteDir = null,
        string? profileId = null,
        bool migrateLegacy = false,
        InstallerRuntimeContext? runtimeCtx = null,
        string? offlineBundlePath = null,
        string? manifestPath = null,
        string? payloadUrlOverride = null,
        string? payloadShaOverride = null,
        bool? fullSuiteToggle = null,
        Action<int, string>? onProgress = null,
        OnlineTransferTestHooks? transferHooks = null)
    {
        InstallerRuntimeContext ctx = runtimeCtx ?? new InstallerRuntimeContext(activeDataDir, null, activeLogDir, dryRun, "GREENROOM");
        string? currentLogDir = string.IsNullOrWhiteSpace(activeLogDir) ? ResolveActiveLogDirectory(ctx, null, activeAppHome) : activeLogDir;
        void Report(int pct, string msg)
        {
            onProgress?.Invoke(pct, msg);
            progress?.Report(msg);
        }

        string? downloadedTempZip = null;
        try
        {
            Report(5, "Resolving installation destination...");
            InstallTargetResolution target = ResolveInstallTarget(ctx, explicitInstallDir, suiteDir, fullSuiteToggle, activeAppHome);
            string installDir = target.InstallDir;
            currentLogDir = string.IsNullOrWhiteSpace(activeLogDir) ? ResolveActiveLogDirectory(ctx, installDir, activeAppHome) : activeLogDir;

            DualInstallerManifestDto? dualManifest = transferHooks?.ManifestOverride ?? LoadDualInstallerManifest(typeof(Program).Assembly, manifestPath, ctx);
            bool hasOfflineBundle = !string.IsNullOrWhiteSpace(offlineBundlePath);
            bool isOfflineInstaller = IsOfflineInstaller(typeof(Program).Assembly, dualManifest);
            bool isOfflineMode = isOfflineInstaller || hasOfflineBundle;

            var options = new InstallerOptions
            {
                Version = version,
                ExplicitInstallDir = explicitInstallDir,
                ExternalPayloadPath = externalPayloadPath,
                OfflineBundlePath = offlineBundlePath,
                ManifestPath = manifestPath,
                PayloadUrlOverride = payloadUrlOverride,
                PayloadSha256Override = payloadShaOverride,
                CreateShortcuts = createShortcuts,
                SkipVenv = skipVenv,
                ForcePip = forcePip,
                EnableAutostart = enableAutostart,
                NoDaemonRestart = noDaemonRestart,
                ContainedMode = ctx.ShouldSuppressLiveHostMutations,
                TestMode = ctx.IsIsolatedTestProcess,
                DryRun = dryRun,
                MigrateLegacy = migrateLegacy,
                IsOfflineMode = isOfflineMode,
                FullSuiteToggle = fullSuiteToggle ?? target.IsMotherSuiteMode,
                SuiteDir = target.MotherSuiteDir ?? suiteDir,
                ProfileId = profileId,
                ActiveDataDir = activeDataDir,
                ActiveAppHome = activeAppHome,
                ActiveLogDir = currentLogDir
            };

            if (!IsOwnInstallManifest(installDir, out InstallManifest? previousManifest, out string? foreignProduct, allowLegacyWithoutManifest: true))
            {
                string ownErr = $"Refusing to overwrite '{installDir}': directory belongs to '{foreignProduct}', not Greenroom.";
                WriteLog(currentLogDir, "ERROR", ownErr);
                Console.Error.WriteLine($"[ERROR] {ownErr}");
                return 1;
            }

            bool isUpgrade = previousManifest != null || HasExistingInstallationArtifacts(installDir);
            string? previousVersion = previousManifest?.Version;

            if (dryRun)
            {
                Report(10, "Verifying release manifest/package for dry-run...");
                var dryVerify = VerifyOnlineOrOffline(externalPayloadPath, offlineBundlePath, manifestPath, payloadUrlOverride, payloadShaOverride, typeof(Program).Assembly, ctx, transferHooks);
                if (!dryVerify.Valid)
                {
                    WriteLog(currentLogDir, "ERROR", dryVerify.Message);
                    Console.Error.WriteLine($"[ERROR] {dryVerify.Message}");
                    return 1;
                }
                string modeLabel = isUpgrade ? $"upgrade (from v{previousVersion ?? "existing"})" : "fresh install";
                string dryMsg = $"[DRY-RUN] Verified payload ({dryVerify.SizeBytes:N0} bytes, {dryVerify.EntryCount} entries, SHA-256: {dryVerify.Sha256}); would perform {modeLabel} of Greenroom v{version} to '{installDir}' (Offline={isOfflineMode}, SkipVenv={skipVenv}, Shortcuts={createShortcuts}).";
                WriteLog(currentLogDir, "INFO", dryMsg);
                Console.WriteLine(dryMsg);
                return 0;
            }

            string? resolvedArchivePath = externalPayloadPath;
            string payloadSha;
            if (hasOfflineBundle)
            {
                Report(10, "Verifying offline Portable bundle...");
                string bundleFile = ResolveOfflineBundlePath(offlineBundlePath!, version, ctx);
                var bundleVerify = VerifyArchiveFile(bundleFile, dualManifest, transferHooks?.AllowUnpinnedOfflineBundle == true, payloadShaOverride);
                if (!bundleVerify.Valid)
                {
                    WriteLog(currentLogDir, "ERROR", bundleVerify.Message);
                    Console.Error.WriteLine($"[ERROR] {bundleVerify.Message}");
                    return 1;
                }
                resolvedArchivePath = bundleFile;
                payloadSha = bundleVerify.Sha256;
            }
            else if (!string.IsNullOrWhiteSpace(externalPayloadPath) || isOfflineInstaller)
            {
                Report(10, "Verifying embedded offline package...");
                var embVerify = VerifyEmbeddedOrExternalArchive(externalPayloadPath);
                if (!embVerify.Valid)
                {
                    WriteLog(currentLogDir, "ERROR", embVerify.Message);
                    Console.Error.WriteLine($"[ERROR] {embVerify.Message}");
                    return 1;
                }
                payloadSha = embVerify.Sha256;
            }
            else
            {
                Report(10, "Downloading verified Greenroom package...");
                if (dualManifest is null)
                    throw new InvalidOperationException(FormatThreePartOfflineError(version, "Missing embedded installer.manifest.json."));
                string dlUrl = ResolveEffectiveDownloadUrl(dualManifest, payloadUrlOverride);
                PayloadAssetEntry? asset = dualManifest.PayloadAssets.FirstOrDefault();
                string expectedSha = ResolveEffectiveExpectedSha256(asset, payloadShaOverride);
                long expectedSize = asset?.ExpectedSizeBytes ?? 0;
                string stageDir = ResolveOnlineDownloadStageDirectory(ctx, installDir, activeAppHome);
                string stageDest = Path.Combine(stageDir, $"Greenroom-v{version}-win-x64-Portable.zip");
                downloadedTempZip = DownloadAndVerifyOnlinePayloadAsync(
                    dlUrl, expectedSha, expectedSize, stageDest, version,
                    dualManifest.SuiteId ?? "mother", Report, transferHooks).GetAwaiter().GetResult();
                resolvedArchivePath = downloadedTempZip;
                payloadSha = expectedSha;
            }

            WriteLog(currentLogDir, "INFO",
                isUpgrade
                    ? $"Starting upgrade-safe Greenroom v{previousVersion ?? "existing"} -> v{version} at '{installDir}' (Step={target.ResolutionStep}, Offline={isOfflineMode}, SkipVenv={skipVenv})..."
                    : $"Starting Greenroom v{version} install to '{installDir}' (Step={target.ResolutionStep}, Offline={isOfflineMode}, SkipVenv={skipVenv})...");

            Report(36, "Stopping running Greenroom Companion instances...");
            bool wasDaemonRunning = TryStopExistingDaemon(installDir, currentLogDir);
            Directory.CreateDirectory(installDir);
            ClearReadOnlyAttributesRecursively(installDir);

            Report(38, "Creating atomic pre-upgrade database backup...");
            string? preUpgradeBackupPath = CreatePreUpgradeDatabaseBackup(installDir, version, currentLogDir);
            SanitizeCompanionLogIfPresent(Path.Combine(installDir, "data", "companion.log"), currentLogDir);

            Report(42, "Refreshing application files...");
            PrepareManagedDirectoriesForExtraction(installDir);

            var managedTopFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            int extractedFiles = ExtractPayloadToInstallDirectory(installDir, resolvedArchivePath, managedTopFiles, Report);

            Report(60, "Configuring runtime directories & templates...");
            MirrorAppTemplatesAndEnsureRuntimeDirs(installDir, managedTopFiles);
            MigrateLegacyFlatInstallIfRequested(installDir, options, ctx, currentLogDir);

            string uninstallerPath = Path.Combine(installDir, UninstallerExeName);
            string? selfExe = Environment.ProcessPath;
            if (!string.IsNullOrWhiteSpace(selfExe) && File.Exists(selfExe) &&
                selfExe.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) &&
                !Path.GetFileName(selfExe).Equals("dotnet.exe", StringComparison.OrdinalIgnoreCase) &&
                !Path.GetFileName(selfExe).Equals("testhost.exe", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(Path.GetFullPath(selfExe), Path.GetFullPath(uninstallerPath), StringComparison.OrdinalIgnoreCase))
            {
                if (File.Exists(uninstallerPath)) ForceDeleteFile(uninstallerPath);
                File.Copy(selfExe, uninstallerPath, overwrite: true);
                extractedFiles++;
            }

            if (!skipVenv)
            {
                BootstrapPythonEnvironment(installDir, currentLogDir, forcePip, isOfflineMode, version, Report, activeDataDir);
            }
            else if (isUpgrade)
            {
                Report(87, "Merging configuration templates...");
                TryMigrateConfigsWithAvailablePython(installDir, currentLogDir);
            }

            var createdShortcuts = new List<string>();
            if (createShortcuts && (OperatingSystem.IsWindows() || ctx.IsSandboxActive))
            {
                Report(93, "Creating Desktop and Start Menu shortcuts...");
                createdShortcuts.AddRange(CreateShortcuts(installDir, uninstallerPath, ctx, activeDataDir));
            }

            bool hadExistingAutostart = !string.IsNullOrWhiteSpace(previousManifest?.AutostartRecord) && File.Exists(previousManifest.AutostartRecord);
            if (!ctx.ShouldSuppressHostMutations && OperatingSystem.IsWindows())
            {
                string startupVbs = Path.Combine(KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.Startup, GetEffectiveSandbox(ctx)), "GreenroomCompanion.vbs");
                if (File.Exists(startupVbs)) hadExistingAutostart = true;
            }

            Report(95, "Registering uninstaller & suite metadata...");
            string? registryRecord = RegisterUninstaller(installDir, uninstallerPath, version, ctx);
            string? autostartRecord = (enableAutostart || hadExistingAutostart) ? ConfigureAutostart(installDir, ctx, activeDataDir) : null;
            string? motherRecord = target.IsMotherSuiteMode ? TryRegisterMotherManifest(installDir, options, ctx, currentLogDir) : null;

            if (!ctx.IsStage1Isolated)
            {
                Foundation.Installer.InstallerEngine.TrySaveLastInstallBaseDir(
                    ctx.Registry, installDir, target.UmbrellaSubpath, GetEffectiveSandbox(ctx), ctx.ShouldSuppressHostMutations);
            }

            var manifest = new InstallManifest
            {
                ProductName = "Greenroom",
                AppId = "greenroom",
                Version = version,
                InstalledAtUtc = DateTimeOffset.UtcNow,
                InstallDirectory = installDir,
                PayloadSha256 = payloadSha,
                ManagedDirectories = ["app", "greenroom", "scripts", "wheelhouse"],
                ManagedFiles = [.. managedTopFiles.OrderBy(x => x, StringComparer.OrdinalIgnoreCase)],
                CreatedShortcuts = createdShortcuts,
                UninstallRegistryRecord = registryRecord,
                AutostartRecord = autostartRecord,
                MotherManifestRecord = motherRecord,
                UmbrellaSubpath = target.UmbrellaSubpath
            };

            string manifestFile = Path.Combine(installDir, InstallManifestFileName);
            if (File.Exists(manifestFile)) ForceDeleteFile(manifestFile);
            File.WriteAllText(manifestFile, JsonSerializer.Serialize(manifest, JsonOpts), new UTF8Encoding(false));
            extractedFiles++;

            if (!ctx.ShouldSuppressLiveHostMutations && !noDaemonRestart && (wasDaemonRunning || enableAutostart || hadExistingAutostart))
            {
                Report(98, "Restarting Greenroom Companion daemon...");
                TryRestartDaemonAfterUpgrade(installDir, ctx, currentLogDir);
            }

            string actionVerb = isUpgrade ? "Upgraded" : "Installed";
            string backupNote = !string.IsNullOrWhiteSpace(preUpgradeBackupPath) ? $", pre-upgrade DB backup: '{Path.GetFileName(preUpgradeBackupPath)}'" : string.Empty;
            string okMsg = $"{actionVerb} Greenroom v{version} ({extractedFiles:N0} files, SHA-256: {payloadSha}{backupNote}) to '{installDir}'.";
            Report(100, okMsg);
            WriteLog(currentLogDir, "INFO", okMsg);
            Console.WriteLine(okMsg);
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
            WriteLog(currentLogDir, "ERROR", $"Install failed: {ex.Message}");
            Console.Error.WriteLine($"[ERROR] {ex.Message}");
            return 1;
        }
        finally
        {
            if (!string.IsNullOrWhiteSpace(downloadedTempZip) && File.Exists(downloadedTempZip))
                ForceDeleteFile(downloadedTempZip);
        }
    }

    private static string ResolveOnlineDownloadStageDirectory(InstallerRuntimeContext ctx, string installDir, string? activeAppHome)
    {
        if (ctx.IsSandboxActive)
            return Path.Combine(KnownFolderPaths.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, ctx.Sandbox), "Temp", "Greenroom-Setup-dl");
        if (ctx.IsStage1Isolated)
            return Path.Combine(FirstNonEmpty(ctx.DataDirectory, activeAppHome, installDir)!, ".dl_stage");
        return Path.Combine(Path.GetTempPath(), "Greenroom-Setup-dl");
    }
}
